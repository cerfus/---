#!/usr/bin/env python3
"""Авторизация мобильного слоя. Белый список из одного владельца.

Порядок проверок зафиксирован и исполняется сверху вниз:

    1. настроен ли токен       -> иначе интеграции нет вовсе
    2. опознан ли пользователь -> иначе обращение анонимное
    3. это ли владелец         -> иначе отказ
    4. известна ли команда     -> иначе отказ

Отказ не объясняет постороннему, что именно не совпало: подробность здесь
— подсказка тому, кто подбирает доступ. Подробность уходит в журнал.
"""
DENY_MESSAGE = "Access denied."

# Машинные причины отказа. Уходят в журнал, не в ответ пользователю.
DENY_NOT_CONFIGURED = "integration_disabled"
DENY_NO_USER = "user_not_identified"
DENY_NOT_OWNER = "not_owner"
DENY_UNKNOWN_COMMAND = "unknown_command"

ALLOW = "allowed"


def extract_user_id(update):
    """Telegram-идентификатор отправителя. None, если его нет.

    Отсутствие from.id — не повод догадываться: канальные посты и
    служебные апдейты владельцем не являются.
    """
    if not isinstance(update, dict):
        return None
    for key in ("message", "edited_message", "channel_post",
                "edited_channel_post", "callback_query"):
        payload = update.get(key)
        if isinstance(payload, dict):
            frm = payload.get("from")
            if isinstance(frm, dict) and "id" in frm:
                try:
                    return int(frm["id"])
                except (TypeError, ValueError):
                    return None
    return None


def extract_text(update):
    for key in ("message", "edited_message"):
        payload = update.get(key) if isinstance(update, dict) else None
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            return payload["text"]
    return ""


def extract_update_id(update):
    if isinstance(update, dict):
        try:
            return int(update.get("update_id"))
        except (TypeError, ValueError):
            return None
    return None


def authorize(cfg, update):
    """(разрешено, машинная причина, user_id). Ни одного исключения."""
    user_id = extract_user_id(update)
    if not cfg.enabled:
        return False, DENY_NOT_CONFIGURED, user_id
    if user_id is None:
        return False, DENY_NO_USER, None
    if user_id != cfg.owner_id:
        return False, DENY_NOT_OWNER, user_id
    return True, ALLOW, user_id
