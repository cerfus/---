#!/usr/bin/env python3
"""Классификация сверки двух источников.

Референсная реализация правила, проверяемая на данных EXP-004.
Phase 2 переносит её в normalize/reconcile.py и в CHECK-ограничения.

Ключевой принцип: канонический источник определяется НАБЛЮДЕНИЯМИ для
конкретной пары (ролик, метрика), а не назначается источнику навсегда.
Формулировки нейтральны: источник отстал от канонического наблюдения,
а не «ошибся» и не «выдал неверные данные».
"""
import math

# --- реестры метрик ---------------------------------------------------------

# Метрика отдаётся обоими источниками: пропуск одного из них означает, что
# пересечение не проверено, а не что источник единственный.
BOTH_AVAILABLE = {"views", "likes", "comments", "shares", "duration_sec"}

# Метрику отдаёт только Supermetrics — это штатный single_source.
SUPERMETRICS_ONLY = {"reach", "favorites", "completion_rate",
                     "avg_view_time_sec", "total_time_watched_sec"}

# Условие 1: both_lagged допустим ТОЛЬКО для монотонных счётчиков из белого
# списка. Список расширяется явно и только после подтверждения монотонности
# наблюдениями.
MONOTONIC_COUNTER_WHITELIST = {"views", "likes", "shares", "comments"}

# Кандидаты, НЕ включённые в белый список: монотонность не подтверждена
# наблюдениями (reach и favorites теоретически могут пересчитываться).
MONOTONIC_CANDIDATES_NOT_ENABLED = {"reach", "favorites", "total_time_watched_sec"}

# Условие 5: известные детерминированные преобразования остаются
# both_expected_transform и НИКОГДА не переклассифицируются в both_lagged.
EXPECTED_TRANSFORMS = {
    "duration_sec": lambda a, b: math.floor(a) == b,   # metricool = floor(supermetrics)
}

# Условие 6: минимум наблюдений канонического источника для доказательства.
MIN_CANONICAL_OBSERVATIONS = 2

LAG_COMPETING_EXPLANATION = (
    "Источники читают независимо, и совпадение с более ранним каноническим "
    "значением может быть случайным. С ростом числа подтверждённых случаев "
    "случайность становится менее правдоподобной, но не исключается."
)


def _prove_lag(series_canon, series_lag, t2):
    """Проверяет условия 2, 3, 4 для конкретного направления отставания.

    series_*: список (t, value), отсортированный по t. t2 — момент оценки.
    Возвращает доказательство или None.
    """
    at_t2_lag = [v for t, v in series_lag if t == t2]
    at_t2_canon = [v for t, v in series_canon if t == t2]
    if not at_t2_lag or not at_t2_canon:
        return None
    b_t2, a_t2 = float(at_t2_lag[0]), float(at_t2_canon[0])

    # Условие 6: канонический источник должен иметь достаточную историю
    if len({t for t, _ in series_canon}) < MIN_CANONICAL_OBSERVATIONS:
        return None

    # Условие 4: отстающий не может опережать канонический
    if b_t2 > a_t2:
        return None

    # Условия 2 и 3: существует более раннее каноническое наблюдение A(t1),
    # строго меньшее текущего канонического, в точности равное B(t2).
    for t1, v1 in series_canon:
        if t1 >= t2:
            continue
        a_t1 = float(v1)
        if a_t1 < a_t2 and a_t1 == b_t2:
            return {"t1": t1, "t2": t2, "canonical_t1_value": a_t1,
                    "canonical_t2_value": a_t2, "lagging_t2_value": b_t2}
    return None


def classify(metric, series_by_source, t2, source_names=("supermetrics", "metricool")):
    """Возвращает статус сверки для (метрика, момент t2).

    series_by_source: {source: [(t, value), ...]} — уже отсортировано по t.
    """
    s1, s2 = source_names
    v1 = _value_at(series_by_source.get(s1, []), t2)
    v2 = _value_at(series_by_source.get(s2, []), t2)

    if v1 is None and v2 is None:
        return {"reconciliation_status": "unavailable"}

    if v1 is None or v2 is None:
        present = s1 if v1 is not None else s2
        if metric in BOTH_AVAILABLE:
            return {"reconciliation_status": "untested_overlap",
                    "present_source": present,
                    "note": "метрика доступна в обоих источниках, запрошен один"}
        return {"reconciliation_status": "single_source", "present_source": present}

    a, b = float(v1), float(v2)

    # Условие 5: известное преобразование имеет приоритет над всем остальным
    rule = EXPECTED_TRANSFORMS.get(metric)
    if rule and (rule(a, b) or rule(b, a)):
        return {"reconciliation_status": "both_expected_transform",
                "transform": f"{metric}: floor()"}

    if a == b:
        return {"reconciliation_status": "both_matched"}

    # Условие 1: both_lagged доступен только монотонным счётчикам из списка
    if metric in MONOTONIC_COUNTER_WHITELIST:
        proofs = []
        for canon, lag in ((s1, s2), (s2, s1)):
            pr = _prove_lag(series_by_source.get(canon, []),
                            series_by_source.get(lag, []), t2)
            if pr:
                proofs.append((canon, lag, pr))
        if len(proofs) == 1:
            canon, lag, pr = proofs[0]
            return {
                "reconciliation_status": "both_lagged",
                "canonical_source": canon,
                "lagging_source": lag,
                "lag_basis": "lagging_value_equals_previous_canonical_value",
                "lag_evidence": pr,
                "competing_explanation": LAG_COMPETING_EXPLANATION,
                "statement": f"{lag} lagged behind the canonical observation "
                             f"from {canon}",
            }
        # Два направления сразу — доказательство неоднозначно, не применяем
    return {"reconciliation_status": "both_discrepancy",
            "absolute_difference": abs(a - b),
            "relative_difference": abs(a - b) / max(abs(a), abs(b)) if max(abs(a), abs(b)) else 0.0}


def _value_at(series, t):
    for tt, v in series:
        if tt == t:
            return v
    return None


# --- политика FACT ----------------------------------------------------------

FACT_ALLOWED_STATUSES = frozenset({
    "both_matched", "both_expected_transform", "both_lagged", "single_source",
})
FACT_BLOCKED_STATUSES = frozenset({
    "both_discrepancy", "untested_overlap", "unavailable",
})


def fact_allowed(reconciliation_status):
    """Правило ck_fact_reconcil в исполняемом виде.

    FACT опирается на статус ИМЕННО той метрики, для которой он выводится:
    доказанное отставание по views не разрешает FACT по reach.
    """
    if reconciliation_status in FACT_ALLOWED_STATUSES:
        return True
    if reconciliation_status in FACT_BLOCKED_STATUSES:
        return False
    raise ValueError(f"неизвестный статус сверки: {reconciliation_status!r}")
