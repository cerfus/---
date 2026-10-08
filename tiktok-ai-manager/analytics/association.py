#!/usr/bin/env python3
"""Измерение связей между метриками. ТОЛЬКО ЧИСЛА.

Модуль не формирует закономерностей, не называет наблюдаемое паттерном,
не утверждает причинность и ничего не рекомендует. Он считает
коэффициент, размер выборки и приближённую значимость — и перечисляет
ограничения, при которых эти числа осмысленны.

Значимость считается детерминированно через t-приближение: перестановочный
тест потребовал бы генератора случайных чисел, а аналитический слой обязан
быть полностью воспроизводимым.
"""
import math

from analytics import policies as A

ASSOCIATION_METHOD = "spearman_rho.v1"
SIGNIFICANCE_METHOD = "student_t_approximation.v1"
MIN_N_FOR_ASSOCIATION = 5


def _ranks(values):
    """Ранги со средним для связок — детерминированно."""
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = math.sqrt(sum((v - mx) ** 2 for v in rx) * sum((v - my) ** 2 for v in ry))
    return num / den if den else 0.0


def _betacf(a, b, x, itmax=200, eps=3e-12):
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(rho, n):
    """Двусторонняя приближённая значимость. Асимптотика, не точный тест."""
    if n <= 2 or abs(rho) >= 1.0:
        return None
    t = rho * math.sqrt((n - 2) / (1.0 - rho * rho))
    df = n - 2
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def measure(video_records, x_metric, y_metric):
    """Одно измерение связи. Выводов не делает."""
    pairs = [(r.get(x_metric), r.get(y_metric)) for r in video_records]
    used = [(float(a), float(b)) for a, b in pairs if a is not None and b is not None]
    n = len(used)
    base = {
        "x_metric": x_metric, "y_metric": y_metric,
        "n": n, "total_n": len(video_records),
        "coverage_ratio": A.safe_ratio(n, len(video_records)),
        "method": ASSOCIATION_METHOD,
        "significance_method": SIGNIFICANCE_METHOD,
        "min_sample_required": A.MIN_SAMPLE_REQUIRED_DEFAULT,
        "sample_status": A.sample_status(n),
        "interpretation": "not_interpreted",
        "causality_claim": False,
        "is_content_pattern": False,
        "limitations": [
            "связь измерена на одной выборке и причинности не показывает",
            "значимость получена асимптотическим приближением, а не точным тестом",
            f"n={n} при пороге {A.MIN_SAMPLE_REQUIRED_DEFAULT}",
            "все ролики относятся к бакету backfill: ранние возрасты не наблюдались",
        ],
        "engine_version": "association-1.0.0",
        "policy_version": A.ANALYTICS_POLICY_VERSION,
    }
    if n < MIN_N_FOR_ASSOCIATION:
        return {**base, "rho": None, "p_two_sided": None,
                "status": f"insufficient_n_lt_{MIN_N_FOR_ASSOCIATION}"}
    xs = [a for a, _ in used]
    ys = [b for _, b in used]
    rho = spearman(xs, ys)
    return {**base, "rho": rho, "p_two_sided": t_two_sided_p(rho, n),
            "status": "measured"}


# Пары, которые Phase 3 измеряет. Список фиксирован здесь, а не собирается
# перебором: перебор всех пар метрик — это ловля совпадений.
MEASURED_PAIRS = [
    ("duration_sec", "completion_rate"),
    ("duration_sec", "avg_view_time_sec"),
    ("duration_sec", "views"),
    ("duration_sec", "engagement_rate"),
]


def measure_all(video_records):
    return [measure(video_records, x, y) for x, y in MEASURED_PAIRS]
