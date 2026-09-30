#!/usr/bin/env python3
"""Регрессия Phase 2 на реальных данных EXP-004.

Цифры Phase 2 отличаются от зафиксированных в EXP-004. Код под старые числа
не подгонялся: отличия объяснены построчно ниже и проверяются тестом.
"""
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from normalize import observations
from reconcile import policies as P
from reconcile.engine import reconcile

RESULTS = []

# Зафиксировано в EXP-004 (модель логических срезов, метрики без наблюдений
# не перечислялись).
EXP004 = {"both_matched": 122, "single_source": 208, "untested_overlap": 80,
          "both_expected_transform": 32, "both_lagged": 6, "both_discrepancy": 0}

# Базовая линия Phase 2 (движок с временным сопоставлением и полным реестром
# метрик). Отличия от EXP-004 разобраны в EXPLAINED_DELTA.
PHASE2 = {"both_matched": 74, "single_source": 208, "untested_overlap": 112,
          "both_expected_transform": 16, "both_lagged": 6, "both_discrepancy": 0,
          "unavailable": 16}

EXPLAINED_DELTA = {
    "both_expected_transform": (
        -16, "duration_sec объявлена не зависящей от времени: один срез на ролик "
             "вместо двух. Свойство ролика не является временным рядом, и "
             "классифицировать его дважды значило бы удваивать доказательство."),
    "both_matched": (
        -48, "срез 00:00:00 имеет точность метки до даты. По правилу временного "
             "сопоставления такие пары уходят в untested_overlap: views, likes и "
             "shares по 16 роликов = 48."),
    "untested_overlap": (
        +32, "+48 за счёт среза с неточной меткой, −16 за счёт ухода duration_sec "
             "из временных срезов."),
    "unavailable": (
        +16, "src_foryou запрашивался у Metricool и вернул NULL. EXP-004 отбрасывал "
             "полностью пустые ряды до классификации, Phase 2 перечисляет их явно."),
    "both_lagged": (0, "не изменилось — критическая точка регрессии."),
    "single_source": (0, "не изменилось."),
    "both_discrepancy": (0, "не изменилось."),
}

LAGGED_VIDEOS = {"7643545925926915360", "7669369589641366817", "7683780392029015328"}


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<58}{detail}")


def main():
    res, content_hash = reconcile(observations.load())
    c = collections.Counter(r["classification"] for r in res)

    print("\nРАСПРЕДЕЛЕНИЕ")
    for k in sorted(set(PHASE2) | set(c)):
        check(f"{k} = {PHASE2.get(k, 0)}", c.get(k, 0) == PHASE2.get(k, 0),
              f"получено {c.get(k, 0)}")
    check("итого классификаций", len(res) == sum(PHASE2.values()), str(len(res)))

    print("\nДЕЛЬТА К EXP-004 ОБЪЯСНЕНА")
    for k, (expected_delta, why) in sorted(EXPLAINED_DELTA.items()):
        actual = c.get(k, 0) - EXP004.get(k, 0)
        check(f"{k}: дельта {expected_delta:+d}", actual == expected_delta,
              f"фактически {actual:+d}")

    print("\nКРИТИЧЕСКАЯ ТОЧКА: три ранее найденных расхождения")
    lagged = [r for r in res if r["classification"] == "both_lagged"]
    check("ровно 6 классификаций both_lagged", len(lagged) == 6, str(len(lagged)))
    check("это те же три ролика",
          {r["video_id"] for r in lagged} == LAGGED_VIDEOS)
    check("каждый ролик отстаёт на двух срезах",
          all(v == 2 for v in collections.Counter(
              r["video_id"] for r in lagged).values()))
    check("метрика везде views", {r["metric"] for r in lagged} == {"views"})
    check("канонический источник определён по наблюдениям, а не глобально",
          all(r["canonical_source"] == "supermetrics" for r in lagged))
    check("отстающий источник — metricool",
          all(r["lagging_source"] == "metricool" for r in lagged))
    check("у каждого отставания есть доказательство",
          all("lag_evidence" in r and r["lag_evidence"].get("canonical_t1") for r in lagged))
    check("у каждого отставания сохранено конкурирующее объяснение",
          all(r.get("competing_explanation") for r in lagged))
    check("порядок наблюдений выведен из монотонности, а не из неточной метки",
          all(r["lag_evidence"]["order_basis"] == "monotonic_value_order"
              for r in lagged))

    print("\nПОЛИТИКА FACT НА РЕАЛЬНЫХ ДАННЫХ")
    allowed = sum(v for k, v in c.items() if P.fact_allowed(k))
    blocked = sum(v for k, v in c.items() if not P.fact_allowed(k))
    check("FACT допустим на 304 классификациях", allowed == 304, str(allowed))
    check("FACT запрещён на 128 классификациях", blocked == 128, str(blocked))
    check("both_discrepancy отсутствует", c.get("both_discrepancy", 0) == 0)

    print("\nДЕТЕРМИНИЗМ")
    _, h2 = reconcile(observations.load())
    check("content_hash воспроизводится", content_hash == h2, content_hash[:16])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
