#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Удаляет пустые папки в D:\\Стекла.

Пустыми считаются те, в которых нет ни одного файла — ни в них самих, ни
во вложенных папках. Файлы не трогаются вовсе.

    python clean_empty.py
    python clean_empty.py --out "D:\\Стекла"
"""

import argparse
import os

from common import default_out, log


def пустая(папка):
    """В папке и всём, что внутри, нет ни одного файла."""
    for _корень, _папки, файлы in os.walk(папка):
        if файлы:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Удаление пустых папок")
    parser.add_argument("--out", default=default_out(),
                        help="где чистить (по умолчанию %s)" % default_out())
    args = parser.parse_args()

    if not os.path.isdir(args.out):
        raise SystemExit("Папки нет: %s" % args.out)

    удалено = []
    # Идём снизу вверх: сначала вложенные, потом те, что стали пустыми
    for корень, папки, _файлы in os.walk(args.out, topdown=False):
        for имя in папки:
            путь = os.path.join(корень, имя)
            if not пустая(путь):
                continue
            try:
                for под_корень, под_папки, _ in os.walk(путь, topdown=False):
                    for под_имя in под_папки:
                        os.rmdir(os.path.join(под_корень, под_имя))
                os.rmdir(путь)
                удалено.append(имя)
            except OSError as exc:
                log("! Не смог удалить %s: %s" % (имя, exc))

    if not удалено:
        log("Пустых папок нет.")
        return
    log("Удалено пустых папок: %d" % len(удалено))
    for имя in sorted(удалено):
        log("    %s" % имя)


if __name__ == "__main__":
    main()
