#!/usr/bin/env python3
"""OBSERVATION -> FEATURE. Дальше по цепочке Phase 4 не идёт.

Слой строго ниже по течению от сверки и аналитики: он их читает и никогда
не изменяет. Ранжирования признаков, «лучших» форматов и любых выводов
здесь нет по построению.
"""
import math

from analytics import policies as A
from analytics.baseline import select_metric, snapshot_ref, summarize
from features import policies as F

FEATURE_ENGINE_VERSION = "feature-engine-1.0.0"


def _row(video_id, name, value, ftype, tier, status, reason=None,
         source_basis=None, reconciliation_basis=None, evidence_refs=None,
         computed_from=None, extra=None):
    r = {
        "video_id": video_id, "feature_name": name, "feature_value": value,
        "feature_type": ftype, "tier": tier,
        "feature_status": status, "status_reason": reason,
        "policy_version": F.FEATURE_POLICY_VERSION,
        "engine_version": FEATURE_ENGINE_VERSION,
        "source_basis": source_basis or {},
        "reconciliation_basis": reconciliation_basis or {},
        "evidence_refs": sorted(evidence_refs or []),
        "computed_from": sorted(computed_from or []),
    }
    if extra:
        r.update(extra)
    return r


def _select_all(video_id, by_vm):
    return {m: select_metric(video_id, m, by_vm) for m in F.TIER_0_METRICS}


def build(videos, results, snapshots):
    by_vm = {}
    for r in results:
        by_vm.setdefault((r["video_id"], r["metric"]), []).append(r)
    snaps_by_v = {}
    for s in snapshots:
        snaps_by_v.setdefault(s["video_id"], []).append(s)

    selections = {v["video_id"]: _select_all(v["video_id"], by_vm) for v in videos}
    derived = {v["video_id"]: _derive(v["video_id"], selections[v["video_id"]])
               for v in videos}

    # Границы выбросов считаются по всей выборке окна. Ролик входит в неё сам —
    # это свойство описательной статистики, а не утечка вывода из будущего.
    fences = {}
    for feat, metric in sorted(F.OUTLIER_FEATURES.items()):
        vals = [derived[v["video_id"]].get(metric) if metric in derived[v["video_id"]]
                else selections[v["video_id"]].get(metric, {}).get("value")
                for v in videos]
        fences[feat] = summarize(vals)

    rows = []
    for v in sorted(videos, key=lambda x: x["video_id"]):
        vid = v["video_id"]
        sel, der = selections[vid], derived[vid]
        snaps = snaps_by_v.get(vid, [])
        rows += _tier0(vid, v, sel, snaps)
        rows += _tier05(vid, sel, der, fences, baseline_n=len(videos))
        rows += _quality(vid, sel, der, snaps)
        rows += _unavailable(vid)
    rows.sort(key=lambda r: (r["video_id"], r["feature_name"]))
    return rows


def _derive(video_id, sel):
    """Производные величины, нужные нескольким признакам сразу."""
    views = sel["views"]["value"] if sel["views"]["eligible"] else None
    out = {}
    for feat, num in sorted(F.RATE_FEATURES.items()):
        n = sel[num]["value"] if sel[num]["eligible"] else None
        out[feat] = A.safe_ratio(n, views)
    parts = [sel[m]["value"] if sel[m]["eligible"] else None
             for m in F.ENGAGEMENT_COMPONENTS]
    total = None if any(p is None for p in parts) else sum(float(p) for p in parts)
    out["engagement_rate"] = A.safe_ratio(total, views)
    dur = sel["duration_sec"]["value"] if sel["duration_sec"]["eligible"] else None
    avt = sel["avg_view_time_sec"]["value"] if sel["avg_view_time_sec"]["eligible"] else None
    out["avg_view_time_ratio"] = (A.safe_ratio(avt, dur)
                                  if dur is not None and float(dur) > 0 else None)
    out["views"] = views
    out["completion_rate"] = (sel["completion_rate"]["value"]
                              if sel["completion_rate"]["eligible"] else None)
    out["views_log1p"] = (math.log1p(float(views))
                          if views is not None and float(views) >= 0 else None)
    return out


def _basis(sel, metrics):
    return ({m: sel[m]["source"] for m in metrics},
            {m: sel[m]["classification"] for m in metrics},
            {r for m in metrics for r in sel[m]["observation_refs"]}
            | {r for m in metrics for r in sel[m]["reconciliation_refs"]})


def _tier0(vid, video, sel, snaps):
    rows = [
        _row(vid, "published_at", video["published_at"], "timestamp", F.TIER_0,
             "observed", computed_from=["videos.published_at"],
             evidence_refs=[f"videos.jsonl#{vid}"]),
    ]
    for m in F.TIER_0_METRICS:
        s = sel[m]
        src, rec, ev = _basis(sel, [m])
        if s["eligible"]:
            rows.append(_row(vid, m, s["value"], "numeric", F.TIER_0, "observed",
                             source_basis=src, reconciliation_basis=rec,
                             evidence_refs=ev, computed_from=[f"observation.{m}"]))
        else:
            rows.append(_row(vid, m, None, "numeric", F.TIER_0, "unavailable",
                             reason=s["reason"], source_basis=src,
                             reconciliation_basis=rec, evidence_refs=ev,
                             computed_from=[f"observation.{m}"]))
    buckets = sorted({s["age_bucket"] for s in snaps})
    ages = [s["age_seconds"] for s in snaps if s.get("age_seconds") is not None]
    rows.append(_row(vid, "age_bucket",
                     buckets[0] if len(buckets) == 1 else ("mixed" if buckets else None),
                     "categorical", F.TIER_0,
                     "observed" if buckets else "unavailable",
                     reason=None if buckets else "no_snapshots",
                     computed_from=["snapshot.age_bucket"],
                     evidence_refs=[snapshot_ref(s["video_id"], s["source"],
                                                 s["observed_at"]) for s in snaps]))
    rows.append(_row(vid, "snapshot_age_sec_max", max(ages) if ages else None,
                     "numeric", F.TIER_0, "observed" if ages else "unavailable",
                     reason=None if ages else "no_second_precision_timestamp",
                     computed_from=["snapshot.age_seconds"],
                     evidence_refs=[snapshot_ref(s["video_id"], s["source"],
                                                 s["observed_at"]) for s in snaps]))
    rows.append(_row(vid, "data_basis", sel["views"]["classification"], "categorical",
                     F.TIER_0, "observed" if sel["views"]["classification"] else "unavailable",
                     reason=None if sel["views"]["classification"] else "no_reconciliation",
                     computed_from=["reconciliation.views.classification"],
                     evidence_refs=sel["views"]["reconciliation_refs"]))
    return rows


def _tier05(vid, sel, der, fences, baseline_n):
    rows = []
    dur = sel["duration_sec"]["value"] if sel["duration_sec"]["eligible"] else None
    bucket, why = F.duration_bucket(dur)
    src, rec, ev = _basis(sel, ["duration_sec"])
    rows.append(_row(vid, "duration_bucket", bucket, "categorical", F.TIER_05,
                     "derived" if bucket else "unavailable", reason=why,
                     source_basis=src, reconciliation_basis=rec, evidence_refs=ev,
                     computed_from=["duration_sec"],
                     extra={"bucket_rule": F.DURATION_BUCKET_RULE}))

    for feat, num in sorted(F.RATE_FEATURES.items()):
        src, rec, ev = _basis(sel, [num, "views"])
        v = der[feat]
        rows.append(_row(vid, feat, v, "numeric", F.TIER_05,
                         "derived" if v is not None else "unavailable",
                         reason=None if v is not None else _ratio_reason(sel, num),
                         source_basis=src, reconciliation_basis=rec, evidence_refs=ev,
                         computed_from=[num, "views"]))

    src, rec, ev = _basis(sel, F.ENGAGEMENT_COMPONENTS + ["views"])
    v = der["engagement_rate"]
    rows.append(_row(vid, "engagement_rate", v, "numeric", F.TIER_05,
                     "derived" if v is not None else "unavailable",
                     reason=None if v is not None else "component_or_denominator_missing",
                     source_basis=src, reconciliation_basis=rec, evidence_refs=ev,
                     computed_from=F.ENGAGEMENT_COMPONENTS + ["views"]))

    src, rec, ev = _basis(sel, ["avg_view_time_sec", "duration_sec"])
    v = der["avg_view_time_ratio"]
    rows.append(_row(vid, "avg_view_time_ratio", v, "numeric", F.TIER_05,
                     "derived" if v is not None else "unavailable",
                     reason=None if v is not None else "duration_missing_or_non_positive",
                     source_basis=src, reconciliation_basis=rec, evidence_refs=ev,
                     computed_from=["avg_view_time_sec", "duration_sec"],
                     extra={"note": "отношение среднего времени просмотра к "
                                    "длительности; удержанием НЕ является"}))

    src, rec, ev = _basis(sel, ["views"])
    rows.append(_row(vid, "views_log1p", der["views_log1p"], "numeric", F.TIER_05,
                     "derived" if der["views_log1p"] is not None else "unavailable",
                     reason=None if der["views_log1p"] is not None else "views_missing",
                     source_basis=src, reconciliation_basis=rec, evidence_refs=ev,
                     computed_from=["views"],
                     extra={"note": "исходное views хранится отдельно и не заменяется"}))

    for feat, metric in sorted(F.OUTLIER_FEATURES.items()):
        val = der.get(metric)
        s = fences[feat]
        if val is None or s["n"] == 0 or s.get("iqr_lower_fence") is None:
            rows.append(_row(vid, feat, None, "boolean", F.TIER_05, "unavailable",
                             reason="metric_missing" if val is None else "no_fences",
                             computed_from=[metric],
                             extra={"outlier_rule": A.OUTLIER_RULE_ID,
                                    "analytics_policy_version": A.ANALYTICS_POLICY_VERSION}))
        else:
            is_out = float(val) < s["iqr_lower_fence"] or float(val) > s["iqr_upper_fence"]
            rows.append(_row(vid, feat, is_out, "boolean", F.TIER_05, "derived",
                             computed_from=[metric],
                             extra={"outlier_rule": A.OUTLIER_RULE_ID,
                                    "analytics_policy_version": A.ANALYTICS_POLICY_VERSION,
                                    "lower_fence": s["iqr_lower_fence"],
                                    "upper_fence": s["iqr_upper_fence"],
                                    "sample_n": s["n"]}))

    for feat, metric in sorted(F.BASELINE_RELATIVE.items()):
        ok, why = F.baseline_relative_eligible(
            baseline_n=baseline_n, video_in_baseline=True, window_matches=True)
        rows.append(_row(vid, feat, None, "numeric", F.TIER_05,
                         "insufficient_baseline" if not ok else "derived",
                         reason=why,
                         computed_from=[metric, "account_baseline"],
                         extra={"baseline_min_sample": F.BASELINE_MIN_SAMPLE,
                                "baseline_n": baseline_n,
                                "leave_one_out_required":
                                    F.BASELINE_REQUIRE_LEAVE_ONE_OUT}))
    return rows


def _ratio_reason(sel, num):
    if not sel[num]["eligible"]:
        return f"numerator_{sel[num]['reason']}"
    if not sel["views"]["eligible"]:
        return f"denominator_{sel['views']['reason']}"
    return "denominator_zero_or_missing"


def _quality(vid, sel, der, snaps):
    eligible = [m for m in F.TIER_0_METRICS if sel[m]["eligible"]]
    flags = {
        "has_views": sel["views"]["eligible"],
        "has_engagement": all(sel[m]["eligible"] for m in F.ENGAGEMENT_COMPONENTS),
        "has_watch_time": (sel["avg_view_time_sec"]["eligible"]
                           and sel["total_time_watched_sec"]["eligible"]),
        "has_completion": sel["completion_rate"]["eligible"],
        "has_duration": sel["duration_sec"]["eligible"],
        "has_fresh_timestamp": any(s.get("observed_at_precision") == "second"
                                   for s in snaps),
        "has_reconciliation": bool(sel["views"]["classification"]),
    }
    rows = [_row(vid, k, v, "boolean", F.TIER_0, "observed",
                 computed_from=["eligibility"],
                 extra={"note": "отсутствие признака не равно нулевому значению"})
            for k, v in sorted(flags.items())]
    rows.append(_row(vid, "coverage_ratio",
                     A.safe_ratio(len(eligible), len(F.TIER_0_METRICS)),
                     "numeric", F.TIER_05, "derived",
                     computed_from=["eligibility"],
                     extra={"eligible_metrics": sorted(eligible),
                            "total_metrics": len(F.TIER_0_METRICS)}))
    return rows


def _unavailable(vid):
    return [_row(vid, name, None, "categorical", F.TIER_0, "unavailable", reason=why,
                 computed_from=[])
            for name, why in sorted(F.CONTENT_FEATURES_UNAVAILABLE.items())]
