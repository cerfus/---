#!/usr/bin/env bash
# Полная проверка: пересборка с нуля, сверка и все тесты.
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/ensure_pg.sh >/dev/null

echo "=== 1. нормализация raw -> JSONL (детерминизм) ==="
python3 normalize/normalize.py | sed -n '1,2p'
H1=$(cat data/videos.jsonl data/snapshots/*.jsonl | sha256sum | cut -c1-16)
python3 normalize/normalize.py >/dev/null
H2=$(cat data/videos.jsonl data/snapshots/*.jsonl | sha256sum | cut -c1-16)
[ "$H1" = "$H2" ] && echo "  JSONL детерминирован ($H2)" || { echo "  FAIL"; exit 1; }

echo; echo "=== 2. наблюдения из сырья ==="
python3 normalize/observations.py | sed -n '1,2p'

echo; echo "=== 3. пересборка БД с нуля исключительно из JSONL ==="
bash scripts/rebuild_from_jsonl.sh | sed 's/^/  /'
S1=$(python3 db/state_hash.py | tail -1 | awk '{print $NF}')
bash scripts/rebuild_from_jsonl.sh >/dev/null
S2=$(python3 db/state_hash.py | tail -1 | awk '{print $NF}')
[ "$S1" = "$S2" ] && echo "  REBUILD INVARIANT: PASS ${S1:0:24}…" || { echo "  FAIL"; exit 1; }

echo; echo "=== 4. сверка источников ==="
R1=$(python3 reconcile/run.py --load | grep content_hash | awk '{print $2}')
R2=$(python3 reconcile/run.py | grep content_hash | awk '{print $2}')
[ "$R1" = "$R2" ] && echo "  RECONCILIATION DETERMINISM: PASS ${R1:0:24}…" || { echo "  FAIL"; exit 1; }

echo; echo "=== 5. аналитика ==="
A1=$(python3 analytics/run.py --load | grep analytics_hash | awk '{print $2}')
A2=$(python3 analytics/run.py | grep analytics_hash | awk '{print $2}')
[ "$A1" = "$A2" ] && echo "  ANALYTICS DETERMINISM: PASS ${A1:0:24}…" || { echo "  FAIL"; exit 1; }

echo; echo "=== 6. тесты Phase 1 ==="; python3 tests/test_phase1.py | tail -2
echo; echo "=== 7. тесты Phase 2 (движок сверки) ==="; python3 tests/test_reconcile_engine.py | tail -2
echo; echo "=== 8. тесты Phase 3 (аналитика) ==="; python3 tests/test_analytics.py | tail -2

echo; echo "=== 9. регрессия EXP-004 ==="; python3 tests/test_exp004_regression.py | tail -2
echo; echo "=== 10. правило both_lagged (LAG-1..LAG-7) ==="; python3 tests/test_reconciliation.py | tail -2
echo; echo "=== 11. воспроизводимость EXP-004 ==="; python3 experiments/EXP-004/exp004.py verify | tail -6

echo; echo "=== 12. способность публикации ==="
python3 - <<'PY'
import sys; sys.path.insert(0,'.')
import psycopg; from core import config
with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
    cur.execute("SELECT enabled FROM system_capabilities WHERE capability='publishing.submit'")
    v = cur.fetchone()[0]
print(f"  publishing.submit = {v}" + ("  OK" if v is False else "  FAIL"))
raise SystemExit(0 if v is False else 1)
PY
