#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ищет в D:\\Стекла лишние копии, оставшиеся от прежних прогонов.

Скрипт помнит, куда положил каждое видео (файл _состояние.json). Всё, что
лежит в папках, но в этой памяти не значится, — след от старой раскладки:
видео, которое в прошлый раз попало в другую папку или под другим именем.

  python check_dupes.py            только показать
  python check_dupes.py --delete   удалить лишнее
"""

import argparse
import os

from common import STATE_FILE, UNKNOWN_DIR, VIDEO_EXT, default_out, load_state, log


def all_videos(root):
    found = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if not (name.isdigit() or name == UNKNOWN_DIR):
            continue
        for inner in sorted(os.listdir(path)):
            if inner.lower().endswith(VIDEO_EXT):
                found.append(os.path.join(path, inner))
    return found


def main():
    parser = argparse.ArgumentParser(
        description="Поиск лишних копий от прежних раскладок")
    parser.add_argument("--out", default=default_out(),
                        help="папка с разложенными видео (по умолчанию %s)"
                             % default_out())
    parser.add_argument("--delete", action="store_true",
                        help="удалить лишние копии (без ключа только показывает)")
    args = parser.parse_args()

    root = args.out
    if not os.path.isdir(root):
        raise SystemExit("Папки нет: %s" % root)

    state = load_state(root)
    if not state:
        raise SystemExit(
            "Не нашёл %s — без него не отличить лишние копии от нужных.\n"
            "Проще всего удалить папку целиком и разложить заново."
            % os.path.join(root, STATE_FILE))

    # Сравниваем по месту внутри раскладки — «папка/имя файла», а не по
    # полному пути. Иначе стоит перенести или переименовать D:\Стекла, как
    # вся память перестанет совпадать и КАЖДЫЙ файл сочтётся лишним.
    def место(path):
        return (os.path.normcase(os.path.basename(os.path.dirname(path))),
                os.path.normcase(os.path.basename(path)))

    known = {место(запись["файл"]) for запись in state.values() if запись.get("файл")}
    # Исходные имена видео — по ним узнаём, что у лишнего файла есть двойник.
    originals = sorted({os.path.basename(ключ) for ключ in state}, key=len, reverse=True)

    videos = all_videos(root)
    extra = [path for path in videos if место(path) not in known]

    log("Видео в папках:            %d" % len(videos))
    log("Числится за раскладкой:    %d" % (len(videos) - len(extra)))
    log("Лишние копии:              %d" % len(extra))

    if not extra:
        log("")
        log("Всё чисто, дублей нет.")
        return

    # Предохранитель: если память почти ни с чем не совпала, значит она не про
    # эту папку — раскладку переносили, или подсунут чужой _состояние.json.
    # Удалять в такой ситуации нельзя ничего.
    учтено = len(videos) - len(extra)
    if учтено == 0 or len(extra) > len(videos) / 2:
        log("")
        log("СТОП. Память раскладки почти не совпадает с тем, что лежит в папке:")
        log("  учтено %d из %d видео." % (учтено, len(videos)))
        log("Похоже, папку переносили или переименовывали, либо рядом лежит")
        log("чужой %s. Удалять в таком виде нельзя — можно потерять всё." % STATE_FILE)
        log("")
        log("Что делать: разложи заново с нуля — удали папку целиком и")
        log("запусти «Шаг 1 - разложить.bat». Видео возьмутся из выгрузки.")
        return

    # Удалять можно только то, чей исходник точно лежит в учтённых папках.
    есть_двойник, единственные = [], []
    for path in extra:
        имя = os.path.basename(path).lower()
        if any(имя.endswith(original.lower()) for original in originals):
            есть_двойник.append(path)
        else:
            единственные.append(path)

    log("  из них с учтённым двойником: %d" % len(есть_двойник))
    if единственные:
        log("  БЕЗ двойника (удалять нельзя): %d" % len(единственные))

    log("")
    по_папкам = {}
    for path in есть_двойник:
        по_папкам.setdefault(os.path.basename(os.path.dirname(path)), []).append(path)
    for папка in sorted(по_папкам, key=lambda n: (not n.isdigit(), n)):
        файлы = по_папкам[папка]
        log("  %-14s %d шт." % (папка, len(файлы)))
        for path in файлы[:3]:
            log("        %s" % os.path.basename(path))
        if len(файлы) > 3:
            log("        ... и ещё %d" % (len(файлы) - 3))

    if единственные:
        log("")
        log("Эти файлы единственные в своём роде — их не удаляю,")
        log("разберись с ними руками:")
        for path in единственные[:10]:
            log("    %s" % path)
        if len(единственные) > 10:
            log("    ... и ещё %d" % (len(единственные) - 10))

    if not args.delete:
        log("")
        log("Это только показ. Чтобы удалить лишнее:")
        log("    python check_dupes.py --delete")
        return

    log("")
    removed = 0
    for path in есть_двойник:
        try:
            os.remove(path)
            removed += 1
        except OSError as exc:
            log("! Не смог удалить %s: %s" % (path, exc))
    log("Удалено: %d" % removed)
    log("Теперь запусти «Проверка - по 4 видео.bat», чтобы свериться заново.")


if __name__ == "__main__":
    main()
