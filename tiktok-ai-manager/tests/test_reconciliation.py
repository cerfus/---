#!/usr/bin/env python3
"""Тесты правила both_lagged и политики FACT.

ВНИМАНИЕ: все значения здесь СИНТЕТИЧЕСКИЕ фикстуры для проверки правила.
Они намеренно лежат вне data/ и никогда не смешиваются с наблюдениями.

Запуск: python3 tests/test_reconciliation.py   (или через pytest)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "EXP-004"))
import reconciliation as rec   # noqa: E402

T1 = "2026-09-17T09:00:00Z"
T2 = "2026-09-17T10:00:00Z"


def series(*pairs):
    return list(pairs)


# --- LAG-1 ------------------------------------------------------------------
def test_lag_1_proven_lag():
    """Canonical 100 -> 110, lagging 100 -> 100 => both_lagged."""
    r = rec.classify("views", {
        "supermetrics": series((T1, 100), (T2, 110)),
        "metricool":    series((T1, 100), (T2, 100)),
    }, T2)
    assert r["reconciliation_status"] == "both_lagged", r
    assert r["canonical_source"] == "supermetrics"
    assert r["lagging_source"] == "metricool"
    assert r["lag_basis"] == "lagging_value_equals_previous_canonical_value"
    assert r["lag_evidence"]["canonical_t1_value"] == 100
    assert r["lag_evidence"]["canonical_t2_value"] == 110
    assert r["competing_explanation"]
    return "LAG-1 both_lagged, canonical=supermetrics"


# --- LAG-2 ------------------------------------------------------------------
def test_lag_2_partial_value_is_not_lag():
    """Canonical 100 -> 110, lagging 100 -> 105 => НЕ both_lagged.

    105 не равно ни одному предыдущему каноническому значению, значит это не
    доказанное отставание, а расхождение неизвестной природы.
    """
    r = rec.classify("views", {
        "supermetrics": series((T1, 100), (T2, 110)),
        "metricool":    series((T1, 100), (T2, 105)),
    }, T2)
    assert r["reconciliation_status"] != "both_lagged", r
    assert r["reconciliation_status"] == "both_discrepancy", r
    return "LAG-2 both_discrepancy (105 не равно ни одному прошлому канону)"


# --- LAG-3 ------------------------------------------------------------------
def test_lag_3_lagging_ahead_is_discrepancy():
    """Canonical 100 -> 110, lagging 100 -> 115 => both_discrepancy.

    Условие 4: отстающий не может опережать канонический.
    """
    r = rec.classify("views", {
        "supermetrics": series((T1, 100), (T2, 110)),
        "metricool":    series((T1, 100), (T2, 115)),
    }, T2)
    assert r["reconciliation_status"] == "both_discrepancy", r
    return "LAG-3 both_discrepancy (отстающий опережает канонический)"


# --- LAG-4 ------------------------------------------------------------------
def test_lag_4_floor_stays_expected_transform():
    """duration с правилом floor() остаётся both_expected_transform."""
    r = rec.classify("duration_sec", {
        "supermetrics": series((T1, 10.2), (T2, 10.2)),
        "metricool":    series((T1, 10.0), (T2, 10.0)),
    }, T2)
    assert r["reconciliation_status"] == "both_expected_transform", r
    return "LAG-4 both_expected_transform (floor, не lag)"


def test_lag_4b_floor_not_reclassified_even_if_lag_shape():
    """Даже если форма данных похожа на отставание, floor() имеет приоритет."""
    r = rec.classify("duration_sec", {
        "supermetrics": series((T1, 7.0), (T2, 7.834)),
        "metricool":    series((T1, 7.0), (T2, 7.0)),
    }, T2)
    assert r["reconciliation_status"] == "both_expected_transform", r
    return "LAG-4b floor() имеет приоритет над формой отставания"


# --- LAG-5 ------------------------------------------------------------------
def test_lag_5_single_observation_insufficient():
    """Единичное совпадение без временной последовательности недостаточно."""
    r = rec.classify("views", {
        "supermetrics": series((T2, 110)),      # только одно наблюдение
        "metricool":    series((T2, 100)),
    }, T2)
    assert r["reconciliation_status"] != "both_lagged", r
    assert r["reconciliation_status"] == "both_discrepancy", r
    return "LAG-5 both_discrepancy (истории канона недостаточно)"


# --- LAG-6 ------------------------------------------------------------------
def test_lag_6_fact_allowed_on_proven_lag():
    assert rec.fact_allowed("both_lagged") is True
    return "LAG-6 FACT разрешён при both_lagged"


# --- LAG-7 ------------------------------------------------------------------
def test_lag_7_fact_blocked_on_discrepancy():
    assert rec.fact_allowed("both_discrepancy") is False
    assert rec.fact_allowed("untested_overlap") is False
    assert rec.fact_allowed("unavailable") is False
    assert rec.fact_allowed("both_matched") is True
    assert rec.fact_allowed("both_expected_transform") is True
    assert rec.fact_allowed("single_source") is True
    return "LAG-7 FACT запрещён при discrepancy / untested_overlap / unavailable"


# --- сторожевые случаи ------------------------------------------------------
def test_guard_metric_not_in_whitelist():
    """Метрика вне белого списка не получает both_lagged даже при той же форме."""
    assert "reach" in rec.MONOTONIC_CANDIDATES_NOT_ENABLED
    r = rec.classify("reach", {
        "supermetrics": series((T1, 100), (T2, 110)),
        "metricool":    series((T1, 100), (T2, 100)),
    }, T2)
    assert r["reconciliation_status"] == "both_discrepancy", r
    return "GUARD метрика вне белого списка -> both_discrepancy"


def test_guard_untested_overlap():
    """Метрика есть в обоих источниках, запрошен один -> untested_overlap."""
    r = rec.classify("comments", {
        "metricool": series((T1, 5), (T2, 5)),
    }, T2)
    assert r["reconciliation_status"] == "untested_overlap", r
    return "GUARD untested_overlap при непроверенном пересечении"


def test_guard_single_source():
    """Метрику отдаёт только один источник -> single_source, FACT разрешён."""
    r = rec.classify("completion_rate", {
        "supermetrics": series((T1, 0.30), (T2, 0.31)),
    }, T2)
    assert r["reconciliation_status"] == "single_source", r
    assert rec.fact_allowed(r["reconciliation_status"]) is True
    return "GUARD single_source, FACT разрешён"


def test_guard_unavailable():
    r = rec.classify("src_foryou", {}, T2)
    assert r["reconciliation_status"] == "unavailable", r
    assert rec.fact_allowed(r["reconciliation_status"]) is False
    return "GUARD unavailable, FACT запрещён"


def test_guard_equal_values_matched():
    r = rec.classify("views", {
        "supermetrics": series((T1, 100), (T2, 110)),
        "metricool":    series((T1, 100), (T2, 110)),
    }, T2)
    assert r["reconciliation_status"] == "both_matched", r
    return "GUARD равные значения -> both_matched"


def test_guard_canonical_must_increase():
    """Если канонический не вырос, отставание не доказано."""
    r = rec.classify("views", {
        "supermetrics": series((T1, 110), (T2, 110)),
        "metricool":    series((T1, 100), (T2, 100)),
    }, T2)
    assert r["reconciliation_status"] == "both_discrepancy", r
    return "GUARD канонический не вырос -> both_discrepancy"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            msg = fn()
            print(f"  [OK]   {name:<46} {msg or ''}")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {name:<46} {e}")
    print(f"\nтестов: {len(tests)} | провалов: {failed}")
    sys.exit(1 if failed else 0)
