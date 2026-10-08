#!/usr/bin/env python3
"""Импорт выгрузок: формат raw/v1 закреплён кодом, а не внимательностью."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from normalize import import_export as IE, normalize as N      # noqa: E402

RESULTS = []
R4 = ROOT / "data" / "raw" / "2026-10-08T210821Z_metricool_posts_r4.json"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def raises(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True


def main():
    print("=== A. инструмент воспроизводит R4, собранный вручную ===")
    r4 = json.loads(R4.read_text(encoding="utf-8"))
    m = r4["_meta"]
    doc = IE.build("posts", {"rows": r4["rows"]}, "R4", m["fetched_at"],
                   m["request"]["from"], m["request"]["to"])
    check("A1 тот же header", doc["header"] == r4["header"])
    check("A2 те же строки, как есть", doc["rows"] == r4["rows"])
    check("A3 тот же запрос", doc["_meta"]["request"] == m["request"])
    check("A4 то же имя файла", IE.filename(doc, "posts") == R4.name, IE.filename(doc, "posts"))
    check("A5 посты — наблюдение", doc["_meta"]["usable_as_observation"] is True)

    print("\n=== B. защита от ошибок ===")
    check("B1 строки другой ширины отвергаются",
          raises(lambda: IE.build("posts", {"rows": [[1, 2, 3]]}, "R9"), ValueError))
    check("B2 пустой ответ отвергается",
          raises(lambda: IE.build("posts", {"rows": []}, "R9"), ValueError))
    check("B3 лучшее время без часового пояса отвергается",
          raises(lambda: IE.build("besttime", {"data": [{"dayOfWeek": 1,
                 "bestTimesByHour": []}]}, "R9"), ValueError))
    check("B4 неизвестный вид отвергается",
          raises(lambda: IE.build("likes", {}, "R9"), ValueError))
    tmp = Path(tempfile.mkdtemp())
    d = IE.build("posts", {"rows": r4["rows"]}, "R9", "2026-01-01T00:00:00Z", "a", "b")
    IE.write(d, "posts", tmp)
    check("B5 существующее сырьё не перезаписывается",
          raises(lambda: IE.write(d, "posts", tmp), FileExistsError))

    print("\n=== C. настройки бренда без личных данных ===")
    brand = {"data": [{"id": 1, "label": "x", "timezone": "Europe/Moscow",
                       "ownerUsername": "someone@example.com",
                       "image": "https://cdn/x.jpg?refresh_token=abc&x-signature=def",
                       "joinDate": {"dateTime": "2026-01-01T00:00:00", "timezone": "UTC"},
                       "firstConnectionDate": None}]}
    b = IE.build("brand", brand, "R9", "2026-01-01T00:00:00Z")
    text = json.dumps(b, ensure_ascii=False)
    check("C1 часовой пояс сохранён", b["data"][0]["timezone"] == "Europe/Moscow")
    check("C2 email владельца не сохранён", "someone@example.com" not in text)
    check("C3 подписанная ссылка не сохранена",
          "x-signature" not in text and "refresh_token" not in text)
    real = sorted((ROOT / "data" / "raw").glob("*_metricool_brand_*.json"))
    check("C4 в репозитории настройки бренда без email и подписи",
          real and not any("@" in p.read_text(encoding="utf-8").split('"data"')[1]
                           or "x-signature" in p.read_text(encoding="utf-8") for p in real))

    print("\n=== D. normalize читает только наблюдения ===")
    tmp = Path(tempfile.mkdtemp())
    IE.write(IE.build("posts", {"rows": r4["rows"]}, "R9", "2026-01-01T00:00:00Z", "a", "b"),
             "posts", tmp)
    IE.write(IE.build("traffic", {"rows": [r[:2] + [None] * 6 for r in r4["rows"]]}, "R9",
                      "2026-01-01T00:00:00Z", "a", "b"), "traffic", tmp)
    IE.write(IE.build("besttime", {"data": [{"dayOfWeek": 1, "bestTimesByHour": [
        {"hourOfDay": 0, "value": 1}]}]}, "R9", "2026-01-01T00:00:00Z", tz="UTC"),
        "besttime", tmp)
    IE.write(IE.build("brand", brand, "R9", "2026-01-01T00:00:00Z"), "brand", tmp)
    saved = N.RAW
    N.RAW = tmp
    try:
        kinds = [p.name.split("_metricool_")[1].split("_")[0] for p, *_ in N.read_raw()]
    finally:
        N.RAW = saved
    check("D1 из четырёх видов normalize берёт только посты", kinds == ["posts"], str(kinds))
    tr = IE.build("traffic", {"rows": [r[:2] + [None] * 6 for r in r4["rows"]]}, "R9")
    check("D2 итог проверки трафика записан в мета", "ненулевых значений TKPO16-21: 0"
          in tr["_meta"]["result"])

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
