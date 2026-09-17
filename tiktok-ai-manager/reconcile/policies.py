#!/usr/bin/env python3
"""Политики сверки. Всё численно и детерминированно, «примерно равно» нет.

Ни одна политика не выражает мнения о качестве источника. Канонический
источник определяется наблюдениями для конкретной пары video × metric и
глобально не назначается.
"""
import math

POLICY_VERSION = "reconcile-policies-1.0.0"

# ── доступность метрики по источникам ───────────────────────────────────────
# Метрику отдают оба источника: пропуск одного означает, что пересечение не
# проверено, а не что источник единственный.
BOTH_AVAILABLE = frozenset({"views", "likes", "comments", "shares", "duration_sec"})
# Метрику отдаёт только Supermetrics — это штатный single_source.
SUPERMETRICS_ONLY = frozenset({"reach", "favorites", "completion_rate",
                               "avg_view_time_sec", "total_time_watched_sec"})
# Не отдаёт никто: источники трафика.
NEITHER = frozenset({"src_foryou", "src_hashtag", "src_sound", "src_search",
                     "src_profile"})

# ── правила сравнения ───────────────────────────────────────────────────────
# Целочисленные счётчики сравниваются ТОЛЬКО точным равенством.
# Допуск с плавающей точкой к ним не применяется никогда.
INTEGER_COUNTERS = frozenset({"views", "reach", "likes", "comments", "shares",
                              "favorites", "total_time_watched_sec"})
# Для вещественных метрик допуск задан численно и явно.
FLOAT_TOLERANCE = {
    "completion_rate": 1e-6,     # источник отдаёт 4 знака
    "avg_view_time_sec": 1e-4,   # источник отдаёт 4 знака
    "duration_sec": 1e-3,        # источник отдаёт 3 знака
}

# ── время ───────────────────────────────────────────────────────────────────
# Метрики, не зависящие от момента наблюдения: их можно сверять без
# временного сопоставления.
TIME_INVARIANT = frozenset({"duration_sec"})
# Окно сопоставления наблюдений двух источников.
PAIR_WINDOW_SEC = 300
# Для зависящих от времени метрик обе метки обязаны иметь точность до секунды.
REQUIRED_PRECISION = "second"

# ── монотонные счётчики ─────────────────────────────────────────────────────
# both_lagged разрешён только здесь. Список расширяется явно и только после
# доказательства монотонности наблюдениями.
MONOTONIC_COUNTER_WHITELIST = frozenset({"views", "likes", "shares", "comments"})
# Кандидаты, НЕ включённые: монотонность не доказана.
MONOTONIC_CANDIDATES_NOT_ENABLED = frozenset({"reach", "favorites",
                                              "total_time_watched_sec"})
MIN_CANONICAL_OBSERVATIONS = 2


# ── зарегистрированные преобразования ───────────────────────────────────────
def _duration_floor(sm, mt):
    """Доказано на 32 сопоставлениях EXP-004: metricool = floor(supermetrics)."""
    return math.floor(sm) == mt


EXPECTED_TRANSFORMS = {
    "duration_sec": {
        "rule_id": "transform.duration.floor.v1",
        "description": "metricool.duration_sec == floor(supermetrics.duration_sec)",
        "canonical": "supermetrics",     # больше знаков после запятой
        "check": _duration_floor,
    },
}

# ── политика FACT (для Phase 7, здесь только фиксируется) ───────────────────
FACT_ALLOWED = frozenset({"both_matched", "both_expected_transform",
                          "both_lagged", "single_source"})
FACT_BLOCKED = frozenset({"both_discrepancy", "untested_overlap", "unavailable"})
ALL_STATUSES = FACT_ALLOWED | FACT_BLOCKED

LAG_COMPETING_EXPLANATION = (
    "Источники читают независимо, и совпадение с более ранним каноническим "
    "значением может быть случайным. С ростом числа подтверждённых случаев "
    "случайность менее правдоподобна, но не исключается.")


def fact_allowed(status):
    if status in FACT_ALLOWED:
        return True
    if status in FACT_BLOCKED:
        return False
    raise ValueError(f"неизвестный статус сверки: {status!r}")


def values_equal(metric, a, b):
    """Численное правило равенства. Для счётчиков — только точное совпадение."""
    if metric in INTEGER_COUNTERS:
        return int(a) == int(b), "equality.integer.exact.v1"
    tol = FLOAT_TOLERANCE.get(metric)
    if tol is None:
        return float(a) == float(b), "equality.float.exact.v1"
    return abs(float(a) - float(b)) <= tol, f"equality.float.tol_{tol:g}.v1"
