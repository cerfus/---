#!/usr/bin/env python3
"""Журнал экспериментов: предрегистрация, привязка, оценка.

Все записи идут во временный журнал. Настоящий experiments/register.jsonl
тест только читает — и в конце доказывает, что не тронул его ни байтом.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A, experiments as E     # noqa: E402
from insights.validator import find_violations          # noqa: E402

RESULTS = []
REAL = ROOT / "experiments" / "register.jsonl"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analysis_stub(median=1000, unverified=(), videos=()):
    return {"policy_version": "stub", "observed_at": "2026-09-17T10:00:00+00:00",
            "median_views_mature": median,
            "hypotheses": [{"id": "H1", "statement": "Признак «День публикации: "
                            "воскресенье» может быть связан с залётом, n=10. "
                            "Причинность не установлена."}],
            "videos": list(videos), "unverified": list(unverified)}


IDEA = {"title": "Воскресный ролик", "what_to_film": "Ролик о персонаже",
        "caption_draft": "черновик", "when_to_post": "воскресенье, 19:00 UTC",
        "tests_hypothesis": "H1", "data_basis": "H1", "success_check":
        "Через 30 дней сравнить с медианой."}


def main():
    real_before = sha(REAL)

    print("=== A. настоящий журнал (только чтение) ===")
    st = E.load()
    check("A1 журнал читается, EXP-001…004 на месте",
          {"EXP-001", "EXP-002", "EXP-003", "EXP-004"} <= set(st), str(sorted(st)))
    e3 = st["EXP-003"]
    ev_file = ROOT / e3["events"][-1]["evidence"] if e3["events"] else None
    check("A2 EXP-003 закрыт «не подтвердилось», доказательство существует",
          e3["status"] == "concluded" and e3["verdict"] == "not_confirmed"
          and ev_file is not None and ev_file.exists(), str(e3.get("verdict")))
    a = A.analyze()
    ev1 = E.evaluate(st["EXP-001"], a)
    check("A3 EXP-001 заблокирован механической парой, и пара всё ещё в реестре",
          st["EXP-001"]["status"] == "blocked" and ev1["block_still_valid"] is True)
    check("A4 исходные записи не тронуты: у EXP-001 прежняя гипотеза",
          st["EXP-001"]["base"]["hypothesis"].startswith("Возврат к хронометражу"))
    check("A5 у события concluded/blocked есть время и причина",
          all(e.get("at") and (e.get("result") or e.get("note"))
              for s in st.values() for e in s["events"]
              if e["event"] in ("concluded", "blocked")))

    print("\n=== B. предрегистрация ===")
    tmp = Path(tempfile.mkdtemp()) / "register.jsonl"
    seed = [{"id": "EXP-001", "status": "proposed", "created_at": "2026-09-17",
             "hypothesis": "исходная", "min_sample": 5}]
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in seed),
                   encoding="utf-8")
    seed_bytes = tmp.read_bytes()
    code = E.register_idea(IDEA, analysis_stub(1000), tmp, now="2026-10-01T00:00:00+00:00")
    st = E.load(tmp)
    check("B1 следующий код — EXP-002", code == "EXP-002", code)
    check("B2 статус — preregistered", st[code]["status"] == "preregistered")
    check("B3 база сравнения зафиксирована при регистрации",
          st[code]["base"]["baseline"]["median_views_mature"] == 1000)
    check("B4 гипотеза взята из разбора, а не из текста идеи",
          st[code]["hypothesis"].startswith("Признак «День публикации"))
    check("B5 журнал только дописывается: прежние байты на месте",
          tmp.read_bytes().startswith(seed_bytes))
    bad = dict(IDEA, tests_hypothesis="H9", data_basis="")
    try:
        E.register_idea(bad, analysis_stub(), tmp)
        refused = False
    except ValueError:
        refused = True
    check("B6 идея без проверяемой гипотезы не регистрируется", refused)

    print("\n=== C. привязка ролика ===")
    vids = {"111111111111111111": {"video_id": "111111111111111111",
                                   "published_at": "2026-09-20T10:00:00+00:00"},
            "222222222222222222": {"video_id": "222222222222222222",
                                   "published_at": "2026-10-05T10:00:00+00:00"},
            "333333333333333333": {"video_id": "333333333333333333",
                                   "published_at": "2026-10-06T10:00:00+00:00"}}
    ok, why = E.link("EXP-099", "222222222222222222", tmp, videos=vids)
    check("C1 неизвестный эксперимент — отказ", not ok and "нет" in why)
    ok, why = E.link(code, "просто текст", tmp, videos=vids)
    check("C2 не video_id и не ссылка — отказ", not ok)
    ok, why = E.link(code, "999999999999999999", tmp, videos=vids)
    check("C3 ролика нет в данных — отказ с подсказкой про выгрузку",
          not ok and "выгрузка" in why)
    ok, why = E.link(code, "111111111111111111", tmp, videos=vids)
    check("C4 ролик раньше регистрации — отказ: это подгонка",
          not ok and "подгонка" in why, why)
    ok, why = E.link(code, "https://www.tiktok.com/@gulyashik52/video/222222222222222222?utm=x",
                     tmp, videos=vids)
    check("C5 ссылка с хвостом принимается, ролик привязан", ok, str(why))
    check("C6 статус — running", E.load(tmp)[code]["status"] == "running")
    ok, why = E.link(code, "222222222222222222", tmp, videos=vids)
    check("C7 повторная привязка — отказ", not ok and "уже" in why)
    E.conclude(code, "stub", "закрыт для проверки", "нет", tmp)
    ok, why = E.link(code, "333333333333333333", tmp, videos=vids)
    check("C8 к закрытому эксперименту не привязать", not ok and "закрыта" in why)

    print("\n=== D. оценка ===")
    tmp2 = Path(tempfile.mkdtemp()) / "register.jsonl"
    c2 = E.register_idea(IDEA, analysis_stub(1000), tmp2, now="2026-09-01T00:00:00+00:00")
    many = {f"4{i:017d}": {"video_id": f"4{i:017d}",
                           "published_at": "2026-09-10T00:00:00+00:00"} for i in range(5)}
    for vid in many:
        E.link(c2, vid, tmp2, videos=many)
    young = [{"video_id": "4" + "0" * 17, "views": 5000, "age_days": 10,
              "observed_at": "2026-09-20T00:00:00+00:00", "verified_views": None,
              "verified_at": None, "url": "", "source": "metricool", "caption": ""}]
    ev = E.evaluate(E.load(tmp2)[c2], analysis_stub(1000, unverified=young))
    rows = {r["video_id"]: r for r in ev["videos"]}
    check("D1 молодой ролик — «ждём ещё N дн.»",
          "ждём ещё 20 дн." in rows["4" + "0" * 17]["state"], rows["4" + "0" * 17]["state"])
    check("D2 ролик без наблюдений — просьба о выгрузке",
          "нужна новая выгрузка" in rows["4" + "0" * 16 + "1"]["state"])
    mature = [{"video_id": vid, "views": 900 + 200 * i, "age_days": 40,
               "observed_at": "2026-10-20T00:00:00+00:00", "verified_views": None,
               "verified_at": None, "url": "", "source": "metricool", "caption": ""}
              for i, vid in enumerate(many)]
    ev = E.evaluate(E.load(tmp2)[c2], analysis_stub(1000, unverified=mature[:3]))
    check("D3 меньше min_sample зрелых — вывода нет",
          ev["n_mature"] == 3 and "вывода пока нет" in ev["summary"], ev["summary"])
    check("D4 несверенное помечено «не сверено»",
          all("не сверено" in r["state"] for r in ev["videos"] if "views" in r))
    ev = E.evaluate(E.load(tmp2)[c2], analysis_stub(1000, unverified=mature))
    check("D5 при min_sample — счёт «k из n выше медианы», с n",
          ev["n_above"] == 4 and "4 из 5" in ev["summary"] and "n=5" in ev["summary"],
          ev["summary"])
    ev_moved = E.evaluate(E.load(tmp2)[c2], analysis_stub(5000, unverified=mature))
    check("D6 база не сдвигается задним числом: медиана регистрации, а не текущая",
          ev_moved["baseline"] == 1000 and ev_moved["n_above"] == 4)
    texts = [ev["summary"]] + [r["state"] for r in ev["videos"]]
    viol = [(t, find_violations(t, "RECOMMENDATION")) for t in texts
            if find_violations(t, "RECOMMENDATION")]
    check("D7 итоги проходят валидатор формулировок", not viol, str(viol[:1]))

    print("\n=== E. блокировка политикой ===")
    tmp3 = Path(tempfile.mkdtemp()) / "register.jsonl"
    c3 = E.register_idea(IDEA, analysis_stub(), tmp3)
    E.block(c3, "mechanically_dependent", "проверка", pair=("views", "likes"), path=tmp3)
    ev = E.evaluate(E.load(tmp3)[c3], analysis_stub())
    check("E1 пары нет в реестре — блокировка помечена устаревшей",
          ev["block_still_valid"] is False)
    check("E2 устаревшая блокировка видна в тексте",
          "устарела" in E.render(E.load(tmp3), analysis_stub()))

    print("\n=== F. меню ===")
    p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                       input="5\n1\n0\n\n0\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    check("F1 пункт 5 → 1 показывает журнал", "EXP-003" in p.stdout and p.returncode == 0)
    p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                       input="5\n4\n0\n\n0\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    check("F2 пункт 5 → 4 без готовых экспериментов объясняет, когда будет итог",
          p.returncode == 0 and "итога пока нет ни у одного" in p.stdout)

    print("\n=== P. привязка до прихода выгрузки ===")
    real_v = E._videos()
    gaps = [(datetime.fromisoformat(v["published_at"]) -
             datetime.fromisoformat(E.id_time(vid))).total_seconds()
            for vid, v in real_v.items()]
    check("P6 FACT: дата из ID совпадает с published_at выгрузки (0…60 с) у всех роликов",
          len(gaps) == len(real_v) >= 17 and all(0 <= g <= 60 for g in gaps),
          f"n={len(gaps)}, разброс {min(gaps):.0f}…{max(gaps):.0f} с")

    def make_id(stamp, tail=12345):
        return str((int(datetime.fromisoformat(stamp).timestamp()) << 32) | tail)

    tmp4 = Path(tempfile.mkdtemp()) / "register.jsonl"
    c4 = E.register_idea(IDEA, analysis_stub(1000), tmp4, now="2026-10-01T00:00:00+00:00")
    fresh = make_id("2026-10-02T18:00:00+00:00")
    ok, why = E.link(c4, f"https://www.tiktok.com/@gulyashik52/video/{fresh}", tmp4,
                     now="2026-10-02T18:05:00+00:00", videos={})
    rec = E._read(tmp4)[-1]
    check("P1 ролика нет в выгрузке, дата из ID после регистрации — привязан как ожидающий",
          ok and rec.get("pending") is True and rec["published_at"] is None
          and rec["id_time"].startswith("2026-10-02T18:00") and "выгрузк" in why, str(why))
    old_id = make_id("2026-09-25T12:00:00+00:00")
    ok, why = E.link(c4, old_id, tmp4, now="2026-10-02T18:05:00+00:00", videos={})
    check("P2 дата из ID раньше регистрации — отказ сразу: подгонка",
          not ok and "подгонка" in why and "ID ролика" in why, str(why))
    future = make_id("2026-12-01T00:00:00+00:00")
    ok, why = E.link(c4, future, tmp4, now="2026-10-02T18:05:00+00:00", videos={})
    check("P3 дата из ID в будущем — это не ID ролика, отказ", not ok, str(why))

    ev = E.evaluate(E.load(tmp4)[c4], analysis_stub(1000), videos={})
    row = next(r for r in ev["videos"] if r["video_id"] == fresh)
    check("P5 пока ролика нет в выгрузке — просьба о выгрузке, в итог не входит",
          "нужна новая выгрузка" in row["state"] and ev["n_mature"] == 0, row["state"])

    # выгрузка пришла — и показала, что ролик вышел ДО регистрации
    # (ID обманул, перезалив, ошибка ввода — неважно): привязка снимается с учёта
    came = {fresh: {"video_id": fresh, "published_at": "2026-09-30T23:00:00+00:00"}}
    obs = [{"video_id": fresh, "views": 99999, "age_days": 40,
            "observed_at": "2026-11-10T00:00:00+00:00", "verified_views": None,
            "verified_at": None, "url": "", "source": "metricool", "caption": ""}]
    ev = E.evaluate(E.load(tmp4)[c4], analysis_stub(1000, unverified=obs), videos=came)
    row = next(r for r in ev["videos"] if r["video_id"] == fresh)
    check("P4 выгрузка показала публикацию до регистрации — привязка недействительна, "
          "в счёт не идёт",
          row.get("invalid") and "недействительна" in row["state"]
          and ev["n_mature"] == 0 and ev["n_above"] == 0, row["state"])
    check("P4a недействительная привязка видна в итоге и в списке",
          "недействительных привязок: 1" in ev["summary"]
          and "недействительна" in E.render(E.load(tmp4), analysis_stub(1000, unverified=obs),
                                            videos=came), ev["summary"])
    came_ok = {fresh: {"video_id": fresh, "published_at": "2026-10-02T18:00:20+00:00"}}
    ev = E.evaluate(E.load(tmp4)[c4], analysis_stub(1000, unverified=obs), videos=came_ok)
    check("P4b выгрузка подтвердила порядок — ролик идёт в счёт",
          ev["n_mature"] == 1 and ev["n_above"] == 1, ev["summary"])

    print("\n=== H. один эксперимент на гипотезу ===")
    tmp5 = Path(tempfile.mkdtemp()) / "register.jsonl"
    i1 = dict(IDEA, title="Первая")
    i2 = dict(IDEA, title="Вторая")
    c5 = E.register_idea(i1, analysis_stub(1000), tmp5, now="2026-10-01T00:00:00+00:00")
    before = E.load(tmp5)
    c6 = E.register_idea(i2, analysis_stub(5000), tmp5, now="2026-10-03T00:00:00+00:00")
    st = E.load(tmp5)[c5]
    check("H1 вторая идея под ту же гипотезу — в тот же эксперимент",
          c6 == c5 and [i["title"] for i in st["ideas"]] == ["Первая", "Вторая"]
          and E._read(tmp5)[-1]["event"] == "idea_added")
    check("H2 база сравнения — с первой регистрации, не сдвинута второй",
          st["base"]["baseline"]["median_views_mature"] == 1000
          and st["created_at"].startswith("2026-10-01"))
    msg = E.taken_message(c6, before, E.load(tmp5))
    check("H3 человеку сказано, что идея добавлена и сколько нужно роликов",
          "добавлена к" in msg and "нужно 5" in msg, msg[:80])
    try:
        E.register_idea(i1, analysis_stub(1000), tmp5)
        dup = False
    except ValueError as exc:
        dup = "уже в работе" in str(exc)
    check("H4 ту же идею дважды не взять", dup)
    other = dict(IDEA, title="Другая гипотеза", tests_hypothesis="H9", data_basis="X связан с Y")
    c7 = E.register_idea(other, analysis_stub(1000), tmp5)
    check("H5 другая гипотеза — новый эксперимент", c7 != c5)
    v1 = {"5" + "0" * 17: {"video_id": "5" + "0" * 17,
                           "published_at": "2026-10-04T10:00:00+00:00"}}
    E.link(c5, "5" + "0" * 17, tmp5, videos=v1)
    ids = E.idea_states(E.load(tmp5))
    check("H6 первая привязка засчитана первой идее, вторая ещё в работе",
          ids["Первая"] == (c5, "published") and ids["Вторая"] == (c5, "taken"), str(ids))
    txt = E.render(E.load(tmp5), analysis_stub(1000), videos=v1)
    check("H8 в списке видны идеи эксперимента и сколько роликов привязано",
          "«Первая»; «Вторая»" in txt and "роликов привязано 1" in txt)
    E.conclude(c5, "stub", "закрыт", "нет", tmp5)
    c8 = E.register_idea(dict(IDEA, title="Третья"), analysis_stub(1000), tmp5)
    check("H7 к закрытому эксперименту не присоединяется — новый", c8 not in (c5, c7))

    print("\n=== T. шаблоны повторяемы ===")
    tmp6 = Path(tempfile.mkdtemp()) / "register.jsonl"
    from advisor import ideas as I
    a_real = A.analyze()
    tpl = I.offline_ideas(a_real)[0]
    t1 = E.register_idea(tpl, a_real, tmp6, now="2026-10-01T00:00:00+00:00")
    t2 = E.register_idea(tpl, a_real, tmp6, now="2026-10-02T00:00:00+00:00")
    st = E.load(tmp6)[t1]
    check("T1 шаблон можно взять второй раз — в тот же эксперимент",
          t1 == t2 and len(st["ideas"]) == 2
          and all(i.get("source") == "template" for i in st["ideas"]))
    vid = "6" + "0" * 17
    E.link(t1, vid, tmp6, videos={vid: {"video_id": vid,
                                        "published_at": "2026-10-03T00:00:00+00:00"}})
    check("T2 вышедший шаблон не считается «вышедшей идеей» — его можно снимать снова",
          tpl["title"] not in E.idea_states(E.load(tmp6)))
    check("T3 идея сессии — по-прежнему одна (защита от дубля не ослаблена)",
          not E._repeatable(IDEA) and E._repeatable(tpl))

    print("\n=== V. контроль того же периода ===")
    ctx = A.context()
    rows, _ = A.load(A.ROOT, ctx)
    vids_real = E._videos()
    diff = [(r["video_id"], k) for r in rows
            for k, v in A.video_attrs(vids_real[r["video_id"]], ctx).items()
            if r.get(k) != v]
    check("V1 признаки ролика для контроля — те же, что в разборе (все ролики)",
          rows and not diff, f"роликов {len(rows)}, расхождений {len(diff)}")
    tmp7 = Path(tempfile.mkdtemp()) / "register.jsonl"
    h1_idea = dict(IDEA, title="Ночной", tests_hypothesis="H1")
    a_real = A.analyze()
    cx = E.register_idea(h1_idea, a_real, tmp7, now="2026-10-01T00:00:00+00:00")
    cond = E.load(tmp7)[cx]["base"].get("condition") or {}
    check("V2 при регистрации записано условие гипотезы в машинном виде",
          cond.get("attribute") == "hour_utc" and cond.get("bounds", {}).get("kind") == "hour")

    def vid(n):
        return f"7{n:017d}"
    vids, obs = {}, []

    def add(n, when, views, age=40, linked=False):
        vids[vid(n)] = {"video_id": vid(n), "published_at": when, "caption": ""}
        obs.append({"video_id": vid(n), "views": views, "age_days": age,
                    "observed_at": "2026-11-20T00:00:00+00:00", "verified_views": None,
                    "verified_at": None, "url": "", "source": "metricool", "caption": ""})
        if linked:
            E.link(cx, vid(n), tmp7, videos=vids)
    for i in range(5):                                  # в окне 19–23 UTC
        add(i, f"2026-10-0{2 + i}T20:00:00+00:00", 2000 + 100 * i, linked=True)
    add(10, "2026-10-03T12:00:00+00:00", 500)           # контроль
    add(11, "2026-10-04T12:00:00+00:00", 700)           # контроль
    add(12, "2026-10-05T12:00:00+00:00", 900)           # контроль
    add(13, "2026-09-20T12:00:00+00:00", 99999)         # до регистрации — не в счёт
    add(14, "2026-10-06T12:00:00+00:00", 88888, age=5)  # молодой — не в счёт
    add(15, "2026-10-07T21:00:00+00:00", 77777)         # в окне, не привязан — не контроль
    stub = analysis_stub(1000, unverified=obs)
    ev = E.evaluate(E.load(tmp7)[cx], stub, videos=vids, ctx=ctx)
    check("V3 контроль: только зрелые, после регистрации, вне условия",
          ev["control"] == {"n": 3, "median": 700, "label": ev["control"]["label"]}
          and "не 19–23" in ev["control"]["label"], str(ev.get("control")))
    check("V4 в итоге — медиана эксперимента против контроля, с n",
          "медиана эксперимента 2200 против контроля того же периода 700 (n=3" in ev["summary"],
          ev["summary"])
    check("V5 итог с контролем проходит валидатор формулировок",
          not find_violations(ev["summary"], "RECOMMENDATION"))
    only = {k: v for k, v in vids.items() if k in {vid(i) for i in range(5)}}
    ev = E.evaluate(E.load(tmp7)[cx], analysis_stub(1000, unverified=obs[:5]),
                    videos=only, ctx=ctx)
    check("V6 нет роликов вне условия — так и сказано", "контроля нет" in ev["summary"])
    tmp8 = Path(tempfile.mkdtemp()) / "register.jsonl"
    cy = E.register_idea(IDEA, analysis_stub(1000), tmp8, now="2026-10-01T00:00:00+00:00")
    for i in range(5):
        E.link(cy, vid(i), tmp8, videos=vids)
    ev = E.evaluate(E.load(tmp8)[cy], stub, videos=vids, ctx=ctx)
    ctl = ev.get("control") or {}
    check("V7 гипотеза без машинного условия — контроль: ролики периода вне "
          "эксперимента, и это сказано (условие не выдумывается)",
          ctl.get("n") == 4 and ctl.get("median") == 800
          and "условие гипотезы не машинное" in ctl.get("label", "")
          and "условие гипотезы не машинное" in ev["summary"], str(ctl))

    print("\n=== R. итог по правилу, записанному заранее ===")
    check("R1 правило итога записано при регистрации",
          E.load(tmp7)[cx]["base"].get("decision_rule") == E.DECISION_RULE)

    def scenario(linked_views, control_views, label):
        reg = Path(tempfile.mkdtemp()) / f"{label}.jsonl"
        code = E.register_idea(h1_idea, a_real, reg, now="2026-10-01T00:00:00+00:00")
        vv, ob = {}, []
        for i, views in enumerate(linked_views + control_views):
            linked = i < len(linked_views)
            hour = 20 if linked else 12
            vv[vid(100 + i)] = {"video_id": vid(100 + i), "caption": "",
                                "published_at": f"2026-10-{2 + i:02d}T{hour}:00:00+00:00"}
            ob.append({"video_id": vid(100 + i), "views": views, "age_days": 40,
                       "observed_at": "2026-12-01T00:00:00+00:00", "verified_views": None,
                       "verified_at": None, "url": "", "source": "metricool", "caption": ""})
            if linked:
                E.link(code, vid(100 + i), reg, videos=vv)
        an = analysis_stub(1000, unverified=ob)
        ev = E.evaluate(E.load(reg)[code], an, videos=vv, ctx=ctx)
        ev["rule"] = ev.get("rule") or ("нет правила", "")   # провал, а не падение
        return reg, code, an, vv, ev

    _r, _c, _a, _v, ev = scenario([500, 600, 700, 800, 900], [400, 450, 500], "low")
    check("R2 не выше базы — not_supported", ev["rule"][0] == "not_supported", ev["rule"][1])
    _r, _c, _a, _v, ev = scenario([2000] * 5, [3000, 3100, 3200], "allup")
    check("R3 выше базы, но не выше контроля — not_supported: вырос весь аккаунт",
          ev["rule"][0] == "not_supported" and "весь аккаунт" in ev["rule"][1], ev["rule"][1])
    reg, code, an, vv, ev = scenario([2000] * 5, [500, 600], "fewctl")
    ok, msg = E.conclude_by_rule(code, an, reg, videos=vv, ctx=ctx)
    check("R4 контроля меньше 3 — inconclusive, эксперимент остаётся открытым",
          ev["rule"][0] == "inconclusive" and not ok
          and E.load(reg)[code]["status"] == "running", msg)
    reg, code, an, vv, ev = scenario([2000, 2100, 2200, 2300, 2400], [500, 700, 900], "good")
    ok, msg = E.conclude_by_rule(code, an, reg, videos=vv, ctx=ctx)
    st = E.load(reg)[code]
    check("R5 выше базы и контроля — supported, закрыт с текстом правила",
          ok and st["status"] == "concluded" and st["verdict"] == "supported"
          and "n=5" in st["result"] and "Причинность не установлена" in st["result"], msg)
    texts = [scenario(*args)[4]["rule"][1] for args in
             (([500] * 5, [400] * 3, "t1"), ([2000] * 5, [3000] * 3, "t2"),
              ([2000] * 5, [500], "t3"), ([2000] * 5, [500] * 3, "t4"))]
    viol = [t for t in texts if find_violations(t, "RECOMMENDATION")]
    check("R6 все четыре формулировки итога проходят валидатор", not viol, str(viol[:1]))
    legacy = Path(tempfile.mkdtemp()) / "legacy.jsonl"
    lc = E.register_idea(IDEA, analysis_stub(1000), legacy, now="2026-10-01T00:00:00+00:00")
    rows = [json.loads(l) for l in legacy.read_text(encoding="utf-8").splitlines()]
    rows[-1].pop("decision_rule")
    legacy.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                      encoding="utf-8")
    for i in range(5):
        E.link(lc, vid(i), legacy, videos=vids)
    ok, msg = E.conclude_by_rule(lc, stub, legacy, videos=vids, ctx=ctx)
    check("R7 регистрация без правила — итог по правилу не выносится", not ok
          and "не записано" in msg, msg)
    reg, code, an, vv, ev = scenario([2000] * 2, [500] * 3, "early")
    ok, msg = E.conclude_by_rule(code, an, reg, videos=vv, ctx=ctx)
    check("R8 меньше min_sample — не закрывается", not ok and "пока нет" in msg, msg)

    print("\n=== G. какие идеи считаются последними ===")
    d = Path(tempfile.mkdtemp())
    (d / "20261001T000000_session.json").write_text(json.dumps(
        {"mode": "session", "ideas": [{"title": "настоящая"}]}, ensure_ascii=False), encoding="utf-8")
    (d / "20261002T000000_template.json").write_text(json.dumps(
        {"mode": "template", "ideas": [{"title": "шаблон"}]}, ensure_ascii=False), encoding="utf-8")
    path, ideas = E.latest_ideas(d)
    check("G1 более новые шаблоны не прячут настоящие идеи",
          [i["title"] for i in ideas] == ["настоящая"], path.name if path else "—")
    (d / "20261001T000000_session.json").unlink()
    path, ideas = E.latest_ideas(d)
    check("G2 если настоящих нет — берутся шаблоны", [i["title"] for i in ideas] == ["шаблон"])

    check("Z1 настоящий журнал не изменён тестом ни байтом", sha(REAL) == real_before)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
