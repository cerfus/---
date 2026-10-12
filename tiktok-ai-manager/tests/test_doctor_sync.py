#!/usr/bin/env python3
"""Готовность программы и обновление базы на ПК."""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import doctor as D        # noqa: E402
import sync_db as S       # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def main():
    print("=== A. проверка готовности ===")
    res = D.checks()
    check("A1 в рабочем окружении нет блокирующих проблем",
          not [r for r in res if r[0] == D.FAIL], str([r for r in res if r[0] == D.FAIL]))
    check("A2 у каждого предупреждения есть что сделать",
          all(fix for st, _, fix in res if st in (D.WARN, D.FAIL)))
    check("A3 отставание базы: меньше роликов", D.db_lags(16, "2026-09-17", 17, "2026-10-08"))
    check("A4 отставание базы: старее замеры", D.db_lags(17, "2026-09-17", 17, "2026-10-08"))
    check("A5 актуальная база не помечается отстающей",
          not D.db_lags(17, "2026-10-08T10:00", 17, "2026-10-08T21:00"))

    saved = os.environ.get("TIKTOK_DSN_RO")
    secret = "Pa55wordNotToLeak"
    os.environ["TIKTOK_DSN_RO"] = f"postgresql://tiktok_ro:{secret}@10.255.255.1:5432/x"
    t = time.time()
    try:
        res = D.checks()
    finally:
        if saved is None:
            os.environ.pop("TIKTOK_DSN_RO", None)
        else:
            os.environ["TIKTOK_DSN_RO"] = saved
    spent = time.time() - t
    text = D.render(res)
    check("A6 недоступная база — предупреждение, а не падение",
          any("база не отвечает" in w for _, w, _ in res), text[-200:])
    check("A7 проверка не зависает на недоступной базе",
          spent < D.CONNECT_TIMEOUT_SEC + 10, f"{spent:.1f} с")
    check("A8 пароль из строки подключения не печатается", secret not in text)

    print("\n=== B. обновление базы повторяет цепочку пересборки ===")
    sh = (ROOT / "scripts" / "rebuild_from_jsonl.sh").read_text(encoding="utf-8")
    chain = [l.split("python3", 1)[1].split("|")[0].strip()
             for l in sh.splitlines() if l.strip().startswith("python3 ")]
    mine = [" ".join(a for a in args if a != "--adopt") for _, args in S.STEPS]
    check("B1 шаги и порядок — как в rebuild_from_jsonl.sh (без DROP DATABASE)",
          chain == mine, f"{chain} vs {mine}")
    check("B2 удаления в цепочке нет",
          not any(re.search(r"drop|delete|truncate", " ".join(a).lower()) for _, a in S.STEPS))

    hang = Path(tempfile.mkdtemp()) / "hang.py"
    hang.write_text("import time\nprint('шаг')\ntime.sleep(600)\n", encoding="utf-8")
    t = time.time()
    rc, out = S.run_step([str(hang)], timeout=2)
    check("B3 зависший шаг снимается по таймауту", rc == 124 and time.time() - t < 30,
          f"код {rc}")
    check("B4 вывод до зависания сохраняется", "шаг" in out)

    print("\n=== C. запуск двойным щелчком ===")
    b = (ROOT / "AI-Manager.bat").read_bytes()
    try:
        txt = b.decode("ascii")
        ascii_ok = True
    except UnicodeDecodeError:
        txt, ascii_ok = "", False
    cmds = "\n".join(l for l in txt.splitlines() if not l.strip().lower().startswith("rem"))
    check("C1 AI-Manager.bat: ASCII, CRLF, без BOM",
          ascii_ok and b.count(b"\r\n") > 0 and b.count(b"\n") == b.count(b"\r\n")
          and not b.startswith(b"\xef\xbb\xbf"))
    check("C2 вызывает меню и переходит в папку проекта",
          "scripts\\menu.bat" in cmds and '%~dp0' in cmds)
    call = [l for l in cmds.splitlines() if "scripts\\menu.bat" in l]
    check("C2a после меню выход — на той же строке, что вызов "
          "(файл может обновиться, пока меню открыто)",
          len(call) == 1 and "exit /b" in call[0], str(call))
    check("C3 без скобочных блоков IF",
          not [l for l in cmds.splitlines() if l.rstrip().endswith("(")])
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    check("C4 requirements.txt называет драйвер базы и часовые пояса",
          "psycopg" in req and "tzdata" in req)

    print("\n=== D. удобство: свежесть данных, новая версия, установка ===")
    from datetime import date
    check("D1 данные моложе недели — OK, с датой и возрастом",
          D.freshness("2026-10-08T21:08:21+00:00", date(2026, 10, 12))
          == (D.OK, "данные от 2026-10-08 (4 дн. назад)", ""))
    st, what, fix = D.freshness("2026-10-08T21:08:21+00:00", date(2026, 10, 20))
    check("D2 данные старше 8 дней — предупреждение и что сделать с Routine",
          st == D.WARN and "12 дн." in what and "Metricool" in fix and "пункт 10" in fix)
    res_old = D.checks(today=date(2027, 1, 1))
    check("D3 в общей проверке устаревшие данные видны как предупреждение",
          any(r[0] == D.WARN and "не приходит" in r[1] for r in res_old))
    check("D4 видео ждут, инструментов нет — предупреждение с командой установки",
          D.video_check(2, False)[0] == D.WARN
          and "requirements-extract" in D.video_check(2, False)[2]
          and D.video_check(0, False)[0] == D.INFO and D.video_check(0, True)[0] == D.OK)

    base = Path(tempfile.mkdtemp())
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}

    def g(cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=str(cwd), check=True, capture_output=True, env=env)
    g(base, "init", "-q", "--bare", "-b", "main", "origin.git")
    g(base, "clone", "-q", "origin.git", "a")
    (base / "a" / "f.txt").write_text("1\n", encoding="utf-8")
    g(base / "a", "add", "-A")
    g(base / "a", "commit", "-q", "-m", "1")
    g(base / "a", "push", "-q", "-u", "origin", "main")
    g(base, "clone", "-q", "origin.git", "pc")
    n0 = D.updates_available(base / "pc")
    (base / "a" / "f.txt").write_text("2\n", encoding="utf-8")
    g(base / "a", "commit", "-q", "-am", "2")
    g(base / "a", "push", "-q")
    n1 = D.updates_available(base / "pc")
    check("D5 новая версия в GitHub считается: 0, затем 1 новый коммит", (n0, n1) == (0, 1),
          str((n0, n1)))
    g(base / "pc", "switch", "-q", "-c", "no-upstream")
    check("D6 нет upstream — «проверить нельзя», а не ошибка",
          D.updates_available(base / "pc") is None)
    check("D7 тесты не ходят в сеть: общая проверка по умолчанию без fetch",
          not any("новая версия" in r[1] or "версия программы" in r[1] for r in D.checks()))

    saved = {k: os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID")}
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:SECRET-TOKEN-VALUE"
    os.environ["TELEGRAM_OWNER_ID"] = "42"
    try:
        out = D.render(D.checks())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("D8 Telegram настроен — сказано, а токен не напечатан",
          "Telegram-бот настроен" in out and "SECRET-TOKEN" not in out)

    import runpy
    ns = runpy.run_path(str(ROOT / "scripts" / "menu.py"))
    calls = []
    ran = ns["offer_install"](lambda _p: "нет", missing=["tzdata"], sub=lambda *a: calls.append(a))
    check("D9 без согласия пакеты не ставятся", ran is False and calls == [])
    ran = ns["offer_install"](lambda _p: "y", missing=["tzdata"], sub=lambda *a: calls.append(a))
    check("D10 с согласием — ровно python -m pip install -r requirements.txt",
          ran is True and calls == [tuple(D.INSTALL_ARGS)]
          and D.INSTALL_ARGS == ["-m", "pip", "install", "-r", "requirements.txt"])
    ran = ns["offer_install"](lambda _p: "y", missing=[], sub=lambda *a: calls.append(a))
    check("D11 всё на месте — ничего не спрашивается и не ставится", ran is False
          and len(calls) == 1)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
