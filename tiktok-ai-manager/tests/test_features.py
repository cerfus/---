#!/usr/bin/env python3
"""Phase 4: тесты слоя признаков. Фикстуры синтетические, вне data/."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analytics import policies as A
from features import engine
from features import policies as F

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<58}{detail}")


def rr(video_id, metric, classification, value_a=None, value_b=None, canonical=None):
    return {"video_id": video_id, "metric": metric,
            "slice": "2026-09-17T10:00:00+00:00", "classification": classification,
            "rule_id": "test", "source_a": "supermetrics", "source_b": "metricool",
            "value_a": value_a, "value_b": value_b,
            "observation_id_a": "oa", "observation_id_b": "ob",
            "observed_at_a": "2026-09-17T10:00:00+00:00",
            "observed_at_b": "2026-09-17T10:00:00+00:00",
            "canonical_source": canonical, "content_hash": f"h:{metric}"}


def snap(video_id="V1", precision="second", age=1000, bucket="backfill"):
    return {"video_id": video_id, "source": "supermetrics",
            "observed_at": "2026-09-17T10:00:00+00:00", "age_bucket": bucket,
            "age_seconds": age, "observed_at_precision": precision}


def feats(results, snaps=None, videos=None):
    vs = videos or [{"video_id": "V1", "published_at": "2026-05-01T00:00:00+00:00"}]
    rows = engine.build(vs, results, snaps if snaps is not None else [snap()])
    return {r["feature_name"]: r for r in rows if r["video_id"] == vs[0]["video_id"]}


def base_metrics(views=1000, likes=100, comments=10, shares=5, favorites=5,
                 duration=10.0, avt=6.0, cr=0.3, ttw=6000, vid="V1"):
    out = []
    for m, v in (("views", views), ("likes", likes), ("comments", comments),
                 ("shares", shares), ("favorites", favorites)):
        out.append(rr(vid, m, "both_matched", v, v))
    for m, v in (("duration_sec", duration), ("avg_view_time_sec", avt),
                 ("completion_rate", cr), ("total_time_watched_sec", ttw),
                 ("reach", 900)):
        out.append(rr(vid, m, "single_source", v, None, canonical="supermetrics"))
    return out


# ────────────────────────────────────────────────────────────── DURATION
def test_duration():
    print("\nDURATION")
    cases = [(7.999, "very_short"), (8.0, "short"), (14.999, "short"),
             (15.0, "medium"), (29.999, "medium"), (30.0, "long"),
             (59.999, "long"), (60.0, "very_long"), (80.5, "very_long")]
    for d, want in cases:
        got, _ = F.duration_bucket(d)
        check(f"граница {d} -> {want}", got == want, got or "—")
    check("NULL -> нет корзины с причиной",
          F.duration_bucket(None) == (None, "value_missing"))
    check("ноль невалиден",
          F.duration_bucket(0) == (None, "invalid_non_positive_duration"))
    check("отрицательная длительность невалидна",
          F.duration_bucket(-5) == (None, "invalid_non_positive_duration"))
    f = feats(base_metrics(duration=10.0))
    check("корзина попадает в признак с правилом",
          f["duration_bucket"]["feature_value"] == "short"
          and f["duration_bucket"]["bucket_rule"] == F.DURATION_BUCKET_RULE)
    check("корзина — Tier 0.5, длительность — Tier 0",
          f["duration_bucket"]["tier"] == F.TIER_05
          and f["duration_sec"]["tier"] == F.TIER_0)


# ───────────────────────────────────────────────────────────────── RATES
def test_rates():
    print("\nRATES")
    f = feats(base_metrics())
    check("like_rate = 100/1000", f["like_rate"]["feature_value"] == 0.1)
    check("comment_rate = 10/1000", f["comment_rate"]["feature_value"] == 0.01)
    check("engagement_rate = 120/1000",
          abs(f["engagement_rate"]["feature_value"] - 0.12) < 1e-12)
    f = feats(base_metrics(views=0))
    check("нулевой знаменатель -> NULL, причина указана",
          f["like_rate"]["feature_value"] is None
          and f["like_rate"]["feature_status"] == "unavailable",
          f["like_rate"]["status_reason"])
    check("сам ноль просмотров сохранён как ноль, а не как NULL",
          f["views"]["feature_value"] == 0)
    m = [r for r in base_metrics() if r["metric"] != "likes"]
    m.append(rr("V1", "likes", "unavailable"))
    f = feats(m)
    check("NULL в числителе -> ставка NULL", f["like_rate"]["feature_value"] is None)
    check("NULL в слагаемом -> engagement_rate NULL целиком",
          f["engagement_rate"]["feature_value"] is None)
    f = feats(base_metrics(views=3, likes=1))
    check("целочисленный числитель делится без потери точности",
          abs(f["like_rate"]["feature_value"] - 1 / 3) < 1e-15)


# ───────────────────────────────────────────────────────────────── WATCH
def test_watch():
    print("\nWATCH")
    f = feats(base_metrics(duration=10.0, avt=6.0))
    check("avg_view_time_ratio = 6/10", f["avg_view_time_ratio"]["feature_value"] == 0.6)
    check("признак не называется удержанием",
          "удержан" in f["avg_view_time_ratio"]["note"].lower()
          and "НЕ является" in f["avg_view_time_ratio"]["note"])
    f = feats(base_metrics(duration=0.0))
    check("нулевая длительность -> NULL, а не деление на ноль",
          f["avg_view_time_ratio"]["feature_value"] is None,
          f["avg_view_time_ratio"]["status_reason"])
    m = [r for r in base_metrics() if r["metric"] != "duration_sec"]
    m.append(rr("V1", "duration_sec", "unavailable"))
    f = feats(m)
    check("отсутствующая длительность -> NULL",
          f["avg_view_time_ratio"]["feature_value"] is None)
    check("completion_rate остаётся самостоятельной метрикой Tier 0",
          f["completion_rate"]["tier"] == F.TIER_0)


# ────────────────────────────────────────────────────────────── LOG VIEWS
def test_log_views():
    print("\nLOG VIEWS")
    f = feats(base_metrics(views=0))
    check("views=0 -> log1p=0", f["views_log1p"]["feature_value"] == 0.0)
    f = feats(base_metrics(views=1))
    check("views=1 -> log1p=ln2",
          abs(f["views_log1p"]["feature_value"] - math.log(2)) < 1e-12)
    f = feats(base_metrics(views=698889))
    check("большое значение считается",
          abs(f["views_log1p"]["feature_value"] - math.log1p(698889)) < 1e-9)
    check("исходное views хранится рядом и не заменяется",
          f["views"]["feature_value"] == 698889)
    m = [r for r in base_metrics() if r["metric"] != "views"]
    m.append(rr("V1", "views", "unavailable"))
    f = feats(m)
    check("views NULL -> log1p NULL", f["views_log1p"]["feature_value"] is None)


# ─────────────────────────────────────────────────────── BASELINE-RELATIVE
def test_baseline_relative():
    print("\nBASELINE-RELATIVE")
    check("валидный baseline пропускает",
          F.baseline_relative_eligible(30, False, True) == (True, None))
    ok, why = F.baseline_relative_eligible(16, False, True)
    check("малый baseline блокирует", not ok and why.startswith("insufficient"), why)
    ok, why = F.baseline_relative_eligible(30, False, False)
    check("несовпадающее окно блокирует", not ok and why == "window_mismatch")
    ok, why = F.baseline_relative_eligible(30, True, True)
    check("утечка через включение самого ролика блокирует",
          not ok and why == "self_inclusion_leakage")
    f = feats(base_metrics())
    for name in F.BASELINE_RELATIVE:
        check(f"{name} на реальной выборке -> NULL со статусом",
              f[name]["feature_value"] is None
              and f[name]["feature_status"] == "insufficient_baseline")


# ────────────────────────────────────────────────────────────── OUTLIERS
def test_outliers():
    print("\nOUTLIERS")
    vids = [{"video_id": f"V{i}", "published_at": "2026-05-01T00:00:00+00:00"}
            for i in range(1, 6)]
    res, snaps = [], []
    for v, views in zip(vids, [1, 2, 3, 4, 100]):
        res += base_metrics(views=views * 100, vid=v["video_id"])
        snaps.append(snap(v["video_id"]))
    rows = engine.build(vids, res, snaps)
    o = {r["video_id"]: r for r in rows if r["feature_name"] == "views_outlier"}
    check("выброс найден ровно один", sum(1 for r in o.values() if r["feature_value"]) == 1)
    check("выброс — крайнее значение", o["V5"]["feature_value"] is True)
    check("правило и версия политики сохранены",
          o["V5"]["outlier_rule"] == A.OUTLIER_RULE_ID
          and o["V5"]["analytics_policy_version"] == A.ANALYTICS_POLICY_VERSION)
    check("границы сохранены как доказательство",
          o["V5"]["lower_fence"] is not None and o["V5"]["upper_fence"] is not None)
    check("значение ровно на границе выбросом не считается",
          o["V4"]["feature_value"] is False,
          f"400 при верхней границе {o['V4']['upper_fence']}")
    m = [r for r in base_metrics() if r["metric"] != "completion_rate"]
    m.append(rr("V1", "completion_rate", "unavailable"))
    f = feats(m)
    check("отсутствующая метрика -> выброс NULL, а не False",
          f["completion_outlier"]["feature_value"] is None
          and f["completion_outlier"]["status_reason"] == "metric_missing")


# ──────────────────────────────────────────────────────── RECONCILIATION
def test_reconciliation_statuses():
    print("\nRECONCILIATION")
    for st in ("both_matched", "both_expected_transform", "both_lagged", "single_source"):
        m = [r for r in base_metrics() if r["metric"] != "views"]
        m.append(rr("V1", "views", st, 500, 500, canonical="supermetrics"))
        f = feats(m)
        check(f"{st}: значение попадает в признак",
              f["views"]["feature_value"] == 500
              and f["views"]["feature_status"] == "observed")
    for st, reason in (("both_discrepancy", "data_discrepant"),
                       ("untested_overlap", "data_untested"),
                       ("unavailable", "data_unavailable")):
        m = [r for r in base_metrics() if r["metric"] != "views"]
        m.append(rr("V1", "views", st, 500, 400))
        f = feats(m)
        check(f"{st}: значение НЕ попадает, причина {reason}",
              f["views"]["feature_value"] is None
              and f["views"]["status_reason"] == reason)
    check("семь статусов покрыты", len(F.ELIGIBLE_RECONCILIATION) + len(F.INELIGIBLE_REASON) == 7)
    f = feats(base_metrics())
    check("data_basis ссылается на статус сверки, а не на выдуманный score",
          f["data_basis"]["feature_value"] == "both_matched")


# ───────────────────────────────────────────────────────── DATA QUALITY
def test_data_quality():
    print("\nDATA QUALITY")
    f = feats(base_metrics())
    for flag in F.QUALITY_FLAGS:
        check(f"{flag} = True при полных данных", f[flag]["feature_value"] is True)
    check("coverage_ratio = 1 при полных данных", f["coverage_ratio"]["feature_value"] == 1.0)
    m = [r for r in base_metrics() if r["metric"] not in
         ("avg_view_time_sec", "total_time_watched_sec")]
    m += [rr("V1", "avg_view_time_sec", "unavailable"),
          rr("V1", "total_time_watched_sec", "unavailable")]
    f = feats(m)
    check("has_watch_time = False при отсутствии данных",
          f["has_watch_time"]["feature_value"] is False)
    check("coverage_ratio падает соответственно",
          f["coverage_ratio"]["feature_value"] == 0.8,
          str(f["coverage_ratio"]["feature_value"]))
    check("флаг отличает отсутствие признака от нулевого значения",
          "не равно нулевому" in f["has_watch_time"]["note"])
    f = feats(base_metrics(), snaps=[snap(precision="date", age=None)])
    check("has_fresh_timestamp = False при метке точности до даты",
          f["has_fresh_timestamp"]["feature_value"] is False)
    check("возраст при неточной метке -> NULL, а не приблизительный",
          f["snapshot_age_sec_max"]["feature_value"] is None)
    f = feats(base_metrics(), snaps=[])
    check("нет снимков -> age_bucket NULL, а не выдуманный",
          f["age_bucket"]["feature_value"] is None)


# ─────────────────────────────────────────────────── CONTENT UNAVAILABLE
def test_content_unavailable():
    print("\nCONTENT FEATURES")
    f = feats(base_metrics())
    for name in F.CONTENT_FEATURES_UNAVAILABLE:
        check(f"{name} объявлен недоступным с причиной",
              f[name]["feature_value"] is None
              and f[name]["feature_status"] == "unavailable"
              and f[name]["status_reason"])
    check("traffic_source не превращается в категорию «нет трафика»",
          f["traffic_source"]["feature_value"] is None)


# ──────────────────────────────────────────────────────────── VERSIONING
def test_versioning():
    print("\nVERSIONING")
    f = feats(base_metrics())
    check("каждая строка несёт версию политики",
          all(r["policy_version"] == F.FEATURE_POLICY_VERSION for r in f.values()))
    check("каждая строка несёт версию движка",
          all(r["engine_version"] == engine.FEATURE_ENGINE_VERSION for r in f.values()))
    from features.run import OUT
    names = [p.name for p in OUT.glob("features_*.jsonl")]
    check("версия политики входит в имя файла",
          any(F.FEATURE_POLICY_VERSION in n for n in names), str(names))
    check("тиры не смешаны",
          {r["tier"] for r in f.values()} <= {F.TIER_0, F.TIER_05})
    check("Tier 0 не содержит производных",
          all(r["feature_status"] != "derived"
              for r in f.values() if r["tier"] == F.TIER_0))


# ──────────────────────────────────────────────────── REPRODUCIBILITY
def test_reproducibility():
    print("\nREPRODUCIBILITY")
    from features.run import build, content_hash, load_inputs
    _, h1, r1 = build(write=False)
    _, h2, r2 = build(write=False)
    check("два прогона дают одинаковый feature_hash", h1 == h2, h1[:16])
    check("тот же feature_run_id", r1 == r2, r1[:8])
    videos, snaps, results = load_inputs()
    rows_a = engine.build(videos, results, snaps)
    rows_b = engine.build(list(reversed(videos)), list(reversed(results)),
                          list(reversed(snaps)))
    check("порядок входных строк не влияет на результат",
          content_hash(rows_a) == content_hash(rows_b))
    check("computed_at не входит в хеш", "computed_at" in
          __import__("features.run", fromlist=["x"]).VOLATILE)


if __name__ == "__main__":
    for fn in (test_duration, test_rates, test_watch, test_log_views,
               test_baseline_relative, test_outliers, test_reconciliation_statuses,
               test_data_quality, test_content_unavailable, test_versioning,
               test_reproducibility):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
