#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладывает уже скачанные видео по папкам с номерами квартир.

Нужен, если my.telegram.org не даёт api_id. Тогда видео выгружаются
средствами самого Телеграма:

    Telegram Desktop -> открыть группу "Видео дефектов"
    -> три точки справа сверху -> Экспорт истории чата
    -> отметить "Видеофайлы", снять лишнее
    -> формат: JSON (подписи читаются и из HTML-выгрузки)
    -> размер файла поставить побольше -> Экспортировать

Потом натравить этот скрипт на полученную папку:

    python sort_export.py --from "C:\\Users\\Вы\\Downloads\\ChatExport_2026-09-09"
"""

import argparse
import html as html_module
import json
import os
import re
import shutil
import urllib.parse

from common import (
    REPORT_FILE,
    UNKNOWN_DIR,
    Recognizer,
    append_report,
    check_counts,
    forget_unknown,
    default_out,
    has_ffmpeg,
    load_state,
    log,
    neighbour_texts,
    read_setting,
    save_state,
    write_setting,
    unique_path,
)
from ru_numbers import find_apartment

VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm",
             ".3gp", ".wmv", ".mpg", ".mpeg")


def flatten_text(value):
    """В выгрузке текст бывает строкой, а бывает списком кусков."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


def read_export_json(export_dir):
    """Читает result.json: путь к файлу -> сведения о сообщении."""
    path = os.path.join(export_dir, "result.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        log("! Не смог прочитать result.json (%s), обойдусь именами файлов." % exc)
        return {}

    skipped_files = 0
    collected = []
    for message in data.get("messages", []):
        relative = message.get("file") or ""
        # Если при выгрузке не отметили "Видеофайлы", Телеграм вместо пути
        # пишет "(File not included...)" — это не файл.
        if relative.startswith("("):
            skipped_files += 1
            relative = ""
        key = os.path.normpath(relative).replace("\\", "/").lower() if relative else ""
        collected.append({
            "файл": key,
            "текст": flatten_text(message.get("text")),
            "дата": (message.get("date") or "").replace("T", " ")[:16],
            "автор": message.get("from") or "",
        })

    рядом = neighbour_texts(collected)
    info = {}
    for item in collected:
        if not item["файл"]:
            continue
        info[item["файл"]] = {
            "подпись": item["текст"],
            "рядом": рядом.get(item["файл"], ""),
            "дата": item["дата"],
            "автор": item["автор"],
        }
    log("Прочитал result.json: подписи есть для %d файлов." % len(info))
    if skipped_files:
        log("! В выгрузке %d сообщений без самих файлов — похоже, при экспорте"
            % skipped_files)
        log("  не была отмечена галочка «Видеофайлы» или не хватило лимита размера.")
    return info


# --------------------------------------------------------------------------
# Выгрузка в HTML: подписи лежат в messages*.html рядом со ссылками на видео
# --------------------------------------------------------------------------

_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="([^"]+\.(?:mp4|mov|avi|mkv|webm|m4v|3gp))"', re.I)
_TEXT = re.compile(r'<div class="text">(.*?)</div>', re.S)
_FROM = re.compile(r'<div class="from_name">(.*?)</div>', re.S)
_DATE = re.compile(r'<div class="pull_right date details" title="([^"]*)"')


def _plain(fragment):
    """Из куска HTML делает обычный текст."""
    if not fragment:
        return ""
    fragment = re.sub(r"<br\s*/?>", " ", fragment)
    return html_module.unescape(_TAGS.sub("", fragment)).strip()


def _html_files(export_dir):
    """messages.html, messages2.html, ... по-человечески по порядку."""
    found = []
    for name in os.listdir(export_dir):
        match = re.fullmatch(r"messages(\d*)\.html", name, re.I)
        if match:
            found.append((int(match.group(1) or 1), os.path.join(export_dir, name)))
    return [path for _number, path in sorted(found)]


def read_export_html(export_dir):
    """Читает messages*.html: путь к файлу -> сведения о сообщении."""
    pages = _html_files(export_dir)
    if not pages:
        return {}

    # Сначала собираем сообщения подряд, чтобы потом видеть соседей.
    collected = []
    for page in pages:
        try:
            with open(page, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            log("! Не смог прочитать %s: %s" % (os.path.basename(page), exc))
            continue
        blocks = content.split('<div class="message ')[1:]
        for block in blocks:
            link = _HREF.search(block)
            relative = ""
            if link:
                relative = urllib.parse.unquote(link.group(1))
                relative = os.path.normpath(relative).replace("\\", "/").lower()
            author = _FROM.search(block)
            date = _DATE.search(block)
            collected.append({
                "файл": relative,
                "текст": _plain(_TEXT.search(block).group(1)) if _TEXT.search(block) else "",
                "автор": _plain(author.group(1)) if author else "",
                "дата": (date.group(1) if date else "")[:16],
            })

    рядом = neighbour_texts(collected)
    info = {}
    for item in collected:
        if not item["файл"]:
            continue
        info[item["файл"]] = {
            "подпись": item["текст"],
            "рядом": рядом.get(item["файл"], ""),
            "дата": item["дата"],
            "автор": item["автор"],
        }
    log("Прочитал выгрузку HTML (%d файлов): подписи есть для %d видео."
        % (len(pages), len(info)))
    return info


def find_videos(export_dir):
    found = []
    for folder, _dirs, names in os.walk(export_dir):
        for name in names:
            if name.lower().endswith(VIDEO_EXT):
                found.append(os.path.join(folder, name))
    found.sort()
    return found


def detect(video_path, meta, recognizer):
    """Возвращает (номер|None, как определили, исходный текст)."""
    caption = (meta.get("подпись") or "").strip()
    number, how = find_apartment(caption)
    if number:
        return number, "подпись (%s)" % how, caption

    name = os.path.basename(video_path)
    number, how = find_apartment(name)
    if number:
        return number, "имя файла (%s)" % how, name

    speech = recognizer.transcribe(video_path)
    if speech:
        number, how = find_apartment(speech)
        if number:
            return number, "речь в видео (%s)" % how, speech

    on_screen = recognizer.read_frames(video_path)
    if on_screen:
        number, how = find_apartment(on_screen)
        if number:
            return number, "текст на кадре (%s)" % how, on_screen

    neighbour = (meta.get("рядом") or "").strip()
    if neighbour:
        number, how = find_apartment(neighbour)
        if number:
            return number, "соседнее сообщение (%s)" % how, neighbour

    return None, "не определено", (caption or speech or "")


def ask_folder():
    log("")
    log("Укажи папку с выгрузкой из Телеграма.")
    log("Это папка вида  ChatExport_2026-09-09  — обычно в Загрузках.")
    log("Проще всего: открой её в проводнике, скопируй путь из адресной")
    log("строки и вставь сюда правой кнопкой мыши.")
    log("")
    previous = read_setting("export", "last_folder", "")
    if previous and os.path.isdir(previous):
        log("В прошлый раз была: %s" % previous)
        log("Нажми Enter, чтобы взять её же, или вставь другой путь.")
    while True:
        entered = input("Папка: ").strip().strip('"')
        if not entered and previous and os.path.isdir(previous):
            return previous
        if entered and os.path.isdir(entered):
            return entered
        log("  Такой папки нет. Проверь путь и попробуй ещё раз.")


def main():
    parser = argparse.ArgumentParser(
        description="Раскладывает скачанные видео по папкам с номерами квартир")
    parser.add_argument("--from", dest="source", default=None,
                        help="папка с выгрузкой из Телеграма (спросит, если не указать)")
    parser.add_argument("--out", default=default_out(),
                        help="куда раскладывать (по умолчанию %s)" % default_out())
    parser.add_argument("--move", action="store_true",
                        help="переносить файлы, а не копировать (экономит место)")
    parser.add_argument("--whisper-model", default="small",
                        help="модель распознавания речи: tiny/base/small/medium/large-v3")
    parser.add_argument("--seconds", type=int, default=40,
                        help="сколько секунд начала видео слушать")
    parser.add_argument("--ocr", action="store_true",
                        help="дополнительно читать номер с кадров")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                        help="на чём распознавать речь (по умолчанию auto, "
                             "при неудаче сам перейдёт на cpu)")
    parser.add_argument("--no-speech", action="store_true",
                        help="не распознавать речь, только подписи и имена файлов")
    parser.add_argument("--per-flat", type=int, default=4,
                        help="сколько видео ожидается в квартире (по числу окон, "
                             "по умолчанию 4; 0 — не проверять)")
    parser.add_argument("--redo-unknown", action="store_true",
                        help="заново обработать то, что попало в 'неопознанно'")
    args = parser.parse_args()

    source = args.source or ask_folder()
    if not os.path.isdir(source):
        raise SystemExit("Папка не найдена: %s" % source)
    write_setting("export", "last_folder", source)

    root = args.out
    os.makedirs(root, exist_ok=True)

    state = load_state(root)
    if args.redo_unknown:
        state = forget_unknown(root, state)

    info = read_export_json(source)
    if not info:
        # Выгрузка в HTML — подписи всё равно можно достать.
        info = read_export_html(source)
    if not info:
        log("! Подписей к видео нет: выгрузка сделана без result.json и без")
        log("  messages.html, либо подписей в чате не было. Номер будет")
        log("  определяться только по имени файла и по речи в видео.")
    videos = find_videos(source)
    log("Нашёл видео: %d" % len(videos))
    if not videos:
        raise SystemExit("В этой папке видео нет. Проверь, что выгрузка "
                         "делалась с галочкой «Видеофайлы».")

    recognizer = Recognizer(args.whisper_model, args.seconds, args.ocr,
                            enabled=not args.no_speech, device=args.device)
    if not has_ffmpeg() and not args.no_speech:
        log("! ffmpeg не найден — номер возьмётся только из подписей и имён файлов.")

    counters = {"разложено": 0, "пропущено": 0, "неопознано": 0, "ошибок": 0}

    for position, video_path in enumerate(videos, 1):
        relative = os.path.relpath(video_path, source).replace("\\", "/").lower()
        if relative in state and os.path.exists(state[relative].get("файл", "")):
            counters["пропущено"] += 1
            continue

        size_mb = os.path.getsize(video_path) / 1048576.0
        log("[%d/%d] %s, %.1f МБ" % (position, len(videos),
                                     os.path.basename(video_path), size_mb))

        meta = info.get(relative, {})
        apartment, how, source_text = detect(video_path, meta, recognizer)

        folder_name = str(apartment) if apartment else UNKNOWN_DIR
        target_dir = os.path.join(root, folder_name)
        os.makedirs(target_dir, exist_ok=True)

        prefix = "кв%s" % apartment if apartment else "неопознанно"
        stamp = (meta.get("дата") or "").replace(":", "-").replace(" ", "_")
        base = os.path.splitext(os.path.basename(video_path))[0]
        ext = os.path.splitext(video_path)[1] or ".mp4"
        final_path = unique_path(target_dir, "%s_%s%s%s" % (
            prefix, stamp + "_" if stamp else "", base, ext))

        try:
            if args.move:
                shutil.move(video_path, final_path)
            else:
                shutil.copy2(video_path, final_path)
        except (OSError, shutil.Error) as exc:
            log("  ! не удалось положить файл: %s" % exc)
            counters["ошибок"] += 1
            continue

        if apartment:
            log("  -> квартира %s (%s)" % (apartment, how))
        else:
            log("  -> неопознанно (номер не найден)")
            counters["неопознано"] += 1
        counters["разложено"] += 1

        state[relative] = {"папка": folder_name, "файл": final_path,
                           "квартира": apartment, "как": how}
        save_state(root, state)
        append_report(root, [
            os.path.basename(video_path),
            meta.get("дата", ""),
            meta.get("автор", ""),
            apartment or "",
            how,
            (source_text or "").replace("\n", " ")[:300],
            final_path,
        ])

    log("")
    log("Готово. Разложено: %(разложено)d, пропущено (уже было): %(пропущено)d, "
        "без номера: %(неопознано)d, ошибок: %(ошибок)d" % counters)
    log("Отчёт: %s" % os.path.join(root, REPORT_FILE))
    if counters["неопознано"]:
        log("Видео из папки '%s' разложи руками — в отчёте видно, "
            "что в них было слышно." % UNKNOWN_DIR)

    log("")
    check_counts(root, args.per_flat)


if __name__ == "__main__":
    main()
