#!/usr/bin/env python3
"""Детерминированные генераторы выводов.

Формулировки собираются по шаблонам из чисел, а не сочиняются: только так
текст воспроизводим и проверяем валидатором. Каждый вывод проходит
валидацию ПЕРЕД выпуском; непрошедший поднимает исключение, а не молча
исправляется.
"""
import hashlib
import json

from insights import pipeline
from insights import policies as P
from insights.validator import assert_valid

GENERATORS_VERSION = "insights-generators-1.0.0"


def _insight(claim_type, statement, source_kind, n_sample=None,
             competing_explanation=None, based_on=None, refs=None,
             causality_established=False):
    assert_valid(statement, claim_type, causality_established)
    body = {
        "claim_type": claim_type, "statement": statement,
        "source_kind": source_kind, "n_sample": n_sample,
        "min_sample_required": P.MIN_SAMPLE_FOR_FACT if claim_type == "FACT" else None,
        "competing_explanation": competing_explanation,
        "based_on": sorted(based_on or []), "refs": sorted(refs or []),
        "status": "active",
        "policy_version": P.INSIGHTS_POLICY_VERSION,
        "generators_version": GENERATORS_VERSION,
    }
    body["content_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in sorted(body.items())},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return body


# Тип для валидатора: у записи блокировки нет ни выборки, ни модальности,
# но причинные и оценочные конструкции ей запрещены так же, как выводу.
# Причина отдельная: текст блокировки печатается в отчёте наравне с выводами,
# и до сих пор проверялся только там — то есть уже после выпуска.
BLOCKED_CLAIM_TYPE = "BLOCKED"


def _blocked(what, why, reason_code, detail=None):
    """Заблокированный вывод. reason_code — машинная причина: последующие фазы
    обязаны уметь отличать «мало данных» от «связь механическая» без разбора
    человеческого текста."""
    for part in (what, why):
        assert_valid(part, BLOCKED_CLAIM_TYPE)
    return {"conclusion": what, "reason": why, "reason_code": reason_code,
            "detail": detail or {}}


# ── качество данных из покрытия ─────────────────────────────────────────────
def from_coverage(coverage_rows):
    ins, blocked = [], []
    overall = next(r for r in coverage_rows if r["scope"] == "all_metrics")
    pct = overall["coverage_pct"]
    ins.append(_insight(
        "DATA_QUALITY",
        f"Пригодными для расчёта оказались {overall['eligible_observations']} "
        f"из {overall['total_observations']} наблюдений, покрытие {100*pct:.1f}%.",
        "coverage", refs=["analytics/coverage.jsonl#all_metrics"]))

    for r in sorted((x for x in coverage_rows if x["scope"] == "metric"),
                    key=lambda x: x["metric"]):
        if r["coverage_pct"] is None or r["coverage_pct"] >= P.COVERAGE_WARN_THRESHOLD:
            continue
        parts = []
        if r["data_untested"]:
            parts.append(f"не проверено {r['data_untested']}")
        if r["data_unavailable"]:
            parts.append(f"недоступно {r['data_unavailable']}")
        if r["data_discrepant"]:
            parts.append(f"противоречиво {r['data_discrepant']}")
        ins.append(_insight(
            "DATA_QUALITY",
            f"Покрытие метрики {r['metric']} составляет "
            f"{100*r['coverage_pct']:.0f}% на {r['total_observations']} наблюдениях: "
            + ", ".join(parts) + ".",
            "coverage", refs=[f"analytics/coverage.jsonl#{r['metric']}"]))
        blocked.append(_blocked(
            f"FACT по метрике {r['metric']}",
            "часть наблюдений непригодна по статусу сверки",
            "ineligible_reconciliation_status",
            {"metric": r["metric"], "eligible": r["eligible_observations"],
             "total": r["total_observations"],
             "untested": r["data_untested"], "unavailable": r["data_unavailable"],
             "discrepant": r["data_discrepant"]}))
    return ins, blocked


# ── отставание источника ────────────────────────────────────────────────────
def from_reconciliation(recon_rows):
    ins, blocked = [], []
    lagged = [r for r in recon_rows if r["classification"] == "both_lagged"]
    if lagged:
        vids = sorted({r["video_id"] for r in lagged})
        metrics = sorted({r["metric"] for r in lagged})
        canon = sorted({r["canonical_source"] for r in lagged})
        lag = sorted({r["lagging_source"] for r in lagged})
        ins.append(_insight(
            "DATA_QUALITY",
            f"На {len(lagged)} сопоставлениях по метрике {', '.join(metrics)} "
            f"источник {', '.join(lag)} отстал от канонического наблюдения "
            f"{', '.join(canon)}; затронуто роликов: {len(vids)}. "
            "Расхождение объяснено доказанным отставанием, а не конфликтом.",
            "reconciliation", n_sample=len(lagged),
            refs=sorted(r["content_hash"] for r in lagged)[:8]))
    disc = [r for r in recon_rows if r["classification"] == "both_discrepancy"]
    if disc:
        ins.append(_insight(
            "ANOMALY",
            f"Обнаружено {len(disc)} расхождений между источниками, не объяснённых "
            "ни известным преобразованием, ни доказанным отставанием.",
            "reconciliation", n_sample=len(disc)))
        for r in disc:
            blocked.append(_blocked(
                f"FACT по {r['metric']} для ролика {r['video_id']}",
                "источники противоречат друг другу", "source_discrepancy",
                {"status": "both_discrepancy"}))
    untested = [r for r in recon_rows if r["classification"] == "untested_overlap"]
    if untested:
        by_metric = {}
        for r in untested:
            by_metric[r["metric"]] = by_metric.get(r["metric"], 0) + 1
        ins.append(_insight(
            "RECOMMENDATION",
            "Запрашивать метрики, доступные обоим источникам, одним и тем же "
            "набором полей на каждом срезе: это закроет "
            f"{len(untested)} непроверенных пересечений "
            f"({', '.join(f'{k}: {v}' for k, v in sorted(by_metric.items()))}).",
            "reconciliation",
            based_on=["coverage.untested_overlap"], n_sample=len(untested)))
    return ins, blocked


# ── размер выборки ──────────────────────────────────────────────────────────
def from_sample_size(account_rows, n_videos):
    ins, blocked = [], []
    ins.append(_insight(
        "DATA_QUALITY",
        f"В выборке n={n_videos} роликов при пороге "
        f"{P.MIN_SAMPLE_FOR_FACT} для вывода уровня FACT.",
        "analytics", refs=["analytics/baseline_account.jsonl"]))
    if n_videos < P.MIN_SAMPLE_FOR_FACT:
        metrics = sorted({r["metric"] for r in account_rows
                          if r["window_status"] == "observed"})
        blocked.append(_blocked(
            "любой вывод уровня FACT по метрикам аккаунта",
            f"размер выборки n={n_videos} ниже порога {P.MIN_SAMPLE_FOR_FACT}",
            "insufficient_sample",
            {"metrics": metrics, "n": n_videos,
             "needed": P.MIN_SAMPLE_FOR_FACT - n_videos}))
    for r in sorted(account_rows, key=lambda x: (x["window"], x["metric"])):
        if r["window_status"] == "no_data" and r["metric"] == "views":
            blocked.append(_blocked(
                f"baseline окна {r['window']}",
                "в выборке нет роликов этого возрастного бакета",
                "no_videos_in_age_bucket",
                {"window": r["window"], "age_bucket": r["age_bucket"]}))
    return ins, blocked


# ── измеренные связи ────────────────────────────────────────────────────────
def from_associations(assoc_rows):
    """Выводы из измеренных связей — строго через конвейер гейтов.

    Порядок проверок задан в insights/pipeline.py и исполняется там же.
    Здесь только перевод вердиктов конвейера в выводы и записи блокировок.

    Что попадает в список заблокированных:
      · стадия, ОСТАНОВИВШАЯ конвейер — всегда;
      · ограничение стадии mechanical_dependency — всегда, даже если связь
        всё равно умерла позже. Нерешённая механическая природа пары — это
        состояние политики, а не свойство текущих чисел: если завтра
        значимость появится, запрет должен уже лежать в журнале;
      · отказ конкретному потребителю (гипотеза, рекомендация) — на той
        стадии, где он реально что-то закрывает.
    Ограничение стадии outlier_robustness своей записи не даёт: оно
    проявляется отказом на стадиях 6-7 и дублировать его незачем.
    """
    ins, blocked = [], []
    for a in sorted(assoc_rows, key=lambda x: (x["x_metric"], x["y_metric"])):
        x, y = a["x_metric"], a["y_metric"]
        ev = pipeline.evaluate_association(a)
        by_stage = {s["stage"]: s for s in ev["stages"]}

        mech = by_stage.get("mechanical_dependency")
        if mech and mech["verdict"] == "restrict":
            blocked.append(_blocked(
                f"повышение связи {x} и {y} до рекомендации, доказательства "
                "Content DNA или основания эксперимента",
                "механическая природа пары не решена владельцем: "
                + mech["detail"]["candidate_note"]
                + " До решения статистическая значимость сама по себе "
                "повышения не даёт. Причинность не установлена.",
                mech["reason_code"], mech["detail"]))

        if ev["stopped_at"]:
            stop = by_stage[ev["stopped_at"]]
            blocked.append(_blocked(*_stop_wording(x, y, stop),
                                    stop["reason_code"], stop["detail"]))
            continue

        if ev["allow"]["HYPOTHESIS"]:
            direction = "обратная" if a["rho"] < 0 else "прямая"
            competing = ("связь может быть следствием общей причины, обратного "
                         "направления или свойства самих определений метрик; "
                         "выборка одна и мала")
            if ev["limitations"]:
                competing += "; " + "; ".join(ev["limitations"])
            ins.append(_insight(
                "HYPOTHESIS",
                f"В выборке n={a['n']} между {x} и {y} "
                f"наблюдается {direction} ранговая связь rho={a['rho']:+.3f} "
                f"(приближённая значимость p≈{a['p_two_sided']:.4f}). "
                f"Связь может отражать общий источник или особенности метрик. "
                f"Причинность не установлена.",
                "analytics", n_sample=a["n"], competing_explanation=competing,
                refs=[f"analytics/association.jsonl#{x}~{y}"]))
            blocked.append(_blocked(
                f"причинное утверждение о {x} и {y}",
                "нет завершённого эксперимента", "no_concluded_experiment",
                {"rho": a["rho"], "n": a["n"],
                 "sample_status": a["sample_status"]}))
        else:
            d = ev["deny"]["HYPOTHESIS"]
            blocked.append(_blocked(
                f"гипотеза о связи {x} и {y}",
                f"отказано на стадии {d['stage']}", d["reason_code"],
                d["detail"]))

        if not ev["allow"]["RECOMMENDATION"]:
            d = ev["deny"]["RECOMMENDATION"]
            blocked.append(_blocked(
                f"рекомендация по связи {x} и {y}",
                f"отказано на стадии {d['stage']}", d["reason_code"],
                d["detail"]))
    return ins, blocked


def _stop_wording(x, y, stop):
    """Текст блокировки для стадии, остановившей конвейер."""
    code, d = stop["reason_code"], stop["detail"]
    if code == P.MECHANICAL_REASON_CODE:
        return (f"содержательный вывод о связи {x} и {y}",
                "метрики связаны механически: " + d["mechanism"] +
                " Статистика сохранена как техническая величина. "
                "Причинность не установлена.")
    if code == pipeline.NOISE:
        return (f"гипотеза о связи {x} и {y}",
                f"связь не выделяется из шума при n={d['n']} "
                f"(rho={d['rho']:+.3f}, p≈{d['p']:.3f} > {d['threshold']})")
    if code == pipeline.INSUFFICIENT_SAMPLE:
        return (f"любой вывод о связи {x} и {y}",
                f"наблюдений {d['n']} при минимуме "
                f"{d['min_n_for_association']} для измерения связи")
    return (f"любой вывод о связи {x} и {y}",
            f"связь не измерена: статус {d.get('status')!r}")


# ── признаки и возраст ──────────────────────────────────────────────────────
def from_features(feature_rows, n_videos):
    ins, blocked = [], []
    unavailable = sorted({r["feature_name"] for r in feature_rows
                          if r["feature_status"] == "unavailable"
                          and not r["computed_from"]})
    if unavailable:
        ins.append(_insight(
            "DATA_QUALITY",
            f"Недоступны содержательные признаки: {', '.join(unavailable)}. "
            "Источник данных для них отсутствует.",
            "features", refs=["data/features/manifest.json"]))
        for name in unavailable:
            blocked.append(_blocked(
                f"любой вывод о признаке {name}",
                "нет источника данных для признака", "no_data_source",
                {"feature": name}))
    insufficient = sorted({r["feature_name"] for r in feature_rows
                           if r["feature_status"] == "insufficient_baseline"})
    for name in insufficient:
        blocked.append(_blocked(
            f"признак {name}", "baseline недостаточен и включает сам ролик",
            "insufficient_baseline", {"feature": name, "n": n_videos}))
    buckets = sorted({r["feature_value"] for r in feature_rows
                      if r["feature_name"] == "age_bucket" and r["feature_value"]})
    if buckets == ["backfill"]:
        ins.append(_insight(
            "DATA_QUALITY",
            f"Все {n_videos} роликов относятся к возрастному бакету backfill: "
            "ранние возрасты не наблюдались.",
            "features"))
        blocked.append(_blocked(
            "любой вывод о поведении метрик в первые часы жизни ролика",
            "нет роликов моложе 24 часов; Phase 0 остаётся BLOCKED — DATA COVERAGE",
            "phase0_data_coverage", {"buckets_present": buckets}))
    return ins, blocked


# ── причинность ─────────────────────────────────────────────────────────────
def from_causality(n_concluded_experiments):
    """Постоянный запрет причинности.

    Раньше блок о причинности выпускался на каждую измеренную связь. После
    введения гейтов связи отсекаются раньше, и запрет пропадал ровно тогда,
    когда гипотез не набралось. Но отсутствие гипотез не делает причинные
    утверждения допустимыми — поэтому запрет объявляется безусловно.
    """
    if n_concluded_experiments > 0:
        return [], []
    return [], [_blocked(
        "любое причинное утверждение о метриках аккаунта",
        "ни один эксперимент не завершён; наблюдаемые связи причинности "
        "не показывают",
        "no_concluded_experiment",
        {"concluded_experiments": n_concluded_experiments})]


def generate_all(coverage_rows, recon_rows, account_rows, assoc_rows,
                 feature_rows, n_videos, n_concluded_experiments=0):
    ins, blocked = [], []
    for fn, args in ((from_coverage, (coverage_rows,)),
                     (from_reconciliation, (recon_rows,)),
                     (from_sample_size, (account_rows, n_videos)),
                     (from_associations, (assoc_rows,)),
                     (from_features, (feature_rows, n_videos)),
                     (from_causality, (n_concluded_experiments,))):
        i, b = fn(*args)
        ins += i
        blocked += b
    ins.sort(key=lambda x: (x["claim_type"], x["statement"]))
    blocked.sort(key=lambda x: (x["conclusion"], x["reason"]))
    return ins, blocked
