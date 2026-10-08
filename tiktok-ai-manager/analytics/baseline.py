#!/usr/bin/env python3
"""Детерминированная аналитика: baseline роликов, аккаунта и покрытие.

Ни одно число здесь не получено рассуждением — только арифметикой над
наблюдениями, прошедшими сверку. Причинных утверждений нет по построению:
модуль не формирует текст, кроме описательных меток.
"""
import json
from collections import Counter
from pathlib import Path

from analytics import policies as A

ANALYTICS_ENGINE_VERSION = "analytics-engine-1.0.0"
SOURCE_A, SOURCE_B = "supermetrics", "metricool"


def snapshot_ref(video_id, source, observed_at):
    """Естественный ключ снимка.

    Суррогатный snapshot_id намеренно не используется: он BIGSERIAL и при
    пересборке базы меняется, а ссылка обязана переживать пересборку.
    """
    return f"{video_id}:{source}:{observed_at}"


def _value_of(r):
    """Значение канонического источника для одной классификации."""
    if r["classification"] == "single_source":
        return r["value_a"] if r["value_a"] is not None else r["value_b"]
    canon = r.get("canonical_source")
    if canon == SOURCE_A:
        return r["value_a"]
    if canon == SOURCE_B:
        return r["value_b"]
    return r["value_a"]          # both_matched: значения равны по определению


def select_metric(video_id, metric, results_by_vm):
    """Выбор значения метрики ролика по детерминированному правилу.

    Берётся самый поздний ПРИГОДНЫЙ срез. Непригодные не отбрасываются
    молча: их статусы возвращаются как диагностика покрытия.
    """
    rows = sorted(results_by_vm.get((video_id, metric), []),
                  key=lambda r: str(r["slice"]))
    eligible = [r for r in rows if A.is_eligible(metric, r["classification"])]
    seen = [r["classification"] for r in rows]
    if not eligible:
        reason = (A.ineligibility_reason(rows[-1]["classification"])
                  if rows else "no_observation")
        return {"value": None, "eligible": False, "reason": reason,
                "classification": rows[-1]["classification"] if rows else None,
                "slice": rows[-1]["slice"] if rows else None,
                "source": None, "observed_at": None,
                "reconciliation_refs": [r["content_hash"] for r in rows],
                "observation_refs": [], "statuses_seen": seen}
    r = eligible[-1]
    src = r.get("canonical_source")
    if src is None:
        src = SOURCE_A if r["value_a"] is not None else SOURCE_B
    obs = [x for x in (r.get("observation_id_a"), r.get("observation_id_b")) if x]
    at = r.get("observed_at_a") if src == SOURCE_A else r.get("observed_at_b")
    return {"value": _value_of(r), "eligible": True, "reason": None,
            "classification": r["classification"], "slice": r["slice"],
            "source": src, "observed_at": at,
            "reconciliation_refs": [r["content_hash"]],
            "observation_refs": sorted(obs), "statuses_seen": seen}


def video_baseline(videos, results, snapshots):
    """Аналитическая запись на каждый ролик. Ролики не отсеиваются."""
    by_vm = {}
    for r in results:
        by_vm.setdefault((r["video_id"], r["metric"]), []).append(r)
    snap_by_v = {}
    for s in snapshots:
        snap_by_v.setdefault(s["video_id"], []).append(s)

    out = []
    for v in sorted(videos, key=lambda x: x["video_id"]):
        vid = v["video_id"]
        sel = {m: select_metric(vid, m, by_vm)
               for m in A.COUNT_METRICS + A.WATCH_METRICS + A.PROPERTY_METRICS}
        val = {m: sel[m]["value"] for m in sel}

        views = val.get("views")
        rates = {f"{m.rstrip('s')}_rate" if m != "favorites" else "favorite_rate":
                 A.safe_ratio(val.get(m), views) for m in A.RATE_NUMERATORS}
        # engagement: NULL распространяется — отсутствующее слагаемое не ноль
        parts = [val.get(m) for m in A.RATE_NUMERATORS]
        eng_num = None if any(p is None for p in parts) else sum(float(p) for p in parts)
        rates["engagement_rate"] = A.safe_ratio(eng_num, views)

        derived_avg = A.safe_ratio(val.get("total_time_watched_sec"), views)
        actual_avg = val.get("avg_view_time_sec")
        if derived_avg is None or actual_avg is None:
            watch_match = None
        else:
            watch_match = abs(derived_avg - float(actual_avg)) <= A.WATCH_DERIVATION_TOLERANCE

        buckets = sorted({s["age_bucket"] for s in snap_by_v.get(vid, [])})
        ages = [s["age_seconds"] for s in snap_by_v.get(vid, [])
                if s.get("age_seconds") is not None]

        rec = {
            "video_id": vid,
            "published_at": v["published_at"],
            "duration_sec": val.get("duration_sec"),
            "views": val.get("views"), "reach": val.get("reach"),
            "likes": val.get("likes"), "comments": val.get("comments"),
            "shares": val.get("shares"), "favorites": val.get("favorites"),
            "like_rate": rates.get("like_rate"),
            "comment_rate": rates.get("comment_rate"),
            "share_rate": rates.get("share_rate"),
            "favorite_rate": rates.get("favorite_rate"),
            "engagement_rate": rates["engagement_rate"],
            "avg_view_time_sec": actual_avg,
            "avg_view_time_sec_derived": derived_avg,
            "avg_view_time_derivation_matches": watch_match,
            "completion_rate": val.get("completion_rate"),
            "per_second_retention": None,
            "per_second_retention_status": "unavailable",
            "age_bucket": buckets[0] if len(buckets) == 1 else
                          ("mixed" if buckets else None),
            "age_seconds_observed_max": max(ages) if ages else None,
            "sample_status": None,          # проставляется на уровне аккаунта
            "source_basis": {m: sel[m]["source"] for m in sorted(sel)},
            "reconciliation_basis": {m: sel[m]["classification"] for m in sorted(sel)},
            "eligibility": {m: (sel[m]["eligible"] or sel[m]["reason"])
                            for m in sorted(sel)},
            "observation_refs": sorted({o for m in sel for o in sel[m]["observation_refs"]}),
            "reconciliation_refs": sorted({o for m in sel
                                           for o in sel[m]["reconciliation_refs"]}),
            "snapshot_refs": sorted({snapshot_ref(s["video_id"], s["source"],
                                                  s["observed_at"])
                                     for s in snap_by_v.get(vid, [])}),
            "engine_version": ANALYTICS_ENGINE_VERSION,
            "policy_version": A.ANALYTICS_POLICY_VERSION,
        }
        out.append(rec)
    return out


def summarize(values, min_required=None):
    """n, среднее, медиана, перцентили, границы, выбросы. Метод зафиксирован."""
    vals = sorted(float(v) for v in values if v is not None)
    n = len(vals)
    base = {"n": n, "min_sample_required": min_required or A.MIN_SAMPLE_REQUIRED_DEFAULT,
            "sample_status": A.sample_status(n, min_required),
            "percentile_method": A.PERCENTILE_METHOD,
            "outlier_rule": A.OUTLIER_RULE_ID}
    if n == 0:
        return {**base, "mean": None, "median": None, "min": None, "max": None,
                "p25": None, "p75": None, "p90": None, "p90_status": "no_data",
                "n_outliers": None, "outlier_values": []}
    q1, q3 = A.percentile(vals, 0.25), A.percentile(vals, 0.75)
    iqr = q3 - q1
    lo, hi = q1 - A.IQR_MULTIPLIER * iqr, q3 + A.IQR_MULTIPLIER * iqr
    outliers = [v for v in vals if v < lo or v > hi]
    p90 = A.percentile(vals, 0.90) if n >= A.P90_MIN_N else None
    return {**base,
            "mean": sum(vals) / n, "median": A.percentile(vals, 0.50),
            "min": vals[0], "max": vals[-1], "p25": q1, "p75": q3,
            "p90": p90,
            "p90_status": "computed" if p90 is not None else
                          f"suppressed_n_lt_{A.P90_MIN_N}",
            "iqr_lower_fence": lo, "iqr_upper_fence": hi,
            "n_outliers": len(outliers), "outlier_values": outliers}


def account_baseline(video_records, total_n):
    """Агрегаты по окнам. Окна не смешиваются, отсутствие данных — явное."""
    metrics = (A.COUNT_METRICS + A.WATCH_METRICS + A.PROPERTY_METRICS +
               ["like_rate", "comment_rate", "share_rate", "favorite_rate",
                "engagement_rate"])
    out = []
    for window, spec in sorted(A.BASELINE_WINDOWS.items()):
        rows = [r for r in video_records if r["age_bucket"] == spec["age_bucket"]]
        for metric in metrics:
            vals = [r.get(metric) for r in rows]
            eligible_n = sum(1 for v in vals if v is not None)
            s = summarize(vals)
            out.append({
                "window": window, "age_bucket": spec["age_bucket"], "metric": metric,
                **s,
                "total_n": total_n, "eligible_n": eligible_n,
                "coverage_ratio": A.safe_ratio(eligible_n, total_n),
                "window_status": "observed" if rows else "no_data",
                "window_reason": None if rows else A.WINDOW_NO_DATA_REASON,
                "engine_version": ANALYTICS_ENGINE_VERSION,
                "policy_version": A.ANALYTICS_POLICY_VERSION,
            })
    return out


def coverage(results):
    """Покрытие. Недоступно, не проверено и противоречиво — три разных состояния."""
    total = len(results)
    per_status = Counter(r["classification"] for r in results)
    eligible = sum(v for k, v in per_status.items() if k in A.ELIGIBLE_STATUSES)
    rows = [{
        "scope": "all_metrics", "metric": None,
        "total_observations": total, "eligible_observations": eligible,
        "ineligible_observations": total - eligible,
        "coverage_pct": A.safe_ratio(eligible, total),
        "data_unavailable": per_status.get("unavailable", 0),
        "data_untested": per_status.get("untested_overlap", 0),
        "data_discrepant": per_status.get("both_discrepancy", 0),
        **{f"pct_{k}": A.safe_ratio(per_status.get(k, 0), total)
           for k in sorted(A.ELIGIBLE_STATUSES | set(A.INELIGIBLE_REASONS))},
        **{f"n_{k}": per_status.get(k, 0)
           for k in sorted(A.ELIGIBLE_STATUSES | set(A.INELIGIBLE_REASONS))},
        "engine_version": ANALYTICS_ENGINE_VERSION,
        "policy_version": A.ANALYTICS_POLICY_VERSION,
    }]
    by_metric = {}
    for r in results:
        by_metric.setdefault(r["metric"], []).append(r)
    for metric, rs in sorted(by_metric.items()):
        ps = Counter(r["classification"] for r in rs)
        el = sum(v for k, v in ps.items() if A.is_eligible(metric, k))
        rows.append({
            "scope": "metric", "metric": metric,
            "total_observations": len(rs), "eligible_observations": el,
            "ineligible_observations": len(rs) - el,
            "coverage_pct": A.safe_ratio(el, len(rs)),
            "data_unavailable": ps.get("unavailable", 0),
            "data_untested": ps.get("untested_overlap", 0),
            "data_discrepant": ps.get("both_discrepancy", 0),
            **{f"pct_{k}": A.safe_ratio(ps.get(k, 0), len(rs))
               for k in sorted(A.ELIGIBLE_STATUSES | set(A.INELIGIBLE_REASONS))},
            **{f"n_{k}": ps.get(k, 0)
               for k in sorted(A.ELIGIBLE_STATUSES | set(A.INELIGIBLE_REASONS))},
            "engine_version": ANALYTICS_ENGINE_VERSION,
            "policy_version": A.ANALYTICS_POLICY_VERSION,
        })
    return rows
