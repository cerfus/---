#!/usr/bin/env python3
"""Детерминированный движок сверки: video × metric × наблюдение.

Никакой случайности, никакого текущего времени, никакого порядка «как
получилось»: всё сортируется явно. reconciliation_run_id в содержимое
результата не входит и на классификацию не влияет.
"""
import hashlib
import json
from datetime import datetime

from reconcile import policies as P

ENGINE_VERSION = "reconcile-engine-1.0.0"
SOURCES = ("supermetrics", "metricool")


def _dt(s):
    return datetime.fromisoformat(s)


def _slices(obs_for_metric, time_invariant):
    """Детерминированная группировка наблюдений двух источников по срезам.

    Для метрик, не зависящих от времени, срез один: сравнивать можно любые
    наблюдения. Для остальных срез — кластер наблюдений, укладывающихся в
    окно сопоставления, не более одного на источник.
    """
    rows = sorted(obs_for_metric, key=lambda r: (r["observed_at"], r["source"],
                                                 r["observation_id"]))
    if time_invariant:
        return [("ALL", rows)] if rows else []
    out, cur, anchor = [], [], None
    for r in rows:
        t = _dt(r["observed_at"])
        if anchor is None:
            anchor, cur = t, [r]
            continue
        same_slice = (t - anchor).total_seconds() <= P.PAIR_WINDOW_SEC and \
            not any(c["source"] == r["source"] for c in cur)
        if same_slice:
            cur.append(r)
        else:
            out.append((cur[0]["observed_at"], cur))
            anchor, cur = t, [r]
    if cur:
        out.append((cur[0]["observed_at"], cur))
    return out


def _pick(rows, source):
    for r in rows:
        if r["source"] == source:
            return r
    return None


def _history(all_obs, source, upto_observed_at):
    """Наблюдения источника строго раньше указанного момента, по возрастанию."""
    h = [o for o in all_obs
         if o["source"] == source and o["value"] is not None
         and o["observed_at"] < upto_observed_at]
    return sorted(h, key=lambda r: (r["observed_at"], r["observation_id"]))


def _is_non_decreasing(all_obs, source, upto_observed_at):
    """Собственный ряд источника не убывает вплоть до момента сравнения.

    Источник, чей счётчик откатился назад, НЕ является отставшим: это
    аномалия. Без этой проверки убывание ошибочно объявлялось отставанием,
    потому что прошлое большее значение формально совпадало с текущим
    значением второго источника.
    """
    vals = [float(o["value"]) for o in sorted(
        (o for o in all_obs
         if o["source"] == source and o["value"] is not None
         and o["observed_at"] <= upto_observed_at),
        key=lambda r: (r["observed_at"], r["observation_id"]))]
    return all(x <= y for x, y in zip(vals, vals[1:]))


def _prove_lag(metric, all_obs, canon_src, lag_src, a_obs, b_obs):
    """Условия 1-4, 6-7 утверждённого правила both_lagged.

    Порядок наблюдений доказывается двумя способами. Обычный — по меткам
    времени. Если метка канонического наблюдения имеет точность до даты,
    порядок выводится из МОНОТОННОСТИ: для неубывающего счётчика меньшее
    значение не может быть позже большего. Это вывод из данных, а не
    допущение о том, когда именно была сделана выгрузка.
    """
    if metric not in P.MONOTONIC_COUNTER_WHITELIST:
        return None
    a_t2, b_t2 = float(a_obs["value"]), float(b_obs["value"])
    if b_t2 > a_t2:                                   # условие 4
        return None
    # условие 9: оба ряда обязаны быть неубывающими. Убывание счётчика —
    # аномалия, и описывать её как отставание значило бы классифицировать ложно.
    if not _is_non_decreasing(all_obs, canon_src, a_obs["observed_at"]):
        return None
    if not _is_non_decreasing(all_obs, lag_src, b_obs["observed_at"]):
        return None
    hist = _history(all_obs, canon_src, a_obs["observed_at"])
    if len({h["observed_at"] for h in hist}) + 1 < P.MIN_CANONICAL_OBSERVATIONS:
        return None                                   # условие 6
    for h in sorted(hist, key=lambda r: (float(r["value"]), r["observed_at"])):
        a_t1 = float(h["value"])
        if a_t1 < a_t2 and a_t1 == b_t2:              # условия 2 и 3
            order_basis = ("timestamp"
                           if h["observed_at_precision"] == P.REQUIRED_PRECISION
                           else "monotonic_value_order")
            return {
                "canonical_t1": {"observation_id": h["observation_id"],
                                 "value": a_t1, "observed_at": h["observed_at"],
                                 "observed_at_precision": h["observed_at_precision"]},
                "canonical_t2": {"observation_id": a_obs["observation_id"], "value": a_t2},
                "lagging_t2": {"observation_id": b_obs["observation_id"], "value": b_t2},
                "order_basis": order_basis,
            }
    return None


def classify_slice(metric, slice_key, rows, all_obs):
    """Одна классификация для одного среза одной метрики одного ролика."""
    a = _pick(rows, SOURCES[0])
    b = _pick(rows, SOURCES[1])
    av = a["value"] if a else None
    bv = b["value"] if b else None
    base = {"metric": metric, "slice": slice_key,
            "source_a": SOURCES[0], "source_b": SOURCES[1],
            "value_a": av, "value_b": bv,
            "observation_id_a": a["observation_id"] if a else None,
            "observation_id_b": b["observation_id"] if b else None,
            "observed_at_a": a["observed_at"] if a else None,
            "observed_at_b": b["observed_at"] if b else None,
            "canonical_source": None, "lagging_source": None, "lag_basis": None,
            "competing_explanation": None}

    # 1. присутствие. NULL никогда не превращается в 0.
    if av is None and bv is None:
        return {**base, "classification": "unavailable", "rule_id": "presence.none.v1"}
    if av is None or bv is None:
        if metric in P.BOTH_AVAILABLE:
            return {**base, "classification": "untested_overlap",
                    "rule_id": "presence.one_of_two_available.v1"}
        return {**base, "classification": "single_source",
                "rule_id": "presence.single_provider.v1",
                "canonical_source": a["source"] if a else b["source"]}

    # 2. пригодность временного сопоставления
    if metric not in P.TIME_INVARIANT:
        if (a["observed_at_precision"] != P.REQUIRED_PRECISION
                or b["observed_at_precision"] != P.REQUIRED_PRECISION):
            return {**base, "classification": "untested_overlap",
                    "rule_id": "time.insufficient_precision.v1"}
        gap = abs((_dt(a["observed_at"]) - _dt(b["observed_at"])).total_seconds())
        if gap > P.PAIR_WINDOW_SEC:
            return {**base, "classification": "untested_overlap",
                    "rule_id": "time.outside_pair_window.v1"}

    # 3. зарегистрированное преобразование проверяется ДО равенства и отставания
    tr = P.EXPECTED_TRANSFORMS.get(metric)
    if tr and tr["check"](float(av), float(bv)):
        return {**base, "classification": "both_expected_transform",
                "rule_id": tr["rule_id"], "canonical_source": tr["canonical"]}

    # 4. численное равенство
    eq, rule = P.values_equal(metric, av, bv)
    if eq:
        return {**base, "classification": "both_matched", "rule_id": rule}

    # 5. доказанное отставание, в обе стороны
    proofs = []
    for canon, lag, ao, bo in ((SOURCES[0], SOURCES[1], a, b),
                               (SOURCES[1], SOURCES[0], b, a)):
        pr = _prove_lag(metric, all_obs, canon, lag, ao, bo)
        if pr:
            proofs.append((canon, lag, pr))
    if len(proofs) == 1:                               # условие 8
        canon, lag, pr = proofs[0]
        return {**base, "classification": "both_lagged",
                "rule_id": "lag.previous_canonical_value.v1",
                "canonical_source": canon, "lagging_source": lag,
                "lag_basis": "lagging_value_equals_previous_canonical_value",
                "lag_evidence": pr,
                "competing_explanation": P.LAG_COMPETING_EXPLANATION,
                "statement": f"{lag} lagged behind the canonical observation from {canon}"}

    # 6. остаток — настоящее противоречие
    d = abs(float(av) - float(bv))
    return {**base, "classification": "both_discrepancy",
            "rule_id": "discrepancy.unexplained.v1",
            "absolute_difference": d,
            "relative_difference": d / max(abs(float(av)), abs(float(bv)))
            if max(abs(float(av)), abs(float(bv))) else 0.0}


def reconcile(observations):
    """Полный детерминированный проход. Возвращает (результаты, content_hash)."""
    by_vm = {}
    for o in observations:
        by_vm.setdefault((o["video_id"], o["metric"]), []).append(o)

    results = []
    for (vid, metric), obs in sorted(by_vm.items()):
        invariant = metric in P.TIME_INVARIANT
        for slice_key, rows in _slices(obs, invariant):
            r = classify_slice(metric, slice_key, rows, obs)
            r["video_id"] = vid
            r["engine_version"] = ENGINE_VERSION
            r["policy_version"] = P.POLICY_VERSION
            results.append(r)

    results.sort(key=lambda r: (r["video_id"], r["metric"], str(r["slice"])))
    payload = json.dumps(results, ensure_ascii=False, sort_keys=True)
    return results, hashlib.sha256(payload.encode("utf-8")).hexdigest()
