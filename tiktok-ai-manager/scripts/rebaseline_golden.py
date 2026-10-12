#!/usr/bin/env python3
"""Пересчитать эталоны tests/golden.py после нового сырья — по процедуре.

    python3 scripts/rebaseline_golden.py --check            что изменится, без записи
    python3 scripts/rebaseline_golden.py --why "R5: …"      записать с причиной

Процедура из шапки tests/golden.py: сырьё добавляется БЕЗ изменения кода,
провалиться вправе только эталоны, прежние значения уходят в HISTORY с
причиной. Этот инструмент делает механическую часть и не даёт её обойти:

  * отказ, если относительно HEAD изменено что-то кроме данных (data/,
    reports/) и самого tests/golden.py: при неизменном коде сдвиг
    эталона вызван только данными, а пересчёт «под ответ» после правки
    кода — именно то, от чего процедура защищает;
  * значения считаются теми же функциями, что проверяют тесты
    (insights.run.input_hashes / build, файл признаков, data/raw);
  * прежние значения дописываются в HISTORY, а не теряются.

Порядок: СНАЧАЛА полная проверка (bash scripts/verify_all.sh) — она
пересобирает производные данные (сверку, аналитику, признаки, выводы) и
падает только на эталонах; ПОТОМ этот пересчёт; потом проверка снова.
Обратный порядок прочёл бы устаревшие производные файлы — инструмент
это ловит: пришло сырьё с наблюдениями, а хеш сверки не сдвинулся.

После записи обязательна полная проверка: если провалилось что-то кроме
эталонов, оно провалится и после пересчёта — это дефект, а не сдвиг.
"""
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests" / "golden.py"
ALLOWED_PREFIXES = ("data/", "reports/")
ALLOWED_FILES = ("tests/golden.py",)


def changed_paths(root=ROOT):
    """Пути (от каталога проекта), изменённые относительно HEAD, вместе с
    неотслеживаемыми. Пути вне проекта не возвращаются."""
    prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=str(root),
                            capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all",
                          "--", "."], cwd=str(root), capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    paths, items, i = [], out.split("\0"), 0
    while i < len(items):
        entry = items[i]
        i += 1
        if len(entry) < 4:
            continue
        if "R" in entry[:2] or "C" in entry[:2]:
            i += 1
        p = entry[3:]
        paths.append(p[len(prefix):] if p.startswith(prefix) else p)
    return paths


def code_changes(paths):
    """Что из изменённого — не данные. Пусто — пересчёт разрешён."""
    return [p for p in paths
            if not p.startswith(ALLOWED_PREFIXES) and p not in ALLOWED_FILES]


def compute(root=ROOT):
    """Текущие значения эталона — теми же функциями, что проверяют тесты."""
    from insights import run as R
    raw = sorted(p.name for p in (root / "data" / "raw").glob("*.json"))
    videos = [l for l in (root / "data" / "videos.jsonl").read_text(encoding="utf-8")
              .splitlines() if l.strip()]
    feats = [json.loads(l) for l in (root / "data" / "features" /
                                     "features_feature-policy-1.0.0.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    tiers = Counter(r["tier"] for r in feats)
    ins, blocked, _md, h, _run_id, hashes = R.build(write=False)
    return {"RAW_SET": raw, "N_VIDEOS": len(videos), "UPSTREAM": dict(hashes),
            "INSIGHTS_HASH": h, "N_INSIGHTS": len(ins), "N_BLOCKED": len(blocked),
            "FEATURE_ROWS": len(feats), "TIER0_ROWS": tiers.get("tier_0", 0),
            "TIER05_ROWS": tiers.get("tier_0.5", 0),
            "FEATURE_STATUSES": dict(sorted(Counter(r["feature_status"]
                                                    for r in feats).items()))}


def current(golden_path=None):
    golden_path = golden_path or GOLDEN        # при вызове, а не при определении
    ns = {"__file__": str(golden_path)}
    exec(compile(Path(golden_path).read_text(encoding="utf-8"), str(golden_path), "exec"), ns)
    return {"RAW_SET": sorted(ns["RAW_SET"]), "N_VIDEOS": ns["N_VIDEOS"],
            "UPSTREAM": dict(ns["UPSTREAM"]), "INSIGHTS_HASH": ns["INSIGHTS_HASH"],
            "N_INSIGHTS": ns["N_INSIGHTS"], "N_BLOCKED": ns["N_BLOCKED"],
            "FEATURE_ROWS": ns["FEATURE_ROWS"], "TIER0_ROWS": ns["TIER0_ROWS"],
            "TIER05_ROWS": ns["TIER05_ROWS"],
            "FEATURE_STATUSES": dict(sorted(ns["FEATURE_STATUSES"].items())),
            "_history_len": len(ns["HISTORY"])}


def stale_derived(old, new, root=ROOT):
    """Новые файлы сырья с наблюдениями при неизменном хеше сверки —
    производные данные не пересобраны. Список таких файлов или []."""
    added = sorted(set(new["RAW_SET"]) - set(old["RAW_SET"]))
    obs = []
    for name in added:
        f = Path(root) / "data" / "raw" / name
        meta = json.loads(f.read_text(encoding="utf-8")).get("_meta", {}) if f.exists() else {}
        if meta.get("usable_as_observation") is not False:
            obs.append(name)
    same = new["UPSTREAM"].get("reconciliation") == old["UPSTREAM"].get("reconciliation")
    return obs if obs and same else []


def diff(old, new):
    return [k for k in new if old.get(k) != new[k]]


def _dict_block(d, indent):
    pad = " " * indent
    items = [f'"{k}": {json.dumps(v)}' for k, v in d.items()]
    return "{" + (",\n" + pad).join(items) + "}"


def rewrite(text, old, new, why):
    """Новый текст golden.py. Каждая заменяемая конструкция обязана
    найтись ровно один раз — иначе отказ, а не молчаливая порча файла."""
    raw = "\n".join(f'    "{n}",' for n in new["RAW_SET"])
    up = "\n".join(f'    "{k}": "{v}",' for k, v in new["UPSTREAM"].items())
    st = new["FEATURE_STATUSES"]
    st_items = [f'"{k}": {v}' for k, v in st.items()]
    st_lines = ", ".join(st_items[:2]) + (",\n                    " + ", ".join(st_items[2:])
                                          if len(st_items) > 2 else "")
    subs = [
        (r"RAW_SET = frozenset\(\{\n.*?\n\}\)", f"RAW_SET = frozenset({{\n{raw}\n}})"),
        (r"N_VIDEOS = \d+", f"N_VIDEOS = {new['N_VIDEOS']}"),
        (r"UPSTREAM = \{\n.*?\n\}", f"UPSTREAM = {{\n{up}\n}}"),
        (r'INSIGHTS_HASH = "[0-9a-f]+"', f'INSIGHTS_HASH = "{new["INSIGHTS_HASH"]}"'),
        (r"N_INSIGHTS, N_BLOCKED = \d+, \d+",
         f"N_INSIGHTS, N_BLOCKED = {new['N_INSIGHTS']}, {new['N_BLOCKED']}"),
        (r"FEATURE_ROWS = \d+", f"FEATURE_ROWS = {new['FEATURE_ROWS']}"),
        (r"TIER0_ROWS, TIER05_ROWS = \d+, \d+",
         f"TIER0_ROWS, TIER05_ROWS = {new['TIER0_ROWS']}, {new['TIER05_ROWS']}"),
        (r"FEATURE_STATUSES = \{.*?\}", f"FEATURE_STATUSES = {{{st_lines}}}"),
    ]
    for pattern, repl in subs:
        text, n = re.subn(pattern, lambda _m, r=repl: r, text, count=0, flags=re.S)
        if n != 1:
            raise ValueError(f"в golden.py не найдено ровно одно «{pattern[:30]}…» ({n})")
    added = sorted(set(new["RAW_SET"]) - set(old["RAW_SET"]))
    prev = {"n_videos": old["N_VIDEOS"], "upstream": old["UPSTREAM"],
            "insights_hash": old["INSIGHTS_HASH"],
            "insights": [old["N_INSIGHTS"], old["N_BLOCKED"]],
            "feature_rows": old["FEATURE_ROWS"],
            "tiers": [old["TIER0_ROWS"], old["TIER05_ROWS"]],
            "feature_statuses": old["FEATURE_STATUSES"]}
    entry = ("    {" + f'"raw": {json.dumps(", ".join(added) or "без нового сырья", ensure_ascii=False)},'
             + f'\n     "why": {json.dumps(why, ensure_ascii=False)},'
             + f'\n     "previous": {json.dumps(prev, ensure_ascii=False)}' + "},\n")
    m = re.search(r"\nHISTORY = \[\n.*?\n\]\n", text, flags=re.S)
    if not m:
        raise ValueError("в golden.py не найден список HISTORY")
    end = m.end() - 2                      # перед закрывающей «]\n»
    return text[:end] + entry + text[end:]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Пересчёт эталонов после нового сырья.")
    ap.add_argument("--check", action="store_true", help="только показать, без записи")
    ap.add_argument("--why", help="причина сдвига для HISTORY (обязательна для записи)")
    args = ap.parse_args(argv)

    code = code_changes(changed_paths())
    if code:
        print("ОТКАЗ: относительно HEAD изменён не только сырьё и производные данные:")
        for p in code[:20]:
            print(f"  {p}")
        print("Эталон пересчитывается только при неизменном коде. Закоммитьте код "
              "отдельно, прогоните проверку, затем добавьте сырьё.")
        return 2
    old, new = current(), compute()
    stale = stale_derived(old, new)
    if stale:
        print("ОТКАЗ: пришло сырьё с наблюдениями (" + ", ".join(stale) + "), а хеш сверки "
              "не сдвинулся — производные данные не пересобраны. Сначала "
              "bash scripts/verify_all.sh (упадут только эталоны), затем этот пересчёт.")
        return 2
    changed = diff(old, new)
    if not changed:
        print("эталон актуален — пересчитывать нечего")
        return 0
    for k in changed:
        o, n = old[k], new[k]
        if k == "RAW_SET":
            print(f"  RAW_SET: + {sorted(set(n) - set(o))}  − {sorted(set(o) - set(n))}")
        else:
            print(f"  {k}: {o} → {n}")
    if args.check:
        return 0
    if not args.why:
        print("нужна причина: --why \"R5: что пришло и почему сдвинулось\"")
        return 2
    text = rewrite(GOLDEN.read_text(encoding="utf-8"), old, new, args.why)
    GOLDEN.write_text(text, encoding="utf-8", newline="\n")
    after = current()
    bad = [k for k in new if after[k] != new[k]]
    if bad or after["_history_len"] != old["_history_len"] + 1:
        print(f"ОШИБКА записи: не совпало {bad}")
        return 1
    print("эталон записан, прежние значения — в HISTORY. Теперь полная проверка: "
          "bash scripts/verify_all.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
