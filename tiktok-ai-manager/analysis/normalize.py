#!/usr/bin/env python3
"""Собирает videos.jsonl и snapshots.jsonl из сырых выгрузок в data/raw/.

Два источника намеренно не сливаются в одну строку: каждый снимок хранит
провенанс. Расхождение между источниками — это сигнал, что выводу доверять
нельзя, и оно должно быть видно, а не замазано.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
MENTION = re.compile(r"@([\w.]+)", re.UNICODE)


def video_id_from_url(url):
    return url.rstrip("/").split("/")[-1].split("?")[0]


def load_metricool(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    pulled = doc["_meta"]["pulled_at"]
    out = []
    for r in doc["rows"]:
        ts, url, caption, dur, views, likes, comments, shares = r[:8]
        out.append({
            "video_id": video_id_from_url(url),
            "url": url,
            "caption": caption,
            "published_at": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:{ts[12:14]}",
            "duration_sec": float(dur),          # Metricool округляет вниз
            "views": int(views),
            "likes": int(likes),
            "comments": int(comments),
            "shares": int(shares),
            "_pulled_at": pulled,
        })
    return out


def load_supermetrics(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    pulled = doc["_meta"]["pulled_at"]
    cols = doc["header"]
    out = []
    for r in doc["rows"]:
        row = dict(zip(cols, r))
        row["published_at"] = row.pop("create_datetime").replace(" ", "T")
        row["_pulled_at"] = pulled
        out.append(row)
    return out


def main():
    mt = load_metricool(RAW / "2026-09-17_metricool_posts.json")
    sm = load_supermetrics(RAW / "2026-09-17_supermetrics_videos.json")
    sm_by_id = {r["video_id"]: r for r in sm}

    # --- сверка источников -------------------------------------------------
    problems = []
    for m in mt:
        s = sm_by_id.get(m["video_id"])
        if s is None:
            problems.append(f"{m['video_id']}: нет в Supermetrics")
            continue
        for field in ("views", "likes", "shares"):
            if int(s[field]) != m[field]:
                problems.append(
                    f"{m['video_id']}: {field} Metricool={m[field]} Supermetrics={s[field]}")
    missing_in_mt = set(sm_by_id) - {m["video_id"] for m in mt}
    for vid in sorted(missing_in_mt):
        problems.append(f"{vid}: нет в Metricool")

    # --- videos.jsonl (неизменяемые свойства ролика) -----------------------
    videos = []
    for m in sorted(mt, key=lambda x: x["published_at"]):
        s = sm_by_id.get(m["video_id"], {})
        videos.append({
            "video_id": m["video_id"],
            "published_at": m["published_at"],
            "url": m["url"],
            "caption": m["caption"].strip(),
            # Supermetrics даёт точную длительность, Metricool — усечённую
            "duration_sec": float(s.get("duration_sec", m["duration_sec"])),
            "hashtags": HASHTAG.findall(m["caption"]),
            "mentions": MENTION.findall(m["caption"]),
            "caption_len": len(m["caption"].strip()),
        })

    # --- snapshots.jsonl (метрики на момент замера, по одному на источник) --
    snaps = []
    for m in sorted(mt, key=lambda x: x["published_at"]):
        snaps.append({
            "video_id": m["video_id"], "snapshot_at": m["_pulled_at"], "source": "metricool",
            "views": m["views"], "likes": m["likes"],
            "comments": m["comments"], "shares": m["shares"],
            "reach": None, "favorites": None,
            "completion_rate": None, "avg_view_time_sec": None, "total_time_watched_sec": None,
        })
    for s in sorted(sm, key=lambda x: x["published_at"]):
        snaps.append({
            "video_id": s["video_id"], "snapshot_at": s["_pulled_at"], "source": "supermetrics",
            "views": int(s["views"]), "likes": int(s["likes"]),
            "comments": None, "shares": int(s["shares"]),
            "reach": int(s["reach"]), "favorites": int(s["favorites"]),
            "completion_rate": float(s["completion_rate"]),
            "avg_view_time_sec": float(s["avg_view_time_sec"]),
            "total_time_watched_sec": int(s["total_time_watched_sec"]),
        })

    write(ROOT / "data" / "videos.jsonl", videos)
    write(ROOT / "data" / "snapshots.jsonl", snaps)

    print(f"videos.jsonl    : {len(videos)} роликов")
    print(f"snapshots.jsonl : {len(snaps)} снимков "
          f"({len(mt)} metricool + {len(sm)} supermetrics)")
    if problems:
        print("\nРАСХОЖДЕНИЯ МЕЖДУ ИСТОЧНИКАМИ:")
        for p in problems:
            print("  !", p)
        return 1
    print("\nСверка источников: расхождений нет "
          "(views/likes/shares совпадают по всем роликам).")
    return 0


def write(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
