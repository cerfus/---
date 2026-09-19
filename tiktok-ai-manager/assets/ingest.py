#!/usr/bin/env python3
"""Локальный ингест видео — одна команда на весь путь.

    python3 -m assets.ingest            # разбор без записи в PostgreSQL
    python3 -m assets.ingest --load     # то же + запись в PostgreSQL

Оркестратор, а не новый движок: ингест ассетов (assets/run.py) и экстрактор
Tier 1 (features/visual_run.py) вызываются как есть и не переделываются.
Здесь добавлены только три вещи, которых им не хватало для работы руками:
разбор посторонних файлов в каталоге приёма, проверка воспроизводимости
сразу после записи и отчёт о покрытии.

Шаги:
    1. обнаружение файлов        6. извлечение Tier 1
    2. сопоставление с videos    7. доказательства
    3. валидация                 8. проверка детерминизма
    4. SHA-256                   9. отчёт о покрытии
    5. регистрация ассетов

Видео берётся ТОЛЬКО из data/assets/incoming/. Скачивания из TikTok и
обращений к сторонним сервисам здесь нет и не предполагается.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from assets import policies as A
from assets import probe as PR
from assets import run as assets_run
from features import visual_policies as V
from features import visual_run as tier1_run

COVERAGE_MD = ROOT / "data" / "features_tier1" / "coverage.md"
COVERAGE_JSON = ROOT / "data" / "features_tier1" / "coverage.json"

# Момент прогона живёт ОТДЕЛЬНО от отчёта о покрытии и вне git.
# Причина: coverage.json коммитится, а метка времени менялась при каждом
# запуске и пачкала рабочее дерево — дифф без содержания, ложные тревоги
# хуков и невозможность отличить «ничего не изменилось» от «изменилось».
# Наблюдаемость при этом не теряется: время остаётся здесь.
RUNTIME_DIR = ROOT / "data" / "runtime"
RUNTIME_LAST_RUN = RUNTIME_DIR / "ingest_last_run.json"

# Состояние покрытия ассетами. Ровно три значения, и они не смешиваются:
# «ни одного файла» и «часть файлов» — разные ситуации для планирования.
STATUS_AVAILABLE = "AVAILABLE"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNAVAILABLE = "UNAVAILABLE"


def _rel(path):
    """Путь для показа. Вне проекта relative_to бросает исключение, поэтому
    падать из-за строки в отчёте нельзя: печатаем как есть."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def video_asset_status(videos, assets):
    n = sum(1 for v in videos if A.current_asset(assets, v["video_id"]))
    if n == 0:
        return STATUS_UNAVAILABLE, n
    if n < len(videos):
        return STATUS_PARTIAL, n
    return STATUS_AVAILABLE, n


def coverage(videos, assets, rows):
    """Покрытие: по роликам, по признакам, по причинам отсутствия."""
    status, n_with = video_asset_status(videos, assets)
    by_video, by_feature, by_reason = {}, {}, {}
    for r in rows:
        absent = r["feature_status"] in V.ABSENT_STATUS
        v = by_video.setdefault(r["video_id"],
                                {"present": 0, "absent": 0, "statuses": {}})
        v["absent" if absent else "present"] += 1
        v["statuses"][r["feature_status"]] = \
            v["statuses"].get(r["feature_status"], 0) + 1
        f = by_feature.setdefault(r["feature_name"],
                                  {"present": 0, "absent": 0,
                                   "group": r["feature_group"]})
        f["absent" if absent else "present"] += 1
        if absent:
            code = (r["status_reason"] or "").split(":")[0] or "—"
            by_reason[code] = by_reason.get(code, 0) + 1

    for vid, v in by_video.items():
        cur = A.current_asset(assets, vid)
        hist = [a for a in assets if a["video_id"] == vid]
        last = max(hist, key=lambda a: a["asset_version"]) if hist else None
        v["asset_status"] = (cur or last or {}).get("asset_status", "no_asset")
        v["asset_version"] = (cur or last or {}).get("asset_version")
        v["asset_sha256"] = (cur or {}).get("sha256")

    total = len(rows)
    present = sum(1 for r in rows if r["feature_status"] not in V.ABSENT_STATUS)
    return {
        "video_asset_status": status,
        "n_videos": len(videos),
        "n_videos_with_current_asset": n_with,
        "n_feature_rows": total,
        "n_features_with_value": present,
        "n_features_absent": total - present,
        "feature_fill_rate": round(present / total, 6) if total else None,
        "by_video": dict(sorted(by_video.items())),
        "by_feature": dict(sorted(by_feature.items())),
        "by_absence_reason": dict(sorted(by_reason.items())),
        "policy_version": V.VISUAL_FEATURE_POLICY_VERSION,
        "extractor_version": PR.extractor_version(),
        "n_features_declared": len(V.TIER_1_FEATURES),
    }


def coverage_markdown(cov, hashes, scan):
    L = ["# Покрытие Tier 1", "",
         f"`VIDEO_ASSET_STATUS: {cov['video_asset_status']}`", "",
         f"* роликов: **{cov['n_videos']}**, из них с текущим валидным "
         f"ассетом: **{cov['n_videos_with_current_asset']}**",
         f"* строк признаков: **{cov['n_feature_rows']}** "
         f"({cov['n_features_declared']} признаков x {cov['n_videos']} роликов)",
         f"* со значением: **{cov['n_features_with_value']}**, "
         f"без значения: **{cov['n_features_absent']}**",
         f"* заполненность: "
         f"**{100 * (cov['feature_fill_rate'] or 0):.1f}%**", "",
         f"policy: `{cov['policy_version']}` · extractor: "
         f"`{cov['extractor_version']}`",
         f"asset_hash: `{hashes['asset'][:16]}…` · tier1_hash: "
         f"`{hashes['tier1'][:16]}…`", ""]

    if cov["n_features_with_value"] == 0:
        L += ["Значений нет ни у одного признака: валидных видеофайлов не "
              "зарегистрировано. Это честное отсутствие, а не пустой расчёт — "
              "каждая строка несёт машинную причину (таблица ниже).", ""]

    L += ["## Каталог приёма", ""]
    if not any(scan[k] for k in ("matched", "unmatched", "unsupported",
                                 "ambiguous")):
        L += ["Файлов нет. Контракт: `data/assets/incoming/<video_id>.<ext>`,",
              "поддерживаются " + ", ".join(assets_run.VIDEO_SUFFIXES) + ".", ""]
    else:
        L += [f"* сопоставлено с роликами: **{len(scan['matched'])}**"]
        if scan["unmatched"]:
            L += [f"* **не сопоставлено: {len(scan['unmatched'])}** — имя не "
                  f"совпадает ни с одним video_id: "
                  + ", ".join(f"`{n}`" for n in scan["unmatched"][:10])]
        if scan["unsupported"]:
            L += [f"* неподдерживаемое расширение: {len(scan['unsupported'])} — "
                  + ", ".join(f"`{n}`" for n in scan["unsupported"][:10])]
        if scan["ambiguous"]:
            L += [f"* неоднозначных (несколько файлов на один video_id): "
                  f"{len(scan['ambiguous'])}"]
        L += [""]

    L += ["## По роликам", "",
          "| video_id | ассет | версия | sha256 | со значением | без значения |",
          "|---|---|---:|---|---:|---:|"]
    for vid, v in cov["by_video"].items():
        sha = (v["asset_sha256"] or "")[:12] or "—"
        L.append(f"| {vid} | {v['asset_status']} | "
                 f"{v['asset_version'] or '—'} | {sha} | "
                 f"{v['present']} | {v['absent']} |")

    L += ["", "## По признакам", "",
          "| признак | группа | со значением | без значения |", "|---|---|---:|---:|"]
    for name, f in cov["by_feature"].items():
        L.append(f"| {name} | {f['group']} | {f['present']} | {f['absent']} |")

    L += ["", "## Причины отсутствия", "",
          "| код | строк |", "|---|---:|"]
    for code, n in sorted(cov["by_absence_reason"].items(),
                          key=lambda x: (-x[1], x[0])):
        L.append(f"| `{code}` | {n} |")

    L += ["", "---",
          "",
          "Отчёт пересобирается командой `python3 -m assets.ingest`. "
          "Значение признака появляется только из промера реального файла; "
          "подпись, хештеги и статистика источником Tier 1 не являются.",
          ""]
    return "\n".join(L)


def run(load=False, verbose=True):
    def say(*a):
        if verbose:
            print(*a)

    videos = assets_run.load_videos()

    # ── 1-2. обнаружение и сопоставление ────────────────────────────────
    scan = assets_run.scan_incoming(videos=videos)
    say(f"\n[1] обнаружение: {_rel(assets_run.INCOMING)}")
    say(f"    видеофайлов найдено: "
        f"{len(scan['matched']) + len(scan['unmatched']) + sum(len(v) for v in scan['ambiguous'].values())}"
        f" | посторонних расширений: {len(scan['unsupported'])}"
        f" | пропущено служебных: {len(scan['ignored'])}")
    say(f"[2] сопоставление с data/videos.jsonl ({len(videos)} роликов): "
        f"совпало {len(scan['matched'])}")
    for n in scan["unmatched"]:
        say(f"    ВНИМАНИЕ: {n} — имя не совпадает ни с одним video_id, "
            f"файл НЕ будет обработан")
    for n in scan["unsupported"]:
        say(f"    ВНИМАНИЕ: {n} — расширение не поддерживается")
    for stem, files in scan["ambiguous"].items():
        say(f"    ВНИМАНИЕ: {stem} — несколько файлов: {', '.join(files)}")

    # ── 3-5. валидация, SHA-256, регистрация ────────────────────────────
    assets, added, problems, ah, a_run = assets_run.build(write=True)
    say(f"\n[3] валидация и [4] SHA-256: промерено новых файлов: {len(added)}")
    for r in added:
        if r["asset_status"] == "missing":
            continue
        say(f"    {r['video_id']}  {r['asset_status']:<8} "
            f"{(r['sha256'] or '—')[:16]}  {r['byte_size']} Б  "
            f"{r['width']}x{r['height']} @{r['fps']}  {r['duration_sec']} c")
    for p in problems:
        say(f"    ОТКЛОНЕНО {p['video_id']}: {'; '.join(p['violations'])}")
    say(f"[5] регистрация: в манифесте {len(assets)} строк "
        f"(+{len(added)}); asset_hash {ah[:16]}…")
    if load:
        say(f"    записано в PostgreSQL: {assets_run.load_to_db(assets)}")

    # ── 6. Tier 1 ───────────────────────────────────────────────────────
    rows, th, t_run, by_status = tier1_run.build(write=True)
    say(f"\n[6] Tier 1: строк {len(rows)}; tier1_hash {th[:16]}…")
    for st, n in sorted(by_status.items()):
        say(f"    {st:<22}{n}")
    if load:
        say(f"    записано в PostgreSQL: {tier1_run.load_to_db(rows, t_run)}")

    # ── 7. доказательства ───────────────────────────────────────────────
    no_ev = [r for r in rows if not r["evidence_refs"]]
    no_basis = [r for r in rows if "asset_status" not in r["source_basis"]]
    valued = [r for r in rows if r["feature_status"] not in V.ABSENT_STATUS]
    no_sha = [r for r in valued if not r["source_basis"].get("asset_sha256")]
    say(f"\n[7] доказательства: без evidence_refs {len(no_ev)}, "
        f"без source_basis {len(no_basis)}, "
        f"значений без asset_sha256 {len(no_sha)}")
    evidence_ok = not (no_ev or no_basis or no_sha)

    # ── 8. детерминизм ──────────────────────────────────────────────────
    _, _, _, ah2, _ = assets_run.build(write=False)
    _, th2, _, _ = tier1_run.build(write=False)
    det_ok = (ah == ah2) and (th == th2)
    say(f"\n[8] детерминизм: asset_hash {'совпал' if ah == ah2 else 'РАЗОШЁЛСЯ'}"
        f", tier1_hash {'совпал' if th == th2 else 'РАЗОШЁЛСЯ'}")

    # ── 9. покрытие ─────────────────────────────────────────────────────
    cov = coverage(videos, assets, rows)
    hashes = {"asset": ah, "tier1": th}
    scan_serialised = {
        k: (sorted(v) if isinstance(v, list) else
            {kk: [str(x) for x in vv] if isinstance(vv, list) else str(vv)
             for kk, vv in v.items()})
        for k, v in scan.items()}

    # Детерминированный артефакт: при неизменных данных байт в байт тот же.
    COVERAGE_JSON.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_JSON.write_text(json.dumps(
        {**cov, "hashes": hashes, "incoming_scan": scan_serialised},
        ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    COVERAGE_MD.write_text(coverage_markdown(cov, hashes, scan), encoding="utf-8")

    # Наблюдаемость: когда прогон был и что получилось. Вне git.
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_LAST_RUN.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asset_hash": ah, "tier1_hash": th,
        "asset_run_id": a_run, "tier1_run_id": t_run,
        "video_asset_status": cov["video_asset_status"],
        "n_assets_added": len(added),
        "n_features_with_value": cov["n_features_with_value"],
        "n_feature_rows": cov["n_feature_rows"],
        "evidence_ok": evidence_ok, "deterministic": det_ok,
        "n_problems": len(problems),
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    say(f"\n[9] покрытие: со значением {cov['n_features_with_value']} из "
        f"{cov['n_feature_rows']} "
        f"({100 * (cov['feature_fill_rate'] or 0):.1f}%); "
        f"отчёт {_rel(COVERAGE_MD)}")

    say(f"\nVIDEO_ASSET_STATUS: {cov['video_asset_status']}")
    ok = evidence_ok and det_ok and not problems
    say("ИНГЕСТ: " + ("OK" if ok else "ЕСТЬ ЗАМЕЧАНИЯ — см. выше"))
    return {"ok": ok, "scan": scan, "assets": assets, "added": added,
            "rows": rows, "coverage": cov, "hashes": hashes,
            "evidence_ok": evidence_ok, "deterministic": det_ok,
            "problems": problems}


if __name__ == "__main__":
    res = run(load="--load" in sys.argv)
    sys.exit(0 if res["ok"] else 1)
