#!/usr/bin/env python3
"""EXP-004 — измерение фактической частоты обновления метрик.

Читает все сырые выгрузки раундов из data/raw/, превращает их в append-only
наблюдения и считает дельты между раундами. Ничего не сглаживает: расхождение
между источниками сохраняется как наблюдение, а не устраняется.

Использование:
    python3 experiments/EXP-004/exp004.py build     # raw -> observations.jsonl
    python3 experiments/EXP-004/exp004.py deltas    # изменения между раундами
    python3 experiments/EXP-004/exp004.py reconcile # сверка источников по раундам
    python3 experiments/EXP-004/exp004.py verify    # проверки воспроизводимости
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "data" / "raw"
OBS = ROOT / "data" / "observations" / "exp004_observations.jsonl"

# Раунды читаются из _meta самих файлов, а не из захардкоженной таблицы:
# признак пригодности задаёт сам файл, и забыть его нельзя.
R1_METRICOOL_HEADER = ["published", "url", "description", "duration_sec", "views",
                       "likes", "comments", "shares", "reach", "completion_rate",
                       "avg_view_time_sec"]

METRICS = ["views", "reach", "likes", "comments", "shares", "favorites",
           "completion_rate", "avg_view_time_sec", "total_time_watched_sec",
           "duration_sec", "src_foryou"]


def vid_from_url(u):
    return u.rstrip("/").split("/")[-1].split("?")[0]


def parse_published(s):
    if "-" in s:
        return s.replace(" ", "T")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}"


def canonical_source(raw_name):
    """Baseline писал 'Supermetrics MCP', поздние раунды — 'supermetrics'."""
    n = raw_name.strip().lower().replace(" mcp", "")
    if n not in ("supermetrics", "metricool"):
        raise ValueError(f"неизвестный источник: {raw_name!r}")
    return n


def resolve_header(doc, source):
    hdr = doc.get("header")
    if hdr:
        return hdr
    if source == "metricool":
        return R1_METRICOOL_HEADER
    raise ValueError("нет header и нет известной раскладки")


def observation_time(meta):
    """Метка актуальности источника важнее нашей метки получения.

    Supermetrics отдаёт cache_time — момент, на который данные верны.
    Наш fetched_at может отстоять от него на минуты.
    """
    if meta.get("observed_at_source"):
        return meta["observed_at_source"], "source_cache_time"
    at = meta.get("fetched_at") or meta.get("pulled_at")
    prec = "second" if "T" in at else "date"
    return at, prec


def load_round(path, published_map):
    doc = json.loads(path.read_text(encoding="utf-8"))
    meta = doc["_meta"]
    if meta.get("usable_as_observation") is False:
        return [], meta.get("exclusion_reason", "исключён файлом")
    source = canonical_source(meta["source"])
    round_id = meta.get("round", "R1")
    at, prec = observation_time(meta)
    hdr = resolve_header(doc, source)
    out = []
    for i, row in enumerate(doc["rows"]):
        if len(row) != len(hdr):
            raise ValueError(f"{path.name} строка {i}: {len(row)} значений при {len(hdr)} колонках")
        rec = dict(zip(hdr, row))
        vid = rec.get("video_id") or vid_from_url(rec.get("url") or rec["share_url"])
        raw_pub = rec.get("create_datetime") or rec.get("published")
        published = parse_published(raw_pub) if raw_pub else published_map.get(vid)
        if published:
            published_map.setdefault(vid, published)
        for m in METRICS:
            if m not in rec:
                continue
            out.append({
                "schema": "exp004_observation/v1",
                "round": round_id, "video_id": vid, "source": source, "metric": m,
                "value": rec[m],
                "observed_at": at, "observed_at_precision": prec,
                "published_at": published,
                "age_seconds_at_observation": age_seconds(published, at, prec),
                "raw_ref": f"data/raw/{path.name}#rows[{i}]",
            })
    return out, None


def age_seconds(published, fetched_at, precision):
    if precision != "second":
        return None            # честнее NULL, чем придуманная точность
    p = datetime.fromisoformat(published).replace(tzinfo=timezone.utc)
    f = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return int((f - p).total_seconds())


def build():
    files = sorted(RAW.glob("*.json"))
    rows, excluded, pub = [], [], {}
    for _ in range(2):          # второй проход добирает published для файлов без него
        rows, excluded, pub2 = [], [], dict(pub)
        for f in files:
            got, why = load_round(f, pub2)
            if why:
                excluded.append((f.name, why))
            rows += got
        pub = pub2
    OBS.parent.mkdir(parents=True, exist_ok=True)
    with OBS.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    rounds = sorted({r["round"] for r in rows})
    print(f"наблюдений: {len(rows)} | раунды: {rounds} | "
          f"роликов: {len({r['video_id'] for r in rows})}")
    nn = sum(1 for r in rows if r["value"] is not None)
    print(f"непустых значений: {nn} | NULL: {len(rows) - nn}")
    for name, why in excluded:
        print(f"ИСКЛЮЧЁН {name}\n    причина: {why}")


def load_obs():
    return [json.loads(l) for l in OBS.read_text(encoding="utf-8").splitlines() if l.strip()]


ROUND_ORDER = ["R1", "R2", "R3", "R3b"]


def round_times(obs):
    out = {}
    for o in obs:
        out.setdefault(o["round"], set()).add((o["source"], o["observed_at"],
                                               o["observed_at_precision"]))
    return out


def deltas():
    obs = load_obs()
    print("МЕТКИ ВРЕМЕНИ РАУНДОВ")
    for r in ROUND_ORDER:
        for src, at, prec in sorted(round_times(obs).get(r, [])):
            print(f"  {r:<5}{src:<13}{at:<26}точность: {prec}")
    print()

    idx = {}
    for o in obs:
        idx.setdefault((o["video_id"], o["source"], o["metric"]), {})[o["round"]] = o

    for a, b in [("R1", "R2"), ("R2", "R3"), ("R2", "R3b")]:
        changed, same, pairs = [], 0, 0
        for key, byr in sorted(idx.items()):
            x, y = byr.get(a), byr.get(b)
            if not x or not y:
                continue
            pairs += 1
            vx, vy = x["value"], y["value"]
            if vx is None and vy is None:
                same += 1
            elif vx is None or vy is None:
                changed.append((key, vx, vy))
            elif float(vx) != float(vy):
                changed.append((key, float(vx), float(vy)))
            else:
                same += 1
        print(f"--- {a} -> {b} | сопоставимых пар: {pairs} | "
              f"без изменений: {same} | изменилось: {len(changed)}")
        for (vid, src, m), vx, vy in changed:
            d = (f"{vy - vx:+.4f}" if isinstance(vx, float) and isinstance(vy, float)
                 else f"{vx} -> {vy}")
            print(f"      {vid[-6:]} {src:<13}{m:<24}{vx:>13} -> {vy:<13} {d}")
        if not changed:
            print("      (ни одного изменения)")
        print()


def reconcile():
    obs = load_obs()
    idx = {}
    for o in obs:
        idx.setdefault((o["round"], o["video_id"], o["metric"]), {})[o["source"]] = o["value"]
    print(f"  {'round':<7}{'video':>8} {'metric':<24}{'supermetrics':>14}{'metricool':>12}"
          f"{'abs':>8}{'rel':>9}  status")
    stats = {}
    for (rnd, vid, m), by in sorted(idx.items()):
        sm, mt = by.get("supermetrics"), by.get("metricool")
        st = classify(m, sm, mt)
        stats[st] = stats.get(st, 0) + 1
        if st in ("both_discrepancy", "both_expected_transform"):
            a, b = float(sm), float(mt)
            d = abs(a - b); rel = d / max(abs(a), abs(b)) if max(abs(a), abs(b)) else 0
            print(f"  {rnd:<7}{vid[-6:]:>8} {m:<24}{a:>14.3f}{b:>12.3f}{d:>8.3f}{rel:>9.5f}  {st}")
    print("\nсводка статусов:")
    for k in sorted(stats):
        print(f"  {k:<28}{stats[k]}")


def classify(metric, sm, mt):
    if sm is None and mt is None:
        return "unavailable"
    if sm is None or mt is None:
        return "single_source"
    a, b = float(sm), float(mt)
    if metric == "duration_sec":
        import math
        return "both_expected_transform" if math.floor(a) == b else "both_discrepancy"
    if a == b:
        return "both_matched"
    return "both_discrepancy"


def verify():
    """Проверки воспроизводимости, предусмотренные архитектурой."""
    ok = True
    obs = load_obs()

    # 1. Сырьё не менялось: перечитывание даёт идентичный результат
    import subprocess, hashlib
    before = hashlib.sha256(OBS.read_bytes()).hexdigest()
    build_quiet()
    after = hashlib.sha256(OBS.read_bytes()).hexdigest()
    p = before == after
    ok &= p
    print(f"  [{'OK' if p else 'FAIL'}] пересборка observations из raw идемпотентна")

    # 2. У каждого наблюдения есть провенанс
    p = all(o.get("raw_ref") and o.get("source") and o.get("observed_at") for o in obs)
    ok &= p
    print(f"  [{'OK' if p else 'FAIL'}] у каждого наблюдения есть source, observed_at, raw_ref")

    # 3. Возраст не выдумывается при метке с точностью до даты
    bad = [o for o in obs if o["observed_at_precision"] != "second"
           and o["age_seconds_at_observation"] is not None]
    p = not bad
    ok &= p
    print(f"  [{'OK' if p else 'FAIL'}] возраст = NULL там, где метка неточна ({len(bad)} нарушений)")

    # 4. avg_view_time = total_time_watched / views (проверка, а не допущение)
    idx = {}
    for o in obs:
        if o["source"] == "supermetrics":
            idx.setdefault((o["round"], o["video_id"]), {})[o["metric"]] = o["value"]
    checked = mism = 0
    for k, r in idx.items():
        if r.get("avg_view_time_sec") and r.get("total_time_watched_sec") and r.get("views"):
            checked += 1
            if abs(r["total_time_watched_sec"] / r["views"] - r["avg_view_time_sec"]) > 0.001:
                mism += 1
    p = checked > 0 and mism == 0
    ok &= p
    print(f"  [{'OK' if p else 'FAIL'}] avg_view_time == total_time/views "
          f"(проверено {checked}, расхождений {mism})")

    # 5. Счётчики не убывают по всей последовательности раундов
    order = {r: i for i, r in enumerate(ROUND_ORDER)}
    cnt = {}
    for o in obs:
        if o["metric"] in ("views", "likes", "comments", "shares", "favorites", "reach") \
           and o["value"] is not None:
            cnt.setdefault((o["video_id"], o["source"], o["metric"]), []).append(
                (order.get(o["round"], 99), o["round"], float(o["value"])))
    drops = []
    for k, seq in cnt.items():
        seq.sort()
        for (_, ra, va), (_, rb, vb) in zip(seq, seq[1:]):
            if vb < va:
                drops.append((k, ra, va, rb, vb))
    p = not drops
    ok &= p
    print(f"  [{'OK' if p else 'FAIL'}] монотонность счётчиков по всем раундам "
          f"({len(drops)} падений)")
    for k, ra, va, rb, vb in drops:
        print(f"        падение: {k} {ra}={va} -> {rb}={vb}")

    # 6. Ответы, помеченные источником как несвежие, не попали в наблюдения
    import glob
    replays = [f for f in glob.glob(str(RAW / "*.json"))
               if json.loads(Path(f).read_text(encoding="utf-8"))["_meta"]
                  .get("usable_as_observation") is False]
    used = {o["raw_ref"].split("#")[0].split("/")[-1] for o in obs}
    leaked = [Path(f).name for f in replays if Path(f).name in used]
    p = not leaked
    ok &= p
    print(f"  [{'OK' if p else 'FAIL'}] повторы кэша исключены из наблюдений "
          f"(найдено повторов: {len(replays)}, просочилось: {len(leaked)})")
    return 0 if ok else 1


def build_quiet():
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        build()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    sys.exit({"build": build, "deltas": deltas,
              "reconcile": reconcile, "verify": verify}[cmd]() or 0)
