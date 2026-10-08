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
