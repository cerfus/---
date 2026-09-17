#!/usr/bin/env bash
# Пересборка БД с нуля ИСКЛЮЧИТЕЛЬНО из JSONL.
# Роли и .env не трогаются: пересоздаётся только база.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${TIKTOK_DB:-tiktok_manager}"

su postgres -c "psql -v ON_ERROR_STOP=1 -tAc \"
  SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE datname='${DB}' AND pid <> pg_backend_pid()\"" >/dev/null
su postgres -c "dropdb --if-exists ${DB}"
su postgres -c "createdb ${DB}"
su postgres -c "psql -v ON_ERROR_STOP=1 -d ${DB} -c 'ALTER DATABASE ${DB} OWNER TO tiktok_owner'" >/dev/null
su postgres -c "psql -v ON_ERROR_STOP=1 -d ${DB} -c 'ALTER SCHEMA public OWNER TO tiktok_owner'" >/dev/null
su postgres -c "psql -v ON_ERROR_STOP=1 -d ${DB} -c 'REVOKE CREATE ON SCHEMA public FROM PUBLIC'" >/dev/null

cd "${ROOT}"
python3 db/migrate.py
python3 db/load.py
