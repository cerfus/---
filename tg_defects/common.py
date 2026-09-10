# -*- coding: utf-8 -*-
"""Общая часть: распознавание, отчёт, состояние. Используют оба скрипта."""

import configparser
import csv
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
