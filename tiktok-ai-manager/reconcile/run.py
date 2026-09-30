#!/usr/bin/env python3
"""Прогон сверки: наблюдения -> результаты JSONL -> PostgreSQL.

reconciliation_run_id идентифицирует запуск и НЕ входит в содержимое
результата: content_hash считается без него, поэтому два запуска на
одних данных дают одинаковый хеш.
"""
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from normalize import observations
from reconcile import policies as P
from reconcile.engine import reconcile

OUT = ROOT / "data" / "reconciliation"

# run_id детерминирован по содержимому: один и тот же вход -> один и тот же id.
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000002")

CONTENT_KEYS = ("video_id", "metric", "slice", "classification", "rule_id",
                "source_a", "source_b", "value_a", "value_b",
                "observation_id_a", "observation_id_b",
                "canonical_source", "lagging_source", "lag_basis",
                "engine_version", "policy_version")


def row_hash(r):
    payload = json.dumps({k: r.get(k) for k in CONTENT_KEYS},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_of(r):
    ev = {"values": {r["source_a"]: r.get("value_a"), r["source_b"]: r.get("value_b")},
          "observations": {r["source_a"]: r.get("observation_id_a"),
                           r["source_b"]: r.get("observation_id_b")},
          "observed_at": {r["source_a"]: r.get("observed_at_a"),
                          r["source_b"]: r.get("observed_at_b")},
          "slice": r["slice"]}
    for k in ("lag_evidence", "absolute_difference", "relative_difference",
              "statement", "competing_explanation"):
        if k in r and r[k] is not None:
            ev[k] = r[k]
    return ev


def build(write=True):
    obs = observations.load()
    results, content_hash = reconcile(obs)
    run_id = str(uuid.uuid5(RUN_NS, content_hash))
    for r in results:
        r["content_hash"] = row_hash(r)
        r["evidence"] = evidence_of(r)
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        for f in OUT.glob("*.jsonl"):
            f.unlink()
        with (OUT / "results.jsonl").open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        (OUT / "manifest.json").write_text(json.dumps({
            "content_hash": content_hash,
            "reconciliation_run_id": run_id,
            "n_results": len(results),
            "engine_version": results[0]["engine_version"] if results else None,
            "policy_version": P.POLICY_VERSION,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return results, content_hash, run_id


INSERT = """INSERT INTO reconciliation_results (
  reconciliation_run_id, video_id, metric, slice_key, classification, rule_id,
  source_a, source_b, value_a, value_b, observation_id_a, observation_id_b,
  observed_at_a, observed_at_b, canonical_source, lagging_source, lag_basis,
  competing_explanation, evidence, engine_version, policy_version,
  content_hash, computed_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
ON CONFLICT (reconciliation_run_id, video_id, metric, slice_key) DO NOTHING"""


def load_to_db(results, run_id, computed_at=None):
    import psycopg
    from core import config
    computed_at = computed_at or datetime.now(timezone.utc)
    n = 0
    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        for r in results:
            cur.execute(INSERT, (
                run_id, r["video_id"], r["metric"], str(r["slice"]), r["classification"],
                r["rule_id"], r["source_a"], r["source_b"], r.get("value_a"),
                r.get("value_b"), r.get("observation_id_a"), r.get("observation_id_b"),
                r.get("observed_at_a"), r.get("observed_at_b"), r.get("canonical_source"),
                r.get("lagging_source"), r.get("lag_basis"), r.get("competing_explanation"),
                json.dumps(r["evidence"], ensure_ascii=False, sort_keys=True),
                r["engine_version"], r["policy_version"], r["content_hash"], computed_at))
            n += cur.rowcount
        conn.commit()
    return n


if __name__ == "__main__":
    import collections
    results, content_hash, run_id = build()
    c = collections.Counter(r["classification"] for r in results)
    print(f"классификаций: {len(results)}")
    for k, v in sorted(c.items()):
        mark = "FACT разрешён" if P.fact_allowed(k) else "FACT ЗАПРЕЩЁН"
        print(f"  {k:<26}{v:>5}   {mark}")
    print(f"\ncontent_hash: {content_hash}")
    print(f"run_id:       {run_id}")
    if "--load" in sys.argv:
        n = load_to_db(results, run_id)
        print(f"записано в PostgreSQL: {n}")
