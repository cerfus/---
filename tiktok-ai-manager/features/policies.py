#!/usr/bin/env python3
"""Политика признаков. Версионирована: смена правил требует новой версии,
а не переписывания прошлых вычислений.

Phase 4 отвечает ровно на один вопрос: какие характеристики ролика можно
надёжно извлечь из доступных данных. Она не оценивает признаки, не
ранжирует их и не делает выводов.
"""
FEATURE_POLICY_VERSION = "feature-policy-1.0.0"

TIER_0 = "tier_0"        # прямое наблюдение без преобразования
TIER_05 = "tier_0.5"     # производное: детерминированное правило поверх Tier 0

# ── границы бакетов длительности ────────────────────────────────────────────
# ТЕХНИЧЕСКИЕ корзины, а не рекомендация. Границы выбраны круглыми и НЕ
# подбирались под наблюдаемые результаты: подгонка границ под данные была бы
# скрытым паттерном. Изменение границ требует новой версии политики.
DURATION_BUCKETS = [
    ("very_short", None, 8.0),
    ("short",      8.0,  15.0),
    ("medium",     15.0, 30.0),
    ("long",       30.0, 60.0),
    ("very_long",  60.0, None),
]
DURATION_BUCKET_RULE = "fixed_technical_bins.v1"

# ── метрики, которые берутся как есть ───────────────────────────────────────
TIER_0_METRICS = ["views", "reach", "likes", "comments", "shares", "favorites",
                  "avg_view_time_sec", "completion_rate", "total_time_watched_sec",
                  "duration_sec"]

# ── производные ставки: числитель / views ───────────────────────────────────
RATE_FEATURES = {
    "like_rate": "likes", "comment_rate": "comments",
    "share_rate": "shares", "favorite_rate": "favorites",
}
ENGAGEMENT_COMPONENTS = ["likes", "comments", "shares", "favorites"]

# ── признаки, относительные к baseline ──────────────────────────────────────
# Включаются ТОЛЬКО при выполнении всех условий. На текущих данных ни одно
# из них не выполняется, и это штатный результат, а не пробел.
BASELINE_RELATIVE = {
    "views_vs_baseline": "views",
    "engagement_vs_baseline": "engagement_rate",
    "completion_vs_baseline": "completion_rate",
}
BASELINE_MIN_SAMPLE = 25          # тот же утверждённый порог
BASELINE_REQUIRE_LEAVE_ONE_OUT = True   # ролик не сравнивается с baseline, включающим его самого

# ── выбросы: правило Phase 3, не новое ──────────────────────────────────────
OUTLIER_FEATURES = {"views_outlier": "views",
                    "engagement_outlier": "engagement_rate",
                    "completion_outlier": "completion_rate"}

# ── флаги качества данных ───────────────────────────────────────────────────
# Отсутствие признака НЕ равно нулевому значению признака. Флаги существуют
# именно чтобы это различие было машиночитаемым.
QUALITY_FLAGS = ["has_views", "has_engagement", "has_watch_time", "has_completion",
                 "has_duration", "has_fresh_timestamp", "has_reconciliation"]

# ── признаки, требующие данных, которых нет ─────────────────────────────────
# Объявлены явно со статусом unavailable. Молчание здесь было бы хуже: позже
# кто-то решил бы, что признак просто забыли.
CONTENT_FEATURES_UNAVAILABLE = {
    "hook_type":       "нет видеофайла и нет структурированных аннотаций",
    "topic":           "нет видеофайла и нет структурированных аннотаций",
    "emotion":         "нет видеофайла и нет структурированных аннотаций",
    "visual_style":    "нет видеофайла и нет структурированных аннотаций",
    "character":       "нет видеофайла и нет структурированных аннотаций",
    "story_structure": "нет видеофайла и нет структурированных аннотаций",
    "cta":             "нет видеофайла и нет структурированных аннотаций",
    "editing_style":   "нет видеофайла и нет структурированных аннотаций",
    "traffic_source":  "источники трафика не отдаёт ни Metricool, ни Supermetrics",
}

# ── пригодность по статусу сверки ───────────────────────────────────────────
ELIGIBLE_RECONCILIATION = frozenset({"both_matched", "both_expected_transform",
                                     "both_lagged", "single_source"})
INELIGIBLE_REASON = {"both_discrepancy": "data_discrepant",
                     "untested_overlap": "data_untested",
                     "unavailable": "data_unavailable"}

FEATURE_STATUS = ("observed", "derived", "unavailable", "insufficient_baseline")


def duration_bucket(duration_sec):
    """Корзина длительности. Отрицательная и нулевая длительность невалидны."""
    if duration_sec is None:
        return None, "value_missing"
    d = float(duration_sec)
    if d <= 0:
        return None, "invalid_non_positive_duration"
    for name, lo, hi in DURATION_BUCKETS:
        if (lo is None or d >= lo) and (hi is None or d < hi):
            return name, None
    return None, "no_bucket_matched"


def baseline_relative_eligible(baseline_n, video_in_baseline, window_matches):
    """Гейт признаков, относительных к baseline. Возвращает (можно, причина).

    Порядок проверок фиксирован, чтобы причина отказа была воспроизводима.
    Сравнение ролика с baseline, в который он сам входит, — утечка: его
    собственное значение тянет baseline к себе.
    """
    if not window_matches:
        return False, "window_mismatch"
    if baseline_n is None or baseline_n < BASELINE_MIN_SAMPLE:
        return False, f"insufficient_baseline_n_lt_{BASELINE_MIN_SAMPLE}"
    if video_in_baseline and BASELINE_REQUIRE_LEAVE_ONE_OUT:
        return False, "self_inclusion_leakage"
    return True, None
