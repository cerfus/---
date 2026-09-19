#!/usr/bin/env python3
"""Применение миграций. Владелец схемы — tiktok_owner, не рантайм-роль.

0001_roles.sql выполняется суперпользователем из bootstrap_db.sh и помечается
здесь как применённая извне: рантайму и владельцу не нужны права на роли.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import psycopg
from core import config

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
EXTERNAL = {"0001_roles.sql"}      # применяется bootstrap-скриптом

DDL_TRACKER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename    TEXT PRIMARY KEY,
  sha256      TEXT NOT NULL,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by  TEXT NOT NULL DEFAULT current_user,
  external    BOOLEAN NOT NULL DEFAULT FALSE
);
"""


def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def migrate(verbose=True):
    applied, skipped, drift = [], [], []
    with psycopg.connect(config.dsn("owner"), autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL_TRACKER)
            conn.commit()
            cur.execute("SELECT filename, sha256 FROM schema_migrations")
            known = dict(cur.fetchall())

            for path in sorted(MIGRATIONS.glob("*.sql")):
                digest = sha256(path)
                if path.name in known:
                    if known[path.name] != digest:
                        drift.append(path.name)
                    skipped.append(path.name)
                    continue
                if path.name in EXTERNAL:
                    cur.execute(
                        "INSERT INTO schema_migrations (filename, sha256, external)"
                        " VALUES (%s, %s, TRUE)", (path.name, digest))
                    conn.commit()
                    skipped.append(path.name + " (внешняя)")
                    continue
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute(
                    "INSERT INTO schema_migrations (filename, sha256) VALUES (%s, %s)",
                    (path.name, digest))
                conn.commit()
                applied.append(path.name)

    if verbose:
        for n in applied:
            print(f"  применена  {n}")
        for n in skipped:
            print(f"  пропущена  {n}")
        for n in drift:
            print(f"  ДРЕЙФ      {n}: файл изменён после применения")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(migrate())
