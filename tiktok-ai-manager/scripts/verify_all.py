#!/usr/bin/env python3
"""Полная проверка проекта. Работает одинаково в Windows CMD и в Linux.

    python scripts/verify_all.py              все проверки, БД не пересоздаётся
    python scripts/verify_all.py --rebuild    + пересборка БД с нуля из JSONL
    python scripts/verify_all.py --help

Это перенос scripts/verify_all.sh на переносимый Python. Ни bash, ни WSL,
ни sed/grep/awk/sha256sum не нужны: фильтрация вывода и подсчёт хешей
делаются здесь же средствами стандартной библиотеки.

ОТЛИЧИЕ ОТ .SH, СДЕЛАННОЕ НАМЕРЕННО. Оболочечный вариант падает на первой
же ошибке (set -e), и остаток проверок тогда молча не выполняется — именно
так однажды осталась непройденной проверка publishing.submit. Здесь
выполняются ВСЕ шаги, а провалы копятся и печатаются списком в конце.
Код возврата ненулевой, если провален хотя бы один шаг.

ПЕРЕСБОРКА БД (шаг 3) ПО УМОЛЧАНИЮ НЕ ВЫПОЛНЯЕТСЯ. Она требует DROP
DATABASE, а роли проекта созданы в 0001 с NOCREATEDB — ни owner, ни rw, ни
ro выполнить её не могут в принципе. Нужен отдельный суперпользовательский
DSN в TIKTOK_DSN_SUPER, и запрашивать пересборку надо явно: --rebuild.
Без него шаг честно помечается ПРОПУЩЕН, а не выдаётся за пройденный.
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def _setup_console():
    """Кириллица в консоли Windows.

    Кодовая страница консоли по умолчанию cp866, а весь вывод здесь —
    UTF-8. Переключаем страницу сами, а не в .bat: тогда запуск напрямую
    (`python scripts\\verify_all.py`) выглядит так же, как через обёртку.
    errors="replace" — чтобы неведомый символ не ронял всю проверку.
    """
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass                                  # не смертельно: только вид
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_setup_console()

RESULTS = []            # (шаг, название, состояние, пояснение)
CHECKS = [0]            # суммарное число проверок внутри дочерних сценариев
OK, FAIL, SKIP = "OK", "FAIL", "ПРОПУЩЕН"


# ─────────────────────────── запуск и фильтры ───────────────────────────

def child_env():
    """Дочерний Python обязан писать UTF-8, иначе кириллица в cp866 рвётся."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def scrub_text(text):
    """Убрать секреты из вывода перед печатью.

    Вывод этой проверки люди копируют в переписку и в issue целиком.
    Трассировка упавшего дочернего сценария может донести до консоли
    строку подключения с паролем. Пользуемся уже выверенными образцами
    из mobile/audit.py, а не заводим второй их набор: там же покрыты
    токен Telegram и заголовок Bearer.
    """
    try:
        from mobile import audit
        return audit.scrub(text)
    except Exception:
        from core import config                   # запасной путь: только DSN
        return config.redact(text)


# Потолок времени на один дочерний шаг. Самый тяжёлый из нынешних —
# тесты Phase 6, около 15 секунд, так что запас сорокакратный и по
# здоровому шагу он не ударит. Меняется через окружение, если понадобится.
STEP_TIMEOUT_SEC = int(os.environ.get("TIKTOK_STEP_TIMEOUT_SEC", "600"))
RC_TIMEOUT = 124                     # тот же код, что возвращает GNU timeout


def rc_detail(rc):
    """Человеческое объяснение ненулевого кода возврата."""
    if rc == RC_TIMEOUT:
        return f"шаг не уложился в {STEP_TIMEOUT_SEC} с и был снят"
    return f"код возврата {rc}"


def run(*args, timeout=None):
    """Запуск текущим интерпретатором. Возвращает (код, очищенный вывод).

    Потолок времени обязателен. Без него один заблокировавшийся дочерний
    процесс останавливает всю проверку насовсем: отчёт не доходит ни до
    publishing.submit, ни до итоговой строки, и человек видит замерший
    экран вместо провала. Это та же беда, что и с set -e в ISSUE-2 —
    проверка молча не доводится до конца, — только ещё тише, потому что
    здесь нет даже кода возврата.

    Снятый по таймауту шаг — провал (RC_TIMEOUT), и остальные шаги
    продолжают выполняться. Уже накопленный вывод сохраняется: по нему
    видно, на чём именно шаг встал.
    """
    limit = STEP_TIMEOUT_SEC if timeout is None else timeout
    try:
        proc = subprocess.run(
            [sys.executable, *args], cwd=str(ROOT), env=child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", timeout=limit)
    except subprocess.TimeoutExpired as exc:
        partial = exc.output or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return RC_TIMEOUT, scrub_text(
            f"{partial}\n[снято по таймауту: {limit} с]")
    return proc.returncode, scrub_text(proc.stdout or "")


def head(text, n):
    return "\n".join(text.splitlines()[:n])


def tail(text, n):
    return "\n".join(text.splitlines()[-n:])


def indent(text, pad="  "):
    return "\n".join(pad + ln for ln in text.splitlines())


def field(text, needle, idx):
    """Замена `grep needle | awk '{print $idx+1}'` — первое совпадение."""
    for line in text.splitlines():
        if needle in line:
            parts = line.split()
            if len(parts) > idx:
                return parts[idx]
    return None


def last_field_of_last_line(text):
    """Замена `tail -1 | awk '{print $NF}'`."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    parts = lines[-1].split()
    return parts[-1] if parts else None


def sha256_of(paths):
    """Замена `cat ... | sha256sum`: хеш побайтовой склейки файлов."""
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()


# ──────────────────────────── учёт результатов ───────────────────────────

def record(step, title, state, detail=""):
    RESULTS.append((step, title, state, detail))
    print(f"  [{state}] {detail}" if detail else f"  [{state}]")
    return state == OK


def banner(step, title):
    print(f"\n=== {step}. {title} ===")


def determinism(step, title, args, needle, idx, label):
    """Команда выполняется дважды; хеш обязан совпасть."""
    banner(step, title)
    rc1, out1 = run(*args, "--load")
    if rc1 != 0:
        print(indent(tail(out1, 15)))
        return record(step, title, FAIL, f"первый запуск: {rc_detail(rc1)}")
    rc2, out2 = run(*args)
    if rc2 != 0:
        print(indent(tail(out2, 15)))
        return record(step, title, FAIL, f"второй запуск: {rc_detail(rc2)}")
    h1, h2 = field(out1, needle, idx), field(out2, needle, idx)
    if h1 is None or h2 is None:
        return record(step, title, FAIL, f"в выводе нет «{needle}»")
    if h1 != h2:
        return record(step, title, FAIL, f"{label}: {h1[:24]}… != {h2[:24]}…")
    return record(step, title, OK, f"{label}: PASS {h1[:24]}…")


def script_step(step, title, args, lines=2, mode="tail", count=False):
    """Обычный шаг: код возврата обязан быть нулевым."""
    banner(step, title)
    rc, out = run(*args)
    if count:
        CHECKS[0] += count_checks(out)
    shown = tail(out, lines) if mode == "tail" else head(out, lines)
    print(indent(shown if shown.strip() else out.strip()))
    if rc != 0:
        return record(step, title, FAIL, rc_detail(rc))
    return record(step, title, OK)


# ───────────────────────────────── шаги ──────────────────────────────────

def ensure_db():
    """На Linux кластер может лежать; на Windows это служба, её не трогаем."""
    banner("0", "доступность PostgreSQL")
    helper = ROOT / "scripts" / "ensure_pg.sh"
    for attempt in (1, 2):
        try:
            import psycopg
            from core import config
            with psycopg.connect(config.dsn("ro")) as c, c.cursor() as cur:
                cur.execute("SELECT version()")
                ver = cur.fetchone()[0]
            return record("0", "доступность PostgreSQL", OK, ver.split(",")[0])
        except Exception as exc:
            if attempt == 1 and os.name != "nt" and helper.exists():
                subprocess.run(["bash", str(helper)], cwd=str(ROOT),
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                continue
            from core import config
            return record("0", "доступность PostgreSQL", FAIL,
                          f"{type(exc).__name__}: {config.redact(exc)}")


def step_normalize():
    banner("1", "нормализация raw -> JSONL (детерминизм)")
    rc, out = run("normalize/normalize.py")
    print(indent(head(out, 2)))
    if rc != 0:
        return record("1", "нормализация", FAIL, rc_detail(rc))
    files = [ROOT / "data" / "videos.jsonl"]
    files += sorted((ROOT / "data" / "snapshots").glob("*.jsonl"))
    h1 = sha256_of(files)
    rc, out = run("normalize/normalize.py")
    if rc != 0:
        return record("1", "нормализация", FAIL, f"повтор: {rc_detail(rc)}")
    h2 = sha256_of(files)
    if h1 != h2:
        return record("1", "нормализация", FAIL,
                      f"JSONL недетерминирован: {h1[:16]} != {h2[:16]}")
    return record("1", "нормализация", OK, f"JSONL детерминирован ({h2[:16]})")


def step_rebuild(do_rebuild):
    """Пересборка БД с нуля из JSONL. Разрушительна, потому только по просьбе."""
    title = "пересборка БД с нуля исключительно из JSONL"
    banner("3", title)
    if not do_rebuild:
        print("  Пересборка требует DROP DATABASE. Роли проекта созданы")
        print("  в 0001 как NOCREATEDB и выполнить её не могут: нужен")
        print("  суперпользовательский DSN в TIKTOK_DSN_SUPER и явный ключ")
        print("  --rebuild. Существующая база НЕ изменена.")
        record("3", title, SKIP, "запрошено не было")
        # Непересборочная замена: слепок состояния обязан быть устойчив.
        banner("3a", "слепок состояния воспроизводим (без пересборки)")
        rc1, o1 = run("db/state_hash.py")
        rc2, o2 = run("db/state_hash.py")
        if rc1 or rc2:
            return record("3a", "слепок состояния", FAIL,
                          f"state_hash: {rc_detail(rc1)} / {rc_detail(rc2)}")
        s1, s2 = last_field_of_last_line(o1), last_field_of_last_line(o2)
        if not s1 or s1 != s2:
            return record("3a", "слепок состояния", FAIL, f"{s1} != {s2}")
        return record("3a", "слепок состояния", OK,
                      f"устойчив {s1[:24]}…  (инвариант пересборки НЕ проверен)")
    return rebuild_and_compare(title)


def rebuild_and_compare(title):
    dsn_super = os.environ.get("TIKTOK_DSN_SUPER")
    if not dsn_super:
        return record("3", title, FAIL,
                      "--rebuild запрошен, но TIKTOK_DSN_SUPER не задан")
    import psycopg
    from psycopg import conninfo as ci
    from core import config
    db = os.environ.get("TIKTOK_DB", "tiktok_manager")

    def recreate():
        parts = ci.conninfo_to_dict(dsn_super)
        maint = ci.make_conninfo(**{**parts, "dbname": "postgres"})
        with psycopg.connect(maint, autocommit=True) as c, c.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname=%s AND pid <> pg_backend_pid()", (db,))
            cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
            cur.execute(f'CREATE DATABASE "{db}"')
            cur.execute(f'ALTER DATABASE "{db}" OWNER TO tiktok_owner')
        fresh = ci.make_conninfo(**{**parts, "dbname": db})
        with psycopg.connect(fresh, autocommit=True) as c, c.cursor() as cur:
            cur.execute("ALTER SCHEMA public OWNER TO tiktok_owner")
            cur.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    def refill():
        for args in (["db/migrate.py"], ["db/load.py"],
                     ["reconcile/run.py", "--load"], ["analytics/run.py", "--load"],
                     ["features/run.py", "--load"], ["-m", "assets.ingest", "--load"],
                     ["insights/run.py", "--load"]):
            rc, out = run(*args)
            if rc != 0:
                print(indent(tail(out, 15)))
                raise RuntimeError(f"{' '.join(args)}: {rc_detail(rc)}")

    try:
        recreate(); refill()
        rc, o1 = run("db/state_hash.py")
        s1 = last_field_of_last_line(o1)
        recreate(); refill()
        rc, o2 = run("db/state_hash.py")
        s2 = last_field_of_last_line(o2)
    except Exception as exc:
        return record("3", title, FAIL, f"{type(exc).__name__}: {exc}")
    if not s1 or s1 != s2:
        return record("3", title, FAIL, f"слепки разошлись: {s1} != {s2}")
    return record("3", title, OK, f"REBUILD INVARIANT: PASS {s1[:24]}…")


def step_extractors():
    """Инструменты промера. Их отсутствие — не провал: Tier 1 честно
    отдаёт unavailable. Шаг никогда не валит сборку."""
    banner("6в", "инструменты промера видео")
    rc, _ = run("-c", "import cv2, numpy, imageio_ffmpeg")
    if rc == 0:
        print("  инструменты промера уже установлены")
    else:
        print("  установка из requirements-extract.txt")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "-r",
                        "requirements-extract.txt"],
                       cwd=str(ROOT), env=child_env(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rc, out = run("-c", "import sys; sys.path.insert(0,'.')\n"
                        "from assets import probe\n"
                        "t = probe.tooling()\n"
                        "[print(f'  {k:<10}{v or \"ОТСУТСТВУЕТ\"}') "
                        "for k, v in sorted(t.items())]\n"
                        "print(f'  extractor_version: {probe.extractor_version(t)}')")
    print(out.rstrip())
    return record("6в", "инструменты промера", OK, "шаг не влияет на итог")


def step_publishing():
    banner("14", "способность публикации")
    code = ("import sys; sys.path.insert(0,'.')\n"
            "import psycopg\n"
            "from core import config\n"
            "with psycopg.connect(config.dsn('ro')) as c, c.cursor() as cur:\n"
            "    cur.execute(\"SELECT enabled FROM system_capabilities"
            " WHERE capability='publishing.submit'\")\n"
            "    row = cur.fetchone()\n"
            "print(row[0] if row else 'НЕТ ЗАПИСИ')\n"
            "raise SystemExit(0 if row and row[0] is False else 1)")
    rc, out = run("-c", code)
    value = scrub_text(out.strip().splitlines()[-1]) if out.strip() else "?"
    if rc != 0:
        return record("14", "publishing.submit", FAIL,
                      f"publishing.submit = {value} (обязано быть False)")
    return record("14", "publishing.submit", OK, "publishing.submit = False")


TESTS = [
    ("7",    "тесты Phase 1",                             "tests/test_phase1.py"),
    ("8",    "тесты Phase 2 (движок сверки)",             "tests/test_reconcile_engine.py"),
    ("9",    "тесты Phase 3 (аналитика)",                 "tests/test_analytics.py"),
    ("11",   "тесты Phase 4 (признаки)",                  "tests/test_features.py"),
    ("11б",  "тесты хранилища признаков",                 "tests/test_feature_storage.py"),
    ("11в",  "тесты Phase 5 (выводы и отчёт)",            "tests/test_insights.py"),
    ("11г",  "тесты Phase 5.1 (механическая зависимость)", "tests/test_mechanical_dependency.py"),
    ("11д",  "регрессия Phase 5.1 hardening (A-G + порядок)", "tests/test_phase51_hardening.py"),
    ("11е",  "тесты Phase 6 (ассеты и Tier 1)",           "tests/test_phase6_assets.py"),
    ("11ж",  "тесты Phase 6.5 (мобильный пульт)",         "tests/test_phase65_mobile.py"),
    ("11и",  "переносимая сборка не беднее оболочечной",  "tests/test_verify_all_portable.py"),
    ("11к",  "советник и меню (без обращения к API)",     "tests/test_advisor.py"),
]


def count_checks(text):
    """Число проверок из итоговой строки дочернего сценария.

    Формулировка не единая: большинство печатает «проверок: N», а
    tests/test_reconciliation.py — «тестов: N». Учитываем обе, иначе
    итог тихо занижается. Отдельно считаем строки вида «[OK] …» там,
    где итоговой строки нет вовсе (проверка EXP-004).
    """
    for line in text.splitlines():
        for word in ("проверок:", "тестов:"):
            if word in line:
                parts = line.replace("|", " ").split()
                try:
                    return int(parts[parts.index(word) + 1])
                except (ValueError, IndexError):
                    return 0
    return sum(1 for ln in text.splitlines() if "[OK]" in ln)


def main(argv):
    ap = argparse.ArgumentParser(
        description="Полная проверка проекта (Windows и Linux).")
    ap.add_argument("--rebuild", action="store_true",
                    help="пересобрать БД с нуля из JSONL; требует "
                         "TIKTOK_DSN_SUPER, выполняет DROP DATABASE")
    args = ap.parse_args(argv)

    print("=" * 72)
    print(f"  проект: {ROOT}")
    print(f"  python: {sys.version.split()[0]}  ({sys.executable})")
    print(f"  платформа: {sys.platform}")
    print("=" * 72)

    ensure_db()
    step_normalize()
    script_step("2", "наблюдения из сырья", ["normalize/observations.py"],
                lines=2, mode="head")
    step_rebuild(args.rebuild)
    script_step("3б", "схема соответствует миграциям",
                ["db/verify_schema.py"], count=True)
    determinism("4", "сверка источников", ["reconcile/run.py"],
                "content_hash", 1, "RECONCILIATION DETERMINISM")
    determinism("5", "аналитика", ["analytics/run.py"],
                "analytics_hash", 1, "ANALYTICS DETERMINISM")
    determinism("6", "слой признаков (JSONL + PostgreSQL)", ["features/run.py"],
                "feature_hash:", 1, "FEATURE DETERMINISM")
    step_extractors()
    script_step("6г", "локальный ингест видео (одна команда)",
                ["-m", "assets.ingest", "--load"], lines=12)
    determinism("6б", "выводы и ежедневный отчёт", ["insights/run.py"],
                "insights_hash:", 1, "INSIGHTS DETERMINISM")

    for step, title, path in TESTS:
        banner(step, title)
        rc, out = run(path)
        print(indent(tail(out, 2)))
        CHECKS[0] += count_checks(out)
        if rc != 0:
            print(indent(tail(out, 25)))
            record(step, title, FAIL, rc_detail(rc))
        else:
            record(step, title, OK)

    script_step("11з", "стартовая проверка Telegram (семантическая)",
                ["-m", "mobile.verify"], lines=5)
    script_step("10", "регрессия EXP-004",
                ["tests/test_exp004_regression.py"], count=True)
    script_step("12", "правило both_lagged (LAG-1..LAG-7)",
                ["tests/test_reconciliation.py"], count=True)
    script_step("13", "воспроизводимость EXP-004",
                ["experiments/EXP-004/exp004.py", "verify"], lines=6, count=True)
    step_publishing()

    print("\n" + "=" * 72)
    failed = [r for r in RESULTS if r[2] == FAIL]
    skipped = [r for r in RESULTS if r[2] == SKIP]
    print(f"  шагов: {len(RESULTS)} | провалов: {len(failed)} |"
          f" пропущено: {len(skipped)}")
    print(f"  проверок внутри сценариев: {CHECKS[0]}")
    for step, title, _, detail in skipped:
        print(f"  ПРОПУЩЕН {step}. {title} — {detail}")
    for step, title, _, detail in failed:
        print(f"  ПРОВАЛ   {step}. {title} — {detail}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
