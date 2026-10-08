#!/usr/bin/env python3
"""Журнал экспериментов: идея → предрегистрация → ролик → итог.

    python -m advisor.experiments list                 состояние и итоги
    python -m advisor.experiments take N [--file F]    взять идею N в работу
    python -m advisor.experiments link EXP-005 ID|URL  привязать опубликованный ролик

experiments/register.jsonl — append-only. Прежние записи (EXP-001…EXP-004)
не правятся: состояние эксперимента — его исходная запись плюс события,
дописанные после неё. Событие — строка с ключом "event":

  registered — идея взята в работу ДО публикации (предрегистрация);
  idea_added — ещё одна идея под ту же гипотезу добавлена в открытый
               эксперимент: вывод требует min_sample роликов, и один
               эксперимент на каждую идею не набрал бы их никогда;
  linked     — к эксперименту привязан опубликованный ролик;
  concluded  — эксперимент закрыт: итог и ссылка на доказательство;
  blocked    — эксперимент запрещён политикой, с машинной причиной.

Главная защита — порядок во времени. Ролик, опубликованный раньше
регистрации, к эксперименту не привязывается: гипотезу «проверяли» бы
роликом, результат которого уже был известен. Это подгонка, а не опыт.

Ролик можно привязать сразу после публикации, не дожидаясь выгрузки:
в ID ролика TikTok зашито время его создания (старшие 32 бита — секунды
Unix). Проверено на всех роликах аккаунта: расхождение с published_at
выгрузки — от 3 до 44 секунд (тест P6 держит это на данных). По этой
дате порядок проверяется сразу, а когда ролик придёт в выгрузке — ещё
раз, по published_at. Нарушение порядка делает привязку недействительной:
ролик не входит в итог, и это видно в списке.

Итог по одному ролику — наблюдение, а не вывод. Сравнение с базой
выдаётся только после MATURITY_DAYS, и пока роликов меньше min_sample,
эксперимент остаётся открытым.
"""
import json
import re
import statistics
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
# Правило итога записывается при регистрации — ДО результата. Итог потом
# выносит правило, а не взгляд на цифры: иначе это подгонка.
DECISION_RULE = "median-vs-base-and-control-v1"
MIN_CONTROL = 3                 # меньше роликов в контроле — контроля нет
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
                "videos": [], "events": [], "origin": "idea", "base": r,
                "ideas": [r["idea"]] if r.get("idea") else []}
        st["events"].append(r)
        if r["event"] == "idea_added":
            st.setdefault("ideas", []).append(r["idea"])
        elif r["event"] == "linked":
            st["videos"].append(r["video_id"])
            st.setdefault("links", {})[r["video_id"]] = r
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

def stale_reason(idea, analysis):
    """Почему идея устарела, или None. Устарела — если гипотеза, под которую
    она написана, в текущем разборе звучит иначе или исчезла (номера H
    зависят от данных). Идеи без снимка (ранние файлы) проверить нельзя —
    они не считаются устаревшими."""
    snap = idea.get("_snapshot") or {}
    hid = idea.get("tests_hypothesis")
    if hid not in snap:
        return None
    now = {h["id"]: h["statement"] for h in analysis["hypotheses"]}.get(hid)
    if now == snap[hid]:
        return None
    return (f"гипотеза {hid} после новой выгрузки "
            + ("исчезла из разбора" if now is None else "стала другой")
            + " — сгенерируйте идеи заново (пункт 4)")


def register_idea(idea, analysis, path=REGISTER, now=None):
    """Предрегистрация идеи. Возвращает код эксперимента."""
    hid = idea.get("tests_hypothesis")
    stale = stale_reason(idea, analysis)
    if stale:
        raise ValueError(stale)
    hyps = {h["id"]: h for h in analysis["hypotheses"]}
    hyps.update({h["id"]: h for h in idea.get("_new_hypotheses", [])})
    statement = hyps[hid]["statement"] if hid in hyps else idea.get("data_basis")
    h = hyps.get(hid) or {}
    # условие гипотезы в машинном виде: по нему оценка делит ролики периода
    # на «в условии» и контроль. Номера H меняются с данными, условие — нет
    condition = ({"attribute": h["attribute"], "bounds": h["bounds"],
                  "label": h.get("label"), "value": h.get("value")}
                 if h.get("bounds") else None)
    if not statement:
        raise ValueError("у идеи нет проверяемой гипотезы")
    states = load(path)
    title = idea.get("title")
    repeatable = _repeatable(idea)
    for st in states.values():
        if not repeatable and title and any(i.get("title") == title
                                            for i in st.get("ideas", [])):
            raise ValueError(f"идея «{title}» уже в работе: {st['code']}")
    card = {k: idea.get(k, "") for k in ("title", "what_to_film",
                                         "caption_draft", "when_to_post")}
    if repeatable:
        card["source"] = "template"
    # Та же гипотеза уже проверяется — идея присоединяется к открытому
    # эксперименту: база сравнения остаётся замороженной с его регистрации,
    # а ролики копятся до min_sample, а не расползаются по экспериментам.
    same = [st for st in states.values()
            if st["origin"] == "idea" and st["hypothesis"] == statement
            and st["status"] in ("preregistered", "running")]
    if same:
        code = same[0]["code"]
        _append(path, {"event": "idea_added", "code": code, "at": now or _now(),
                       "idea": card})
        return code
    code = next_code(states)
    _append(path, {
        "event": "registered", "code": code, "at": now or _now(),
        "hypothesis_id": hid, "hypothesis": statement,
        "idea": card,
        "condition": condition,
        "decision_rule": DECISION_RULE,
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


ID_EPOCH_MIN = datetime(2016, 9, 1, tzinfo=timezone.utc)   # запуск TikTok
ID_FUTURE_SLACK_SEC = 86400


def id_time(vid, now=None):
    """Время создания ролика из его ID (UTC) или None, если дата неправдоподобна.

    Неправдоподобна — раньше запуска TikTok или позже, чем «сейчас» плюс
    сутки: такой номер не ID ролика, а опечатка или чужое число.
    """
    try:
        t = datetime.fromtimestamp(int(vid) >> 32, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    now = datetime.fromisoformat(now) if isinstance(now, str) else (
        now or datetime.now(timezone.utc))
    if t < ID_EPOCH_MIN or (t - now).total_seconds() > ID_FUTURE_SLACK_SEC:
        return None
    return t.isoformat()


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
    reg_at = st["created_at"]
    record = {"event": "linked", "code": code, "at": now or _now(), "video_id": vid}
    if v is not None:
        when, source = v["published_at"], "выгрузка"
        record["published_at"] = when
    else:
        # ролика ещё нет в выгрузке: дата — из его ID, порядок проверим
        # повторно, когда он придёт (evaluate)
        when = id_time(vid, now=now)
        if when is None:
            return False, (f"ролика {vid} нет в данных, и по номеру не видно даты "
                           "публикации — проверьте ссылку или дождитесь, пока "
                           "выгрузка его покажет")
        source = "ID ролика"
        record.update(published_at=None, id_time=when, pending=True)
    if _as_utc(when) < _as_utc(reg_at):
        return False, (f"ролик опубликован {when[:16]} ({source}), раньше регистрации "
                       f"{code} ({reg_at[:16]}): результат был известен до "
                       "эксперимента, такая проверка — подгонка")
    # ролик засчитывается идее эксперимента по очереди взятия: первая
    # привязка — первой идее, вторая — второй. Нужно плану: вышедшие идеи
    # больше не планируются
    ideas = st.get("ideas") or []
    if len(st["videos"]) < len(ideas):
        record["idea_title"] = ideas[len(st["videos"])].get("title")
    _append(path, record)
    return True, ("ролика пока нет в выгрузке — привязан по дате из ID, порядок "
                  "перепроверится, когда он появится" if v is None else None)


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


def _link_valid(st, vid, videos):
    """(действительна ли привязка, пояснение). Порядок «регистрация →
    публикация» проверяется по самой точной дате, какая есть сейчас:
    published_at выгрузки, если ролик в ней уже есть, иначе — дата из ID."""
    rec = (st.get("links") or {}).get(vid, {})
    v = videos.get(vid)
    when = (v or {}).get("published_at") or rec.get("published_at") or rec.get("id_time")
    if when and _as_utc(when) < _as_utc(st["created_at"]):
        return False, (f"привязка недействительна: ролик опубликован {when[:16]}, "
                       f"раньше регистрации ({st['created_at'][:16]}) — в итог не входит")
    return True, None


def control(st, analysis, videos, ctx=None):
    """Контроль того же периода: зрелые ролики, вышедшие после регистрации,
    не привязанные к эксперименту и ВНЕ условия гипотезы. Сравнение с ними
    не зависит от того, просел или вырос аккаунт целиком — в отличие от
    сравнения с медианой прошлых роликов. None — условие не записано
    (ранние регистрации, гипотезы сессии без машинных границ)."""
    cond = st["base"].get("condition")
    if not cond:
        return None
    ctx = A.context() if ctx is None else ctx
    reg = _as_utc(st["created_at"])
    views = []
    for vid, v in videos.items():
        if vid in st["videos"] or not v.get("published_at") \
                or _as_utc(v["published_at"]) < reg:
            continue
        obs = _observation(vid, analysis)
        if obs is None or obs[1] < MATURITY_DAYS:
            continue
        if A.holds(cond, A.video_attrs(v, ctx)) is False:
            views.append(obs[0])
    return {"n": len(views),
            "median": statistics.median(views) if views else None,
            "label": f"{cond.get('label')}: не {cond.get('value')}"}


def rule_verdict(st, mine, base, ctl, n):
    """(итог, пояснение) по правилу, записанному при регистрации, или None.

    supported     — медиана эксперимента выше и базы, и контроля того же
                    периода (в контроле не меньше MIN_CONTROL роликов);
    not_supported — не выше базы, либо выше базы, но не выше контроля
                    (вырос весь аккаунт, а не ролики в условии);
    inconclusive  — выше базы, а контроля нет или он меньше MIN_CONTROL:
                    общий рост аккаунта не исключён. Эксперимент не
                    закрывается — нужны ролики вне условия в тот же период.
    """
    if st["base"].get("decision_rule") != DECISION_RULE:
        return None
    tail = f", n={n}. Причинность не установлена."
    if mine <= base:
        return ("not_supported", f"медиана эксперимента {A._num(mine)} не выше медианы "
                                 f"базы {A._num(base)}" + tail)
    if ctl is None or ctl["n"] < MIN_CONTROL:
        k = 0 if ctl is None else ctl["n"]
        return ("inconclusive", f"медиана эксперимента {A._num(mine)} выше базы "
                                f"{A._num(base)}, но роликов того же периода вне условия "
                                f"{k} из {MIN_CONTROL} нужных: общий рост аккаунта не "
                                "исключён" + tail)
    if mine <= ctl["median"]:
        return ("not_supported", f"медиана эксперимента {A._num(mine)} выше базы "
                                 f"{A._num(base)}, но не выше контроля того же периода "
                                 f"{A._num(ctl['median'])} (n={ctl['n']}): вырос весь "
                                 "аккаунт, а не ролики в условии" + tail)
    return ("supported", f"медиана эксперимента {A._num(mine)} выше базы {A._num(base)} "
                         f"и контроля того же периода {A._num(ctl['median'])} "
                         f"(n={ctl['n']})" + tail)


def conclude_by_rule(code, analysis, path=REGISTER, now=None, videos=None, ctx=None):
    """(True, текст) — закрыт по правилу; (False, причина) — не закрыт."""
    st = load(path).get(code)
    if st is None or st["status"] not in OPEN:
        return False, f"{code}: нет открытого эксперимента с таким кодом"
    ev = evaluate(st, analysis, videos, ctx)
    if ev["n_mature"] < (ev.get("min_sample") or DEFAULT_MIN_SAMPLE):
        return False, f"{code}: итога пока нет — {ev.get('summary')}"
    if ev.get("rule") is None:
        return False, f"{code}: правило итога не записано при регистрации — итог вручную"
    verdict, text = ev["rule"]
    if verdict == "inconclusive":
        return False, f"{code} остаётся открытым: {text}"
    conclude(code, verdict, text, f"наблюдения на {analysis.get('observed_at')}", path, now)
    return True, f"{code} закрыт: {verdict} — {text}"


def evaluate(st, analysis, videos=None, ctx=None):
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
    videos = _videos() if videos is None else videos
    for vid in st["videos"]:
        valid, why = _link_valid(st, vid, videos)
        if not valid:
            out["videos"].append({"video_id": vid, "state": why, "invalid": True})
            continue
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
        out["summary"] = (f"роликов привязано {len(st['videos'])}, зрелых "
                          f"{out['n_mature']} из {need} — вывода пока нет")
    else:
        out["summary"] = (f"{out['n_above']} из {out['n_mature']} зрелых роликов выше "
                          f"медианы базы, n={out['n_mature']}")
        mature_views = [r["views"] for r in out["videos"]
                        if "views" in r and r["age_days"] >= MATURITY_DAYS]
        ctl = control(st, analysis, videos, ctx)
        out["control"] = ctl
        out["rule"] = rule_verdict(st, statistics.median(mature_views), base, ctl,
                                   out["n_mature"])
        if ctl is not None:
            mine = statistics.median(mature_views)
            out["summary"] += (
                f" · медиана эксперимента {A._num(mine)} против контроля того же "
                f"периода {A._num(ctl['median'])} (n={ctl['n']}, «{ctl['label']}»)"
                if ctl["n"] else
                " · контроля нет: роликов того же периода вне условия ещё нет")
    n_invalid = sum(1 for r in out["videos"] if r.get("invalid"))
    if n_invalid:
        out["summary"] += f" · недействительных привязок: {n_invalid}"
    return out


def render(states, analysis, videos=None):
    L = ["ЭКСПЕРИМЕНТЫ", ""]
    if not states:
        return "ЭКСПЕРИМЕНТЫ\n\nжурнал пуст"
    for code in sorted(states, key=lambda c: (c is None, c)):
        st = states[code]
        ev = evaluate(st, analysis, videos)
        L.append(f"{code}  [{st['status']}]  {(st.get('hypothesis') or '')[:110]}")
        if st["status"] == "blocked":
            valid = ev.get("block_still_valid")
            L.append(f"    заблокирован: {ev['note']}")
            if valid is False:
                L.append("    ВНИМАНИЕ: пары больше нет в реестре — блокировка устарела")
        elif st["status"] == "concluded":
            L.append(f"    итог: {st.get('verdict')} — {st.get('result')}")
        else:
            if st.get("ideas"):
                L.append("    идеи: " + "; ".join(f"«{i.get('title')}»" for i in st["ideas"]))
            if ev.get("baseline") is not None:
                L.append(f"    база: медиана зрелых {A._num(ev['baseline'])} "
                         f"(зафиксирована при регистрации)")
            for row in ev["videos"]:
                views = f"{row['views']} просм., {row['age_days']} дн. · " if "views" in row else ""
                L.append(f"    ролик {row['video_id']}: {views}{row['state']}")
            L.append(f"    {ev['summary']}")
            if ev.get("rule"):
                L.append(f"    итог по правилу: {ev['rule'][0]} — {ev['rule'][1]}"
                         + ("" if ev["rule"][0] == "inconclusive"
                            else " · закрыть: меню 5 → 4"))
        L.append("")
    return "\n".join(L).rstrip()


def taken_message(code, before, after):
    """Что сказать человеку после «взять идею в работу»."""
    st = after[code]
    tail = ("Снимите и выложите ролик, затем привяжите его: меню 5 → 3 "
            f"(или python -m advisor.experiments link {code} <ссылка>).")
    if code not in before:
        return (f"{code} зарегистрирован ДО публикации, база сравнения зафиксирована. "
                + tail)
    need = st.get("min_sample") or DEFAULT_MIN_SAMPLE
    return (f"идея добавлена к {code}: он уже проверяет ту же гипотезу. Идей в нём "
            f"{len(st.get('ideas') or [])}, роликов привязано {len(st['videos'])}, "
            f"для вывода нужно {need} зрелых. База сравнения — прежняя, с "
            f"{st['created_at'][:10]}. " + tail)


def _repeatable(idea):
    """Шаблон («ролик в привычном формате, меняется один признак») можно
    брать сколько угодно раз: так набирается выборка под гипотезу без
    ключа API. Идея модели или сессии — одна, второй раз её не взять."""
    return idea.get("source") == "template"


def idea_states(states):
    """{название идеи: (код, "published" | "taken")}: вышла ли идея роликом
    или только взята в работу. Шаблонов здесь нет — они повторяемы."""
    out = {}
    for st in states.values():
        done = {(st.get("links") or {}).get(v, {}).get("idea_title") for v in st["videos"]}
        for i in st.get("ideas") or []:
            if _repeatable(i):
                continue
            out[i.get("title")] = (st["code"], "published" if i.get("title") in done
                                   else "taken")
    return out


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
    return path, _attach(data)


def _attach(data):
    """Идеи документа с контекстом, нужным регистрации и плану."""
    ideas = data.get("ideas", [])
    for i in ideas:
        i["_new_hypotheses"] = data.get("new_hypotheses", [])
        i["_snapshot"] = data.get("hypotheses")
    return ideas


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
            ideas = _attach(json.loads(path.read_text(encoding="utf-8")))
        else:
            path, ideas = latest_ideas()
        if not ideas:
            print("идей нет — сначала пункт «Идеи для следующих видео»")
            return 1
        if not 1 <= args.n <= len(ideas):
            print(f"идеи №{args.n} нет: в {path.name} их {len(ideas)}")
            return 1
        before = load()
        try:
            code = register_idea(ideas[args.n - 1], a)
        except ValueError as exc:
            print(f"не зарегистрировано: {exc}")
            return 1
        print(taken_message(code, before, load()))
        return 0
    ok, why = link(args.code, args.video)
    print((f"привязано к {args.code}" + (f" — {why}" if why else ""))
          if ok else f"не привязано: {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
