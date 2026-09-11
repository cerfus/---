#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Показывает, что распознавание видит в начале видео.

Достаёт первые кадры нескольких видео и складывает рядом два файла:
сам кадр и то место, которое скрипт счёл табличкой. По ним сразу видно,
почему номер не читается — не туда смотрит или табличка нечитаемая.

    python show_frames.py --from "C:\\Users\\Вы\\Desktop\\авп"
"""

import argparse
import os
import shutil

from common import VIDEO_EXT, log, read_setting, write_setting
from plates import _crop_plate, _prepare, grab_frames, ocr_available

OUT_DIR = "_кадры"


def videos_in(folder, limit):
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(VIDEO_EXT):
            found.append(path)
    if len(found) <= limit:
        return found
    # Берём вразброс по всему списку, а не только начало
    шаг = len(found) / float(limit)
    return [found[int(i * шаг)] for i in range(limit)]


def main():
    parser = argparse.ArgumentParser(
        description="Показать, что распознавание видит в начале видео")
    parser.add_argument("--from", dest="source", default=None,
                        help="папка с видео")
    parser.add_argument("--count", type=int, default=6,
                        help="сколько видео разобрать (по умолчанию 6)")
    parser.add_argument("--seconds", type=int, default=5,
                        help="из скольких первых секунд брать кадры")
    parser.add_argument("--frames", type=int, default=3,
                        help="сколько кадров на видео")
    args = parser.parse_args()

    source = args.source or read_setting("max", "last_folder", "")
    if not source or not os.path.isdir(source):
        raise SystemExit("Укажи папку: python show_frames.py --from \"путь\"")
    write_setting("max", "last_folder", source)

    можно, беда = ocr_available()
    if not можно:
        raise SystemExit("Не выйдет: %s" % беда)

    videos = videos_in(source, args.count)
    if not videos:
        raise SystemExit("В %s нет видео." % source)

    цель = os.path.join(source, OUT_DIR)
    shutil.rmtree(цель, ignore_errors=True)
    os.makedirs(цель)

    from PIL import Image, ImageOps
    for номер, путь in enumerate(videos, 1):
        основа = os.path.splitext(os.path.basename(путь))[0][:24]
        work, кадры = grab_frames(путь, args.seconds, args.frames)
        try:
            if not кадры:
                log("%d. %s — кадры не достались" % (номер, основа))
                continue
            кадр = кадры[0]
            полный = ImageOps.grayscale(Image.open(кадр))
            полный.save(os.path.join(цель, "%02d_%s_кадр.png" % (номер, основа)))
            вырез = _crop_plate(полный)
            if вырез is None:
                log("%d. %s — табличку не выделил вовсе" % (номер, основа))
            else:
                _prepare(кадр, 0).save(
                    os.path.join(цель, "%02d_%s_вырез.png" % (номер, основа)))
                log("%d. %s — кадр %s, вырез %s" %
                    (номер, основа, полный.size, вырез.size))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    log("")
    log("Картинки здесь: %s" % цель)
    log("Открой их и посмотри: на «вырезе» должна быть табличка с номером.")
    log("Если там окно, стена или кусок комнаты — распознавание смотрит не туда.")


if __name__ == "__main__":
    main()
