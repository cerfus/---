#!/usr/bin/env python3
"""raw -> JSONL. Детерминированно: повторный прогон даёт побайтово тот же файл.

Source of truth — JSONL. PostgreSQL пересобирается из них и источником истины
не является. Сырьё в data/raw/ не редактируется никогда.
"""
import json
import math
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT_VIDEOS = ROOT / "data" / "videos.jsonl"
SNAP_DIR = ROOT / "data" / "snapshots"

# run_id выводится из имени файла: при пересборке он тот же, и сравнение
# состояния не натыкается на случайные идентификаторы.
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000001")

HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
MENTION = re.compile(r"@([\w.]+)", re.UNICODE)

ACCOUNT = {
    "platform": "tiktok",
    "handle": "gulyashik52",
    "owner_identity": "dopoks050586@gmail.com",
    "account_type": "business",
    "timezone": "Europe/Moscow",
    "timezone_semantics": "unverified",     # OQ-1 не закрыт
    "metricool_brand_id": 7004662,
    "supermetrics_ds_id": "TIKBA",
    "connected_at": "2026-09-17T06:54:38+00:00",   # 08:54:38 Europe/Madrid = CEST
}

METRIC_COLS = ["views", "reach", "likes", "comments", "shares", "favorites",
               "completion_rate", "avg_view_time_sec", "total_time_watched_sec",
               "src_foryou", "src_hashtag", "src_sound", "src_search", "src_profile"]

R1_METRICOOL_HEADER = ["published", "url", "description", "duration_sec", "views",
                       "likes", "comments", "shares", "reach", "completion_rate",
                       "avg_view_time_sec"]


def canonical_source(name):
    n = name.strip().lower().replace(" mcp", "")
    if n not in ("supermetrics", "metricool"):
        raise ValueError(f"неизвестный источник: {name!r}")
    return n


def vid_from_url(u):
    return u.rstrip("/").split("/")[-1].split("?")[0]


def parse_published(s):
    if "-" in s:
        return s.replace(" ", "T")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}"


def to_utc(s):
    """Все метки приводятся к UTC. Локальную зону аккаунта не предполагаем."""
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def observation_time(meta):
    if meta.get("observed_at_source"):
        return to_utc(meta["observed_at_source"]), "second", "source_cache_time"
    at = meta.get("fetched_at") or meta.get("pulled_at")
    if "T" in at:
        return to_utc(at), "second", "client_fetch_time"
    return to_utc(at + "T00:00:00+00:00"), "date", "client_fetch_time"


def read_raw():
    """Все пригодные сырые файлы, отсортированные по имени."""
    for path in sorted(RAW.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        meta = doc["_meta"]
        if meta.get("usable_as_observation") is False:
            continue
        src = canonical_source(meta["source"])
        hdr = doc.get("header") or (R1_METRICOOL_HEADER if src == "metricool" else None)
        if hdr is None:
            raise ValueError(f"{path.name}: нет header и нет известной раскладки")
        yield path, meta, src, hdr, doc["rows"]


def normalize():
    connected = to_utc(ACCOUNT["connected_at"])
    videos, snaps = {}, []

    for path, meta, src, hdr, rows in read_raw():
        obs_at, precision, authority = observation_time(meta)
        fetched = to_utc(meta.get("fetched_at") or meta.get("pulled_at") + "T00:00:00+00:00"
                         if "T" not in (meta.get("fetched_at") or meta.get("pulled_at"))
                         else meta.get("fetched_at"))
        run_id = str(uuid.uuid5(RUN_NS, path.name))

        for i, row in enumerate(rows):
            rec = dict(zip(hdr, row))
            vid = rec.get("video_id") or vid_from_url(rec.get("url") or rec["share_url"])
            raw_ref = f"data/raw/{path.name}#rows[{i}]"

            raw_pub = rec.get("create_datetime") or rec.get("published")
            if raw_pub:
                pub = to_utc(parse_published(raw_pub))
                v = videos.setdefault(vid, {"video_id": vid, "first_seen_run_id": run_id})
                # supermetrics точнее: секунды и 3 знака длительности
                if src == "supermetrics" or "published_at" not in v:
                    v["published_at"] = pub.isoformat()
                    v["published_at_src"] = src
                if rec.get("duration_sec") is not None:
                    d = float(rec["duration_sec"])
                    if src == "supermetrics" or "duration_sec" not in v:
                        v["duration_sec"] = d
                        v["duration_src"] = src
                cap = rec.get("description")
                if cap is not None and (src == "metricool" or "caption" not in v):
                    v["caption"] = cap.strip()
                    v["caption_len"] = len(cap.strip())
                for key, col in (("url", "url"), ("share_url", "share_url")):
                    if rec.get(col):
                        v.setdefault(key, rec[col].split("?")[0])
                v["is_pre_connection"] = pub < connected
                v["account_handle"] = ACCOUNT["handle"]

            age = None
            if precision == "second" and raw_pub:
                age = int((obs_at - to_utc(parse_published(raw_pub))).total_seconds())
            snap = {
                "video_id": vid, "source": src,
                "fetched_at": fetched.isoformat(), "observed_at": obs_at.isoformat(),
                "observed_at_precision": precision, "observed_at_authority": authority,
                "age_seconds": age,
                "age_bucket": "backfill",     # все ролики опубликованы до подключения
                "bucket_offset_sec": None,
                "raw_ref": raw_ref, "run_id": run_id,
            }
            for m in METRIC_COLS:
                snap[m] = cast_metric(m, rec.get(m))
            snaps.append(snap)

    # длительность из metricool усечена: восстанавливаем точное значение
    for v in videos.values():
        if v.get("duration_src") == "metricool":
            v["duration_sec"] = float(v["duration_sec"])

    write_jsonl(OUT_VIDEOS, sorted(videos.values(), key=lambda x: x["video_id"]))

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    for f in SNAP_DIR.glob("*.jsonl"):
        f.unlink()
    parts = {}
    for s in snaps:
        key = f"{s['observed_at'][:10]}_{s['run_id'][:8]}"
        parts.setdefault(key, []).append(s)
    for key, rows in sorted(parts.items()):
        rows.sort(key=lambda x: (x["video_id"], x["source"], x["observed_at"]))
        write_jsonl(SNAP_DIR / f"{key}.jsonl", rows)

    write_jsonl(ROOT / "data" / "account.jsonl", [ACCOUNT])
    return len(videos), len(snaps), len(parts)


INT_METRICS = {"views", "reach", "likes", "comments", "shares", "favorites",
               "total_time_watched_sec"}


def cast_metric(name, value):
    """Metricool отдаёт счётчики строками. NULL остаётся NULL и не становится 0."""
    if value is None:
        return None
    if name in INT_METRICS:
        return int(value)
    return float(value)


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    nv, ns, np_ = normalize()
    print(f"videos.jsonl: {nv} роликов")
    print(f"snapshots: {ns} строк в {np_} партициях")
    for f in sorted(SNAP_DIR.glob("*.jsonl")):
        print(f"  {f.name}: {sum(1 for _ in f.open(encoding='utf-8'))}")
