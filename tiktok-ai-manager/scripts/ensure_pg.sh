#!/usr/bin/env bash
# Кластер PostgreSQL в эфемерном контейнере не стартует сам. Поднимаем, если лежит.
export PATH=/usr/lib/postgresql/16/bin:$PATH
if ! pg_isready -q 2>/dev/null; then
  pg_ctlcluster 16 main start 2>/dev/null || true
  for _ in $(seq 1 15); do pg_isready -q 2>/dev/null && break; sleep 1; done
fi
pg_isready
