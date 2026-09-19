#!/usr/bin/env python3
"""Журнал обращений к мобильному слою.

Append-only JSONL — тем же принципом, что и остальные слои проекта: файл
дописывается, строки не переписываются. В PostgreSQL журнал НЕ дублируется
намеренно: это операционная телеметрия, а не доменные данные, она не
участвует в пересборке и не должна попадать в слепок состояния.

Секреты сюда не попадают. Не «стараемся не писать», а вычищаются на выходе:
редактор проходит по каждому значению перед записью.
"""
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AUDIT_DIR = ROOT / "data" / "mobile"
AUDIT_LOG = AUDIT_DIR / "audit.jsonl"

REDACTED = "«вырезано»"

# Имена, значение которых не записывается никогда, в каком бы месте
# структуры оно ни встретилось.
SECRET_KEYS = frozenset({
    "token", "bot_token", "telegram_bot_token", "api_token", "access_token",
    "approval_token", "approval_token_hash", "token_salt", "password",
    "passwd", "secret", "api_key", "apikey", "dsn", "database_url",
    "authorization", "credentials",
})

# Формы, по которым секрет узнаётся в свободном тексте.
SECRET_PATTERNS = (
    # токен бота Telegram: <числовой id>:<base64-подобная строка>
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b"),
    # строка подключения с паролем
    re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"),
    # Bearer-заголовок
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
)


def scrub(value, _depth=0):
    """Рекурсивная чистка значения от секретов."""
    if _depth > 12:
        return REDACTED
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).strip().lower() in SECRET_KEYS:
                out[k] = REDACTED
            else:
                out[k] = scrub(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]
    if isinstance(value, str):
        out = value
        for pat in SECRET_PATTERNS:
            out = pat.sub(lambda m: (m.group(1) + REDACTED + "@")
                          if m.re.groups else REDACTED, out)
        return out
    return value


def new_correlation_id():
    """Тот же вид идентификатора, что уже используется в publishing_queue:
    UUID. Вторая система корреляции проекту не нужна."""
    return str(uuid.uuid4())


def event(command, status, telegram_user_id=None, correlation_id=None,
          details=None, update_id=None):
    """Собрать запись журнала. Запись не делает — только строит."""
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "telegram_user_id": (str(telegram_user_id)
                             if telegram_user_id is not None else None),
        "command": command,
        "status": status,
        "correlation_id": correlation_id or new_correlation_id(),
        "update_id": update_id,
        "details": scrub(details or {}),
    }


def write(ev, path=None):
    """Дописать запись. Сбой журналирования не должен ронять ответ владельцу,
    но и молчать о нём нельзя — возвращаем признак успеха."""
    target = Path(path) if path else AUDIT_LOG
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(scrub(ev), ensure_ascii=False, sort_keys=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except OSError:
        return False


def record(command, status, telegram_user_id=None, correlation_id=None,
           details=None, update_id=None, path=None):
    ev = event(command, status, telegram_user_id, correlation_id, details,
               update_id)
    write(ev, path)
    return ev


def read_all(path=None):
    target = Path(path) if path else AUDIT_LOG
    if not target.exists():
        return []
    return [json.loads(l) for l in target.read_text(encoding="utf-8").splitlines()
            if l.strip()]
