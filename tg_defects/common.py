# -*- coding: utf-8 -*-
"""Общая часть: распознавание, отчёт, состояние. Используют оба скрипта."""

import configparser
import csv
import re
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Предупреждение про символьные ссылки в кэше моделей пугает, но ни на что
# не влияет — глушим до импорта faster-whisper.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

CONFIG_FILE = "config.ini"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UNKNOWN_DIR = "неопознанно"
STATE_FILE = "_состояние.json"
REPORT_FILE = "_отчет.csv"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _short(exc):
    """Сообщения библиотек бывают на десять строк — оставляем первую."""
    text = str(exc).strip().splitlines()
    return text[0][:160] if text else exc.__class__.__name__


def config_path():
    return os.path.join(SCRIPT_DIR, CONFIG_FILE)


def read_setting(section, key, default=""):
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path(), encoding="utf-8")
    except (configparser.Error, OSError):
        return default
    return parser.get(section, key, fallback=default).strip()


def write_setting(section, key, value):
    """Дописывает одну настройку, сохраняя всё остальное в файле."""
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path(), encoding="utf-8")
    except (configparser.Error, OSError):
        pass
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, str(value))
    try:
        with open(config_path(), "w", encoding="utf-8") as fh:
            fh.write("# Настройки скрипта. Файл личный, никому не пересылай.\n")
            parser.write(fh)
    except OSError as exc:
        log("! Не смог сохранить настройку: %s" % exc)


def log(message):
    print(message, flush=True)


def has_ffmpeg():
    return shutil.which("ffmpeg") is not None


def default_out():
    return r"D:\Стекла" if os.name == "nt" else os.path.abspath("Стекла")


# --------------------------------------------------------------------------
# Состояние и отчёт
# --------------------------------------------------------------------------

def load_state(root):
    path = os.path.join(root, STATE_FILE)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            log("! Файл состояния повреждён, начинаю с чистого листа.")
    return {}


def save_state(root, state):
    path = os.path.join(root, STATE_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_report(root, row):
    path = os.path.join(root, REPORT_FILE)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        if new:
            writer.writerow(["источник", "дата", "автор", "квартира",
                             "как определено", "исходный текст", "файл"])
        writer.writerow(row)


def forget_unknown(root, state):
    """Убирает из состояния всё неопознанное и удаляет прежние копии,
    чтобы при повторной обработке не появлялись дубли."""
    unknown_root = os.path.abspath(os.path.join(root, UNKNOWN_DIR))
    removed = 0
    for key in [k for k, v in state.items() if v.get("папка") == UNKNOWN_DIR]:
        placed = state[key].get("файл", "")
        # Удаляем только то, что сами же положили в папку "неопознанно".
        if placed and os.path.isfile(placed):
            full = os.path.abspath(placed)
            if os.path.dirname(full) == unknown_root:
                try:
                    os.remove(full)
                    removed += 1
                except OSError as exc:
                    log("! Не смог убрать старую копию %s: %s" % (full, exc))
        del state[key]
    if removed:
        log("Убрал %d прежних копий из папки «%s», чтобы не было дублей."
            % (removed, UNKNOWN_DIR))
    return state


def unique_path(folder, filename):
    """Не затирает уже лежащий файл — добавляет (2), (3)..."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, "%s (%d)%s" % (base, counter, ext))
        counter += 1
    return candidate


# --------------------------------------------------------------------------
# Что сказано и показано в самом видео
# --------------------------------------------------------------------------

CHECK_FILE = "_проверка.txt"
EXPECTED_FILE = "_ожидание.json"
VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm",
             ".3gp", ".wmv", ".mpg", ".mpeg")


def load_expected(root):
    path = os.path.join(root, EXPECTED_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return {int(k): int(v) for k, v in json.load(fh).items()}
    except (OSError, ValueError, TypeError):
        return {}


def save_expected(root, expected):
    """Запоминает, сколько видео обещано подписями для каждой квартиры."""
    path = os.path.join(root, EXPECTED_FILE)
    # Старый файл может быть пустым или битым — это не повод терять новые
    # данные, просто перезаписываем его целиком.
    current = load_expected(root)
    current.update(expected)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in current.items()}, fh,
                      ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as exc:
        log("! Не смог сохранить ожидаемые количества: %s" % exc)


def count_videos(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    return sum(1 for name in names if name.lower().endswith(VIDEO_EXT))


def count_placed(root):
    """Сколько видео уже разложено по папкам-квартирам и в «неопознанно»."""
    total = 0
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return 0
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path) and (name.isdigit() or name == UNKNOWN_DIR):
            total += count_videos(path)
    return total


def clean_layout(root):
    """Сносит прежнюю раскладку: папки квартир и служебные файлы.

    Трогает только то, что раскладывал сам — папки с числовым именем,
    «неопознанно» и свои служебные файлы. Ничего чужого в папке не заденет.
    """
    removed = 0
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        log("! Не смог заглянуть в %s: %s" % (root, exc))
        return 0
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path) and (name.isdigit() or name == UNKNOWN_DIR):
            try:
                removed += count_videos(path)
                shutil.rmtree(path)
            except OSError as exc:
                log("! Не смог удалить папку %s: %s" % (name, exc))
        elif name in (STATE_FILE, REPORT_FILE, CHECK_FILE, EXPECTED_FILE,
                      "_подсказки.txt"):
            try:
                os.remove(path)
            except OSError as exc:
                log("! Не смог удалить %s: %s" % (name, exc))
    log("Убрал прежнюю раскладку: %d видео." % removed)
    return removed


def check_counts(root, expected=4):
    """Сверяет, сколько видео легло в каждую квартиру.

    Если подписи назвали дефекты («Об1 стп1,2 Об2 стп1» — три стеклопакета),
    сверяемся с этим числом: оно точнее любой общей нормы. Для квартир без
    подписи берём expected — сколько окон обычно снимают.
    """
    promised = load_expected(root)
    if expected <= 0 and not promised:
        return []

    flats = {}
    try:
        entries = sorted(os.listdir(root))
    except OSError as exc:
        log("! Не смог заглянуть в %s: %s" % (root, exc))
        return []
    for name in entries:
        path = os.path.join(root, name)
        if os.path.isdir(path) and name.isdigit():
            flats[int(name)] = count_videos(path)

    unknown = count_videos(os.path.join(root, UNKNOWN_DIR))

    сходится, расходится, без_подписи = [], [], []
    for flat, actual in sorted(flats.items()):
        want = promised.get(flat)
        if want is None:
            if expected > 0 and actual != expected:
                без_подписи.append((flat, actual, expected))
            continue
        if actual == want:
            сходится.append(flat)
        else:
            расходится.append((flat, actual, want))

    lines = []
    lines.append("Проверка: сверяю с тем, что обещано в подписях")
    lines.append("-" * 58)
    lines.append("Квартир найдено:        %d" % len(flats))
    lines.append("  сходится с подписью:  %d" % len(сходится))
    lines.append("  расходится:           %d" % len(расходится))
    for flat, actual, want in расходится:
        lines.append("      кв %-5d лежит %d, а подпись обещала %d"
                     % (flat, actual, want))
    if без_подписи:
        lines.append("  без подписи (сверял с %d):  %d" % (expected, len(без_подписи)))
        for flat, actual, want in без_подписи:
            lines.append("      кв %-5d лежит %d" % (flat, actual))
    lines.append("В папке «%s»:  %d" % (UNKNOWN_DIR, unknown))
    lines.append("Всего видео разложено:  %d" % (sum(flats.values()) + unknown))
    lines.append("")
    if расходится or unknown:
        lines.append("Где смотреть в первую очередь:")
        мало = [f for f, a, w in расходится if a < w]
        много = [f for f, a, w in расходится if a > w]
        if много:
            lines.append("  Перебор — туда попало чужое: %s"
                         % ", ".join(str(f) for f in много))
        if мало:
            lines.append("  Недобор — ищи в «%s»: %s"
                         % (UNKNOWN_DIR, ", ".join(str(f) for f in мало)))
        if unknown:
            lines.append("  «%s» — %d видео разложить руками."
                         % (UNKNOWN_DIR, unknown))

    for line in lines:
        log(line)
    try:
        with open(os.path.join(root, CHECK_FILE), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        log("Эта же сводка лежит в %s" % os.path.join(root, CHECK_FILE))
    except OSError as exc:
        log("! Не смог записать сводку: %s" % exc)
    return lines


_DEFECTS = re.compile(r"(?:стп|сп)\s*\.?\s*((?:\d\s*[,/]\s*)*\d)", re.IGNORECASE)


def count_defects(text):
    """Считает стеклопакеты в подписи: "Об1 стп1,2 Об2 стп 1" -> 3.

    Ровно столько видео и снимают для квартиры, так что число говорит,
    сколько следующих роликов относятся к той же квартире.
    """
    if not text:
        return 1
    total = sum(len(re.findall(r"\d", m.group(1))) for m in _DEFECTS.finditer(text))
    return max(total, 1)


def neighbour_texts(items):
    """items — сообщения по порядку: {"файл": путь или "", "текст": строка}.

    Возвращает {файл: текст рядом}. Текст между двумя видео пропускаем:
    непонятно, к какому он относится, а ошибиться папкой хуже, чем
    оставить видео в «неопознанно» — там его хотя бы видно.
    """
    result = {}
    for index, item in enumerate(items):
        # Сосед — это отдельное сообщение с текстом. Видео с подписью
        # соседом не считается: его подпись принадлежит ему самому.
        if not item.get("текст") or item.get("файл"):
            continue
        before = items[index - 1] if index > 0 else None
        after = items[index + 1] if index + 1 < len(items) else None
        before_video = bool(before and before.get("файл"))
        after_video = bool(after and after.get("файл"))
        if before_video and after_video:
            continue                      # двусмысленно — не берём вовсе
        target = before if before_video else (after if after_video else None)
        if target is None:
            continue
        key = target["файл"]
        if key in result:
            continue                      # у видео уже есть текст рядом
        result[key] = item["текст"]
    return result


class Recognizer:
    """Ленивая обёртка над Whisper: модель грузится только при первой нужде.

    Если видеокарта не годится (нет библиотек CUDA — частый случай),
    молча переходит на процессор: медленнее, но работает везде.
    """

    def __init__(self, model_size="small", seconds=40, use_ocr=False,
                 enabled=True, device="auto"):
        self.model_size = model_size
        self.seconds = seconds
        self.use_ocr = use_ocr
        self._model = None
        self._model_failed = not enabled
        self.device = device or "auto"
        if self.device == "auto":
            # Если в прошлый раз видеокарта уже подвела — не пробуем снова.
            remembered = read_setting("recognition", "device", "")
            if remembered in ("cpu", "cuda"):
                self.device = remembered

    def _switch_to_cpu(self, exc):
        log("! Видеокарта для распознавания не годится: %s" % _short(exc))
        log("  Перехожу на процессор. Будет медленнее, но надёжно.")
        self.device = "cpu"
        self._model = None
        write_setting("recognition", "device", "cpu")

    def _get_model(self):
        if self._model is not None or self._model_failed:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            log("! faster-whisper не установлен — распознавание речи выключено.")
            self._model_failed = True
            return None

        log("  загружаю модель распознавания речи (%s, %s), "
            "первый раз это долго..." % (self.model_size, self.device))
        try:
            self._model = WhisperModel(self.model_size, device=self.device,
                                       compute_type="int8")
        except Exception as exc:                          # noqa: BLE001
            if self.device != "cpu":
                self._switch_to_cpu(exc)
                return self._get_model()
            log("! Не удалось загрузить модель: %s" % exc)
            self._model_failed = True
            return None
        return self._model

    def _extract_audio(self, video_path):
        """Вырезает первые N секунд звука. Возвращает путь к wav или ''."""
        handle, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path,
               "-t", str(self.seconds), "-vn", "-ac", "1", "-ar", "16000",
               wav_path]
        try:
            if subprocess.call(cmd) == 0 and os.path.getsize(wav_path) > 1024:
                return wav_path
        except OSError as exc:
            log("! Не смог достать звук из видео: %s" % exc)
        os.remove(wav_path)
        return ""

    def transcribe(self, video_path):
        """Расшифровывает первые N секунд видео. Возвращает текст или ''."""
        if self._model_failed or not has_ffmpeg():
            return ""
        wav_path = self._extract_audio(video_path)
        if not wav_path:
            return ""
        try:
            # Первая попытка может упереться в видеокарту — тогда повторяем
            # на процессоре, уже без потери этого файла.
            for attempt in (1, 2):
                model = self._get_model()
                if model is None:
                    return ""
                try:
                    segments, _ = model.transcribe(wav_path, language="ru",
                                                   beam_size=5, vad_filter=True)
                    return " ".join(seg.text for seg in segments).strip()
                except Exception as exc:                  # noqa: BLE001
                    if attempt == 1 and self.device != "cpu":
                        self._switch_to_cpu(exc)
                        continue
                    log("! Ошибка распознавания речи: %s" % exc)
                    return ""
            return ""
        finally:
            if os.path.exists(wav_path):
                os.remove(wav_path)

    def read_frames(self, video_path):
        """Читает текст с нескольких первых кадров (номер на двери и т.п.)."""
        if not self.use_ocr or not has_ffmpeg():
            return ""
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            log("! Для --ocr нужны pytesseract и Pillow — пропускаю кадры.")
            self.use_ocr = False
            return ""
        work = tempfile.mkdtemp()
        try:
            cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path,
                   "-t", str(self.seconds), "-vf", "fps=1/2,scale=1280:-1",
                   "-frames:v", "6", os.path.join(work, "кадр_%02d.png")]
            if subprocess.call(cmd) != 0:
                return ""
            chunks = []
            for name in sorted(os.listdir(work)):
                try:
                    chunks.append(pytesseract.image_to_string(
                        Image.open(os.path.join(work, name)), lang="rus+eng"))
                except Exception:                         # noqa: BLE001
                    continue
            return " ".join(chunks).strip()
        finally:
            shutil.rmtree(work, ignore_errors=True)
