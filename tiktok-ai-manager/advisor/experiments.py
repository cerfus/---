#!/usr/bin/env python3
"""Журнал экспериментов: идея → предрегистрация → ролик → итог.

    python -m advisor.experiments list                 состояние и итоги
    python -m advisor.experiments take N [--file F]    взять идею N в работу
    python -m advisor.experiments link EXP-005 ID|URL  привязать опубликованный ролик

experiments/register.jsonl — append-only. Прежние записи (EXP-001…EXP-004)
не правятся: состояние эксперимента — его исходная запись плюс события,
дописанные после неё. Событие — строка с ключом "event":

  registered — идея взята в работу ДО публикации (предрегистрация);
  linked     — к эксперименту привязан опубликованный ролик;
  concluded  — эксперимент закрыт: итог и ссылка на доказательство;
  blocked    — эксперимент запрещён политикой, с машинной причиной.

Главная защита — порядок во времени. Ролик, опубликованный раньше
регистрации, к эксперименту не привязывается: гипотезу «проверяли» бы
роликом, результат которого уже был известен. Это подгонка, а не опыт.

Итог по одному ролику — наблюдение, а не вывод. Сравнение с базой
выдаётся только после MATURITY_DAYS, и пока роликов меньше min_sample,
эксперимент остаётся открытым.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A                       # noqa: E402
from insights.policies import mechanical_dependency     # noqa: E402

REGISTER = ROOT / "experiments" / "register.jsonl"
IDEAS_DIR = ROOT / "data" / "ideas"
MATURITY_DAYS = A.MATURE_AGE_DAYS
DEFAULT_MIN_SAMPLE = 5          # тот же нижний порог, что CHECK в таблице experiments
OPEN = ("proposed", "preregistered", "running", "partially_concluded")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _append(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def load(path=REGISTER):
    """{code: состояние}. Исходные записи плюс события в порядке файла."""
    states = {}
    for r in _read(path):
        if "event" not in r:
            code = r.get("code") or r.get("id")
            states[code] = {
                "code": code, "status": r.get("status"),
                "hypothesis": r.get("hypothesis"),
                "created_at": r.get("created_at"),
                "min_sample": r.get("min_sample") or r.get("target_sample_size"),
                "success_criteria": r.get("success_criteria"),
                "videos": [], "events": [], "origin": "register", "base": r}
            continue
        code = r["code"]
        st = states.get(code)
        if st is None:
            if r["event"] != "registered":
                raise ValueError(f"событие {r['event']} для неизвестного {code}")
            st = states[code] = {
                "code": code, "status": "preregistered",
                "hypothesis": r["hypothesis"], "created_at": r["at"],
                "min_sample": r.get("min_sample", DEFAULT_MIN_SAMPLE),
                "success_criteria": r.get("success_check"),
                "videos": [], "events": [], "origin": "idea", "base": r}
        st["events"].append(r)
        if r["event"] == "linked":
            st["videos"].append(r["video_id"])
            if st["status"] in ("proposed", "preregistered"):
                st["status"] = "running"
        elif r["event"] == "concluded":
            st["status"] = "concluded"
            st["verdict"], st["result"] = r.get("verdict"), r.get("result")
        elif r["event"] == "blocked":
            st["status"] = "blocked"
            st["blocked"] = r
    return states


def next_code(states):
    nums = [int(m.group(1)) for c in states for m in [re.fullmatch(r"EXP-(\d+)", c or "")] if m]
    return f"EXP-{max(nums, default=0) + 1:03d}"


# ─────────────────────────────── события ─────────────────────────────────────

def register_idea(idea, analysis, path=REGISTER, now=None):
    """Предрегистрация идеи. Возвращает код эксперимента."""
    hid = idea.get("tests_hypothesis")
    hyps = {h["id"]: h for h in analysis["hypotheses"]}
    hyps.update({h["id"]: h for h in idea.get("_new_hypotheses", [])})
    statement = hyps[hid]["statement"] if hid in hyps else idea.get("data_basis")
    if not statement:
        raise ValueError("у идеи нет проверяемой гипотезы")
    states = load(path)
    code = next_code(states)
    _append(path, {
        "event": "registered", "code": code, "at": now or _now(),
        "hypothesis_id": hid, "hypothesis": statement,
        "idea": {k: idea.get(k, "") for k in ("title", "what_to_film",
                                              "caption_draft", "when_to_post")},
        "success_check": idea.get("success_check"),
        "min_sample": DEFAULT_MIN_SAMPLE, "maturity_days": MATURITY_DAYS,
        # база сравнения фиксируется В МОМЕНТ регистрации: потом её не
        # подвинуть под результат
        "baseline": {"median_views_mature": analysis.get("median_views_mature"),
                     "verified_at": analysis.get("observed_at"),
                     "policy": analysis.get("policy_version")},
    })
    return code


def video_id_of(text):
    """video_id из числа или ссылки на ролик."""
    m = re.search(r"(\d{15,25})", text or "")
    return m.group(1) if m else None


def _videos(root=ROOT):
    return {v["video_id"]: v for v in _read(Path(root) / "data" / "videos.jsonl")}


def link(code, video, path=REGISTER, now=None, videos=None):
    """(True, None) либо (False, причина)."""
    states = load(path)
    st = states.get(code)
    if st is None:
        return False, f"эксперимента {code} нет"
    if st["status"] not in OPEN:
        return False, f"{code} в статусе {st['status']} — привязка закрыта"
    vid = video_id_of(video)
    if vid is None:
        return False, "не похоже на video_id или ссылку на ролик"
    if vid in st["videos"]:
        return False, f"ролик {vid} уже привязан к {code}"
    videos = _videos() if videos is None else videos
    v = videos.get(vid)
    if v is None:
        return False, (f"ролика {vid} нет в данных — сначала новая выгрузка "
                       "(ролик появится в data/videos.jsonl)")
    reg_at = st["created_at"]
    if v["published_at"] < _as_utc(reg_at):
        return False, (f"ролик опубликован {v['published_at'][:16]}, раньше регистрации "
                       f"{code} ({reg_at[:16]}): результат был известен до "
                       "эксперимента, такая проверка — подгонка")
    _append(path, {"event": "linked", "code": code, "at": now or _now(),
                   "video_id": vid, "published_at": v["published_at"]})
    return True, None


def _as_utc(stamp):
    """Дата без времени (старые записи) — начало суток UTC."""
    if stamp and "T" not in stamp:
        return stamp + "T00:00:00+00:00"
    return datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()


def conclude(code, verdict, result, evidence, path=REGISTER, now=None):
    _append(path, {"event": "concluded", "code": code, "at": now or _now(),
                   "verdict": verdict, "result": result, "evidence": evidence})


def block(code, reason_code, note, pair=None, path=REGISTER, now=None):
    rec = {"event": "blocked", "code": code, "at": now or _now(),
           "reason_code": reason_code, "note": note}
    if pair:
        rec["pair"] = list(pair)
    _append(path, rec)


# ─────────────────────────────── оценка ──────────────────────────────────────

def _observation(vid, analysis):
    """(просмотры, возраст в днях, сверено ли, дата) — по свежайшему наблюдению."""
    for u in analysis.get("unverified", []):
        if u["video_id"] == vid:
            return u["views"], u["age_days"], False, u["observed_at"]
    for v in analysis.get("videos", []):
        if v["video_id"] == vid:
            return v["views"], v["age_days"], True, v["observed_at"]
    return None


def evaluate(st, analysis):
    """Состояние эксперимента в цифрах. Ничего не пишет."""
    out = {"code": st["code"], "status": st["status"], "videos": [],
           "n_mature": 0, "n_above": 0, "min_sample": st.get("min_sample")}
    if st["status"] == "blocked":
        # блокировка бывает событием (с машинной причиной) или полем исходной
        # записи (blocked_by, свободный текст) — у ранних экспериментов
        b = st.get("blocked") or {
            "reason_code": "register",
            "note": st["base"].get("blocked_by") or "заблокирован в исходной записи"}
        pair = b.get("pair")
        out["note"] = b["note"]
        out["block_still_valid"] = (mechanical_dependency(*pair) is not None
                                    if pair and b["reason_code"] == "mechanically_dependent"
                                    else None)
        return out
    base = (st["base"].get("baseline") or {}).get("median_views_mature")
    out["baseline"] = base
    for vid in st["videos"]:
        obs = _observation(vid, analysis)
        if obs is None:
            out["videos"].append({"video_id": vid, "state": "нет наблюдений — нужна новая выгрузка"})
            continue
        views, age, verified, at = obs
        row = {"video_id": vid, "views": views, "age_days": age,
               "verified": verified, "observed_at": at}
        if age < MATURITY_DAYS:
            row["state"] = f"ждём ещё {MATURITY_DAYS - age} дн. до зрелости"
        elif base is None:
            row["state"] = "нет базы сравнения"
        else:
            out["n_mature"] += 1
            above = views > base
            out["n_above"] += int(above)
            row["state"] = (f"{'выше' if above else 'не выше'} медианы базы "
                            f"({A._num(base)})")
        if not verified:
            row["state"] += " · не сверено"
        out["videos"].append(row)
    need = out["min_sample"] or DEFAULT_MIN_SAMPLE
    if st["status"] in ("concluded",):
        out["summary"] = f"закрыт: {st.get('verdict')}"
    elif not st["videos"] and st["origin"] == "register":
        # ранние эксперименты (EXP-001…004) описаны своим критерием, а не
        # сравнением с медианой: их метрики — CR, share-rate, частота замеров
        out["summary"] = f"критерий: {st.get('success_criteria') or '—'}"
    elif not st["videos"]:
        out["summary"] = "ждёт публикации: привяжите ролик после выхода"
    elif out["n_mature"] < need:
        out["summary"] = (f"зрелых роликов {out['n_mature']} из {need} — "
                          "вывода пока нет")
    else:
        out["summary"] = (f"{out['n_above']} из {out['n_mature']} зрелых роликов выше "
                          f"медианы базы, n={out['n_mature']}")
    return out


def render(states, analysis):
    L = ["ЭКСПЕРИМЕНТЫ", ""]
    if not states:
        return "ЭКСПЕРИМЕНТЫ\n\nжурнал пуст"
    for code in sorted(states, key=lambda c: (c is None, c)):
        st = states[code]
        ev = evaluate(st, analysis)
        L.append(f"{code}  [{st['status']}]  {(st.get('hypothesis') or '')[:110]}")
        if st["status"] == "blocked":
            valid = ev.get("block_still_valid")
            L.append(f"    заблокирован: {ev['note']}")
            if valid is False:
                L.append("    ВНИМАНИЕ: пары больше нет в реестре — блокировка устарела")
        elif st["status"] == "concluded":
            L.append(f"    итог: {st.get('verdict')} — {st.get('result')}")
        else:
            if ev.get("baseline") is not None:
                L.append(f"    база: медиана зрелых {A._num(ev['baseline'])} "
                         f"(зафиксирована при регистрации)")
            for row in ev["videos"]:
                views = f"{row['views']} просм., {row['age_days']} дн. · " if "views" in row else ""
                L.append(f"    ролик {row['video_id']}: {views}{row['state']}")
            L.append(f"    {ev['summary']}")
        L.append("")
    return "\n".join(L).rstrip()


# ─────────────────────────────── идеи ────────────────────────────────────────

def latest_ideas(ideas_dir=None):
    # каталог читается при вызове, а не при определении функции: иначе его
    # нельзя было бы подменить в тесте
    files = sorted(Path(ideas_dir or IDEAS_DIR).glob("*.json"))
    if not files:
        return None, []
    # Последние НАСТОЯЩИЕ идеи (модель или сессия) важнее последних
    # шаблонов: иначе один запуск пункта 4 без ключа прятал бы хорошие идеи
    # от экспериментов, /next и дашборда. Шаблоны — только если других нет.
    docs = [(f, json.loads(f.read_text(encoding="utf-8"))) for f in files]
    real = [(f, d) for f, d in docs if d.get("mode") != "template" and d.get("ideas")]
    path, data = (real or docs)[-1]
    ideas = data.get("ideas", [])
    for i in ideas:
        i["_new_hypotheses"] = data.get("new_hypotheses", [])
    return path, ideas


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Журнал экспериментов.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("take")
    t.add_argument("n", type=int)
    t.add_argument("--file")
    l_ = sub.add_parser("link")
    l_.add_argument("code")
    l_.add_argument("video")
    args = ap.parse_args(argv)

    a = A.analyze()
    if args.cmd == "list":
        print(render(load(), a))
        return 0
    if args.cmd == "take":
        if args.file:
            path = Path(args.file)
            data = json.loads(path.read_text(encoding="utf-8"))
            ideas = data.get("ideas", [])
            for i in ideas:
                i["_new_hypotheses"] = data.get("new_hypotheses", [])
        else:
            path, ideas = latest_ideas()
        if not ideas:
            print("идей нет — сначала пункт «Идеи для следующих видео»")
            return 1
        if not 1 <= args.n <= len(ideas):
            print(f"идеи №{args.n} нет: в {path.name} их {len(ideas)}")
            return 1
        code = register_idea(ideas[args.n - 1], a)
        print(f"{code} зарегистрирован до публикации. После выхода ролика: "
              f"python -m advisor.experiments link {code} <ссылка>")
        return 0
    ok, why = link(args.code, args.video)
    print(f"привязано к {args.code}" if ok else f"не привязано: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
