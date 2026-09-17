#!/usr/bin/env python3
"""Phase 2: тесты движка сверки.

Все значения здесь — СИНТЕТИЧЕСКИЕ фикстуры. Они лежат вне data/ и
никогда не смешиваются с наблюдениями.
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from reconcile import policies as P
from reconcile.engine import _prove_lag, classify_slice, reconcile

RESULTS = []
T1 = "2026-09-17T09:00:00+00:00"
T2 = "2026-09-17T10:00:00+00:00"
T3 = "2026-09-17T11:00:00+00:00"


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<56}{detail}")


def ob(source, metric, value, at=T2, precision="second", vid="V1"):
    return {"observation_id": f"{source[:2]}:{metric}:{at}", "video_id": vid,
            "source": source, "metric": metric, "value": value, "observed_at": at,
            "observed_at_precision": precision, "observed_at_authority": "client_fetch_time",
            "published_at": "2026-05-01T00:00:00+00:00",
            "raw_ref": "synthetic", "run_id": "00000000-0000-0000-0000-000000000000"}


def classify(metric, obs, at=T2):
    rows = [o for o in obs if o["observed_at"] == at] if at != "ALL" else obs
    return classify_slice(metric, at, rows, obs)


def one(obs, metric):
    res, _ = reconcile(obs)
    return [r for r in res if r["metric"] == metric]


# ─────────────────────────────────────────────────────────────── MATCH
def test_match():
    print("\nMATCH")
    obs = [ob("supermetrics", "views", 100), ob("metricool", "views", 100)]
    check("точное совпадение целых -> both_matched",
          classify("views", obs)["classification"] == "both_matched")

    obs = [ob("supermetrics", "views", 100), ob("metricool", "views", 101)]
    r = classify("views", obs)
    check("целые 100 vs 101 не совпадают (допуск к счётчикам не применяется)",
          r["classification"] != "both_matched", r["classification"])

    eq, rule = P.values_equal("completion_rate", 0.3608, 0.36080000001)
    check("float в пределах допуска -> равенство", eq, rule)
    eq, _ = P.values_equal("completion_rate", 0.3608, 0.3620)
    check("float за пределами допуска -> неравенство", not eq)
    eq, rule = P.values_equal("views", 100, 100)
    check("правило для счётчиков именно целочисленное",
          rule == "equality.integer.exact.v1", rule)


# ─────────────────────────────────────────────────────────── TRANSFORM
def test_transform():
    print("\nTRANSFORM")
    obs = [ob("supermetrics", "duration_sec", 10.2, at="ALL"),
           ob("metricool", "duration_sec", 10.0, at="ALL")]
    r = classify("duration_sec", obs, at="ALL")
    check("floor(10.2)==10 -> both_expected_transform",
          r["classification"] == "both_expected_transform", r["rule_id"])
    check("преобразование назначает канонический источник",
          r["canonical_source"] == "supermetrics")

    obs = [ob("supermetrics", "duration_sec", 10.2, at="ALL"),
           ob("metricool", "duration_sec", 11.0, at="ALL")]
    r = classify("duration_sec", obs, at="ALL")
    check("floor(10.2)!=11 -> НЕ transform",
          r["classification"] != "both_expected_transform", r["classification"])
    check("несостоявшееся преобразование даёт both_discrepancy, а не подгонку",
          r["classification"] == "both_discrepancy")

    obs = [ob("supermetrics", "duration_sec", 10.2, at="ALL"),
           ob("metricool", "duration_sec", 10.0, at="ALL")]
    before = copy.deepcopy(obs)
    reconcile(obs)
    check("сверка не меняет исходные наблюдения", obs == before)


# ───────────────────────────────────────────────────────────────── LAG
def test_lag():
    print("\nLAG")
    rise = [ob("supermetrics", "views", 100, at=T1),
            ob("supermetrics", "views", 110, at=T2)]

    obs = rise + [ob("metricool", "views", 100, at=T1),
                  ob("metricool", "views", 100, at=T2)]
    r = classify("views", obs)
    check("канон растёт, второй остался на прежнем -> both_lagged",
          r["classification"] == "both_lagged", r.get("rule_id", ""))
    check("канонический и отстающий источники определены",
          r.get("canonical_source") == "supermetrics"
          and r.get("lagging_source") == "metricool")
    check("сохранено конкурирующее объяснение", bool(r.get("competing_explanation")))

    obs = rise + [ob("metricool", "views", 100, at=T1),
                  ob("metricool", "views", 110, at=T2)]
    check("второй догнал -> both_matched, отставания больше нет",
          classify("views", obs)["classification"] == "both_matched")

    obs = rise + [ob("metricool", "views", 100, at=T1),
                  ob("metricool", "views", 115, at=T2)]
    r = classify("views", obs)
    check("второй опережает канон -> НЕ отставание",
          r["classification"] == "both_discrepancy", r["classification"])

    # неоднозначность: доказательство проходит в обе стороны
    obs = [ob("supermetrics", "views", 100, at=T1), ob("supermetrics", "views", 110, at=T2),
           ob("metricool", "views", 110, at=T1), ob("metricool", "views", 100, at=T2)]
    r = classify("views", obs)
    check("двусторонняя неоднозначность -> both_discrepancy, а не lag",
          r["classification"] in ("both_discrepancy", "both_lagged")
          and r["classification"] == "both_discrepancy", r["classification"])

    obs = [ob("supermetrics", "views", 110, at=T2), ob("metricool", "views", 100, at=T2)]
    r = classify("views", obs)
    check("одно наблюдение канона -> НЕ отставание",
          r["classification"] == "both_discrepancy", r["classification"])

    pr = _prove_lag("reach", [], "supermetrics", "metricool",
                    ob("supermetrics", "reach", 110), ob("metricool", "reach", 100))
    check("метрика вне белого списка не получает отставание", pr is None)
    check("reach/favorites/total_time_watched НЕ в белом списке",
          not (P.MONOTONIC_COUNTER_WHITELIST & P.MONOTONIC_CANDIDATES_NOT_ENABLED))

    # преобразование проверяется раньше отставания
    obs = [ob("supermetrics", "duration_sec", 10.2, at="ALL"),
           ob("metricool", "duration_sec", 10.0, at="ALL")]
    check("transform имеет приоритет над формой отставания",
          classify("duration_sec", obs, at="ALL")["classification"]
          == "both_expected_transform")


# ──────────────────────────────────────────────────────────────── TIME
def test_time():
    print("\nTIME")
    obs = [ob("supermetrics", "views", 100), ob("metricool", "views", 100)]
    check("точные совпадающие метки -> сравнение выполняется",
          classify("views", obs)["classification"] == "both_matched")

    obs = [ob("supermetrics", "views", 100, precision="date"),
           ob("metricool", "views", 100, precision="date")]
    r = classify("views", obs)
    check("недостаточная точность метки -> untested_overlap, а не догадка",
          r["classification"] == "untested_overlap", r["rule_id"])

    obs = [ob("supermetrics", "views", 100, at=T2), ob("metricool", "views", 100, at=T3)]
    res = one(obs, "views")
    check("разнесённые во времени наблюдения не образуют ложное совпадение",
          all(x["classification"] == "untested_overlap" for x in res),
          f"{len(res)} среза")

    from normalize import observations
    real = observations.load()
    refs = {o["raw_ref"].split("#")[0] for o in real}
    check("повтор кэша не попал в наблюдения",
          not any("REPLAY" in r for r in refs), f"{len(refs)} файлов")


# ────────────────────────────────────────────────────────────── SOURCE
def test_source():
    print("\nSOURCE")
    obs = [ob("supermetrics", "reach", 500)]
    r = classify("reach", obs)
    check("метрика только у одного источника -> single_source",
          r["classification"] == "single_source", r["rule_id"])
    check("single_source не является расхождением",
          P.fact_allowed(r["classification"]))

    obs = [ob("metricool", "src_foryou", None)]
    r = classify("src_foryou", obs)
    check("значение недоступно -> unavailable",
          r["classification"] == "unavailable")
    check("NULL остаётся NULL и не превращается в ноль",
          r["value_a"] is None and r["value_b"] is None)

    obs = [ob("supermetrics", "views", 100), ob("metricool", "views", None)]
    r = classify("views", obs)
    check("метрика есть в обоих, но одно значение NULL -> untested_overlap",
          r["classification"] == "untested_overlap", r["rule_id"])

    for st in P.FACT_ALLOWED:
        assert P.fact_allowed(st)
    for st in P.FACT_BLOCKED:
        assert not P.fact_allowed(st)
    check("политика FACT: разрешено 4 статуса, запрещено 3",
          len(P.FACT_ALLOWED) == 4 and len(P.FACT_BLOCKED) == 3)


# ───────────────────────────────────────────────── REPRODUCIBILITY
def test_reproducibility():
    print("\nREPRODUCIBILITY")
    from normalize import observations
    obs = observations.load()
    r1, h1 = reconcile(obs)
    r2, h2 = reconcile(list(reversed(obs)))     # порядок входа не должен влиять
    check("два прогона дают одинаковый content_hash", h1 == h2, h1[:16])
    check("количество классификаций совпадает", len(r1) == len(r2), str(len(r1)))

    from reconcile.run import build, row_hash
    _, hb1, run1 = build(write=False)
    _, hb2, run2 = build(write=False)
    check("run.build детерминирован", hb1 == hb2 and run1 == run2, run1[:8])
    check("reconciliation_run_id не входит в построчный хеш",
          "run" not in json.dumps(list(__import__("reconcile.run", fromlist=["x"])
                                       .CONTENT_KEYS)))
    sample = dict(r1[0])
    a = row_hash(sample)
    sample["reconciliation_run_id"] = "иной-запуск"
    check("подмена run_id не меняет построчный хеш", row_hash(sample) == a)


if __name__ == "__main__":
    for fn in (test_match, test_transform, test_lag, test_time, test_source,
               test_reproducibility):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
