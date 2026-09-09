#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скачивает все видео из Telegram-группы и раскладывает по папкам
с номерами квартир: D:\\Стекла\\200\\, D:\\Стекла\\201\\ ...
Видео, где номер определить не удалось, попадают в D:\\Стекла\\неопознанно\\.

Номер квартиры ищется по порядку:
  1) подпись к видео в Телеграме;
  2) текст соседнего сообщения (то, что писали рядом с видео);
  3) имя файла;
  4) речь в начале видео (распознавание Whisper);
  5) текст на кадрах в начале видео (OCR, если включён --ocr).

Запуск:  python download_defects.py --chat "Видео дефектов" --out "D:\\Стекла"
"""

import argparse
import configparser
import os
import re
import shutil
import sys

from common import (
    REPORT_FILE,
    UNKNOWN_DIR,
    Recognizer,
    append_report,
    forget_unknown,
    default_out,
    has_ffmpeg,
    load_state,
    log,
    save_state,
    unique_path,
)
from ru_numbers import find_apartment

try:
    from telethon.sync import TelegramClient
    from telethon.tl.types import (
        DocumentAttributeFilename,
        DocumentAttributeVideo,
        MessageMediaDocument,
    )
except ImportError:
    sys.exit("Не установлен telethon. Выполни: pip install -r requirements.txt")

TEMP_DIR = "_временно"
SESSION_NAME = "tg_defects_session"
CONFIG_FILE = "config.ini"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------

def is_video(message):
    if getattr(message, "video", None):
        return True
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaDocument) and media.document:
        mime = (media.document.mime_type or "").lower()
        if mime.startswith("video/"):
            return True
        for attr in media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return True
    return False


def media_filename(message):
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaDocument) and media.document:
        for attr in media.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
    return ""


def media_size(message):
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaDocument) and media.document:
        return media.document.size or 0
    return 0


# --------------------------------------------------------------------------
# Распознавание речи и текста на кадрах
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Определение номера квартиры
# --------------------------------------------------------------------------

def detect_apartment(message, neighbour_text, file_path, recognizer):
    """Возвращает (номер|None, откуда узнали, исходный текст)."""
    caption = (message.message or "").strip()
    number, how = find_apartment(caption)
    if number:
        return number, "подпись (%s)" % how, caption

    name = media_filename(message)
    number, how = find_apartment(name)
    if number:
        return number, "имя файла (%s)" % how, name

    # Дальше — то, что сказано и показано в самом видео: это надёжнее
    # соседних сообщений, которые могут относиться к другому ролику.
    speech = recognizer.transcribe(file_path)
    if speech:
        number, how = find_apartment(speech)
        if number:
            return number, "речь в видео (%s)" % how, speech

    on_screen = recognizer.read_frames(file_path)
    if on_screen:
        number, how = find_apartment(on_screen)
        if number:
            return number, "текст на кадре (%s)" % how, on_screen

    if neighbour_text:
        number, how = find_apartment(neighbour_text)
        if number:
            return number, "соседнее сообщение (%s)" % how, neighbour_text.strip()

    return None, "не определено", (caption or speech or "")


def build_filename(message, apartment):
    stamp = message.date.strftime("%Y-%m-%d") if message.date else "без-даты"
    original = media_filename(message)
    ext = os.path.splitext(original)[1] or ".mp4"
    prefix = "кв%s" % apartment if apartment else "неопознанно"
    return "%s_%s_id%d%s" % (prefix, stamp, message.id, ext)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def resolve_chat(client, wanted):
    """Находит чат по id, @имени или части названия."""
    if re.fullmatch(r"-?\d+", wanted or ""):
        return client.get_entity(int(wanted))
    if wanted.startswith("@"):
        return client.get_entity(wanted)

    needle = (wanted or "").lower()
    matches = []
    for dialog in client.iter_dialogs():
        title = (dialog.name or "")
        if needle and needle in title.lower():
            matches.append(dialog)
    if not matches:
        raise SystemExit(
            'Чат "%s" не найден. Запусти с --list-chats, чтобы увидеть список.' % wanted)
    if len(matches) > 1:
        log("Под описание подходит несколько чатов, уточни --chat по id:")
        for dialog in matches:
            log("   %-14s %s" % (dialog.id, dialog.name))
        raise SystemExit(1)
    return matches[0].entity


def list_chats(client):
    log("%-16s %s" % ("id", "название"))
    for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            log("%-16s %s" % (dialog.id, dialog.name))


def neighbours_by_group(messages):
    """Подписи часто пишут отдельным сообщением рядом. Собираем такие тексты."""
    texts = {}
    ordered = sorted(messages, key=lambda m: m.id)
    for index, message in enumerate(ordered):
        if not is_video(message):
            continue
        parts = []
        grouped = getattr(message, "grouped_id", None)
        if grouped:
            for other in ordered:
                if getattr(other, "grouped_id", None) == grouped and other.message:
                    parts.append(other.message)
        for offset in (-1, 1):
            neighbour = index + offset
            if 0 <= neighbour < len(ordered):
                other = ordered[neighbour]
                if not is_video(other) and other.message:
                    parts.append(other.message)
        unique = list(dict.fromkeys(part.strip() for part in parts if part.strip()))
        texts[message.id] = "\n".join(unique)
    return texts


# --------------------------------------------------------------------------
# api_id и api_hash: спрашиваем один раз и запоминаем рядом со скриптом
# --------------------------------------------------------------------------

WHERE_TO_GET = """
Нужны api_id и api_hash — это пропуск к Телеграму. Берутся один раз:

  1. Открой в браузере   https://my.telegram.org
  2. Введи свой номер телефона (в виде +7...).
  3. Код придёт НЕ в СМС, а сообщением в самом Телеграме — от «Telegram».
  4. Войдя, нажми  API development tools.
  5. Заполни форму: App title и Short name — любые, например  steklo.
     Platform выбери Desktop, описание можно не заполнять.
  6. Нажми Create application.
  7. На странице появятся:
        App api_id     — число, примерно 7-8 цифр
        App api_hash   — длинная строка из букв и цифр

Скопируй их сюда. Больше спрашивать не буду — сохраню в файл config.ini
рядом со скриптом. Никому этот файл не пересылай.
"""


def config_path():
    return os.path.join(SCRIPT_DIR, CONFIG_FILE)


def read_config():
    path = config_path()
    if not os.path.exists(path):
        return None, None
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error:
        return None, None
    if not parser.has_section("telegram"):
        return None, None
    section = parser["telegram"]
    return section.get("api_id", "").strip(), section.get("api_hash", "").strip()


def write_config(api_id, api_hash):
    parser = configparser.ConfigParser()
    parser["telegram"] = {"api_id": str(api_id), "api_hash": api_hash}
    with open(config_path(), "w", encoding="utf-8") as fh:
        fh.write("# Ключи доступа к Телеграму. Файл личный, никому не пересылай.\n")
        parser.write(fh)


def ask_credentials():
    """Спрашивает ключи у пользователя и сохраняет их."""
    log(WHERE_TO_GET)
    api_id = ""
    while not api_id:
        entered = input("api_id (только цифры): ").strip()
        if entered.isdigit():
            api_id = entered
        else:
            log("  Это должно быть число. Попробуй ещё раз.")

    api_hash = ""
    while not api_hash:
        entered = input("api_hash (длинная строка): ").strip()
        if len(entered) >= 30 and " " not in entered:
            api_hash = entered
        else:
            log("  Похоже, скопировалось не полностью. Попробуй ещё раз.")

    write_config(api_id, api_hash)
    log("")
    log("Сохранил в %s — больше вводить не придётся." % config_path())
    log("")
    return api_id, api_hash


def get_credentials(args):
    api_id = (args.api_id or "").strip() if args.api_id else ""
    api_hash = (args.api_hash or "").strip() if args.api_hash else ""
    if not api_id or not api_hash:
        saved_id, saved_hash = read_config()
        api_id = api_id or (saved_id or "")
        api_hash = api_hash or (saved_hash or "")
    if not api_id or not api_hash:
        api_id, api_hash = ask_credentials()
    return api_id, api_hash


# --------------------------------------------------------------------------
# Основной сценарий
# --------------------------------------------------------------------------

def main():
    out_default = default_out()
    parser = argparse.ArgumentParser(
        description="Скачивание видео дефектов из Telegram с раскладкой по квартирам")
    parser.add_argument("--chat", default="Видео дефектов",
                        help='название группы, @имя или id (по умолчанию "Видео дефектов")')
    parser.add_argument("--out", default=out_default,
                        help="куда складывать (по умолчанию %s)" % out_default)
    parser.add_argument("--api-id", default=os.environ.get("TG_API_ID"),
                        help="api_id с my.telegram.org (спросит сам, если не указать)")
    parser.add_argument("--api-hash", default=os.environ.get("TG_API_HASH"),
                        help="api_hash с my.telegram.org (спросит сам, если не указать)")
    parser.add_argument("--limit", type=int, default=None,
                        help="обработать только N последних сообщений (для пробы)")
    parser.add_argument("--whisper-model", default="small",
                        help="модель распознавания речи: tiny/base/small/medium/large-v3")
    parser.add_argument("--seconds", type=int, default=40,
                        help="сколько секунд начала видео слушать (по умолчанию 40)")
    parser.add_argument("--ocr", action="store_true",
                        help="дополнительно читать текст с кадров (нужен tesseract)")
    parser.add_argument("--no-speech", action="store_true",
                        help="не распознавать речь, только подписи и имена файлов")
    parser.add_argument("--redo-unknown", action="store_true",
                        help="заново обработать то, что лежит в папке 'неопознанно'")
    parser.add_argument("--list-chats", action="store_true",
                        help="показать список групп и выйти")
    args = parser.parse_args()

    api_id, api_hash = get_credentials(args)

    log("Подключаюсь к Телеграму...")
    log("(в первый раз спросит номер телефона и код — код придёт "
        "сообщением в самом Телеграме)")
    session_path = os.path.join(SCRIPT_DIR, SESSION_NAME)
    client = TelegramClient(session_path, int(api_id), api_hash)
    client.start()

    if args.list_chats:
        list_chats(client)
        client.disconnect()
        return

    root = args.out
    temp_root = os.path.join(root, TEMP_DIR)
    os.makedirs(temp_root, exist_ok=True)

    state = load_state(root)
    if args.redo_unknown:
        state = forget_unknown(root, state)

    chat = resolve_chat(client, args.chat)
    log('Группа: %s' % getattr(chat, "title", args.chat))
    log("Складываю в: %s" % root)

    all_messages = list(client.iter_messages(chat, limit=args.limit))
    videos = [m for m in all_messages if is_video(m)]
    videos.sort(key=lambda m: m.id)
    log("Всего видео в группе: %d" % len(videos))

    neighbour_texts = neighbours_by_group(all_messages)
    recognizer = Recognizer(args.whisper_model, args.seconds, args.ocr,
                            enabled=not args.no_speech)

    if not has_ffmpeg() and not args.no_speech:
        log("! ffmpeg не найден — номер будет браться только из подписей и имён файлов.")

    counters = {"скачано": 0, "пропущено": 0, "ошибок": 0, "неопознано": 0}

    for position, message in enumerate(videos, 1):
        key = str(message.id)
        if key in state and os.path.exists(state[key].get("файл", "")):
            counters["пропущено"] += 1
            continue

        size_mb = media_size(message) / 1048576.0
        log("[%d/%d] сообщение %d, %.1f МБ" % (position, len(videos), message.id, size_mb))

        temp_path = os.path.join(temp_root, "msg%d%s" % (
            message.id, os.path.splitext(media_filename(message))[1] or ".mp4"))
        try:
            if not os.path.exists(temp_path):
                client.download_media(message, file=temp_path)
        except Exception as exc:                          # noqa: BLE001
            log("  ! не удалось скачать: %s" % exc)
            counters["ошибок"] += 1
            continue

        apartment, how, source_text = detect_apartment(
            message, neighbour_texts.get(message.id, ""), temp_path, recognizer)

        folder_name = str(apartment) if apartment else UNKNOWN_DIR
        target_dir = os.path.join(root, folder_name)
        os.makedirs(target_dir, exist_ok=True)
        final_path = unique_path(target_dir, build_filename(message, apartment))

        try:
            shutil.move(temp_path, final_path)
        except OSError as exc:
            log("  ! не удалось переместить файл: %s" % exc)
            counters["ошибок"] += 1
            continue

        if apartment:
            log("  -> квартира %s (%s)" % (apartment, how))
        else:
            log("  -> неопознанно (номер в видео не найден)")
            counters["неопознано"] += 1
        counters["скачано"] += 1

        sender = ""
        try:
            entity = message.sender
            if entity is not None:
                sender = getattr(entity, "first_name", None) or \
                         getattr(entity, "title", None) or ""
        except Exception:                                 # noqa: BLE001
            pass

        state[key] = {
            "папка": folder_name,
            "файл": final_path,
            "квартира": apartment,
            "как": how,
        }
        save_state(root, state)
        append_report(root, [
            "telegram #%d" % message.id,
            message.date.strftime("%Y-%m-%d %H:%M") if message.date else "",
            sender,
            apartment or "",
            how,
            (source_text or "").replace("\n", " ")[:300],
            final_path,
        ])

    client.disconnect()
    try:
        os.rmdir(temp_root)
    except OSError:
        pass

    log("")
    log("Готово. Скачано: %(скачано)d, пропущено (уже было): %(пропущено)d, "
        "без номера: %(неопознано)d, ошибок: %(ошибок)d" % counters)
    log("Отчёт со всеми номерами: %s" % os.path.join(root, REPORT_FILE))
    if counters["неопознано"]:
        log("Разложи видео из папки '%s' руками, потом можно запустить "
            "скрипт снова с --redo-unknown." % UNKNOWN_DIR)


if __name__ == "__main__":
    main()
