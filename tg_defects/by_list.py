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
import re
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
COLUMNS = ["№", "файл", "снято", "длина", "квартира", "как прочитано"]


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
    # Время файла на диске — это когда его скачали, а не сняли. В колонку
    # такое ставить нельзя: будет только сбивать с толку.
    return когда, сколько


# "20191044897441 (1).mp4" — та же запись, скачанная второй раз
_COPY = re.compile(r"^(.*?)(?:\s*\((\d+)\))?(\.[^.]+)$")


def base_name(filename):
    """Имя без пометки о повторном скачивании: "видео (1).mp4" -> "видео.mp4"."""
    match = _COPY.match(filename)
    if not match:
        return filename.lower()
    return (match.group(1) + match.group(3)).lower()


def drop_copies(записи):
    """Убирает повторные скачивания. записи: [(путь, когда, длительность)].

    Одинаковое имя и одинаковая длительность — это один и тот же ролик,
    сохранённый дважды. Оставляем тот, у чьего имени нет пометки "(1)".
    """
    по_базе = {}
    for запись in записи:
        по_базе.setdefault(base_name(os.path.basename(запись[0])), []).append(запись)

    оставить, убрано, подозрительные = [], 0, []
    for база, группа in по_базе.items():
        группа.sort(key=lambda з: len(os.path.basename(з[0])))
        длительности = {з[2] for з in группа if з[2]}
        if len(длительности) > 1:
            # Длительность разная — значит это разные видео, оставляем все
            подозрительные.append(база)
            оставить.extend(группа)
            continue
        оставить.append(группа[0])
        убрано += len(группа) - 1

    if убрано:
        log("Повторных скачиваний убрано из списка: %d" % убрано)
    if подозрительные:
        log("! У %d имён копии разной длины — оставил все, разберись сам:"
            % len(подозрительные))
        for база in подозрительные[:5]:
            log("    %s" % база)
    return оставить, убрано


def sort_key(записи):
    """Порядок как в чате. Имена-числа — это номера сообщений, они растут."""
    имена = [os.path.splitext(os.path.basename(з[0]))[0] for з in записи]
    чистые = [base_name(os.path.basename(з[0])).rsplit(".", 1)[0] for з in записи]
    if чистые and all(имя.isdigit() for имя in чистые):
        log("Имена файлов — номера сообщений, упорядочиваю по ним.")
        return lambda з: int(base_name(os.path.basename(з[0])).rsplit(".", 1)[0])
    log("Упорядочиваю по времени съёмки.")
    return lambda з: (з[1], os.path.basename(з[0]))


def read_plates(записи, seconds, frames, confidence):
    """Читает номер с таблички в начале каждого видео."""
    from plates import ocr_available, read_plate

    можно, беда = ocr_available()
    if не_можно(можно, беда):
        return {}

    log("")
    log("Читаю таблички в начале видео. Это примерно %d минут." %
        max(1, int(len(записи) * 5 / 60)))
    log("Число принимается, только если распознано уверенно: лучше оставить")
    log("пустым, чем разложить видео не в ту квартиру.")
    log("")

    прочитано = {}
    for position, (path, _когда, _сколько) in enumerate(записи, 1):
        номер, почему = read_plate(path, seconds=seconds, frames=frames,
                                   confidence=confidence)
        прочитано[path] = (номер, почему)
        отметка = "кв %d" % номер if номер else "—"
        log("[%d/%d] %-34s %s" % (position, len(записи),
                                  os.path.basename(path)[:34], отметка))
    нашлось = sum(1 for номер, _ in прочитано.values() if номер)
    log("")
    log("Номер прочитан у %d видео из %d." % (нашлось, len(записи)))
    if нашлось < len(записи):
        log("Остальные оставлены пустыми — впиши номера сам или оставь как")
        log("есть, тогда они уедут в «%s»." % UNKNOWN_DIR)
    return прочитано


def не_можно(можно, беда):
    if можно:
        return False
    log("")
    log("! Распознать таблички не получится: %s" % беда)
    log("  Список соберу без номеров.")
    return True


def make_list(source, ocr=False, seconds=5, frames=3, confidence=75):
    videos = videos_in(source)
    if not videos:
        raise SystemExit("В %s нет видео." % source)

    log("Нашёл файлов: %d. Читаю длительность..." % len(videos))
    записи = []
    for position, path in enumerate(videos, 1):
        if position % 25 == 0:
            log("  ...%d из %d" % (position, len(videos)))
        когда, сколько = shot_info(path)
        записи.append((path, когда, сколько))

    записи, _убрано = drop_copies(записи)
    записи.sort(key=sort_key(записи))
    log("Видео в списке: %d" % len(записи))

    прочитано = read_plates(записи, seconds, frames, confidence) if ocr else {}
    строки = []
    for position, (path, когда, сколько) in enumerate(записи, 1):
        номер, почему = прочитано.get(path, (None, ""))
        if номер:
            клетка = str(номер)
        elif ocr:
            # Табличку не прочитали. Пишем явное «нет», чтобы видео ушло в
            # «неопознанно», а не унаследовало квартиру строкой выше.
            клетка = "нет"
        else:
            клетка = ""
        строки.append([position, os.path.basename(path), когда, сколько,
                       клетка, почему])

    path = os.path.join(source, LIST_FILE)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(COLUMNS)
        writer.writerows(строки)

    log("")
    log("Список готов: %s" % path)
    log("")
    log("Что дальше:")
    log("  1. Открой его в Excel. Видео идут в том же порядке, что и в чате,")
    log("     а колонка «длина» поможет узнать нужный ролик.")
    log("  2. В колонке «квартира»:")
    log("       число       — эта квартира")
    log("       пусто       — та же квартира, что строкой выше")
    log("       нет         — в «%s», ничего не наследовать" % UNKNOWN_DIR)
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

    назначения, текущая = [], None
    унаследовано = без_номера = 0
    for строка in rows[1:]:
        if len(строка) < 5 or not строка[1].strip():
            continue
        имя = строка[1].strip()
        клетка = строка[4].strip()
        низ = клетка.lower()
        if низ in ("нет", "-", "—", "?"):
            # Явный отказ: это видео в «неопознанно», квартиру сверху не берём
            назначения.append((имя, None))
            без_номера += 1
            continue
        if клетка:
            цифры = "".join(c for c in клетка if c.isdigit())
            текущая = int(цифры) if цифры else None
        else:
            if текущая is not None:
                унаследовано += 1
            else:
                без_номера += 1
        назначения.append((имя, текущая))

    if унаследовано:
        log("Пустых клеток, взявших квартиру строкой выше: %d" % унаследовано)
    if без_номера:
        log("Без квартиры: %d — уедут в «%s»." % (без_номера, UNKNOWN_DIR))
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
    parser.add_argument("--ocr", action="store_true",
                        help="прочитать номер с таблички в начале видео")
    parser.add_argument("--seconds", type=int, default=5,
                        help="сколько секунд начала видео смотреть (по умолчанию 5)")
    parser.add_argument("--frames", type=int, default=3,
                        help="сколько кадров брать (по умолчанию 3)")
    parser.add_argument("--confidence", type=int, default=75,
                        help="насколько уверенным должно быть чтение, 0-100 "
                             "(по умолчанию 75; ниже — больше ошибок)")
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
        make_list(source, ocr=args.ocr, seconds=args.seconds,
                  frames=args.frames, confidence=args.confidence)


if __name__ == "__main__":
    main()
