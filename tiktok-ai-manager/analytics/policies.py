#!/usr/bin/env python3
"""Политики аналитического слоя. Всё численно, версионировано, без суждений.

Phase 3 только измеряет. Здесь нет и не может быть причинности, «паттернов»
и рекомендаций: это предмет более поздних фаз.
"""
import math

ANALYTICS_POLICY_VERSION = "analytics-policies-1.0.0"

# ── допустимость наблюдения по статусу сверки ───────────────────────────────
# Совпадает с политикой FACT Phase 2, но объявлена отдельно: аналитика и
# доказательность — разные решения, и связывать их молча нельзя.
ELIGIBLE_STATUSES = frozenset({"both_matched", "both_expected_transform",
                               "both_lagged", "single_source"})
# Три РАЗНЫХ состояния непригодности, которые нельзя смешивать.
INELIGIBLE_REASONS = {
    "both_discrepancy": "data_discrepant",
    "untested_overlap": "data_untested",
    "unavailable": "data_unavailable",
}

ANALYTICS_ELIGIBILITY_POLICY = {
    "version": ANALYTICS_POLICY_VERSION,
    "default": sorted(ELIGIBLE_STATUSES),
    # Явное перечисление по метрикам: расширение требует правки политики,
    # а не молчаливого попадания новой метрики в baseline.
    "per_metric": {
        "views":                  sorted(ELIGIBLE_STATUSES),
        "likes":                  sorted(ELIGIBLE_STATUSES),
        "comments":               sorted(ELIGIBLE_STATUSES),
        "shares":                 sorted(ELIGIBLE_STATUSES),
        "favorites":              sorted(ELIGIBLE_STATUSES),
        "reach":                  sorted(ELIGIBLE_STATUSES),
        "duration_sec":           sorted(ELIGIBLE_STATUSES),
        "completion_rate":        sorted(ELIGIBLE_STATUSES),
        "avg_view_time_sec":      sorted(ELIGIBLE_STATUSES),
        "total_time_watched_sec": sorted(ELIGIBLE_STATUSES),
        # источники трафика недоступны ни у одного источника
        "src_foryou":             [],
        "src_hashtag":            [],
        "src_sound":              [],
        "src_search":             [],
        "src_profile":            [],
    },
}

# ── метрики baseline ────────────────────────────────────────────────────────
COUNT_METRICS = ["views", "reach", "likes", "comments", "shares", "favorites"]
RATE_NUMERATORS = ["likes", "comments", "shares", "favorites"]
WATCH_METRICS = ["avg_view_time_sec", "total_time_watched_sec", "completion_rate"]
PROPERTY_METRICS = ["duration_sec"]

# Явно зафиксировано отсутствие данных, а не забыто.
PERMANENTLY_UNAVAILABLE = {
    "per_second_retention": "ни один доступный источник не отдаёт удержание по секундам",
    "traffic_sources": "поля Metricool TKPO16-TKPO21 возвращают NULL, в TIKBA их нет",
}

# ── перцентили ──────────────────────────────────────────────────────────────
PERCENTILE_METHOD = "linear_interpolation_between_closest_ranks.v1"
PERCENTILES = {"p25": 0.25, "p50": 0.50, "p75": 0.75, "p90": 0.90}
# p90 на крошечной выборке — ложная точность: он вырождается в максимум.
P90_MIN_N = 10

# ── выбросы ─────────────────────────────────────────────────────────────────
# Правило аналитического слоя. Оно НЕ заменяет правило выбросозависимости
# закономерностей из Phase 0 (MAD, k=5): у них разные задачи и разные имена.
OUTLIER_RULE_ID = "iqr.1.5.v1"
IQR_MULTIPLIER = 1.5

# ── размер выборки ──────────────────────────────────────────────────────────
# Переиспользован утверждённый порог FACT. Аналитика печатает числа при любом
# n, но помечает выборку: метка — не запрет, а предупреждение.
MIN_SAMPLE_REQUIRED_DEFAULT = 25
MIN_SAMPLE_SOURCE = "утверждённый порог FACT (content_dna.min_sample_required)"

# ── согласованность времени просмотра ───────────────────────────────────────
# Производное значение хранится ОТДЕЛЬНО и фактическое не подменяет.
WATCH_DERIVATION_TOLERANCE = 1e-4

# ── окна baseline аккаунта ──────────────────────────────────────────────────
# Промотировать backfill в 7d+ запрещено: фактический возраст не доказан.
BASELINE_WINDOWS = {
    "backfill":      {"age_bucket": "backfill", "status": "observed"},
    "24h_baseline":  {"age_bucket": "T+24h",    "status": "no_data"},
    "48h_baseline":  {"age_bucket": "T+48h",    "status": "no_data"},
    "7d_live_baseline": {"age_bucket": "T+7d",  "status": "no_data"},
}
WINDOW_NO_DATA_REASON = (
    "в выборке нет роликов с этим возрастным бакетом: все наблюдения относятся "
    "к роликам, опубликованным до подключения источников (Phase 0 остаётся "
    "BLOCKED — DATA COVERAGE)")


def eligible_statuses(metric):
    return frozenset(ANALYTICS_ELIGIBILITY_POLICY["per_metric"].get(
        metric, ANALYTICS_ELIGIBILITY_POLICY["default"]))


def is_eligible(metric, status):
    return status in eligible_statuses(metric)


def ineligibility_reason(status):
    return INELIGIBLE_REASONS.get(status, "not_eligible_for_metric")


def percentile(values_sorted, p):
    """Линейная интерполяция между соседними рангами.

    k = p*(n-1); при дробном k значение линейно интерполируется между
    соседями. Метод зафиксирован в PERCENTILE_METHOD.
    """
    n = len(values_sorted)
    if n == 0:
        return None
    if n == 1:
        return float(values_sorted[0])
    k = (n - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(values_sorted[int(k)])
    return float(values_sorted[lo]) * (hi - k) + float(values_sorted[hi]) * (k - lo)


def safe_ratio(numerator, denominator):
    """Деление с честным NULL. Нулевой или отсутствующий знаменатель -> None."""
    if numerator is None or denominator is None:
        return None
    if float(denominator) == 0.0:
        return None
    return float(numerator) / float(denominator)


def sample_status(n, min_required=None):
    m = MIN_SAMPLE_REQUIRED_DEFAULT if min_required is None else min_required
    return "sufficient_sample" if n >= m else "insufficient_sample"
