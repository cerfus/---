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

    known = {os.path.normcase(os.path.abspath(запись.get("файл", "")))
             for запись in state.values() if запись.get("файл")}
    videos = all_videos(root)
    extra = [path for path in videos
             if os.path.normcase(os.path.abspath(path)) not in known]

    log("Видео в папках:            %d" % len(videos))
    log("Числится за раскладкой:    %d" % (len(videos) - len(extra)))
    log("Лишние копии:              %d" % len(extra))

    if not extra:
        log("")
        log("Всё чисто, дублей нет.")
        return

    log("")
    по_папкам = {}
    for path in extra:
        по_папкам.setdefault(os.path.basename(os.path.dirname(path)), []).append(path)
    for папка in sorted(по_папкам, key=lambda n: (not n.isdigit(), n)):
        файлы = по_папкам[папка]
        log("  %-14s %d шт." % (папка, len(файлы)))
        for path in файлы[:3]:
            log("        %s" % os.path.basename(path))
        if len(файлы) > 3:
            log("        ... и ещё %d" % (len(файлы) - 3))

    if not args.delete:
        log("")
        log("Это только показ. Чтобы удалить лишнее:")
        log("    python check_dupes.py --delete")
        return

    log("")
    removed = 0
    for path in extra:
        try:
            os.remove(path)
            removed += 1
        except OSError as exc:
            log("! Не смог удалить %s: %s" % (path, exc))
    log("Удалено: %d" % removed)
    log("Теперь запусти «Проверка - по 4 видео.bat», чтобы свериться заново.")


if __name__ == "__main__":
    main()
