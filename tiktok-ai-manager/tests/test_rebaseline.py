#!/usr/bin/env python3
"""Пересчёт эталонов tests/golden.py: считает то же, что проверяют тесты,
не даёт пересчитать при изменённом коде и не теряет прежние значения.

Настоящий tests/golden.py тест только читает: запись идёт во временную копию.
"""
import copy
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("rb", ROOT / "scripts" / "rebaseline_golden.py")
RB = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RB)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def g(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout


def quiet(fn, *a, **k):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **k)
    return rc, buf.getvalue()


def main():
    real = sha(RB.GOLDEN)
    old = RB.current()

    print("=== A. считает то же, что проверяют тесты ===")
    new = RB.compute()
    check("A1 на текущем сырье пересчёт совпадает с эталоном до последнего знака",
          RB.diff(old, new) == [], str(RB.diff(old, new)))

    print("\n=== B. запись ===")
    fake = copy.deepcopy(new)
    fake.update(N_VIDEOS=18, INSIGHTS_HASH="b" * 64, N_INSIGHTS=15, FEATURE_ROWS=810,
                TIER0_ROWS=540, TIER05_ROWS=270,
                RAW_SET=new["RAW_SET"] + ["2026-10-15T090000Z_metricool_posts_r5.json"],
                FEATURE_STATUSES={"derived": 200, "insufficient_baseline": 54,
                                  "observed": 370, "unavailable": 186})
    fake["UPSTREAM"] = dict(new["UPSTREAM"], analytics="a" * 64)
    src = RB.GOLDEN.read_text(encoding="utf-8")
    text = RB.rewrite(src, old, fake, "R5: проверка")
    tmp = Path(tempfile.mkdtemp()) / "golden.py"
    tmp.write_text(text, encoding="utf-8", newline="\n")
    back = RB.current(tmp)
    check("B1 записанное читается обратно теми же значениями",
          [k for k in fake if back[k] != fake[k]] == [])
    check("B2 HISTORY вырос на одну запись", back["_history_len"] == old["_history_len"] + 1)
    ns = {"__file__": str(tmp)}
    exec(compile(text, str(tmp), "exec"), ns)
    last = ns["HISTORY"][-1]
    prev = last.get("previous") or {}
    check("B3 в HISTORY — прежние значения, новое сырьё и причина",
          prev.get("n_videos") == old["N_VIDEOS"]
          and prev.get("insights_hash") == old["INSIGHTS_HASH"]
          and prev.get("upstream") == old["UPSTREAM"]
          and "r5" in last.get("raw", "") and last.get("why") == "R5: проверка")
    head, tail = src.split("RAW_SET = frozenset", 1)[0], src.split("\n]\n", 1)[1]
    check("B4 шапка с процедурой и функции файла не тронуты",
          text.startswith(head) and text.endswith(tail))
    broken = src.replace("N_VIDEOS = ", "N_VIDEOS_X = ")
    try:
        RB.rewrite(broken, old, fake, "x")
        refused = False
    except ValueError:
        refused = True
    check("B5 не нашлась конструкция — отказ, а не молчаливая порча", refused)

    print("\n=== C. только данные ===")
    check("C1 сырьё, производные данные, отчёты и сам эталон — разрешены",
          RB.code_changes(["data/raw/x.json", "data/videos.jsonl", "reports/daily/a.md",
                           "tests/golden.py"]) == [])
    check("C2 код и тесты — запрещены",
          RB.code_changes(["advisor/plan.py", "tests/test_plan.py", "data/x.json"])
          == ["advisor/plan.py", "tests/test_plan.py"])
    base = Path(tempfile.mkdtemp())
    env_saved = os.environ.get("GIT_CONFIG_GLOBAL")
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        g(base, "init", "-q")
        for rel in ("proj/data/a.json", "proj/advisor/b.py"):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text("1\n", encoding="utf-8")
        g(base, "add", "-A")
        g(base, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i")
        (base / "proj/data/a.json").write_text("2\n", encoding="utf-8")
        (base / "proj/advisor/b.py").write_text("2\n", encoding="utf-8")
        (base / "proj/data/raw").mkdir()
        (base / "proj/data/raw/new.json").write_text("{}", encoding="utf-8")
        (base / "outside.txt").write_text("x", encoding="utf-8")
        got = sorted(RB.changed_paths(base / "proj"))
    finally:
        if env_saved is None:
            os.environ.pop("GIT_CONFIG_GLOBAL", None)
        else:
            os.environ["GIT_CONFIG_GLOBAL"] = env_saved
    check("C3 изменения видны от каталога проекта, включая новые файлы; вне проекта — нет",
          got == ["advisor/b.py", "data/a.json", "data/raw/new.json"], str(got))

    print("\n=== D. команда ===")
    saved = (RB.changed_paths, RB.compute, RB.GOLDEN)
    try:
        RB.changed_paths = lambda root=None: ["advisor/x.py", "data/y.json"]
        rc, out = quiet(RB.main, ["--why", "R5"])
        check("D1 код изменён — отказ (2), эталон не тронут",
              rc == 2 and "ОТКАЗ" in out and "advisor/x.py" in out and sha(RB.GOLDEN) == real)
        RB.changed_paths = lambda root=None: ["data/y.json"]
        rc, out = quiet(RB.main, [])
        check("D2 данные те же — «эталон актуален», без записи",
              rc == 0 and "актуален" in out and sha(RB.GOLDEN) == real)
        RB.compute = lambda root=None: fake
        rc, out = quiet(RB.main, ["--check"])
        check("D3 --check показывает сдвиг и не пишет",
              rc == 0 and "N_VIDEOS: 17 → 18" in out and sha(RB.GOLDEN) == real, out[:80])
        rc, out = quiet(RB.main, [])
        check("D4 без причины не пишет", rc == 2 and "--why" in out and sha(RB.GOLDEN) == real)
        copy_ = Path(tempfile.mkdtemp()) / "golden.py"
        copy_.write_bytes(RB.GOLDEN.read_bytes())
        RB.GOLDEN = copy_
        rc, out = quiet(RB.main, ["--why", "R5: проверка"])
        check("D5 с причиной — пишет и сам перепроверяет записанное",
              rc == 0 and RB.current(copy_)["N_VIDEOS"] == 18 and "записан" in out)
    finally:
        RB.changed_paths, RB.compute, RB.GOLDEN = saved

    check("Z1 настоящий tests/golden.py не изменён тестом", sha(RB.GOLDEN) == real)
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
