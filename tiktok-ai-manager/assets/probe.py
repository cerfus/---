#!/usr/bin/env python3
"""Промер видеофайла. ТОЛЬКО то, что действительно читается из файла.

Модуль ничего не интерпретирует. Он открывает контейнер, считает хеш,
достаёт геометрию и частоту кадров, смотрит наличие аудиодорожки и считает
смены планов по фиксированному правилу. Всё, чего он сделать не может,
возвращается как причина недоступности, а не как значение по умолчанию.

Детерминизм: случайности нет нигде. Кадры читаются последовательно, шаг
выборки выводится из частоты кадров, пороги заданы в политике. Один и тот
же файл при той же версии экстрактора даёт тот же результат.
"""
import hashlib
import json
import subprocess
from pathlib import Path

from assets import policies as A

PROBE_VERSION = "tier1-probe-1.0.0"

# Правило выборки кадров. Фиксировано в версии экстрактора: изменение шага
# или порога — это новая версия, а не молчаливая правка.
SAMPLE_FPS = 5.0                 # сколько кадров в секунду анализируется
SCENE_DIFF_THRESHOLD = 0.18      # доля средней абсолютной разницы яркости
ANALYSIS_SIDE = 64               # сторона кадра после приведения к серому
OPENING_WINDOW_SEC = 3.0         # окно «начала ролика» для opening-признаков
HASH_CHUNK = 1024 * 1024


def tooling():
    """Что из инструментов промера реально доступно в этом окружении."""
    out = {"cv2": None, "numpy": None, "ffmpeg": None}
    try:
        import cv2
        out["cv2"] = cv2.__version__
    except Exception:
        pass
    try:
        import numpy
        out["numpy"] = numpy.__version__
    except Exception:
        pass
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([exe, "-version"], capture_output=True, text=True,
                           timeout=30)
        # Только номер версии: полная строка содержит URL сборки и пробелы,
        # а extractor_version должен оставаться коротким ключом.
        first = r.stdout.splitlines()[0] if r.stdout else ""
        parts = first.split()
        out["ffmpeg"] = parts[2] if len(parts) > 2 and parts[0] == "ffmpeg" else None
    except Exception:
        pass
    return out


def extractor_version(tools=None):
    """Версия экстрактора включает версии библиотек намеренно.

    Другой декодер может вернуть другие кадры, и результат, полученный им,
    не тождествен прежнему. Прятать это в одной строке «1.0.0» значило бы
    утверждать воспроизводимость, которой нет.
    """
    t = tools if tools is not None else tooling()
    parts = [PROBE_VERSION]
    for k in ("cv2", "numpy", "ffmpeg"):
        parts.append(f"{k}={t[k] or 'absent'}")
    return "+".join(parts)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _invalid(reason, detail=None, **known):
    out = {"ok": False, "status_reason": reason, "detail": detail or {}}
    out.update(known)
    return out


def _audio_streams(path):
    """Наличие аудиодорожки по выводу ffmpeg. None — определить нечем."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    text = r.stderr or ""
    if "Stream #" not in text:
        return None
    return any("Audio:" in line for line in text.splitlines()
               if "Stream #" in line)


def _scene_stats(path, fps, frame_count):
    """Смены планов по фиксированному правилу разницы яркости.

    Возвращает (stats, None) либо (None, причина). Правило простое и
    объявленное: это НЕ детектор монтажа, а воспроизводимый счётчик
    скачков яркости между соседними выбранными кадрами.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return None, A.REASON_NO_TOOLING

    stride = max(1, int(round(fps / SAMPLE_FPS))) if fps else 1
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None, A.REASON_DECODE_FAILED

    prev = None
    idx = 0
    read = 0
    diffs = []
    changes = []
    first_frame_mean = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                   (ANALYSIS_SIDE, ANALYSIS_SIDE),
                                   interpolation=cv2.INTER_AREA)
                cur = small.astype(np.float32) / 255.0
                if first_frame_mean is None:
                    first_frame_mean = float(cur.mean())
                if prev is not None:
                    d = float(np.abs(cur - prev).mean())
                    diffs.append(d)
                    if d >= SCENE_DIFF_THRESHOLD:
                        changes.append(idx)
                prev = cur
                read += 1
            idx += 1
    finally:
        cap.release()

    if read == 0:
        return None, A.REASON_NO_FRAMES
    return {
        "frames_read": idx, "frames_analysed": read, "stride": stride,
        "scene_change_count": len(changes),
        "scene_change_frames": changes,
        "shot_count": len(changes) + 1,
        "first_frame_mean_luma": round(first_frame_mean, 6),
        "max_diff": round(max(diffs), 6) if diffs else None,
        "opening_window_sec": OPENING_WINDOW_SEC,
        "opening_scene_changes": len(
            [c for c in changes if fps and c / fps < OPENING_WINDOW_SEC]),
        "opening_frames_decoded": len(
            [1 for i in range(0, idx, stride) if fps and i / fps < OPENING_WINDOW_SEC]),
    }, None


def probe(path):
    """Полный промер файла. Никаких предположений при неудаче."""
    p = Path(path)
    tools = tooling()
    base = {"probe_version": PROBE_VERSION, "tooling": tools,
            "extractor_version": extractor_version(tools)}

    if not p.exists() or not p.is_file():
        return _invalid(A.REASON_FILE_MISSING, {"path": str(p)}, **base)
    size = p.stat().st_size
    if size == 0:
        return _invalid(A.REASON_EMPTY, {"path": str(p)}, **base)

    with open(p, "rb") as fh:
        head = fh.read(16)
    mime = A.mime_from_signature(head)
    digest = sha256_of(p)
    known = {**base, "sha256": digest, "byte_size": size, "mime_type": mime}
    if mime is None:
        return _invalid(A.REASON_UNKNOWN_CONTAINER,
                        {"head_hex": head[:12].hex()}, **known)
    if mime not in A.ALLOWED_MIME:
        return _invalid(A.REASON_UNSUPPORTED_MIME, {"mime": mime}, **known)
    if tools["cv2"] is None:
        return _invalid(A.REASON_NO_TOOLING, {"missing": "cv2"}, **known)

    import cv2
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        cap.release()
        return _invalid(A.REASON_DECODE_FAILED, {"mime": mime}, **known)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    declared_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if width <= 0 or height <= 0 or fps <= 0:
        return _invalid(A.REASON_DECODE_FAILED,
                        {"width": width, "height": height, "fps": fps}, **known)

    stats, why = _scene_stats(p, fps, declared_frames)
    if stats is None:
        return _invalid(why, {"declared_frames": declared_frames}, **known)

    # Счётчик кадров берётся ИЗ ФАКТИЧЕСКОГО чтения, а не из заголовка:
    # заголовок недокачанного файла обещает больше, чем в нём есть.
    frame_count = stats["frames_read"]
    if declared_frames > 0 and frame_count < declared_frames * 0.9:
        return _invalid(A.REASON_TRUNCATED,
                        {"declared_frames": declared_frames,
                         "decoded_frames": frame_count}, **known)

    return {
        "ok": True, "status_reason": None,
        **known,
        "width": width, "height": height,
        "fps": round(fps, 4),
        "frame_count": frame_count,
        "declared_frame_count": declared_frames,
        "duration_sec": round(frame_count / fps, 3),
        "audio_present": _audio_streams(p),
        "scene": stats,
    }


def evidence_ref(kind, **fields):
    """Ссылка-доказательство в стабильной текстовой форме."""
    return f"{kind}:" + json.dumps(fields, ensure_ascii=False, sort_keys=True)
