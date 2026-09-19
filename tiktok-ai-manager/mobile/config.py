#!/usr/bin/env python3
"""Конфигурация мобильного слоя. Только из окружения, никогда из кода.

Правило отказа — FAIL CLOSED. Нет токена или нет владельца — интеграция
выключена целиком. Половинчатого состояния «бот работает, но владелец не
задан» не существует: такой бот отвечал бы кому угодно.

Отсутствие Telegram НЕ должно валить остальную систему: аналитика, ингест
и отчёты обязаны работать без него. Поэтому здесь возвращается статус, а не
поднимается исключение при импорте.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import config as core_config

TOKEN_VAR = "TELEGRAM_BOT_TOKEN"
OWNER_VAR = "TELEGRAM_OWNER_ID"

# Максимальный размер сообщения Telegram. Длинный текст режется по этому
# пределу с запасом на служебные строки.
TELEGRAM_MAX_MESSAGE = 4096
SAFE_CHUNK = 3500

# Долгий опрос: сколько секунд держать соединение getUpdates.
POLL_TIMEOUT_SEC = 25
HTTP_TIMEOUT_SEC = POLL_TIMEOUT_SEC + 10

DISABLED_NO_TOKEN = f"Telegram integration: DISABLED — {TOKEN_VAR} missing"
DISABLED_NO_OWNER = f"Telegram integration: DISABLED — {OWNER_VAR} missing"
DISABLED_BAD_OWNER = f"Telegram integration: DISABLED — {OWNER_VAR} is not numeric"


class MobileConfig:
    """Разобранная конфигурация. Токен держится здесь и НИКУДА не пишется:
    ни в аудит, ни в логи, ни в ответы пользователю."""

    def __init__(self, token=None, owner_id=None, reason=None):
        self._token = token
        self.owner_id = owner_id
        self.reason = reason

    @property
    def enabled(self):
        return self._token is not None and self.owner_id is not None

    @property
    def token(self):
        if not self.enabled:
            raise RuntimeError("Telegram integration disabled: " + (self.reason or ""))
        return self._token

    def __repr__(self):
        # Токен не попадает в repr даже случайно: repr уходит в трассировки.
        return (f"MobileConfig(enabled={self.enabled}, "
                f"owner_id={self.owner_id}, reason={self.reason!r})")

    __str__ = __repr__


def load(env=None):
    """Разбор окружения. Возвращает MobileConfig, исключений не бросает."""
    core_config.load_dotenv()
    src = env if env is not None else os.environ
    token = (src.get(TOKEN_VAR) or "").strip()
    owner = (src.get(OWNER_VAR) or "").strip()

    if not token:
        return MobileConfig(reason=DISABLED_NO_TOKEN)
    if not owner:
        # Токен без владельца опаснее отсутствия токена: бот поднялся бы и
        # отвечал любому, кто его найдёт. Поэтому тоже выключено.
        return MobileConfig(reason=DISABLED_NO_OWNER)
    if not owner.lstrip("-").isdigit():
        return MobileConfig(reason=DISABLED_BAD_OWNER)
    return MobileConfig(token=token, owner_id=int(owner))


def status_line(cfg=None):
    c = cfg if cfg is not None else load()
    return "Telegram integration: ENABLED" if c.enabled else c.reason
