#!/usr/bin/env python3
"""Маршрутизатор: апдейт Telegram -> команда -> сервис -> ответ.

Здесь нет ни одного обращения к базе и ни одной доменной формулы. Роутер
отвечает за четыре вещи: пустить или не пустить, найти обработчик, записать
след и не выпустить наружу подробность ошибки.

Транспорт отделён намеренно: функция handle() принимает обычный словарь и
возвращает обычный словарь. Поэтому весь мобильный слой проверяется тестами
без сети, без токена и без Telegram.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mobile import audit, auth, commands
from mobile import config as mobile_config
from mobile import formatters as F

STATUS_SUCCESS = "success"
STATUS_DENIED = "denied"
STATUS_ERROR = "error"
STATUS_IGNORED = "ignored"
STATUS_DUPLICATE = "duplicate"


class UpdateLedger:
    """Память об уже обработанных update_id.

    В V0.1 все команды читающие, поэтому повтор безвреден и выполняется
    заново — но след повтора обязан быть виден. Когда появится изменяющая
    команда, этот же учёт не даст выполнить её дважды: готовая точка, а не
    обещание переделать.
    """

    def __init__(self, capacity=2048):
        self.capacity = capacity
        self._seen = {}
        self._order = []

    def seen(self, update_id):
        return update_id is not None and update_id in self._seen

    def remember(self, update_id, correlation_id):
        if update_id is None:
            return
        if update_id not in self._seen:
            self._order.append(update_id)
            if len(self._order) > self.capacity:
                self._seen.pop(self._order.pop(0), None)
        self._seen[update_id] = correlation_id

    def correlation_of(self, update_id):
        return self._seen.get(update_id)


def _reply(text, status, correlation_id, command=None, chunks=None):
    return {"text": text, "chunks": chunks or F.split_message(text),
            "status": status, "correlation_id": correlation_id,
            "command": command}


def handle(update, cfg=None, ledger=None, audit_path=None, logger=None):
    """Обработать один апдейт. Исключений наружу не выпускает."""
    cfg = cfg if cfg is not None else mobile_config.load()
    correlation_id = audit.new_correlation_id()
    update_id = auth.extract_update_id(update)
    text = auth.extract_text(update)
    name = commands.parse(text)

    def log(status, command, details, user_id):
        audit.record(command, status, telegram_user_id=user_id,
                     correlation_id=correlation_id, details=details,
                     update_id=update_id, path=audit_path)

    # ── допуск ──────────────────────────────────────────────────────────
    allowed, reason, user_id = auth.authorize(cfg, update)
    if not allowed:
        # Постороннему не сообщается, ЧТО именно не совпало: подробность
        # ушла бы подбирающему доступ. Она остаётся в журнале.
        log(STATUS_DENIED, name or "<unrecognised>",
            {"deny_reason": reason, "had_text": bool(text)}, user_id)
        return _reply(auth.DENY_MESSAGE, STATUS_DENIED, correlation_id, name)

    # ── запрещённые на этой фазе имена ──────────────────────────────────
    if commands.is_forbidden(text):
        log(STATUS_DENIED, text.strip().split(maxsplit=1)[0],
            {"deny_reason": "command_forbidden_in_phase_6_5"}, user_id)
        return _reply("Access denied.", STATUS_DENIED, correlation_id)

    if name is None:
        log(STATUS_IGNORED, "<unrecognised>", {"had_text": bool(text)}, user_id)
        return _reply("Unknown command. Send /help for the list.",
                      STATUS_IGNORED, correlation_id)

    # ── повтор доставки ─────────────────────────────────────────────────
    if ledger is not None and ledger.seen(update_id):
        log(STATUS_DUPLICATE, name,
            {"first_correlation_id": ledger.correlation_of(update_id)}, user_id)
        if commands.REGISTRY[name].mutating:
            # Изменяющая команда повторно не исполняется никогда.
            return _reply("Already processed.", STATUS_DUPLICATE,
                          correlation_id, name)
    if ledger is not None:
        ledger.remember(update_id, correlation_id)

    # ── исполнение ──────────────────────────────────────────────────────
    cmd = commands.REGISTRY[name]
    try:
        text_out = cmd.handler(update)
    except Exception as exc:
        # Наружу — только безопасное сообщение с идентификатором.
        # Подробность идёт в журнал и в лог приложения.
        detail = {"error_type": type(exc).__name__,
                  "error": audit.scrub(str(exc))}
        log(STATUS_ERROR, name, detail, user_id)
        if logger is not None:
            logger(f"[{correlation_id}] {name}: "
                   f"{type(exc).__name__}: {audit.scrub(str(exc))}")
        return _reply(F.error(correlation_id), STATUS_ERROR,
                      correlation_id, name)

    chunks = F.split_message(text_out, mobile_config.SAFE_CHUNK)
    log(STATUS_SUCCESS, name,
        {"chars": len(text_out), "chunks": len(chunks),
         "mutating": cmd.mutating}, user_id)
    return _reply(text_out, STATUS_SUCCESS, correlation_id, name, chunks)
