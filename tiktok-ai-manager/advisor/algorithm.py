#!/usr/bin/env python3
"""Как алгоритм раздаёт ролики ЭТОГО аккаунта — и план улучшения.

    python -m advisor.algorithm

«Алгоритм» здесь — не устройство TikTok: его в данных нет, и общие
представления о нём протокол проекта запрещает (CLAUDE.md, «Главный
принцип»). Изучается наблюдаемое поведение раздачи для этого аккаунта:

  F1 распределение — сколько получает обычный ролик и где «ступенька»;
  F2 кто смотрит — доля новых зрителей (просмотры на одного охваченного);
  F3 реакция — лайки, репосты, комментарии, сохранения на просмотр;
  F4 удержание — доля досмотров и среднее время просмотра;
  F5 жизненный цикл — сколько ролик прибавляет после публикации;
  F6 ритм публикаций;
  и чего не видно вовсе (G…): что нужно, чтобы узнать.

Каждое утверждение — FACT (число и n, по сверенным или одноисточниковым
метрикам — CLAUDE.md, статусы сверки), HYPOTHESIS (модальность,
конкурирующее объяснение, «Причинность не установлена») или пробел.
План улучшения — RECOMMENDATION, каждый пункт ссылается на находку и
называет действие в программе. Связь, закрытая политикой (механически
зависимые пары), в рекомендацию не превращается — спрашивается
insights.policies.promotion_allowed. Модуль ничего не пишет.
"""
import glob
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A                       # noqa: E402
from insights import policies                           # noqa: E402

ALGO_POLICY_VERSION = "algorithm-study-1.0.0"
GAP_RATIO = 10          # соседние по просмотрам ролики отличаются в 10+ раз — «ступенька»
YOUNG_DAYS = 14         # возраст ролика на первом замере: «молодой»
OLD_DAYS = 45           # и «старый»; между ними — средний
COMPETING_RATIO = ("Доля — отношение к просмотрам: у ролика с огромным охватом "
                   "знаменатель растёт за счёт незнакомых зрителей, и доля сдвигается "
                   "сама по себе (обратная причинность: охват меняет долю, а не "
                   "наоборот).")
REACTIONS = (("likes", "лайков"), ("shares", "репостов"), ("comments", "комментариев"))


def _num(x, digits=2):
    if x is None:
        return "—"
    if isinstance(x, float) and not x.is_integer():
        return f"{x:.{digits}f}".replace(".", ",")
    return A._num(x)


def _pct(x):
    return "—" if x is None else f"{100 * x:.2f}".replace(".", ",") + "%"


def load(root=ROOT):
    snaps = [json.loads(l) for f in sorted(glob.glob(str(Path(root) / "data" / "snapshots"
                                                        / "*.jsonl")))
             for l in Path(f).read_text(encoding="utf-8").splitlines() if l.strip()]
    videos = {v["video_id"]: v for v in (json.loads(l) for l in
              (Path(root) / "data" / "videos.jsonl").read_text(encoding="utf-8")
              .splitlines() if l.strip())}
    return snaps, videos


def matched(snaps, vid, metrics):
    """Последний момент, где ОБА источника дали одинаковые значения всех
    metrics (статус both_matched): {метрика: значение} или None."""
    by_time = defaultdict(dict)
    for s in snaps:
        if s["video_id"] == vid:
            by_time[s["observed_at"]][s["source"]] = s
    for at in sorted(by_time, reverse=True):
        pair = by_time[at]
        if {"metricool", "supermetrics"} <= set(pair):
            a, b = pair["metricool"], pair["supermetrics"]
            if all(a.get(m) is not None and a.get(m) == b.get(m) for m in metrics):
                return {m: a[m] for m in metrics}
    return None


def single(snaps, vid, metrics, source="supermetrics"):
    """Последнее наблюдение источника, где все metrics заданы (single_source)."""
    obs = [s for s in snaps if s["video_id"] == vid and s["source"] == source
           and all(s.get(m) is not None for m in metrics)]
    return max(obs, key=lambda s: s["observed_at"]) if obs else None


def compare(hit_vals, rest_vals):
    """Где хиты относительно остальных: above_all / below_all — за пределами
    всех остальных; above_median / below_median — все хиты по одну сторону
    медианы остальных; mixed — не отличает."""
    if not hit_vals or not rest_vals:
        return None
    med = statistics.median(rest_vals)
    out = {"hits": hit_vals, "rest_median": med, "rest_min": min(rest_vals),
           "rest_max": max(rest_vals), "n_rest": len(rest_vals)}
    if min(hit_vals) > max(rest_vals):
        out["verdict"] = "above_all"
    elif max(hit_vals) < min(rest_vals):
        out["verdict"] = "below_all"
    elif min(hit_vals) > med:
        out["verdict"] = "above_median"
    elif max(hit_vals) < med:
        out["verdict"] = "below_median"
    else:
        out["verdict"] = "mixed"
    return out


WHERE = {"above_all": "выше, чем у любого из остальных",
         "below_all": "ниже, чем у любого из остальных",
         "above_median": "выше медианы остальных",
         "below_median": "ниже медианы остальных"}


def study(snaps, videos, a):
    """Находки, пробелы и план. Чистая функция: данные — параметрами."""
    rows = a["videos"]                               # сверенные просмотры
    hits = [r for r in rows if r.get("hit")]
    rest = [r for r in rows if not r.get("hit")]
    n = len(rows)
    F, G, P = [], [], []

    def fact(fid, text, **extra):
        F.append({"id": fid, "claim_type": "FACT", "text": text, **extra})

    def hyp(fid, text, competing, **extra):
        F.append({"id": fid, "claim_type": "HYPOTHESIS",
                  "text": text + " Причинность не установлена.",
                  "competing_explanation": competing, **extra})

    # ── F1 распределение ────────────────────────────────────────────────
    views = sorted(r["views"] for r in rows)
    gap = None
    if len(views) >= 2:
        ratios = [(views[i + 1] / views[i] if views[i] else float("inf"), i)
                  for i in range(len(views) - 1)]
        k, i = max(ratios)
        if k >= GAP_RATIO:
            gap = {"low_max": views[i], "high_min": views[i + 1], "ratio": k,
                   "n_low": i + 1, "n_high": len(views) - i - 1}
    total = sum(views) or 1
    unv = [u for u in a.get("unverified", []) if u.get("verified_views") is None]
    tail = (f" Ещё {len(unv)} ролик(а) без сверки здесь не учтены "
            f"({', '.join(A._num(u['views']) for u in unv)} просм., не сверено)."
            if unv else "")
    if gap:
        top_share = sum(views[gap["n_low"]:]) / total
        fact("F1", f"Просмотры распределены ступенькой: {gap['n_low']} роликов набрали от "
                   f"{A._num(views[0])} до {A._num(gap['low_max'])}, {gap['n_high']} — от "
                   f"{A._num(gap['high_min'])} до {A._num(views[-1])}; между "
                   f"{A._num(gap['low_max'])} и {A._num(gap['high_min'])} нет ни одного "
                   f"ролика (разрыв в {gap['ratio']:.0f} раз), верхняя группа даёт "
                   f"{_pct(top_share)} всех просмотров, n={n}.{tail}", gap=gap,
             top_share=top_share)
    elif views:
        fact("F1", f"Просмотры от {A._num(views[0])} до {A._num(views[-1])}, медиана "
                   f"{A._num(statistics.median(views))}; разрыва в {GAP_RATIO}+ раз между "
                   f"соседними роликами нет, n={n}.{tail}")

    # ── F2 кто смотрит: просмотры на одного охваченного ─────────────────
    def per_reach(r):
        s = single(snaps, r["video_id"], ("views", "reach"))
        return s["views"] / s["reach"] if s and s["reach"] else None
    hv = [x for x in (per_reach(r) for r in hits) if x is not None]
    rv = [x for x in (per_reach(r) for r in rest) if x is not None]
    c = compare(hv, rv)
    if c:
        fact("F2", f"Просмотров на одного охваченного зрителя у хитов "
                   f"{', '.join(_num(x) for x in sorted(hv))}, у остальных — от "
                   f"{_num(c['rest_min'])} до {_num(c['rest_max'])} (медиана "
                   f"{_num(c['rest_median'])}): почти каждый просмотр хита — новый "
                   f"зритель, n={len(hv) + len(rv)} (Supermetrics, один источник).",
             compare=c,
             note="Это и арифметика: повторные просмотры даёт в основном постоянная "
                  "аудитория, её размер почти не меняется, и при огромном охвате их "
                  "доля мала сама по себе.")

    # ── F3 реакция зрителей ─────────────────────────────────────────────
    for metric, word in REACTIONS:
        def rate(r, metric=metric):
            m = matched(snaps, r["video_id"], ("views", metric))
            return m[metric] / m["views"] if m and m["views"] else None
        hv = [x for x in (rate(r) for r in hits) if x is not None]
        rv = [x for x in (rate(r) for r in rest) if x is not None]
        c = compare(hv, rv)
        if not c:
            continue
        fid = f"F3-{metric}"
        part = ("" if len(hv) == len(hits) else
                f" (сверенное значение есть у {len(hv)} из {len(hits)} хитов)")
        fact(fid, f"Доля {word} на просмотр у хитов {', '.join(_pct(x) for x in sorted(hv))}"
                  f"{part}, у остальных медиана {_pct(c['rest_median'])} (от "
                  f"{_pct(c['rest_min'])} до {_pct(c['rest_max'])}), n={len(hv) + len(rv)} "
                  "(оба источника совпали).", compare=c, metric=metric)
        # «у всех хитов» — только если значение есть у всех
        if c["verdict"] != "mixed" and len(hv) == len(hits):
            hyp(fid + "h", f"Доля {word} может отличать раздачу хитов: у всех хитов она "
                           f"{WHERE[c['verdict']]}, n={len(hv) + len(rv)}.",
                COMPETING_RATIO, compare=c, metric=metric)
    # сохранения — только у Supermetrics
    def fav_rate(r):
        s = single(snaps, r["video_id"], ("views", "favorites"))
        return s["favorites"] / s["views"] if s and s["views"] else None
    hv = [x for x in (fav_rate(r) for r in hits) if x is not None]
    rv = [x for x in (fav_rate(r) for r in rest) if x is not None]
    c = compare(hv, rv)
    if c:
        fact("F3-favorites", f"Доля сохранений на просмотр у хитов "
                             f"{', '.join(_pct(x) for x in sorted(hv))}, у остальных медиана "
                             f"{_pct(c['rest_median'])}, n={len(hv) + len(rv)} (Supermetrics).",
             compare=c, metric="favorites")

    # ── F4 удержание ────────────────────────────────────────────────────
    mech = policies.mechanical_dependency("duration_sec", "completion_rate")
    for metric, word, unit in (("completion_rate", "доля досмотров", "%"),
                               ("avg_view_time_sec", "среднее время просмотра", " с")):
        def ret(r, metric=metric):
            s = single(snaps, r["video_id"], (metric,))
            return s[metric] if s else None
        hv = [x for x in (ret(r) for r in hits) if x is not None]
        rv = [x for x in (ret(r) for r in rest) if x is not None]
        c = compare(hv, rv)
        if not c:
            continue
        show = _pct if unit == "%" else (lambda x: _num(x) + " с")
        fact(f"F4-{metric}", f"{word.capitalize()} у хитов {', '.join(show(x) for x in sorted(hv))}, "
                             f"у остальных медиана {show(c['rest_median'])} (от "
                             f"{show(c['rest_min'])} до {show(c['rest_max'])}), "
                             f"n={len(hv) + len(rv)} (Supermetrics, до 2026-10-01).",
             compare=c, metric=metric)
        if c["verdict"] != "mixed" and len(hv) == len(hits):
            competing = ("Длительность ролика механически задаёт долю досмотров (пара "
                         "зарегистрирована в реестре политики): разница может быть "
                         "разницей длительностей, а не зрительского интереса."
                         if metric == "completion_rate" and mech else
                         "Широкая аудитория незнакомых зрителей смотрит иначе, чем "
                         "постоянная: показатель может быть следствием охвата.")
            hyp(f"F4-{metric}h", f"{word.capitalize()} может отличать раздачу хитов: у всех "
                                 f"хитов она {WHERE[c['verdict']]}, n={len(hv) + len(rv)}.",
                competing, compare=c, metric=metric)

    # ── F5 жизненный цикл (один источник) ───────────────────────────────
    groups = {"young": [], "mid": [], "old": []}
    first_ages = []
    for vid, v in videos.items():
        obs = sorted((s for s in snaps if s["video_id"] == vid and s["source"] == "metricool"
                      and s.get("views") is not None), key=lambda s: s["observed_at"])
        if not obs:
            continue
        pub = datetime.fromisoformat(v["published_at"])
        first_ages.append((datetime.fromisoformat(obs[0]["observed_at"]) - pub).days)
        if len(obs) < 2 or obs[0]["views"] == 0:
            continue
        f, l = obs[0], obs[-1]
        age = (datetime.fromisoformat(f["observed_at"]) - pub).days
        days = (datetime.fromisoformat(l["observed_at"])
                - datetime.fromisoformat(f["observed_at"])).days
        if days <= 0:
            continue
        g = (l["views"] - f["views"]) / f["views"]
        key = "young" if age < YOUNG_DAYS else ("old" if age > OLD_DAYS else "mid")
        groups[key].append({"video_id": vid, "age": age, "days": days, "growth": g})
    spans = {k: [x["days"] for x in v] for k, v in groups.items() if v}
    if groups["young"] and groups["old"]:
        y, o = groups["young"], groups["old"]
        days = statistics.median([x["days"] for x in y + o])
        hyp("F5", f"Раздача может продолжаться в основном первые недели: ролики младше "
                  f"{YOUNG_DAYS} дней на первом замере прибавили за {days:.0f} дн. от "
                  f"{_pct(min(x['growth'] for x in y))} до {_pct(max(x['growth'] for x in y))} "
                  f"(n={len(y)}), старше {OLD_DAYS} дней — от "
                  f"{_pct(min(x['growth'] for x in o))} до {_pct(max(x['growth'] for x in o))} "
                  f"(n={len(o)}); один источник (Metricool), не сверено.",
            "Замеров всего два на ролик, и у Metricool нет метки свежести: прирост "
            "мог прийти в любой день между ними. Молодые ролики — другие по теме и "
            "времени публикации, сравнение по возрасту не отделено от остального.",
            groups=groups, spans=spans)

    # ── F6 ритм публикаций ──────────────────────────────────────────────
    days_ = sorted(date.fromisoformat(v["published_at"][:10]) for v in videos.values())
    gaps = [(b - a_).days for a_, b in zip(days_, days_[1:])]
    if gaps:
        fact("F6", f"Между публикациями медиана {_num(statistics.median(gaps), 1)} дн., "
                   f"самая длинная пауза {max(gaps)} дн., в один день выходило до "
                   f"{max(days_.count(d) for d in set(days_))} роликов, n={len(days_)}.",
             gaps=gaps)

    # ── G пробелы ───────────────────────────────────────────────────────
    src = ("src_foryou", "src_hashtag", "src_profile", "src_search", "src_sound")
    if not any(s.get(k) is not None for s in snaps for k in src):
        G.append({"id": "G1", "text": "Источники трафика (For You, профиль, поиск, звук, "
                                       "хештег) не отдаёт ни один источник — EXP-003 закрыт "
                                       "«не подтвердилось». Через что раздаётся ролик, не "
                                       "видно."})
    if first_ages:
        G.append({"id": "G2", "text": f"Первые часы после публикации не наблюдались: самый "
                                       f"ранний первый замер — через {min(first_ages)} дн. "
                                       f"после выхода ролика. Как алгоритм пробует ролик на "
                                       f"первой порции зрителей, не видно.",
                  "min_first_age": min(first_ages)})
    if not any("followers" in s for s in snaps):
        G.append({"id": "G3", "text": "Подписчиков и их прироста в выгрузках нет: какую "
                                       "долю просмотров дают подписчики, не видно."})
    sm = [s["observed_at"] for s in snaps if s["source"] == "supermetrics"]
    if sm:
        G.append({"id": "G4", "text": f"Охват, досмотры и время просмотра последний раз "
                                       f"измерены {max(sm)[:10]}: Supermetrics с 2026-10-01 "
                                       f"недоступен, у Metricool они пустые."})

    # ── P план улучшения ────────────────────────────────────────────────
    ids = {f["id"] for f in F}
    if "F1" in ids and F[0].get("gap"):
        g_ = F[0]["gap"]
        P.append({"basis": ["F1"], "text":
                  f"Набирать попытки: прорыв случился у {g_['n_high']} роликов из {n}, "
                  "остальные стоят в нижней группе. Публиковать по плану (пункт 6) — "
                  "один ролик в день, каждый проверяет гипотезу разбора.",
                  "action": "пункт 6 — план публикаций"})
    if "F5" in ids:
        P.append({"basis": ["F5"], "text":
                  "Не судить ролик по первой неделе: молодые ролики ещё прибавляют. "
                  f"Итог эксперимента — после {A.MATURE_AGE_DAYS} дней, это правило уже в "
                  "программе.", "action": "пункт 5 → 4 — итог через 30 дней"})
    shares_h = next((f for f in F if f["id"] == "F3-sharesh"), None)
    if shares_h and shares_h["compare"]["verdict"] in ("above_all", "above_median"):
        P.append({"basis": ["F3-sharesh"], "text":
                  "Проверить просьбу о репосте в подписи как эксперимент: доля репостов "
                  "у хитов выше медианы остальных, а журнал уже держит EXP-002 о репостах. "
                  "Вывод — только по правилу итога, после 5 зрелых роликов.",
                  "action": "идея от сессии Claude (процедура «Идеи из сессии»), "
                            "затем 5 → 2"})
    if mech:
        ok, code, _d = policies.promotion_allowed("duration_sec", "completion_rate",
                                                  "RECOMMENDATION")
        if not ok:
            P.append({"basis": ["F4-completion_rate"], "text":
                      "Длительность ради досмотров не менять: пара «длительность ↔ доля "
                      f"досмотров» закрыта политикой ({code}), EXP-001 заблокирован. "
                      "Досмотры здесь — описание, а не рычаг.", "action": "—"})
    if any(x["id"] == "G2" for x in G):
        P.append({"basis": ["G2"], "text":
                  "Мерить раньше: еженедельное обновление даёт недельный шаг. Чтобы "
                  "увидеть первые сутки, на следующий день после публикации попросить "
                  "сессию Claude «обнови данные».", "action": "сессия Claude"})
    if any(x["id"] == "G4" for x in G):
        P.append({"basis": ["G4"], "text":
                  "Охват и досмотры вернуть, когда эксперименту понадобятся (решение о "
                  "Supermetrics в CLAUDE.md): без них F2 и F4 не обновляются.",
                  "action": "решение владельца"})
    if a.get("hypotheses"):
        hs = ", ".join(h["id"] for h in a["hypotheses"])
        P.append({"basis": ["разбор"], "text":
                  f"Проверять гипотезы разбора о времени и подписи ({hs}) публикациями "
                  "по плану: это единственный способ отличить закономерность раздачи "
                  "от совпадения.", "action": "пункты 6 и 5"})
    for p in P:
        p["claim_type"] = "RECOMMENDATION"
    return {"policy_version": ALGO_POLICY_VERSION, "observed_at": a.get("observed_at"),
            "n": n, "n_hits": len(hits), "findings": F, "gaps": G, "plan": P}


def render(r):
    L = ["КАК АЛГОРИТМ РАЗДАЁТ РОЛИКИ АККАУНТА",
         f"по данным этого аккаунта на {r['observed_at']}; роликов {r['n']}, хитов "
         f"{r['n_hits']}. Устройство TikTok в данных не видно — только поведение раздачи.",
         ""]
    for f in r["findings"]:
        L.append(f"{f['id']}  {f['claim_type']} · {f['text']}")
        if f.get("note"):
            L.append(f"      оговорка: {f['note']}")
        if f.get("competing_explanation"):
            L.append(f"      конкурирующее объяснение: {f['competing_explanation']}")
    if r["gaps"]:
        L += ["", "ЧЕГО НЕ ВИДНО"]
        L += [f"{g['id']}  {g['text']}" for g in r["gaps"]]
    L += ["", "ПЛАН УЛУЧШЕНИЯ · RECOMMENDATION"]
    for i, p in enumerate(r["plan"], 1):
        L.append(f"{i}. {p['text']}")
        L.append(f"   основание: {', '.join(p['basis'])} · в программе: {p['action']}")
    L += ["", "Это ставки на проверку, а не обещание охватов: FACT — по данным "
              "аккаунта, всё остальное проверяется публикациями."]
    return "\n".join(L)


def main(argv=None):
    from mobile.audit import scrub
    snaps, videos = load()
    print(scrub(render(study(snaps, videos, A.analyze()))))
    return 0


if __name__ == "__main__":
    from core import console
    console.setup()
    sys.exit(main())
