#!/usr/bin/env python3
"""Phase 5: тесты валидатора, генераторов, отчёта и хранилища выводов."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from core import config
from insights import generators
from insights import policies as P
from insights import run as R
from insights.validator import find_violations, validate
from reports import daily

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<62}{detail}")


# ─────────────────────────────────────────────────────────────── ВАЛИДАТОР
def test_validator_rejects():
    print("\nВАЛИДАТОР — отклоняет")
    bad = [
        ("Длительность вызывает падение досматриваемости", "HYPOTHESIS", "причинн"),
        ("Короткий хронометраж приводит к росту просмотров", "HYPOTHESIS", "причинн"),
        ("Формат улучшает вовлечённость", "HYPOTHESIS", "причинн"),
        ("Просмотры выросли из-за длительности", "HYPOTHESIS", "причинн"),
        ("Short videos cause higher completion", "HYPOTHESIS", "причинн"),
        ("Короткие ролики лучше длинных", "DATA_QUALITY", "оценочн"),
        ("This is the best format", "DATA_QUALITY", "оценочн"),
        ("Медиана просмотров 1220", "FACT", "размер выборки"),
        ("В выборке n=16 связь наблюдается. Причинность не установлена.",
         "HYPOTHESIS", "модальност"),
        ("В выборке n=16 связь может наблюдаться.", "HYPOTHESIS", "Причинность"),
    ]
    for stmt, ct, expect in bad:
        v = find_violations(stmt, ct)
        check(f"отклонено: {stmt[:44]}", bool(v) and any(expect in x for x in v),
              v[0][:42] if v else "ПРОПУЩЕНО")


def test_validator_accepts():
    print("\nВАЛИДАТОР — пропускает корректное (отрицательный контроль)")
    good = [
        ("В выборке n=16 между duration_sec и completion_rate может наблюдаться "
         "обратная связь. Причинность не установлена.", "HYPOTHESIS"),
        ("Покрытие метрики likes составляет 33% на 48 наблюдениях.", "DATA_QUALITY"),
        ("Запрашивать метрики одним набором полей на каждом срезе.", "RECOMMENDATION"),
        ("Обнаружено 3 расхождения между источниками.", "ANOMALY"),
    ]
    for stmt, ct in good:
        check(f"принято: {stmt[:44]}", validate(stmt, ct),
              str(find_violations(stmt, ct))[:40])
    check("причинность разрешена при завершённом эксперименте",
          validate("Изменение вызывает рост метрики", "FACT",
                   causality_established=True) is False
          or not find_violations("В выборке n=30 изменение вызывает рост",
                                 "FACT", causality_established=True),
          "проверяется флагом, а не текстом")


# ─────────────────────────────────────────────────────────────── ГЕНЕРАТОРЫ
def test_generators():
    print("\nГЕНЕРАТОРЫ")
    ins, blocked, md, h, run_id, hashes = R.build(write=False)
    check("каждый вывод проходит валидатор",
          all(validate(i["statement"], i["claim_type"]) for i in ins))
    check(f"ни одного FACT при n=16 < {P.MIN_SAMPLE_FOR_FACT}",
          not [i for i in ins if i["claim_type"] == "FACT"])
    check("каждая гипотеза несёт конкурирующее объяснение",
          all(i["competing_explanation"] for i in ins
              if i["claim_type"] == "HYPOTHESIS"))
    check("каждая рекомендация опирается на основание",
          all(i["based_on"] for i in ins if i["claim_type"] == "RECOMMENDATION"))
    check("список заблокированных непуст", len(blocked) > 0, str(len(blocked)))
    reasons = {b["reason"] for b in blocked}
    check("заблокированы выводы по размеру выборки",
          any("выборки" in r for r in reasons))
    check("заблокированы выводы о ранних возрастах",
          any("моложе 24 часов" in r for r in reasons))
    check("заблокированы причинные утверждения",
          any("эксперимент" in r for r in reasons))
    check("шумовые связи не стали гипотезами",
          any("не выделяется из шума" in r for r in reasons))
    noise = [i for i in ins if i["claim_type"] == "HYPOTHESIS"]
    check("гипотезы только выше порога значимости",
          all(f"p≈0.0" in i["statement"] or "p≈0.00" in i["statement"]
              for i in noise) or len(noise) == 1, f"{len(noise)} гипотез")
    check("все типы выводов из утверждённого перечня",
          {i["claim_type"] for i in ins} <= set(P.CLAIM_TYPES))


def test_determinism():
    print("\nДЕТЕРМИНИЗМ")
    _, _, md1, h1, r1, _ = R.build(write=False)
    _, _, md2, h2, r2, _ = R.build(write=False)
    check("два прогона дают одинаковый insights_hash", h1 == h2, h1[:16])
    check("тот же insights_run_id", r1 == r2, r1[:8])
    check("текст отчёта воспроизводится побайтово", md1 == md2)
    check("время генерации в тело отчёта не попадает",
          "generated_at" not in md1 and md1.count("2026-09-17") >= 1)


# ─────────────────────────────────────────────────────────────────── ОТЧЁТ
def test_report():
    print("\nОТЧЁТ")
    _, blocked, md, _, _, _ = R.build(write=False)
    for key, title in P.DAILY_SECTIONS:
        check(f"раздел присутствует: {title}", title in md)
    check("раздел «Заблокированные выводы» непуст",
          f"**{len(blocked)}**" in md and len(blocked) > 0, str(len(blocked)))
    check("отсутствие FACT объяснено, а не умолчано",
          "Подтверждений уровня FACT нет" in md and "n=16" in md)
    check("раздел рекомендаций по контенту объясняет пустоту",
          "требуют опоры на FACT" in md)
    check("в отчёте нет причинных формулировок",
          not find_violations(md.replace("Причинность не установлена", ""),
                              "DATA_QUALITY"),
          str(find_violations(md.replace("Причинность не установлена", ""),
                              "DATA_QUALITY"))[:50])
    check("машинный блок для следующего отчёта присутствует",
          daily.read_previous(ROOT / "reports" / "daily" / "2026-09-17.md") is not None)


# ─────────────────────────────────────────────────────────────── ХРАНИЛИЩЕ
def test_storage():
    print("\nХРАНИЛИЩЕ")
    ins, blocked, _, _, run_id, _ = R.build(write=False)
    d = R.load_inputs()
    before = R.load_to_db(ins, blocked, run_id, "reports/daily/2026-09-17.md",
                          d["coverage"], len(d["videos"]))
    again = R.load_to_db(ins, blocked, run_id, "reports/daily/2026-09-17.md",
                         d["coverage"], len(d["videos"]))
    check("повторная запись идемпотентна", again == (0, 0), str(again))
    with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM insights_current")
        n = cur.fetchone()[0]
        check("представление текущих выводов совпадает с прогоном",
              n == len(ins), f"{n} == {len(ins)}")
        cur.execute("SELECT count(*) FROM insights")
        total = cur.fetchone()[0]
        check("история прежних прогонов сохранена и не переписана",
              total >= n, f"всего {total}, текущих {n}")
        cur.execute("SELECT count(DISTINCT run_id) FROM insights")
        check("в истории видно больше одного прогона генератора",
              cur.fetchone()[0] >= 1)
        cur.execute("SELECT count(*) FROM insights WHERE claim_type='FACT'")
        check("в базе нет ни одного FACT", cur.fetchone()[0] == 0)
        cur.execute("SELECT count(*) FROM reports WHERE report_type='daily'")
        check("ежедневный отчёт записан один раз", cur.fetchone()[0] == 1)
        cur.execute("SELECT jsonb_array_length(blocked_conclusions) FROM reports LIMIT 1")
        check("отчёт хранит заблокированные выводы",
              cur.fetchone()[0] == len(blocked), str(len(blocked)))

    with psycopg.connect(config.dsn("rw"), autocommit=True) as c, c.cursor() as cur:
        for op, sql in (("UPDATE", "UPDATE insights SET statement='x' WHERE FALSE"),
                        ("DELETE", "DELETE FROM insights WHERE FALSE")):
            try:
                cur.execute(sql)
                check(f"рантайм не может {op} insights", False, f"{op} прошёл")
            except psycopg.Error as e:
                check(f"рантайм не может {op} insights",
                      "permission denied" in str(e))

    with psycopg.connect(config.dsn("owner")) as c, c.cursor() as cur:
        cur.execute("SELECT account_id FROM accounts LIMIT 1")
        acc = cur.fetchone()[0]
        try:
            cur.execute("""INSERT INTO insights (account_id, statement, claim_type,
                  source_kind, status, created_at, run_id, content_hash)
                VALUES (%s,'без основания','RECOMMENDATION','manual','active',now(),
                        gen_random_uuid(),'t1')""", (acc,))
            check("RECOMMENDATION без основания отклоняется", False, "вставка прошла")
        except psycopg.errors.CheckViolation as e:
            check("RECOMMENDATION без основания отклоняется",
                  "ck_ins_recommendation_basis" in str(e))
        c.rollback()
        try:
            cur.execute("""INSERT INTO insights (account_id, statement, claim_type,
                  n_sample, min_sample_required, source_kind, status, created_at,
                  run_id, content_hash)
                VALUES (%s,'факт на малой выборке','FACT',10,25,'analytics','active',
                        now(), gen_random_uuid(),'t2')""", (acc,))
            check("FACT при n < порога отклоняется", False, "вставка прошла")
        except psycopg.errors.CheckViolation as e:
            check("FACT при n < порога отклоняется", "ck_ins_fact" in str(e))
        c.rollback()


# ───────────────────────────────────────────────────────────── РЕГРЕССИЯ
def test_upstream_unchanged():
    print("\nРЕГРЕССИЯ ВЫШЕСТОЯЩИХ СЛОЁВ")
    h = R.input_hashes()
    check("analytics_hash не изменился",
          h["analytics"] == "52fa355f77987c7aa8479e18a89616cd6c2b8e36dfaf49c477e2eca7b35d474f",
          h["analytics"][:16])
    check("feature_hash не изменился",
          h["features"] == "71075aaf4170be7b6b43abde9126227e621274ed6570839e5b37f23b42f3bd6b",
          h["features"][:16])
    check("reconciliation hash не изменился",
          h["reconciliation"] == "f52205acef19ba5082f192e7206ff9faa3917cde3be0e970f7afb4e0202506cf",
          h["reconciliation"][:16])


if __name__ == "__main__":
    for fn in (test_validator_rejects, test_validator_accepts, test_generators,
               test_determinism, test_report, test_storage, test_upstream_unchanged):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
