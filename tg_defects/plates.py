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
DEFAULT_CONFIDENCE = 55      # подобран на кадрах с настоящих табличек
ENOUGH_CONFIDENCE = 92       # настолько уверенное чтение уточнять незачем
AGREED_CONFIDENCE = 25       # повторившееся на разных кадрах — уже довод
CONFIGS = ("--psm 8", "--psm 7", "--psm 13",
           "--psm 8 -c tessedit_char_whitelist=0123456789")
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
    шаг = max(seconds / float(count), 0.25)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path,
           "-t", str(seconds), "-vf",
           ("fps=%.3f,scale=w=1280:h=1280:force_original_aspect_ratio=decrease"
            % (1.0 / шаг)),
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


def _blobs(маска):
    """Связные области маски: список (пикселей, xmin, ymin, xmax, ymax)."""
    from collections import deque
    import numpy as np
    H, W = маска.shape
    посещено = np.zeros_like(маска, dtype=bool)
    найдено = []
    for y0 in range(H):
        for x0 in range(W):
            if not маска[y0, x0] or посещено[y0, x0]:
                continue
            очередь = deque([(y0, x0)])
            посещено[y0, x0] = True
            пикселей = 0
            ymin = ymax = y0
            xmin = xmax = x0
            while очередь:
                y, x = очередь.popleft()
                пикселей += 1
                if y < ymin: ymin = y
                if y > ymax: ymax = y
                if x < xmin: xmin = x
                if x > xmax: xmax = x
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if (0 <= ny < H and 0 <= nx < W
                            and маска[ny, nx] and not посещено[ny, nx]):
                        посещено[ny, nx] = True
                        очередь.append((ny, nx))
            найдено.append((пикселей, xmin, ymin, xmax, ymax))
    return найдено


def _find_plate(img, dark=True):
    """Ищет табличку — крупный прямоугольник ровного тона на стене.

    У номерных табличек тёмный фон и светлые цифры, поэтому по умолчанию
    ищем тёмное пятно. Пятна, доходящие до края кадра, пропускаем: это
    косяк двери или тень, а не табличка.
    """
    import numpy as np
    from PIL import Image
    мелкая = img.resize((240, max(1, int(240 * img.height / img.width))),
                        Image.BILINEAR)
    a = np.asarray(мелкая)
    порог = _otsu(a)
    маска = (a < порог) if dark else (a > порог)
    H, W = маска.shape

    подходящие = []
    for пикселей, xmin, ymin, xmax, ymax in _blobs(маска):
        ширина, высота = xmax - xmin + 1, ymax - ymin + 1
        доля = пикселей / float(H * W)
        заполнение = пикселей / float(ширина * высота)
        отношение = ширина / float(высота)
        у_края = xmin <= 1 or xmax >= W - 2 or ymin <= 1 or ymax >= H - 2
        if (0.004 < доля < 0.30 and заполнение > 0.55
                and 0.55 < отношение < 2.2 and not у_края):
            подходящие.append((пикселей, xmin, ymin, xmax, ymax))
    if not подходящие:
        return None

    _, xmin, ymin, xmax, ymax = max(подходящие)
    kx, ky = img.width / float(W), img.height / float(H)
    # Режем ровно по пятну: запас наружу зацепил бы стену, а она светлее
    # цифр и сбивает следующий шаг.
    return img.crop((int(xmin * kx), int(ymin * ky),
                     int((xmax + 1) * kx), int((ymax + 1) * ky)))


def _crop_digits(табличка):
    """Оставляет от таблички только цифры: логотип и поля мешают читать."""
    import numpy as np
    from PIL import Image
    мелкая = табличка.resize(
        (220, max(1, int(220 * табличка.height / табличка.width))), Image.BILINEAR)
    a = np.asarray(мелкая)
    H, W = a.shape

    годные = []
    for пикселей, xmin, ymin, xmax, ymax in _blobs(a > _otsu(a)):
        if пикселей < 10:
            continue
        if xmin <= 1 or xmax >= W - 2 or ymin <= 1 or ymax >= H - 2:
            continue                      # край — это стена, а не цифра
        годные.append((пикселей, xmin, ymin, xmax, ymax))
    if not годные:
        return табличка

    высоты = [(п[4] - п[2] + 1) for п in годные]
    порог = max(высоты) * 0.45            # логотип заметно ниже цифр
    цифры = [п for п in годные if (п[4] - п[2] + 1) >= порог]
    xmin = min(п[1] for п in цифры)
    xmax = max(п[3] for п in цифры)
    ymin = min(п[2] for п in цифры)
    ymax = max(п[4] for п in цифры)
    kx, ky = табличка.width / float(W), табличка.height / float(H)
    поле = 6
    return табличка.crop((max(int((xmin - поле) * kx), 0),
                          max(int((ymin - поле) * ky), 0),
                          min(int((xmax + поле) * kx), табличка.width),
                          min(int((ymax + поле) * ky), табличка.height)))


def _prepare(frame_path):
    """Готовит картинки для чтения: сначала тёмная табличка, потом светлая."""
    from PIL import Image, ImageOps
    полный = ImageOps.grayscale(Image.open(frame_path))
    готовые = []
    for тёмная in (True, False):
        табличка = _find_plate(полный, dark=тёмная)
        if табличка is None:
            continue
        область = _crop_digits(табличка) if тёмная else табличка
        картинка = ImageOps.invert(область) if тёмная else область
        картинка = картинка.resize((картинка.width * 3, картинка.height * 3),
                                   Image.LANCZOS)
        готовые.append(ImageOps.expand(ImageOps.autocontrast(картинка),
                                       border=50, fill=255))
        if тёмная:
            break                         # тёмная нашлась — светлую не ищем
    return готовые


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


def read_plate(video_path, seconds=4, frames=8, confidence=DEFAULT_CONFIDENCE):
    """Номер с таблички. Возвращает (номер|None, пояснение)."""
    work, кадры = grab_frames(video_path, seconds, frames)
    try:
        if not кадры:
            return None, "не удалось достать кадры"

        лучшее, накадрах, находили = {}, {}, False
        for кадр in кадры:
            try:
                картинки = _prepare(кадр)
            except Exception:                             # noqa: BLE001
                continue
            if картинки:
                находили = True
            на_этом = set()
            for картинка in картинки:
                for конфиг in CONFIGS:
                    for число, уверенность in _read_confident(картинка, конфиг):
                        лучшее[число] = max(лучшее.get(число, 0), уверенность)
                        на_этом.add(число)
            for число in на_этом:
                накадрах[число] = накадрах.get(число, 0) + 1
            if any(у >= ENOUGH_CONFIDENCE for у in лучшее.values()):
                break                     # прочитано уверенно, хватит

        if not лучшее:
            return None, ("табличка нашлась, но цифры не читаются"
                          if находили else "таблички не видно")

        # Сначала уверенные, при равной уверенности — виденные на большем
        # числе кадров.
        число = max(лучшее, key=lambda н: (лучшее[н], накадрах.get(н, 0)))
        повторилось = накадрах.get(число, 0) >= 2
        уверенность = лучшее[число]

        if уверенность >= confidence:
            return число, "уверенность %.0f" % уверенность
        if повторилось and уверенность >= AGREED_CONFIDENCE:
            # Одно и то же число на разных кадрах — это уже не случайность
            return число, ("уверенность %.0f, но прочитано на %d кадрах"
                           % (уверенность, накадрах[число]))
        return None, "прочиталось ненадёжно: %d (уверенность %.0f)" % (
            число, уверенность)
    finally:
        shutil.rmtree(work, ignore_errors=True)
