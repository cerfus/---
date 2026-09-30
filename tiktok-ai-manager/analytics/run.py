#!/usr/bin/env python3
"""Прогон аналитики: JSONL -> аналитические артефакты -> PostgreSQL.

analytics_run_id и computed_at идентифицируют запуск и В СОДЕРЖИМОЕ НЕ ВХОДЯТ:
analytics_hash считается без них, поэтому два прогона на одних данных дают
один хеш.
"""
import glob
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analytics import association, baseline
from analytics import policies as A

OUT = ROOT / "data" / "analytics"
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000003")
VOLATILE = {"analytics_run_id", "computed_at"}


def _jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def load_inputs():
    videos = _jsonl(ROOT / "data" / "videos.jsonl")
    snaps = [r for f in sorted(glob.glob(str(ROOT / "data" / "snapshots" / "*.jsonl")))
             for r in _jsonl(f)]
    results = _jsonl(ROOT / "data" / "reconciliation" / "results.jsonl")
    return videos, snaps, results


def content_hash(artifacts):
    """Хеш содержимого без полей запуска."""
    clean = {name: [{k: v for k, v in sorted(row.items()) if k not in VOLATILE}
                    for row in rows]
             for name, rows in sorted(artifacts.items())}
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


def build(write=True):
    videos, snaps, results = load_inputs()
    vb = baseline.video_baseline(videos, results, snaps)
    ab = baseline.account_baseline(vb, total_n=len(videos))
    cov = baseline.coverage(results)
    assoc = association.measure_all(vb)

    # метка размера выборки на записи ролика берётся из окна его бакета
    n_by_bucket = {}
    for r in vb:
        n_by_bucket[r["age_bucket"]] = n_by_bucket.get(r["age_bucket"], 0) + 1
    for r in vb:
        r["sample_status"] = A.sample_status(n_by_bucket.get(r["age_bucket"], 0))

    artifacts = {"baseline_video": vb, "baseline_account": ab,
                 "coverage": cov, "association": assoc}
    h = content_hash(artifacts)
    run_id = str(uuid.uuid5(RUN_NS, h))

    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        for f in OUT.glob("*.jsonl"):
            f.unlink()
        for name, rows in sorted(artifacts.items()):
            with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        (OUT / "manifest.json").write_text(json.dumps({
            "analytics_hash": h, "analytics_run_id": run_id,
            "engine_version": baseline.ANALYTICS_ENGINE_VERSION,
            "policy_version": A.ANALYTICS_POLICY_VERSION,
            "counts": {k: len(v) for k, v in sorted(artifacts.items())},
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifacts, h, run_id


def load_to_db(artifacts, run_id):
    import psycopg
    from core import config
    now = datetime.now(timezone.utc)
    n = 0
    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        for r in artifacts["baseline_video"]:
            cur.execute("""INSERT INTO analytics_video_baseline (
                analytics_run_id, video_id, published_at, duration_sec, views, reach,
                likes, comments, shares, favorites, like_rate, comment_rate, share_rate,
                favorite_rate, engagement_rate, avg_view_time_sec,
                avg_view_time_sec_derived, avg_view_time_derivation_matches,
                completion_rate, per_second_retention_status, age_bucket,
                age_seconds_observed_max, source_basis, reconciliation_basis, eligibility,
                observation_refs, reconciliation_refs, snapshot_refs,
                engine_version, policy_version, computed_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (analytics_run_id, video_id) DO NOTHING""",
                (run_id, r["video_id"], r["published_at"], r["duration_sec"], r["views"],
                 r["reach"], r["likes"], r["comments"], r["shares"], r["favorites"],
                 r["like_rate"], r["comment_rate"], r["share_rate"], r["favorite_rate"],
                 r["engagement_rate"], r["avg_view_time_sec"],
                 r["avg_view_time_sec_derived"], r["avg_view_time_derivation_matches"],
                 r["completion_rate"], r["per_second_retention_status"], r["age_bucket"],
                 r["age_seconds_observed_max"],
                 json.dumps(r["source_basis"], ensure_ascii=False, sort_keys=True),
                 json.dumps(r["reconciliation_basis"], ensure_ascii=False, sort_keys=True),
                 json.dumps(r["eligibility"], ensure_ascii=False, sort_keys=True),
                 r["observation_refs"], r["reconciliation_refs"], r["snapshot_refs"],
                 r["engine_version"], r["policy_version"], now))
            n += cur.rowcount
        for r in artifacts["baseline_account"]:
            cur.execute("""INSERT INTO analytics_account_baseline (
                analytics_run_id, window_name, age_bucket, metric, n, total_n, eligible_n,
                coverage_ratio, min_sample_required, sample_status, mean, median,
                min_value, max_value, p25, p75, p90, p90_status, iqr_lower_fence,
                iqr_upper_fence, n_outliers, outlier_rule, percentile_method,
                window_status, window_reason, engine_version, policy_version, computed_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s,%s)
              ON CONFLICT (analytics_run_id, window_name, metric) DO NOTHING""",
                (run_id, r["window"], r["age_bucket"], r["metric"], r["n"], r["total_n"],
                 r["eligible_n"], r["coverage_ratio"], r["min_sample_required"],
                 r["sample_status"], r["mean"], r["median"], r["min"], r["max"],
                 r["p25"], r["p75"], r["p90"], r["p90_status"],
                 r.get("iqr_lower_fence"), r.get("iqr_upper_fence"), r["n_outliers"],
                 r["outlier_rule"], r["percentile_method"], r["window_status"],
                 r["window_reason"], r["engine_version"], r["policy_version"], now))
            n += cur.rowcount
        for r in artifacts["coverage"]:
            detail = {k: v for k, v in r.items()
                      if k.startswith(("pct_", "n_")) or k == "scope"}
            cur.execute("""INSERT INTO analytics_coverage (
                analytics_run_id, scope, metric, total_observations,
                eligible_observations, ineligible_observations, coverage_pct,
                data_unavailable, data_untested, data_discrepant, detail,
                engine_version, policy_version, computed_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
              ON CONFLICT (analytics_run_id, scope, metric) DO NOTHING""",
                (run_id, r["scope"], r["metric"], r["total_observations"],
                 r["eligible_observations"], r["ineligible_observations"],
                 r["coverage_pct"], r["data_unavailable"], r["data_untested"],
                 r["data_discrepant"],
                 json.dumps(detail, ensure_ascii=False, sort_keys=True),
                 r["engine_version"], r["policy_version"], now))
            n += cur.rowcount
        for r in artifacts["association"]:
            cur.execute("""INSERT INTO analytics_association (
                analytics_run_id, x_metric, y_metric, n, total_n, coverage_ratio, rho,
                p_two_sided, method, significance_method, min_sample_required,
                sample_status, status, interpretation, causality_claim,
                is_content_pattern, limitations, engine_version, policy_version,
                computed_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
              ON CONFLICT (analytics_run_id, x_metric, y_metric) DO NOTHING""",
                (run_id, r["x_metric"], r["y_metric"], r["n"], r["total_n"],
                 r["coverage_ratio"], r["rho"], r["p_two_sided"], r["method"],
                 r["significance_method"], r["min_sample_required"], r["sample_status"],
                 r["status"], r["interpretation"], r["causality_claim"],
                 r["is_content_pattern"],
                 json.dumps(r["limitations"], ensure_ascii=False),
                 r["engine_version"], r["policy_version"], now))
            n += cur.rowcount
        conn.commit()
    return n


if __name__ == "__main__":
    arts, h, run_id = build()
    for k, v in sorted(arts.items()):
        print(f"  {k:<20}{len(v):>5} строк")
    print(f"\nanalytics_hash: {h}")
    print(f"analytics_run_id: {run_id}")
    if "--load" in sys.argv:
        print(f"записано в PostgreSQL: {load_to_db(arts, run_id)}")
