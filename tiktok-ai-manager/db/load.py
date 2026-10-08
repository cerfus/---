#!/usr/bin/env python3
"""JSONL -> PostgreSQL. База ПЕРЕСОБИРАЕМА и источником истины не является.

Загрузка идемпотентна: повторный прогон не плодит строки благодаря
уникальным ключам, а не благодаря предварительной очистке.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg
from core import config

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SNAP_COLS = ["video_id", "source", "fetched_at", "observed_at", "observed_at_precision",
             "observed_at_authority", "age_seconds", "age_bucket", "bucket_offset_sec",
             "views", "reach", "likes", "comments", "shares", "favorites",
             "completion_rate", "avg_view_time_sec", "total_time_watched_sec",
             "src_foryou", "src_hashtag", "src_sound", "src_search", "src_profile",
             "raw_ref", "run_id"]


def jsonl(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load(verbose=True):
    account = jsonl(DATA / "account.jsonl")[0]
    videos = jsonl(DATA / "videos.jsonl")
    snaps = []
    for f in sorted((DATA / "snapshots").glob("*.jsonl")):
        snaps += jsonl(f)

    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO accounts (platform, handle, owner_identity, account_type,
                                  timezone, timezone_semantics, metricool_brand_id,
                                  supermetrics_ds_id, connected_at)
            VALUES (%(platform)s, %(handle)s, %(owner_identity)s, %(account_type)s,
                    %(timezone)s, %(timezone_semantics)s, %(metricool_brand_id)s,
                    %(supermetrics_ds_id)s, %(connected_at)s)
            ON CONFLICT (platform, handle) DO UPDATE SET connected_at = EXCLUDED.connected_at
            RETURNING account_id""", account)
        account_id = cur.fetchone()[0]

        n_v = 0
        for v in videos:
            cur.execute("""
                INSERT INTO videos (video_id, account_id, published_at, published_at_src,
                                    url, share_url, caption, caption_len,
                                    duration_sec, duration_src, is_pre_connection,
                                    first_seen_run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (video_id) DO NOTHING""",
                (v["video_id"], account_id, v["published_at"], v["published_at_src"],
                 v.get("url"), v.get("share_url"), v.get("caption"), v.get("caption_len"),
                 v["duration_sec"], v["duration_src"], v["is_pre_connection"],
                 v["first_seen_run_id"]))
            n_v += cur.rowcount

        n_s = 0
        cols = ", ".join(SNAP_COLS)
        ph = ", ".join(f"%({c})s" for c in SNAP_COLS)
        for s in snaps:
            cur.execute(
                f"INSERT INTO video_snapshots ({cols}) VALUES ({ph})"
                " ON CONFLICT (video_id, source, observed_at) DO NOTHING",
                {c: s.get(c) for c in SNAP_COLS})
            n_s += cur.rowcount
        conn.commit()

    if verbose:
        print(f"accounts: 1 | videos: +{n_v} из {len(videos)} | "
              f"snapshots: +{n_s} из {len(snaps)}")
    return {"videos": len(videos), "snapshots": len(snaps)}


if __name__ == "__main__":
    load()
