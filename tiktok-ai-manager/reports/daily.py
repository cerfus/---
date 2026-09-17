#!/usr/bin/env python3
"""Ежедневный отчёт. Восемь разделов, ни один не опускается.

Пустой раздел печатается с причиной: отчёт, умалчивающий о пустоте,
создаёт видимость полноты. Раздел «Заблокированные выводы» обязателен и
непуст — именно он отличает отчёт от витрины.

Текст детерминирован: время генерации в тело не попадает, только период и
хеши входов.
"""
import json
import re

from insights import policies as P

REPORT_VERSION = "daily-report-1.0.0"
FOOTER_RE = re.compile(r"<!--\s*machine-readable\s*(\{.*?\})\s*-->", re.DOTALL)


def _fmt(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        return f"{v:,.{nd}f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.0f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def read_previous(path):
    """Читает машинный блок предыдущего отчёта, если он есть."""
    if not path or not path.exists():
        return None
    m = FOOTER_RE.search(path.read_text(encoding="utf-8"))
    return json.loads(m.group(1)) if m else None


def build(period, ins, blocked, account_rows, coverage_rows, experiments,
          hashes, previous=None, n_videos=0):
    by_type = {}
    for i in ins:
        by_type.setdefault(i["claim_type"], []).append(i)

    L = [f"# Ежедневный отчёт — {period}", "",
         f"Аккаунт `@gulyashik52` · версия отчёта `{REPORT_VERSION}` · "
         f"политика `{P.INSIGHTS_POLICY_VERSION}`", ""]

    # 1 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['what_changed']}", ""]
    if previous is None:
        L += ["Первый отчёт: сравнивать не с чем. Последующие отчёты будут "
              "сравниваться с машинным блоком этого.", ""]
    else:
        changed = [k for k in sorted(hashes) if previous.get("hashes", {}).get(k) != hashes[k]]
        if not changed:
            L += ["Изменений нет: все хеши входных слоёв совпадают с предыдущим "
                  "отчётом.", ""]
        else:
            L += ["Изменились слои: " + ", ".join(f"`{c}`" for c in changed), ""]
            for c in changed:
                L += [f"* `{c}`: `{previous.get('hashes', {}).get(c, '—')[:12]}` → "
                      f"`{hashes[c][:12]}`"]
            L += [""]

    # 2 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['important_metrics']}", ""]
    obs = [r for r in account_rows if r["window_status"] == "observed"]
    if not obs:
        L += ["Нет окон с наблюдениями.", ""]
    else:
        window = obs[0]["window"]
        L += [f"Окно `{window}` (возрастной бакет `{obs[0]['age_bucket']}`), "
              f"n={obs[0]['n']}, статус выборки `{obs[0]['sample_status']}`.", "",
              "| Метрика | n | медиана | p25 | p75 | выбросов |",
              "|---|---:|---:|---:|---:|---:|"]
        keep = ["views", "likes", "comments", "shares", "favorites",
                "engagement_rate", "completion_rate", "avg_view_time_sec",
                "duration_sec"]
        for m in keep:
            r = next((x for x in obs if x["metric"] == m), None)
            if r:
                L.append(f"| {m} | {r['n']} | {_fmt(r['median'])} | {_fmt(r['p25'])} "
                         f"| {_fmt(r['p75'])} | {r['n_outliers']} |")
        L += ["", "Все значения описательные. Ни одно из них не является выводом "
              "о качестве контента.", ""]

    # 3 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['new_evidence']}", ""]
    facts = by_type.get("FACT", [])
    if not facts:
        L += [f"Подтверждений уровня FACT нет: размер выборки n={n_videos} ниже "
              f"порога {P.MIN_SAMPLE_FOR_FACT}. Это ожидаемое состояние, а не сбой.", ""]
    else:
        L += [f"* {f['statement']} (n={f['n_sample']})" for f in facts] + [""]

    # 4 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['new_hypotheses']}", ""]
    hyps = by_type.get("HYPOTHESIS", [])
    if not hyps:
        # Пустой раздел обязан объяснить, почему он пуст: иначе читатель решит,
        # что связи не измерялись.
        cand = [b for b in blocked
                if b.get("reason_code") in ("mechanically_dependent",
                                            "not_distinguishable_from_noise")]
        L += ["Новых гипотез нет."]
        if cand:
            mech = [b for b in cand if b["reason_code"] == "mechanically_dependent"]
            noise = [b for b in cand if b["reason_code"] == "not_distinguishable_from_noise"]
            L += ["",
                  f"Кандидатов рассмотрено и отклонено: {len(cand)} "
                  f"(механически зависимых пар — {len(mech)}, "
                  f"неотличимых от шума — {len(noise)}). "
                  "Основания в разделе 8."]
        L += [""]
    for h in hyps:
        L += [f"* {h['statement']}",
              f"  * конкурирующее объяснение: {h['competing_explanation']}",
              f"  * проверяется экспериментом; на текущих данных не проверена"]
    if hyps:
        L += [""]

    # 5 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['experiments']}", ""]
    if not experiments:
        L += ["В базе нет заведённых экспериментов. Реестр гипотез Phase 0 "
              "(`EXP-001`…`EXP-004`) хранится в `experiments/register.jsonl` и "
              "переносится в базу на Phase 8.", ""]
    else:
        L += ["| Код | Статус | Выборка |", "|---|---|---:|"]
        L += [f"| {e['code']} | {e['status']} | {e.get('n', '—')} |" for e in experiments]
        L += [""]

    # 6 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['content_recommendations']}", ""]
    L += ["Рекомендаций по контенту нет и быть не может на этом этапе: они "
          f"требуют опоры на FACT, а подтверждений уровня FACT ноль (n={n_videos} "
          f"< {P.MIN_SAMPLE_FOR_FACT}). Рекомендации по сбору данных вынесены в "
          "раздел 7.", ""]

    # 7 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['data_quality']}", ""]
    dq = by_type.get("DATA_QUALITY", [])
    an = by_type.get("ANOMALY", [])
    rec = by_type.get("RECOMMENDATION", [])
    if not (dq or an or rec):
        L += ["Проблем качества данных не зафиксировано.", ""]
    for i in dq:
        L.append(f"* {i['statement']}")
    for i in an:
        L.append(f"* **Аномалия:** {i['statement']}")
    if rec:
        L += ["", "Рекомендации по сбору данных:"]
        for i in rec:
            L.append(f"* {i['statement']}")
    L += [""]
    overall = next((r for r in coverage_rows if r["scope"] == "all_metrics"), None)
    if overall:
        L += ["| Состояние | Наблюдений |", "|---|---:|",
              f"| пригодно | {overall['eligible_observations']} |",
              f"| не проверено | {overall['data_untested']} |",
              f"| недоступно | {overall['data_unavailable']} |",
              f"| противоречиво | {overall['data_discrepant']} |", ""]

    # 8 ─────────────────────────────────────────────────────────────────────
    L += [f"## {dict(P.DAILY_SECTIONS)['blocked_conclusions']}", ""]
    if not blocked:
        L += ["**Пусто — это подозрительно.** Отчёт без заблокированных выводов "
              "означает, что система не проверяла границы своих знаний.", ""]
    else:
        codes = {}
        for b in blocked:
            codes[b.get("reason_code", "—")] = codes.get(b.get("reason_code", "—"), 0) + 1
        L += [f"Выводов, которые система могла бы сделать, но не имеет права: "
              f"**{len(blocked)}**.", "",
              "По машинным причинам: " +
              ", ".join(f"`{k}` — {v}" for k, v in sorted(codes.items())), "",
              "| Вывод | Причина (код) | Почему заблокирован |", "|---|---|---|"]
        for b in blocked:
            L.append(f"| {b['conclusion']} | `{b.get('reason_code', '—')}` "
                     f"| {b['reason']} |")
        L += [""]

    L += ["---", "",
          f"<!-- machine-readable {json.dumps({'period': period, 'hashes': hashes, 'n_insights': len(ins), 'n_blocked': len(blocked), 'report_version': REPORT_VERSION}, ensure_ascii=False, sort_keys=True)} -->"]
    return "\n".join(L) + "\n"
