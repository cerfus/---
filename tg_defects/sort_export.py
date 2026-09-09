#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладывает уже скачанные видео по папкам с номерами квартир.

Нужен, если my.telegram.org не даёт api_id. Тогда видео выгружаются
средствами самого Телеграма:

    Telegram Desktop -> открыть группу "Видео дефектов"
    -> три точки справа сверху -> Экспорт истории чата
    -> отметить "Видеофайлы", снять лишнее
    -> формат: JSON (тогда будут видны подписи к видео)
    -> размер файла поставить побольше -> Экспортировать

Потом натравить этот скрипт на полученную папку:

    python sort_export.py --from "C:\\Users\\Вы\\Downloads\\ChatExport_2026-09-09"
"""

import argparse
import json
import os
import shutil

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

    info = {}
    skipped_files = 0
    messages = data.get("messages", [])
    for index, message in enumerate(messages):
        relative = message.get("file")
        if not relative:
            continue
        # Если при выгрузке не отметили "Видеофайлы", Телеграм вместо пути
        # пишет "(File not included...)" — это не файл.
        if relative.startswith("("):
            skipped_files += 1
            continue
        caption = flatten_text(message.get("text"))
        # Подпись часто в соседнем сообщении без файла
        neighbours = []
        for offset in (-1, 1):
            position = index + offset
            if 0 <= position < len(messages):
                other = messages[position]
                if not other.get("file"):
                    text = flatten_text(other.get("text"))
                    if text:
                        neighbours.append(text)
        key = os.path.normpath(relative).replace("\\", "/").lower()
        info[key] = {
            "подпись": caption,
            "рядом": "\n".join(neighbours),
            "дата": (message.get("date") or "").replace("T", " ")[:16],
            "автор": message.get("from") or "",
            "id": message.get("id"),
        }
    log("Прочитал result.json: подписи есть для %d файлов." % len(info))
    if skipped_files:
        log("! В выгрузке %d сообщений без самих файлов — похоже, при экспорте"
            % skipped_files)
        log("  не была отмечена галочка «Видеофайлы» или не хватило лимита размера.")
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
    while True:
        entered = input("Папка: ").strip().strip('"')
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
    parser.add_argument("--no-speech", action="store_true",
                        help="не распознавать речь, только подписи и имена файлов")
    parser.add_argument("--redo-unknown", action="store_true",
                        help="заново обработать то, что попало в 'неопознанно'")
    args = parser.parse_args()

    source = args.source or ask_folder()
    if not os.path.isdir(source):
        raise SystemExit("Папка не найдена: %s" % source)

    root = args.out
    os.makedirs(root, exist_ok=True)

    state = load_state(root)
    if args.redo_unknown:
        state = forget_unknown(root, state)

    info = read_export_json(source)
    videos = find_videos(source)
    log("Нашёл видео: %d" % len(videos))
    if not videos:
        raise SystemExit("В этой папке видео нет. Проверь, что выгрузка "
                         "делалась с галочкой «Видеофайлы».")

    recognizer = Recognizer(args.whisper_model, args.seconds, args.ocr,
                            enabled=not args.no_speech)
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


if __name__ == "__main__":
    main()
