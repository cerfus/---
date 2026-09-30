#!/usr/bin/env python3
"""Политика видеоассетов.

Ассет — это файл, а не запись о файле. Поэтому его идентичность — хеш
содержимого, а не имя, путь или video_id. Из этого следует всё остальное:
одинаковые байты не создают второй ассет; один ролик может иметь несколько
версий; текущей становится последняя ВАЛИДНАЯ, а не последняя записанная.
"""
import uuid

ASSET_POLICY_VERSION = "asset-policy-1.0.0"

# Пространство имён для детерминированного asset_uid. Uuid5 от sha256 даёт
# один и тот же идентификатор содержимого в любом прогоне и в любом
# окружении — включая случай, когда те же байты приписаны другому ролику.
ASSET_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000006")

# Статус ассета. Неизменяемое свойство строки на момент регистрации:
# таблица append-only, «устаревание» вычисляется представлением.
ASSET_STATUS = ("valid", "invalid", "missing")

# Откуда файл взялся. Скачивания с TikTok в списке нет намеренно: медиа-URL
# не отдаёт ни один доступный источник, и придумывать такой путь нельзя.
ASSET_SOURCE = ("owner_upload", "manual_export", "not_supplied")

# Поля манифеста. Список закрыт: строка без любого из них не принимается,
# иначе «частично заполненный ассет» тихо станет нормой.
MANIFEST_FIELDS = (
    "asset_uid", "video_id", "asset_version", "source", "source_uri",
    "sha256", "byte_size", "mime_type", "duration_sec", "width", "height",
    "fps", "frame_count", "acquired_at", "extractor_version",
    "policy_version", "asset_status", "status_reason", "evidence_refs",
    "extra",
)

# Поля, без которых ассет не может быть валидным. Ровно они же проверяются
# CHECK-ом в БД — два независимых замка, как у append-only.
REQUIRED_FOR_VALID = ("sha256", "byte_size", "mime_type", "duration_sec",
                      "width", "height", "fps", "frame_count", "acquired_at")

# Контейнеры, которые слой готов принимать. Распознаются по сигнатуре
# файла, а не по расширению: расширение — это утверждение автора файла.
MIME_BY_SIGNATURE = {
    "ftypisom": "video/mp4", "ftypiso2": "video/mp4", "ftypmp41": "video/mp4",
    "ftypmp42": "video/mp4", "ftypavc1": "video/mp4", "ftypM4V ": "video/mp4",
    "ftypqt  ": "video/quicktime",
}
WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
ALLOWED_MIME = ("video/mp4", "video/quicktime", "video/webm")

# Причины, по которым файл признаётся негодным. Коды машинные: слой
# признаков обязан отличать «файла нет» от «файл битый» без разбора текста.
REASON_NOT_SUPPLIED = "asset_not_supplied"
REASON_FILE_MISSING = "file_not_found"
REASON_EMPTY = "file_empty"
REASON_UNKNOWN_CONTAINER = "unrecognised_container"
REASON_UNSUPPORTED_MIME = "unsupported_mime_type"
REASON_DECODE_FAILED = "decode_failed"
REASON_NO_FRAMES = "no_decodable_frames"
REASON_NO_TOOLING = "extractor_tooling_unavailable"
REASON_TRUNCATED = "truncated_or_incomplete"

INVALID_REASONS = (REASON_FILE_MISSING, REASON_EMPTY, REASON_UNKNOWN_CONTAINER,
                   REASON_UNSUPPORTED_MIME, REASON_DECODE_FAILED,
                   REASON_NO_FRAMES, REASON_NO_TOOLING, REASON_TRUNCATED)


def asset_uid(sha256):
    """Идентификатор содержимого. None, пока файла нет."""
    return str(uuid.uuid5(ASSET_NS, sha256)) if sha256 else None


def mime_from_signature(head):
    """MIME по первым байтам файла. None — контейнер не распознан."""
    if head[:4] == WEBM_MAGIC:
        return "video/webm"
    if len(head) >= 12:
        brand = head[4:12].decode("ascii", "replace")
        if brand in MIME_BY_SIGNATURE:
            return MIME_BY_SIGNATURE[brand]
        if head[4:8] == b"ftyp":
            return None          # ftyp есть, бренд незнаком
    return None


def validate_record(rec):
    """Нарушения политики в строке манифеста. Пустой список — строка годна."""
    v = []
    missing = [f for f in MANIFEST_FIELDS if f not in rec]
    if missing:
        v.append(f"нет обязательных полей: {', '.join(missing)}")
        return v
    if rec["asset_status"] not in ASSET_STATUS:
        v.append(f"неизвестный asset_status {rec['asset_status']!r}")
    if rec["source"] not in ASSET_SOURCE:
        v.append(f"неизвестный source {rec['source']!r}")
    if (rec["asset_status"] == "missing") != (rec["source"] == "not_supplied"):
        v.append("missing и not_supplied обязаны идти вместе")
    if rec["asset_status"] == "valid":
        absent = [f for f in REQUIRED_FOR_VALID if rec.get(f) in (None, "")]
        if absent:
            v.append(f"валидный ассет без промеров: {', '.join(absent)}")
        if rec.get("mime_type") not in ALLOWED_MIME:
            v.append(f"недопустимый mime {rec.get('mime_type')!r}")
    else:
        if not rec.get("status_reason"):
            v.append("невалидный ассет без причины")
    if rec["asset_status"] == "missing" and rec.get("sha256") is not None:
        v.append("у отсутствующего файла не может быть sha256")
    if rec.get("sha256") is not None:
        h = rec["sha256"]
        if not (isinstance(h, str) and len(h) == 64
                and all(c in "0123456789abcdef" for c in h)):
            v.append("sha256 не выглядит хешем")
        elif rec.get("asset_uid") != asset_uid(h):
            v.append("asset_uid не выведен из sha256")
    if not isinstance(rec.get("asset_version"), int) or rec["asset_version"] < 1:
        v.append("asset_version должен быть целым от 1")
    if not rec.get("evidence_refs"):
        v.append("ассет без evidence_refs")
    return v


def current_asset(records, video_id):
    """Текущий ассет ролика: последняя ВАЛИДНАЯ версия.

    Битый или отсутствующий файл текущим не становится ни при каких
    условиях — в этом весь смысл отдельной функции вместо max() по версии.
    """
    valid = [r for r in records
             if r["video_id"] == video_id and r["asset_status"] == "valid"]
    if not valid:
        return None
    return max(valid, key=lambda r: r["asset_version"])


def duplicate_of(records, video_id, sha256):
    """Уже зарегистрированная строка с тем же содержимым для того же ролика."""
    if sha256 is None:
        return None
    for r in records:
        if r["video_id"] == video_id and r.get("sha256") == sha256:
            return r
    return None


def next_version(records, video_id):
    versions = [r["asset_version"] for r in records if r["video_id"] == video_id]
    return max(versions) + 1 if versions else 1
