#!/usr/bin/env python3
"""raw -> наблюдения (video × source × metric × момент).

Зачем отдельный слой поверх снимков: video_snapshots хранит канонические
метрики временного ряда и НЕ хранит длительность каждого источника по
отдельности — она свёрнута в videos.duration_sec. Сверке же нужны оба
исходных значения, иначе доказанное правило floor() проверить не на чем.

Наблюдения — производный артефакт: они детерминированно пересобираются из
data/raw/ и источником истины не являются. Существующие таблицы и снимки
не изменяются.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from normalize.normalize import (ROOT, cast_metric, observation_time, parse_published,
                                 read_raw, to_utc, vid_from_url)

OUT_DIR = ROOT / "data" / "observations"

# Метрики, которые вообще могут наблюдаться. duration_sec включена именно
# потому, что в снимках её нет.
OBSERVABLE = ["views", "reach", "likes", "comments", "shares", "favorites",
              "completion_rate", "avg_view_time_sec", "total_time_watched_sec",
              "duration_sec", "src_foryou", "src_hashtag", "src_sound",
              "src_search", "src_profile"]


def observation_id(run_id, source, video_id, metric):
    """Детерминированный идентификатор: run_id выводится из имени файла."""
    return f"{run_id[:8]}:{source}:{video_id}:{metric}"


def build():
    rows = []
    for path, meta, src, hdr, raw_rows in read_raw():
        obs_at, precision, authority = observation_time(meta)
        run_id = _run_id(path.name)
        for i, row in enumerate(raw_rows):
            rec = dict(zip(hdr, row))
            vid = rec.get("video_id") or vid_from_url(rec.get("url") or rec["share_url"])
            raw_pub = rec.get("create_datetime") or rec.get("published")
            published = to_utc(parse_published(raw_pub)).isoformat() if raw_pub else None
            for m in OBSERVABLE:
                if m not in rec:
                    continue                      # поле не запрашивалось у источника
                rows.append({
                    "observation_id": observation_id(run_id, src, vid, m),
                    "video_id": vid, "source": src, "metric": m,
                    "value": cast_metric(m, rec.get(m)),
                    "observed_at": obs_at.isoformat(),
                    "observed_at_precision": precision,
                    "observed_at_authority": authority,
                    "published_at": published,
                    "raw_ref": f"data/raw/{path.name}#rows[{i}]",
                    "run_id": run_id,
                })
    rows.sort(key=lambda r: (r["video_id"], r["metric"], r["observed_at"],
                             r["source"], r["observation_id"]))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for f in OUT_DIR.glob("phase2_*.jsonl"):
        f.unlink()
    parts = {}
    for r in rows:
        parts.setdefault(r["observed_at"][:10], []).append(r)
    for day, rs in sorted(parts.items()):
        with (OUT_DIR / f"phase2_{day}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return rows


def _run_id(filename):
    import uuid
    from normalize.normalize import RUN_NS
    return str(uuid.uuid5(RUN_NS, filename))


def load():
    return [json.loads(l) for f in sorted(OUT_DIR.glob("phase2_*.jsonl"))
            for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


if __name__ == "__main__":
    rows = build()
    import collections
    print(f"наблюдений: {len(rows)}")
    print(f"роликов: {len({r['video_id'] for r in rows})} | "
          f"метрик: {len({r['metric'] for r in rows})}")
    c = collections.Counter((r["source"], r["observed_at"], r["observed_at_precision"])
                            for r in rows)
    for k, v in sorted(c.items(), key=lambda x: (x[0][1], x[0][0])):
        print(f"  {k[0]:<13}{k[1]:<28}{k[2]:<8}{v:>5}")
    nn = sum(1 for r in rows if r["value"] is not None)
    print(f"непустых: {nn} | NULL: {len(rows) - nn}")
