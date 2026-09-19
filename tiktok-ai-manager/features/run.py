#!/usr/bin/env python3
"""Прогон слоя признаков -> JSONL -> PostgreSQL.

Хранилище приведено миграцией 0009 к длинному append-only формату с
идентичностью (video_id, feature_name, policy_version). Новая версия
политики создаёт новые строки и никогда не затирает прежние;
computed_at и run_id в идентичность не входят.
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

DB_PERSISTENCE = {
    "table": "video_features",
    "identity": ["video_id", "feature_name", "policy_version"],
    "append_only": True,
    "migration": "0009_video_features_versioned.sql",
}

# Колонки, которые ложатся в таблицу напрямую. Всё остальное из строки
# признака уходит в extra, чтобы ничего не потерялось при записи.
STANDARD_KEYS = {"video_id", "feature_name", "feature_value", "feature_type",
                 "tier", "feature_status", "status_reason", "policy_version",
                 "engine_version", "source_basis", "reconciliation_basis",
                 "evidence_refs", "computed_from"}


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
            "db_persistence": DB_PERSISTENCE,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rows, h, run_id


def encode_value(v):
    """Каноническое кодирование значения.

    repr() у float воспроизводит число точно при обратном чтении, поэтому
    текстовое представление и NUMERIC не расходятся между собой.
    bool проверяется раньше int: в Python bool — подкласс int.
    """
    from decimal import Decimal
    if v is None:
        return None, None, None
    if isinstance(v, bool):
        return ("true" if v else "false"), None, v
    if isinstance(v, (int, float)):
        return repr(v), Decimal(repr(v)), None
    return str(v), None, None


INSERT_FEATURE = """INSERT INTO video_features (
  video_id, feature_name, feature_value, feature_value_numeric, feature_value_bool,
  feature_type, feature_status, status_reason, tier, policy_version,
  extractor_version, source_basis, reconciliation_basis, evidence_refs,
  computed_from, extra, computed_at, run_id)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s,%s)
ON CONFLICT (video_id, feature_name, policy_version, extractor_version)
  DO NOTHING"""


def load_to_db(rows, run_id, computed_at=None):
    import psycopg
    from datetime import datetime, timezone
    from core import config
    computed_at = computed_at or datetime.now(timezone.utc)
    n = 0
    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        for r in rows:
            txt, num, boolean = encode_value(r["feature_value"])
            extra = {k: v for k, v in sorted(r.items()) if k not in STANDARD_KEYS}
            cur.execute(INSERT_FEATURE, (
                r["video_id"], r["feature_name"], txt, num, boolean,
                r["feature_type"], r["feature_status"], r["status_reason"],
                r["tier"], r["policy_version"], r["engine_version"],
                json.dumps(r["source_basis"], ensure_ascii=False, sort_keys=True),
                json.dumps(r["reconciliation_basis"], ensure_ascii=False, sort_keys=True),
                r["evidence_refs"], r["computed_from"],
                json.dumps(extra, ensure_ascii=False, sort_keys=True) if extra else None,
                computed_at, run_id))
            n += cur.rowcount
        conn.commit()
    return n


def db_matches_jsonl(policy_version=None):
    """Сверка содержимого PostgreSQL с JSONL. Возвращает список расхождений."""
    import psycopg
    from core import config
    pv = policy_version or F.FEATURE_POLICY_VERSION
    rows, _, _ = build(write=False)
    expect = {(r["video_id"], r["feature_name"]): r for r in rows
              if r["policy_version"] == pv}
    problems = []
    with psycopg.connect(config.dsn("ro")) as conn, conn.cursor() as cur:
        cur.execute("""SELECT video_id, feature_name, feature_value,
                              feature_value_numeric, feature_value_bool, feature_type,
                              feature_status, status_reason, tier, evidence_refs,
                              computed_from
                         FROM video_features WHERE policy_version = %s
                        ORDER BY video_id, feature_name""", (pv,))
        got = cur.fetchall()
    if len(got) != len(expect):
        problems.append(f"строк в БД {len(got)}, в JSONL {len(expect)}")
    for (vid, name, txt, num, boolean, ftype, status, reason, tier,
         ev, cf) in got:
        e = expect.get((vid, name))
        if e is None:
            problems.append(f"{vid}/{name}: нет в JSONL")
            continue
        exp_txt, exp_num, exp_bool = encode_value(e["feature_value"])
        if txt != exp_txt:
            problems.append(f"{vid}/{name}: значение {txt!r} != {exp_txt!r}")
        if (num is None) != (exp_num is None) or (
                num is not None and float(num) != float(exp_num)):
            problems.append(f"{vid}/{name}: numeric {num} != {exp_num}")
        if boolean != exp_bool:
            problems.append(f"{vid}/{name}: bool {boolean} != {exp_bool}")
        for got_v, exp_v, label in ((ftype, e["feature_type"], "type"),
                                    (status, e["feature_status"], "status"),
                                    (reason, e["status_reason"], "reason"),
                                    (tier, e["tier"], "tier"),
                                    (list(ev), e["evidence_refs"], "evidence_refs"),
                                    (list(cf), e["computed_from"], "computed_from")):
            if got_v != exp_v:
                problems.append(f"{vid}/{name}: {label} {got_v!r} != {exp_v!r}")
    return problems


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
    if "--load" in sys.argv:
        print(f"записано в PostgreSQL: {load_to_db(rows, run_id)}")
