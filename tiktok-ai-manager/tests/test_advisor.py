#!/usr/bin/env python3
"""Советник: разбор, идеи, меню.

Тест НИКОГДА не обращается к настоящему API: он входит в verify_all, и при
заданном ключе случайный вызов стоил бы денег на каждой проверке. Поэтому
make_client на всё время теста подменён ловушкой, которая валит тест, если
до неё дошло, а модель изображает подменный клиент.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A, ideas as I            # noqa: E402
from insights.validator import find_violations            # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def row(vid, views, age, weekday="понедельник", hour=12, dur=10.0, words=0, tags=3):
    return {"video_id": vid, "url": f"u/{vid}", "views": views,
            "observed_at": "2026-09-17T10:00:00+00:00",
            "published_at": "2026-01-01T00:00:00+00:00", "age_days": age,
            "duration_sec": dur, "weekday": weekday, "hour_utc": hour,
            "hashtags": tags, "caption_words": words,
            "caption_kind": "повествовательная" if words >= 5 else "только хештеги",
            "caption": f"подпись {vid}"}


class FakeClient:
    def __init__(self, text=None, stop="end_turn", exc=None):
        self.calls, self.text, self.stop, self.exc = [], text, stop, exc
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason=self.stop,
                               content=[SimpleNamespace(type="text", text=self.text)])


def main():
    trap_hits = []

    def trap():
        trap_hits.append(1)
        raise AssertionError("тест дошёл до настоящего API-клиента")

    saved_make = I.make_client
    I.make_client = trap
    try:
        run_all(saved_make)
    finally:
        I.make_client = saved_make
    check("Z1 настоящий API-клиент не создавался ни разу", not trap_hits)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


def run_all(real_make_client):
    print("=== A. разбор на данных аккаунта ===")
    a = A.analyze()
    check("A1 роликов 16", a["n_videos"] == 16, str(a["n_videos"]))
    check("A2 зрелых 10, молодых 6", a["n_mature"] == 10 and len(a["young"]) == 6)
    check("A3 медиана зрелых 1435", a["median_views_mature"] == 1435)
    check("A4 хитов 2", a["n_hits"] == 2)
    check("A5 хиты дали 97.8% просмотров", a["hits_share_pct"] == 97.8,
          str(a["hits_share_pct"]))
    check("A6 разбор детерминирован",
          json.dumps(a, sort_keys=True, ensure_ascii=False)
          == json.dumps(A.analyze(), sort_keys=True, ensure_ascii=False))
    claims = a["facts"] + a["hypotheses"] + a["not_distinguishing"]
    bad = [(c["statement"], find_violations(c["statement"], c["claim_type"]))
           for c in claims if find_violations(c["statement"], c["claim_type"])]
    check("A7 каждая формулировка проходит валидатор", not bad, str(bad[:1]))
    check("A8 закономерности — только HYPOTHESIS",
          all(h["claim_type"] == "HYPOTHESIS" for h in a["hypotheses"])
          and not any("связан" in f["statement"] for f in a["facts"]))
    check("A9 у гипотез N, порог 25 и конкурирующее объяснение",
          all(h["n_sample"] == 10 and h["min_sample_required"] == 25
              and h["competing_explanation"] for h in a["hypotheses"]))
    ids = {h["id"]: h["attribute"] for h in a["hypotheses"]}
    check("A10 номера гипотез стабильны: H1 час, H2 слова, H3 день, H4 активность",
          ids == {"H1": "hour_utc", "H2": "caption_words", "H3": "weekday",
                  "H4": "activity_pct"}, str(ids))
    nd = {x["attribute"] for x in a["not_distinguishing"]}
    check("A11 длительность, хештеги и вид подписи — «не отличает»",
          {"duration_sec", "hashtags", "caption_kind"} <= nd, str(sorted(nd)))
    young_hit = [v for v in a["videos"] if not v["mature"] and v["hit"]]
    check("A12 молодой ролик хитом не считается", not young_hit)

    # Только сверенные просмотры: FACT опирается на статус именно этой метрики
    ver = A._verified_views(ROOT)
    check("A13 в разборе только сверенные просмотры",
          all(v["views"] == ver[v["video_id"]][0] for v in a["videos"]))
    newest = [u for u in a["unverified"] if u["verified_views"] is None]
    check("A14 ролик без сверенных данных в разбор не входит, но показан",
          newest and not {u["video_id"] for u in newest} & {v["video_id"] for v in a["videos"]},
          str([u["video_id"] for u in newest]))
    check("A15 несверенное помечено в тексте разбора",
          "НЕ СВЕРЕНО" in A.render(a) if a["unverified"] else True)

    H = {h["id"]: h for h in a["hypotheses"]}
    check("A16 час публикации держится в поясе бренда: 22–02 Europe/Moscow",
          H["H1"]["tz_robust"] is True and "22–02" in H["H1"]["local_view"],
          str(H["H1"].get("local_view")))
    check("A17 воскресенье — только в UTC: в Москве хиты в разные дни",
          H["H3"]["tz_robust"] is False and "понедельник" in H["H3"]["tz_note"],
          str(H["H3"].get("tz_note")))
    check("A18 у гипотезы об активности названо допущение источника",
          "модель Metricool" in H["H4"]["competing_explanation"])
    hit_pct = sorted(v["activity_pct"] for v in a["videos"] if v["hit"])
    check("A19 хиты вышли в тихие часы по оценке Metricool: 7-й и 35-й перцентиль",
          hit_pct == [7, 35], str(hit_pct))
    g = {x["video_id"]: x for x in a["growth"]}
    fresh = {u["video_id"]: u for u in a["unverified"]}
    check("A20 прирост = свежее − сверенное, новый ролик помечен новым",
          all(x["new"] == (fresh[v]["verified_views"] is None) and
              (x["new"] or x["delta"] == fresh[v]["views"] - fresh[v]["verified_views"])
              for v, x in g.items()))
    check("A21 текст разбора называет зависимость от пояса",
          "зависит от часового пояса" in A.render(a))

    print("\n=== A'. окно часов по кругу ===")
    check("A22 [19, 23] → 19–23 (4 ч)", A.hour_window([19, 23]) == (19, 4))
    check("A23 [2, 22] → 22–02 через полночь (4 ч), а не 2–22",
          A.hour_window([22, 2]) == (22, 4), str(A.hour_window([22, 2])))
    check("A24 один час — окно нулевой длины", A.hour_window([5]) == (5, 0))
    check("A25 попадание в окно через полночь",
          A.in_window(0, 22, 4) and A.in_window(2, 22, 4) and not A.in_window(3, 22, 4))

    print("\n=== A''. без базы часовых поясов (как на голом Windows) ===")
    import zoneinfo
    saved_zi = zoneinfo.ZoneInfo
    def no_tz(*_a, **_k):
        raise zoneinfo.ZoneInfoNotFoundError("нет базы")
    zoneinfo.ZoneInfo = no_tz
    try:
        ctx = A.context(ROOT)
        rows, fr = A.load(ROOT, ctx)
        a0 = A.analyze(rows, fresh=fr, ctx=ctx)
    finally:
        zoneinfo.ZoneInfo = saved_zi
    check("A26 разбор не падает, причина названа с подсказкой tzdata",
          ctx["tz"] is None and "tzdata" in (ctx["tz_note"] or ""))
    check("A27 без пояса: гипотеза о дне не помечается, активность не считается",
          all(h.get("tz_robust") is None for h in a0["hypotheses"])
          and "activity_pct" not in {h["attribute"] for h in a0["hypotheses"]})
    check("A28 причина видна в тексте разбора", "tzdata" in A.render(a0))

    print("\n=== B. разбор на подставных данных ===")
    b = A.analyze([row("y1", 999999, 3), row("y2", 5, 4)])
    check("B1 без зрелых роликов — ни гипотез, ни хитов",
          not b["hypotheses"] and b["n_mature"] == 0)
    # молодой ролик с огромными просмотрами не делает хитом зрелый
    rows = [row(f"m{i}", 100, 60) for i in range(8)]
    rows += [row("h1", 5000, 60, "вторник"), row("h2", 6000, 60, "пятница"),
             row("young", 10**6, 2)]
    b = A.analyze(rows)
    check("B2 хиты с разными днями — день не гипотеза",
          "weekday" not in {h["attribute"] for h in b["hypotheses"]}
          and any(x["attribute"] == "weekday" for x in b["not_distinguishing"]))
    check("B3 молодой рекордсмен в сравнение не вошёл",
          b["n_hits"] == 2 and "young" in b["young"])
    same = [row(f"m{i}", 100, 60, "среда") for i in range(8)]
    same += [row("h1", 5000, 60, "среда"), row("h2", 6000, 60, "среда")]
    b = A.analyze(same)
    check("B4 признак, общий с остальными, — не гипотеза",
          "weekday" not in {h["attribute"] for h in b["hypotheses"]})

    fresh = [{"video_id": "z", "url": "u/z", "source": "metricool", "views": 10**7,
              "observed_at": "2026-10-08T00:00:00+00:00", "age_days": 60,
              "verified_views": None, "verified_at": None, "caption": ""}]
    b = A.analyze(same, fresh=fresh)
    check("B5 несверенный рекорд хитом не становится",
          "z" not in {v["video_id"] for v in b["videos"]} and b["unverified"][0]["video_id"] == "z")

    # гипотеза держится только в местном поясе — заводится оттуда и помечается
    tzrows = [dict(row(f"m{i}", 100, 60, "среда"), weekday_local="среда") for i in range(8)]
    tzrows += [dict(row("h1", 5000, 60, "суббота"), weekday_local="воскресенье"),
               dict(row("h2", 6000, 60, "воскресенье"), weekday_local="воскресенье")]
    b = A.analyze(tzrows, ctx={"tz_name": "Europe/Moscow"})
    wk = [h for h in b["hypotheses"] if h["attribute"].startswith("weekday")]
    check("B6 только в местном поясе — гипотеза из местного, помечена «в UTC не отличает»",
          len(wk) == 1 and wk[0]["attribute"] == "weekday_local"
          and wk[0]["tz_robust"] is False and "в UTC не отличает" in wk[0]["tz_note"],
          str([(h["attribute"], h.get("tz_note")) for h in wk]))

    print("\n=== C. идеи без модели ===")
    r = I.generate(a, offline=True)
    check("C1 режим — шаблоны", r["mode"] == "template" and r["model"] is None)
    hyp_ids = {h["id"] for h in a["hypotheses"]}
    check("C2 по идее на каждую гипотезу",
          [i["tests_hypothesis"] for i in r["ideas"]] == [h["id"] for h in a["hypotheses"]])
    viol = [(i["title"], f, find_violations(i[f], "RECOMMENDATION"))
            for i in r["ideas"] for f in ("title", "what_to_film", "when_to_post",
                                           "success_check")
            if find_violations(i[f], "RECOMMENDATION")]
    check("C3 тексты шаблонов проходят валидатор", not viol, str(viol[:1]))
    check("C4 у каждой идеи метка RECOMMENDATION",
          all(i["claim_type"] == "RECOMMENDATION" for i in r["ideas"]))
    check("C5 внутренние имена полей пользователю не показываются",
          not any("hour_utc" in i["what_to_film"] for i in r["ideas"]))
    when = {i["tests_hypothesis"]: i["when_to_post"] for i in r["ideas"]}
    check("C6 время публикации дано в обоих поясах", "22–02" in when.get("H1", ""))
    check("C7 зависимая от пояса гипотеза предупреждает в самой идее",
          "зависит от пояса" in when.get("H3", ""))

    print("\n=== D. идеи от модели (подменный клиент) ===")
    brief = I.build_brief(a)
    hits = [v["video_id"] for v in a["videos"] if v["hit"]]
    flop = next(v["video_id"] for v in a["videos"] if v["mature"] and not v["hit"])
    ver_part, unver_part = I.brief_parts(a)
    check("D0a рост от сверенного значения попадает в бриф, и только в несверенную часть",
          "РОСТ ОТ ПОСЛЕДНЕГО СВЕРЕННОГО" in unver_part
          and "РОСТ ОТ ПОСЛЕДНЕГО СВЕРЕННОГО" not in ver_part)
    check("D0 число-приманка 4242 в брифе отсутствует",
          "4242" not in I.numbers_in(brief))
    good_l1 = {"id": "L1",
               "statement": "Подпись в виде справки о человеке может быть связана "
                            "с залётом, n=10. Причинность не установлена.",
               "evidence_video_ids": [hits[0], flop],
               "competing_explanation": "Такая же подпись встречается и у ролика "
                                        "без залёта; совпадение может оказаться случайным."}
    bad_l2 = dict(good_l1, id="L2", evidence_video_ids=["0000000000000000001"])
    bad_l3 = dict(good_l1, id="L3",
                  statement="Подпись-справка работает всегда, n=10.")
    base = {"title": "Справка о блогере", "caption_draft": "Кто такой … — коротко",
            "what_to_film": "Короткий ролик о персонаже с подписью-справкой.",
            "when_to_post": "воскресенье, 19:00 UTC",
            "data_basis": f"Хит {hits[0]} набрал 698889 просмотров; n=10.",
            "success_check": "Через 30 дней сравнить с медианой 1435."}
    payload = {"new_hypotheses": [good_l1, bad_l2, bad_l3], "ideas": [
        dict(base, tests_hypothesis="H3"),
        dict(base, title="По новой гипотезе", tests_hypothesis="L1"),
        dict(base, title="Неизвестная гипотеза", tests_hypothesis="H9"),
        dict(base, title="Выдуманная цифра", tests_hypothesis="H1",
             data_basis="Хиты набрали 4242 просмотров в первый час."),
        dict(base, title="Обещание", tests_hypothesis="H1",
             what_to_film="Так лучше: такой ролик точно зайдёт."),
        dict(base, title="По отвергнутой гипотезе", tests_hypothesis="L2"),
    ]}
    fc = FakeClient(json.dumps(payload, ensure_ascii=False))
    r = I.generate(a, client=fc)
    kw = fc.calls[0] if fc.calls else {}
    check("D1 ровно один запрос к модели", len(fc.calls) == 1)
    check("D2 модель claude-opus-5-5", kw.get("model") == "claude-opus-5-5")
    check("D3 серверный запасной путь при отказе",
          kw.get("fallbacks") == "default"
          and "server-side-fallback-2026-07-01" in kw.get("betas", []))
    oc = kw.get("output_config", {})
    check("D4 ответ ограничен JSON-схемой",
          oc.get("format", {}).get("type") == "json_schema"
          and oc["format"]["schema"] is I.SCHEMA)
    check("D5 effort задан явно", oc.get("effort") in ("high", "xhigh", "max"))
    check("D6 нет устаревших параметров (thinking off, budget, temperature)",
          "thinking" not in kw and "temperature" not in kw
          and "budget_tokens" not in repr(kw))
    check("D7 бриф — содержимое сообщения",
          kw.get("messages", [{}])[0].get("content") == brief)
    check("D8 схема закрыта: additionalProperties=False на всех уровнях",
          I.SCHEMA["additionalProperties"] is False
          and all(I.SCHEMA["properties"][k]["items"]["additionalProperties"] is False
                  for k in ("ideas", "new_hypotheses")))

    check("D9 режим — модель", r["mode"] == "model")
    titles = [i["title"] for i in r["ideas"]]
    check("D10 приняты ровно две корректные идеи",
          titles == ["Справка о блогере", "По новой гипотезе"], str(titles))
    check("D11 принята корректная гипотеза модели",
          [h["id"] for h in r["new_hypotheses"]] == ["L1"]
          and r["new_hypotheses"][0]["claim_type"] == "HYPOTHESIS"
          and r["new_hypotheses"][0]["min_sample_required"] == 25)
    rej = {x["id"]: " ".join(x["reasons"]) for x in r["rejected"]}
    check("D12 гипотеза с несуществующим video_id отвергнута",
          "несуществующие video_id" in rej.get("L2", ""))
    check("D13 гипотеза без модальности и без пометки отвергнута",
          "модальности" in rej.get("L3", ""))
    check("D14 идея к неизвестной гипотезе отвергнута",
          "неизвестную гипотезу" in rej.get("Неизвестная гипотеза", ""))
    check("D15 выдуманное число в опоре отвергнуто",
          "4242" in rej.get("Выдуманная цифра", ""))
    check("D16 обещание результата отвергнуто",
          "оценочная" in rej.get("Обещание", ""))
    check("D17 идея к отвергнутой гипотезе отвергнута",
          "неизвестную гипотезу" in rej.get("По отвергнутой гипотезе", ""))
    check("D18 отброшенное показывается, а не исчезает",
          "Отброшено проверкой: 6" in I.render(r))

    rs = I.from_payload(payload, a)
    check("D18a готовый ответ (сессия) проверяется так же строго, как ответ API",
          [i["title"] for i in rs["ideas"]] == [i["title"] for i in r["ideas"]]
          and {x["id"] for x in rs["rejected"]} == {x["id"] for x in r["rejected"]})
    check("D18b источник честно назван: сессия, а не API",
          rs["mode"] == "session" and all(i["source"] == "session" for i in rs["ideas"])
          and "сессия" in I.render(rs))

    for name, client, why in (
            ("D19 отказ модели", FakeClient("{}", stop="refusal"), "отказалась"),
            ("D20 ответ оборвался", FakeClient("{", stop="max_tokens"), "оборвался"),
            ("D21 ответ не JSON", FakeClient("не json"), "не JSON")):
        r2 = I.generate(a, client=client)
        check(f"{name} — шаблоны и причина",
              r2["mode"] == "template" and why in (r2["note"] or "")
              and len(r2["ideas"]) == len(a["hypotheses"]), str(r2["note"]))

    class SDK:                                   # поддельный модуль anthropic
        class APIStatusError(Exception): pass
        class AuthenticationError(APIStatusError): pass
        class RateLimitError(APIStatusError): pass
        class APIConnectionError(Exception): pass
    for name, exc, why in (("D22 неверный ключ", SDK.AuthenticationError(), "не подошёл"),
                           ("D23 лимит", SDK.RateLimitError(), "лимит"),
                           ("D24 нет сети", SDK.APIConnectionError(), "нет связи")):
        r2 = I.generate(a, client=FakeClient(exc=exc), sdk=SDK)
        check(f"{name} — понятная причина и шаблоны",
              r2["mode"] == "template" and why in (r2["note"] or ""), str(r2["note"]))

    # несверенное число — только с пометкой «не сверено»
    if a["unverified"]:
        u = a["unverified"][0]
        raw_n = str(u["views"])
        check("D25 несверенное число есть только в несверенной части брифа",
              raw_n in I.unverified_only_numbers(a), raw_n)
        bare = dict(payload, new_hypotheses=[], ideas=[dict(
            base, title="Без пометки", tests_hypothesis="H1",
            data_basis=f"Свежий счётчик {raw_n}, n=10.")])
        marked = dict(bare, ideas=[dict(bare["ideas"][0], title="С пометкой",
                                        data_basis=f"Свежий счётчик {raw_n} (не сверено), n=10.")])
        rb = I.generate(a, client=FakeClient(json.dumps(bare, ensure_ascii=False)))
        rm = I.generate(a, client=FakeClient(json.dumps(marked, ensure_ascii=False)))
        check("D26 несверенное число без пометки отвергнуто",
              not rb["ideas"] and "без пометки" in " ".join(rb["rejected"][0]["reasons"]))
        check("D27 то же число с пометкой «не сверено» принято",
              [i["title"] for i in rm["ideas"]] == ["С пометкой"])

    print("\n=== E. ключ не утекает ===")
    key = "sk-ant-api03-TESTSECRETKEY0123456789abcdef"
    leak = dict(payload, ideas=[dict(base, tests_hypothesis="H3",
                                     title=f"ключ {key}")])
    saved_env = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = key
    try:
        fc3 = FakeClient(json.dumps(leak, ensure_ascii=False))
        r3 = I.generate(a, client=fc3)
        out = Path(tempfile.mkdtemp())
        j, t = I.save(r3, out)
        files = j.read_text(encoding="utf-8") + t.read_text(encoding="utf-8")
        check("E1 ключ не попал в текст для консоли", key not in I.render(r3))
        check("E2 ключ не попал в сохранённые файлы", key not in files)
        check("E3 ключ не уходит в параметры запроса",
              len(fc3.calls) == 1 and key not in repr(fc3.calls))
    finally:
        if saved_env is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = saved_env
    from mobile import audit
    check("E4 общий скрабер узнаёт ключ Anthropic", key not in audit.scrub(f"x {key} y"))

    # без ключа make_client честно отказывается; .env на время опыта не читается
    from core import config
    saved_load, saved_env = config.load_dotenv, os.environ.pop("ANTHROPIC_API_KEY", None)
    config.load_dotenv = lambda *a, **k: None
    try:
        # настоящий make_client: проверяет ключ и пакет, запросов не делает
        client, why = real_make_client()
        check("E5 без ключа клиента нет, причина названа",
              client is None and "ANTHROPIC_API_KEY" in why, str(why))
    finally:
        config.load_dotenv = saved_load
        if saved_env is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved_env

    print("\n=== F. сохранение ===")
    out = Path(tempfile.mkdtemp())
    before = sorted((ROOT / "data" / "ideas").glob("*")) if (ROOT / "data" / "ideas").exists() else []
    j, t = I.save(I.generate(a, offline=True), out)
    data = json.loads(j.read_text(encoding="utf-8"))
    check("F1 JSON сохраняется и читается", data["mode"] == "template")
    check("F2 текст для чтения сохраняется", "RECOMMENDATION" in t.read_text(encoding="utf-8"))
    after = sorted((ROOT / "data" / "ideas").glob("*")) if (ROOT / "data" / "ideas").exists() else []
    check("F3 тест не пишет в data/ideas проекта", before == after)

    print("\n=== G. меню ===")
    def menu(stdin):
        p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                           input=stdin, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return p.returncode, p.stdout
    rc, out_ = menu("0\n")
    check("G1 пункт 0 — выход с кодом 0", rc == 0 and "до встречи" in out_)
    rc, out_ = menu("")
    check("G2 закрытый ввод — выход, а не бесконечный цикл", rc == 0)
    rc, out_ = menu("42\n0\n")
    check("G3 неизвестный пункт назван", "нет пункта «42»" in out_)
    rc, out_ = menu("3\n\n0\n")
    check("G4 пункт 3 показывает разбор", "ЧТО ЗАЛЕТЕЛО" in out_ and "HYPOTHESIS" in out_)
    import runpy
    menu_ns = runpy.run_path(str(ROOT / "scripts" / "menu.py"))
    key_of = {fn.__name__: k for k, _, fn in menu_ns["ITEMS"] if fn}
    rc, out_ = menu(f"{key_of['migrate']}\nнет\n\n0\n")
    check("G5 миграции без «да» не запускаются",
          "отменено" in out_ and "Миграции" in out_)
    src = (ROOT / "scripts" / "menu.py").read_text(encoding="utf-8")
    check("G6 в меню нет публикации",
          not any(w in src.lower() for w in ("publish(", "/publish", "submit(")))
    check("G7 каждый пункт описан и пронумерован",
          [k for k, _, _ in __import__("importlib").import_module("runpy").run_path(
              str(ROOT / "scripts" / "menu.py"))["ITEMS"]]
          == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "0"])

    ok_yes = all(menu_ns["confirmed"](x) for x in ("да", "Да", "д", "y", "YES", " yes "))
    ok_no = not any(menu_ns["confirmed"](x) for x in ("нет", "", None, "n", "дда"))
    check("G8 согласие: да / д / y / yes — и ничего больше", ok_yes and ok_no)

    print("\n=== H. menu.bat пригоден для cmd.exe ===")
    bat_b = (ROOT / "scripts" / "menu.bat").read_bytes()
    try:
        bat = bat_b.decode("ascii"); ascii_ok = True
    except UnicodeDecodeError:
        bat, ascii_ok = "", False
    cmds = "\n".join(l for l in bat.splitlines()
                     if not l.strip().lower().startswith("rem")).lower()
    check("H1 ASCII целиком", ascii_ok)
    check("H2 CRLF без одиночных LF",
          bat_b.count(b"\r\n") > 0 and bat_b.count(b"\n") == bat_b.count(b"\r\n"))
    check("H3 нет BOM", not bat_b.startswith(b"\xef\xbb\xbf"))
    check("H4 вызывает scripts\\menu.py", "scripts\\menu.py" in cmds)
    check("H5 нет bash, wsl, python3", not any(w in cmds for w in ("bash", "wsl", "python3")))
    check("H5a при ошибке окно не закрывается (pause)",
          'if not "%rc%"=="0" pause' in cmds)
    check("H6 нет скобочных блоков IF",
          not [l for l in cmds.splitlines() if l.rstrip().endswith("(")])


if __name__ == "__main__":
    sys.exit(main())
