#!/usr/bin/env bash
# Создаёт БД и три роли с СГЕНЕРИРОВАННЫМИ паролями, пишет .env (в .gitignore).
# Пароли не печатаются в stdout и не попадают в историю команд.
set -euo pipefail
export PATH=/usr/lib/postgresql/16/bin:$PATH
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="${TIKTOK_DB:-tiktok_manager}"

gen() { python3 -c "import secrets;print(secrets.token_urlsafe(24))"; }
OWNER_PW="$(gen)"; RW_PW="$(gen)"; RO_PW="$(gen)"

su postgres -c "psql -v ON_ERROR_STOP=1 -tAc \"SELECT 1 FROM pg_database WHERE datname='${DB}'\"" \
  | grep -q 1 || su postgres -c "createdb ${DB}"

su postgres -c "psql -v ON_ERROR_STOP=1 -d ${DB} \
  -v owner_pw='${OWNER_PW}' -v rw_pw='${RW_PW}' -v ro_pw='${RO_PW}' \
  -f '${ROOT}/db/migrations/0001_roles.sql'" >/dev/null

su postgres -c "psql -v ON_ERROR_STOP=1 -d ${DB} -c 'ALTER DATABASE ${DB} OWNER TO tiktok_owner'" >/dev/null
su postgres -c "psql -v ON_ERROR_STOP=1 -d ${DB} -c 'ALTER SCHEMA public OWNER TO tiktok_owner'" >/dev/null

umask 077
cat > "${ROOT}/.env" <<ENVEOF
# СГЕНЕРИРОВАНО bootstrap_db.sh — файл в .gitignore, в репозиторий не попадает
TIKTOK_DB=${DB}
TIKTOK_DSN_OWNER=postgresql://tiktok_owner:${OWNER_PW}@127.0.0.1:5432/${DB}
TIKTOK_DSN_RW=postgresql://tiktok_rw:${RW_PW}@127.0.0.1:5432/${DB}
TIKTOK_DSN_RO=postgresql://tiktok_ro:${RO_PW}@127.0.0.1:5432/${DB}
ENVEOF
chmod 600 "${ROOT}/.env"
echo "БД ${DB} готова, роли созданы, .env записан с правами 600"
