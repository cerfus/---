#!/usr/bin/env python3
"""Ингест видеоассетов: файлы -> JSONL-манифест -> PostgreSQL.

КОНТРАКТ ИНГЕСТА. Файл кладётся в data/assets/incoming/ с именем, равным
video_id: 7628289075081956640.mp4. Имя — не украшение: video_id входит в
ссылку на ролик (tiktok.com/@handle/video/<video_id>), поэтому связь файла
с роликом однозначна и не требует догадок.

Манифест append-only: прежние строки не переписываются никогда. Повторный
прогон с тем же файлом ничего не добавляет — содержимое уже зарегистрировано
под своим хешем. Новый файл для того же ролика добавляет НОВУЮ версию,
а не заменяет прежнюю.

Скачивание с TikTok здесь отсутствует намеренно: медиа-URL не отдаёт ни
Supermetrics, ни Metricool, а придумывать доступ запрещено протоколом.
"""
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from assets import policies as A
from assets import probe as PR

OUT = ROOT / "data" / "assets"
INCOMING = OUT / "incoming"
MANIFEST = OUT / "manifest.jsonl"
RUN_NS = uuid.UUID("6f1b3e7a-0000-4000-8000-000000000007")

# Не входят в хеш содержания: моменты времени закономерно различаются между
# прогонами и машинами и воспроизводимость описывать не должны.
VOLATILE = {"acquired_at", "registered_at", "run_id"}

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v", ".qt")


def _jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def load_videos():
    return _jsonl(ROOT / "data" / "videos.jsonl")


def load_manifest():
    return _jsonl(MANIFEST)


def find_incoming(video_id):
    """Файл ролика по контракту ингеста. Несколько кандидатов — это ошибка
    оператора, а не повод выбрать один молча."""
    if not INCOMING.exists():
        return None, None
    hits = sorted(p for p in INCOMING.iterdir()
                  if p.is_file() and p.stem == video_id
                  and p.suffix.lower() in VIDEO_SUFFIXES)
    if not hits:
        return None, None
    if len(hits) > 1:
        return None, f"ambiguous_incoming:{','.join(p.name for p in hits)}"
    return hits[0], None


def scan_incoming(incoming=None, videos=None):
    """Полный разбор каталога приёма: что найдено и что с этим не так.

    ДОБАВЛЕНО отдельно от find_incoming намеренно. find_incoming ищет файл
    ПО ИЗВЕСТНОМУ video_id и о постороннем файле молчит — для сборки
    манифеста этого достаточно. Но оператору молчание вредит: файл,
    названный с опечаткой или с чужим id, просто не появится нигде, и
    человек будет думать, что ролик обработан.

    Возвращает {matched, unmatched, unsupported, ambiguous, ignored}.
    """
    inc = Path(incoming) if incoming else INCOMING
    known = {v["video_id"] for v in (videos if videos is not None
                                     else load_videos())}
    out = {"matched": {}, "unmatched": [], "unsupported": [],
           "ambiguous": {}, "ignored": []}
    if not inc.exists():
        return out
    by_stem = {}
    for f in sorted(inc.iterdir()):
        if not f.is_file():
            continue
        if f.name.startswith("."):
            out["ignored"].append(f.name)          # .gitkeep и подобные
            continue
        if f.suffix.lower() not in VIDEO_SUFFIXES:
            out["unsupported"].append(f.name)
            continue
        by_stem.setdefault(f.stem, []).append(f)
    for stem, files in sorted(by_stem.items()):
        if stem not in known:
            out["unmatched"] += [f.name for f in files]
            continue
        if len(files) > 1:
            out["ambiguous"][stem] = [f.name for f in files]
            continue
        out["matched"][stem] = files[0]
    return out


def register(video_id, existing, path=None, ambiguity=None):
    """Одна строка манифеста для ролика. None — регистрировать нечего."""
    version = A.next_version(existing, video_id)
    common = {
        "video_id": video_id, "asset_version": version,
        "policy_version": A.ASSET_POLICY_VERSION,
        "extra": {},
    }

    if path is None:
        # Файла нет. Это фиксируется явной строкой, а не молчанием: позже
        # никто не должен гадать, ролик пропустили или файла не было.
        if any(r["video_id"] == video_id and r["asset_status"] == "missing"
               for r in existing):
            return None            # отсутствие уже зафиксировано
        tools = PR.tooling()
        return {
            **common, "asset_uid": None, "source": "not_supplied",
            "source_uri": f"data/assets/incoming/{video_id}.<ext>",
            "sha256": None, "byte_size": None, "mime_type": None,
            "duration_sec": None, "width": None, "height": None,
            "fps": None, "frame_count": None, "acquired_at": None,
            "extractor_version": PR.extractor_version(tools),
            "asset_status": "missing",
            "status_reason": ambiguity or A.REASON_NOT_SUPPLIED,
            "evidence_refs": [PR.evidence_ref(
                "ingest_contract", path=f"data/assets/incoming/{video_id}.<ext>",
                checked_at_run="deterministic", result="absent")],
        }

    r = PR.probe(path)
    dup = A.duplicate_of(existing, video_id, r.get("sha256"))
    if dup is not None:
        return None                # те же байты уже зарегистрированы

    # Путь относительно проекта — так source_uri читаем и переносим.
    # Файл вне дерева проекта (например, в тестовой песочнице) записывается
    # как есть: падать из-за формы пути нельзя.
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:
        rel = str(path)
    acquired = datetime.fromtimestamp(path.stat().st_mtime,
                                      tz=timezone.utc).isoformat()
    rec = {
        **common,
        "asset_uid": A.asset_uid(r.get("sha256")),
        "source": "owner_upload", "source_uri": rel,
        "sha256": r.get("sha256"), "byte_size": r.get("byte_size"),
        "mime_type": r.get("mime_type"),
        "duration_sec": r.get("duration_sec"), "width": r.get("width"),
        "height": r.get("height"), "fps": r.get("fps"),
        "frame_count": r.get("frame_count"),
        "acquired_at": acquired,
        "extractor_version": r["extractor_version"],
        "asset_status": "valid" if r["ok"] else "invalid",
        "status_reason": r["status_reason"],
        "evidence_refs": [
            PR.evidence_ref("file", path=rel, sha256=r.get("sha256"),
                            byte_size=r.get("byte_size")),
            PR.evidence_ref("probe", version=r["probe_version"],
                            extractor=r["extractor_version"]),
        ],
        "extra": {"probe": {k: v for k, v in r.items()
                            if k in ("scene", "audio_present", "tooling",
                                     "declared_frame_count", "detail")}},
    }
    return rec


def content_hash(rows):
    clean = [{k: v for k, v in sorted(r.items()) if k not in VOLATILE}
             for r in sorted(rows, key=lambda x: (x["video_id"],
                                                  x["asset_version"]))]
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


def build(write=True):
    videos = load_videos()
    existing = load_manifest()
    added, problems = [], []

    for v in sorted(videos, key=lambda x: x["video_id"]):
        vid = v["video_id"]
        path, ambiguity = find_incoming(vid)
        rec = register(vid, existing + added, path=path, ambiguity=ambiguity)
        if rec is None:
            continue
        bad = A.validate_record(rec)
        if bad:
            problems.append({"video_id": vid, "violations": bad})
            continue
        added.append(rec)

    rows = existing + added
    h = content_hash(rows)
    run_id = str(uuid.uuid5(RUN_NS, h))
    now = datetime.now(timezone.utc).isoformat()
    for rec in added:
        rec.setdefault("registered_at", now)
        rec.setdefault("run_id", run_id)

    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        INCOMING.mkdir(parents=True, exist_ok=True)
        # append-only: дописываем, а не переписываем
        with MANIFEST.open("a", encoding="utf-8") as fh:
            for rec in added:
                fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        (OUT / "manifest.json").write_text(json.dumps({
            "asset_hash": h, "asset_run_id": run_id,
            "policy_version": A.ASSET_POLICY_VERSION,
            "extractor_version": PR.extractor_version(),
            "n_assets": len(rows),
            "n_valid": sum(1 for r in rows if r["asset_status"] == "valid"),
            "n_invalid": sum(1 for r in rows if r["asset_status"] == "invalid"),
            "n_missing": sum(1 for r in rows if r["asset_status"] == "missing"),
            "n_videos": len(videos),
            "n_videos_with_current_asset": sum(
                1 for v in videos if A.current_asset(rows, v["video_id"])),
            "ingest_contract": "data/assets/incoming/<video_id>.<ext>",
            "problems": problems,
            "incoming_scan": {k: (sorted(v) if isinstance(v, list)
                                  else {kk: (vv if isinstance(vv, list) else str(vv))
                                        for kk, vv in sorted(v.items())})
                              for k, v in scan_incoming().items()},
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rows, added, problems, h, run_id


def load_to_db(rows):
    import psycopg
    from core import config
    n = 0
    with psycopg.connect(config.dsn("rw"), autocommit=False) as conn, conn.cursor() as cur:
        for r in sorted(rows, key=lambda x: (x["video_id"], x["asset_version"])):
            cur.execute("""INSERT INTO video_assets (
                  video_id, asset_uid, asset_version, source, source_uri, sha256,
                  byte_size, mime_type, duration_sec, width, height, fps,
                  frame_count, acquired_at, extractor_version, policy_version,
                  asset_status, status_reason, evidence_refs, extra,
                  registered_at, run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s::jsonb,%s,%s)
                ON CONFLICT (video_id, asset_version) DO NOTHING""",
                (r["video_id"], r["asset_uid"] or str(uuid.uuid5(
                    A.ASSET_NS, f"missing:{r['video_id']}:{r['asset_version']}")),
                 r["asset_version"], r["source"], r["source_uri"], r["sha256"],
                 r["byte_size"], r["mime_type"], r["duration_sec"], r["width"],
                 r["height"], r["fps"], r["frame_count"], r["acquired_at"],
                 r["extractor_version"], r["policy_version"], r["asset_status"],
                 r["status_reason"], r["evidence_refs"],
                 json.dumps(r["extra"], ensure_ascii=False, sort_keys=True),
                 r["registered_at"], r["run_id"]))
            n += cur.rowcount
        conn.commit()
    return n


if __name__ == "__main__":
    rows, added, problems, h, run_id = build()
    tools = PR.tooling()
    print(f"инструменты промера: " + ", ".join(
        f"{k}={v or 'ОТСУТСТВУЕТ'}" for k, v in sorted(tools.items())))
    print(f"ассетов в манифесте: {len(rows)} (добавлено {len(added)})")
    for st in A.ASSET_STATUS:
        print(f"  {st:<10}{sum(1 for r in rows if r['asset_status'] == st)}")
    print(f"роликов с текущим ассетом: "
          f"{sum(1 for v in load_videos() if A.current_asset(rows, v['video_id']))}"
          f" из {len(load_videos())}")
    if problems:
        print(f"ОТКЛОНЕНО строк: {len(problems)}")
        for p in problems[:5]:
            print(f"  {p['video_id']}: {'; '.join(p['violations'])}")
    print(f"\nasset_hash: {h}")
    print(f"asset_run_id: {run_id}")
    if "--load" in sys.argv:
        print(f"записано в PostgreSQL: {load_to_db(rows)}")
