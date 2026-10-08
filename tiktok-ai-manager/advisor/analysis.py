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

from insights.policies import MIN_SAMPLE_FOR_FACT          # noqa: E402
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


def load(root=ROOT):
    """Ролики с последним наблюдением. Детерминированно: порядок — video_id."""
    root = Path(root)
    videos = {}
    with open(root / "data" / "videos.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                v = json.loads(line)
                videos[v["video_id"]] = v

    # последнее наблюдение по каждой паре (ролик, источник)
    latest = {}
    for path in sorted(glob.glob(str(root / "data" / "snapshots" / "*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                s = json.loads(line)
                key = (s["video_id"], s["source"])
                cur = latest.get(key)
                if cur is None or s["observed_at"] > cur["observed_at"]:
                    latest[key] = s

    rows = []
    for vid in sorted(videos):
        v = videos[vid]
        obs = [s for (i, _), s in latest.items() if i == vid]
        if not obs:
            continue
        # Счётчик монотонен: отставший источник даёт меньшее значение,
        # поэтому берётся наибольшее из последних наблюдений источников.
        views = max((s.get("views") or 0) for s in obs)
        seen = max(s["observed_at"] for s in obs)
        pub = datetime.fromisoformat(v["published_at"])
        age = (datetime.fromisoformat(seen) - pub).days
        caption = v.get("caption") or ""
        words = len(_caption_words(caption))
        rows.append({
            "video_id": vid,
            "url": v.get("url"),
            "views": views,
            "observed_at": seen,
            "published_at": v["published_at"],
            "age_days": age,
            "duration_sec": v.get("duration_sec"),
            "weekday": WEEKDAYS[pub.weekday()],
            "hour_utc": pub.hour,
            "hashtags": len(re.findall(r"#\S+", caption)),
            "caption_words": words,
            "caption_kind": ("повествовательная" if words >= NARRATIVE_MIN_WORDS
                             else "только хештеги"),
            "caption": caption,
        })
    return rows


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
           ("hour_utc", "Час публикации (UTC)", "ч"),
           ("caption_words", "Число слов в подписи без хештегов", ""),
           ("hashtags", "Число хештегов", ""))
CATEGORICAL = (("weekday", "День публикации"),
               ("caption_kind", "Вид подписи"))


def analyze(rows=None, root=ROOT):
    rows = load(root) if rows is None else rows
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

    counter = [0]                    # сквозная нумерация гипотез H1, H2, …
    for key, label, unit in NUMERIC:
        hv = [r[key] for r in hits if r[key] is not None]
        if len(hv) != len(hits):
            continue
        lo, hi = min(hv), max(hv)
        inside = [r for r in rest if r[key] is not None and lo <= r[key] <= hi]
        span = _num(lo) if lo == hi else f"{_num(lo)}–{_num(hi)}"
        span += f" {unit}" if unit else ""
        _record(out, key, label, span, len(hits), len(inside), len(rest), nm,
                counter)
    for key, label in CATEGORICAL:
        values = {r[key] for r in hits}
        if len(values) != 1:
            out["not_distinguishing"].append(_checked({
                "attribute": key, "claim_type": "FACT", "n_sample": nm,
                "statement": f"{label} у хитов разный "
                             f"({', '.join(sorted(values))}), n={nm}."}))
            continue
        same = [r for r in rest if r[key] == next(iter(values))]
        _record(out, key, label, next(iter(values)), len(hits), len(same),
                len(rest), nm, counter)
    return out


def _record(out, key, label, value, n_hits, n_same, n_rest, nm, counter):
    """Признак отделяет хиты — гипотеза; не отделяет — описание."""
    if n_same * 2 < n_rest:
        counter[0] += 1
        out["hypotheses"].append(_checked({
            "id": f"H{counter[0]}", "claim_type": "HYPOTHESIS",
            "attribute": key, "label": label, "value": value,
            "n_sample": nm, "min_sample_required": MIN_SAMPLE_FOR_FACT,
            "hits_matching": n_hits, "rest_matching": n_same, "rest_total": n_rest,
            "statement": f"Признак «{label}: {value}» может быть связан с залётом: так у "
                         f"{n_hits} из {n_hits} хитов и у {n_same} из {n_rest} "
                         f"остальных зрелых роликов, n={nm}. "
                         f"Причинность не установлена.",
            "competing_explanation": COMPETING}))
    else:
        out["not_distinguishing"].append(_checked({
            "attribute": key, "claim_type": "FACT", "n_sample": nm,
            "statement": f"{label} не отличает хиты: у хитов «{value}», так же "
                         f"у {n_same} из {n_rest} остальных зрелых роликов, "
                         f"n={nm}."}))


def render(a):
    """Текст для консоли. Только то, что уже лежит в разборе."""
    L = ["ЧТО ЗАЛЕТЕЛО И ЧЕМ ОТЛИЧАЕТСЯ",
         f"по данным на {a['observed_at']}; роликов {a['n_videos']}, "
         f"зрелых {a['n_mature']}", ""]
    L += [f"FACT  {f['statement']}" for f in a["facts"]]
    hits = [v for v in a["videos"] if v.get("hit")]
    if hits:
        L += ["", "Хиты:"]
        for v in hits:
            L.append(f"  {v['views']:>8} просм. · {_num(v['duration_sec'])} с · "
                     f"{v['weekday']} {v['hour_utc']:02d}:00 UTC · {v['url']}")
            L.append(f"           «{v['caption'][:90]}»")
    L += ["", "Гипотезы (проверяются только новыми публикациями):"]
    if a["hypotheses"]:
        for h in a["hypotheses"]:
            L.append(f"  HYPOTHESIS {h['id']}  {h['statement']}")
        L.append(f"  Конкурирующее объяснение: {COMPETING}")
    else:
        L.append("  нет: ни один признак из данных не отделяет хиты от остальных")
    if a["not_distinguishing"]:
        L += ["", "Не отличает хиты:"]
        L += [f"  {x['statement']}" for x in a["not_distinguishing"]]
    L += ["", f"Чтобы стало FACT, нужно не меньше {a['min_sample_required']} "
              f"зрелых роликов; сейчас {a['n_mature']}.",
          "Часовой пояс аудитории не подтверждён (OQ-1): время указано в UTC."]
    return "\n".join(L)


if __name__ == "__main__":
    print(render(analyze()))
