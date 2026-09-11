# -*- coding: utf-8 -*-
"""Проверка чтения таблички с номером квартиры.

Собирает кадры, похожие на настоящие таблички в подъезде: чёрный
прямоугольник со светлыми цифрами на светлой стене, рядом тёмный косяк
двери. Проверяет, что номер читается, а там, где таблички нет, скрипт
молчит, а не выдумывает число.

Запуск: python test_plates.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

from plates import ocr_available, read_plate

СЛУЧАИ = [
    ("37", 37, dict()),
    ("181", 181, dict()),
    ("46", 46, dict(наклон=4)),
    ("205", 205, dict(доля=0.30, яркость=205, размытие=1.1)),
    ("128", 128, dict(наклон=-6)),
    ("71", 71, dict(доля=0.52, яркость=240)),
]


def кадр_таблички(путь, номер, доля=0.42, наклон=0, яркость=228, размытие=0.6):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import random
    random.seed(len(номер) * 7)
    шрифт = None
    for кандидат in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                     r"C:\Windows\Fonts\arial.ttf",
                     "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(кандидат):
            шрифт = кандидат
            break
    if шрифт is None:
        return False

    W, H = 1080, 1920
    img = Image.new("L", (W, H), яркость)
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.randint(80, 260)
        d.ellipse([x-r, y-r, x+r, y+r], fill=яркость + random.randint(-12, 8))
    d.rectangle([0, 0, int(W*0.10), H], fill=28)          # косяк двери

    сторона = int(W * доля)
    табличка = Image.new("L", (сторона, int(сторона*0.9)), 22)
    dt = ImageDraw.Draw(табличка)
    f = ImageFont.truetype(шрифт, int(сторона*0.42))
    b = dt.textbbox((0, 0), номер, font=f)
    dt.text(((сторона-(b[2]-b[0]))//2 - b[0],
             (int(сторона*0.9)-(b[3]-b[1]))//2 - b[1] + int(сторона*0.04)),
            номер, font=f, fill=238)
    fl = ImageFont.truetype(шрифт, int(сторона*0.075))
    dt.rectangle([сторона-int(сторона*0.22), int(сторона*0.05),
                  сторона-int(сторона*0.06), int(сторона*0.19)], outline=238, width=3)
    dt.text((сторона-int(сторона*0.21), int(сторона*0.13)), "Level", font=fl, fill=238)
    if наклон:
        табличка = табличка.rotate(наклон, expand=True, fillcolor=яркость)
    img.paste(табличка, (int(W*0.5 - табличка.width/2),
                         int(H*0.45 - табличка.height/2)))
    if размытие:
        img = img.filter(ImageFilter.GaussianBlur(размытие))
    img.save(путь)
    return True


def кадр_окна(путь):
    from PIL import Image, ImageDraw
    img = Image.new("L", (1080, 1920), 150)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1080, 1190], fill=232)
    for i in range(14):
        d.rectangle([594+i*28, 96, 610+i*28, 1440], fill=170)
    img.save(путь)


def собрать_видео(папка, имя, номер, как):
    кадры = os.path.join(папка, "кадры")
    os.makedirs(кадры, exist_ok=True)
    try:
        for n in range(1, 7):
            путь = os.path.join(кадры, "f%03d.png" % n)
            if номер is None:
                кадр_окна(путь)
            elif not кадр_таблички(путь, номер, **как):
                return None
        for n in range(7, 15):
            кадр_окна(os.path.join(кадры, "f%03d.png" % n))
        видео = os.path.join(папка, имя + ".mp4")
        готово = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-framerate", "3",
             "-i", os.path.join(кадры, "f%03d.png"),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", видео],
            capture_output=True)
        return видео if готово.returncode == 0 else None
    finally:
        shutil.rmtree(кадры, ignore_errors=True)


def main():
    можно, беда = ocr_available()
    if не_готово(можно, беда):
        return 0

    папка = tempfile.mkdtemp()
    try:
        # Распознавание никогда не будет безошибочным, поэтому проверяем не
        # «всё прочитано», а два свойства, от которых зависит раскладка:
        #   1. выдуманных номеров быть не должно вовсе;
        #   2. читаться должно большинство табличек, иначе смысла нет.
        табличек = прочитано = выдумано = 0
        for номер, ждём, как in СЛУЧАИ + [(None, None, {})]:
            имя = номер or "без_таблички"
            видео = собрать_видео(папка, имя, номер, как)
            if видео is None:
                print("ПРОПУСК %s — не собралось тестовое видео" % имя)
                continue
            получено, почему = read_plate(видео)
            if ждём is not None:
                табличек += 1
                if получено == ждём:
                    прочитано += 1
                elif получено is not None:
                    выдумано += 1
                    print("ПЛОХО %-14s ждём=%s, а прочитано %s — выдуманный номер"
                          % (имя, ждём, получено))
                else:
                    print("  пропуск %-12s (%s)" % (имя, почему))
            elif получено is not None:
                выдумано += 1
                print("ПЛОХО без таблички прочитан номер %s" % получено)

        мало = табличек and прочитано < табличек * 0.6
        if мало:
            print("ПЛОХО прочитано всего %d табличек из %d" % (прочитано, табличек))
        print("Табличек: %d, прочитано: %d, выдумано: %d"
              % (табличек, прочитано, выдумано))
        return 1 if (выдумано or мало) else 0
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def не_готово(можно, беда):
    if можно:
        return False
    print("Пропускаю: распознавание недоступно (%s)" % беда.splitlines()[0])
    return True


if __name__ == "__main__":
    raise SystemExit(main())
