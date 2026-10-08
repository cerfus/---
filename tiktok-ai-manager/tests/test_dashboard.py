#!/usr/bin/env python3
"""Дашборд: показывает ровно то, что в разборе, и не исполняет чужой текст.

Браузер тест не открывает: TIKTOK_NO_BROWSER, и отдельная проверка, что
webbrowser.open действительно не вызывался.
"""
import os
import re
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import analysis as A, dashboard as D      # noqa: E402
from insights.validator import find_violations         # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def text_of(html):
    html = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def main():
    print("=== A. содержимое совпадает с разбором ===")
    a = A.analyze()
    page = D.build(a)
    check("A1 доля хитов на странице — из разбора",
          D.fmt(a["hits_share_pct"]) + "%" in page, D.fmt(a["hits_share_pct"]))
    check("A2 медиана зрелых — из разбора", D.fmt(a["median_views_mature"]) in page)
    check("A3 каждый сверенный ролик есть в таблице",
          all(v["video_id"][-6:] in page for v in a["videos"]))
    charts = page.split('<div class="bars">')[1:]
    n_rows = [c.split("</div></div></div>")[0].count('class="row ') for c in charts]
    check("A4 два графика: со всеми роликами и без хитов",
          n_rows == [len(a["videos"]), len(a["videos"]) - a["n_hits"]], str(n_rows))
    check("A5 каждая гипотеза на месте с меткой и n",
          all(f"HYPOTHESIS · {h['id']} · n={h['n_sample']}" in page for h in a["hypotheses"]))
    check("A6 несверенное вынесено отдельно и предупреждено",
          ("Свежее, не сверено" in page and "сверки нет" in page) == bool(a["unverified"]))

    heat = page.split('<div class="heat">')[1].split("</div></div>")[0] if '<div class="heat">' in page else ""
    check("A7 тепловая карта активности: 7 × 24 = 168 ячеек",
          heat.count('class="c') == 168, str(heat.count('class="c')))
    check("A8 на карте отмечены слоты хитов",
          heat.count('class="c mark"') == a["n_hits"], str(heat.count('class="c mark"')))
    check("A9 карта подписана как оценка источника, с таблицей",
          "модель источника, не наблюдение" in page and "Таблица оценки" in page)
    check("A10 зависимость гипотезы от пояса видна на карточке",
          "зависит от часового пояса" in page)
    check("A11 раздел роста помечен «не сверено»",
          "Кто продолжает расти" in page and "не сверено" in page)

    print("\n=== B. безопасность и автономность ===")
    evil = dict(a, videos=[dict(a["videos"][0],
                                caption='<script>alert(1)</script><img src=x onerror=alert(2)>')]
                + a["videos"][1:])
    p2 = D.build(evil)
    check("B1 подпись ролика экранируется, а не исполняется",
          "<script>alert(1)" not in p2 and "&lt;script&gt;alert(1)" in p2
          and "<img src=x" not in p2)
    check("B2 на странице ровно один свой <script>", p2.count("<script") == 1)
    check("B3 подсказка пишет текст через textContent, не innerHTML",
          "textContent" in D.JS and "innerHTML" not in D.JS)
    check("B4 ни внешних скриптов, ни внешних стилей",
          not re.search(r"<script[^>]+src=|<link[^>]+stylesheet", page))
    check("B5 тёмная тема задана в обеих областях (ОС и переключатель)",
          "prefers-color-scheme:dark" in page and ':root[data-theme="dark"]' in page)
    check("B6 сохраняется в data/runtime, который не в git",
          D.OUT.parent == ROOT / "data" / "runtime"
          and "data/runtime/" in (ROOT / ".gitignore").read_text(encoding="utf-8"))

    print("\n=== C. формулировки самого дашборда ===")
    bare = D.build(a, states={}, ideas_file=None, ideas=[])
    v = find_violations(text_of(bare).replace("Причинность не установлена", ""),
                        "RECOMMENDATION")
    check("C1 текст страницы без причинных и оценочных конструкций", not v, str(v))

    print("\n=== D. запуск ===")
    calls = []
    saved = webbrowser.open
    webbrowser.open = lambda *a_, **k: calls.append(a_)
    os.environ["TIKTOK_NO_BROWSER"] = "1"
    out = Path(tempfile.mkdtemp()) / "d.html"
    try:
        rc = D.main(["--out", str(out)])
    finally:
        webbrowser.open = saved
        os.environ.pop("TIKTOK_NO_BROWSER", None)
    check("D1 собирается в указанный путь", rc == 0 and out.exists() and out.stat().st_size > 5000)
    check("D2 при TIKTOK_NO_BROWSER браузер не открывается", not calls)
    import runpy
    key = next(k for k, _, fn in runpy.run_path(str(ROOT / "scripts" / "menu.py"))["ITEMS"]
               if fn and fn.__name__ == "dashboard")
    p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                       input=f"{key}\n\n0\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "TIKTOK_NO_BROWSER": "1"})
    check("D3 пункт меню «Дашборд» собирает дашборд", p.returncode == 0 and "дашборд:" in p.stdout)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
