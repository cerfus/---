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

# ── распознавание уже применённых миграций ──────────────────────────────────
# База может нести объекты миграций, но не иметь журнала: так бывает, когда
# .sql применяли руками через psql, а не этим мигратором. Тогда повторный
# прогон падал на «отношение уже существует», и довести схему до конца было
# нечем.
#
# Каждый запрос ниже отвечает на один вопрос: «объекты ЭТОЙ миграции уже в
# базе?». Признак выбран так, чтобы отличать именно её вклад — например,
# 0009 узнаётся по колонке feature_name, которой нет в исходной
# video_features из 0002. Ошибка запроса (нет таблицы) значит «не применена».
DETECTORS = {
    "0001_roles.sql":
        "SELECT count(*) = 3 FROM pg_roles"
        " WHERE rolname IN ('tiktok_owner','tiktok_rw','tiktok_ro')",
    "0002_schema.sql":
        "SELECT to_regclass('accounts') IS NOT NULL",
    "0003_triggers.sql":
        "SELECT EXISTS (SELECT 1 FROM pg_trigger"
        " WHERE tgname = 'trg_pq_guard' AND NOT tgisinternal)",
    "0004_grants.sql":
        "SELECT EXISTS (SELECT 1 FROM information_schema.role_table_grants"
        " WHERE grantee='tiktok_rw' AND table_name='videos'"
        " AND privilege_type='INSERT')",
    "0005_seed.sql":
        "SELECT EXISTS (SELECT 1 FROM system_capabilities"
        " WHERE capability='publishing.submit')",
    "0006_snapshot_precision.sql":
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
        " WHERE table_name='video_snapshots'"
        " AND column_name='observed_at_precision')",
    "0007_reconciliation_results.sql":
        "SELECT to_regclass('reconciliation_results') IS NOT NULL",
    "0008_analytics.sql":
        "SELECT to_regclass('analytics_association') IS NOT NULL",
    # 0009 заменяет структуру video_features целиком: feature_name есть
    # только в новой версии, в исходной из 0002 его нет.
    "0009_video_features_versioned.sql":
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
        " WHERE table_name='video_features' AND column_name='feature_name')",
    "0010_insights_idempotency.sql":
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns"
        " WHERE table_name='insights' AND column_name='content_hash')",
    "0011_insights_current_view.sql":
        "SELECT to_regclass('insights_current') IS NOT NULL",
    "0012_insights_run_identity.sql":
        "SELECT EXISTS (SELECT 1 FROM pg_constraint"
        " WHERE conname='uq_insight_run_content')",
    "0013_reports_run_identity.sql":
        "SELECT to_regclass('reports_current') IS NOT NULL",
    "0014_mechanical_guard.sql":
        "SELECT to_regclass('mechanical_metric_pairs') IS NOT NULL",
    "0015_video_assets.sql":
        "SELECT to_regclass('video_assets') IS NOT NULL",
    "0016_feature_extractor_identity.sql":
        "SELECT to_regclass('video_features_current') IS NOT NULL",
}


def already_applied(cur, name):
    """Объекты миграции уже в базе? None — признак не описан."""
    sql = DETECTORS.get(name)
    if sql is None:
        return None
    cur.execute("SAVEPOINT detect")
    try:
        cur.execute(sql)
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT detect")
        return bool(row and row[0])
    except psycopg.Error:
        # Нет таблицы или колонки — значит миграция не применялась.
        cur.execute("ROLLBACK TO SAVEPOINT detect")
        return False

DDL_TRACKER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename    TEXT PRIMARY KEY,
  sha256      TEXT NOT NULL,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  applied_by  TEXT NOT NULL DEFAULT current_user,
  external    BOOLEAN NOT NULL DEFAULT FALSE,
  -- TRUE: миграция не выполнялась этим мигратором, а признана применённой
  -- по своим объектам в базе. Отличать важно: у такой записи нет гарантии,
  -- что применялась именно эта версия файла.
  adopted     BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE schema_migrations
  ADD COLUMN IF NOT EXISTS adopted BOOLEAN NOT NULL DEFAULT FALSE;
GRANT SELECT ON schema_migrations TO tiktok_rw, tiktok_ro;
"""


def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def migrate(verbose=True, adopt=False):
    """adopt=True: миграцию, объекты которой уже в базе, не выполнять, а
    записать в журнал как применённую. Так существующая база доводится до
    актуального состояния, не ломаясь на «уже существует»."""
    applied, skipped, drift, adopted, blocked = [], [], [], [], []
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

                present = already_applied(cur, path.name)
                if present and adopt:
                    # Объекты на месте: выполнять повторно нельзя — упадёт
                    # или, хуже, пересоздаст таблицу. Фиксируем как применённую.
                    cur.execute(
                        "INSERT INTO schema_migrations (filename, sha256, adopted)"
                        " VALUES (%s, %s, TRUE)", (path.name, digest))
                    conn.commit()
                    adopted.append(path.name)
                    continue
                if present and not adopt:
                    # Без --adopt не гадаем и не ломаем: называем причину.
                    blocked.append(path.name)
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
        for n in adopted:
            print(f"  усыновлена {n}  (объекты уже были в базе)")
        for n in skipped:
            print(f"  пропущена  {n}")
        for n in drift:
            print(f"  ДРЕЙФ      {n}: файл изменён после применения")
        for n in blocked:
            print(f"  НЕ ПРИМЕНЕНА {n}: её объекты уже есть в базе, "
                  f"но записи в журнале нет")
        if blocked:
            print("\n  База несёт объекты этих миграций, но журнал их не "
                  "содержит.\n  Так бывает, когда .sql применяли вручную. "
                  "Повторный запуск\n  сломал бы схему, поэтому он не "
                  "выполнен.\n\n  Чтобы зафиксировать их как применённые и "
                  "продолжить:\n      python db/migrate.py --adopt")
    return 1 if (drift or blocked) else 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        print("  python db/migrate.py            применить недостающие миграции")
        print("  python db/migrate.py --adopt    + признать применёнными те,")
        print("                                  чьи объекты уже есть в базе")
        return 0
    return migrate(adopt="--adopt" in argv)


if __name__ == "__main__":
    sys.exit(main())
