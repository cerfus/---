#!/usr/bin/env python3
"""Проверка: схема базы соответствует набору миграций.

    python db/verify_schema.py

Работает одинаково на Windows и Linux: ни bash, ни psql, ни su не нужны —
только psycopg и DSN из .env. Ничего не изменяет, только читает.

Проверяется не «таблица есть», а «есть именно то, что создаёт миграция»:
0009 узнаётся по составу ключа video_features, 0016 — по вхождению в него
extractor_version. Поэтому проверка отличает частично применённую схему
от полной.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS = []

# Таблицы, которые обязаны существовать, и миграция-источник.
TABLES = {
    "accounts": "0002", "videos": "0002", "video_snapshots": "0002",
    "video_analysis": "0002", "content_patterns": "0002",
    "dna_versions": "0002", "content_dna": "0002", "experiments": "0002",
    "experiment_results": "0002", "ideas": "0002", "scripts": "0002",
    "insights": "0002", "reports": "0002", "publishing_queue": "0002",
    "publishing_history": "0002", "system_capabilities": "0002",
    "reconciliation_results": "0007",
    "analytics_video_baseline": "0008", "analytics_account_baseline": "0008",
    "analytics_coverage": "0008", "analytics_association": "0008",
    "video_features": "0009",
    "mechanical_metric_pairs": "0014",
    "video_assets": "0015", "semantic_feature_names": "0015",
    "semantic_annotations": "0015",
    "schema_migrations": "migrator",
}

VIEWS = {"insights_current": "0011", "reports_current": "0013",
         "video_assets_current": "0015", "video_features_current": "0016"}

TRIGGERS = {
    "trg_pq_guard": "0003", "trg_pq_supersedes": "0003",
    "trg_dna_snapshots": "0003", "trg_exp_concluded": "0003",
    "trg_pattern_causality": "0003",
    "trg_vf_append_only": "0009",
    "trg_pat_mechanical": "0014", "trg_dna_mechanical": "0014",
    "trg_exp_mechanical": "0014",
    "trg_va_append_only": "0015", "trg_vf_semantic": "0015",
    "trg_dna_semantic": "0015",
}

FUNCTIONS = {
    "pq_guard": "0003", "video_features_append_only": "0009",
    "is_mechanical_pair": "0014", "pat_mechanical_guard": "0014",
    "dna_mechanical_guard": "0014", "exp_mechanical_guard": "0014",
    "video_assets_append_only": "0015", "vf_semantic_guard": "0015",
    "dna_semantic_guard": "0015",
}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {name:<52}{detail}")


def one(cur, sql, params=None):
    """Одно значение либо None.

    None при ошибке, а НЕ текст ошибки: иначе сравнение с числом падает и
    проверка ломается там, где обязана показать честный FAIL. Соединение
    открыто в autocommit — неудачный запрос не отравляет следующие.
    """
    try:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def count(cur, table):
    """Число строк либо None, если таблицы нет."""
    v = one(cur, f"SELECT count(*) FROM {table}")
    return v if isinstance(v, int) else None


def main():
    import psycopg
    from core import config

    print("=== ПОДКЛЮЧЕНИЕ ===")
    try:
        # autocommit: каждая проверка независима, ошибка одной не срывает
        # остальные — верификатор обязан досчитать до конца и показать всё.
        conn = psycopg.connect(config.dsn("owner"), autocommit=True)
    except Exception as exc:
        print(f"  [FAIL] не удалось подключиться: {type(exc).__name__}")
        print(f"         {config.redact(exc)}")
        return 1
    with conn, conn.cursor() as cur:
        db = one(cur, "SELECT current_database()")
        usr = one(cur, "SELECT current_user")
        ver = one(cur, "SELECT version()")
        tz = one(cur, "SELECT current_setting('TimeZone')")
        check(f"подключение {usr}@{db}", True, str(ver).split(",")[0])
        check("часовой пояс базы = UTC", tz == "UTC", str(tz))

        print("\n=== МИГРАЦИИ ===")
        n_mig = count(cur, "schema_migrations")
        check("журнал schema_migrations существует", n_mig is not None,
              "таблицы нет" if n_mig is None else f"записей: {n_mig}")
        if n_mig is not None:
            files = sorted(p.name for p in
                           (ROOT / "db" / "migrations").glob("*.sql"))
            cur.execute("SELECT filename FROM schema_migrations")
            known = {r[0] for r in cur.fetchall()}
            missing = [f for f in files if f not in known]
            check("все файлы миграций записаны в журнал", not missing,
                  f"нет записи: {', '.join(missing)}" if missing
                  else f"{len(files)} из {len(files)}")
            check("усыновлённых (не выполнялись этим мигратором)", True,
                  str(count(cur, "schema_migrations WHERE adopted")))

        print("\n=== ТАБЛИЦЫ ===")
        for t, src in sorted(TABLES.items()):
            check(f"{t} ({src})",
                  one(cur, "SELECT to_regclass(%s) IS NOT NULL", (t,)) is True)

        print("\n=== ПРЕДСТАВЛЕНИЯ ===")
        for v, src in sorted(VIEWS.items()):
            check(f"{v} ({src})",
                  one(cur, "SELECT to_regclass(%s) IS NOT NULL", (v,)) is True)

        print("\n=== VIDEO_FEATURES: структура 0009 + 0015 + 0016 ===")
        cols = one(cur, "SELECT count(*) FROM information_schema.columns"
                        " WHERE table_name='video_features'")
        check("колонок 19", cols == 19, str(cols))
        key = one(cur, "SELECT string_agg(a.attname, ',' ORDER BY a.attnum)"
                       " FROM pg_constraint c"
                       " JOIN unnest(c.conkey) u(n) ON TRUE"
                       " JOIN pg_attribute a ON a.attrelid=c.conrelid"
                       "  AND a.attnum=u.n"
                       " WHERE c.conname='uq_video_feature_version'")
        check("ключ включает policy_version и extractor_version (0009+0016)",
              key == "video_id,feature_name,policy_version,extractor_version",
              str(key))
        tier = one(cur, "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                        " WHERE conname='video_features_tier_check'")
        check("tier допускает tier_1 (0015)", "tier_1" in str(tier))
        st = one(cur, "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                      " WHERE conname='video_features_feature_status_check'")
        check("статусы включают invalid_asset и insufficient_evidence (0015)",
              "invalid_asset" in str(st) and "insufficient_evidence" in str(st))

        print("\n=== ТРИГГЕРЫ ===")
        for t, src in sorted(TRIGGERS.items()):
            check(f"{t} ({src})",
                  one(cur, "SELECT EXISTS (SELECT 1 FROM pg_trigger"
                           " WHERE tgname=%s AND NOT tgisinternal)", (t,)))

        print("\n=== ФУНКЦИИ ===")
        for f, src in sorted(FUNCTIONS.items()):
            check(f"{f} ({src})",
                  one(cur, "SELECT EXISTS (SELECT 1 FROM pg_proc p"
                           " JOIN pg_namespace n ON n.oid=p.pronamespace"
                           " WHERE n.nspname='public' AND p.proname=%s)", (f,)))

        print("\n=== ДАННЫЕ 0014 / 0015 ===")
        n_mech = count(cur, "mechanical_metric_pairs")
        check("mechanical_metric_pairs заполнена",
              n_mech is not None and n_mech >= 1,
              "таблицы нет" if n_mech is None else str(n_mech))
        n_sem = count(cur, "semantic_feature_names")
        check("semantic_feature_names заполнена",
              n_sem is not None and n_sem >= 9,
              "таблицы нет" if n_sem is None else str(n_sem))

        print("\n=== ПРАВА РОЛЕЙ ===")
        rw_del = one(cur, "SELECT count(*) FROM information_schema."
                          "role_table_grants WHERE grantee='tiktok_rw'"
                          " AND privilege_type='DELETE'")
        check("tiktok_rw НЕ имеет DELETE ни на одну таблицу", rw_del == 0,
              str(rw_del))
        ro_w = one(cur, "SELECT count(*) FROM information_schema."
                        "role_table_grants WHERE grantee='tiktok_ro'"
                        " AND privilege_type IN ('INSERT','UPDATE','DELETE')")
        check("tiktok_ro НЕ имеет прав на запись", ro_w == 0, str(ro_w))
        rw_ins = one(cur, "SELECT count(*) FROM information_schema."
                          "role_table_grants WHERE grantee='tiktok_rw'"
                          " AND privilege_type='INSERT'")
        check("tiktok_rw имеет INSERT", (rw_ins or 0) > 0, str(rw_ins))

        print("\n=== ПУБЛИКАЦИЯ ===")
        pub = one(cur, "SELECT enabled FROM system_capabilities"
                       " WHERE capability='publishing.submit'")
        check("publishing.submit = FALSE", pub is False, str(pub))

    print("\n=== ПОДКЛЮЧЕНИЕ ПРИЛОЖЕНИЯ (роли rw и ro) ===")
    for role in ("rw", "ro"):
        try:
            with psycopg.connect(config.dsn(role)) as c, c.cursor() as cur:
                u = one(cur, "SELECT current_user")
                n = count(cur, "videos")
                check(f"роль {role} подключается и читает", u is not None,
                      f"{u}, videos: {n}")
        except Exception as exc:
            check(f"роль {role} подключается и читает", False,
                  type(exc).__name__)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
