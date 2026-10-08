#!/usr/bin/env python3
"""Ответ коннектора Metricool → сырьё raw/v1 с метаданными. Повторяемо.

    python -m normalize.import_export posts    RESP.json --from F --to T --round R5
    python -m normalize.import_export traffic  RESP.json --from F --to T --round R5
    python -m normalize.import_export besttime RESP.json --from F --to T --tz Europe/Moscow --round R5
    python -m normalize.import_export brand    RESP.json --round R5

Раунд R4 собирался руками, и формат держался на внимательности. Здесь он
закреплён кодом: запрос записывается дословно, строки ответа — как есть,
перезапись существующего файла запрещена (сырьё не правится никогда).

Что из этого — наблюдение, решает вид выгрузки, а не человек:
  posts    — наблюдение (usable_as_observation=true), его читает normalize;
  traffic  — доказательство для EXP-003, не наблюдение;
  besttime — оценка Metricool активности аудитории по часам, не наблюдение;
  brand    — настройки бренда (часовой пояс), не наблюдение.

Исключение из «как есть» одно и названо: у настроек бренда сохраняются
только поля, нужные проекту. Email владельца и подписанная ссылка на
аватар в репозиторий не попадают.

Ответ постов, построчно совпадающий с последней выгрузкой, не
записывается: Metricool не отдаёт метку свежести, и кэш неотличим от
«ничего не изменилось». Записанный, он стал бы наблюдениями с новым
временем — утверждением «в этот момент было столько», которого ответ не
доказывает. Команда так и пишет: «НОВЫХ ДАННЫХ НЕТ».
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
BRAND_ID = 7004662

POSTS_METRICS = ["TKPO02", "TKPO03", "TKPO05", "TKPO06", "TKPO07", "TKPO08",
                 "TKPO09", "TKPO10", "TKPO11", "TKPO13", "TKPO15"]
POSTS_HEADER = ["published", "url", "description", "duration_sec", "views", "likes",
                "comments", "shares", "reach", "completion_rate", "avg_view_time_sec"]
TRAFFIC_METRICS = ["TKPO02", "TKPO03", "TKPO16", "TKPO17", "TKPO18", "TKPO19",
                   "TKPO20", "TKPO21"]
# соответствие кодов TKPO16-21 каналам трафика в проекте не проверено
TRAFFIC_HEADER = ["published", "url"] + TRAFFIC_METRICS[2:]
BRAND_FIELDS = ("id", "label", "timezone", "joinDate", "firstConnectionDate")

CACHE_NOTE = ("Metricool не сообщает признака кэша и не отдаёт метки актуальности. "
              "Свежесть ответа НЕИЗВЕСТНА.")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(response, width):
    rows = response.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("в ответе нет строк (rows)")
    bad = [i for i, r in enumerate(rows) if not isinstance(r, list) or len(r) != width]
    if bad:
        raise ValueError(f"строки {bad[:5]} не из {width} колонок — запрос был другим?")
    return rows


def build(kind, response, round_, fetched_at=None, date_from=None, date_to=None, tz=None):
    """Документ raw/v1. Ничего не пишет."""
    fetched_at = fetched_at or _now()
    meta = {"schema": "raw/v1", "round": round_, "fetched_at": fetched_at,
            "run_id": f"{round_.lower()}-metricool-{kind}", "source": "metricool"}
    if kind == "posts":
        rows = _rows(response, len(POSTS_HEADER))
        meta.update(tool="getAnalyticsDataByMetrics", usable_as_observation=True,
                    cache_note=CACHE_NOTE, row_count=len(rows),
                    request={"brandId": BRAND_ID, "from": date_from, "to": date_to,
                             "metrics": POSTS_METRICS})
        return {"_meta": meta, "header": POSTS_HEADER, "rows": rows}
    if kind == "traffic":
        rows = _rows(response, len(TRAFFIC_HEADER))
        nonnull = sum(1 for r in rows for v in r[2:] if v is not None)
        meta.update(tool="getAnalyticsDataByMetrics", usable_as_observation=False,
                    purpose="EXP-003: источники трафика у роликов после подключения",
                    result=f"ненулевых значений TKPO16-21: {nonnull}",
                    row_count=len(rows),
                    header_note="соответствие кодов TKPO16-21 каналам трафика не проверено",
                    request={"brandId": BRAND_ID, "from": date_from, "to": date_to,
                             "metrics": TRAFFIC_METRICS})
        return {"_meta": meta, "header": TRAFFIC_HEADER, "rows": rows}
    if kind == "besttime":
        data = response.get("data")
        if not isinstance(data, list) or not all(
                "dayOfWeek" in d and "bestTimesByHour" in d for d in data):
            raise ValueError("ответ не похож на getBestTimeToPostByNetwork")
        if not tz:
            raise ValueError("нужен часовой пояс запроса (--tz)")
        meta.update(tool="getBestTimeToPostByNetwork", usable_as_observation=False,
                    purpose="оценка Metricool: активность аудитории по дням и часам",
                    semantics="dayOfWeek 1=понедельник … 7=воскресенье; часы — в поясе "
                              "timezone; value — чем больше, тем лучше по оценке Metricool. "
                              "Это модель источника, а не наблюдение просмотров.",
                    request={"brandId": str(BRAND_ID), "fromDate": date_from,
                             "toDate": date_to, "timezone": tz, "socialNetwork": "tiktok"})
        return {"_meta": meta, "data": data}
    if kind == "brand":
        data = response.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError("ответ не похож на getBrandSettings")
        kept = [{k: b.get(k) for k in BRAND_FIELDS} for b in data]
        meta.update(tool="getBrandSettings", usable_as_observation=False,
                    purpose="часовой пояс бренда для показа времени",
                    minimized=f"сохранены только поля {', '.join(BRAND_FIELDS)}; email "
                              "владельца и подписанная ссылка на аватар не сохраняются",
                    request={})
        return {"_meta": meta, "data": kept}
    raise ValueError(f"неизвестный вид выгрузки: {kind}")


def filename(doc, kind):
    stamp = doc["_meta"]["fetched_at"].replace("-", "").replace(":", "")
    stamp = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}{stamp[8:]}"
    return f"{stamp}_metricool_{kind}_{doc['_meta']['round'].lower()}.json"


def write(doc, kind, raw_dir=RAW):
    path = Path(raw_dir) / filename(doc, kind)
    if path.exists():
        raise FileExistsError(f"{path.name} уже есть — сырьё не перезаписывается")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return path


def same_as_latest(doc, raw_dir=None):
    """Имя последней выгрузки постов Metricool, если строки нового ответа с
    ней совпадают построчно; иначе None. Последняя — по моменту получения."""
    raw_dir = Path(raw_dir or RAW)
    latest = None
    for f in raw_dir.glob("*_metricool_posts*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        meta = d.get("_meta", {})
        if meta.get("usable_as_observation") is False:
            continue
        at = meta.get("fetched_at") or meta.get("pulled_at") or ""
        if latest is None or at > latest[0]:
            latest = (at, f, d)
    if latest and latest[2].get("rows") == doc.get("rows"):
        return latest[1].name
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ответ Metricool → data/raw (raw/v1).")
    ap.add_argument("kind", choices=("posts", "traffic", "besttime", "brand"))
    ap.add_argument("response", help="файл с ответом коннектора (JSON)")
    ap.add_argument("--round", required=True, help="раунд, например R5")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--tz")
    ap.add_argument("--fetched-at", help="момент запроса UTC, по умолчанию — сейчас")
    args = ap.parse_args(argv)
    response = json.loads(Path(args.response).read_text(encoding="utf-8"))
    doc = build(args.kind, response, args.round, args.fetched_at,
                args.date_from, args.date_to, args.tz)
    if args.kind == "posts":
        prev = same_as_latest(doc, RAW)
        if prev:
            print(f"НОВЫХ ДАННЫХ НЕТ: ответ построчно совпадает с {prev} — файл не "
                  "записан. Кэш это или за это время ничего не изменилось, Metricool "
                  "не сообщает; наблюдение с новым временем утверждало бы больше, чем "
                  "известно.")
            return 0
    path = write(doc, args.kind, RAW)
    shown = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path
    print(f"записано: {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
