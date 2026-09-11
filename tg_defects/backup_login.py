#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Копия входа в аккаунт: складывает файлы входа в отдельную папку и
возвращает их обратно.

Чтобы входить без номера и кода, рядом со скриптом нужны всего два файла:

    config.ini                  — api_id и api_hash
    tg_defects_session.session  — ключ авторизации

Скрипт просто копирует эти два файла в указанную папку (для бэкапа или
переноса на другой компьютер) и умеет вернуть их на место. Ключи он не
читает и не расшифровывает — только копирует файлы как есть.

    python backup_login.py --to   D:\\backup     собрать копию входа в папку
    python backup_login.py --from D:\\backup     вернуть вход из этой папки

Папка, где лежат эти файлы, равносильна доступу к аккаунту: у кого они
есть — тот входит без пароля. Храни как пароль, по сети не пересылай,
после переноса лишнюю копию удали.
"""

import argparse
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGIN_FILES = ["config.ini", "tg_defects_session.session"]


def log(message):
    print(message, flush=True)


def present(folder):
    """Какие из файлов входа лежат в папке."""
    return [name for name in LOGIN_FILES
            if os.path.exists(os.path.join(folder, name))]


def restrict(path):
    """По возможности закрыть файл от чужих (только владелец). На Windows
    молча ничего не делает — там доступ решают права папки."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def backup(dest):
    have = present(SCRIPT_DIR)
    if "tg_defects_session.session" not in have:
        raise SystemExit(
            "Рядом со скриптом нет файла входа tg_defects_session.session — "
            "копировать нечего.\nСначала войди: запусти Проба.bat.")
    if "config.ini" not in have:
        log("! config.ini рядом нет — скопирую только сам вход. api_id и "
            "api_hash на новом месте придётся ввести заново.")

    os.makedirs(dest, exist_ok=True)
    copied = []
    for name in have:
        src = os.path.join(SCRIPT_DIR, name)
        dst = os.path.join(dest, name)
        shutil.copy2(src, dst)
        restrict(dst)
        copied.append(name)

    log("Готово. Копия входа собрана в папке:")
    log("  %s" % os.path.abspath(dest))
    for name in copied:
        log("    %s" % name)
    log("")
    log("!!! Эта папка теперь равносильна паролю от аккаунта.")
    log("    Никому не пересылай. Перенёс на другой компьютер — там запусти:")
    log("       python backup_login.py --from <эта папка>")


def restore(src, force):
    if not os.path.isdir(src):
        raise SystemExit("Папки нет: %s" % src)
    have = present(src)
    if "tg_defects_session.session" not in have:
        raise SystemExit(
            "В папке %s нет файла входа tg_defects_session.session — "
            "возвращать нечего." % src)

    clash = present(SCRIPT_DIR)
    if clash and not force:
        raise SystemExit(
            "Рядом со скриптом уже есть вход (%s).\n"
            "Если правда хочешь заменить его копией из папки, добавь ключ "
            "--force." % ", ".join(clash))

    restored = []
    for name in have:
        src_file = os.path.join(src, name)
        dst_file = os.path.join(SCRIPT_DIR, name)
        shutil.copy2(src_file, dst_file)
        restrict(dst_file)
        restored.append(name)

    log("Вход возвращён на место:")
    for name in restored:
        log("    %s" % name)
    log("")
    if "config.ini" in restored:
        log("Готово — Проба.bat и Запустить.bat работают без номера и кода.")
    else:
        log("Сам вход на месте, но config.ini в копии не было — при первом "
            "запуске скрипт спросит api_id и api_hash.")


def main():
    parser = argparse.ArgumentParser(
        description="Сделать копию входа в аккаунт или вернуть его из копии")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--to", metavar="ПАПКА",
                       help="скопировать файлы входа в эту папку (бэкап/перенос)")
    group.add_argument("--from", dest="source", metavar="ПАПКА",
                       help="вернуть файлы входа из этой папки")
    parser.add_argument("--force", action="store_true",
                        help="при возврате заменить вход, если он уже есть")
    args = parser.parse_args()

    if args.to:
        backup(args.to)
    else:
        restore(args.source, args.force)


if __name__ == "__main__":
    main()
