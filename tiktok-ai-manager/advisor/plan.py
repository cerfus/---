#!/usr/bin/env python3
"""План публикаций по сохранённым идеям и календарь .ics.

    python -m advisor.plan                  план на экран и data/runtime/plan.ics
    python -m advisor.plan --no-ics         только план
    python -m advisor.plan --start 2026-10-12

RECOMMENDATION, а не FACT: план раскладывает идеи по дням так, чтобы
публикация ПРОВЕРЯЛА гипотезу, ради которой идея написана, и не смешивала
её с другими гипотезами о времени:

  * идея проверяет признак времени (час, день, активность аудитории) —
    слот внутри этого признака и вне остальных временных гипотез;
  * идея проверяет что-то другое (подпись, длительность, тему) — слот вне
    всех временных гипотез, чтобы время не примешивалось к результату.

Признаки слота считаются той же функцией, что признаки роликов в разборе
(analysis.time_attrs), поэтому «в окне» значит одно и то же и там, и тут.
Если развести гипотезы нельзя (окна совпадают на всех часах), слот всё
равно ставится, но с оговоркой, с какой гипотезой проверка смешана.

Нейтральные идеи (не о времени) стоят вне всех временных окон — и тем
самым дают экспериментам о времени контроль того же периода
(experiments.control).

Один ролик в день. Публикации нет: план — это календарь для человека,
publishing.submit остаётся FALSE.
"""
import hashlib
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A, experiments as E   # noqa: E402

HORIZON_DAYS = 21
LEAD_HOURS = 2                  # запас на съёмку: слот раньше «сейчас + 2 ч» не предлагается
TIME_ATTRS = {"hour_utc", "hour_local", "weekday", "weekday_local", "activity_pct"}
ICS_PATH = ROOT / "data" / "runtime" / "plan.ics"
EVENT_MINUTES = 30
REMIND_MINUTES = 60


def _hour_penalty(h):
    """Насколько неудобен час для человека: дневные часы — 0, ночь — 3."""
    if 10 <= h <= 22:
        return 0
    if h in (23, 8, 9):
        return 1
    if h in (0, 7):
        return 2
    return 3


def _tz(ctx):
    return ctx.get("tz") or timezone.utc


def slot_for(idea_h, others, day, ctx, not_before=None):
    """Лучший час дня для идеи: (datetime UTC, с какими гипотезами смешан)
    или None. idea_h — гипотеза идеи о времени (или None), others —
    остальные временные гипотезы."""
    tz = _tz(ctx)
    best = None
    for h in range(24):
        local = datetime(day.year, day.month, day.day, h, tzinfo=tz)
        if not_before is not None and local < not_before:
            continue
        attrs = A.time_attrs(local, ctx)
        if idea_h is not None and A.holds(idea_h, attrs) is not True:
            continue
        mixed = [o["id"] for o in others if A.holds(o, attrs) is True]
        # сначала — без смешения, потом — удобный час, потом — ближе к 15:00
        key = (len(mixed), _hour_penalty(h), abs(h - 15))
        if best is None or key < best[0]:
            best = (key, local.astimezone(timezone.utc), mixed)
    return None if best is None else (best[1], best[2])


def build(ideas, analysis, start=None, ctx=None, states=None, now=None):
    """[{idea, n, hypothesis, at, mixed, stale, note}] — в порядке дат.

    Каждая идея берёт ближайший свободный день, где смешения с чужими
    гипотезами меньше всего, а не просто ближайший день: нейтральная идея
    не встанет на воскресенье, пока есть гипотеза «воскресенье», — и этот
    день останется идее, которая её проверяет. n — номер идеи в файле, по
    нему её берут в работу.

    Журнал экспериментов учитывается: идея, уже вышедшая роликом, в план не
    ставится (done), взятая в работу — ставится с пометкой (taken).
    """
    ctx = A.context() if ctx is None else ctx
    by_title = E.idea_states(E.load() if states is None else states)
    hyps = {h["id"]: h for h in analysis["hypotheses"]}
    timed = [h for h in analysis["hypotheses"] if h.get("attribute") in TIME_ATTRS
             and h.get("bounds")]
    # без явного начала план начинается сегодня: сегодняшний вечерний слот
    # полезнее завтрашнего, если до него хватает времени на съёмку
    if start is None:
        now = now or datetime.now(timezone.utc)
        first = now.astimezone(_tz(ctx)).date()
        not_before = now + timedelta(hours=LEAD_HOURS)
    else:
        first, not_before = start, None
    rows = []
    for n, idea in enumerate(ideas, 1):
        hid = idea.get("tests_hypothesis")
        h = hyps.get(hid)
        code, state = by_title.get(idea.get("title"), (None, None))
        rows.append({"n": n, "idea": idea, "hypothesis": hid, "at": None, "mixed": [],
                     "stale": E.stale_reason(idea, analysis), "note": None,
                     "done": code if state == "published" else None,
                     "taken": code if state == "taken" else None,
                     "_h": h if h is not None and h in timed else None})
    taken = set()
    for row in rows:
        if row["stale"] or row["done"]:
            continue
        others = [o for o in timed if o is not row["_h"]]
        best = None
        for d in range(HORIZON_DAYS):
            day = first + timedelta(days=d)
            if day in taken:
                continue
            found = slot_for(row["_h"], others, day, ctx, not_before)
            if found and (best is None or len(found[1]) < len(best[1][1])):
                best = (day, found)
                if not found[1]:
                    break
        if best is None:
            row["note"] = f"за {HORIZON_DAYS} дн. слота под {row['hypothesis']} не нашлось"
            continue
        taken.add(best[0])
        row["at"], row["mixed"] = best[1]
    for row in rows:
        row.pop("_h")
    far = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(rows, key=lambda r: (r["at"] or far, r["n"]))


def _fmt_local(at, ctx):
    loc = at.astimezone(_tz(ctx))
    wd = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")[loc.weekday()]
    return f"{wd} {loc:%d.%m %H:%M}"


def render(plan, analysis, ctx, source=None, states=None):
    tzname = ctx.get("tz_name") or "UTC"
    L = ["ПЛАН ПУБЛИКАЦИЙ · RECOMMENDATION",
         f"идеи: {source or '—'}; гипотезы — по разбору на {analysis.get('observed_at')}",
         f"время — {tzname}, в скобках UTC; один ролик в день", ""]
    if not plan:
        return "\n".join(L + ["идей нет — сначала пункт «Идеи для следующих видео»"])
    done = [r for r in plan if r.get("done")]
    if done:
        L += ["уже вышли: " + "; ".join(f"«{r['idea'].get('title')}» ({r['done']})"
                                         for r in done), ""]
    for r in plan:
        title = r["idea"].get("title")
        if r.get("done"):
            continue
        if r["stale"]:
            L += [f"{r['n']}. «{title}» — не в плане: {r['stale']}", ""]
            continue
        if r["at"] is None:
            L += [f"{r['n']}. «{title}» — не в плане: {r['note']}", ""]
            continue
        check_at = r["at"] + timedelta(days=A.MATURE_AGE_DAYS)
        L.append(f"{r['n']}. {_fmt_local(r['at'], ctx)} ({r['at']:%H:%M} UTC) — «{title}»")
        L.append(f"   проверяет {r['hypothesis']}"
                 + (f"; смешано с {', '.join(r['mixed'])}: развести не вышло, "
                    "итог не отделит одну гипотезу от другой" if r["mixed"]
                    else "; вне окон остальных гипотез о времени"))
        L.append(f"   уже в работе: {r['taken']}; после публикации — 5 → 3, ссылка"
                 if r.get("taken") else
                 f"   до публикации: меню 5 → 2, идея №{r['n']}; после — 5 → 3, ссылка")
        L.append(f"   итог — не раньше {check_at.astimezone(_tz(ctx)):%d.%m} "
                 f"({A.MATURE_AGE_DAYS} дн.)")
        L.append("")
    L += progress(plan, states)
    n = analysis["hypotheses"][0]["n_sample"] if analysis["hypotheses"] else 0
    L.append(f"Гипотезы стоят на n={n} зрелых роликах при пороге "
             f"{analysis.get('min_sample_required')}: план проверяет их, а не "
             "обещает охваты.")
    return "\n".join(L)


def today(ideas, analysis, ctx=None, states=None, start=None, now=None):
    """Строки «что делать сейчас» для экрана запуска: ближайшая публикация
    по плану и эксперименты, у которых готов итог. Ничего не пишет."""
    ctx = A.context() if ctx is None else ctx
    states = E.load() if states is None else states
    L = []
    if not ideas:
        L.append("Идей нет — пункт 4 «Идеи для следующих видео».")
    else:
        rows = [r for r in build(ideas, analysis, start=start, ctx=ctx, states=states,
                                 now=now)
                if r["at"]]
        if rows:
            r = rows[0]
            when = f"{_fmt_local(r['at'], ctx)} ({r['at']:%H:%M} UTC)"
            L.append(f"Ближайшая публикация: {when} — «{r['idea'].get('title')}» "
                     f"(проверяет {r['hypothesis']}).")
            L.append(f"  уже в работе {r['taken']}: после публикации — пункт 5 → 3"
                     if r.get("taken") else
                     f"  сначала пункт 5 → 2, идея №{r['n']}; после публикации — 5 → 3")
        else:
            L.append("В плане пусто — пункт 4: новые идеи.")
    ready = [code for code, st in sorted(states.items()) if st["status"] in E.OPEN
             for ev in [E.evaluate(st, analysis, ctx=ctx)]
             if ev.get("rule") and ev["rule"][0] != "inconclusive"]
    if ready:
        L.append(f"Готов итог: {', '.join(ready)} — пункт 5 → 4.")
    return L


def progress(plan, states=None):
    """Сколько роликов ещё нужно каждому открытому эксперименту и хватает ли
    под это идей в плане. Строки текста; пусто — открытых экспериментов нет."""
    states = E.load() if states is None else states
    open_ = [st for st in states.values()
             if st["origin"] == "idea" and st["status"] in ("preregistered", "running")]
    if not open_:
        return []
    L = ["Прогресс экспериментов:"]
    for st in sorted(open_, key=lambda x: x["code"]):
        hid = st["base"].get("hypothesis_id") or "—"
        need = st.get("min_sample") or E.DEFAULT_MIN_SAMPLE
        have = len(st["videos"])
        planned = sum(1 for r in plan if r["at"] and not r.get("done")
                      and r["hypothesis"] == hid)
        line = f"  {st['code']} · {hid}: роликов {have} из {need}"
        if have >= need:
            line += " — набрано, ждём зрелости (30 дн.)"
        else:
            rest = need - have
            line += f"; ещё нужно {rest}, в плане {planned}"
            if planned < rest:
                line += f" — новые идеи под {hid}: пункт 4"
        L.append(line)
    return L + [""]


# ──────────────────────────────── .ics ───────────────────────────────────────

def _esc(text):
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line):
    """RFC 5545 3.1: строка не длиннее 75 октетов, продолжение — с пробела.
    Режется по границе символа UTF-8, а не посреди него."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        limit = 75 if not out else 74
        if len(cur) + len(b) > limit:
            out.append(cur)
            cur = b""
        cur += b
    out.append(cur)
    return b"\r\n ".join(out)


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ics(plan, now=None):
    """Календарь (bytes, CRLF). UID детерминирован: повторный импорт того же
    плана обновляет события, а не плодит копии."""
    now = now or datetime.now(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tiktok-ai-manager//plan//RU",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:TikTok план"]
    for r in plan:
        if r["at"] is None or r["stale"] or r.get("done"):
            continue
        idea = r["idea"]
        title = idea.get("title") or "идея"
        uid_base = hashlib.sha256(f"{title}|{r['hypothesis']}".encode("utf-8")).hexdigest()[:16]
        desc = "\n".join(x for x in (
            f"Проверяет {r['hypothesis']}" + (f" (смешано с {', '.join(r['mixed'])})"
                                             if r["mixed"] else ""),
            f"Что снять: {idea.get('what_to_film', '')}",
            f"Подпись: {idea['caption_draft']}" if idea.get("caption_draft") else "",
            f"До публикации: меню 5 → 2 (идея №{r['n']}). После: 5 → 3, ссылка на ролик.",
            "Публикует человек: программа ничего не выкладывает.") if x)
        for kind, at, summary, body, alarm in (
                ("post", r["at"], f"TikTok: {title}", desc, True),
                ("check", r["at"] + timedelta(days=A.MATURE_AGE_DAYS),
                 f"Итог эксперимента: {title}",
                 f"Прошло {A.MATURE_AGE_DAYS} дн. Меню 5 → 1: итог по {r['hypothesis']}.",
                 False)):
            lines += ["BEGIN:VEVENT", f"UID:{uid_base}-{kind}@tiktok-ai-manager",
                      f"DTSTAMP:{_stamp(now)}", f"DTSTART:{_stamp(at)}",
                      f"DURATION:PT{EVENT_MINUTES}M", f"SUMMARY:{_esc(summary)}",
                      f"DESCRIPTION:{_esc(body)}"]
            if alarm:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                          f"TRIGGER:-PT{REMIND_MINUTES}M",
                          f"DESCRIPTION:{_esc(summary)}", "END:VALARM"]
            lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return b"".join(_fold(l) + b"\r\n" for l in lines)


def main(argv=None):
    import argparse
    from mobile.audit import scrub
    ap = argparse.ArgumentParser(description="План публикаций и календарь .ics.")
    ap.add_argument("--start", help="первый день плана, ГГГГ-ММ-ДД (по умолчанию сегодня, слоты не раньше чем через 2 ч)")
    ap.add_argument("--no-ics", action="store_true", help="не писать календарь")
    ap.add_argument("--out", help=f"куда писать календарь (по умолчанию {ICS_PATH})")
    args = ap.parse_args(argv)
    ctx = A.context()
    a = A.analyze()
    path, ideas = E.latest_ideas()
    start = date.fromisoformat(args.start) if args.start else None
    plan = build(ideas, a, start=start, ctx=ctx)
    print(scrub(render(plan, a, ctx, path.name if path else None)))
    if args.no_ics or not any(r["at"] for r in plan):
        return 0
    out = Path(args.out) if args.out else ICS_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ics(plan))
    shown = out.relative_to(ROOT).as_posix() if out.is_relative_to(ROOT) else out
    print(f"\nКалендарь: {shown} — откройте файл (двойной щелчок), события "
          "добавятся в календарь; можно переслать себе на телефон.")
    return 0


if __name__ == "__main__":
    from core import console
    console.setup()
    sys.exit(main())
