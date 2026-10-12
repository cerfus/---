#!/usr/bin/env python3
"""Оформление ответов для телефона.

Разметка НЕ используется. Ни Markdown, ни HTML: Telegram разбирает их до
показа, и любой символ из данных — подчёркивание в имени признака, звёздочка
в тексте — ломает разбор и роняет отправку. Обычный текст показывается как
есть, поэтому экранировать нечего.

Из текста вычищаются управляющие символы: они невидимы, но ломают вывод.
"""
import re

NA = "N/A"
MAX_LINE = 160

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(value, limit=MAX_LINE):
    """Любое значение -> безопасная однострочная подпись."""
    if value is None:
        return NA
    text = _CONTROL.sub("", str(value)).replace("\r", " ")
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text or NA


def num(value):
    return NA if value is None else str(value)


def mark(value):
    """Светофор. None — это не «плохо», а «неизвестно»."""
    if value is None:
        return "⚠️"
    return "✅" if value else "🔴"


def split_message(text, limit=3500):
    """Нарезка длинного текста по границам строк.

    Строка длиннее лимита режется по символам — иначе она не уйдёт никогда.
    """
    chunks, cur = [], []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append("\n".join(cur))
                cur, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        if size + len(line) + 1 > limit and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()] or [""]


# ── /start и /help ──────────────────────────────────────────────────────────
def start(status):
    pub = status.get("publishing_enabled")
    return "\n".join([
        "TikTok AI Manager",
        f"Status: {'OK' if status.get('database') else 'DEGRADED'}",
        f"Publishing: {'ENABLED' if pub else 'DISABLED'}",
        "",
        "Available:",
        "/status", "/report", "/insights", "/ideas",
        "/scripts", "/experiments", "/queue", "/help",
    ])


def help_text(commands):
    lines = ["📖 Commands", ""]
    for name, desc in commands:
        lines.append(f"{name} — {desc}")
    return "\n".join(lines)


# ── /status ─────────────────────────────────────────────────────────────────
def status(s):
    pub = s.get("publishing_enabled")
    rep = (s.get("last_report") or [None])[0]
    lines = [
        "🧠 AI Manager",
        f"System: {'OK' if s.get('database') else 'DEGRADED'}",
        f"Database: {mark(s.get('database'))}",
        "",
        f"Videos: {num(s.get('videos'))}",
        f"Snapshots: {num(s.get('snapshots'))}",
        f"Assets: {num(s.get('assets_current'))} current "
        f"/ {num(s.get('assets_total'))} registered",
        f"Asset status: {clean(s.get('asset_status'))}",
        f"Tier 1: {num(s.get('tier1_with_value'))} with value "
        f"/ {num(s.get('tier1_rows'))} rows",
        "",
        f"Insights: {num(s.get('insights'))}  (FACT: {num(s.get('facts'))})",
        f"Ideas: {num(s.get('ideas'))}",
        f"Scripts: {num(s.get('scripts'))}",
        f"Experiments: {num(s.get('experiments'))}",
        f"Queue: {num(s.get('queue'))}",
        "",
        f"Publishing: {mark(pub)} {'ENABLED' if pub else 'DISABLED'}",
        "",
        "Last report:",
    ]
    if rep:
        lines += [f"  {clean(rep.get('report_type'))} "
                  f"{clean(rep.get('period_start'))} → {clean(rep.get('period_end'))}",
                  f"  {clean(rep.get('summary'))}"]
    else:
        lines.append("  " + NA)
    lines += ["", f"Phase: {clean(s.get('phase'))}"]
    return "\n".join(lines)


# ── /report ─────────────────────────────────────────────────────────────────
def report(rep):
    if not rep:
        return "No report available yet."
    head = ["📄 Latest report",
            f"Type: {clean(rep.get('report_type'))}",
            f"Period: {clean(rep.get('period_start'))} → "
            f"{clean(rep.get('period_end'))}",
            f"Generated: {clean(rep.get('generated_at'))}",
            f"Videos in period: {num(rep.get('n_videos_in_period'))}",
            f"Summary: {clean(rep.get('summary'), limit=400)}",
            ""]
    body = rep.get("body")
    if not body:
        head.append(f"Report file not readable: {clean(rep.get('path'))}")
        return "\n".join(head)
    return "\n".join(head) + "\n" + _CONTROL.sub("", body)


# ── /insights ───────────────────────────────────────────────────────────────
GROUPS = (("DATA_QUALITY", "DATA QUALITY"),
          ("RECOMMENDATION", "RECOMMENDATIONS"),
          ("HYPOTHESIS", "HYPOTHESES"),
          ("FACT", "FACTS"),
          ("ANOMALY", "ANOMALIES"))


def insights(data):
    rows = data.get("insights") or []
    blocked = data.get("blocked") or []
    lines = ["🧠 Current insights"]
    for key, title in GROUPS:
        items = [r for r in rows if r.get("claim_type") == key]
        lines += ["", title]
        if not items:
            lines.append("• None")
            continue
        for r in items:
            lines.append(f"• {clean(r.get('statement'), limit=320)}")
            if r.get("n_sample") is not None:
                lines.append(f"  n={num(r.get('n_sample'))}")
    lines += ["", f"BLOCKED ({len(blocked)})"]
    if not blocked:
        lines.append("• None")
    else:
        for b in blocked:
            lines.append(f"• {clean(b.get('conclusion'), limit=200)}")
            lines.append(f"  {clean(b.get('reason_code'), limit=80)}")
    return "\n".join(lines)


# ── /ideas ──────────────────────────────────────────────────────────────────
def ideas(rows):
    if not rows:
        return "No ideas available."
    lines = ["💡 Content Ideas", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {clean(r.get('title'), limit=200)}")
        lines.append(f"   basis: {clean(r.get('rationale'), limit=200)}")
        lines.append(f"   status: {clean(r.get('status'))}")
    return "\n".join(lines)


# ── /scripts ────────────────────────────────────────────────────────────────
def scripts(rows):
    if not rows:
        return "No scripts available."
    lines = ["✍️ Scripts", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {clean(r.get('planned_hook'), limit=200)}")
        lines.append(f"   version: {num(r.get('version'))}")
        lines.append(f"   basis: {clean(r.get('idea_title'), limit=200)}")
        lines.append(f"   status: {clean(r.get('idea_status'))}")
    return "\n".join(lines)


# ── /experiments ────────────────────────────────────────────────────────────
def experiments(rows):
    if not rows:
        return "No experiments available."
    lines = []
    for r in rows:
        lines += ["🧪 Experiment",
                  f"ID: {clean(r.get('code'))}",
                  f"Status: {clean(r.get('status'))}",
                  f"Variable: {clean(r.get('variable'))}",
                  f"Success metric: {clean(r.get('success_metric'))}",
                  f"Minimum sample: {num(r.get('target_sample_size'))}",
                  f"Current sample: {num(r.get('current_sample'))}"]
        if r.get("conclusion"):
            lines.append(f"Conclusion: {clean(r.get('conclusion'), limit=300)}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ── /queue ──────────────────────────────────────────────────────────────────
QUEUE_STATUSES = ("draft", "awaiting_owner_approval", "approved", "rejected",
                  "cancelled", "submitted_for_review", "failed")


def queue(q):
    by = q.get("by_status") or {}
    enabled = q.get("publishing_enabled")
    lines = ["📋 Publishing Queue",
             f"Publishing capability: {'ENABLED' if enabled else 'DISABLED'}"]
    if not q.get("capability_present"):
        lines.append("  (capability row absent — treated as DISABLED)")
    lines.append("")
    for st in QUEUE_STATUSES:
        lines.append(f"{st}: {by.get(st, 0)}")
    lines += ["", "Submitting: DISABLED",
              f"Total: {num(q.get('total'))}"]
    return "\n".join(lines)


# ── /refresh ────────────────────────────────────────────────────────────────
def refresh(chk):
    lines = ["🔄 Service check",
             f"Database: {mark(chk.get('database'))}",
             f"DB role: {clean(chk.get('read_only_role'))} (read-only)",
             f"Tables readable: {num(chk.get('tables_readable'))}",
             f"Publishing: {mark(chk.get('publishing_enabled'))}",
             "", "Artifacts:"]
    for k, v in sorted((chk.get("artifacts") or {}).items()):
        lines.append(f"  {k}: {clean(v[:16] + '…') if v else NA}")
    lines += ["", "Read-only check. Nothing was recomputed,",
              "no external source was contacted."]
    return "\n".join(lines)


def error(correlation_id):
    return ("⚠️ Temporary system error.\n"
            f"Correlation ID: {clean(correlation_id)}")
