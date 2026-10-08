#!/usr/bin/env python3
"""Дашборд аккаунта: один HTML-файл, без сервера и без интернета.

    python -m advisor.dashboard              собрать и открыть в браузере
    python -m advisor.dashboard --no-open    только собрать
    python -m advisor.dashboard --out F      куда сохранить

Только чтение: всё берётся из того же разбора, что пункт 3 меню, из журнала
экспериментов и последнего файла идей. Новых чисел дашборд не вычисляет —
он показывает уже проверенные. Сохраняется в data/runtime/ (не в git):
это вид на данные, а не данные.

Графиков два, и это правило проекта, а не украшение: «любая статистика
считается и с выбросами, и без них, и обе цифры показываются». На общем
графике два хита занимают почти всю шкалу; второй — без них, чтобы
остальные ролики было видно.
"""
import html
import json
import os
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A, experiments as E     # noqa: E402

OUT = ROOT / "data" / "runtime" / "dashboard.html"


def esc(x):
    return html.escape(str(x), quote=True)


def fmt(n):
    """Тысячи через узкий пробел: 698 889."""
    if n is None:
        return "—"
    if isinstance(n, float) and not n.is_integer():
        return f"{n:.1f}".replace(".", ",")
    return f"{int(n):,}".replace(",", " ")


CSS = """
.viz-root{color-scheme:light;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);
 --accent:#2a78d6;--accent-track:#cde2fb;--rest:#898781;
 --warn-bg:#fff6e0;--warn-ink:#6b4a00;
 --q1:#cde2fb;--q2:#9ec5f4;--q3:#6da7ec;--q4:#3987e5;--q5:#256abf;--q6:#184f95;--q7:#0d366b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{
 color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);
 --accent:#3987e5;--accent-track:#184f95;--rest:#898781;
 --warn-bg:#2e2510;--warn-ink:#f2cf7a;
 --q1:#0d366b;--q2:#184f95;--q3:#256abf;--q4:#2a78d6;--q5:#5598e7;--q6:#86b6ef;--q7:#b7d3f6}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
 --ink:#fff;--ink-2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
 --ring:rgba(255,255,255,.10);--accent:#3987e5;--accent-track:#184f95;
 --rest:#898781;--warn-bg:#2e2510;--warn-ink:#f2cf7a;
 --q1:#0d366b;--q2:#184f95;--q3:#256abf;--q4:#2a78d6;--q5:#5598e7;--q6:#86b6ef;--q7:#b7d3f6}
*{box-sizing:border-box}
body{margin:0;background:var(--page)}
.viz-root{font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);
 background:var(--page);max-width:1080px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:36px 0 4px}
.sub{color:var(--ink-2);margin:0 0 4px}
.note{color:var(--muted);font-size:13px;margin:4px 0 12px}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:20px}
.kpi .label{color:var(--ink-2);font-size:13px}
.kpi .value{font-size:30px;font-weight:600;margin:2px 0}
.kpi .ctx{color:var(--muted);font-size:12px}
.warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:10px;padding:10px 14px;
 margin-top:16px;font-size:14px}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--ink-2);font-size:13px;margin:6px 0 10px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.bars{display:grid;gap:2px}
.row{display:grid;grid-template-columns:200px 1fr 88px;align-items:center;gap:10px;
 min-height:26px;padding:1px 4px;border-radius:6px;outline:none}
.row:hover,.row:focus{background:var(--grid)}
.row .name{color:var(--ink-2);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.track{position:relative;height:18px}
.bar{position:absolute;left:0;top:0;height:18px;border-radius:0 4px 4px 0;min-width:2px}
.row .val{font-variant-numeric:tabular-nums;text-align:right;font-size:13px}
.hit .bar{background:var(--accent)} .rest .bar{background:var(--rest)}
.hyps{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.tag{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.04em;
 color:var(--ink-2);border:1px solid var(--ring);border-radius:999px;padding:1px 8px}
.meter{display:grid;grid-template-columns:130px 1fr 52px;gap:8px;align-items:center;
 font-size:13px;color:var(--ink-2);margin-top:8px}
.meter .t{height:10px;background:var(--accent-track);border-radius:5px;overflow:hidden}
.meter .f{height:10px;background:var(--accent);border-radius:5px 4px 4px 5px}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px}
td{overflow-wrap:anywhere}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink-2);font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--accent)}
details summary{cursor:pointer;color:var(--ink-2);margin-top:10px}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);
 border:1px solid var(--ring);border-radius:8px;padding:8px 10px;font-size:13px;max-width:320px;
 box-shadow:0 4px 16px rgba(0,0,0,.12);display:none;z-index:9}
#tip b{display:block;font-size:15px}
#tip span{display:block;color:var(--ink-2)}
.heat{display:grid;grid-template-columns:34px repeat(24,minmax(22px,1fr));gap:2px;
 min-width:620px;font-size:11px;color:var(--muted)}
.heat .c{position:relative;height:24px;border-radius:3px;outline:none}
.heat .c.mark::after{content:"";position:absolute;left:50%;top:50%;width:10px;height:10px;
 margin:-7px 0 0 -7px;border-radius:50%;background:var(--surface);border:2px solid var(--ink)}
.heat .h{text-align:center} .heat .d{line-height:24px}
.ramp{display:inline-flex;gap:2px;vertical-align:middle;margin:0 6px}
.ramp i{width:16px;height:10px;border-radius:2px;display:inline-block}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;border:2px solid var(--ink);
 background:var(--surface);vertical-align:-2px;margin-right:6px}
.tz-ok{color:var(--ink-2)} .tz-warn{color:var(--warn-ink);background:var(--warn-bg);
 border-radius:6px;padding:2px 6px;display:inline-block;margin-top:6px}
@media (max-width:560px){.row{grid-template-columns:118px 1fr 64px}.row .name{font-size:12px}.kpi .value{font-size:24px}.meter{grid-template-columns:96px 1fr 44px}}
"""

JS = """
(function(){
 var tip=document.getElementById('tip');
 function show(el,x,y){
  tip.replaceChildren();
  var b=document.createElement('b');b.textContent=el.dataset.head||(el.dataset.views+' просмотров');tip.appendChild(b);
  ['meta','cap'].forEach(function(k){if(el.dataset[k]){var s=document.createElement('span');
   s.textContent=el.dataset[k];tip.appendChild(s);}});
  tip.style.display='block';
  var r=tip.getBoundingClientRect();
  tip.style.left=Math.min(x+14,innerWidth-r.width-8)+'px';
  tip.style.top=Math.min(y+14,innerHeight-r.height-8)+'px';
 }
 document.querySelectorAll('.row,.heat .c').forEach(function(el){
  el.addEventListener('mousemove',function(e){show(el,e.clientX,e.clientY)});
  el.addEventListener('mouseleave',function(){tip.style.display='none'});
  el.addEventListener('focus',function(){var r=el.getBoundingClientRect();show(el,r.left+160,r.bottom)});
  el.addEventListener('blur',function(){tip.style.display='none'});
 });
})();
"""


def bars(videos, title, note):
    vmax = max((v["views"] for v in videos), default=1) or 1
    rows = []
    for v in videos:
        kind = "hit" if v.get("hit") else "rest"
        label = "хит" if v.get("hit") else ("зрелый" if v["mature"] else "молодой")
        # «молодой» передаётся текстом, а не третьим цветом: светло-серый
        # столбик на светлом фоне был почти невидим (контраст 1,36:1)
        young = "" if v["mature"] else "молодой · "
        width = 100.0 * v["views"] / vmax
        meta = (f"{v['published_at'][:10]} · {A._num(v['duration_sec'])} с · "
                f"{v['weekday']} {v['hour_utc']:02d}:00 UTC · {label}, "
                f"{v['age_days']} дн. на {v['observed_at'][:10]}")
        rows.append(
            f'<div class="row {kind}" tabindex="0" data-views="{esc(fmt(v["views"]))}" '
            f'data-meta="{esc(meta)}" data-cap="{esc((v["caption"] or "без подписи")[:140])}">'
            f'<div class="name">{esc(young)}{esc(v["published_at"][:10])} · …{esc(v["video_id"][-5:])}</div>'
            f'<div class="track"><div class="bar" style="width:{width:.2f}%"></div></div>'
            f'<div class="val">{esc(fmt(v["views"]))}</div></div>')
    return (f'<div class="card"><h2 style="margin-top:0">{esc(title)}</h2>'
            f'<p class="note">{esc(note)}</p>'
            '<div class="legend"><span><i style="background:var(--accent)"></i>хит</span>'
            '<span><i style="background:var(--rest)"></i>остальные</span>'
            '<span>«молодой» в подписи строки — младше 30 дней, со зрелыми не сравнивается</span></div>'
            f'<div class="bars">{"".join(rows)}</div></div>')


DAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def heatmap(a):
    """Активность аудитории по оценке Metricool и где вышли хиты.

    Форма — тепловая карта (сетка «день × час», один оттенок: больше —
    темнее в светлой теме). Кружки — слоты публикации хитов, переведённые
    в пояс оценки. Это оценка источника, а не наблюдение просмотров.
    """
    ctx = a.get("context") or {}
    grid = ctx.get("grid") or []
    if not grid:
        return ""
    hits = {}
    for v in a["videos"]:
        if v.get("hit") and v.get("grid_slot"):
            hits.setdefault(tuple(v["grid_slot"]), []).append(v)
    cells = ['<div></div>'] + [f'<div class="h">{h:02d}</div>' for h in range(24)]
    for d in range(1, 8):
        cells.append(f'<div class="d">{DAYS_SHORT[d - 1]}</div>')
        for dd, h, val, pct in (c for c in grid if c[0] == d):
            q = min(7, 1 + pct * 7 // 101)
            hv = hits.get((dd, h), [])
            mark = " mark" if hv else ""
            extra = (" · хит: " + ", ".join(fmt(x["views"]) + " просм." for x in hv)) if hv else ""
            cells.append(
                f'<div class="c{mark}" tabindex="0" style="background:var(--q{q})" '
                f'data-head="{esc(DAYS_SHORT[d - 1])} {h:02d}:00" '
                f'data-meta="{esc(f"оценка {val} · {pct}-й перцентиль{extra}")}"></div>')
    start, stop = ctx.get("grid_period") or ("?", "?")
    ramp = "".join(f'<i style="background:var(--q{i})"></i>' for i in range(1, 8))
    rows = "".join(f"<tr><td>{DAYS_SHORT[d - 1]} {h:02d}:00</td><td class='n'>{val}</td>"
                   f"<td class='n'>{pct}</td></tr>" for d, h, val, pct in grid)
    return (f'<h2>Когда активна аудитория</h2><p class="note">Оценка Metricool — модель '
            f'источника, не наблюдение: неделя {esc(start)}…{esc(stop)}, пояс '
            f'{esc(ctx.get("grid_tz_name") or "?")}. Ролики вышли раньше этой недели.</p>'
            f'<div class="card"><div class="legend">меньше<span class="ramp">{ramp}</span>больше'
            f'<span><span class="dot"></span>здесь вышел хит</span></div>'
            f'<div class="scroll"><div class="heat">{"".join(cells)}</div></div>'
            f'<details><summary>Таблица оценки</summary><div class="scroll"><table><thead><tr>'
            f'<th>Слот</th><th>Оценка</th><th>Перцентиль</th></tr></thead><tbody>{rows}'
            f'</tbody></table></div></details></div>')


def meters(h):
    hits, rest, total = h["hits_matching"], h["rest_matching"], h["rest_total"]
    def m(label, k, n):
        pct = 100.0 * k / n if n else 0
        return (f'<div class="meter"><span>{esc(label)}</span><div class="t">'
                f'<div class="f" style="width:{pct:.0f}%"></div></div>'
                f'<span>{k} из {n}</span></div>')
    return m("хиты", hits, hits) + m("остальные зрелые", rest, total)


def tz_badge(h):
    if h.get("tz_robust") is True:
        return f'<p class="tz-ok note">держится и в другом поясе — {esc(h["local_view"])}</p>'
    if h.get("tz_robust") is False:
        return f'<p class="tz-warn">⚠ {esc(h["tz_note"])}</p>'
    return ""


def growth(a):
    g = [x for x in a.get("growth", [])][:8]
    if not g:
        return ""
    rows = []
    for x in g:
        if x["new"]:
            what = f"новый: {fmt(x['views'])} за {x['age_days']} дн."
        else:
            what = f"+{fmt(x['delta'])} за {x['days']} дн. ({A._num(x['per_day'])}/дн.)"
        rows.append(f'<tr><td>…{esc(x["video_id"][-6:])} <span class="note">'
                    f'{esc((x["caption"] or "")[:50])}</span></td><td class="n">{esc(what)}</td></tr>')
    return ('<h2>Кто продолжает расти</h2><p class="note">От последнего сверенного значения к '
            'свежему. Один источник — не сверено.</p><div class="card scroll"><table><thead><tr>'
            '<th>Ролик</th><th>Прирост</th></tr></thead><tbody>' + "".join(rows)
            + '</tbody></table></div>')


def build(a=None, states=None, ideas_file=None, ideas=None):
    a = A.analyze() if a is None else a
    states = E.load() if states is None else states
    if ideas is None:
        ideas_file, ideas = E.latest_ideas()
    hits = [v for v in a["videos"] if v.get("hit")]
    rest = [v for v in a["videos"] if not v.get("hit")]
    fresh_at = max((u["observed_at"] for u in a["unverified"]), default=None)
    open_exps = [s for s in states.values() if s["status"] in E.OPEN]

    P = ["<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">",
         "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
         "<title>AI Manager · дашборд</title>", f"<style>{CSS}</style></head>",
         "<body><div class=\"viz-root\">",
         "<h1>AI Manager · @gulyashik52</h1>",
         f"<p class=\"sub\">Сверенные данные на {esc((a['observed_at'] or '')[:10])}"
         + (f" · свежие несверенные на {esc(fresh_at[:10])}" if fresh_at else "") + "</p>",
         "<p class=\"note\">FACT — только по сверенным данным. Всё помеченное "
         "HYPOTHESIS проверяется новыми публикациями, а не этим экраном.</p>"]
    if a["unverified"]:
        P.append("<div class=\"warn\">Свежие числа пришли из одного источника: второй "
                 "недоступен, сверки нет. Они показаны отдельно и в выводы не входят.</div>")

    kpi = [("Роликов в данных", fmt(a["n_videos"] + sum(1 for u in a["unverified"]
                                                          if u["verified_views"] is None)),
            f"сверено {a['n_videos']}"),
           ("Доля хитов в просмотрах", f"{fmt(a.get('hits_share_pct'))}%",
            f"{a.get('n_hits', 0)} ролика, n={a['n_videos']}, сверено"),
           ("Медиана зрелых", fmt(a.get("median_views_mature")),
            f"n={a['n_mature']}; хит — от {fmt(a.get('hit_threshold'))}"),
           ("Гипотез", fmt(len(a["hypotheses"])),
            f"для FACT нужно {a['min_sample_required']} зрелых, есть {a['n_mature']}"),
           ("Экспериментов открыто", fmt(len(open_exps)), f"всего в журнале {len(states)}")]
    P.append("<div class=\"kpis\">" + "".join(
        f'<div class="card kpi"><div class="label">{esc(l)}</div>'
        f'<div class="value">{esc(v)}</div><div class="ctx">{esc(c)}</div></div>'
        for l, v, c in kpi) + "</div>")

    P.append("<h2>Просмотры по роликам</h2>")
    P.append(bars(a["videos"], "Все сверенные ролики",
                  f"Два хита — {fmt(a.get('hits_share_pct'))}% всех просмотров: "
                  "остальные на этой шкале почти не видны."))
    P.append("<div style=\"height:12px\"></div>")
    P.append(bars(rest, "Без хитов",
                  "Та же величина без двух выбросов — шкала по самому большому из остальных."))

    P.append("<h2>Гипотезы</h2><p class=\"note\">Признак становится гипотезой, только "
             "если он есть у всех хитов и меньше чем у половины остальных зрелых роликов.</p>")
    if a["hypotheses"]:
        P.append("<div class=\"hyps\">" + "".join(
            f'<div class="card"><span class="tag">HYPOTHESIS · {esc(h["id"])} · n={h["n_sample"]}'
            f'</span><p style="margin:8px 0 0"><b>{esc(h["label"])}: {esc(h["value"])}</b></p>'
            f'{meters(h)}{tz_badge(h)}<p class="note" style="margin-top:10px">Причинность не '
            f'установлена. {esc(h["competing_explanation"])}</p></div>'
            for h in a["hypotheses"]) + "</div>")
    else:
        P.append("<p>Ни один признак не отделяет хиты от остальных.</p>")
    if a["not_distinguishing"]:
        P.append("<p class=\"note\" style=\"margin-top:14px\">Не отличает хиты:</p><ul>"
                 + "".join(f"<li>{esc(x['statement'])}</li>" for x in a["not_distinguishing"])
                 + "</ul>")

    P.append(heatmap(a))
    P.append(growth(a))
    if a["unverified"]:
        P.append("<h2>Свежее, не сверено</h2><p class=\"note\">Один источник, второй "
                 "недоступен. FACT на этих числах не строится.</p><div class=\"card scroll\">"
                 "<table><thead><tr><th>Ролик</th><th>Сейчас</th><th>Было сверено</th>"
                 "<th>Возраст</th></tr></thead><tbody>")
        for u in a["unverified"]:
            was = (f"{fmt(u['verified_views'])} на {u['verified_at'][:10]}"
                   if u["verified_views"] is not None else "новый, сверки не было")
            P.append(f'<tr><td><a href="{esc(u["url"])}">…{esc(u["video_id"][-6:])}</a> '
                     f'<span class="note">{esc((u["caption"] or "")[:60])}</span></td>'
                     f'<td class="n">{esc(fmt(u["views"]))}</td><td class="n">{esc(was)}</td>'
                     f'<td class="n">{u["age_days"]} дн.</td></tr>')
        P.append("</tbody></table></div>")

    P.append("<h2>Эксперименты</h2><div class=\"card scroll\"><table><thead><tr><th>Код</th>"
             "<th>Статус</th><th>Гипотеза</th><th>Где сейчас</th></tr></thead><tbody>")
    for code in sorted(states):
        st = states[code]
        ev = E.evaluate(st, a)
        where = (ev.get("note") if st["status"] == "blocked"
                 else f"{st.get('verdict')}: {st.get('result')}" if st["status"] == "concluded"
                 else ev.get("summary"))
        P.append(f"<tr><td>{esc(code)}</td><td>{esc(st['status'])}</td>"
                 f"<td>{esc((st.get('hypothesis') or '')[:120])}</td><td>{esc(where)}</td></tr>")
    P.append("</tbody></table></div>")

    P.append("<h2>Последние идеи</h2>")
    if ideas:
        P.append(f"<p class=\"note\">{esc(ideas_file.name if ideas_file else '')} · "
                 "ставки, а не обещания; взять в работу — пункт 5 меню</p><div class=\"card\"><ol>")
        for i in ideas:
            P.append(f"<li><b>{esc(i.get('title'))}</b> <span class=\"tag\">проверяет "
                     f"{esc(i.get('tests_hypothesis'))}</span><br>{esc(i.get('what_to_film'))}"
                     f"<br><span class=\"note\">{esc(i.get('when_to_post'))} · "
                     f"{esc(i.get('success_check'))}</span></li>")
        P.append("</ol></div>")
    else:
        P.append("<p class=\"note\">Идей ещё нет — пункт 4 меню.</p>")

    P.append("<details><summary>Таблица всех сверенных роликов</summary><div class=\"card scroll\">"
             "<table><thead><tr><th>Дата</th><th>Ролик</th><th>Просмотры</th><th>Длит.</th>"
             "<th>День, час UTC</th><th>Статус</th></tr></thead><tbody>")
    for v in a["videos"]:
        kind = "хит" if v.get("hit") else ("зрелый" if v["mature"] else "молодой")
        P.append(f'<tr><td>{esc(v["published_at"][:10])}</td><td><a href="{esc(v["url"])}">'
                 f'…{esc(v["video_id"][-6:])}</a></td><td class="n">{esc(fmt(v["views"]))}</td>'
                 f'<td class="n">{esc(A._num(v["duration_sec"]))} с</td>'
                 f'<td>{esc(v["weekday"])} {v["hour_utc"]:02d}:00</td><td>{kind}</td></tr>')
    P.append("</tbody></table></div></details>")
    P.append(f"<p class=\"note\" style=\"margin-top:24px\">Собрано из data/ · политика "
             f"{esc(a['policy_version'])} · часовой пояс аудитории не подтверждён, время в UTC</p>")
    P.append(f"<div id=\"tip\" role=\"tooltip\"></div><script>{JS}</script></div></body></html>")
    return "\n".join(P)


def save(text, out=OUT):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Дашборд аккаунта.")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)
    path = save(build(), args.out)
    print(f"дашборд: {path}")
    if not args.no_open and not os.environ.get("TIKTOK_NO_BROWSER"):
        webbrowser.open(path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
