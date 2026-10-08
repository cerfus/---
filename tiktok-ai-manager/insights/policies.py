#!/usr/bin/env python3
"""Политика выводов Phase 5."""
INSIGHTS_POLICY_VERSION = "insights-policy-1.0.0"

# Тот же утверждённый порог: вывод уровня FACT требует выборки не меньше.
MIN_SAMPLE_FOR_FACT = 25

# Покрытие ниже этого уровня порождает вывод о качестве данных.
COVERAGE_WARN_THRESHOLD = 0.80

# Порог для выпуска гипотезы из измеренной связи. Значение конвенциональное
# и выбрано явно: без него генератор выпускал бы гипотезу на каждую связь,
# включая те, что неотличимы от шума (rho=+0.028 при p=0.92). Значимость
# приближённая, поэтому порог — фильтр против мусора, а не доказательство.
HYPOTHESIS_MAX_P = 0.05

# ── механически зависимые пары метрик ───────────────────────────────────────
# Пара попадает сюда, когда связь между метриками следует из ИХ УСТРОЙСТВА,
# а не из поведения аудитории. Статистику по такой паре считать и показывать
# можно; строить на ней содержательный вывод — нельзя.
#
# Реестр расширяется только с доказательством. Ни одна пара не добавляется
# «на всякий случай».
MECHANICAL_DEPENDENCIES = {
    frozenset({"duration_sec", "completion_rate"}): {
        "rule_id": "mechdep.duration_completion.v1",
        # Механизм записан по проверке на данных, а не по общему рассуждению.
        # ВАЖНО: completion_rate НЕ равен avg_view_time / duration — проверено
        # на всех 16 роликах, совпадений 0. Тождества здесь нет.
        "mechanism":
            "completion_rate — доля зрителей, досмотревших ролик целиком, то есть "
            "функция выживания, взятая в точке duration_sec. При неизменном "
            "распределении абсолютного времени просмотра эта доля убывает с ростом "
            "длительности по построению метрики.",
        "evidence":
            "EXP-004: абсолютное время просмотра почти не зависит от длительности "
            "(медиана 6,25 с, диапазон 4,28-7,50 с без 80-секундного ролика), тогда "
            "как длительность меняется от 7,2 до 80,5 с.",
        "not_an_identity":
            "completion_rate != avg_view_time_sec / duration_sec: проверено на "
            "16 роликах, совпадений 0.",
    },
}

# Кандидаты, НЕ внесённые в реестр без отдельного решения владельца.
MECHANICAL_CANDIDATES_NOT_REGISTERED = {
    frozenset({"duration_sec", "avg_view_time_sec"}):
        "avg_view_time_sec ограничено сверху длительностью по определению "
        "(нарушений 0/16): пара механически ограничена сверху. Не внесена — "
        "владелец просил не добавлять пары без отдельного решения.",
}

MECHANICAL_REASON_CODE = "mechanically_dependent"
MECHANICAL_CANDIDATE_REASON_CODE = "mechanical_candidate_undecided"

# Куда вывод о связи может быть повышен. Список закрыт: любой новый
# потребитель связи обязан появиться здесь, иначе он не пройдёт проверку.
PROMOTION_TARGETS = ("FACT", "HYPOTHESIS", "RECOMMENDATION",
                     "content_dna_evidence", "experiment_basis")

# Что разрешено НЕРЕШЁННОМУ кандидату. Гипотеза разрешена сознательно: это
# честный промежуточный статус — «связь измерена, природа не выяснена».
# Всё, что опирается на связь как на установленную (рекомендация, факт,
# доказательство Content DNA, основание эксперимента), требует решения
# владельца по механической природе пары и потому закрыто.
CANDIDATE_ALLOWED_TARGETS = ("HYPOTHESIS",)


class MechanicalLeakage(Exception):
    """Попытка повысить механически зависимую связь до содержательного вывода."""


def canonical_pair(x_metric, y_metric):
    """Пара в каноническом порядке. Нужна там, где frozenset неприменим:
    в SQL, в JSON и в сообщениях."""
    return tuple(sorted((x_metric, y_metric)))


def mechanical_dependency(x_metric, y_metric):
    """Запись реестра для пары метрик либо None."""
    return MECHANICAL_DEPENDENCIES.get(frozenset({x_metric, y_metric}))


def mechanical_candidate(x_metric, y_metric):
    """Причина, по которой пара числится нерешённым кандидатом, либо None."""
    return MECHANICAL_CANDIDATES_NOT_REGISTERED.get(
        frozenset({x_metric, y_metric}))


def association_claim_allowed(x_metric, y_metric):
    """Можно ли строить содержательный вывод на связи этой пары.

    Запрет распространяется сразу на FACT, HYPOTHESIS и RECOMMENDATION, а
    также на использование связи как доказательства для Content DNA и как
    основания эксперимента: все они опираются на содержательность связи,
    которой у механически зависимой пары нет.
    """
    dep = mechanical_dependency(x_metric, y_metric)
    if dep:
        return False, MECHANICAL_REASON_CODE, dep
    return True, None, None


def promotion_allowed(x_metric, y_metric, target):
    """Разрешено ли повысить связь пары до конкретного потребителя.

    Единственная точка решения. Любой слой — генератор выводов, сборка
    Content DNA, регистрация эксперимента — обязан спросить здесь, иначе
    статистическая значимость останется единственным условием попадания
    связи в содержательный вывод, а это ровно тот путь, который реестр
    и закрывает.

    Возвращает (allowed, reason_code, detail).
    """
    if target not in PROMOTION_TARGETS:
        raise ValueError(f"UNKNOWN_PROMOTION_TARGET: {target!r}; "
                         f"известны {PROMOTION_TARGETS}")
    dep = mechanical_dependency(x_metric, y_metric)
    if dep:
        return False, MECHANICAL_REASON_CODE, {
            "pair": list(canonical_pair(x_metric, y_metric)),
            "target": target, "rule_id": dep["rule_id"],
            "mechanism": dep["mechanism"], "evidence": dep["evidence"],
            "not_an_identity": dep["not_an_identity"],
            "forbidden": list(PROMOTION_TARGETS)}
    cand = mechanical_candidate(x_metric, y_metric)
    if cand and target not in CANDIDATE_ALLOWED_TARGETS:
        return False, MECHANICAL_CANDIDATE_REASON_CODE, {
            "pair": list(canonical_pair(x_metric, y_metric)),
            "target": target, "candidate_note": cand,
            "allowed_targets": list(CANDIDATE_ALLOWED_TARGETS),
            "forbidden": [t for t in PROMOTION_TARGETS
                          if t not in CANDIDATE_ALLOWED_TARGETS],
            "decision_required_from": "owner"}
    return True, None, None


def assert_promotion_allowed(x_metric, y_metric, target):
    """Жёсткая форма promotion_allowed: нарушение — исключение, не запись в лог."""
    ok, code, detail = promotion_allowed(x_metric, y_metric, target)
    if not ok:
        raise MechanicalLeakage(
            f"{code}: {x_metric} ~ {y_metric} -> {target}")
    return True


# Статусы сверки, при которых метрика не может обосновать FACT.
FACT_BLOCKING_STATUSES = ("both_discrepancy", "untested_overlap", "unavailable")

CLAIM_TYPES = ("FACT", "HYPOTHESIS", "RECOMMENDATION", "ANOMALY", "DATA_QUALITY")

# Разделы ежедневного отчёта. Ни один не опускается: пустой раздел печатается
# с причиной, иначе отчёт создаёт видимость полноты.
DAILY_SECTIONS = [
    ("what_changed", "1. Что изменилось"),
    ("important_metrics", "2. Ключевые метрики"),
    ("new_evidence", "3. Новые подтверждения"),
    ("new_hypotheses", "4. Новые гипотезы"),
    ("experiments", "5. Эксперименты"),
    ("content_recommendations", "6. Рекомендации по контенту"),
    ("data_quality", "7. Проблемы качества данных"),
    ("blocked_conclusions", "8. Заблокированные выводы"),
]
