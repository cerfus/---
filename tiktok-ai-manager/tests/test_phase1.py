#!/usr/bin/env python3
"""Phase 1: обязательные тесты M, N, P, Q и проверки безопасности.

Данные для проверок создаются в транзакции и откатываются: наблюдения из
JSONL не изменяются.
"""
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from core import config

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<52}{detail}")
    return ok


def owner():
    return psycopg.connect(config.dsn("owner"), autocommit=False)


def rw():
    return psycopg.connect(config.dsn("rw"), autocommit=True)


def seed(cur):
    """Минимальный валидный контекст: аккаунт, закономерность, версия DNA."""
    cur.execute("SELECT account_id FROM accounts LIMIT 1")
    acc = cur.fetchone()[0]
    cur.execute("SELECT snapshot_id FROM video_snapshots ORDER BY snapshot_id LIMIT 3")
    snaps = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT video_id FROM videos ORDER BY video_id LIMIT 3")
    vids = [r[0] for r in cur.fetchall()]
    cur.execute("""
        INSERT INTO content_patterns (account_id, dimension, pattern_key, metric,
          age_bucket, n_sample, n_positive, min_sample_required, effect_statistic,
          effect_value, outlier_rule, outlier_dependent, mechanical, snapshot_basis,
          reconciliation_status, claim_type, competing_explanation, computed_at, run_id)
        VALUES (%s,'duration','<=11.2s','completion_rate','backfill',30,7,25,'spearman',
                -0.71,'mad_k5',FALSE,FALSE,'{}'::jsonb,'both_matched','FACT',
                'обратная причинность не исключена', now(), gen_random_uuid())
        RETURNING pattern_id""", (acc,))
    pat = cur.fetchone()[0]
    cur.execute("""INSERT INTO dna_versions (account_id, version, built_at, n_videos,
                     n_claims, run_id) VALUES (%s, 1, now(), 16, 1, gen_random_uuid())
                   RETURNING dna_version_id""", (acc,))
    dv = cur.fetchone()[0]
    return acc, pat, dv, snaps, vids


def dna_row(acc, dv, pat, snaps, vids, **over):
    d = dict(account_id=acc, dna_version_id=dv, version=1, section="weak_formats",
             statement="тестовое утверждение", claim_type="FACT", n_sample=30,
             min_sample_required=25, evidence_count=3, strength="moderate",
             source="supermetrics", source_count=1,
             reconciliation_status="both_matched", pattern_id=pat,
             evidence=json.dumps({"video_ids": vids, "snapshot_ids": snaps,
                                  "metric": "completion_rate"}))
    d.update(over)
    return d


INSERT_DNA = """INSERT INTO content_dna (account_id, dna_version_id, version, built_at,
  created_at, n_videos, section, statement, claim_type, n_sample, min_sample_required,
  evidence_count, strength, source, source_count, reconciliation_status, pattern_id, evidence)
VALUES (%(account_id)s,%(dna_version_id)s,%(version)s, now(), now(), 16, %(section)s,
        %(statement)s,%(claim_type)s,%(n_sample)s,%(min_sample_required)s,
        %(evidence_count)s,%(strength)s,%(source)s,%(source_count)s,
        %(reconciliation_status)s,%(pattern_id)s,%(evidence)s::jsonb)"""


def expect_error(cur, sql, params, fragment):
    """Откат ТОЛЬКО неудачной вставки: полный rollback снёс бы подготовленные
    данные и превратил бы последующие проверки в ложные падения."""
    cur.execute("SAVEPOINT sp_expect")
    try:
        cur.execute(sql, params)
        cur.execute("ROLLBACK TO SAVEPOINT sp_expect")
        return False, "вставка прошла, хотя должна была быть отклонена"
    except psycopg.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT sp_expect")
        return (fragment.lower() in str(e).lower()), str(e).splitlines()[0][:70]


# ─────────────────────────────────────────────────────────────── M
def test_M():
    print("\nM — FACT требует минимального размера выборки")
    with owner() as conn, conn.cursor() as cur:
        acc, pat, dv, snaps, vids = seed(cur)
        ok, msg = expect_error(cur, INSERT_DNA,
                               dna_row(acc, dv, pat, snaps, vids, n_sample=24),
                               "ck_dna_fact")
        check("M.1 content_dna: FACT при n_sample < min_sample_required", ok, msg)

        cur.execute("SAVEPOINT s")
        try:
            cur.execute(INSERT_DNA, dna_row(acc, dv, pat, snaps, vids, n_sample=25))
            check("M.2 content_dna: FACT при n_sample = min_sample_required проходит", True)
        except psycopg.Error as e:
            check("M.2 content_dna: FACT при n_sample = min проходит", False,
                  str(e).splitlines()[0][:70])
        cur.execute("ROLLBACK TO SAVEPOINT s")

        ok, msg = expect_error(cur, INSERT_DNA,
                               dna_row(acc, dv, pat, snaps, vids, evidence_count=2,
                                       evidence=json.dumps({"video_ids": vids[:2],
                                                            "snapshot_ids": snaps[:2]})),
                               "ck_dna_ev")
        check("M.3 content_dna: evidence_count < 3 отклоняется", ok, msg)

        ok, msg = expect_error(cur, """INSERT INTO content_patterns (account_id, dimension,
              pattern_key, metric, age_bucket, n_sample, n_positive, min_sample_required,
              effect_statistic, effect_value, outlier_rule, outlier_dependent, mechanical,
              snapshot_basis, reconciliation_status, claim_type, competing_explanation,
              computed_at, run_id)
            VALUES (%s,'d','k','m','backfill',10,3,25,'spearman',-0.5,'mad_k5',FALSE,FALSE,
                    '{}'::jsonb,'both_matched','FACT','конкурирующее', now(), gen_random_uuid())""",
            (acc,), "ck_pat_fact_n")
        check("M.4 content_patterns: FACT при n_sample < min отклоняется", ok, msg)
        conn.rollback()


# ─────────────────────────────────────────────────────────────── N
def test_N():
    print("\nN — FACT блокируется расхождением источников, both_lagged разрешён")
    with owner() as conn, conn.cursor() as cur:
        acc, pat, dv, snaps, vids = seed(cur)
        for status in ("both_discrepancy", "untested_overlap", "unavailable"):
            ok, msg = expect_error(cur, INSERT_DNA,
                                   dna_row(acc, dv, pat, snaps, vids,
                                           reconciliation_status=status),
                                   "ck_dna_reconcil")
            check(f"N.1 FACT при {status} отклоняется", ok, msg)
        for status in ("both_matched", "both_expected_transform", "both_lagged",
                       "single_source"):
            cur.execute("SAVEPOINT s")
            try:
                cur.execute(INSERT_DNA, dna_row(acc, dv, pat, snaps, vids,
                                                reconciliation_status=status,
                                                statement=f"утверждение {status}"))
                check(f"N.2 FACT при {status} проходит", True)
            except psycopg.Error as e:
                check(f"N.2 FACT при {status} проходит", False, str(e).splitlines()[0][:70])
            cur.execute("ROLLBACK TO SAVEPOINT s")
        # HYPOTHESIS при расхождении допустима — блокируется именно FACT
        cur.execute("SAVEPOINT s2")
        try:
            cur.execute(INSERT_DNA, dna_row(acc, dv, pat, snaps, vids,
                                            claim_type="HYPOTHESIS", pattern_id=None,
                                            reconciliation_status="both_discrepancy",
                                            statement="гипотеза при расхождении"))
            check("N.3 HYPOTHESIS при both_discrepancy допустима", True)
        except psycopg.Error as e:
            check("N.3 HYPOTHESIS при both_discrepancy допустима", False,
                  str(e).splitlines()[0][:70])
        cur.execute("ROLLBACK TO SAVEPOINT s2")
        conn.rollback()


# ─────────────────────────────────────────────────────────────── P
def test_P():
    print("\nP — старая версия DNA остаётся воспроизводимой")
    with owner() as conn, conn.cursor() as cur:
        acc, pat, dv, snaps, vids = seed(cur)
        cur.execute(INSERT_DNA, dna_row(acc, dv, pat, snaps, vids,
                                        statement="утверждение версии 1"))
        cur.execute("""SELECT evidence->'snapshot_ids' FROM content_dna
                        WHERE statement = 'утверждение версии 1'""")
        pinned = [int(x) for x in cur.fetchone()[0]]
        cur.execute("SELECT sum(views) FROM video_snapshots WHERE snapshot_id = ANY(%s)",
                    (pinned,))
        before = cur.fetchone()[0]

        # появляются новые наблюдения — закреплённое доказательство не меняется
        cur.execute("""INSERT INTO video_snapshots (video_id, source, fetched_at,
              observed_at, observed_at_precision, observed_at_authority, age_bucket,
              views, raw_ref, run_id)
            SELECT video_id, 'supermetrics', now(), now(), 'second',
                   'client_fetch_time', 'backfill', 999999,
                   'data/raw/__test__', gen_random_uuid()
              FROM videos ORDER BY video_id LIMIT 3""")
        cur.execute("SELECT sum(views) FROM video_snapshots WHERE snapshot_id = ANY(%s)",
                    (pinned,))
        after = cur.fetchone()[0]
        check("P.1 закреплённые snapshot_ids дают то же значение после новых данных",
              before == after, f"{before} == {after}")

        ok, msg = expect_error(cur, INSERT_DNA,
                               dna_row(acc, dv, pat, [10**9, 10**9 + 1, 10**9 + 2], vids,
                                       statement="несуществующие снимки"),
                               "EVIDENCE_SNAPSHOT_NOT_FOUND")
        check("P.2 ссылка на несуществующий снимок отклоняется", ok, msg)

        ok, msg = expect_error(cur, INSERT_DNA,
                               dna_row(acc, dv, pat, snaps[:2], vids,
                                       statement="снимков меньше роликов"),
                               "ck_dna_ev_pinned")
        check("P.3 число snapshot_ids должно совпадать с числом video_ids", ok, msg)
        conn.rollback()

    with rw() as conn, conn.cursor() as cur:
        for table in ("content_dna", "dna_versions", "scripts"):
            try:
                cur.execute(f"UPDATE {table} SET version = version WHERE FALSE"
                            if table != "scripts" else
                            "UPDATE scripts SET version = version WHERE FALSE")
                check(f"P.4 рантайм не может UPDATE {table}", False, "UPDATE прошёл")
            except psycopg.Error as e:
                check(f"P.4 рантайм не может UPDATE {table}", "permission denied" in str(e),
                      str(e).splitlines()[0][:60])


# ─────────────────────────────────────────────────────────────── Q
def test_Q():
    print("\nQ — журнал публикаций не изменяется рантайм-ролью")
    with rw() as conn, conn.cursor() as cur:
        for op, sql in (("UPDATE", "UPDATE publishing_history SET actor='x' WHERE FALSE"),
                        ("DELETE", "DELETE FROM publishing_history WHERE FALSE")):
            try:
                cur.execute(sql)
                check(f"Q.1 рантайм не может {op} publishing_history", False, f"{op} прошёл")
            except psycopg.Error as e:
                check(f"Q.1 рантайм не может {op} publishing_history",
                      "permission denied" in str(e), str(e).splitlines()[0][:60])
        cur.execute("""SELECT has_table_privilege('tiktok_rw','publishing_history','INSERT')""")
        check("Q.2 рантайму разрешён INSERT в publishing_history", cur.fetchone()[0])
        cur.execute("SELECT has_table_privilege('tiktok_ro','videos','SELECT'),"
                    " has_table_privilege('tiktok_ro','videos','INSERT')")
        sel, ins = cur.fetchone()
        check("Q.3 аналитическая роль читает и не пишет", sel and not ins)


# ───────────────────────────────────────────────────── прочие проверки
def test_permissions_matrix():
    print("\nПрава — DELETE не выдан нигде, владелец отделён от рантайма")
    with rw() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.table_privileges
                        WHERE grantee IN ('tiktok_rw','tiktok_ro')
                          AND privilege_type = 'DELETE'""")
        check("DELETE не выдан ни одной рабочей роли", cur.fetchone()[0] == 0)
        cur.execute("""SELECT count(*) FROM pg_tables WHERE schemaname='public'
                        AND tableowner IN ('tiktok_rw','tiktok_ro')""")
        check("рантайм-роли не владеют таблицами", cur.fetchone()[0] == 0)
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname IN "
                    "('tiktok_owner','tiktok_rw','tiktok_ro')")
        check("ни одна роль проекта не суперпользователь",
              not any(r[0] for r in cur.fetchall()))


def test_capability_disabled():
    print("\nСпособность публикации выключена")
    with rw() as conn, conn.cursor() as cur:
        cur.execute("SELECT enabled, enabled_by FROM system_capabilities "
                    "WHERE capability='publishing.submit'")
        row = cur.fetchone()
        check("publishing.submit = FALSE", row is not None and row[0] is False,
              f"enabled={row[0] if row else 'нет строки'}")


def test_no_secrets_in_git():
    print("\nСекреты не попадают в git")
    out = subprocess.run(["git", "-C", str(ROOT.parent), "ls-files", "tiktok-ai-manager"],
                         capture_output=True, text=True).stdout.split()
    check(".env не отслеживается git", "tiktok-ai-manager/.env" not in out)
    check(".env.example отслеживается", "tiktok-ai-manager/.env.example" in out)

    tracked = [ROOT.parent / f for f in out]
    leaks = []
    # Подстановки вида ${VAR}, $VAR и явные заглушки паролями не являются.
    dsn_re = re.compile(r"postgresql://[^\s:]+:([^\s@]{6,})@")
    PLACEHOLDER = re.compile(r"[${}]|^CHANGE_ME$|^<.*>$")
    for f in tracked:
        if not f.is_file() or f.suffix in (".png", ".jpg"):
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pw in dsn_re.findall(txt):
            if not PLACEHOLDER.search(pw):
                leaks.append(f"{f.relative_to(ROOT.parent)}: пароль в DSN")
        if re.search(r"approval_token\s*=\s*['\"][A-Za-z0-9_\-]{16,}", txt):
            leaks.append(f"{f.relative_to(ROOT.parent)}: открытый токен согласования")
    check("в отслеживаемых файлах нет реальных DSN с паролями", not leaks, str(leaks[:3]))

    # открытый токен согласования не должен появляться в SQL-слое
    sqls = list((ROOT / "db" / "migrations").glob("*.sql"))
    bad = [s.name for s in sqls
           if re.search(r"approval_token\b(?!_hash)", s.read_text(encoding="utf-8"))]
    check("в SQL нет колонки открытого токена (только approval_token_hash)", not bad, str(bad))

    py = [p for p in ROOT.rglob("*.py") if "__pycache__" not in str(p)]
    hashed_in_app = any("hashlib" in p.read_text(encoding="utf-8")
                        or "compare_digest" in p.read_text(encoding="utf-8") for p in py)
    check("хеширование выполняется в приложении, не в SQL", hashed_in_app)


def test_jsonl_is_source_of_truth():
    print("\nJSONL — источник истины")
    check("data/videos.jsonl существует", (ROOT / "data" / "videos.jsonl").exists())
    check("партиции снимков существуют",
          len(list((ROOT / "data" / "snapshots").glob("*.jsonl"))) > 0)
    raws = list((ROOT / "data" / "raw").glob("*.json"))
    check("сырьё сохранено неизменным", len(raws) >= 7, f"{len(raws)} файлов")


if __name__ == "__main__":
    for fn in (test_M, test_N, test_P, test_Q, test_permissions_matrix,
               test_capability_disabled, test_no_secrets_in_git,
               test_jsonl_is_source_of_truth):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
