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


YES = {"да", "д", "y", "yes"}


def confirmed(answer):
    """Согласие. Латинское y принимается наравне с «да»: ввод кириллицы —
    самое хрупкое место консоли Windows, и подтверждение не должно на нём
    застревать."""
    return (answer or "").strip().lower() in YES


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


def experiments(ask):
    """Подменю: журнал экспериментов. Регистрация — строго до публикации."""
    from advisor import analysis as A, experiments as E
    while True:
        print("\n  Эксперименты\n"
              "   1  Список и итоги\n"
              "   2  Взять идею в работу (до публикации!)\n"
              "   3  Привязать опубликованный ролик\n"
              "   4  Подвести итог (по правилу, записанному при регистрации)\n"
              "   0  Назад\n")
        c = ask("  Выбор > ")
        if c is None or c.strip() == "0":
            return
        c = c.strip()
        a = A.analyze()
        if c == "1":
            print(E.render(E.load(), a))
        elif c == "2":
            path, ideas = E.latest_ideas()
            if not ideas:
                print("идей нет — сначала пункт 4 главного меню")
                continue
            print(f"\nидеи из {path.name}:")
            for i, idea in enumerate(ideas, 1):
                print(f"  {i}. {idea['title']}  (проверяет {idea['tests_hypothesis']})")
            n = (ask("  номер идеи > ") or "").strip()
            if not n.isdigit() or not 1 <= int(n) <= len(ideas):
                print("нет такой идеи")
                continue
            before = E.load()
            try:
                code = E.register_idea(ideas[int(n) - 1], a)
            except ValueError as exc:
                print(f"не зарегистрировано: {exc}")
                continue
            print("\n" + E.taken_message(code, before, E.load()))
        elif c == "3":
            code = (ask("  код эксперимента (EXP-…) > ") or "").strip().upper()
            video = ask("  ссылка на ролик или video_id > ") or ""
            ok, why = E.link(code, video)
            print((f"привязано к {code}" + (f" — {why}" if why else ""))
                  if ok else f"не привязано: {why}")
        elif c == "4":
            ready = [(code, ev) for code, st in sorted(E.load().items())
                     if st["status"] in E.OPEN
                     for ev in [E.evaluate(st, a)] if ev.get("rule")]
            if not ready:
                print("итога пока нет ни у одного эксперимента: нужно "
                      f"{E.DEFAULT_MIN_SAMPLE} зрелых роликов (30 дней после публикации)")
                continue
            for code, ev in ready:
                verdict, text = ev["rule"]
                print(f"\n{code}: {verdict} — {text}")
                if verdict == "inconclusive":
                    print("  остаётся открытым: снимите ролики вне условия (нейтральные "
                          "идеи плана)")
                    continue
                if confirmed(ask(f"  закрыть {code} с этим итогом? да / y: ")):
                    ok, msg = E.conclude_by_rule(code, a)
                    print("  " + msg)
                else:
                    print("  не закрыт")
        else:
            print(f"нет пункта «{c}»")


def plan(ask):
    _sub("-m", "advisor.plan")


def dashboard(ask):
    from advisor import dashboard as D
    D.main([])


def algorithm(ask):
    from advisor import algorithm as G, analysis as A
    snaps, videos = G.load()
    print(G.render(G.study(snaps, videos, A.analyze())))


def update(ask):
    print("Свои эксперименты и идеи сохраняются коммитом, остальные локальные")
    print("изменения откладываются в git stash. Ничего не удаляется.\n")
    _sub("scripts/update.py")


def push(ask):
    print("Журнал экспериментов и сохранённые идеи с этого ПК уйдут в GitHub —")
    print("в вашу ветку. Так их увидят сессии Claude и еженедельный разбор.")
    if not confirmed(ask("Отправить? Введите да (или y): ")):
        print("отменено")
        return
    _sub("scripts/update.py", "--push")


def sync(ask):
    print("Дозагрузка в базу всего, что пришло с git pull. Ничего не удаляется.\n")
    _sub("scripts/sync_db.py")


def doctor(ask):
    _sub("scripts/doctor.py")


def ingest(ask):
    print("Видео берутся из data\\assets\\incoming, имя файла = video_id.mp4\n")
    _sub("-m", "assets.ingest", "--load")


def verify(ask):
    _sub("scripts/verify_all.py")


def migrate(ask):
    print("Миграции: применяются недостающие, уже применённые признаются.")
    print("Данные не удаляются. Повторный запуск безопасен.")
    if not confirmed(ask("Продолжить? Введите да (или y): ")):
        print("отменено")
        return
    if _sub("db/migrate.py", "--adopt") == 0:
        _sub("db/verify_schema.py")


ITEMS = (
    ("1", "Статус системы", status),
    ("2", "Последний отчёт", report),
    ("3", "Что залетело и почему", analyze),
    ("4", "Идеи для следующих видео", ideas),
    ("5", "Эксперименты: взять идею, привязать ролик, итоги", experiments),
    ("6", "План публикаций и календарь (.ics)", plan),
    ("7", "Дашборд в браузере", dashboard),
    ("8", "Загрузить видео из data\\assets\\incoming", ingest),
    ("9", "Как алгоритм раздаёт ролики и план улучшения", algorithm),
    ("10", "Обновить программу из GitHub", update),
    ("11", "Отправить мои эксперименты и идеи в GitHub", push),
    ("12", "Обновить базу после ручного git pull", sync),
    ("13", "Готовность программы", doctor),
    ("14", "Полная проверка (verify_all)", verify),
    ("15", "Миграции (--adopt)", migrate),
    ("0", "Выход", None),
)


def _startup_check():
    """Короткая проверка готовности при запуске: только то, что мешает."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("doctor", ROOT / "scripts" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod.render(mod.checks(), brief=True)
    except Exception as exc:                     # проверка не должна ронять меню
        return f"  (проверка готовности не выполнилась: {type(exc).__name__})"


def _startup_today():
    """Что делать сейчас — из плана и журнала. Не должно ронять меню."""
    try:
        from advisor import analysis as A, experiments as E, plan as P
        _path, ideas = E.latest_ideas()
        return "\n".join("  " + l for l in P.today(ideas, A.analyze()))
    except Exception as exc:
        return f"  (план на сегодня не посчитался: {type(exc).__name__})"


def draw():
    print(f"\n ===== {TITLE} =====\n")
    for key, label, _ in ITEMS:
        print(f"  {key:>2}  {label}")
    print()


def main(ask=input):
    def safe_ask(prompt):
        try:
            return ask(prompt)
        except EOFError:
            return None

    actions = {k: fn for k, _, fn in ITEMS}
    print("\n" + _startup_check())
    print("\n" + _startup_today())
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
