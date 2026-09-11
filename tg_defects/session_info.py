#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Показывает, что даёт файл входа tg_defects_session.session.

Три вещи: что лежит внутри файла, под каким аккаунтом он входит и как
этот вход выглядит в списке устройств самого Телеграма.

Запуск:  python session_info.py
         python session_info.py --show-string   (напечатать вход строкой)

Не запускать, пока идёт скачивание: файл входа занят одним процессом.
"""

import argparse
import os
import sqlite3
import sys

from common import log, read_setting

try:
    from telethon.sync import TelegramClient
    from telethon import functions
    from telethon.sessions import StringSession
except ImportError:
    sys.exit("Не установлен telethon. Выполни: pip install -r requirements.txt")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_NAME = "tg_defects_session"
SESSION_PATH = os.path.join(SCRIPT_DIR, SESSION_NAME)
SESSION_FILE = SESSION_PATH + ".session"


def show_file():
    """Что лежит внутри файла. Телеграм для этого не нужен."""
    log("=== Внутри файла (обычная база SQLite) ===")
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % SESSION_FILE, uri=True)
    except sqlite3.Error as exc:
        log("! Не смог прочитать файл: %s" % exc)
        return
    try:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        log("таблицы: %s" % ", ".join(tables))

        row = conn.execute("SELECT dc_id, server_address, port, auth_key "
                           "FROM sessions").fetchone()
        if not row:
            log("! Вход ещё не сохранён — запусти Проба.bat, он спросит "
                "номер и код.")
            return
        dc_id, server, port, auth_key = row
        log("датацентр: %s (%s:%s)" % (dc_id, server, port))
        log("ключ входа: %d байт, начало %s..."
            % (len(auth_key or b""), (auth_key or b"")[:8].hex()))

        if "entities" in tables:
            cached = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
            log("запомнено чатов и людей: %d" % cached)
    except sqlite3.Error as exc:
        log("! Файл читается, но устроен непривычно: %s" % exc)
    finally:
        conn.close()


def connect():
    """Подключается по сохранённому входу.

    Именно connect, а не start: диагностика не должна просить номер и код,
    её дело — показать, жив вход или нет.
    """
    api_id = read_setting("telegram", "api_id")
    api_hash = read_setting("telegram", "api_hash")
    if not api_id or not api_hash:
        raise SystemExit("В config.ini нет api_id и api_hash — "
                         "запусти сначала Проба.bat.")

    client = TelegramClient(SESSION_PATH, int(api_id), api_hash)
    try:
        client.connect()
    except sqlite3.OperationalError:
        raise SystemExit("Файл входа занят другой копией скрипта. "
                         "Дождись конца скачивания и запусти снова.")
    except OSError as exc:
        raise SystemExit("Не получилось связаться с Телеграмом: %s" % exc)
    return client


def show_account(client, show_string):
    """Тот же файл, но уже как вход: номер и код не спрашиваются."""
    log("")
    log("=== Вход по этому файлу ===")
    if not client.is_user_authorized():
        log("Вход недействителен: ключ в файле есть, но Телеграм его больше")
        log("не принимает — сеанс завершён в Настройках → Устройства либо")
        log("сменился пароль. Проба.bat заново спросит номер и код.")
        return

    me = client.get_me()
    log("вошли как: %s (@%s), id %s" % (
        " ".join(filter(None, [me.first_name, me.last_name])) or "без имени",
        me.username or "без имени пользователя", me.id))
    log("(номер и код не спрашивались — в этом и смысл файла)")

    if show_string:
        log("")
        log("=== Тот же вход одной строкой ===")
        log("Равносильна файлу: с ней входят с любого компьютера.")
        log("Никуда не вставляй и никому не показывай.")
        log(StringSession.save(client.session))

    log("")
    log("=== Устройства, которые видит сам Телеграм ===")
    log("(то же самое в приложении: Настройки → Устройства)")
    for auth in client(functions.account.GetAuthorizationsRequest()).authorizations:
        log("  %-22s %-18s %-15s %s%s" % (
            auth.app_name, auth.device_model, auth.ip, auth.country,
            "   <-- это наш файл" if auth.current else ""))


def main():
    parser = argparse.ArgumentParser(
        description="Что даёт файл входа tg_defects_session.session")
    parser.add_argument("--show-string", action="store_true",
                        help="напечатать вход одной строкой (равносильна "
                             "файлу — только для переноса на другой компьютер)")
    args = parser.parse_args()

    if not os.path.exists(SESSION_FILE):
        raise SystemExit("Файла входа нет: %s\n"
                         "Сначала запусти Проба.bat — он попросит номер и код."
                         % SESSION_FILE)

    show_file()
    client = connect()
    try:
        show_account(client, args.show_string)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
