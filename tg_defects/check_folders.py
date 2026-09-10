#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверяет, по сколько видео лежит в каждой папке-квартире.

Запуск:  python check_folders.py            (ожидает по 4 видео)
         python check_folders.py --per-flat 6
"""

import argparse
import os

from common import check_counts, default_out


def main():
    parser = argparse.ArgumentParser(
        description="Сверка: сколько видео в каждой квартире")
    parser.add_argument("--out", default=default_out(),
                        help="папка с разложенными видео (по умолчанию %s)"
                             % default_out())
    parser.add_argument("--per-flat", type=int, default=4,
                        help="сколько видео ожидается в квартире (по числу окон)")
    args = parser.parse_args()

    if not os.path.isdir(args.out):
        raise SystemExit("Папки нет: %s" % args.out)
    check_counts(args.out, args.per_flat)


if __name__ == "__main__":
    main()
