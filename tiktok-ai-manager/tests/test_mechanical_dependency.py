#!/usr/bin/env python3
"""Phase 5.1: механически зависимые пары метрик.

Связь, вытекающая из устройства метрик, не становится содержательной
оттого, что она статистически сильна.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from insights import generators
from insights import policies as P
from insights import run as R

RESULTS = []
MECH_PAIR = ("duration_sec", "completion_rate")


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<62}{detail}")


def assoc(x, y, rho, p, n=16):
    return {"x_metric": x, "y_metric": y, "rho": rho, "p_two_sided": p, "n": n,
            "total_n": n, "status": "measured", "sample_status": "insufficient_sample",
            "coverage_ratio": 1.0, "method": "spearman_rho.v1",
            "significance_method": "student_t_approximation.v1",
            "min_sample_required": 25, "interpretation": "not_interpreted",
            "causality_claim": False, "is_content_pattern": False,
            "limitations": ["тест"], "engine_version": "t", "policy_version": "t"}


# ─────────────────────────────────────────────────────────────────── 1
def test_1_recognised():
    print("\n1 — пара распознаётся как механически зависимая")
    dep = P.mechanical_dependency(*MECH_PAIR)
    check("duration_sec + completion_rate в реестре", dep is not None)
    check("реестр работает в обратном порядке аргументов",
          P.mechanical_dependency(MECH_PAIR[1], MECH_PAIR[0]) is not None)
    allowed, code, _ = P.association_claim_allowed(*MECH_PAIR)
    check("содержательный вывод запрещён", allowed is False)
    check("машинный код причины", code == "mechanically_dependent", code)
    check("механизм описан", len(dep["mechanism"]) > 80)
    check("указано доказательство из данных", "EXP-004" in dep["evidence"])
    check("реестр НЕ утверждает ложного тождества",
          "!=" in dep["not_an_identity"] and "0" in dep["not_an_identity"],
          dep["not_an_identity"][:52])
    check("зарегистрирована ровно одна пара", len(P.MECHANICAL_DEPENDENCIES) == 1)
    check("непроверенные кандидаты отделены и не применяются",
          len(P.MECHANICAL_CANDIDATES_NOT_REGISTERED) >= 1
          and not (set(P.MECHANICAL_DEPENDENCIES)
                   & set(P.MECHANICAL_CANDIDATES_NOT_REGISTERED)))


# ───────────────────────────────────────────────────────────────── 2-4
def test_2_4_no_claims():
    print("\n2-4 — пара не порождает HYPOTHESIS, RECOMMENDATION, FACT")
    ins, blocked = generators.from_associations([assoc(*MECH_PAIR, -0.717, 0.0018)])
    check("HYPOTHESIS не создан",
          not [i for i in ins if i["claim_type"] == "HYPOTHESIS"])
    check("RECOMMENDATION не создан",
          not [i for i in ins if i["claim_type"] == "RECOMMENDATION"])
    check("FACT не создан", not [i for i in ins if i["claim_type"] == "FACT"])
    check("не создано вообще ни одного вывода", ins == [], str(len(ins)))
    check("сильная статистика не пробивает запрет",
          not generators.from_associations([assoc(*MECH_PAIR, -0.99, 1e-9)])[0])


# ───────────────────────────────────────────────────────────────── 5-6
def test_5_6_blocked():
    print("\n5-6 — пара попадает в заблокированные с машинной причиной")
    _, blocked = generators.from_associations([assoc(*MECH_PAIR, -0.717, 0.0018)])
    check("ровно одна запись блокировки", len(blocked) == 1, str(len(blocked)))
    b = blocked[0]
    check("причина = mechanically_dependent",
          b["reason_code"] == "mechanically_dependent", b["reason_code"])
    check("обе метрики названы",
          all(m in b["conclusion"] for m in MECH_PAIR))
    check("текст объясняет математическую зависимость",
          "механически" in b["reason"] and "построению метрики" in b["reason"])
    check("текст содержит пометку о причинности",
          "Причинность не установлена" in b["reason"])
    check("статистика сохранена, а не удалена",
          b["detail"]["rho"] == -0.717 and b["detail"]["n"] == 16)
    check("перечислено, что именно запрещено",
          set(b["detail"]["forbidden"]) == {"FACT", "HYPOTHESIS", "RECOMMENDATION",
                                            "content_dna_evidence", "experiment_basis"})
    check("указан идентификатор правила",
          b["detail"]["rule_id"] == "mechdep.duration_completion.v1")


# ─────────────────────────────────────────────────────────────────── 7
def test_7_other_associations_work():
    print("\n7 — остальные связи продолжают работать")
    ins, blocked = generators.from_associations([assoc("shares", "views", -0.62, 0.01)])
    check("незарегистрированная пара со значимой связью даёт гипотезу",
          len([i for i in ins if i["claim_type"] == "HYPOTHESIS"]) == 1,
          str([i["claim_type"] for i in ins]))
    h = [i for i in ins if i["claim_type"] == "HYPOTHESIS"][0]
    check("гипотеза несёт конкурирующее объяснение", bool(h["competing_explanation"]))
    check("причинность по ней остаётся заблокированной",
          any(b["reason_code"] == "no_concluded_experiment" for b in blocked))

    ins2, blocked2 = generators.from_associations([assoc("shares", "views", 0.03, 0.91)])
    check("шумовая связь по-прежнему не даёт гипотезы", not ins2)
    check("шумовая причина отличается от механической",
          blocked2[0]["reason_code"] == "not_distinguishable_from_noise")

    mixed = [assoc(*MECH_PAIR, -0.717, 0.0018), assoc("shares", "views", -0.62, 0.01)]
    ins3, blocked3 = generators.from_associations(mixed)
    check("в смешанном наборе блокируется только механическая пара",
          len([i for i in ins3 if i["claim_type"] == "HYPOTHESIS"]) == 1
          and sum(1 for b in blocked3
                  if b["reason_code"] == "mechanically_dependent") == 1)


# ─────────────────────────────────────────────────────────────── 8-9
def test_8_9_determinism():
    print("\n8-9 — детерминизм и воспроизводимость insights_hash")
    _, _, md1, h1, r1, _ = R.build(write=False)
    _, _, md2, h2, r2, _ = R.build(write=False)
    check("два прогона дают одинаковый insights_hash", h1 == h2, h1[:16])
    check("тот же insights_run_id", r1 == r2, r1[:8])
    check("текст отчёта воспроизводится побайтово", md1 == md2)
    a, b = generators.from_associations([assoc(*MECH_PAIR, -0.717, 0.0018)])
    a2, b2 = generators.from_associations([assoc(*MECH_PAIR, -0.717, 0.0018)])
    check("блокировка воспроизводима", a == a2 and b == b2)


# ────────────────────────────────────────────────────────────────── 10
def test_10_upstream_unchanged():
    print("\n10 — артефакты Phase 3 и Phase 4 не изменились")
    h = R.input_hashes()
    expect = {
        "analytics": "52fa355f77987c7aa8479e18a89616cd6c2b8e36dfaf49c477e2eca7b35d474f",
        "features": "71075aaf4170be7b6b43abde9126227e621274ed6570839e5b37f23b42f3bd6b",
        "reconciliation": "f52205acef19ba5082f192e7206ff9faa3917cde3be0e970f7afb4e0202506cf",
    }
    for k, v in sorted(expect.items()):
        check(f"{k}_hash не изменился", h[k] == v, h[k][:16])
    assoc_rows = [json.loads(l) for l in
                  (ROOT / "data" / "analytics" / "association.jsonl")
                  .read_text(encoding="utf-8").splitlines() if l.strip()]
    pair = next(a for a in assoc_rows
                if {a["x_metric"], a["y_metric"]} == set(MECH_PAIR))
    check("исходная статистика Phase 3 сохранена, а не удалена",
          pair["rho"] is not None and pair["n"] == 16,
          f"rho={pair['rho']:.3f}")


# ─────────────────────────────────────────── отчёт и сквозной результат
def test_report_and_pipeline():
    print("\nОТЧЁТ И СКВОЗНОЙ РЕЗУЛЬТАТ")
    ins, blocked, md, _, _, _ = R.build(write=False)
    check("duration ~ completion_rate отсутствует в разделе гипотез",
          "## 4. Новые гипотезы" in md
          and "completion_rate" not in md.split("## 4. Новые гипотезы")[1]
          .split("## 5.")[0])
    sec8 = md.split("## 8. Заблокированные выводы")[1]
    check("пара присутствует в разделе заблокированных",
          "duration_sec" in sec8 and "completion_rate" in sec8)
    check("машинная причина видна в отчёте", "`mechanically_dependent`" in sec8)
    check("раздел гипотез объясняет, почему пуст",
          "Кандидатов рассмотрено и отклонено" in md)
    check("ни одного HYPOTHESIS в сквозном прогоне",
          not [i for i in ins if i["claim_type"] == "HYPOTHESIS"])
    check("механическая блокировка присутствует в сквозном прогоне",
          sum(1 for b in blocked if b["reason_code"] == "mechanically_dependent") == 1)
    check("у каждой блокировки есть машинная причина",
          all(b.get("reason_code") for b in blocked))


if __name__ == "__main__":
    for fn in (test_1_recognised, test_2_4_no_claims, test_5_6_blocked,
               test_7_other_associations_work, test_8_9_determinism,
               test_10_upstream_unchanged, test_report_and_pipeline):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
