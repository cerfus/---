#!/usr/bin/env python3
"""Меню AI Manager в консоли. Запуск: scripts\\menu.bat или python scripts/menu.py

Меню — пульт, а не новый движок: каждый пункт вызывает то, что уже есть
в проекте (mobile.commands, advisor, assets.ingest, verify_all, migrate).
Публикации здесь нет и не будет: publishing.submit остаётся FALSE.

Ввод закончился (Ctrl+Z, закрытый поток) — меню выходит, а не крутится.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import console                       # noqa: E402

TITLE = "AI Manager · @gulyashik52"


def _sub(*args):
    """Дочерний шаг с живым выводом: человек видит ход, а не тишину."""
    try:
        return subprocess.run([sys.executable, *args], cwd=str(ROOT)).returncode
    except KeyboardInterrupt:
        print("\nпрервано — возврат в меню")
        return 130


def status(ask):
    from mobile import commands as C
    print(C.REGISTRY["/status"].handler(None))


def report(ask):
    from mobile import commands as C
    print(C.REGISTRY["/report"].handler(None))


def analyze(ask):
    from advisor import analysis as A
    print(A.render(A.analyze()))


def ideas(ask):
    _sub("-m", "advisor.run", "ideas")


def ingest(ask):
    print("Видео берутся из data\\assets\\incoming, имя файла = video_id.mp4\n")
    _sub("-m", "assets.ingest", "--load")


def verify(ask):
    _sub("scripts/verify_all.py")


def migrate(ask):
    print("Миграции: применяются недостающие, уже применённые признаются.")
    print("Данные не удаляются. Повторный запуск безопасен.")
    if (ask("Продолжить? Введите да: ") or "").strip().lower() != "да":
        print("отменено")
        return
    if _sub("db/migrate.py", "--adopt") == 0:
        _sub("db/verify_schema.py")


ITEMS = (
    ("1", "Статус системы", status),
    ("2", "Последний отчёт", report),
    ("3", "Что залетело и почему", analyze),
    ("4", "Идеи для следующих видео", ideas),
    ("5", "Загрузить видео из data\\assets\\incoming", ingest),
    ("6", "Полная проверка (verify_all)", verify),
    ("7", "Миграции (--adopt)", migrate),
    ("0", "Выход", None),
)


def draw():
    print(f"\n ===== {TITLE} =====\n")
    for key, label, _ in ITEMS:
        print(f"  {key}  {label}")
    print()


def main(ask=input):
    def safe_ask(prompt):
        try:
            return ask(prompt)
        except EOFError:
            return None

    actions = {k: fn for k, _, fn in ITEMS}
    while True:
        draw()
        choice = safe_ask("  Выбор > ")
        if choice is None or choice.strip() == "0":
            print("до встречи")
            return 0
        fn = actions.get(choice.strip())
        if fn is None:
            print(f"нет пункта «{choice.strip()}»")
            continue
        print()
        try:
            fn(safe_ask)
        except Exception as exc:
            from mobile import audit
            print(audit.scrub(f"пункт завершился ошибкой: {type(exc).__name__}: {exc}"))
        if safe_ask("\nEnter — вернуться в меню ") is None:
            print("до встречи")
            return 0


if __name__ == "__main__":
    console.setup()
    sys.exit(main())
