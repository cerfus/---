#!/usr/bin/env python3
"""Готовность программы и обновление базы на ПК."""
import os
import re
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

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
