"""Эталонные значения, привязанные к набору данных. Живут только здесь.

Регрессионные тесты сравнивают выход конвейера с этими значениями ТОЧНЫМ
равенством — строгость та же, что была, когда числа были вписаны прямо в
тесты. Изменилось одно: раньше одни и те же хеши были скопированы в четыре
файла, и обновление данных ломало четыре набора тестов разом. Теперь эталон
один, и рядом записано, на каком сырье он посчитан.

Значения ОБЯЗАНЫ меняться при новом сырье: хеш аналитики по другим роликам
другой. Опасно не это, а пересчёт эталона «под ответ». Поэтому порядок такой:

  1. добавить сырьё, НЕ меняя код;
  2. прогнать проверку: провалиться вправе только значения этого файла.
     Любой другой провал — дефект, его чинят отдельно и называют в коммите;
  3. внести новые значения сюда, а прежние — в HISTORY с причиной сдвига.

Тест, описывающий конкретный проведённый эксперимент (EXP-004), сюда не
относится: он ограничен сырьём своего эксперимента и от обновлений не
зависит (tests/test_exp004_regression.py, EXP004_RAW).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Сырьё, на котором посчитан эталон. Расхождение — первое, что видно в тесте.
RAW_SET = frozenset({
    "2026-09-17_metricool_posts.json",
    "2026-09-17_supermetrics_videos.json",
    "2026-09-17T095902Z_metricool_posts_r2.json",
    "2026-09-17T095902Z_supermetrics_videos_r2.json",
    "2026-09-17T100216Z_metricool_posts_r3.json",
    "2026-09-17T100216Z_supermetrics_videos_r3a_REPLAY.json",
    "2026-09-17T100244Z_supermetrics_videos_r3b.json",
    "2026-10-08T210821Z_metricool_posts_r4.json",
    "2026-10-08T210821Z_metricool_traffic_r4.json",
})

N_VIDEOS = 17

# Хеши вышестоящих слоёв (insights.run.input_hashes)
UPSTREAM = {
    "analytics": "88800c0b779aef6ccfbc3398752be30760d54d81bdb8bc9876648ee5ad4c0846",
    "features": "631999988be9d2302c5e098217627f8616c144598b55f674f5027ff1725780b3",
    "reconciliation": "fd98b3114568b023f83c132ed9d544b1b02f69d7c575dbce0d78bdbf3303b645",
}

INSIGHTS_HASH = "35e4320dc54ed67f13844be5672ae849340e6ad2ee49edc7e128073a5c2867a8"
N_INSIGHTS, N_BLOCKED = 14, 31

# Признаки feature-policy-1.0.0: 45 имён на ролик
FEATURE_ROWS = 765
TIER0_ROWS, TIER05_ROWS = 510, 255
FEATURE_STATUSES = {"derived": 193, "insufficient_baseline": 51,
                    "observed": 347, "unavailable": 174}

HISTORY = [
    {"raw": "R1-R3, 2026-09-17", "n_videos": 16,
     "upstream": {
         "analytics": "52fa355f77987c7aa8479e18a89616cd6c2b8e36dfaf49c477e2eca7b35d474f",
         "features": "71075aaf4170be7b6b43abde9126227e621274ed6570839e5b37f23b42f3bd6b",
         "reconciliation": "f52205acef19ba5082f192e7206ff9faa3917cde3be0e970f7afb4e0202506cf"},
     "insights_hash": "d22e195409886f8e9fc563600b4ad61eb4aac0db299978390550cbcc13e92802",
     "insights": (11, 28), "feature_rows": 720, "tiers": (480, 240),
     "feature_statuses": {"derived": 192, "insufficient_baseline": 48,
                          "observed": 336, "unavailable": 144}},
    {"raw": "R4, 2026-10-08 (Metricool; Supermetrics — TRIAL_EXPIRED)",
     "why": "17-й ролик (опубликован 2026-09-19, первый после подключения); "
            "17 новых наблюдений Metricool без пары во втором источнике — все "
            "untested_overlap; подписи трёх роликов пришли длиннее. "
            "analytics_hash сдвинут ещё и правкой WINDOW_NO_DATA_REASON: прежний "
            "текст утверждал, что все ролики опубликованы до подключения, — "
            "с R4 это неверно. Без этой правки было бы 2b651944d2c9940d…"},
]


def raw_set_now():
    return frozenset(p.name for p in (ROOT / "data" / "raw").glob("*.json"))


def raw_set_diff():
    """(лишние, недостающие) относительно RAW_SET."""
    now = raw_set_now()
    return sorted(now - RAW_SET), sorted(RAW_SET - now)
