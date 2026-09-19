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

echo; echo "=== 6. слой признаков (JSONL + PostgreSQL) ==="
F1=$(python3 features/run.py --load | grep feature_hash: | awk '{print $2}')
F2=$(python3 features/run.py | grep feature_hash: | awk '{print $2}')
[ "$F1" = "$F2" ] && echo "  FEATURE DETERMINISM: PASS ${F1:0:24}…" || { echo "  FAIL"; exit 1; }

echo; echo "=== 6в. инструменты промера видео ==="
bash scripts/ensure_extractors.sh

echo; echo "=== 6г. локальный ингест видео (одна команда) ==="
python3 -m assets.ingest --load | tail -12

echo; echo "=== 6б. выводы и ежедневный отчёт ==="
I1=$(python3 insights/run.py --load | grep insights_hash: | awk '{print $2}')
I2=$(python3 insights/run.py | grep insights_hash: | awk '{print $2}')
[ "$I1" = "$I2" ] && echo "  INSIGHTS DETERMINISM: PASS ${I1:0:24}…" || { echo "  FAIL"; exit 1; }

echo; echo "=== 7. тесты Phase 1 ==="; python3 tests/test_phase1.py | tail -2
echo; echo "=== 8. тесты Phase 2 (движок сверки) ==="; python3 tests/test_reconcile_engine.py | tail -2
echo; echo "=== 9. тесты Phase 3 (аналитика) ==="; python3 tests/test_analytics.py | tail -2

echo; echo "=== 11. тесты Phase 4 (признаки) ==="; python3 tests/test_features.py | tail -2

echo; echo "=== 11б. тесты хранилища признаков ==="; python3 tests/test_feature_storage.py | tail -2

echo; echo "=== 11в. тесты Phase 5 (выводы и отчёт) ==="; python3 tests/test_insights.py | tail -2

echo; echo "=== 11г. тесты Phase 5.1 (механическая зависимость) ==="; python3 tests/test_mechanical_dependency.py | tail -2

echo; echo "=== 11д. регрессия Phase 5.1 hardening (A-G + порядок) ==="; python3 tests/test_phase51_hardening.py | tail -2

echo; echo "=== 11е. тесты Phase 6 (ассеты и Tier 1) ==="; python3 tests/test_phase6_assets.py | tail -2

echo; echo "=== 11ж. тесты Phase 6.5 (мобильный пульт) ==="; python3 tests/test_phase65_mobile.py | tail -2

echo; echo "=== 11з. стартовая проверка Telegram (без запуска опроса) ==="
python3 -m mobile.telegram --check | sed 's/^/  /'

echo; echo "=== 10. регрессия EXP-004 ==="; python3 tests/test_exp004_regression.py | tail -2
echo; echo "=== 12. правило both_lagged (LAG-1..LAG-7) ==="; python3 tests/test_reconciliation.py | tail -2
echo; echo "=== 13. воспроизводимость EXP-004 ==="; python3 experiments/EXP-004/exp004.py verify | tail -6

echo; echo "=== 14. способность публикации ==="
python3 - <<'PY'
import sys; sys.path.insert(0,'.')
import psycopg; from core import config
with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
    cur.execute("SELECT enabled FROM system_capabilities WHERE capability='publishing.submit'")
    v = cur.fetchone()[0]
print(f"  publishing.submit = {v}" + ("  OK" if v is False else "  FAIL"))
raise SystemExit(0 if v is False else 1)
PY
