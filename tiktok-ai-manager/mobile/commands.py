#!/usr/bin/env python3
"""Реестр команд. Белый список — всё, чего в нём нет, отвергается.

Команд изменения состояния в V0.1 нет ни одной. Это не оговорка на будущее,
а проверяемое свойство: у каждой команды стоит mutating=False, и тест
падает, если появится команда с mutating=True без отдельного решения.

Команд /publish, /submit, /send нет и не должно появиться на этой фазе —
даже скрытыми. Их имена перечислены в FORBIDDEN, чтобы попытка добавить
такую команду упиралась в тест, а не в чью-то память.
"""
from mobile import formatters as F
from mobile import service as S

# Имена, которые на Phase 6.5 запрещены полностью.
FORBIDDEN = ("/publish", "/submit", "/send", "/post", "/approve")


class Command:
    def __init__(self, name, description, handler, mutating=False):
        self.name = name
        self.description = description
        self.handler = handler
        self.mutating = mutating


def _start(_):
    return F.start(S.system_status())


def _status(_):
    return F.status(S.system_status())


def _report(_):
    return F.report(S.latest_report())


def _insights(_):
    return F.insights(S.current_insights())


def _ideas(_):
    return F.ideas(S.ideas())


def _scripts(_):
    return F.scripts(S.scripts())


def _experiments(_):
    return F.experiments(S.experiments())


def _queue(_):
    return F.queue(S.publishing_queue())


def _refresh(_):
    return F.refresh(S.check_services())


def _help(_):
    return F.help_text([(c.name, c.description) for c in ordered()])


REGISTRY = {c.name: c for c in (
    Command("/start", "start and show available commands", _start),
    Command("/status", "system status", _status),
    Command("/report", "latest report", _report),
    Command("/insights", "current insights", _insights),
    Command("/ideas", "content ideas", _ideas),
    Command("/scripts", "scripts", _scripts),
    Command("/experiments", "experiments", _experiments),
    Command("/queue", "publishing queue", _queue),
    Command("/refresh", "verify services (read-only)", _refresh),
    Command("/help", "commands", _help),
)}

ORDER = ("/status", "/report", "/insights", "/ideas", "/scripts",
         "/experiments", "/queue", "/refresh", "/help")


def ordered():
    return [REGISTRY[n] for n in ORDER if n in REGISTRY]


def parse(text):
    """Имя команды из текста сообщения. None — команда не распознана.

    Telegram дописывает к команде имя бота в группах (/status@my_bot) —
    это учитывается. Аргументы в V0.1 игнорируются: команд с аргументами
    нет, и молча их принимать нельзя.
    """
    if not isinstance(text, str):
        return None
    head = text.strip().split(maxsplit=1)
    if not head:
        return None
    name = head[0]
    if "@" in name:
        name = name.split("@", 1)[0]
    name = name.lower()
    return name if name in REGISTRY else None


def is_forbidden(text):
    if not isinstance(text, str):
        return False
    head = text.strip().split(maxsplit=1)
    if not head:
        return False
    name = head[0].split("@", 1)[0].lower()
    return name in FORBIDDEN
