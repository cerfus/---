#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверяет, целы ли разложенные видео, и каким кодеком они сняты.

Плеер Windows часто не открывает видео с айфона не потому, что файл битый,
а потому, что оно снято в HEVC (H.265) — встроенный «Медиаплеер» такого не
умеет без отдельного расширения. Этот скрипт отличает одно от другого.

  python check_videos.py           быстро: читает заголовки
  python check_videos.py --deep    надёжно: полностью раскодирует каждое видео
"""

import argparse
import json
import os
import subprocess

from common import UNKNOWN_DIR, VIDEO_EXT, default_out, has_ffmpeg, log


def ffprobe(path):
    """Возвращает (длительность, кодек) или (None, причина поломки)."""
    cmd = ["ffprobe", "-v", "error", "-of", "json",
           "-show_entries", "format=duration:stream=codec_type,codec_name", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "ffprobe не смог прочитать: %s" % exc
    if result.returncode != 0:
        причина = (result.stderr or "").strip().splitlines()
        return None, причина[0][:90] if причина else "ffprobe вернул ошибку"
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return None, "ffprobe выдал невнятный ответ"

    видео = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not видео:
        return None, "в файле нет видеодорожки"
    try:
        длительность = float(data.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        длительность = 0
    if длительность <= 0:
        return None, "нулевая длительность — файл обрезан"
    return длительность, видео[0].get("codec_name", "?")


def decode_fully(path):
    """Прогоняет видео целиком. Ловит обрыв в середине и в хвосте."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "не удалось раскодировать: %s" % exc
    ошибки = (result.stderr or "").strip().splitlines()
    if ошибки:
        return ошибки[0][:90]
    return ""


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
    parser = argparse.ArgumentParser(description="Проверка целости видео")
    parser.add_argument("--out", default=default_out(),
                        help="папка с разложенными видео (по умолчанию %s)"
                             % default_out())
    parser.add_argument("--deep", action="store_true",
                        help="раскодировать каждое видео целиком (долго, но надёжно)")
    args = parser.parse_args()

    if not os.path.isdir(args.out):
        raise SystemExit("Папки нет: %s" % args.out)
    if not has_ffmpeg():
        raise SystemExit("Не найден ffmpeg — без него проверить нельзя.\n"
                         "Поставь его: winget install Gyan.FFmpeg")

    videos = all_videos(args.out)
    if not videos:
        raise SystemExit("В %s нет разложенных видео." % args.out)

    log("Проверяю %d видео%s..." % (len(videos), ", с полным раскодированием"
                                    if args.deep else ""))
    log("")

    кодеки, битые = {}, []
    for position, path in enumerate(videos, 1):
        if position % 20 == 0:
            log("  ...%d из %d" % (position, len(videos)))
        длительность, что = ffprobe(path)
        if длительность is None:
            битые.append((path, что))
            continue
        кодеки[что] = кодеки.get(что, 0) + 1
        if args.deep:
            беда = decode_fully(path)
            if беда:
                битые.append((path, беда))

    log("")
    log("Кодеки:")
    for кодек, сколько in sorted(кодеки.items(), key=lambda p: -p[1]):
        пометка = ""
        if кодек == "hevc":
            пометка = "  <- «Медиаплеер» Windows такое не открывает"
        log("  %-10s %3d шт.%s" % (кодек, сколько, пометка))

    log("")
    if битые:
        log("Испорченные файлы: %d" % len(битые))
        for path, причина in битые:
            log("  %s" % os.path.basename(path))
            log("      %s" % причина)
        log("")
        log("Эти видео придётся заново забрать из Телеграма:")
        log("выгрузка их не докачала.")
    else:
        log("Все %d видео целые." % len(videos))
        if кодеки.get("hevc"):
            log("")
            log("Если какое-то не открывается — дело не в файле, а в плеере.")
            log("Поставь VLC (бесплатный, открывает всё): https://videolan.org")


if __name__ == "__main__":
    main()
