#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Раскладка по списку, заполненному вручную.

Нужна, когда видео скачаны без переписки и номер квартиры взять неоткуда:
подписей нет, в самих видео номер не проговаривают.

Шаг 1 — собрать список:
    python by_list.py --from "C:\\Users\\Вы\\Desktop\\авп"
  Появится файл _список.csv: все видео по порядку съёмки, колонка
  «квартира» пустая.

Шаг 2 — открыть его в Excel и проставить номера. Заполнять надо только
  первую строку каждой квартиры: пустая клетка означает «та же, что выше».

Шаг 3 — разложить:
    python by_list.py --from "C:\\Users\\Вы\\Desktop\\авп" --sort
"""

import argparse
import csv
import json
import os
import shutil
import subprocess

from common import (
    UNKNOWN_DIR,
    read_setting,
    write_setting,
    VIDEO_EXT,
    default_out,
    has_ffmpeg,
    log,
    unique_path,
)

LIST_FILE = "_список.csv"
COLUMNS = ["№", "файл", "снято", "длительность", "квартира"]


def videos_in(folder):
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(VIDEO_EXT):
            found.append(path)
    return found


def shot_info(path):
    """Когда снято и сколько длится. Без ffmpeg — по времени файла."""
    когда, сколько = "", ""
    if has_ffmpeg():
        cmd = ["ffprobe", "-v", "error", "-of", "json", "-show_entries",
               "format=duration:format_tags=creation_time", path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                data = json.loads(result.stdout or "{}").get("format", {})
                когда = (data.get("tags", {}) or {}).get("creation_time", "")
                когда = когда.replace("T", " ")[:16]
                try:
                    секунды = float(data.get("duration", 0))
                    сколько = "%d:%02d" % (секунды // 60, секунды % 60)
                except (TypeError, ValueError):
                    pass
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if not когда:
        import datetime
        когда = datetime.datetime.fromtimestamp(
            os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    return когда, сколько


def make_list(source):
    videos = videos_in(source)
    if not videos:
        raise SystemExit("В %s нет видео." % source)

    log("Нашёл видео: %d. Читаю, когда снято..." % len(videos))
    строки = []
    for position, path in enumerate(videos, 1):
        if position % 20 == 0:
            log("  ...%d из %d" % (position, len(videos)))
        когда, сколько = shot_info(path)
        строки.append([position, os.path.basename(path), когда, сколько, ""])

    # По времени съёмки: видео одной квартиры сняты подряд.
    строки.sort(key=lambda r: (r[2], r[1]))
    for position, строка in enumerate(строки, 1):
        строка[0] = position

    path = os.path.join(source, LIST_FILE)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(COLUMNS)
        writer.writerows(строки)

    log("")
    log("Список готов: %s" % path)
    log("")
    log("Что дальше:")
    log("  1. Открой его в Excel — видео идут по порядку съёмки.")
    log("  2. В колонке «квартира» проставь номера. Заполняй только первую")
    log("     строку каждой квартиры: пустая клетка = та же, что выше.")
    log("  3. Сохрани (Excel спросит про формат — оставь CSV) и запусти:")
    log("     python by_list.py --from \"%s\" --sort" % source)


def read_list(source):
    path = os.path.join(source, LIST_FILE)
    if not os.path.exists(path):
        raise SystemExit("Нет файла %s — сначала собери список без ключа --sort."
                         % path)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter=";"))
    if len(rows) < 2:
        raise SystemExit("Список пуст.")

    назначения, текущая, пустых = [], None, 0
    for строка in rows[1:]:
        if len(строка) < 5 or not строка[1].strip():
            continue
        имя = строка[1].strip()
        номер = строка[4].strip()
        if номер:
            цифры = "".join(c for c in номер if c.isdigit())
            текущая = int(цифры) if цифры else None
        elif текущая is None:
            пустых += 1
        назначения.append((имя, текущая))
    if пустых:
        log("Без номера в начале списка: %d — уедут в «%s»." % (пустых, UNKNOWN_DIR))
    return назначения


def sort_by_list(source, out, move):
    назначения = read_list(source)
    if not назначения:
        raise SystemExit("В списке ни одной строки с файлом.")

    os.makedirs(out, exist_ok=True)
    разложено, пропало, счёт = 0, [], {}
    for имя, квартира in назначения:
        откуда = os.path.join(source, имя)
        if not os.path.isfile(откуда):
            пропало.append(имя)
            continue
        папка = str(квартира) if квартира else UNKNOWN_DIR
        куда = os.path.join(out, папка)
        os.makedirs(куда, exist_ok=True)
        приставка = "кв%s" % квартира if квартира else "неопознанно"
        конечный = unique_path(куда, "%s_%s" % (приставка, имя))
        try:
            if move:
                shutil.move(откуда, конечный)
            else:
                shutil.copy2(откуда, конечный)
        except (OSError, shutil.Error) as exc:
            log("! %s: %s" % (имя, exc))
            continue
        разложено += 1
        счёт[папка] = счёт.get(папка, 0) + 1

    log("")
    log("Разложено: %d" % разложено)
    if пропало:
        log("Не нашёл файлов из списка: %d" % len(пропало))
        for имя in пропало[:5]:
            log("    %s" % имя)
    log("")
    log("По папкам:")
    for папка in sorted(счёт, key=lambda n: (not n.isdigit(), n.zfill(5))):
        log("  %-14s %d" % (папка, счёт[папка]))
    log("")
    log("Проверить раскладку: python check_folders.py --out \"%s\"" % out)


def ask_folder():
    previous = read_setting("max", "last_folder", "")
    log("")
    log("Укажи папку со скачанными видео.")
    log("Открой её в проводнике, скопируй путь из адресной строки")
    log("и вставь сюда правой кнопкой мыши.")
    if previous and os.path.isdir(previous):
        log("")
        log("В прошлый раз была: %s" % previous)
        log("Нажми Enter, чтобы взять её же.")
    log("")
    while True:
        entered = input("Папка: ").strip().strip('"')
        if not entered and previous and os.path.isdir(previous):
            return previous
        if entered and os.path.isdir(entered):
            return entered
        log("  Такой папки нет. Проверь путь и попробуй ещё раз.")


def main():
    parser = argparse.ArgumentParser(
        description="Раскладка видео по списку, заполненному вручную")
    parser.add_argument("--from", dest="source", default=None,
                        help="папка со скачанными видео (спросит, если не указать)")
    parser.add_argument("--out", default=default_out(),
                        help="куда раскладывать (по умолчанию %s)" % default_out())
    parser.add_argument("--sort", action="store_true",
                        help="разложить по заполненному списку")
    parser.add_argument("--move", action="store_true",
                        help="переносить файлы, а не копировать")
    args = parser.parse_args()

    source = args.source or ask_folder()
    if not os.path.isdir(source):
        raise SystemExit("Папки нет: %s" % source)
    write_setting("max", "last_folder", source)
    args.source = source

    if args.sort:
        sort_by_list(args.source, args.out, args.move)
    else:
        make_list(args.source)


if __name__ == "__main__":
    main()
