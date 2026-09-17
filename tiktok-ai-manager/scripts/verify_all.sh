#!/usr/bin/env bash
# Полная проверка Phase 1: пересборка с нуля и все тесты.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== 1. нормализация raw -> JSONL (детерминизм) ==="
H1=$(cat data/videos.jsonl data/snapshots/*.jsonl 2>/dev/null | sha256sum | cut -c1-16 || echo none)
python3 normalize/normalize.py
H2=$(cat data/videos.jsonl data/snapshots/*.jsonl | sha256sum | cut -c1-16)
[ "$H1" = "none" ] || { [ "$H1" = "$H2" ] && echo "  JSONL идентичен предыдущему прогону ($H2)"; }

echo; echo "=== 2. пересборка БД с нуля исключительно из JSONL ==="
bash scripts/rebuild_from_jsonl.sh | sed 's/^/  /'
S1=$(python3 db/state_hash.py | tail -1 | awk '{print $NF}')
bash scripts/rebuild_from_jsonl.sh >/dev/null
S2=$(python3 db/state_hash.py | tail -1 | awk '{print $NF}')
if [ "$S1" = "$S2" ]; then echo "  REBUILD INVARIANT: PASS  ${S1:0:32}…"; else echo "  REBUILD INVARIANT: FAIL"; exit 1; fi

echo; echo "=== 3. тесты Phase 1 (M, N, P, Q, права, секреты) ==="
python3 tests/test_phase1.py | tail -3

echo; echo "=== 4. тесты правила сверки (LAG-1..LAG-7) ==="
python3 tests/test_reconciliation.py | tail -2

echo; echo "=== 5. проверки воспроизводимости EXP-004 ==="
python3 experiments/EXP-004/exp004.py verify
