#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Чтение номера квартиры с таблички в начале видео.

В части роликов первым кадром показывают табличку с номером. Модуль достаёт
несколько первых кадров, находит на них табличку и читает число.

Главное правило: лучше промолчать, чем ошибиться. Число принимается только
если сам Tesseract оценил свою уверенность не ниже порога — иначе видео
уйдёт в «неопознанно», где его видно и можно разложить руками. Неверный же
номер тихо уводит ролик в чужую папку, и находится он только при разборе
дефектов.
"""

import os
import re
import shutil
import subprocess
import tempfile

from common import has_ffmpeg, log

# Куда winget и установщик UB Mannheim обычно кладут Tesseract.
# В PATH он при этом попадает не всегда, поэтому ищем сами.
WINDOWS_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "Programs", "Tesseract-OCR", "tesseract.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "Tesseract-OCR", "tesseract.exe"),
)
WINGET_GLOB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft",
                           "WinGet", "Packages", "UB-Mannheim.TesseractOCR*",
                           "tesseract.exe")

MIN_FLAT, MAX_FLAT = 1, 9999
DEFAULT_CONFIDENCE = 75      # проверено на тестовых видео: ошибок нет
ENOUGH_CONFIDENCE = 92       # настолько уверенное чтение уточнять незачем
CONFIGS = ("--psm 8", "--psm 13")     # табличка — это одно слово или строка
TRIMS = (0, 15, 30)                   # рамку таблички иногда надо срезать
_DIGITS = re.compile(r"\d{1,4}")


_язык = None


def find_tesseract():
    """Прописывает путь к tesseract.exe, если его нет в PATH."""
    import glob
    import pytesseract
    if shutil.which("tesseract"):
        return True
    кандидаты = [путь for путь in WINDOWS_PATHS if путь and os.path.isfile(путь)]
    кандидаты += sorted(glob.glob(WINGET_GLOB))
    if кандидаты:
        pytesseract.pytesseract.tesseract_cmd = кандидаты[0]
        return True
    return False


def pick_language():
    """Русский словарь бывает не установлен — тогда читаем только цифры.

    Для таблички это почти не потеря: цифры в обоих словарях одинаковы,
    русский помогает лишь распознать слово «кв» рядом с числом.
    """
    global _язык
    if _язык:
        return _язык
    import pytesseract
    from PIL import Image
    проба = Image.new("L", (60, 30), 255)
    for язык in ("rus+eng", "eng"):
        try:
            pytesseract.image_to_string(проба, lang=язык)
            _язык = язык
            if язык == "eng":
                log("! Русский словарь Tesseract не найден — читаю только цифры.")
                log("  Для табличек с числами этого достаточно.")
            return _язык
        except Exception:                                 # noqa: BLE001
            continue
    _язык = "eng"
    return _язык


def ocr_available():
    """Есть ли чем распознавать. Возвращает (можно, что не так)."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False, ("не установлены библиотеки. Выполни:\n"
                       "    pip install pytesseract Pillow numpy")
    import pytesseract
    find_tesseract()
    try:
        версия = pytesseract.get_tesseract_version()
    except Exception:                                     # noqa: BLE001
        return False, ("не найден сам Tesseract. Поставь его:\n"
                       "    winget install UB-Mannheim.TesseractOCR\n"
                       "  при установке отметь русский язык (Russian),\n"
                       "  потом закрой это окно и открой заново.\n"
                       "  Если он уже стоит — значит не прописан в PATH;\n"
                       "  тогда перезапусти командную строку")
    log("Tesseract найден, версия %s" % версия)
    if not has_ffmpeg():
        return False, "не найден ffmpeg — без него не достать кадры из видео"
    return True, ""


# --------------------------------------------------------------------------
# Кадры и подготовка картинки
# --------------------------------------------------------------------------

def grab_frames(video_path, seconds, count):
    """Достаёт count кадров из первых seconds секунд."""
    work = tempfile.mkdtemp(prefix="кадры_")
    шаг = max(seconds / float(count), 0.2)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path,
           "-t", str(seconds), "-vf", "fps=%.3f,scale=1280:-2" % (1.0 / шаг),
           "-frames:v", str(count), os.path.join(work, "кадр_%02d.png")]
    try:
        if subprocess.call(cmd, timeout=120) != 0:
            return work, []
    except (OSError, subprocess.TimeoutExpired):
        return work, []
    return work, [os.path.join(work, имя) for имя in sorted(os.listdir(work))]


def _otsu(gray):
    """Порог, разделяющий светлое и тёмное на картинке."""
    import numpy as np
    a = np.asarray(gray)
    hist, _ = np.histogram(a, bins=256, range=(0, 256))
    total, сумма = a.size, float(np.dot(np.arange(256), hist))
    сум_b, вес_b, лучший, порог = 0.0, 0, -1.0, 128
    for t in range(256):
        вес_b += hist[t]
        if вес_b == 0:
            continue
        вес_f = total - вес_b
        if вес_f == 0:
            break
        сум_b += t * hist[t]
        между = вес_b * вес_f * ((сум_b / вес_b) - ((сумма - сум_b) / вес_f)) ** 2
        if между > лучший:
            лучший, порог = между, t
    return порог


def _crop_plate(img):
    """Вырезает самую крупную светлую область — обычно это и есть табличка."""
    import numpy as np
    светлое = np.asarray(img) > _otsu(img)
    строки, столбцы = светлое.sum(axis=1), светлое.sum(axis=0)
    if строки.max() == 0 or столбцы.max() == 0:
        return None
    ys = np.where(строки > строки.max() * 0.35)[0]
    xs = np.where(столбцы > столбцы.max() * 0.35)[0]
    if len(ys) < 10 or len(xs) < 10:
        return None
    поле = 12
    return img.crop((max(int(xs[0]) - поле, 0), max(int(ys[0]) - поле, 0),
                     min(int(xs[-1]) + поле, img.width),
                     min(int(ys[-1]) + поле, img.height)))


def _prepare(frame_path, trim):
    """Табличка крупно, на белом поле: так Tesseract читает заметно лучше."""
    from PIL import Image, ImageOps
    полный = ImageOps.grayscale(Image.open(frame_path))
    вырез = _crop_plate(полный) or полный
    if trim and вырез.width > 4 * trim and вырез.height > 4 * trim:
        вырез = вырез.crop((trim, trim, вырез.width - trim, вырез.height - trim))
    вырез = вырез.resize((вырез.width * 3, вырез.height * 3), Image.LANCZOS)
    return ImageOps.expand(вырез, border=60, fill=255)


# --------------------------------------------------------------------------
# Чтение
# --------------------------------------------------------------------------

def _read_confident(картинка, конфиг):
    """[(число, уверенность)] — Tesseract сам оценивает, насколько уверен."""
    import pytesseract
    try:
        данные = pytesseract.image_to_data(
            картинка, lang=pick_language(), config=конфиг,
            output_type=pytesseract.Output.DICT)
    except Exception:                                     # noqa: BLE001
        return []
    найдено = []
    for текст, conf in zip(данные.get("text", []), данные.get("conf", [])):
        try:
            уверенность = float(conf)
        except (TypeError, ValueError):
            continue
        if уверенность < 0:
            continue
        for цифры in _DIGITS.findall(текст or ""):
            число = int(цифры)
            if MIN_FLAT <= число <= MAX_FLAT:
                найдено.append((число, уверенность))
    return найдено


def read_plate(video_path, seconds=5, frames=3, confidence=DEFAULT_CONFIDENCE):
    """Номер с таблички. Возвращает (номер|None, пояснение)."""
    work, кадры = grab_frames(video_path, seconds, frames)
    try:
        if not кадры:
            return None, "не удалось достать кадры"

        лучшее = {}
        for кадр in кадры:
            for trim in TRIMS:
                try:
                    картинка = _prepare(кадр, trim)
                except Exception:                         # noqa: BLE001
                    continue
                for конфиг in CONFIGS:
                    for число, уверенность in _read_confident(картинка, конфиг):
                        лучшее[число] = max(лучшее.get(число, 0), уверенность)
            if any(у >= ENOUGH_CONFIDENCE for у in лучшее.values()):
                break                     # прочитано уверенно, хватит

        if not лучшее:
            return None, "таблички не видно"

        число = max(лучшее, key=лучшее.get)
        если_бы = "%d (уверенность %.0f)" % (число, лучшее[число])
        if лучшее[число] < confidence:
            return None, "прочиталось ненадёжно: " + если_бы
        return число, "уверенность %.0f" % лучшее[число]
    finally:
        shutil.rmtree(work, ignore_errors=True)
