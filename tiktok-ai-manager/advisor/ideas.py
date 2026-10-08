#!/usr/bin/env python3
"""Идеи для следующих роликов.

Два режима, выбор автоматический:

* модель Claude — если задан ANTHROPIC_API_KEY (в окружении или в .env) и
  установлен пакет anthropic (requirements-advisor.txt). Модель придумывает,
  ЧТО снимать, опираясь на разбор advisor.analysis;
* без модели — шаблоны экспериментов по каждой гипотезе разбора. Работает
  всегда, ничего не стоит и ничего не выдумывает.

Модель — генератор, а не источник фактов. Её ответ проходит те же рубежи,
что и остальные выводы проекта, и всё, что их не прошло, отбрасывается
с названной причиной, а не тихо:

  * каждая идея проверяет ровно одну гипотезу — из разбора (H…) или новую,
    сформулированную моделью (L…) и прошедшую проверку;
  * числа в утверждениях о данных обязаны встречаться в брифе — иначе это
    выдуманная статистика;
  * новая гипотеза опирается на существующие video_id;
  * причинные и оценочные конструкции («лучше», «точно зайдёт») отвергает
    insights.validator — модель предлагает ставку, а не обещает результат.

Ключ берётся только из окружения, в коде и в выводе его нет; любой текст,
уходящий в консоль или в файл, проходит mobile.audit.scrub.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A                           # noqa: E402
from insights.validator import (CAUSAL_PATTERNS, JUDGEMENT_PATTERNS,  # noqa: E402
                                find_violations)

IDEAS_POLICY_VERSION = "advisor-ideas-1.0.0"
MODEL = "claude-opus-5-5"
EFFORT = "high"                    # идеи — работа на качество, не на скорость
MAX_TOKENS = 16000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
N_IDEAS = 5
OUT_DIR = ROOT / "data" / "ideas"

IDEA_FIELDS = ("title", "what_to_film", "caption_draft", "when_to_post",
               "tests_hypothesis", "data_basis", "success_check")
NEW_HYP_FIELDS = ("id", "statement", "evidence_video_ids",
                  "competing_explanation")

SCHEMA = {
    "type": "object",
    "properties": {
        "new_hypotheses": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "statement": {"type": "string"},
                "evidence_video_ids": {"type": "array",
                                       "items": {"type": "string"}},
                "competing_explanation": {"type": "string"},
            },
            "required": list(NEW_HYP_FIELDS),
            "additionalProperties": False}},
        "ideas": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in IDEA_FIELDS},
            "required": list(IDEA_FIELDS),
            "additionalProperties": False}},
    },
    "required": ["new_hypotheses", "ideas"],
    "additionalProperties": False,
}


def scrub(text):
    from mobile import audit
    return audit.scrub(text)


# ───────────────────────────── числа и проверки ─────────────────────────────

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(n):
    n = n.replace(",", ".")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n.lstrip("0") or "0"


def numbers_in(text):
    """Все числа текста в одном написании: 97,8 и 97.80 — одно и то же."""
    return {_norm(m) for m in _NUM.findall(text or "")}


def invented_numbers(text, allowed):
    """Числа, которых нет в брифе. Пусто — выдуманной статистики нет."""
    return sorted(numbers_in(text) - allowed)


# ─────────────────────────────────── бриф ───────────────────────────────────

UNVERIFIED_MARK = "не сверено"


def brief_parts(a):
    """(сверенная часть, несверенная часть). Разделение нужно проверке чисел."""
    L = ["ДАННЫЕ АККАУНТА @gulyashik52 (сверенные)",
         f"Замер: {a['observed_at']}. Роликов {a['n_videos']}, "
         f"зрелых (не моложе {a['mature_age_days']} дней) {a['n_mature']}.", "",
         "Факты:"]
    L += [f"- {f['id']}: {f['statement']}" for f in a["facts"]]
    L += ["", "Гипотезы разбора (проверяются публикациями):"]
    for h in a["hypotheses"]:
        L.append(f"- {h['id']}: {h['statement']}")
        if h.get("tz_robust") is True:
            L.append(f"  держится и в другом поясе — {h['local_view']}")
        elif h.get("tz_robust") is False:
            L.append(f"  ВНИМАНИЕ: {h['tz_note']}")
        if h["competing_explanation"] != A.COMPETING:
            L.append(f"  оговорка: {h['competing_explanation'][len(A.COMPETING) + 1:]}")
    if not a["hypotheses"]:
        L.append("- нет")
    ctx = a.get("context") or {}
    if ctx.get("top_slots"):
        start, stop = ctx.get("grid_period") or ("?", "?")
        L += ["", f"Оценка Metricool (модель источника, не наблюдение; неделя {start}…{stop}, "
                  f"{ctx.get('tz_name') or 'пояс запроса'}) — самые активные часы аудитории: "
                  + ", ".join(f"{d} {h}:00 ({v})" for d, h, v in ctx["top_slots"]) + "."]
    L += ["", "Признаки, которые хиты НЕ отличают:"]
    L += ([f"- {x['statement']}" for x in a["not_distinguishing"]] or ["- нет"])
    L += ["", "Ролики (сверенные просмотры; молодые помечены):"]
    for v in a["videos"]:
        tag = "ХИТ" if v.get("hit") else ("МОЛОДОЙ" if not v["mature"] else "")
        L.append(f"- video_id {v['video_id']} {tag} | просмотров {v['views']} | "
                 f"возраст {v['age_days']} дн. | {A._num(v['duration_sec'])} с | "
                 f"{v['weekday']} {v['hour_utc']}:00 UTC | подпись: "
                 f"«{v['caption'][:300]}»")
    U = []
    growth = [g for g in a.get("growth", []) if not g["new"]][:5]
    if growth:
        U += ["РОСТ ОТ ПОСЛЕДНЕГО СВЕРЕННОГО ЗНАЧЕНИЯ — не сверено:"]
        U += [f"- video_id {g['video_id']} | +{g['delta']} за {g['days']} дн."
              for g in growth]
        U.append("")
    if a.get("unverified"):
        U = ["НЕ СВЕРЕНО — только один источник, второй недоступен. Это не факты: "
             f"упоминать только с пометкой «{UNVERIFIED_MARK}».", ""]
        for u in a["unverified"]:
            U.append(f"- video_id {u['video_id']} | просмотров {u['views']} на "
                     f"{u['observed_at'][:10]} | возраст {u['age_days']} дн. | "
                     f"подпись: «{u['caption'][:300]}»")
    return "\n".join(L), "\n".join(U)


def build_brief(a):
    """Всё, что модели разрешено знать. Только поля разбора, без догадок."""
    verified, unverified = brief_parts(a)
    return verified + ("\n\n" + unverified if unverified else "")


def _banned_list():
    return ", ".join(sorted({label for _, label in CAUSAL_PATTERNS + JUDGEMENT_PATTERNS}))


SYSTEM = """Ты помогаешь владельцу TikTok-аккаунта @gulyashik52 решить, что снять дальше. \
Опирайся только на данные аккаунта, которые придут в сообщении.

Правила, которые проверяются программой после твоего ответа (нарушение — идея отбрасывается):
1. Числа о прошлых роликах бери только из данных и пиши ровно как там записано. \
Других чисел о результатах не придумывай. В поле data_basis — только факты из данных.
2. Не обещай результат и не утверждай причинность. Запрещены слова и обороты: {banned}. \
Каждая идея — ставка, которую проверит публикация.
3. Каждая идея проверяет ровно одну гипотезу: id из списка гипотез разбора (H…) или \
новой гипотезы из твоего new_hypotheses (id L1, L2, …). Поле tests_hypothesis — только этот id.
4. Новая гипотеза: со словом «может» или «возможно», с указанием «n=…», с фразой \
«Причинность не установлена.»; опирается на конкретные video_id из данных; \
называет конкурирующее объяснение.
5. Ролики моложе {mature} дней по просмотрам с остальными не сравнивай.
6. Что происходило в кадре, в данных нет — есть только подписи. Говори о подписях, \
а не о том, что было в видео.
7. Числа из блока «НЕ СВЕРЕНО» — не факты: упоминай их только со словами «не сверено».
8. Числа в what_to_film, caption_draft и when_to_post — это план (длительность, время, \
число роликов серии), а не статистика о прошлом.

Придумай {n} разных идей. Пиши по-русски, конкретно: что снять, черновик подписи, \
когда опубликовать (UTC), как через {mature} дней понять, сработало ли."""


def system_prompt():
    return SYSTEM.format(banned=_banned_list(), mature=A.MATURE_AGE_DAYS,
                         n=N_IDEAS)


# ─────────────────────────────── проверка ответа ─────────────────────────────

def unverified_only_numbers(a):
    """Числа, которые есть в несверенной части брифа и нет в сверенной."""
    verified, unverified = brief_parts(a)
    return numbers_in(unverified) - numbers_in(verified)


def unmarked_unverified(text, a):
    """Несверенные числа в тексте без пометки «не сверено». Пусто — порядок."""
    if UNVERIFIED_MARK in (text or "").lower():
        return []
    return sorted(numbers_in(text) & unverified_only_numbers(a))


def validate(payload, a, brief):
    """Принятые гипотезы и идеи плюс отвергнутые с причинами."""
    allowed = numbers_in(brief)
    known_ids = {v["video_id"] for v in a["videos"]}
    hyp_ids = {h["id"] for h in a["hypotheses"]}
    accepted_h, accepted_i, rejected = [], [], []

    for h in payload.get("new_hypotheses", []):
        why = []
        hid = str(h.get("id", ""))
        if not re.fullmatch(r"L\d+", hid):
            why.append(f"id «{hid}» не вида L1, L2, …")
        ev = h.get("evidence_video_ids") or []
        if not ev:
            why.append("нет опорных video_id")
        unknown = [x for x in ev if x not in known_ids]
        if unknown:
            why.append(f"несуществующие video_id: {', '.join(unknown)}")
        why += find_violations(h.get("statement", ""), "HYPOTHESIS")
        why += [f"конкурирующее объяснение: {x}" for x in
                find_violations(h.get("competing_explanation", ""), "RECOMMENDATION")]
        bad = invented_numbers(h.get("statement", ""), allowed)
        if bad:
            why.append(f"чисел нет в данных: {', '.join(bad)}")
        raw = unmarked_unverified(h.get("statement", ""), a)
        if raw:
            why.append(f"несверенные числа без пометки «{UNVERIFIED_MARK}»: {', '.join(raw)}")
        if why:
            rejected.append({"kind": "гипотеза", "id": hid, "reasons": why})
            continue
        accepted_h.append({**{k: h.get(k) for k in NEW_HYP_FIELDS},
                           "claim_type": "HYPOTHESIS", "source": "model",
                           "min_sample_required": A.MIN_SAMPLE_FOR_FACT,
                           "evidence_count": len(ev)})
        hyp_ids.add(hid)

    for i, idea in enumerate(payload.get("ideas", []), 1):
        why = []
        if idea.get("tests_hypothesis") not in hyp_ids:
            why.append(f"проверяет неизвестную гипотезу «{idea.get('tests_hypothesis')}»")
        # Подпись — текст будущего поста, а не утверждение о данных:
        # проверять её на причинность бессмысленно.
        for field in ("title", "what_to_film", "when_to_post", "data_basis",
                      "success_check"):
            why += [f"{field}: {x}" for x in
                    find_violations(idea.get(field, ""), "RECOMMENDATION")]
        bad = invented_numbers(idea.get("data_basis", ""), allowed)
        if bad:
            why.append(f"data_basis: чисел нет в данных: {', '.join(bad)}")
        raw = unmarked_unverified(idea.get("data_basis", ""), a)
        if raw:
            why.append(f"data_basis: несверенные числа без пометки "
                       f"«{UNVERIFIED_MARK}»: {', '.join(raw)}")
        if why:
            rejected.append({"kind": "идея", "id": idea.get("title") or f"#{i}",
                             "reasons": why})
            continue
        accepted_i.append({**{k: idea.get(k, "") for k in IDEA_FIELDS},
                           "claim_type": "RECOMMENDATION", "source": "model"})
    return accepted_h, accepted_i, rejected


# ─────────────────────────────── без модели ──────────────────────────────────

def _when(h):
    """Когда публиковать по шаблону — в обоих поясах, если они известны."""
    if h["attribute"] in ("hour_utc", "hour_local", "weekday", "weekday_local"):
        text = f"{h['label']}: {h['value']}"
        if h.get("local_view"):
            text += f" (то же: {h['local_view']})"
        if h.get("tz_robust") is False:
            text += "; гипотеза зависит от пояса — проверять именно в нём"
        return text
    if h["attribute"] == "activity_pct":
        return (f"в час, который Metricool оценивает в {h['value']} по активности "
                "аудитории (тепловая карта — на дашборде)")
    return "как обычно"


def offline_ideas(a):
    """Эксперимент на каждую гипотезу разбора. Детерминированно."""
    ideas = []
    median = A._num(a.get("median_views_mature", 0))
    for h in a["hypotheses"]:
        ideas.append({
            "title": f"Эксперимент к {h['id']}",
            "what_to_film": "Ролик в привычном для аккаунта формате. Меняется "
                            f"одно: «{h['label']}: {h['value']}»; остальное как "
                            "обычно — иначе публикация не проверит гипотезу.",
            "caption_draft": "",
            "when_to_post": _when(h),
            "tests_hypothesis": h["id"],
            "data_basis": h["statement"],
            "success_check": f"Через {A.MATURE_AGE_DAYS} дней сравнить просмотры "
                             f"с медианой зрелых роликов ({median}).",
            "claim_type": "RECOMMENDATION", "source": "template",
        })
    return ideas


# ───────────────────────────────── модель ────────────────────────────────────

def make_client():
    """(client, None) либо (None, причина). Ключ не читается и не печатается."""
    from core import config
    config.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY не задан — идеи без модели"
    try:
        import anthropic
    except ImportError:
        return None, ("пакет anthropic не установлен "
                      "(pip install -r requirements-advisor.txt) — идеи без модели")
    return anthropic.Anthropic(), None


def request_kwargs(brief):
    """Параметры запроса отдельно от вызова — их проверяет тест."""
    return dict(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # отказ классификатора безопасности переигрывается на запасной
        # модели сервером, а не возвращается пустым ответом
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": EFFORT,
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        system=system_prompt(),
        messages=[{"role": "user", "content": brief}],
    )


def explain_api_error(exc, sdk):
    """Понятная причина сбоя. sdk передаётся явно — тест подставляет свой."""
    table = (("AuthenticationError", "ключ ANTHROPIC_API_KEY не подошёл"),
             ("PermissionDeniedError", "у ключа нет доступа к модели"),
             ("NotFoundError", f"модель {MODEL} недоступна для этого ключа"),
             ("RateLimitError", "превышен лимит запросов — повторите позже"),
             ("BadRequestError", "запрос отклонён API"),
             ("APITimeoutError", "API не ответил вовремя"),
             ("APIConnectionError", "нет связи с API — проверьте интернет"),
             ("APIStatusError", "ошибка на стороне API"))
    for name, text in table:
        cls = getattr(sdk, name, None)
        if isinstance(cls, type) and isinstance(exc, cls):
            return text
    return f"непредвиденная ошибка: {type(exc).__name__}"


def call_model(client, brief, sdk=None):
    """(payload, None) либо (None, причина)."""
    try:
        resp = client.beta.messages.create(**request_kwargs(brief))
    except Exception as exc:
        if sdk is None:
            try:
                import anthropic as sdk
            except ImportError:
                sdk = None
        return None, explain_api_error(exc, sdk)
    if resp.stop_reason == "refusal":
        return None, "модель отказалась отвечать на этот запрос"
    if resp.stop_reason == "max_tokens":
        return None, "ответ модели оборвался по длине"
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "ответ модели — не JSON"


# ──────────────────────────────── прогон ─────────────────────────────────────

def generate(a=None, client=None, offline=False, sdk=None):
    """Полный прогон. Возвращает результат; ничего не пишет на диск."""
    a = A.analyze() if a is None else a
    brief = build_brief(a)
    result = {"policy_version": IDEAS_POLICY_VERSION,
              "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "analysis_policy": a["policy_version"],
              "mode": "template", "model": None, "note": None,
              "new_hypotheses": [], "ideas": [], "rejected": []}
    reason = "выбран режим без модели" if offline else None
    if not offline and client is None:
        client, reason = make_client()
    if client is not None and not offline:
        payload, reason = call_model(client, brief, sdk)
        if payload is not None:
            hyps, ideas, rejected = validate(payload, a, brief)
            result.update(mode="model", model=MODEL, new_hypotheses=hyps,
                          ideas=ideas, rejected=rejected)
            if not ideas:
                result["note"] = "ни одна идея модели не прошла проверку"
    if result["mode"] == "template":
        result["note"] = reason
        result["ideas"] = offline_ideas(a)
    return result


def render(r):
    L = ["ИДЕИ ДЛЯ СЛЕДУЮЩИХ РОЛИКОВ",
         ("режим: модель " + r["model"]) if r["mode"] == "model"
         else "режим: шаблоны экспериментов, без модели"]
    if r["note"]:
        L.append(f"примечание: {r['note']}")
    if r["new_hypotheses"]:
        L += ["", "Новые гипотезы модели (HYPOTHESIS, проверяются публикацией):"]
        for h in r["new_hypotheses"]:
            L.append(f"  {h['id']}  {h['statement']}")
            L.append(f"      опора: {', '.join(h['evidence_video_ids'])}")
            L.append(f"      конкурирующее объяснение: {h['competing_explanation']}")
    if not r["ideas"]:
        L += ["", "Идей нет: в данных не нашлось гипотез, которые можно проверить.",
              "Больше данных дадут видеофайлы в data/assets/incoming и новые публикации."]
    for i, idea in enumerate(r["ideas"], 1):
        L += ["", f"{i}. RECOMMENDATION · {idea['title']}  "
                  f"(проверяет {idea['tests_hypothesis']})",
              f"   что снять: {idea['what_to_film']}"]
        if idea["caption_draft"]:
            L.append(f"   подпись:   {idea['caption_draft']}")
        L += [f"   когда:     {idea['when_to_post']}",
              f"   опора:     {idea['data_basis']}",
              f"   проверка:  {idea['success_check']}"]
    if r["rejected"]:
        L += ["", f"Отброшено проверкой: {len(r['rejected'])}"]
        for x in r["rejected"]:
            L.append(f"  {x['kind']} «{x['id']}»: {'; '.join(x['reasons'])}")
    L += ["", "Ни одна идея не гарантирует результат: это ставки, которые "
              "проверит публикация."]
    return scrub("\n".join(L))


def save(r, out_dir=OUT_DIR):
    """Результат в JSON (полный) и в текст (для чтения). Пути возвращаются."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = r["generated_at"].replace(":", "").replace("-", "")[:15]
    base = out_dir / f"{stamp}_{r['mode']}"
    j, t = base.with_suffix(".json"), base.with_suffix(".txt")
    j.write_text(scrub(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True)),
                 encoding="utf-8")
    t.write_text(render(r) + "\n", encoding="utf-8")
    return j, t
