#!/usr/bin/env python3
"""Phase 4 storage: версионность, append-only, прослеживаемость.

Тестовые строки пишутся в транзакции и откатываются: боевые данные
признаков не изменяются.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from core import config
from features import policies as F

RESULTS = []
PV1 = F.FEATURE_POLICY_VERSION          # feature-policy-1.0.0
PV2 = "feature-policy-1.1.0"            # гипотетическая следующая версия

INSERT = """INSERT INTO video_features (
  video_id, feature_name, feature_value, feature_value_numeric, feature_type,
  feature_status, tier, policy_version, extractor_version, source_basis,
  reconciliation_basis, evidence_refs, computed_from, computed_at, run_id)
VALUES (%s,%s,%s,%s,'numeric','observed','tier_0',%s,'test','{}'::jsonb,'{}'::jsonb,
        '{}'::text[], '{}'::text[], now(), gen_random_uuid())"""


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<60}{detail}")


def owner():
    return psycopg.connect(config.dsn("owner"), autocommit=False)


def runtime():
    return psycopg.connect(config.dsn("rw"), autocommit=True)


def a_video(cur):
    cur.execute("SELECT video_id FROM videos ORDER BY video_id LIMIT 1")
    return cur.fetchone()[0]


# ───────────────────────────────────────────────────────────────── TEST A
def test_a_one_version_once():
    print("\nTEST A — одна версия признака записывается один раз")
    with owner() as conn, conn.cursor() as cur:
        vid = a_video(cur)
        cur.execute("SAVEPOINT sp")
        cur.execute(INSERT, (vid, "__test_feature__", "1", 1, PV1))
        check("первая запись проходит", True)
        try:
            cur.execute(INSERT, (vid, "__test_feature__", "2", 2, PV1))
            check("повторная запись той же версии отклоняется", False, "вставка прошла")
        except psycopg.errors.UniqueViolation as e:
            check("повторная запись той же версии отклоняется",
                  "uq_video_feature_version" in str(e),
                  "uq_video_feature_version")
            cur.execute("ROLLBACK TO SAVEPOINT sp")
        conn.rollback()


# ───────────────────────────────────────────────────────────────── TEST B
def test_b_versions_coexist():
    print("\nTEST B — версии сосуществуют")
    with owner() as conn, conn.cursor() as cur:
        vid = a_video(cur)
        cur.execute(INSERT, (vid, "__test_feature__", "1", 1, PV1))
        cur.execute(INSERT, (vid, "__test_feature__", "2", 2, PV2))
        cur.execute("""SELECT policy_version, feature_value_numeric
                         FROM video_features
                        WHERE video_id=%s AND feature_name='__test_feature__'
                        ORDER BY policy_version""", (vid,))
        got = cur.fetchall()
        check("обе версии присутствуют одновременно", len(got) == 2, str(got))
        check("значения версий различны и сохранены",
              float(got[0][1]) == 1 and float(got[1][1]) == 2)
        check("версии различаются именно policy_version",
              got[0][0] == PV1 and got[1][0] == PV2)
        conn.rollback()


# ───────────────────────────────────────────────────────────────── TEST C
def test_c_runtime_cannot_modify():
    print("\nTEST C — рантайм не может изменять и удалять")
    with runtime() as conn, conn.cursor() as cur:
        for op, sql in (("UPDATE", "UPDATE video_features SET feature_value='x' WHERE FALSE"),
                        ("DELETE", "DELETE FROM video_features WHERE FALSE")):
            try:
                cur.execute(sql)
                check(f"рантайм не может {op}", False, f"{op} прошёл")
            except psycopg.Error as e:
                msg = str(e).splitlines()[0]
                check(f"рантайм не может {op}", "permission denied" in msg, msg[:46])
        cur.execute("SELECT has_table_privilege('tiktok_rw','video_features','INSERT'),"
                    " has_table_privilege('tiktok_rw','video_features','UPDATE'),"
                    " has_table_privilege('tiktok_rw','video_features','DELETE')")
        ins, upd, dele = cur.fetchone()
        check("права: INSERT есть, UPDATE и DELETE нет", ins and not upd and not dele)
        # Проверка по ИМЕНИ, а не по количеству: считать триггеры — значит
        # ломаться от каждого нового замка. Phase 6 добавил второй триггер
        # (запрет семантики без видео), и это не отменяет первого.
        cur.execute("""SELECT tgname FROM pg_trigger
                        WHERE tgrelid='video_features'::regclass
                          AND NOT tgisinternal ORDER BY tgname""")
        triggers = [r[0] for r in cur.fetchall()]
        check("триггер append-only установлен как второй замок",
              "trg_vf_append_only" in triggers, str(triggers))


# ───────────────────────────────────────────────────────────────── TEST D
def test_d_written_row_immutable():
    print("\nTEST D — записанная строка неизменна для рантайма")
    with runtime() as conn, conn.cursor() as cur:
        cur.execute("""SELECT video_id, feature_name, feature_value_numeric
                         FROM video_features
                        WHERE policy_version=%s AND feature_name='views'
                        ORDER BY video_id LIMIT 1""", (PV1,))
        vid, name, before = cur.fetchone()
        try:
            cur.execute("""UPDATE video_features SET feature_value_numeric = 999999
                            WHERE video_id=%s AND feature_name=%s AND policy_version=%s""",
                        (vid, name, PV1))
            check("боевое значение не изменяется рантаймом", False, "UPDATE прошёл")
        except psycopg.Error:
            pass
        cur.execute("""SELECT feature_value_numeric FROM video_features
                        WHERE video_id=%s AND feature_name=%s AND policy_version=%s""",
                    (vid, name, PV1))
        after = cur.fetchone()[0]
        check("боевое значение не изменилось", before == after, f"{before} == {after}")


# ───────────────────────────────────────────────────────────────── TEST E
def test_e_new_version_leaves_old_intact():
    print("\nTEST E — новая версия не трогает прежние строки")
    with owner() as conn, conn.cursor() as cur:
        cur.execute("""SELECT md5(string_agg(
                          video_id||'|'||feature_name||'|'||
                          coalesce(feature_value,'~')||'|'||feature_status, ','
                          ORDER BY video_id, feature_name))
                         FROM video_features WHERE policy_version=%s""", (PV1,))
        before = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM video_features WHERE policy_version=%s""",
                    (PV1,))
        n_before = cur.fetchone()[0]

        cur.execute("""INSERT INTO video_features (
              video_id, feature_name, feature_value, feature_value_numeric,
              feature_type, feature_status, tier, policy_version, extractor_version,
              source_basis, reconciliation_basis, evidence_refs, computed_from,
              computed_at, run_id)
            SELECT video_id, feature_name, '777', 777, 'numeric', 'observed', 'tier_0',
                   %s, 'test', '{}'::jsonb, '{}'::jsonb, '{}'::text[], '{}'::text[],
                   now(), gen_random_uuid()
              FROM video_features WHERE policy_version=%s AND feature_name='views'""",
            (PV2, PV1))
        added = cur.rowcount
        cur.execute("""SELECT md5(string_agg(
                          video_id||'|'||feature_name||'|'||
                          coalesce(feature_value,'~')||'|'||feature_status, ','
                          ORDER BY video_id, feature_name))
                         FROM video_features WHERE policy_version=%s""", (PV1,))
        after = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM video_features WHERE policy_version=%s""",
                    (PV1,))
        n_after = cur.fetchone()[0]
        check(f"добавлено {added} строк новой версии", added == 16, str(added))
        check("контрольная сумма прежней версии не изменилась", before == after,
              (before or "")[:16])
        check("число строк прежней версии не изменилось", n_before == n_after,
              f"{n_before} == {n_after}")
        conn.rollback()


# ────────────────────────────────────────────────────── СОДЕРЖИМОЕ И СВЯЗИ
def test_content_and_traceability():
    print("\nСОДЕРЖИМОЕ И ПРОСЛЕЖИВАЕМОСТЬ")
    from features.run import db_matches_jsonl
    problems = db_matches_jsonl()
    check("содержимое БД совпадает с JSONL", not problems, str(problems[:2]))

    with runtime() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*), count(DISTINCT video_id),"
                    " count(DISTINCT feature_name) FROM video_features"
                    " WHERE policy_version=%s", (PV1,))
        n, nv, nf = cur.fetchone()
        check("720 строк", n == 720, str(n))
        check("16 роликов", nv == 16, str(nv))
        check("45 имён признаков", nf == 45, str(nf))
        cur.execute("""SELECT feature_status, count(*) FROM video_features
                        WHERE policy_version=%s GROUP BY 1 ORDER BY 1""", (PV1,))
        st = dict(cur.fetchall())
        check("статусы совпадают с JSONL",
              st == {"derived": 192, "insufficient_baseline": 48,
                     "observed": 336, "unavailable": 144}, str(st))

        cur.execute("""SELECT computed_from, evidence_refs, source_basis,
                              reconciliation_basis
                         FROM video_features
                        WHERE feature_name='engagement_rate' AND policy_version=%s
                        ORDER BY video_id LIMIT 1""", (PV1,))
        cf, ev, sb, rb = cur.fetchone()
        check("engagement_rate: computed_from перечисляет входы",
              set(cf) == {"likes", "comments", "shares", "favorites", "views"}, str(cf))
        check("evidence_refs — настоящие ссылки, а не текстовое описание",
              len(ev) >= 5 and all(len(x) > 8 for x in ev), f"{len(ev)} ссылок")
        check("source_basis указывает источник по каждому входу",
              set(sb) == set(cf), str(sorted(sb)))
        check("reconciliation_basis указывает статус сверки по каждому входу",
              set(rb) == set(cf) and all(
                  v in ("both_matched", "both_expected_transform", "both_lagged",
                        "single_source") for v in rb.values()), str(sorted(set(rb.values()))))

        ref = ev[0]
        cur.execute("SELECT count(*) FROM reconciliation_results WHERE content_hash=%s",
                    (ref,))
        hit_recon = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM video_snapshots v JOIN videos vv"
                    " ON vv.video_id=v.video_id WHERE %s LIKE v.video_id||'%%'", (ref,))
        check("ссылка разрешается в результат сверки",
              hit_recon >= 1 or ref.startswith(("R", "supermetrics", "metricool")),
              f"reconciliation_results: {hit_recon}")

        cur.execute("""SELECT count(*) FROM video_features f
                        WHERE policy_version=%s AND NOT EXISTS (
                          SELECT 1 FROM videos v WHERE v.video_id=f.video_id)""", (PV1,))
        check("каждая строка ссылается на существующий ролик", cur.fetchone()[0] == 0)


if __name__ == "__main__":
    for fn in (test_a_one_version_once, test_b_versions_coexist,
               test_c_runtime_cannot_modify, test_d_written_row_immutable,
               test_e_new_version_leaves_old_intact, test_content_and_traceability):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
