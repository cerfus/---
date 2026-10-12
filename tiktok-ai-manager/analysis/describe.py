#!/usr/bin/env python3
"""Описательная статистика по накопленным данным.

Ничего не предсказывает и не рекомендует — только считает то, что есть,
и явно показывает, на каком N посчитано. Интерпретация живёт в
content/dna.md и обязана ссылаться на цифры отсюда.
"""
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_N_FOR_PATTERN = 25   # см. CLAUDE.md: порог перевода HYPOTHESIS -> FACT


def load(name):
    p = ROOT / "data" / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def latest_metrics():
    """Последний снимок по каждому ролику, поля склеены из обоих источников."""
    best = defaultdict(dict)
    for s in sorted(load("snapshots.jsonl"), key=lambda x: x["snapshot_at"]):
        for k, v in s.items():
            if k in ("video_id", "snapshot_at", "source"):
                continue
            if v is not None:
                best[s["video_id"]][k] = v
    return best


def spearman(x, y):
    def rank(a):
        order = sorted(range(len(a)), key=lambda i: a[i])
        r = [0] * len(a)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    rx, ry = rank(x), rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(x)))
    den = (sum((v - mx) ** 2 for v in rx) * sum((v - my) ** 2 for v in ry)) ** 0.5
    return num / den if den else 0.0


def main():
    videos = {v["video_id"]: v for v in load("videos.jsonl")}
    met = latest_metrics()
    rows = []
    for vid, v in videos.items():
        m = met[vid]
        rows.append({**v, **m})
    rows.sort(key=lambda r: r["published_at"])

    n = len(rows)
    views = [r["views"] for r in rows]
    print(f"N = {n} роликов | {rows[0]['published_at'][:10]} .. {rows[-1]['published_at'][:10]}")
    print(f"порог для перевода закономерности в FACT: N >= {MIN_N_FOR_PATTERN} "
          f"({'ДОСТИГНУТ' if n >= MIN_N_FOR_PATTERN else f'не достигнут, нужно ещё {MIN_N_FOR_PATTERN - n}'})")
    print()

    print("=== РАСПРЕДЕЛЕНИЕ ПРОСМОТРОВ ===")
    print(f"сумма {sum(views)} | медиана {int(st.median(views))} | среднее {int(st.mean(views))}")
    top2 = sorted(views)[-2:]
    print(f"два лучших ролика = {100 * sum(top2) / sum(views):.1f}% всех просмотров")
    base = sorted(views)[:-2]
    print(f"базовый уровень без них: медиана {int(st.median(base))}, "
          f"диапазон {min(base)}–{max(base)}")
    print()

    print("=== ПО РОЛИКАМ (сортировка по completion rate) ===")
    print(f"{'дата':12}{'сек':>6}{'CR%':>7}{'views':>8}{'reach':>8}{'like%':>7}{'share%':>7}")
    for r in sorted(rows, key=lambda x: -x["completion_rate"]):
        print(f"{r['published_at'][:10]:12}{r['duration_sec']:>6.1f}"
              f"{100 * r['completion_rate']:>7.1f}{r['views']:>8}{r['reach']:>8}"
              f"{100 * r['likes'] / r['views']:>7.1f}{100 * r['shares'] / r['views']:>7.2f}")
    print()

    print(f"=== КОРРЕЛЯЦИИ (Spearman, N={n}) ===")
    pairs = {
        "длительность -> completion rate": ([r["duration_sec"] for r in rows],
                                            [r["completion_rate"] for r in rows]),
        "completion rate -> просмотры":    ([r["completion_rate"] for r in rows], views),
        "длительность -> просмотры":       ([r["duration_sec"] for r in rows], views),
        "share-rate -> просмотры":         ([r["shares"] / r["views"] for r in rows], views),
        "длина описания -> просмотры":     ([r["caption_len"] for r in rows], views),
    }
    for label, (a, b) in pairs.items():
        print(f"  {label:34} {spearman(a, b):+.2f}")
    print()

    print("=== ХРОНОМЕТРАЖ ===")
    for label, sel in (("<= 11.2 сек", lambda r: r["duration_sec"] <= 11.2),
                       ("> 11.2 сек", lambda r: r["duration_sec"] > 11.2)):
        g = [r for r in rows if sel(r)]
        if not g:
            continue
        hi = [r for r in g if r["completion_rate"] >= 0.22]
        print(f"  {label:12} N={len(g):>2} | медиана CR {100 * st.median([r['completion_rate'] for r in g]):>5.1f}% "
              f"| медиана просмотров {int(st.median([r['views'] for r in g])):>6} "
              f"| роликов с CR>=22%: {len(hi)}/{len(g)}")
    print()

    print("=== ПО МЕСЯЦАМ ===")
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["published_at"][:7]].append(r)
    for mth in sorted(by_month):
        g = by_month[mth]
        print(f"  {mth}: роликов {len(g):>2} | медиана просмотров "
              f"{int(st.median([r['views'] for r in g])):>7} | медиана CR "
              f"{100 * st.median([r['completion_rate'] for r in g]):>5.1f}%")


if __name__ == "__main__":
    main()
