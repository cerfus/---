#!/usr/bin/env python3
"""Обновить базу из данных проекта — после git pull. Ничего не удаляет.

    python scripts/sync_db.py

Полная пересборка (verify_all --rebuild) требует DROP DATABASE и прав
суперпользователя; на ПК их нет и не нужно. Здесь — дозагрузка: те же
шаги --load, что при пересборке, в том же порядке. Все таблицы
append-only, повторная запись того же содержимого ничего не добавляет,
поэтому команду можно запускать сколько угодно раз.

Шаг упал — следующие не запускаются: каждый опирается на предыдущий.
У каждого шага потолок времени, чтобы зависший шаг не держал окно вечно.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STEP_TIMEOUT_SEC = int(os.environ.get("TIKTOK_STEP_TIMEOUT_SEC", "900"))

STEPS = (
    ("миграции (недостающие применить, применённые признать)", ["db/migrate.py", "--adopt"]),
    ("ролики и замеры из JSONL", ["db/load.py"]),
    ("сверка источников", ["reconcile/run.py", "--load"]),
    ("аналитика", ["analytics/run.py", "--load"]),
    ("признаки", ["features/run.py", "--load"]),
    ("видеоассеты", ["-m", "assets.ingest", "--load"]),
    ("выводы и ежедневный отчёт", ["insights/run.py", "--load"]),
)


def run_step(args, timeout=None):
    """(код, вывод). Вывод не печатается целиком — только хвост при ошибке."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        p = subprocess.run([sys.executable, *args], cwd=str(ROOT), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace",
                           timeout=timeout or STEP_TIMEOUT_SEC)
        return p.returncode, p.stdout or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.output or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return 124, out + f"\n[снято по таймауту: {timeout or STEP_TIMEOUT_SEC} с]"


def main():
    from mobile import audit
    print("ОБНОВЛЕНИЕ БАЗЫ ИЗ ДАННЫХ ПРОЕКТА\n")
    for i, (title, args) in enumerate(STEPS, 1):
        print(f"  {i}/{len(STEPS)} {title} …", end="", flush=True)
        rc, out = run_step(args)
        if rc != 0:
            print(" ОШИБКА")
            tail = "\n".join(out.strip().splitlines()[-12:])
            print("\n" + audit.scrub(tail))
            print(f"\n  Остановлено на шаге {i}: следующие шаги от него зависят.")
            return 1
        last = next((l for l in reversed(out.strip().splitlines()) if l.strip()), "")
        print(f" готово  {audit.scrub(last.strip())[:70]}")
    print("\n  База обновлена.")
    # Честное ограничение дозагрузки: таблицы только дописываются, поэтому
    # уже записанные строки не перезаписываются. Проверено сравнением с
    # полной пересборкой: расходятся подписи роликов, изменённые после
    # первой выгрузки, и признак snapshot_age_sec_max (возраст на момент
    # замера). Меню и разбор читают их из данных проекта, а не из базы.
    print("  Уже записанные в базе подписи роликов и признаки не перезаписываются —"
          "\n  таблицы только дописываются. Разбор, идеи и дашборд берут свежие"
          "\n  значения из данных проекта, так что на экранах всё актуально.")
    return 0


if __name__ == "__main__":
    from core import console
    console.setup()
    sys.exit(main())
