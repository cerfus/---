#!/usr/bin/env python3
"""Phase 5.1 hardening — регрессионные тесты A-G.

Каждый тест закрывает конкретный способ, которым механически зависимая или
нерешённая пара метрик могла бы просочиться в содержательный вывод.
Тесты именованы буквами ровно так, как их перечислил владелец.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from core import config
from insights import generators, pipeline
from insights import policies as P
from insights import run as R
from insights.validator import find_violations

RESULTS = []
MECH = ("duration_sec", "completion_rate")
CAND = ("duration_sec", "avg_view_time_sec")
FREE = ("shares", "views")


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<66}{detail}")


def assoc(x, y, rho, p, n=16, **extra):
    row = {"x_metric": x, "y_metric": y, "rho": rho, "p_two_sided": p, "n": n,
           "total_n": n, "status": "measured",
           "sample_status": "insufficient_sample", "coverage_ratio": 1.0,
           "method": "spearman_rho.v1",
           "significance_method": "student_t_approximation.v1",
           "min_sample_required": 25, "interpretation": "not_interpreted",
           "causality_claim": False, "is_content_pattern": False,
           "limitations": ["тест"], "engine_version": "association-1.0.0",
           "policy_version": "analytics-policies-1.0.0"}
    row.update(extra)
    return row


def expect_error(cur, sql, params, fragment, name):
    """Ошибка ожидается. SAVEPOINT — чтобы отказ не снёс всю транзакцию."""
    cur.execute("SAVEPOINT sp")
    try:
        cur.execute(sql, params)
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        check(name, False, "вставка прошла, хотя должна была быть отвергнута")
    except psycopg.Error as e:
        cur.execute("ROLLBACK TO SAVEPOINT sp")
        check(name, fragment in str(e), str(e).split("\n")[0][:60])


# ══════════════════════════════════════════════════════════════════════ A
def test_A_mechanical_pair_blocked():
    """A. Пара duration_sec ~ completion_rate блокируется как mechanical."""
    print("\nA — механическая пара блокируется и статистикой не пробивается")
    dep = P.mechanical_dependency(*MECH)
    check("A1 пара в реестре", dep is not None)
    check("A2 порядок аргументов не важен",
          P.mechanical_dependency(*reversed(MECH)) is not None)

    for rho, p in ((-0.717, 0.0018), (-0.999, 1e-12)):
        ev = pipeline.evaluate_association(assoc(*MECH, rho, p))
        check(f"A3 конвейер остановлен на mechanical_dependency (p={p})",
              ev["stopped_at"] == "mechanical_dependency", ev["stopped_at"] or "-")
        check(f"A4 машинная причина (p={p})",
              ev["reason_code"] == "mechanically_dependent", ev["reason_code"])
        check(f"A5 стадия значимости НЕ исполнялась (p={p})",
              "statistical_significance" not in ev["executed"],
              str(ev["executed"]))
        check(f"A6 все повышения закрыты (p={p})",
              not any(ev["allow"].values()), str(ev["allow"]))

    ins, blocked = generators.from_associations([assoc(*MECH, -0.999, 1e-12)])
    check("A7 ни одного вывода не выпущено", ins == [], str(len(ins)))
    check("A8 ровно одна запись блокировки", len(blocked) == 1, str(len(blocked)))
    b = blocked[0]
    check("A9 причина машинночитаема",
          b["reason_code"] == P.MECHANICAL_REASON_CODE, b["reason_code"])
    check("A10 статистика сохранена, а не стёрта",
          b["detail"]["rho"] == -0.999 and b["detail"]["n"] == 16)
    check("A11 перечислено, что именно запрещено",
          set(b["detail"]["forbidden"]) == set(P.PROMOTION_TARGETS))
    for target in P.PROMOTION_TARGETS:
        ok, code, _ = P.promotion_allowed(*MECH, target)
        check(f"A12 promotion_allowed отказывает: {target}",
              ok is False and code == P.MECHANICAL_REASON_CODE, code or "-")


# ══════════════════════════════════════════════════════════════════════ B
def test_B_candidate_not_auto_promoted():
    """B. duration_sec ~ avg_view_time_sec не повышается автоматически."""
    print("\nB — нерешённый кандидат не повышается от одной лишь значимости")
    check("B1 пара НЕ зарегистрирована как механическая",
          P.mechanical_dependency(*CAND) is None)
    check("B2 пара числится кандидатом",
          P.mechanical_candidate(*CAND) is not None)
    check("B3 реестры не пересекаются",
          not (set(P.MECHANICAL_DEPENDENCIES)
               & set(P.MECHANICAL_CANDIDATES_NOT_REGISTERED)))

    # значимая связь — именно тот случай, ради которого защита и заведена
    ev = pipeline.evaluate_association(assoc(*CAND, 0.82, 0.0001))
    check("B4 значимость достигнута и стадия исполнена",
          "statistical_significance" in ev["executed"]
          and ev["stopped_at"] is None, ev["stopped_at"] or "не остановлен")
    check("B5 гипотеза разрешена", ev["allow"]["HYPOTHESIS"] is True)
    for target in ("RECOMMENDATION", "content_dna_evidence",
                   "experiment_basis", "FACT"):
        check(f"B6 {target} закрыт", ev["allow"][target] is False)
    check("B7 отказ помечен кодом кандидата",
          ev["deny"]["content_dna_evidence"]["reason_code"]
          == P.MECHANICAL_CANDIDATE_REASON_CODE)
    check("B8 отказ выдан на стадии mechanical_dependency",
          ev["deny"]["RECOMMENDATION"]["stage"] == "mechanical_dependency",
          ev["deny"]["RECOMMENDATION"]["stage"])

    for target in ("RECOMMENDATION", "content_dna_evidence", "experiment_basis"):
        ok, code, _ = P.promotion_allowed(*CAND, target)
        check(f"B9 promotion_allowed отказывает: {target}",
              ok is False and code == P.MECHANICAL_CANDIDATE_REASON_CODE,
              code or "-")
    ok, _, _ = P.promotion_allowed(*CAND, "HYPOTHESIS")
    check("B10 гипотеза по кандидату остаётся разрешена", ok is True)

    ins, blocked = generators.from_associations([assoc(*CAND, 0.82, 0.0001)])
    check("B11 гипотеза выпущена",
          len([i for i in ins if i["claim_type"] == "HYPOTHESIS"]) == 1)
    check("B12 рекомендации не выпущено",
          not [i for i in ins if i["claim_type"] == "RECOMMENDATION"])
    codes = {b["reason_code"] for b in blocked}
    check("B13 блокировка кандидата записана",
          P.MECHANICAL_CANDIDATE_REASON_CODE in codes, str(sorted(codes)))
    rec = [b for b in blocked if b["conclusion"].startswith("рекомендация")]
    check("B14 отказ рекомендации записан отдельно", len(rec) == 1)

    # в реальном прогоне связь незначима — блокировка кандидата обязана
    # присутствовать и там: политика не зависит от текущих чисел
    _, real_blocked, _, _, _, _ = R.build(write=False)
    cand_rows = [b for b in real_blocked
                 if b["reason_code"] == P.MECHANICAL_CANDIDATE_REASON_CODE]
    check("B15 в сквозном прогоне кандидат тоже заблокирован к повышению",
          len(cand_rows) == 1, str(len(cand_rows)))
    check("B16 названы обе метрики пары",
          all(m in cand_rows[0]["conclusion"] for m in CAND))
    check("B17 названо решение владельца как условие",
          cand_rows[0]["detail"]["decision_required_from"] == "owner")


# ══════════════════════════════════════════════════════════════════════ C
def test_C_not_in_content_dna():
    """C. Механическая пара не попадает в Content DNA как evidence."""
    print("\nC — механическая пара не становится доказательством Content DNA")
    ok, code, _ = P.promotion_allowed(*MECH, "content_dna_evidence")
    check("C1 политика отказывает", ok is False and code == P.MECHANICAL_REASON_CODE)
    try:
        P.assert_promotion_allowed(*MECH, "content_dna_evidence")
        check("C2 жёсткая форма поднимает исключение", False, "исключения нет")
    except P.MechanicalLeakage as e:
        check("C2 жёсткая форма поднимает исключение",
              P.MECHANICAL_REASON_CODE in str(e), str(e)[:50])

    with psycopg.connect(config.dsn("owner"), autocommit=False) as c, c.cursor() as cur:
        cur.execute("SELECT metric_a, metric_b FROM mechanical_metric_pairs")
        db_pairs = {frozenset(r) for r in cur.fetchall()}
        check("C3 реестр в БД совпадает с реестром в коде",
              db_pairs == set(P.MECHANICAL_DEPENDENCIES), str(db_pairs))
        cur.execute("SELECT is_mechanical_pair(%s,%s), is_mechanical_pair(%s,%s)",
                    (MECH[0], MECH[1], MECH[1], MECH[0]))
        check("C4 функция БД не зависит от порядка метрик",
              cur.fetchone() == (True, True))
        cur.execute("SELECT is_mechanical_pair(%s,%s)", CAND)
        check("C5 кандидат в БД-реестр не попал (решения владельца нет)",
              cur.fetchone()[0] is False)

        cur.execute("SELECT account_id FROM accounts LIMIT 1")
        acc = cur.fetchone()[0]
        cur.execute("""INSERT INTO dna_versions (account_id, version, built_at,
              n_videos, n_claims, run_id)
            VALUES (%s, 999, now(), 16, 0, gen_random_uuid())
            RETURNING dna_version_id""", (acc,))
        dv = cur.fetchone()[0]
        base = dict(account_id=acc, dna_version_id=dv)
        sql = """INSERT INTO content_dna (account_id, dna_version_id, version,
              built_at, created_at, n_videos, section, statement, claim_type,
              n_sample, min_sample_required, evidence_count, strength, source,
              source_count, reconciliation_status, evidence)
            VALUES (%(account_id)s, %(dna_version_id)s, 999, now(), now(), 16,
              'pacing_patterns', %(statement)s, 'HYPOTHESIS', 16, 25, 3,
              'weak', 'supermetrics', 1, 'single_source', %(evidence)s::jsonb)"""
        ev_mech = json.dumps({"video_ids": ["a", "b", "c"],
                              "snapshot_ids": [1, 2, 3],
                              "metric_pair": list(MECH)})
        expect_error(cur, sql, {**base, "statement": "механическая пара",
                                "evidence": ev_mech},
                     "MECHANICALLY_DEPENDENT",
                     "C6 БД отвергает Content DNA с механической парой")
        expect_error(cur, sql,
                     {**base, "statement": "обратный порядок",
                      "evidence": json.dumps(
                          {"video_ids": ["a", "b", "c"], "snapshot_ids": [1, 2, 3],
                           "metric_pair": list(reversed(MECH))})},
                     "MECHANICALLY_DEPENDENT",
                     "C7 отказ не зависит от порядка метрик в evidence")

        cur.execute("SAVEPOINT ok_sp")
        cur.execute(sql, {**base, "statement": "свободная пара",
                          "evidence": json.dumps(
                              {"video_ids": ["a", "b", "c"],
                               "snapshot_ids": [1, 2, 3],
                               "metric_pair": list(FREE)})})
        check("C8 немеханическая пара в Content DNA проходит", True)
        cur.execute("ROLLBACK TO SAVEPOINT ok_sp")

        expect_error(cur, """INSERT INTO content_patterns (account_id, dimension,
              pattern_key, metric, age_bucket, n_sample, n_positive,
              min_sample_required, effect_statistic, effect_value, outlier_rule,
              outlier_dependent, mechanical, snapshot_basis,
              reconciliation_status, claim_type, competing_explanation,
              computed_at, run_id)
            VALUES (%s,'duration_bucket','long','completion_rate','backfill',
              16, 8, 25, 'spearman_rho', -0.717, 'iqr.1.5.v1', FALSE, TRUE,
              '{}'::jsonb, 'single_source', 'HYPOTHESIS', 'механика метрик',
              now(), gen_random_uuid())""", (acc,),
            "ck_pat_mechanical_no_claim",
            "C9 БД отвергает content_patterns с mechanical = TRUE")
        conn_rollback(c)


def conn_rollback(conn):
    conn.rollback()


# ══════════════════════════════════════════════════════════════════════ D
def test_D_not_experiment_basis():
    """D. Механическая пара не становится основанием эксперимента."""
    print("\nD — механическая пара не становится основанием эксперимента")
    ok, code, _ = P.promotion_allowed(*MECH, "experiment_basis")
    check("D1 политика отказывает", ok is False and code == P.MECHANICAL_REASON_CODE)

    with psycopg.connect(config.dsn("owner"), autocommit=False) as c, c.cursor() as cur:
        cur.execute("SELECT account_id FROM accounts LIMIT 1")
        acc = cur.fetchone()[0]
        sql = """INSERT INTO experiments (account_id, code, hypothesis, variable,
              control_def, variant_def, success_metric, secondary_metrics,
              target_sample_size, age_bucket, success_criteria, status)
            VALUES (%(acc)s, %(code)s, 'проверка', %(variable)s, 'короткие',
              'длинные', %(success)s, %(secondary)s, 10, 'backfill',
              'разница', 'proposed')"""
        expect_error(cur, sql, {"acc": acc, "code": "EXP-MECH-1",
                                "variable": "duration_sec",
                                "success": "completion_rate", "secondary": []},
                     "MECHANICALLY_DEPENDENT",
                     "D2 БД отвергает эксперимент duration -> completion_rate")
        expect_error(cur, sql, {"acc": acc, "code": "EXP-MECH-2",
                                "variable": "completion_rate",
                                "success": "duration_sec", "secondary": []},
                     "MECHANICALLY_DEPENDENT",
                     "D3 отказ не зависит от направления")
        expect_error(cur, sql, {"acc": acc, "code": "EXP-MECH-3",
                                "variable": "duration_sec", "success": "views",
                                "secondary": ["completion_rate"]},
                     "MECHANICALLY_DEPENDENT",
                     "D4 механическая пара закрыта и во вторичных метриках")

        cur.execute("SAVEPOINT ok_sp")
        cur.execute(sql, {"acc": acc, "code": "EXP-FREE-1",
                          "variable": "duration_sec", "success": "views",
                          "secondary": ["shares"]})
        check("D5 немеханическая пара как основание проходит", True)
        cur.execute("ROLLBACK TO SAVEPOINT ok_sp")
        c.rollback()

    # кандидат основанием тоже не становится — но по своей причине
    ok, code, _ = P.promotion_allowed(*CAND, "experiment_basis")
    check("D6 кандидат тоже закрыт, но с другим кодом",
          ok is False and code == P.MECHANICAL_CANDIDATE_REASON_CODE, code or "-")


# ══════════════════════════════════════════════════════════════════════ E
def test_E_causal_validator_at_zero_hypotheses():
    """E. Причинный валидатор работает при нуле гипотез."""
    print("\nE — запрет причинности не зависит от числа гипотез")
    ins, blocked = generators.from_causality(0)
    check("E1 при нуле экспериментов запрет выпущен", len(blocked) == 1)
    check("E2 запрет машинночитаем",
          blocked[0]["reason_code"] == "no_concluded_experiment")
    check("E3 при завершённом эксперименте запрет снимается",
          generators.from_causality(1) == ([], []))

    real_ins, real_blocked, md, _, _, _ = R.build(write=False)
    hyps = [i for i in real_ins if i["claim_type"] == "HYPOTHESIS"]
    check("E4 в сквозном прогоне гипотез ноль", len(hyps) == 0, str(len(hyps)))
    check("E5 и при этом запрет причинности в прогоне присутствует",
          any(b["reason_code"] == "no_concluded_experiment"
              and "причинн" in b["conclusion"] for b in real_blocked))

    for phrase in ("duration causes views", "duration leads to views",
                   "duration increases views", "duration decreases views",
                   "duration results in views"):
        v = find_violations(phrase, "DATA_QUALITY")
        check(f"E6 форма заблокирована: «{phrase}»", bool(v), str(v)[:46])
    check("E7 при доказанной причинности те же формы допустимы",
          not find_violations("duration causes views", "DATA_QUALITY",
                              causality_established=True))
    body = md.replace("Причинность не установлена", "")
    check("E8 в тексте отчёта причинных конструкций нет",
          not find_violations(body, "DATA_QUALITY"),
          str(find_violations(body, "DATA_QUALITY"))[:46])


# ══════════════════════════════════════════════════════════════════════ F
def test_F_new_run_supersedes_report():
    """F. Новый прогон не оставляет старый отчёт текущим."""
    print("\nF — новый прогон отчёта вытесняет прежний из текущих")
    with psycopg.connect(config.dsn("owner"), autocommit=False) as c, c.cursor() as cur:
        cur.execute("""SELECT a.attname FROM pg_constraint k
              JOIN unnest(k.conkey) u(attnum) ON TRUE
              JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum
             WHERE k.conname = 'uq_report_run' ORDER BY a.attnum""")
        cols = [r[0] for r in cur.fetchall()]
        check("F1 идентичность отчёта включает период и прогон",
              set(cols) == {"account_id", "report_type", "period_start",
                            "period_end", "run_id"}, str(cols))

        cur.execute("SELECT account_id FROM accounts LIMIT 1")
        acc = cur.fetchone()[0]
        ins = """INSERT INTO reports (account_id, report_type, period_start,
              period_end, generated_at, path, summary, n_videos_in_period,
              n_new_snapshots, data_completeness, blocked_conclusions, run_id)
            VALUES (%s,'daily','2030-01-01','2030-01-01', %s, %s, %s, 1, 0,
              '{}'::jsonb, '[]'::jsonb, %s)"""
        old_run = "11111111-1111-4111-8111-111111111111"
        new_run = "22222222-2222-4222-8222-222222222222"
        cur.execute(ins, (acc, "2030-01-01 10:00+00", "old.md", "прогон 1", old_run))
        cur.execute("""SELECT run_id::text FROM reports_current
                        WHERE period_start='2030-01-01'""")
        check("F2 первый прогон становится текущим",
              [r[0] for r in cur.fetchall()] == [old_run])

        cur.execute(ins, (acc, "2030-01-01 11:00+00", "new.md", "прогон 2", new_run))
        cur.execute("""SELECT run_id::text FROM reports_current
                        WHERE period_start='2030-01-01'""")
        current = [r[0] for r in cur.fetchall()]
        check("F3 текущий отчёт ровно один", len(current) == 1, str(len(current)))
        check("F4 текущим стал новый прогон", current == [new_run], str(current))
        cur.execute("""SELECT count(*) FROM reports WHERE period_start='2030-01-01'""")
        check("F5 прежний прогон сохранён как история",
              cur.fetchone()[0] == 2)

        # тот же период, другой period_end — это другой отчёт, не конфликт
        cur.execute("""INSERT INTO reports (account_id, report_type, period_start,
              period_end, generated_at, path, summary, n_videos_in_period,
              n_new_snapshots, data_completeness, blocked_conclusions, run_id)
            VALUES (%s,'weekly','2030-01-01','2030-01-07', %s, 'w.md', 'нед', 1, 0,
              '{}'::jsonb, '[]'::jsonb, %s)""",
                    (acc, "2030-01-07 10:00+00", new_run))
        cur.execute("""SELECT count(*) FROM reports_current
                        WHERE period_start='2030-01-01'""")
        check("F6 отчёты с разным period_end сосуществуют",
              cur.fetchone()[0] == 2)
        c.rollback()

    cur_n = None
    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM reports_current WHERE report_type='daily'")
        cur_n = cur.fetchone()[0]
    check("F7 откат теста не оставил следов", cur_n == 1, str(cur_n))


# ══════════════════════════════════════════════════════════════════════ G
def test_G_rebuild_repeatability():
    """G. Повторная пересборка и повторный прогон дают тот же результат."""
    print("\nG — повторяемость пересборки и прогона")
    a_ins, a_blk, a_md, a_h, a_run, a_hashes = R.build(write=False)
    b_ins, b_blk, b_md, b_h, b_run, b_hashes = R.build(write=False)
    check("G1 insights_hash воспроизводится", a_h == b_h, a_h[:16])
    check("G2 insights_run_id воспроизводится", a_run == b_run, a_run[:8])
    check("G3 набор выводов совпадает побайтово", a_ins == b_ins)
    check("G4 набор блокировок совпадает побайтово", a_blk == b_blk)
    check("G5 текст отчёта совпадает побайтово", a_md == b_md)
    check("G6 входные хеши совпадают", a_hashes == b_hashes)

    manifest = json.loads((ROOT / "data" / "insights" / "manifest.json")
                          .read_text(encoding="utf-8"))
    check("G7 записанный на диск insights_hash совпадает с пересчитанным",
          manifest["insights_hash"] == a_h, manifest["insights_hash"][:16])
    check("G8 записанный run_id совпадает с пересчитанным",
          manifest["insights_run_id"] == a_run)
    check("G9 счётчики манифеста совпадают с прогоном",
          manifest["n_insights"] == len(a_ins)
          and manifest["n_blocked"] == len(a_blk),
          f"{manifest['n_insights']}/{manifest['n_blocked']}")
    on_disk = [json.loads(l) for l in
               (ROOT / "data" / "insights" / "blocked_conclusions.jsonl")
               .read_text(encoding="utf-8").splitlines() if l.strip()]
    check("G10 файл блокировок на диске совпадает с пересчитанным",
          on_disk == a_blk, f"{len(on_disk)} строк")

    ev1 = pipeline.evaluate_association(assoc(*MECH, -0.717, 0.0018))
    ev2 = pipeline.evaluate_association(assoc(*MECH, -0.717, 0.0018))
    check("G11 вердикт конвейера воспроизводим", ev1 == ev2)

    h = R.input_hashes()
    expect = {
        "analytics": "52fa355f77987c7aa8479e18a89616cd6c2b8e36dfaf49c477e2eca7b35d474f",
        "features": "71075aaf4170be7b6b43abde9126227e621274ed6570839e5b37f23b42f3bd6b",
        "reconciliation": "f52205acef19ba5082f192e7206ff9faa3917cde3be0e970f7afb4e0202506cf",
    }
    for k, v in sorted(expect.items()):
        check(f"G12 {k}_hash не тронут hardening-ом", h[k] == v, h[k][:16])


# ═══════════════════════════════════════════════════ порядок проверок (§3)
def test_order():
    print("\nПОРЯДОК ПРОВЕРОК — значимость не обходит механический гейт")
    check("O1 порядок зафиксирован ровно как утверждён",
          pipeline.CHECK_ORDER == (
              "data_eligibility", "sample_size", "mechanical_dependency",
              "outlier_robustness", "statistical_significance",
              "hypothesis_generation", "recommendation_generation"),
          str(pipeline.CHECK_ORDER))
    check("O2 mechanical_dependency стоит раньше statistical_significance",
          pipeline.CHECK_ORDER.index("mechanical_dependency")
          < pipeline.CHECK_ORDER.index("statistical_significance"))
    check("O3 sample_size стоит раньше mechanical_dependency",
          pipeline.CHECK_ORDER.index("sample_size")
          < pipeline.CHECK_ORDER.index("mechanical_dependency"))
    check("O4 outlier_robustness стоит между механикой и значимостью",
          pipeline.CHECK_ORDER.index("mechanical_dependency")
          < pipeline.CHECK_ORDER.index("outlier_robustness")
          < pipeline.CHECK_ORDER.index("statistical_significance"))
    check("O5 генерация рекомендации — последняя стадия",
          pipeline.CHECK_ORDER[-1] == "recommendation_generation")

    cases = [
        ("неизмеренная связь", assoc(*FREE, None, None, status="no_data"),
         "data_eligibility"),
        ("выборка меньше минимума", assoc(*FREE, 0.9, 0.001, n=3), "sample_size"),
        ("механическая пара", assoc(*MECH, -0.717, 0.0018),
         "mechanical_dependency"),
        ("шум", assoc(*FREE, 0.03, 0.91), "statistical_significance"),
    ]
    for label, row, stage in cases:
        ev = pipeline.evaluate_association(row)
        check(f"O6 {label}: остановка на {stage}",
              ev["stopped_at"] == stage, ev["stopped_at"] or "-")
        check(f"O7 {label}: исполненные стадии идут по порядку",
              pipeline.order_is_respected(ev["executed"]), str(ev["executed"]))
        after = pipeline.CHECK_ORDER[pipeline.CHECK_ORDER.index(stage) + 1:]
        check(f"O8 {label}: последующие стадии не исполнялись",
              not (set(after) & set(ev["executed"])), str(ev["executed"]))

    ev = pipeline.evaluate_association(assoc(*FREE, 0.9, 0.0001))
    check("O9 свободная значимая связь проходит все семь стадий",
          ev["executed"] == list(pipeline.CHECK_ORDER), str(len(ev["executed"])))
    check("O10 гипотеза разрешена, рекомендация — нет",
          ev["allow"]["HYPOTHESIS"] and not ev["allow"]["RECOMMENDATION"])
    check("O11 рекомендация закрыта непроверенной устойчивостью",
          ev["deny"]["RECOMMENDATION"]["reason_code"]
          == pipeline.ROBUSTNESS_NOT_ASSESSED,
          ev["deny"]["RECOMMENDATION"]["reason_code"])
    check("O12 неизвестная цель повышения — ошибка, а не молчаливое «да»",
          _raises(lambda: P.promotion_allowed(*FREE, "content_dna")))


def _raises(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


if __name__ == "__main__":
    for fn in (test_A_mechanical_pair_blocked, test_B_candidate_not_auto_promoted,
               test_C_not_in_content_dna, test_D_not_experiment_basis,
               test_E_causal_validator_at_zero_hypotheses,
               test_F_new_run_supersedes_report, test_G_rebuild_repeatability,
               test_order):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
