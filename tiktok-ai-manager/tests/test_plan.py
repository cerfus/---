#!/usr/bin/env python3
"""План публикаций и календарь .ics.

Главное, что держит тест: слот проверяет ровно ту гипотезу, ради которой
идея написана, — признаки слота считаются той же функцией, что признаки
роликов, а границы гипотез воспроизводят счёт разбора. Если развести
гипотезы нельзя, план говорит об этом, а не молчит.
"""
import copy
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A, experiments as E, plan as P   # noqa: E402
from insights.validator import find_violations                   # noqa: E402

RESULTS = []
START = date(2026, 10, 9)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def unfold(raw):
    return raw.decode("utf-8").replace("\r\n ", "").split("\r\n")


def main():
    ctx = A.context()
    a = A.analyze()
    rows, _ = A.load(A.ROOT, ctx)

    print("=== A. признаки слота = признаки ролика ===")
    diff = [(r["video_id"], k) for r in rows
            for k, v in A.time_attrs(datetime.fromisoformat(r["published_at"]), ctx).items()
            if r.get(k) != v]
    check("A1 time_attrs совпадает с признаками роликов в разборе", not diff and rows,
          f"роликов {len(rows)}, расхождений {len(diff)}")
    mature = [v for v in a["videos"] if v["mature"]]
    hits = [v for v in mature if v["hit"]]
    rest = [v for v in mature if not v["hit"]]
    bad = []
    for h in a["hypotheses"]:
        hm = sum(1 for v in hits if A.holds(h, v) is True)
        rm = sum(1 for v in rest if A.holds(h, v) is True)
        if (hm, rm) != (h["hits_matching"], h["rest_matching"]):
            bad.append((h["id"], hm, rm))
    check("A2 машинные границы гипотез воспроизводят счёт разбора (хиты / остальные)",
          a["hypotheses"] and not bad, str(bad))
    check("A3 у каждой гипотезы есть границы", all(h.get("bounds") for h in a["hypotheses"]))

    print("\n=== B. план по сохранённым идеям ===")
    path, ideas = E.latest_ideas()
    plan = P.build(ideas, a, start=START, ctx=ctx)
    hyps = {h["id"]: h for h in a["hypotheses"]}
    timed = [h for h in a["hypotheses"] if h["attribute"] in P.TIME_ATTRS]
    placed = [r for r in plan if r["at"]]
    check("B1 все идеи получили слот", len(placed) == len(ideas) and ideas,
          f"{len(placed)} из {len(ideas)}")
    own_ok, iso_ok = True, True
    for r in placed:
        attrs = A.time_attrs(r["at"], ctx)
        h = hyps.get(r["hypothesis"])
        if h in timed and A.holds(h, attrs) is not True:
            own_ok = False
        foreign = [o["id"] for o in timed if o is not h and A.holds(o, attrs) is True]
        if foreign != r["mixed"]:
            iso_ok = False
    check("B2 слот идеи о времени лежит внутри её гипотезы", own_ok)
    check("B3 «смешано с» называет ровно те чужие гипотезы, в окна которых попал слот", iso_ok)
    check("B4 на текущих данных все пять идей разведены без смешения",
          all(not r["mixed"] for r in placed), str([(r["n"], r["mixed"]) for r in placed]))
    days = [r["at"].astimezone(ctx["tz"] or timezone.utc).date() for r in placed]
    check("B5 один ролик в день, в пределах горизонта, по порядку дат",
          len(set(days)) == len(days) and days == sorted(days)
          and min(days) >= START and max(days) < START + timedelta(days=P.HORIZON_DAYS))
    h3 = next((r for r in placed if r["hypothesis"] == "H3"), None)
    check("B6 идея под «воскресенье» стоит в воскресенье (UTC)",
          h3 is not None and h3["at"].weekday() == 6, str(h3 and h3["at"]))
    neutral = [r for r in placed if hyps.get(r["hypothesis"]) not in timed]
    check("B7 идеи не о времени стоят вне всех временных гипотез",
          neutral and all(not any(A.holds(o, A.time_attrs(r["at"], ctx)) for o in timed)
                          for r in neutral))
    check("B8 удобное время: нейтральные идеи — днём (10–22 по поясу бренда)",
          all(10 <= r["at"].astimezone(ctx["tz"] or timezone.utc).hour <= 22 for r in neutral))

    sunday = START + timedelta(days=(6 - START.weekday()) % 7)
    idea_h2 = next(i for i in ideas if i["tests_hypothesis"] == "H2")
    idea_h3 = next(i for i in ideas if i["tests_hypothesis"] == "H3")
    p9 = P.build([idea_h2, idea_h3], a, start=sunday, ctx=ctx)
    by = {r["hypothesis"]: r for r in p9}
    check("B9 старт в воскресенье: нейтральная идея уходит на понедельник, "
          "воскресенье остаётся идее про воскресенье",
          by["H2"]["at"].weekday() == 0 and not by["H2"]["mixed"]
          and by["H3"]["at"].weekday() == 6 and not by["H3"]["mixed"],
          f"H2 {by['H2']['at']}, H3 {by['H3']['at']}")

    print("\n=== C. честность, когда развести нельзя ===")
    a2 = copy.deepcopy(a)
    twin = dict(copy.deepcopy(hyps["H1"]), id="H9")
    a2["hypotheses"].append(twin)
    p2 = P.build([dict(ideas[0], _snapshot=None)], a2, start=START, ctx=ctx)
    check("C1 гипотеза-близнец: слот ставится, смешение названо",
          p2[0]["at"] is not None and p2[0]["mixed"] == ["H9"], str(p2[0]["mixed"]))
    txt = P.render(p2, a2, ctx)
    check("C2 в тексте плана сказано, что итог не отделит одну гипотезу от другой",
          "смешано с H9" in txt and "не отделит" in txt)
    a3 = copy.deepcopy(a)
    for h in a3["hypotheses"]:
        if h["id"] == "H4":
            h["bounds"] = {"kind": "range", "lo": 101, "hi": 102}
    idea_h4 = next(i for i in ideas if i["tests_hypothesis"] == "H4")
    p3 = P.build([dict(idea_h4, _snapshot=None)], a3, start=START, ctx=ctx)
    check("C3 невыполнимая гипотеза — не в плане, с причиной",
          p3[0]["at"] is None and "не нашлось" in p3[0]["note"]
          and "не в плане" in P.render(p3, a3, ctx))
    stale = dict(ideas[0], _snapshot={"H1": "другая формулировка"})
    p4 = P.build([stale], a, start=START, ctx=ctx)
    check("C4 идея под изменившуюся гипотезу — устарела, слота нет",
          p4[0]["at"] is None and "заново" in (p4[0]["stale"] or ""))
    try:
        E.register_idea(stale, a, Path(tempfile.mkdtemp()) / "r.jsonl")
        refused = False
    except ValueError as exc:
        refused = "заново" in str(exc)
    check("C5 устаревшую идею нельзя взять в работу", refused)
    check("C6 снимок совпадает — идея не устарела",
          E.stale_reason(dict(ideas[0], _snapshot={h["id"]: h["statement"]
                                                   for h in a["hypotheses"]}), a) is None)
    from advisor import ideas as I
    doc = I.generate(a, offline=True)
    check("C7 новые файлы идей несут снимок гипотез",
          doc.get("hypotheses") == {h["id"]: h["statement"] for h in a["hypotheses"]})

    print("\n=== D. текст ===")
    text = P.render(plan, a, ctx, path.name)
    viol = [l for l in text.splitlines() if find_violations(l, "RECOMMENDATION")]
    check("D1 строки плана проходят валидатор формулировок", not viol, str(viol[:1]))
    check("D2 план назван RECOMMENDATION и говорит о размере выборки",
          "RECOMMENDATION" in text and f"n={a['hypotheses'][0]['n_sample']}" in text)
    check("D3 у каждой идеи — что сделать до и после публикации и когда итог",
          text.count("до публикации: меню 5 → 2") == len(placed)
          and text.count("итог — не раньше") == len(placed))

    print("\n=== E. календарь .ics ===")
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    raw = P.ics(plan, now=now)
    phys = raw.split(b"\r\n")
    check("E1 только CRLF, без одиночных LF", raw.count(b"\n") == raw.count(b"\r\n"))
    check("E2 физические строки не длиннее 75 октетов", all(len(l) <= 75 for l in phys),
          str(max(len(l) for l in phys)))
    try:
        for l in phys:
            l.decode("utf-8")
        whole = True
    except UnicodeDecodeError:
        whole = False
    check("E3 свёртка не режет символы UTF-8 пополам", whole)
    lines = unfold(raw)
    check("E4 BEGIN/END сбалансированы, календарь закрыт",
          lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"
          and lines.count("BEGIN:VEVENT") == lines.count("END:VEVENT") == 2 * len(placed))
    starts = sorted(l[8:] for l in lines if l.startswith("DTSTART:"))
    want = sorted([P._stamp(r["at"]) for r in placed]
                  + [P._stamp(r["at"] + timedelta(days=A.MATURE_AGE_DAYS)) for r in placed])
    check("E5 DTSTART — в UTC (Z) и совпадает с планом и днём итога", starts == want)
    uids = [l for l in lines if l.startswith("UID:")]
    check("E6 UID уникальны и не меняются от прогона к прогону",
          len(set(uids)) == len(uids)
          and uids == [l for l in unfold(P.ics(plan, now=now + timedelta(days=1)))
                       if l.startswith("UID:")])
    check("E7 экранирование: , ; \\ и перевод строки",
          P._esc("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne")
    check("E8 у публикации есть напоминание за час", lines.count("TRIGGER:-PT60M") == len(placed))
    check("E9 в календаре нет слов про автопубликацию — только человек",
          "Публикует человек" in raw.decode("utf-8").replace("\r\n ", ""))

    print("\n=== F. запуск ===")
    out = Path(tempfile.mkdtemp()) / "plan.ics"
    p = subprocess.run([sys.executable, "-m", "advisor.plan", "--start", "2026-10-09",
                        "--out", str(out)], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    check("F1 CLI пишет календарь по указанному пути",
          p.returncode == 0 and out.exists() and b"BEGIN:VEVENT" in out.read_bytes(),
          p.stderr[-200:])
    import runpy
    key = next(k for k, _, fn in runpy.run_path(str(ROOT / "scripts" / "menu.py"))["ITEMS"]
               if fn and fn.__name__ == "plan")
    p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                       input=f"{key}\n\n0\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    check("F2 пункт меню показывает план и путь к календарю",
          p.returncode == 0 and "ПЛАН ПУБЛИКАЦИЙ" in p.stdout and "Календарь:" in p.stdout)
    from mobile import commands as C
    saved = P.ics
    P.ics = lambda *a_, **k_: (_ for _ in ()).throw(AssertionError("телефон пишет .ics"))
    try:
        txt = C.REGISTRY["/plan"].handler(None)
        no_write = True
    except AssertionError:
        txt, no_write = "", False
    finally:
        P.ics = saved
    check("F3 /plan в Telegram показывает план и журнал, календарь не пишет",
          no_write and "ПЛАН ПУБЛИКАЦИЙ" in txt and "ЭКСПЕРИМЕНТЫ" in txt)
    src = (ROOT / "advisor" / "plan.py").read_text(encoding="utf-8")
    check("F4 в плане нет публикации", not any(w in src for w in ("publish(", "submit(")))

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
