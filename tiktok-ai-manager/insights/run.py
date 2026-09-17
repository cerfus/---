#!/usr/bin/env python3
"""Прогон Phase 5: выводы + ежедневный отчёт -> JSONL -> PostgreSQL."""
import glob
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from insights import generators
from insights import policies as P
from reports import daily

OUT = ROOT / "data" / "insights"
REPORT_DIR = ROOT / "reports" / "daily"
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000005")
PERIOD = "2026-09-17"          # дата наблюдений EXP-004


def _jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def load_inputs():
    a = ROOT / "data" / "analytics"
    return {
        "videos": _jsonl(ROOT / "data" / "videos.jsonl"),
        "coverage": _jsonl(a / "coverage.jsonl"),
        "account": _jsonl(a / "baseline_account.jsonl"),
        "association": _jsonl(a / "association.jsonl"),
        "recon": _jsonl(ROOT / "data" / "reconciliation" / "results.jsonl"),
        "features": [r for f in sorted(glob.glob(str(ROOT / "data" / "features" /
                                                    "features_*.jsonl")))
                     for r in _jsonl(f)],
    }


def input_hashes():
    out = {}
    for name, path in (("analytics", ROOT / "data" / "analytics" / "manifest.json"),
                       ("features", ROOT / "data" / "features" / "manifest.json"),
                       ("reconciliation", ROOT / "data" / "reconciliation" / "manifest.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        key = next(k for k in m if k.endswith("_hash"))
        out[name] = m[key]
    return out


def build(write=True):
    d = load_inputs()
    ins, blocked = generators.generate_all(
        d["coverage"], d["recon"], d["account"], d["association"],
        d["features"], n_videos=len(d["videos"]))
    hashes = input_hashes()
    payload = json.dumps({"insights": ins, "blocked": blocked},
                         ensure_ascii=False, sort_keys=True)
    content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    run_id = str(uuid.uuid5(RUN_NS, content_hash))

    report_path = REPORT_DIR / f"{PERIOD}.md"
    previous = daily.read_previous(_previous_report(report_path))
    md = daily.build(PERIOD, ins, blocked, d["account"], d["coverage"],
                     experiments=[], hashes=hashes, previous=previous,
                     n_videos=len(d["videos"]))

    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for name, rows in (("insights", ins), ("blocked_conclusions", blocked)):
            with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
        report_path.write_text(md, encoding="utf-8")
        (OUT / "manifest.json").write_text(json.dumps({
            "insights_hash": content_hash, "insights_run_id": run_id,
            "period": PERIOD, "n_insights": len(ins), "n_blocked": len(blocked),
            "input_hashes": hashes,
            "policy_version": P.INSIGHTS_POLICY_VERSION,
            "report_path": str(report_path.relative_to(ROOT)),
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return ins, blocked, md, content_hash, run_id, hashes


def _previous_report(current):
    prior = sorted(p for p in REPORT_DIR.glob("*.md") if p.name < current.name)
    return prior[-1] if prior else None


def data_completeness(coverage_rows):
    o = next(r for r in coverage_rows if r["scope"] == "all_metrics")
    return {"total_observations": o["total_observations"],
            "eligible_observations": o["eligible_observations"],
            "coverage_pct": o["coverage_pct"],
            "data_unavailable": o["data_unavailable"],
            "data_untested": o["data_untested"],
            "data_discrepant": o["data_discrepant"]}


def load_to_db(ins, blocked, run_id, md_path, coverage_rows, n_videos):
    import psycopg
    from core import config
    now = datetime.now(timezone.utc)
    n_ins = n_rep = 0
    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT account_id FROM accounts ORDER BY account_id LIMIT 1")
        account_id = cur.fetchone()[0]
        for i in ins:
            cur.execute("""INSERT INTO insights (
                  account_id, statement, claim_type, n_sample, min_sample_required,
                  source_kind, competing_explanation, status, created_at, run_id,
                  content_hash, based_on)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (account_id, run_id, content_hash) DO NOTHING""",
                (account_id, i["statement"], i["claim_type"], i["n_sample"],
                 i["min_sample_required"], i["source_kind"],
                 i["competing_explanation"], i["status"], now, run_id,
                 i["content_hash"],
                 json.dumps(i["based_on"], ensure_ascii=False)))
            n_ins += cur.rowcount
        cur.execute("""INSERT INTO reports (
              account_id, report_type, period_start, period_end, generated_at, path,
              summary, n_videos_in_period, n_new_snapshots, data_completeness,
              blocked_conclusions, run_id)
            VALUES (%s,'daily',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
            ON CONFLICT (account_id, report_type, period_start, period_end, run_id) DO NOTHING""",
            (account_id, PERIOD, PERIOD, now, md_path,
             f"FACT: 0 · HYPOTHESIS: {sum(1 for x in ins if x['claim_type']=='HYPOTHESIS')}"
             f" · заблокировано выводов: {len(blocked)}",
             n_videos, 0,
             json.dumps(data_completeness(coverage_rows), ensure_ascii=False,
                        sort_keys=True),
             json.dumps(blocked, ensure_ascii=False, sort_keys=True), run_id))
        n_rep += cur.rowcount
        conn.commit()
    return n_ins, n_rep


if __name__ == "__main__":
    import collections
    ins, blocked, md, h, run_id, hashes = build()
    c = collections.Counter(i["claim_type"] for i in ins)
    print(f"выводов: {len(ins)} | заблокировано: {len(blocked)}")
    for k, v in sorted(c.items()):
        print(f"  {k:<18}{v}")
    print(f"\ninsights_hash: {h}")
    print(f"insights_run_id: {run_id}")
    print(f"отчёт: reports/daily/{PERIOD}.md ({len(md.splitlines())} строк)")
    if "--load" in sys.argv:
        d = load_inputs()
        a, b = load_to_db(ins, blocked, run_id, f"reports/daily/{PERIOD}.md",
                          d["coverage"], len(d["videos"]))
        print(f"записано: insights +{a}, reports +{b}")
