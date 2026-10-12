#!/usr/bin/env python3
"""Детерминированный слепок состояния БД.

Суррогатные BIGSERIAL-идентификаторы и моменты применения миграций при
пересборке закономерно отличаются и в слепок НЕ входят. Сравнивается то,
что действительно должно воспроизводиться: содержимое по естественным ключам.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg
from core import config

# таблица -> (естественный порядок, исключаемые колонки)
SPEC = {
    "accounts": ("platform, handle", {"account_id", "created_at"}),
    "videos": ("video_id", {"account_id"}),
    "video_snapshots": ("video_id, source, observed_at", {"snapshot_id"}),
    # идентичность признака — (video_id, feature_name, policy_version);
    # computed_at и run_id в неё не входят и исключены из слепка
    "video_features": ("video_id, feature_name, policy_version",
                       {"id", "computed_at", "run_id"}),
    "video_analysis": ("video_id, field_name, rubric_version, analyst", {"analysis_id"}),
    "dna_versions": ("account_id, version", {"dna_version_id", "account_id"}),
    "content_patterns": ("account_id, dimension, pattern_key, metric, computed_at",
                         {"pattern_id", "account_id", "experiment_id"}),
    "content_dna": ("account_id, version, section, statement",
                    {"dna_id", "account_id", "dna_version_id", "pattern_id"}),
    "experiments": ("code", {"experiment_id", "account_id", "dna_version_id", "dna_id"}),
    "experiment_results": ("experiment_id, group_name, metric, computed_at",
                           {"result_id", "experiment_id"}),
    "ideas": ("created_at, title", {"idea_id", "account_id", "pattern_id",
                                    "dna_id", "experiment_id"}),
    "scripts": ("idea_id, version", {"script_id", "idea_id"}),
    # идентичность вывода — (run_id, content_hash); created_at варьируется
    "insights": ("run_id, content_hash",
                 {"insight_id", "account_id", "pattern_id", "experiment_id",
                  "superseded_by", "created_at"}),
    "reports": ("report_type, period_start, period_end, run_id",
                {"report_id", "account_id", "generated_at"}),
    "system_capabilities": ("capability", set()),
    "publishing_queue": ("correlation_id", {"queue_id", "account_id", "script_id",
                                            "experiment_id", "supersedes_queue_id"}),
    "publishing_history": ("correlation_id, occurred_at", {"history_id",
                                                           "publishing_queue_id"}),
    # run_id этих таблиц ДЕТЕРМИНИРОВАН (uuid5 от хеша содержимого), поэтому
    # он остаётся в слепке и сам служит доказательством воспроизводимости.
    # computed_at — момент записи, он закономерно различается и исключён.
    "reconciliation_results": ("video_id, metric, slice_key",
                               {"result_id", "computed_at"}),
    "analytics_video_baseline": ("video_id", {"id", "computed_at"}),
    "analytics_account_baseline": ("window_name, metric", {"id", "computed_at"}),
    "analytics_coverage": ("scope, metric NULLS FIRST", {"id", "computed_at"}),
    "analytics_association": ("x_metric, y_metric", {"id", "computed_at"}),
    # Phase 6. Идентичность ассета — (video_id, asset_version); asset_uid
    # детерминирован от sha256 и потому остаётся в слепке как доказательство.
    # registered_at и acquired_at — моменты записи и получения файла, они
    # закономерно различаются между машинами и исключены.
    "video_assets": ("video_id, asset_version",
                     {"asset_id", "registered_at", "acquired_at", "run_id"}),
    "semantic_feature_names": ("feature_name", set()),
    "semantic_annotations": ("video_id, asset_sha256, feature_name, annotator",
                             {"annotation_id", "created_at", "run_id"}),
}


def table_digest(cur, table, order_by, exclude):
    cur.execute("""SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""",
                (table,))
    cols = [c for (c,) in cur.fetchall() if c not in exclude]
    cur.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order_by}")
    rows = cur.fetchall()
    payload = json.dumps([[str(v) for v in row] for row in rows],
                         ensure_ascii=False, sort_keys=True)
    return len(rows), hashlib.sha256(payload.encode("utf-8")).hexdigest()


def state(role="ro"):
    out = {}
    with psycopg.connect(config.dsn(role)) as conn, conn.cursor() as cur:
        for table, (order_by, exclude) in sorted(SPEC.items()):
            n, h = table_digest(cur, table, order_by, exclude)
            out[table] = {"rows": n, "sha256": h}
    combined = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return out, combined


if __name__ == "__main__":
    st, combined = state()
    for t, v in st.items():
        print(f"  {t:<22}{v['rows']:>5}  {v['sha256'][:16]}")
    print(f"\n  СЛЕПОК СОСТОЯНИЯ: {combined}")
