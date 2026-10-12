#!/usr/bin/env python3
"""«Обновить программу из GitHub» на настоящих git-репозиториях.

Во временном каталоге: голый origin («GitHub»), клон «сессия» — туда
приходят новые версии, клон «ПК» — там меню. Проект лежит в подкаталоге,
как в настоящем репозитории. Сеть не нужна, настоящий репозиторий не
трогается. Глобальные настройки git отключены: на ПК владельца имя и
почта могут быть не заданы, и обновление обязано работать и так.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import importlib.util                                      # noqa: E402
spec = importlib.util.spec_from_file_location("update", ROOT / "scripts" / "update.py")
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)

RESULTS = []
SUB = "proj"
ID = ["-c", "user.name=Session", "-c", "user.email=s@example.invalid"]


def empty_gitconfig():
    """Путь к пустому временному файлу настроек git (дескриптор закрыт)."""
    fd, path = tempfile.mkstemp(suffix=".gitconfig")
    os.close(fd)
    return path


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def g(cwd, *args):
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git {args}: {p.stdout}{p.stderr}")
    return p.stdout.strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def setup():
    base = Path(tempfile.mkdtemp(prefix="upd_"))
    origin = base / "origin.git"
    g(base, "init", "-q", "--bare", "-b", "main", str(origin))
    sess = base / "session"
    g(base, "clone", "-q", str(origin), str(sess))
    p = sess / SUB
    write(p / ".gitattributes", (ROOT / ".gitattributes").read_text(encoding="utf-8"))
    write(p / "experiments" / "register.jsonl", '{"id": "EXP-001"}\n{"id": "EXP-002"}\n')
    write(p / "data" / "ideas" / ".gitkeep", "")
    write(p / "data" / "analytics" / "manifest.json", '{"v": 1}\n')
    write(p / "AI-Manager.bat", "@echo off\r\n")
    g(sess, "add", "-A")
    g(sess, *ID, "commit", "-q", "-m", "init")
    g(sess, "push", "-q", "-u", "origin", "main")
    pc = base / "pc"
    g(base, "clone", "-q", str(origin), str(pc))
    return base, sess, pc


def session_commit(sess, files, msg):
    for rel, text in files.items():
        write(sess / SUB / rel, text)
    g(sess, "add", "-A")
    g(sess, *ID, "commit", "-q", "-m", msg)
    g(sess, "push", "-q")


def run(fn, **kw):
    out = []
    rc = fn(say=out.append, **kw)
    return rc, "\n".join(out)


def main():
    saved_env = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    # пустой файл, а не os.devnull: «nul» на Windows git может не принять
    os.environ["GIT_CONFIG_GLOBAL"] = empty_gitconfig()
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    real_head = g(ROOT, "rev-parse", "HEAD")
    try:
        body()
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("Z1 настоящий репозиторий не тронут", g(ROOT, "rev-parse", "HEAD") == real_head)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


def body():
    print("=== A. правило слияния журнала ===")
    check("A1 в проекте журнал экспериментов сливается по union",
          g(ROOT, "check-attr", "merge", "--", "experiments/register.jsonl")
          .endswith("merge: union"))

    print("\n=== B. обычное обновление с локальными изменениями ===")
    base, sess, pc = setup()
    proj = pc / SUB
    session_commit(sess, {"experiments/register.jsonl":
                          '{"id": "EXP-001"}\n{"id": "EXP-002"}\n{"event": "concluded"}\n',
                          "data/analytics/manifest.json": '{"v": 2}\n',
                          "new_feature.py": "print(1)\n"}, "новая версия")
    write(proj / "experiments" / "register.jsonl",
          '{"id": "EXP-001"}\n{"id": "EXP-002"}\n{"event": "registered", "code": "EXP-005"}\n')
    write(proj / "data" / "ideas" / "20261009T000000_session.json", '{"ideas": []}\n')
    write(proj / "data" / "analytics" / "manifest.json", '{"v": "local"}\n')
    rc, out = run(U.update, root=proj, sync=False)
    check("B1 обновление прошло", rc == 0, out.splitlines()[-1] if out else "")
    reg = (proj / "experiments" / "register.jsonl").read_text(encoding="utf-8")
    check("B2 журнал: строки ПК и GitHub обе на месте, без маркеров конфликта",
          "EXP-005" in reg and '"concluded"' in reg and "<<<<" not in reg
          and reg.count("EXP-001") == 1)
    check("B3 свой коммит с журналом и идеями — в истории",
          "Мои эксперименты и идеи (с ПК)" in g(pc, "log", "--format=%s")
          and g(pc, "ls-files", f"{SUB}/data/ideas/20261009T000000_session.json"))
    check("B4 коммит сделан без настроенного имени — запасным",
          U.FALLBACK_NAME in g(pc, "log", "--format=%an"))
    check("B5 пересчитанный на ПК файл — эталон из GitHub",
          (proj / "data" / "analytics" / "manifest.json").read_text() == '{"v": 2}\n')
    stash = g(pc, "stash", "list")
    check("B6 локальная копия не потеряна — в stash с подписью",
          "AI Manager: локальные изменения" in stash
          and '"local"' in g(pc, "stash", "show", "-p", "stash@{0}"))
    check("B7 новая версия получена", (proj / "new_feature.py").exists()
          and "получено коммитов" in out)
    check("B8 рабочая копия чистая", g(pc, "status", "--porcelain") == "")
    check("B9 обновились файлы запуска — не было, совета перезапустить .bat нет",
          "AI-Manager.bat заново" not in out)

    print("\n=== C. повторный запуск и файлы запуска ===")
    rc, out = run(U.update, root=proj, sync=False)
    check("C1 повторно — «обновлений нет»", rc == 0 and "обновлений нет" in out)
    session_commit(sess, {"AI-Manager.bat": "@echo off\r\nREM v2\r\n"}, "bat")
    rc, out = run(U.update, root=proj, sync=False)
    check("C2 обновился .bat — просьба запустить AI-Manager.bat заново",
          rc == 0 and "AI-Manager.bat заново" in out)

    print("\n=== D. отправка в GitHub ===")
    write(proj / "experiments" / "register.jsonl",
          (proj / "experiments" / "register.jsonl").read_text(encoding="utf-8")
          + '{"event": "linked", "code": "EXP-005"}\n')
    rc, out = run(U.push, root=proj)
    g(sess, "pull", "-q", "--no-rebase")
    check("D1 своё ушло в GitHub", rc == 0 and '"linked"' in
          (sess / SUB / "experiments" / "register.jsonl").read_text(encoding="utf-8"), out)
    rc, out = run(U.push, root=proj)
    check("D2 повторно — «отправлять нечего»", rc == 0 and "нечего" in out)
    session_commit(sess, {"x.txt": "1\n"}, "опередил")
    write(proj / "data" / "ideas" / "b.json", "{}\n")
    rc, out = run(U.push, root=proj)
    check("D3 GitHub ушёл вперёд — отказ с подсказкой сначала обновить",
          rc == 1 and "Обновить программу" in out, out[-160:])

    print("\n=== E. конфликт — всё как было ===")
    rc, out = run(U.update, root=proj, sync=False)        # догоняем
    session_commit(sess, {"data/ideas/same.json": '{"from": "github"}\n'}, "идея с сервера")
    write(proj / "data" / "ideas" / "same.json", '{"from": "pc"}\n')
    rc, out = run(U.update, root=proj, sync=False)
    merge_head = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                                cwd=str(pc), capture_output=True).returncode
    check("E1 конфликт — отказ, слияние отменено",
          rc == 1 and "отменено" in out and merge_head != 0, out[-200:])
    check("E2 свой файл цел и закоммичен",
          (proj / "data" / "ideas" / "same.json").read_text() == '{"from": "pc"}\n'
          and g(pc, "status", "--porcelain") == "")

    print("\n=== F. отказы без последствий ===")
    g(pc, "switch", "-q", "-c", "local-only")
    rc, out = run(U.update, root=proj, sync=False)
    check("F1 ветка без upstream — понятный отказ", rc == 1 and "upstream" in out)
    g(pc, "switch", "-q", "--detach")
    rc, out = run(U.update, root=proj, sync=False)
    check("F2 detached HEAD — понятный отказ", rc == 1 and "detached" in out)
    saved = os.environ.get("PATH", "")
    os.environ["PATH"] = str(Path(tempfile.mkdtemp()))
    try:
        rc, out = run(U.update, root=proj, sync=False)
    finally:
        os.environ["PATH"] = saved
    check("F3 нет git — подсказка установить Git for Windows",
          rc == 1 and "Git for Windows" in out)

    print("\n=== G. меню ===")
    import runpy
    ns = runpy.run_path(str(ROOT / "scripts" / "menu.py"))
    keys = {fn.__name__: k for k, _, fn in ns["ITEMS"] if fn}
    check("G1 в меню есть «Обновить программу» и «Отправить»",
          {"update", "push"} <= set(keys))
    p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                       input=f"{keys['push']}\nнет\n\n0\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    check("G2 отправка без согласия не выполняется", "отменено" in p.stdout)
    src = (ROOT / "scripts" / "update.py").read_text(encoding="utf-8")
    bad = [w for w in ('"--force"', '"-f"', '"reset"', '"rebase"', '"clean"', '"--hard"',
                       '"-D"', '"checkout"') if w in src]
    check("G3 нет переписывания истории и удаления", not bad, str(bad))
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
