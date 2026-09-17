#!/usr/bin/env python3
"""Прогон слоя признаков -> JSONL.

Запись в PostgreSQL НЕ выполняется: единственная существующая таблица
video_features имеет PRIMARY KEY (video_id) без версионного измерения и
выданный рантайму UPDATE, поэтому пересчёт под новой версией политики
затирал бы прошлый результат. Решение по хранилищу принимает владелец
проекта; до него слой живёт в append-only JSONL, где версия политики
входит в имя файла.
"""
import glob
import hashlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from features import engine
from features import policies as F

OUT = ROOT / "data" / "features"
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000004")
VOLATILE = {"computed_at", "run_id"}

DB_PERSISTENCE_BLOCKED = {
    "blocked": True,
    "reason": "video_features: PRIMARY KEY (video_id) без версии + UPDATE у рантайма",
    "consequence": "пересчёт под новой policy_version затёр бы прошлый результат",
    "decision_required": "владелец выбирает вариант хранилища",
}


def _jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def load_inputs():
    videos = _jsonl(ROOT / "data" / "videos.jsonl")
    snaps = [r for f in sorted(glob.glob(str(ROOT / "data" / "snapshots" / "*.jsonl")))
             for r in _jsonl(f)]
    results = _jsonl(ROOT / "data" / "reconciliation" / "results.jsonl")
    return videos, snaps, results


def content_hash(rows):
    clean = [{k: v for k, v in sorted(r.items()) if k not in VOLATILE} for r in rows]
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


def build(write=True):
    videos, snaps, results = load_inputs()
    rows = engine.build(videos, results, snaps)
    h = content_hash(rows)
    run_id = str(uuid.uuid5(RUN_NS, h))
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        # версия политики входит в ИМЯ файла: новая версия даёт новый файл,
        # а не переписывает прошлый результат
        name = f"features_{F.FEATURE_POLICY_VERSION}.jsonl"
        for f in OUT.glob(f"features_{F.FEATURE_POLICY_VERSION}*.jsonl"):
            f.unlink()
        with (OUT / name).open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        (OUT / "manifest.json").write_text(json.dumps({
            "feature_hash": h, "feature_run_id": run_id,
            "policy_version": F.FEATURE_POLICY_VERSION,
            "engine_version": engine.FEATURE_ENGINE_VERSION,
            "n_rows": len(rows),
            "n_videos": len({r["video_id"] for r in rows}),
            "n_feature_names": len({r["feature_name"] for r in rows}),
            "db_persistence": DB_PERSISTENCE_BLOCKED,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rows, h, run_id


if __name__ == "__main__":
    import collections
    rows, h, run_id = build()
    print(f"строк признаков: {len(rows)} | роликов: {len({r['video_id'] for r in rows})}"
          f" | имён признаков: {len({r['feature_name'] for r in rows})}")
    by_tier = collections.Counter(r["tier"] for r in rows)
    by_status = collections.Counter(r["feature_status"] for r in rows)
    print("  по тирам:   " + ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())))
    print("  по статусу: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print(f"\nfeature_hash: {h}")
    print(f"feature_run_id: {run_id}")
    print(f"запись в PostgreSQL: ЗАБЛОКИРОВАНА — {DB_PERSISTENCE_BLOCKED['reason']}")
