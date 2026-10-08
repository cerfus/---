#!/usr/bin/env python3
"""Что залетело и чем залетевшие отличаются — только по данным аккаунта.

Источник — JSONL (data/videos.jsonl и data/snapshots/), а не база: JSONL
источник истины, и разбор работает, даже когда PostgreSQL недоступен.

Три правила, без которых советы были бы выдумкой:

1. Молодые ролики не сравниваются со зрелыми по абсолютным просмотрам
   (CLAUDE.md, «Запреты»). Ролик моложе MATURE_AGE_DAYS на момент замера
   в сравнение не входит и показывается отдельно. Без этого правила разбор
   находил «закономерность», которая целиком объяснялась возрастом.

2. Признак попадает в гипотезы, только если он хоть как-то отделяет хиты
   от остальных: все хиты обладают им, а остальные зрелые — меньше чем в
   половине случаев. Иначе он честно записывается как «не отличает».

3. Закономерность при N < MIN_SAMPLE_FOR_FACT — только HYPOTHESIS. FACT
   здесь — лишь описания конкретных роликов и счёт, без обобщения.

4. Просмотры берутся только СВЕРЕННЫЕ: из data/reconciliation/results.jsonl,
   со статусом, допускающим FACT (CLAUDE.md: «FACT опирается на статус
   именно той метрики»). Более свежее, но не сверенное значение — например,
   из одного Metricool, когда Supermetrics недоступен, — в разбор не входит
   и показывается отдельным разделом с пометкой «не сверено».

Каждая формулировка проходит insights.validator при построении: причинные
и оценочные конструкции не доходят до вывода вообще.
"""
import glob
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from insights.policies import (FACT_BLOCKING_STATUSES,     # noqa: E402
                               MIN_SAMPLE_FOR_FACT)
from insights.validator import find_violations              # noqa: E402

ADVISOR_POLICY_VERSION = "advisor-analysis-1.0.0"

# Соглашения, а не выводы из данных. Названы константами, чтобы их было
# видно и можно было оспорить; в отчёт они попадают как есть.
MATURE_AGE_DAYS = 30        # ролик моложе — «рано судить»
HIT_MULTIPLIER = 10         # хит — не меньше 10 медиан зрелых роликов
NARRATIVE_MIN_WORDS = 5     # подпись с меньшим числом слов вне хештегов —
                            # «только хештеги»

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница",
            "суббота", "воскресенье")

COMPETING = ("Хитов слишком мало: совпадение признака у них может оказаться "
             "случайным. Признаки, которых нет в данных (сюжет кадра, звук, "
             "тренд момента), не проверены и могут объяснять разницу целиком.")


def _num(x):
    """Число без хвостовых нулей: 10.0 -> 10, 7.20 -> 7.2."""
    if isinstance(x, float):
        s = f"{x:.1f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(x)


def _caption_words(caption):
    """Слова подписи без хештегов и упоминаний."""
    text = re.sub(r"[#@]\S+", " ", caption or "")
    return [w for w in text.split() if re.search(r"\w", w)]


def _latest_raw(root, kind):
    files = sorted((Path(root) / "data" / "raw").glob(f"*_metricool_{kind}_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def context(root=ROOT):
    """Часовой пояс бренда и оценка активности аудитории от Metricool.

    Обе вещи — не наблюдения, а контекст: пояс нужен, чтобы проверить,
    держится ли гипотеза о дне и часе публикации при смене пояса (OQ-1);
    оценка активности — признак «в какой час по мнению источника вышел
    ролик». Без базы часовых поясов (на Windows — пакет tzdata) локальные
    признаки не считаются, и причина называется, а не замалчивается.
    """
    ctx = {"tz_name": None, "tz": None, "tz_note": None,
           "grid": None, "grid_tz": None, "grid_period": None}
    brand = _latest_raw(root, "brand")
    if brand and brand.get("data"):
        ctx["tz_name"] = brand["data"][0].get("timezone")
    bt = _latest_raw(root, "besttime")
    if bt:
        req = bt["_meta"]["request"]
        ctx["grid"] = {(d["dayOfWeek"], h["hourOfDay"]): h["value"]
                       for d in bt["data"] for h in d["bestTimesByHour"]}
        ctx["grid_tz_name"] = req.get("timezone")
        ctx["grid_period"] = (str(req.get("fromDate"))[:10], str(req.get("toDate"))[:10])
    try:
        from zoneinfo import ZoneInfo
        if ctx["tz_name"]:
            ctx["tz"] = ZoneInfo(ctx["tz_name"])
        if ctx["grid"]:
            ctx["grid_tz"] = ZoneInfo(ctx["grid_tz_name"])
    except Exception as exc:
        ctx["tz"] = ctx["grid_tz"] = None
        ctx["tz_note"] = (f"база часовых поясов недоступна ({type(exc).__name__}): "
                          "местное время и активность аудитории не считаются; "
                          "на Windows нужен пакет tzdata: python -m pip install -r requirements.txt")
    return ctx


def activity_pct(grid, day, hour):
    """Перцентиль часа среди всех 168 часов недели по оценке Metricool."""
    vals = sorted(grid.values())
    v = grid[(day, hour)]
    return round(100 * sum(1 for x in vals if x <= v) / len(vals))


def _verified_views(root):
    """Последнее СВЕРЕННОЕ значение просмотров по ролику: {video_id: (views, at)}.

    Сверенным считается результат сверки со статусом, который не блокирует
    FACT. Значение — от канонического источника, если он назван (отставание),
    иначе совпавшее значение пары.
    """
    path = Path(root) / "data" / "reconciliation" / "results.jsonl"
    out = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("metric") != "views" or r.get("classification") in FACT_BLOCKING_STATUSES:
                continue
            if r.get("canonical_source") == r.get("source_b"):
                value = r.get("value_b")
            elif r.get("canonical_source") == r.get("source_a"):
                value = r.get("value_a")
            else:
                vals = [v for v in (r.get("value_a"), r.get("value_b")) if v is not None]
                value = max(vals) if vals else None
            if value is None:
                continue
            at = max(x for x in (r.get("observed_at_a"), r.get("observed_at_b")) if x)
            cur = out.get(r["video_id"])
            if cur is None or at > cur[1] or (at == cur[1] and value > cur[0]):
                out[r["video_id"]] = (int(value), at)
    return out


def load(root=ROOT, ctx=None):
    """(сверенные ролики, свежие несверенные наблюдения). Порядок — video_id."""
    root = Path(root)
    ctx = ctx or {}
    videos = {}
    with open(root / "data" / "videos.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                v = json.loads(line)
                videos[v["video_id"]] = v

    # последнее наблюдение по каждой паре (ролик, источник) — для раздела
    # «не сверено»: оно может быть свежее последнего сверенного
    latest = {}
    for path in sorted(glob.glob(str(root / "data" / "snapshots" / "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                s = json.loads(line)
                if s.get("views") is None:
                    continue
                key = (s["video_id"], s["source"])
                cur = latest.get(key)
                if cur is None or s["observed_at"] > cur["observed_at"]:
                    latest[key] = s

    verified = _verified_views(root)
    rows, fresh = [], []
    for vid in sorted(videos):
        v = videos[vid]
        pub = datetime.fromisoformat(v["published_at"])
        obs = [s for (i, _), s in latest.items() if i == vid]
        newest = max(obs, key=lambda s: (s["observed_at"], s["views"]), default=None)
        ver = verified.get(vid)
        if newest is not None and (ver is None or newest["observed_at"] > ver[1]):
            fresh.append({
                "video_id": vid, "url": v.get("url"), "source": newest["source"],
                "views": newest["views"], "observed_at": newest["observed_at"],
                "age_days": (datetime.fromisoformat(newest["observed_at"]) - pub).days,
                "verified_views": ver[0] if ver else None,
                "verified_at": ver[1] if ver else None,
                "caption": v.get("caption") or ""})
        if ver is None:
            continue
        views, seen = ver
        caption = v.get("caption") or ""
        words = len(_caption_words(caption))
        rows.append({
            "video_id": vid,
            "url": v.get("url"),
            "views": views,
            "observed_at": seen,
            "published_at": v["published_at"],
            "age_days": (datetime.fromisoformat(seen) - pub).days,
            "duration_sec": v.get("duration_sec"),
            "weekday": WEEKDAYS[pub.weekday()],
            "hour_utc": pub.hour,
            "hashtags": len(re.findall(r"#\S+", caption)),
            "caption_words": words,
            "caption_kind": ("повествовательная" if words >= NARRATIVE_MIN_WORDS
                             else "только хештеги"),
            "caption": caption,
        })
        if ctx.get("tz") is not None:
            loc = pub.astimezone(ctx["tz"])
            rows[-1].update(weekday_local=WEEKDAYS[loc.weekday()], hour_local=loc.hour)
        if ctx.get("grid_tz") is not None:
            g = pub.astimezone(ctx["grid_tz"])
            rows[-1]["activity_pct"] = activity_pct(ctx["grid"], g.isoweekday(), g.hour)
            rows[-1]["grid_slot"] = [g.isoweekday(), g.hour]
    return rows, fresh


def _checked(item):
    """Формулировка, не прошедшая валидатор, — ошибка построения, а не вывод."""
    for field in ("statement", "competing_explanation"):
        text = item.get(field)
        if not text:
            continue
        kind = item["claim_type"] if field == "statement" else "RECOMMENDATION"
        v = find_violations(text, kind)
        if v:
            raise ValueError(f"ADVISOR_CLAIM_REJECTED: {'; '.join(v)} :: {text!r}")
    return item


NUMERIC = (("duration_sec", "Длительность", "с"),
           ("caption_words", "Число слов в подписи без хештегов", ""),
           ("hashtags", "Число хештегов", ""))
CATEGORICAL = (("caption_kind", "Вид подписи"),)
# Время публикации проверяется в двух поясах: гипотеза о дне или часе,
# которая держится только в одном, зависит от нерешённого OQ-1.
TIME_FAMILIES = (
    ("hour", ("hour_utc", "Час публикации (UTC)"), ("hour_local", "Час публикации")),
    ("weekday", ("weekday", "День публикации (UTC)"), ("weekday_local", "День публикации")),
)
ACTIVITY = ("activity_pct", "Активность аудитории в час публикации "
                            "(перцентиль по оценке Metricool)", "%")


def hour_window(hours):
    """Наименьшее окно часов по кругу, покрывающее все: (начало, длина).

    Часы — круг, а не отрезок: публикации в 22 и в 2 часа лежат в окне
    22–02 длиной 4 часа, а не в «2–22» длиной 20. Без этого гипотеза о
    часе разваливалась бы от одного сдвига пояса через полночь.
    """
    hs = sorted(set(hours))
    if len(hs) == 1:
        return hs[0], 0
    gap, i = max(((hs[(k + 1) % len(hs)] - hs[k]) % 24, k) for k in range(len(hs)))
    return hs[(i + 1) % len(hs)], 24 - gap


def in_window(h, start, length):
    return (h - start) % 24 <= length


def _cand_numeric(key, label, unit, hits, rest):
    hv = [r.get(key) for r in hits]
    if any(v is None for v in hv):
        return None
    lo, hi = min(hv), max(hv)
    inside = [r for r in rest if r.get(key) is not None and lo <= r[key] <= hi]
    value = _num(lo) if lo == hi else f"{_num(lo)}–{_num(hi)}"
    return {"key": key, "label": label, "value": value + (f" {unit}" if unit else ""),
            "n_same": len(inside)}


def _cand_hour(key, label, hits, rest):
    hv = [r.get(key) for r in hits]
    if any(v is None for v in hv):
        return None
    start, length = hour_window(hv)
    inside = [r for r in rest if r.get(key) is not None and in_window(r[key], start, length)]
    value = (f"{start:02d} ч" if length == 0
             else f"{start:02d}–{(start + length) % 24:02d} ч")
    return {"key": key, "label": label, "value": value, "n_same": len(inside)}


def _cand_categorical(key, label, hits, rest):
    vals = [r.get(key) for r in hits]
    if any(v is None for v in vals):
        return None
    if len(set(vals)) != 1:
        return {"key": key, "label": label, "differs": sorted(set(vals))}
    return {"key": key, "label": label, "value": vals[0],
            "n_same": sum(1 for r in rest if r.get(key) == vals[0])}


def analyze(rows=None, root=ROOT, fresh=None, ctx=None):
    if rows is None:
        ctx = context(root)
        rows, fresh = load(root, ctx)
    fresh = fresh or []
    n = len(rows)
    total = sum(r["views"] for r in rows)
    mature = [r for r in rows if r["age_days"] >= MATURE_AGE_DAYS]
    young = [r for r in rows if r["age_days"] < MATURE_AGE_DAYS]
    nm = len(mature)

    out = {
        "policy_version": ADVISOR_POLICY_VERSION,
        "mature_age_days": MATURE_AGE_DAYS,
        "hit_multiplier": HIT_MULTIPLIER,
        "min_sample_required": MIN_SAMPLE_FOR_FACT,
        "observed_at": max((r["observed_at"] for r in rows), default=None),
        "n_videos": n, "n_mature": nm, "total_views": total,
        "facts": [], "hypotheses": [], "not_distinguishing": [],
        "videos": [], "young": [],
        "unverified": sorted(fresh, key=lambda x: (-x["views"], x["video_id"])),
    }
    if not mature:
        out["facts"].append(_checked({
            "id": "F1", "claim_type": "FACT", "n_sample": n,
            "statement": f"Зрелых роликов (не моложе {MATURE_AGE_DAYS} дней) "
                         f"пока нет, n={n}: сравнивать нечего."}))
        return out

    median = statistics.median(r["views"] for r in mature)
    threshold = median * HIT_MULTIPLIER
    hits = [r for r in mature if r["views"] >= threshold]
    rest = [r for r in mature if r["views"] < threshold]
    hit_views = sum(r["views"] for r in hits)
    share = 100.0 * hit_views / total if total else 0.0
    out.update(median_views_mature=median, hit_threshold=threshold,
               n_hits=len(hits), hits_share_pct=round(share, 1))

    for r in sorted(rows, key=lambda r: (-r["views"], r["video_id"])):
        item = dict(r, share_pct=round(100.0 * r["views"] / total, 1) if total else 0.0,
                    mature=r["age_days"] >= MATURE_AGE_DAYS,
                    hit=r in hits)
        out["videos"].append(item)
        if not item["mature"]:
            out["young"].append(r["video_id"])

    F = out["facts"]
    F.append(_checked({
        "id": "F1", "claim_type": "FACT", "n_sample": nm,
        "statement": f"Зрелых роликов (не моложе {MATURE_AGE_DAYS} дней на момент "
                     f"замера) {nm} из {n}; медиана их просмотров {_num(median)}, "
                     f"n={nm}."}))
    F.append(_checked({
        "id": "F2", "claim_type": "FACT", "n_sample": nm,
        "statement": f"Хит — зрелый ролик с просмотрами не ниже {HIT_MULTIPLIER} "
                     f"медиан ({_num(threshold)}). Таких {len(hits)}, n={nm}."}))
    if hits:
        F.append(_checked({
            "id": "F3", "claim_type": "FACT", "n_sample": n,
            "statement": f"Хиты набрали {_num(hit_views)} из {_num(total)} "
                         f"просмотров аккаунта ({_num(round(share, 1))}%), n={n}."}))
    if young:
        F.append(_checked({
            "id": "F4", "claim_type": "FACT", "n_sample": len(young),
            "statement": f"Моложе {MATURE_AGE_DAYS} дней: {len(young)} роликов — "
                         f"в сравнение не входят, n={len(young)}."}))

    if not hits or not rest:
        return out

    ctx = ctx or {}
    nh, nr = len(hits), len(rest)
    distinct = lambda c: c and "differs" not in c and c["n_same"] * 2 < nr
    counter = [0]                    # сквозная нумерация гипотез H1, H2, …
    emit = lambda c, **extra: _record(out, c, nh, nr, nm, counter, **extra)

    tz = ctx.get("tz_name")

    def time_family(family, ukey, ulabel, lkey, llabel):
        mk = _cand_hour if family == "hour" else _cand_categorical
        cu = mk(ukey, ulabel, hits, rest)
        cl = mk(lkey, f"{llabel} ({tz})", hits, rest) if tz else None
        if cu is None:
            return
        if cl is None:                               # пояс бренда неизвестен
            emit(cu)
        elif distinct(cu) and distinct(cl):
            emit(cu, tz_robust=True, local_view=f"{cl['label']}: {cl['value']}")
        elif distinct(cu) or distinct(cl):
            held, other, where = (cu, cl, tz) if distinct(cu) else (cl, cu, "UTC")
            why = (f"у хитов разные значения ({', '.join(other['differs'])})"
                   if "differs" in other else
                   f"у хитов «{other['value']}», так же у {other['n_same']} из {nr}")
            emit(held, tz_robust=False,
                 tz_note=f"зависит от часового пояса: в {where} не отличает — {why}")
        else:
            emit(cu)

    # Порядок признаков постоянный — от него зависят номера H1, H2, …,
    # которые видит владелец и на которые ссылаются идеи.
    fam = {f: spec for f, *spec in ((f, u[0], u[1], l[0], l[1])
                                    for f, u, l in TIME_FAMILIES)}
    for kind, spec in (("num", NUMERIC[0]), ("time", "hour"), ("num", NUMERIC[1]),
                       ("num", NUMERIC[2]), ("time", "weekday"), ("cat", CATEGORICAL[0])):
        if kind == "num":
            c = _cand_numeric(*spec, hits, rest)
            if c:
                emit(c)
        elif kind == "cat":
            c = _cand_categorical(*spec, hits, rest)
            if c:
                emit(c)
        else:
            time_family(spec, *fam[spec])
    c = _cand_numeric(*ACTIVITY, hits, rest)
    if c:
        start, end = ctx.get("grid_period") or ("?", "?")
        emit(c, source_note=(f"Оценка активности — модель Metricool на неделю "
                             f"{start}…{end}, а ролики вышли раньше: допущение, что "
                             "недельный ритм аудитории с тех пор не изменился."))
    out["context"] = {"tz_name": tz, "tz_note": ctx.get("tz_note"),
                      "grid_period": ctx.get("grid_period"),
                      "top_slots": _top_slots(ctx),
                      "grid_tz_name": ctx.get("grid_tz_name"),
                      # сетка оценки: [день 1-7, час, значение, перцентиль]
                      "grid": ([[d, h, v, activity_pct(ctx["grid"], d, h)]
                                for (d, h), v in sorted(ctx["grid"].items())]
                               if ctx.get("grid") else [])}
    out["growth"] = _growth(out["unverified"])
    return out


def _top_slots(ctx, n=5):
    """Самые активные часы по оценке Metricool: [(день, час, значение)]."""
    grid = ctx.get("grid")
    if not grid:
        return []
    best = sorted(grid.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [(WEEKDAYS[d - 1], h, v) for (d, h), v in best]


def _growth(fresh):
    """Прирост от последнего сверенного значения к свежему. Не сверено."""
    out = []
    for u in fresh:
        if u["verified_views"] is None:
            out.append({"video_id": u["video_id"], "new": True, "views": u["views"],
                        "age_days": u["age_days"], "caption": u["caption"]})
            continue
        days = (datetime.fromisoformat(u["observed_at"])
                - datetime.fromisoformat(u["verified_at"])).days or 1
        delta = u["views"] - u["verified_views"]
        out.append({"video_id": u["video_id"], "new": False, "delta": delta,
                    "days": days, "per_day": round(delta / days, 1),
                    "views": u["views"], "caption": u["caption"]})
    return sorted(out, key=lambda g: (not g["new"], -(g.get("delta") or 0), g["video_id"]))


def _record(out, c, n_hits, n_rest, nm, counter, **extra):
    """Признак отделяет хиты — гипотеза; не отделяет — описание."""
    key, label = c["key"], c["label"]
    if "differs" in c:
        out["not_distinguishing"].append(_checked({
            "attribute": key, "claim_type": "FACT", "n_sample": nm,
            "statement": f"{label} у хитов разный ({', '.join(c['differs'])}), n={nm}."}))
        return
    value, n_same = c["value"], c["n_same"]
    if n_same * 2 < n_rest:
        counter[0] += 1
        competing = COMPETING + (" " + extra["source_note"] if extra.get("source_note") else "")
        out["hypotheses"].append(_checked({
            "id": f"H{counter[0]}", "claim_type": "HYPOTHESIS",
            "attribute": key, "label": label, "value": value,
            "n_sample": nm, "min_sample_required": MIN_SAMPLE_FOR_FACT,
            "hits_matching": n_hits, "rest_matching": n_same, "rest_total": n_rest,
            "statement": f"Признак «{label}: {value}» может быть связан с залётом: так у "
                         f"{n_hits} из {n_hits} хитов и у {n_same} из {n_rest} "
                         f"остальных зрелых роликов, n={nm}. "
                         f"Причинность не установлена.",
            "competing_explanation": competing,
            "tz_robust": extra.get("tz_robust"),
            "tz_note": extra.get("tz_note"),
            "local_view": extra.get("local_view")}))
    else:
        out["not_distinguishing"].append(_checked({
            "attribute": key, "claim_type": "FACT", "n_sample": nm,
            "statement": f"{label} не отличает хиты: у хитов «{value}», так же "
                         f"у {n_same} из {n_rest} остальных зрелых роликов, "
                         f"n={nm}."}))


def render(a):
    """Текст для консоли. Только то, что уже лежит в разборе."""
    ctx = a.get("context") or {}
    tz = ctx.get("tz_name")
    L = ["ЧТО ЗАЛЕТЕЛО И ЧЕМ ОТЛИЧАЕТСЯ",
         f"по данным на {a['observed_at']}; роликов {a['n_videos']}, "
         f"зрелых {a['n_mature']}"]
    if tz:
        L.append(f"пояс бренда в настройках Metricool: {tz} (время ниже — UTC и {tz})")
    if ctx.get("tz_note"):
        L.append(f"примечание: {ctx['tz_note']}")
    L.append("")
    L += [f"FACT  {f['statement']}" for f in a["facts"]]
    hits = [v for v in a["videos"] if v.get("hit")]
    if hits:
        L += ["", "Хиты:"]
        for v in hits:
            local = (f" ({v['weekday_local']} {v['hour_local']:02d}:00 {tz})"
                     if "weekday_local" in v else "")
            act = (f" · активность аудитории {v['activity_pct']}-й перцентиль"
                   if "activity_pct" in v else "")
            L.append(f"  {v['views']:>8} просм. · {_num(v['duration_sec'])} с · "
                     f"{v['weekday']} {v['hour_utc']:02d}:00 UTC{local}{act}")
            L.append(f"           «{v['caption'][:90]}» {v['url']}")
    L += ["", "Гипотезы (проверяются только новыми публикациями):"]
    if a["hypotheses"]:
        for h in a["hypotheses"]:
            L.append(f"  HYPOTHESIS {h['id']}  {h['statement']}")
            if h.get("tz_robust") is True:
                L.append(f"      держится и в другом поясе — {h['local_view']}")
            elif h.get("tz_robust") is False:
                L.append(f"      ВНИМАНИЕ: {h['tz_note']}")
            if h["competing_explanation"] != COMPETING:
                L.append(f"      {h['competing_explanation'][len(COMPETING) + 1:]}")
        L.append(f"  Конкурирующее объяснение для всех: {COMPETING}")
    else:
        L.append("  нет: ни один признак из данных не отделяет хиты от остальных")
    if a["not_distinguishing"]:
        L += ["", "Не отличает хиты:"]
        L += [f"  {x['statement']}" for x in a["not_distinguishing"]]
    if ctx.get("top_slots"):
        slots = ", ".join(f"{d} {h:02d}:00" for d, h, _ in ctx["top_slots"])
        start, stop = ctx.get("grid_period") or ("?", "?")
        L += ["", f"Оценка Metricool, не наблюдение (неделя {start}…{stop}, "
                  f"{tz or 'пояс запроса'}): самые активные часы аудитории — {slots}."]
    if a.get("growth"):
        L += ["", "Кто продолжает расти — НЕ СВЕРЕНО (один источник):"]
        for g in a["growth"][:6]:
            if g["new"]:
                L.append(f"  новый   {g['views']:>8} за {g['age_days']} дн. · "
                         f"«{g['caption'][:50]}»")
            else:
                L.append(f"  {g['delta']:>+7} за {g['days']} дн. ({_num(g['per_day'])}/дн.) · "
                         f"сейчас {g['views']} · «{g['caption'][:50]}»")
    if a.get("unverified"):
        L += ["", "Свежее, но НЕ СВЕРЕНО (второй источник недоступен) — в разбор "
                  "не входит, FACT на этом не строится:"]
        for u in a["unverified"]:
            was = (f"сверено {u['verified_views']} на {u['verified_at'][:10]}"
                   if u["verified_views"] is not None else "сверенных данных нет")
            L.append(f"  {u['views']:>8} на {u['observed_at'][:10]} ({u['source']}, "
                     f"возраст {u['age_days']} дн.) · {was} · {u['url']}")
    L += ["", f"Чтобы стало FACT, нужно не меньше {a['min_sample_required']} "
              f"зрелых роликов; сейчас {a['n_mature']}.",
          "Часовой пояс аудитории не подтверждён (OQ-1): пояс бренда — это настройка, "
          "а не место, где смотрят ролики."]
    return "\n".join(L)


if __name__ == "__main__":
    print(render(analyze()))
