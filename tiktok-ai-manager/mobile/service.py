#!/usr/bin/env python3
"""Сервисный слой мобильного пульта. ТОЛЬКО ЧТЕНИЕ.

Бизнес-логики здесь нет и быть не должно: слой читает то, что посчитали
Phase 0-6, и ничего не пересчитывает. Команда /report не строит отчёт, а
находит уже построенный; /insights не выводит новых заключений, а
показывает выпущенные.

Запрет на изменения не держится на дисциплине автора. Подключение идёт под
ролью tiktok_ro, которой PostgreSQL не выдал ни INSERT, ни UPDATE, ни
DELETE — попытка изменить что-либо отсюда будет отвергнута базой, а не
код-ревью.

Недоступная величина возвращается как None и показывается как N/A.
Придумывать значение вместо отсутствующего запрещено протоколом проекта.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config as core_config

READ_ONLY_ROLE = "ro"

PHASE = "6.5 Mobile Control Layer"

# Способности публикации: имя -> показываемое состояние. Отсутствие
# способности трактуется как ВЫКЛЮЧЕНО (fail closed), а не как «неизвестно».
PUBLISHING_CAPABILITY = "publishing.submit"


def _connect():
    import psycopg
    return psycopg.connect(core_config.dsn(READ_ONLY_ROLE))


def _one(cur, sql, params=None, default=None):
    """Одно значение. Ошибка запроса не валит весь /status — величина
    становится недоступной, и это видно."""
    try:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else default
    except Exception:
        return default


def _rows(cur, sql, params=None):
    try:
        cur.execute(sql, params or ())
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return None


def _manifest(rel, key):
    try:
        m = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        return m.get(key)
    except Exception:
        return None


# ── /status ─────────────────────────────────────────────────────────────────
def system_status():
    out = {"database": False, "phase": PHASE, "publishing_enabled": None}
    try:
        with _connect() as c, c.cursor() as cur:
            out["database"] = _one(cur, "SELECT 1") == 1
            out["videos"] = _one(cur, "SELECT count(*) FROM videos")
            out["snapshots"] = _one(cur, "SELECT count(*) FROM video_snapshots")
            out["assets_total"] = _one(cur, "SELECT count(*) FROM video_assets")
            out["assets_current"] = _one(
                cur, "SELECT count(*) FROM video_assets_current")
            out["tier1_rows"] = _one(
                cur, "SELECT count(*) FROM video_features WHERE tier='tier_1'")
            out["tier1_with_value"] = _one(
                cur, "SELECT count(*) FROM video_features WHERE tier='tier_1'"
                     " AND feature_status IN ('observed','derived')")
            out["insights"] = _one(cur, "SELECT count(*) FROM insights_current")
            out["facts"] = _one(
                cur, "SELECT count(*) FROM insights_current WHERE claim_type='FACT'")
            out["ideas"] = _one(cur, "SELECT count(*) FROM ideas")
            out["scripts"] = _one(cur, "SELECT count(*) FROM scripts")
            out["experiments"] = _one(cur, "SELECT count(*) FROM experiments")
            out["queue"] = _one(cur, "SELECT count(*) FROM publishing_queue")
            # Способность публикации: NULL означает «записи нет» -> закрыто.
            out["publishing_enabled"] = _one(
                cur, "SELECT enabled FROM system_capabilities WHERE capability=%s",
                (PUBLISHING_CAPABILITY,))
            out["last_report"] = _rows(
                cur, "SELECT report_type, period_start, period_end, generated_at,"
                     " path, summary FROM reports_current"
                     " ORDER BY generated_at DESC LIMIT 1")
    except Exception:
        out["database"] = False
    out["asset_status"] = _manifest("data/features_tier1/coverage.json",
                                    "video_asset_status")
    out["insights_hash"] = _manifest("data/insights/manifest.json",
                                     "insights_hash")
    return out


# ── /report ─────────────────────────────────────────────────────────────────
def latest_report():
    """Уже созданный отчёт. Новый не строится: команда показа не является
    причиной пересчёта."""
    with _connect() as c, c.cursor() as cur:
        rows = _rows(
            cur, "SELECT report_type, period_start, period_end, generated_at,"
                 " path, summary, n_videos_in_period FROM reports_current"
                 " ORDER BY generated_at DESC LIMIT 1")
    if not rows:
        return None
    rep = rows[0]
    body = None
    if rep.get("path"):
        p = ROOT / rep["path"]
        if p.exists():
            try:
                body = p.read_text(encoding="utf-8")
            except OSError:
                body = None
    rep["body"] = body
    return rep


# ── /insights ───────────────────────────────────────────────────────────────
def current_insights():
    with _connect() as c, c.cursor() as cur:
        rows = _rows(
            cur, "SELECT claim_type, statement, n_sample, min_sample_required,"
                 " source_kind, competing_explanation FROM insights_current"
                 " ORDER BY claim_type, statement")
        blocked_json = _one(
            cur, "SELECT blocked_conclusions FROM reports_current"
                 " ORDER BY generated_at DESC LIMIT 1")
    blocked = []
    if blocked_json:
        raw = blocked_json if isinstance(blocked_json, list) else []
        for b in raw:
            blocked.append({"conclusion": b.get("conclusion"),
                            "reason_code": b.get("reason_code"),
                            "reason": b.get("reason")})
    return {"insights": rows or [], "blocked": blocked}


# ── /ideas, /scripts, /experiments ──────────────────────────────────────────
def ideas(limit=20):
    with _connect() as c, c.cursor() as cur:
        return _rows(cur,
                     "SELECT title, origin, status, rationale, predicted_metric,"
                     " predicted_value, created_at FROM ideas"
                     " ORDER BY created_at DESC, title LIMIT %s", (limit,)) or []


def scripts(limit=20):
    with _connect() as c, c.cursor() as cur:
        return _rows(cur,
                     "SELECT s.script_id, s.version, s.planned_hook,"
                     " s.planned_duration_sec, s.created_at, i.title AS idea_title,"
                     " i.status AS idea_status FROM scripts s"
                     " LEFT JOIN ideas i ON i.idea_id = s.idea_id"
                     " ORDER BY s.created_at DESC, s.script_id LIMIT %s",
                     (limit,)) or []


def experiments(limit=20):
    with _connect() as c, c.cursor() as cur:
        rows = _rows(cur,
                     "SELECT code, status, variable, success_metric,"
                     " target_sample_size, age_bucket, start_date, end_date,"
                     " conclusion FROM experiments ORDER BY code LIMIT %s",
                     (limit,)) or []
        for r in rows:
            # Фактическая выборка берётся из результатов эксперимента, а не
            # оценивается: неизвестное число остаётся неизвестным.
            r["current_sample"] = _one(
                cur, "SELECT sum(n) FROM experiment_results er"
                     " JOIN experiments e ON e.experiment_id = er.experiment_id"
                     " WHERE e.code = %s", (r["code"],))
    return rows


# ── /queue ──────────────────────────────────────────────────────────────────
def publishing_queue():
    with _connect() as c, c.cursor() as cur:
        counts = _rows(cur, "SELECT status, count(*) AS n FROM publishing_queue"
                            " GROUP BY status ORDER BY status")
        enabled = _one(cur, "SELECT enabled FROM system_capabilities"
                            " WHERE capability=%s", (PUBLISHING_CAPABILITY,))
        total = _one(cur, "SELECT count(*) FROM publishing_queue")
    return {
        # Отсутствие записи о способности = выключено. Иначе пропажа строки
        # в таблице читалась бы как разрешение.
        "publishing_enabled": bool(enabled) if enabled is not None else False,
        "capability_present": enabled is not None,
        "by_status": {r["status"]: r["n"] for r in (counts or [])},
        "total": total,
    }


# ── /refresh ────────────────────────────────────────────────────────────────
def check_services():
    """Только проверка доступности. Ничего не пересчитывает, никуда не ходит
    по сети, внешние источники не трогает."""
    out = {"database": False, "read_only_role": READ_ONLY_ROLE,
           "artifacts": {}, "publishing_enabled": None}
    try:
        with _connect() as c, c.cursor() as cur:
            out["database"] = _one(cur, "SELECT 1") == 1
            out["publishing_enabled"] = _one(
                cur, "SELECT enabled FROM system_capabilities WHERE capability=%s",
                (PUBLISHING_CAPABILITY,))
            out["tables_readable"] = _one(
                cur, "SELECT count(*) FROM information_schema.tables"
                     " WHERE table_schema='public'")
    except Exception:
        out["database"] = False
    for label, rel, key in (
            ("insights", "data/insights/manifest.json", "insights_hash"),
            ("analytics", "data/analytics/manifest.json", "analytics_hash"),
            ("features", "data/features/manifest.json", "feature_hash"),
            ("tier1", "data/features_tier1/manifest.json", "tier1_hash"),
            ("assets", "data/assets/manifest.json", "asset_hash")):
        out["artifacts"][label] = _manifest(rel, key)
    return out
