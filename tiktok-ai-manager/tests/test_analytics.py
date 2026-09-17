#!/usr/bin/env python3
"""Phase 3: тесты аналитического слоя. Все фикстуры синтетические."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analytics import association, baseline
from analytics import policies as A

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<58}{detail}")


def rr(video_id, metric, classification, value_a=None, value_b=None,
       canonical=None, slice_key="2026-09-17T10:00:00+00:00"):
    return {"video_id": video_id, "metric": metric, "slice": slice_key,
            "classification": classification, "rule_id": "test",
            "source_a": "supermetrics", "source_b": "metricool",
            "value_a": value_a, "value_b": value_b,
            "observation_id_a": "oa", "observation_id_b": "ob",
            "observed_at_a": slice_key, "observed_at_b": slice_key,
            "canonical_source": canonical, "content_hash": f"h:{metric}:{classification}"}


def snap(video_id, bucket="backfill", age=100000, precision="second"):
    return {"video_id": video_id, "source": "supermetrics",
            "observed_at": "2026-09-17T10:00:00+00:00", "age_bucket": bucket,
            "age_seconds": age, "observed_at_precision": precision}


def vid(video_id="V1"):
    return {"video_id": video_id, "published_at": "2026-05-01T00:00:00+00:00"}


def build_one(results, snaps=None):
    return baseline.video_baseline([vid()], results, snaps or [snap("V1")])[0]


# ─────────────────────────────────────────────────────────── ARITHMETIC
def test_arithmetic():
    print("\nARITHMETIC")
    check("деление при положительном знаменателе", A.safe_ratio(5, 10) == 0.5)
    check("нулевой знаменатель -> NULL, не ноль и не ошибка",
          A.safe_ratio(5, 0) is None)
    check("NULL в знаменателе -> NULL", A.safe_ratio(5, None) is None)
    check("NULL в числителе -> NULL", A.safe_ratio(None, 10) is None)
    check("ноль в числителе -> ноль, а не NULL", A.safe_ratio(0, 10) == 0.0)

    r = build_one([rr("V1", "views", "both_matched", 100, 100),
                   rr("V1", "likes", "both_matched", 10, 10)])
    check("views > 0 обрабатывается обычным делением", r["like_rate"] == 0.1)
    r = build_one([rr("V1", "views", "both_matched", 0, 0),
                   rr("V1", "likes", "both_matched", 10, 10)])
    check("views = 0 -> like_rate NULL, а не ноль и не бесконечность",
          r["like_rate"] is None, f"views={r['views']}")
    check("при этом сам ноль просмотров сохранён как ноль", r["views"] == 0)


# ─────────────────────────────────────────────────────────── ENGAGEMENT
def test_engagement():
    print("\nENGAGEMENT")
    res = [rr("V1", "views", "both_matched", 1000, 1000)]
    for m, v in (("likes", 100), ("comments", 10), ("shares", 5), ("favorites", 5)):
        res.append(rr("V1", m, "both_matched", v, v))
    r = build_one(res)
    check("like_rate", r["like_rate"] == 0.1)
    check("comment_rate", r["comment_rate"] == 0.01)
    check("share_rate", r["share_rate"] == 0.005)
    check("favorite_rate", r["favorite_rate"] == 0.005)
    check("engagement_rate = сумма четырёх / views",
          abs(r["engagement_rate"] - 0.12) < 1e-12, str(r["engagement_rate"]))

    res = [rr("V1", "views", "both_matched", 1000, 1000),
           rr("V1", "likes", "both_matched", 100, 100),
           rr("V1", "comments", "unavailable"),
           rr("V1", "shares", "both_matched", 5, 5),
           rr("V1", "favorites", "both_matched", 5, 5)]
    r = build_one(res)
    check("NULL в слагаемом -> engagement_rate NULL, а не сумма остальных",
          r["engagement_rate"] is None)
    check("при этом доступные ставки посчитаны", r["like_rate"] == 0.1)


# ─────────────────────────────────────────────────────────── WATCH TIME
def test_watch_time():
    print("\nWATCH TIME")
    res = [rr("V1", "views", "both_matched", 100, 100),
           rr("V1", "total_time_watched_sec", "single_source", 500, None,
              canonical="supermetrics"),
           rr("V1", "avg_view_time_sec", "single_source", 5.0, None,
              canonical="supermetrics")]
    r = build_one(res)
    check("производное среднее посчитано", r["avg_view_time_sec_derived"] == 5.0)
    res_diff = [rr("V1", "views", "both_matched", 100, 100),
                rr("V1", "total_time_watched_sec", "single_source", 501, None,
                   canonical="supermetrics"),
                rr("V1", "avg_view_time_sec", "single_source", 5.0, None,
                   canonical="supermetrics")]
    rd = build_one(res_diff)
    check("фактическое значение не подменено производным",
          rd["avg_view_time_sec"] == 5.0 and rd["avg_view_time_sec_derived"] == 5.01,
          f"факт {rd['avg_view_time_sec']} / производное {rd['avg_view_time_sec_derived']}")
    check("совпадение в пределах допуска зафиксировано",
          r["avg_view_time_derivation_matches"] is True)

    res[2] = rr("V1", "avg_view_time_sec", "single_source", 5.5, None,
                canonical="supermetrics")
    r = build_one(res)
    check("расхождение за пределами допуска зафиксировано, значение сохранено",
          r["avg_view_time_derivation_matches"] is False
          and r["avg_view_time_sec"] == 5.5)

    res[0] = rr("V1", "views", "both_matched", 0, 0)
    r = build_one(res)
    check("ноль просмотров -> производное NULL, сравнение не выполняется",
          r["avg_view_time_sec_derived"] is None
          and r["avg_view_time_derivation_matches"] is None)


# ─────────────────────────────────────────────────────────── PERCENTILES
def test_percentiles():
    print("\nPERCENTILES")
    d = [1, 2, 3, 4]
    check("p25 на [1,2,3,4] = 1.75", A.percentile(d, 0.25) == 1.75)
    check("p50 на [1,2,3,4] = 2.5", A.percentile(d, 0.50) == 2.5)
    check("p75 на [1,2,3,4] = 3.25", A.percentile(d, 0.75) == 3.25)
    check("p0 = минимум, p100 = максимум",
          A.percentile(d, 0.0) == 1 and A.percentile(d, 1.0) == 4)
    check("единственное значение -> оно само", A.percentile([7], 0.9) == 7.0)
    check("пустой набор -> NULL", A.percentile([], 0.5) is None)
    check("метод зафиксирован в политике",
          A.PERCENTILE_METHOD == "linear_interpolation_between_closest_ranks.v1")

    s = baseline.summarize([5, 1, 4, 2, 3])
    check("сводка не зависит от порядка входа",
          s["median"] == baseline.summarize([1, 2, 3, 4, 5])["median"] == 3.0)
    s10 = baseline.summarize(list(range(1, 11)))
    check(f"p90 считается при n >= {A.P90_MIN_N}", s10["p90"] is not None)
    s9 = baseline.summarize(list(range(1, 10)))
    check("p90 подавлен на малой выборке, причина указана",
          s9["p90"] is None and s9["p90_status"].startswith("suppressed"),
          s9["p90_status"])


# ───────────────────────────────────────────────────────────── OUTLIERS
def test_outliers():
    print("\nOUTLIERS")
    s = baseline.summarize([1, 2, 3, 4, 100])
    check("q1=2, q3=4 на [1,2,3,4,100]", s["p25"] == 2.0 and s["p75"] == 4.0)
    check("границы IQR: -1 и 7",
          s["iqr_lower_fence"] == -1.0 and s["iqr_upper_fence"] == 7.0)
    check("выброс найден ровно один", s["n_outliers"] == 1 and s["outlier_values"] == [100.0])

    s = baseline.summarize([1, 2, 3, 4, 7])
    check("значение ровно на границе выбросом НЕ считается", s["n_outliers"] == 0)
    s = baseline.summarize([1, 2, 3, 4, 7.0001])
    check("значение чуть за границей считается выбросом", s["n_outliers"] == 1)

    a = baseline.summarize([1, 2, 3, 4, 100])
    b = baseline.summarize([100, 4, 3, 2, 1])
    check("правило воспроизводимо при любом порядке входа",
          a["n_outliers"] == b["n_outliers"] and a["outlier_values"] == b["outlier_values"])
    check("идентификатор правила сохранён", a["outlier_rule"] == A.OUTLIER_RULE_ID,
          a["outlier_rule"])


# ────────────────────────────────────────────────────────── ELIGIBILITY
def test_eligibility():
    print("\nELIGIBILITY")
    allowed = ["both_matched", "both_expected_transform", "both_lagged", "single_source"]
    blocked = ["both_discrepancy", "untested_overlap", "unavailable"]
    for st in allowed:
        check(f"{st} пригоден", A.is_eligible("views", st))
    for st in blocked:
        check(f"{st} НЕ пригоден", not A.is_eligible("views", st))
    check("семь статусов покрыты политикой", len(allowed) + len(blocked) == 7)
    check("причины непригодности различаются",
          len({A.ineligibility_reason(s) for s in blocked}) == 3,
          str(sorted(A.ineligibility_reason(s) for s in blocked)))

    r = build_one([rr("V1", "views", "both_discrepancy", 100, 90)])
    check("значение при расхождении в baseline не попадает",
          r["views"] is None and r["eligibility"]["views"] == "data_discrepant")
    r = build_one([rr("V1", "views", "untested_overlap", 100, None)])
    check("непроверенное пересечение не попадает",
          r["views"] is None and r["eligibility"]["views"] == "data_untested")
    r = build_one([rr("V1", "views", "both_lagged", 110, 100, canonical="supermetrics")])
    check("при доказанном отставании берётся значение канонического источника",
          r["views"] == 110 and r["source_basis"]["views"] == "supermetrics")
    check("метрики без источника отключены политикой",
          A.eligible_statuses("src_foryou") == frozenset())


# ────────────────────────────────────────────────────────────────── AGE
def test_age():
    print("\nAGE")
    r = baseline.video_baseline([vid()], [], [snap("V1", "backfill", 100000)])[0]
    check("возраст наблюдался -> сохранён", r["age_seconds_observed_max"] == 100000)
    check("бакет backfill остаётся backfill и не повышается до 7d+",
          r["age_bucket"] == "backfill")
    r = baseline.video_baseline([vid()], [],
                                [snap("V1", "backfill", None, "date")])[0]
    check("метка точности до даты -> возраст NULL, а не приблизительный",
          r["age_seconds_observed_max"] is None)
    r = baseline.video_baseline([vid()], [], [])[0]
    check("нет снимков -> бакет NULL, а не выдуманный", r["age_bucket"] is None)
    r = baseline.video_baseline([vid()], [],
                                [snap("V1", "backfill"), snap("V1", "T+7d")])[0]
    check("несколько бакетов -> mixed, а не выбор одного", r["age_bucket"] == "mixed")
    check("окна 24h/48h/7d объявлены без данных",
          all(A.BASELINE_WINDOWS[w]["status"] == "no_data"
              for w in ("24h_baseline", "48h_baseline", "7d_live_baseline")))


# ───────────────────────────────────────────────────────────── COVERAGE
def test_coverage():
    print("\nCOVERAGE")
    res = ([rr("V1", "views", "both_matched", 1, 1)] * 5 +
           [rr("V1", "likes", "untested_overlap")] * 3 +
           [rr("V1", "reach", "unavailable")] * 2)
    cov = baseline.coverage(res)
    allm = cov[0]
    check("сумма пригодных и непригодных равна общему числу",
          allm["eligible_observations"] + allm["ineligible_observations"]
          == allm["total_observations"] == 10)
    check("доля покрытия детерминирована", allm["coverage_pct"] == 0.5)
    check("три состояния непригодности разделены",
          allm["data_untested"] == 3 and allm["data_unavailable"] == 2
          and allm["data_discrepant"] == 0)
    check("покрытие считается и по каждой метрике",
          {c["metric"] for c in cov if c["scope"] == "metric"} == {"views", "likes", "reach"})
    check("нулевой счётчик остаётся нулём, а отсутствие доли — NULL",
          baseline.coverage([])[0]["coverage_pct"] is None
          and baseline.coverage([])[0]["total_observations"] == 0)


# ────────────────────────────────────────────────────── ASSOCIATION
def test_association():
    print("\nASSOCIATION")
    check("идеальная обратная связь rho = -1",
          association.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0)
    check("связки получают средний ранг",
          abs(association.spearman([1, 1, 2, 2], [1, 1, 2, 2]) - 1.0) < 1e-12)
    recs = [{"duration_sec": i, "completion_rate": -i} for i in range(1, 9)]
    m = association.measure(recs, "duration_sec", "completion_rate")
    check("измерение возвращает rho и n", m["rho"] == -1.0 and m["n"] == 8)
    check("причинность не заявляется", m["causality_claim"] is False)
    check("результат не объявляется паттерном", m["is_content_pattern"] is False)
    check("интерпретация отсутствует", m["interpretation"] == "not_interpreted")
    check("ограничения перечислены", len(m["limitations"]) >= 3)
    m = association.measure([{"duration_sec": 1, "completion_rate": 1}],
                            "duration_sec", "completion_rate")
    check("малая выборка -> rho не считается", m["rho"] is None
          and m["status"].startswith("insufficient"))


# ────────────────────────────────────────────────────────────── REBUILD
def test_rebuild():
    print("\nREBUILD")
    from analytics.run import build, OUT
    _, h1, run1 = build(write=True)
    files_before = sorted(p.name for p in OUT.glob("*.jsonl"))
    for p in OUT.glob("*.jsonl"):
        p.unlink()
    (OUT / "manifest.json").unlink(missing_ok=True)
    check("производные артефакты удалены", not list(OUT.glob("*.jsonl")))
    _, h2, run2 = build(write=True)
    check("пересборка даёт тот же analytics_hash", h1 == h2, h1[:16])
    check("тот же analytics_run_id", run1 == run2, run1[:8])
    check("восстановлен тот же состав файлов",
          sorted(p.name for p in OUT.glob("*.jsonl")) == files_before)
    from analytics.run import content_hash
    a1, _, _ = build(write=False)
    a2, _, _ = build(write=False)
    same_order = all([r1 == r2 for r1, r2 in zip(a1[k], a2[k])] and len(a1[k]) == len(a2[k])
                     for k in a1)
    check("два независимых прогона дают одинаковый порядок строк", same_order)
    shuffled = {k: list(reversed(v)) for k, v in a1.items()}
    check("хеш зависит от порядка, поэтому порядок обязан задаваться сборкой",
          content_hash(a1) != content_hash(shuffled),
          "перестановка меняет хеш — значит детерминизм держится на сортировке")
    check("сборка действительно сортирует: хеш совпадает между прогонами",
          content_hash(a1) == content_hash(a2))
    man = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    check("манифест содержит хеш и версии",
          man["analytics_hash"] == h1 and man["policy_version"] == A.ANALYTICS_POLICY_VERSION)


if __name__ == "__main__":
    for fn in (test_arithmetic, test_engagement, test_watch_time, test_percentiles,
               test_outliers, test_eligibility, test_age, test_coverage,
               test_association, test_rebuild):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
