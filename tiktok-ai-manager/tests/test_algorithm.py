#!/usr/bin/env python3
"""Изучение раздачи роликов и план улучшения (advisor/algorithm.py).

Числа модуля сверяются с независимым пересчётом прямо из data/; на
искусственных наборах проверяется, что находка и пункт плана появляются
ТОЛЬКО когда данные их дают, — иначе это был бы шаблонный совет.
"""
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from advisor import algorithm as G, analysis as A      # noqa: E402
from insights import policies                          # noqa: E402
from insights.validator import find_violations         # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def by_id(r, fid):
    return next((f for f in r["findings"] if f["id"] == fid), None)


def synthetic(hit_shares=(0.010, 0.012), rest_shares=(0.005, 0.02, 0.003, 0.004),
              views_rest=(800, 900, 1000, 1100), views_hits=(200000, 300000),
              traffic=False):
    """Набор: 4 обычных ролика и 2 хита, два замера Metricool и пара источников."""
    snaps, videos, rows = [], {}, []
    all_views = list(views_rest) + list(views_hits)
    shares = list(rest_shares) + list(hit_shares)
    for i, (v, sh) in enumerate(zip(all_views, shares)):
        vid = f"9{i:017d}"
        hit = i >= len(views_rest)
        videos[vid] = {"video_id": vid, "published_at": f"2026-0{3 + i}-01T12:00:00+00:00"}
        base = {"video_id": vid, "likes": int(v * 0.04), "comments": 1,
                "shares": int(round(v * sh)), "reach": int(v / (1.08 if hit else 1.3)),
                "completion_rate": 0.2, "avg_view_time_sec": 6.0, "favorites": 3,
                "src_foryou": 0.9 if traffic else None}
        for src in ("metricool", "supermetrics"):
            snaps.append(dict(base, source=src, observed_at="2026-09-17T10:00:00+00:00",
                              views=v))
        snaps.append(dict(base, source="metricool", observed_at="2026-10-08T21:00:00+00:00",
                          views=int(v * 1.01)))
        rows.append({"video_id": vid, "views": v, "hit": hit})
    a = {"videos": rows, "unverified": [], "hypotheses": [],
         "observed_at": "2026-09-17T10:00:00+00:00"}
    return snaps, videos, a


def main():
    snaps, videos = G.load()
    a = A.analyze()
    r = G.study(snaps, videos, a)
    text = G.render(r)

    print("=== A. на данных аккаунта ===")
    viol = [(x["id"], v) for x in r["findings"] + r["plan"]
            for v in [find_violations(x["text"], x["claim_type"])] if v]
    viol += [(g["id"], v) for g in r["gaps"] for v in [find_violations(g["text"], "RECOMMENDATION")]
             if v]
    check("A1 каждое утверждение проходит валидатор своего типа (FACT / HYPOTHESIS / RECOMMENDATION)",
          not viol and r["findings"] and r["plan"], str(viol[:2]))
    views = sorted(v["views"] for v in a["videos"])
    top = sorted(v["views"] for v in a["videos"] if v["hit"])
    f1 = by_id(r, "F1")
    check("A2 F1: ступенька и доля верхней группы совпадают с независимым пересчётом",
          f1 and f1["gap"]["high_min"] == top[0] and f1["gap"]["n_high"] == len(top)
          and abs(f1["top_share"] - sum(top) / sum(views)) < 1e-12
          and f"n={len(views)}" in f1["text"], f1 and f1["text"][:80])
    ratios = []
    for v in a["videos"]:
        if v["hit"]:
            s = max((s for s in snaps if s["video_id"] == v["video_id"]
                     and s["source"] == "supermetrics" and s["reach"]),
                    key=lambda s: s["observed_at"])
            ratios.append(s["views"] / s["reach"])
    f2 = by_id(r, "F2")
    check("A3 F2: просмотры на охваченного у хитов — как в сырье Supermetrics",
          f2 and sorted(f2["compare"]["hits"]) == sorted(ratios), str(ratios))
    exp_groups = {"young": set(), "old": set()}
    for vid, v in videos.items():
        obs = sorted((s for s in snaps if s["video_id"] == vid and s["source"] == "metricool"),
                     key=lambda s: s["observed_at"])
        if len(obs) < 2:
            continue
        age = (datetime.fromisoformat(obs[0]["observed_at"])
               - datetime.fromisoformat(v["published_at"])).days
        g = (obs[-1]["views"] - obs[0]["views"]) / obs[0]["views"]
        if age < G.YOUNG_DAYS:
            exp_groups["young"].add((vid, round(g, 9)))
        elif age > G.OLD_DAYS:
            exp_groups["old"].add((vid, round(g, 9)))
    f5 = by_id(r, "F5")
    got = {k: {(x["video_id"], round(x["growth"], 9)) for x in f5["groups"][k]}
           for k in ("young", "old")} if f5 else {}
    check("A4 F5: группы и прирост — как в независимом пересчёте; помечено «не сверено»",
          f5 and got == exp_groups and "не сверено" in f5["text"]
          and f5["claim_type"] == "HYPOTHESIS")
    ids = {x["id"] for x in r["findings"]} | {g["id"] for g in r["gaps"]} | {"разбор"}
    check("A5 каждый пункт плана ссылается на существующую находку",
          all(set(p["basis"]) <= ids for p in r["plan"]))
    blocked = not policies.promotion_allowed("duration_sec", "completion_rate",
                                             "RECOMMENDATION")[0]
    durn = [p for p in r["plan"] if "Длительность ради досмотров не менять" in p["text"]]
    check("A6 политика механической пары соблюдена: пункт «длительность не менять» есть "
          "ровно тогда, когда пара закрыта, и нет совета менять длительность",
          bool(durn) == blocked and not any(w in p["text"].lower() for p in r["plan"]
                                            for w in ("короче", "длиннее", "укорот")))
    f3c = by_id(r, "F3-comments")
    check("A7 значение есть не у всех хитов — сказано, и гипотезы «у всех хитов» нет",
          f3c is not None and ("есть у" in f3c["text"]) == (len(f3c["compare"]["hits"]) < 2)
          and (by_id(r, "F3-commentsh") is None or len(f3c["compare"]["hits"]) == 2))
    check("A8 несверенный ролик назван, а не молча пропущен",
          all(A._num(u["views"]) in f1["text"] for u in a["unverified"]
              if u["verified_views"] is None))
    check("A9 в тексте есть «чего не видно» и план",
          "ЧЕГО НЕ ВИДНО" in text and "ПЛАН УЛУЧШЕНИЯ · RECOMMENDATION" in text)

    print("\n=== B. находка — только когда данные её дают ===")
    # хиты по разные стороны медианы остальных (0,7%): один ниже, один выше
    s1, v1, a1 = synthetic(hit_shares=(0.004, 0.012), rest_shares=(0.005, 0.02, 0.003, 0.009))
    r1 = G.study(s1, v1, a1)
    check("B1 репосты хитов по разные стороны медианы — ни гипотезы, ни пункта плана о репостах",
          by_id(r1, "F3-shares")["compare"]["verdict"] == "mixed"
          and by_id(r1, "F3-sharesh") is None
          and not any("репост" in p["text"] for p in r1["plan"]),
          by_id(r1, "F3-shares")["compare"]["verdict"])
    s2, v2, a2 = synthetic()
    r2 = G.study(s2, v2, a2)
    check("B2 репосты хитов выше медианы остальных — гипотеза и пункт плана появляются",
          by_id(r2, "F3-sharesh") is not None
          and any("репост" in p["text"] for p in r2["plan"]))
    s3, v3, a3 = synthetic(views_rest=(800, 900, 1000, 1100), views_hits=(1500, 1700))
    r3 = G.study(s3, v3, a3)
    check("B3 нет разрыва в 10 раз — нет «ступеньки» и пункта «набирать попытки»",
          "ступенькой" not in by_id(r3, "F1")["text"]
          and not any("Набирать попытки" in p["text"] for p in r3["plan"]))
    s4, v4, a4 = synthetic(traffic=True)
    r4 = G.study(s4, v4, a4)
    check("B4 источники трафика есть — пробела G1 нет",
          not any(g["id"] == "G1" for g in r4["gaps"])
          and any(g["id"] == "G1" for g in r2["gaps"]))
    src = (ROOT / "advisor" / "algorithm.py").read_text(encoding="utf-8")
    check("B5 модуль ничего не пишет и не публикует",
          not any(w in src for w in ("write_text", "write_bytes", '"w"', "publish(",
                                     "submit(")))

    print("\n=== C. запуск ===")
    import runpy
    key = next(k for k, _, fn in runpy.run_path(str(ROOT / "scripts" / "menu.py"))["ITEMS"]
               if fn and fn.__name__ == "algorithm")
    p = subprocess.run([sys.executable, "scripts/menu.py"], cwd=str(ROOT),
                       input=f"{key}\n\n0\n", capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    check("C1 пункт меню показывает разбор и план",
          p.returncode == 0 and "КАК АЛГОРИТМ РАЗДАЁТ" in p.stdout and "ПЛАН УЛУЧШЕНИЯ" in p.stdout)
    from mobile import commands as C
    cmd = C.REGISTRY.get("/algo")
    check("C2 /algo в Telegram — тот же текст, только чтение",
          cmd is not None and not cmd.mutating and "КАК АЛГОРИТМ РАЗДАЁТ" in cmd.handler(None))

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
