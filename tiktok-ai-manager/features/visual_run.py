#!/usr/bin/env python3
"""Прогон Tier 1 -> JSONL -> PostgreSQL.

Выход намеренно лежит отдельно от Tier 0/0.5 (data/features_tier1/, а не
data/features/): слои версионируются независимо, и добавление Tier 1 не
имеет права сдвинуть feature_hash прежнего слоя. Хранилище при этом одно —
та же таблица video_features с идентичностью
(video_id, feature_name, policy_version, extractor_version).
"""
import collections
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from assets import policies as A
from assets import probe as PR
from assets import run as assets_run
from features import visual_engine as E
from features import visual_policies as V

OUT = ROOT / "data" / "features_tier1"
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000008")
VOLATILE = {"computed_at", "run_id"}

STANDARD_KEYS = {"video_id", "feature_name", "feature_value", "feature_type",
                 "tier", "feature_status", "status_reason", "policy_version",
                 "engine_version", "extractor_version", "source_basis",
                 "reconciliation_basis", "evidence_refs", "computed_from"}


def content_hash(rows):
    clean = [{k: v for k, v in sorted(r.items()) if k not in VOLATILE}
             for r in rows]
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


def build(write=True):
    videos = assets_run.load_videos()
    assets = assets_run.load_manifest()
    extractor_version = PR.extractor_version()
    rows = E.build_all(videos, assets, extractor_version)
    h = content_hash(rows)
    run_id = str(uuid.uuid5(RUN_NS, h))

    by_status = collections.Counter(r["feature_status"] for r in rows)
    by_group = collections.Counter(
        (r["feature_group"], r["feature_status"]) for r in rows)

    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "tier1_features.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps({**r, "run_id": run_id},
                                    ensure_ascii=False, sort_keys=True) + "\n")
        (OUT / "manifest.json").write_text(json.dumps({
            "tier1_hash": h, "tier1_run_id": run_id,
            "policy_version": V.VISUAL_FEATURE_POLICY_VERSION,
            "engine_version": E.VISUAL_ENGINE_VERSION,
            "extractor_version": extractor_version,
            "n_rows": len(rows), "n_videos": len(videos),
            "n_features_declared": len(V.TIER_1_FEATURES),
            "by_status": dict(sorted(by_status.items())),
            "by_group_status": {f"{g}/{s}": n
                                for (g, s), n in sorted(by_group.items())},
            "n_videos_with_current_asset": sum(
                1 for v in videos if A.current_asset(assets, v["video_id"])),
            "asset_hash": json.loads(
                (ROOT / "data" / "assets" / "manifest.json")
                .read_text(encoding="utf-8"))["asset_hash"],
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rows, h, run_id, by_status


def load_to_db(rows, run_id):
    import psycopg
    from core import config
    now = datetime.now(timezone.utc)
    n = 0
    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        for r in rows:
            extra = {k: v for k, v in r.items() if k not in STANDARD_KEYS}
            numeric = bool_val = None
            if r["feature_status"] not in V.ABSENT_STATUS:
                if r["feature_type"] == "numeric":
                    numeric = r["feature_value"]
                elif r["feature_type"] == "boolean":
                    bool_val = r["feature_value"] == "true"
            cur.execute("""INSERT INTO video_features (
                  video_id, feature_name, feature_value, feature_value_numeric,
                  feature_value_bool, feature_type, feature_status, status_reason,
                  tier, policy_version, extractor_version, source_basis,
                  reconciliation_basis, evidence_refs, computed_from, extra,
                  computed_at, run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                        %s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (video_id, feature_name, policy_version,
                             extractor_version) DO NOTHING""",
                (r["video_id"], r["feature_name"], r["feature_value"], numeric,
                 bool_val, r["feature_type"], r["feature_status"],
                 r["status_reason"], r["tier"], r["policy_version"],
                 r["extractor_version"],
                 json.dumps(r["source_basis"], ensure_ascii=False, sort_keys=True),
                 json.dumps(r["reconciliation_basis"], ensure_ascii=False,
                            sort_keys=True),
                 r["evidence_refs"], r["computed_from"],
                 json.dumps(extra, ensure_ascii=False, sort_keys=True),
                 now, run_id))
            n += cur.rowcount
        conn.commit()
    return n


if __name__ == "__main__":
    rows, h, run_id, by_status = build()
    print(f"Tier 1: строк {len(rows)} "
          f"({len(V.TIER_1_FEATURES)} признаков x "
          f"{len(assets_run.load_videos())} роликов)")
    for st, n in sorted(by_status.items()):
        print(f"  {st:<22}{n}")
    print(f"\nextractor_version: {PR.extractor_version()}")
    print(f"tier1_hash: {h}")
    print(f"tier1_run_id: {run_id}")
    if "--load" in sys.argv:
        print(f"записано в PostgreSQL: {load_to_db(rows, run_id)}")
