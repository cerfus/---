#!/usr/bin/env python3
"""Семантическая проверка стартового контроля мобильного слоя.

    python3 -m mobile.verify

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. `python3 -m mobile.telegram --check` возвращает
ненулевой код, когда интеграция выключена, — и это правильно: наружу
сообщается «стартовать нельзя». Но для сборки такой код означал бы провал,
а отсутствие Telegram провалом не является.

Соблазнительный ответ `|| true` неверен: он гасит ЛЮБУЮ ошибку, включая
настоящую поломку. Поэтому проверяется не код возврата сам по себе, а
СООТВЕТСТВИЕ кода ожидаемому состоянию окружения:

    настроены токен и владелец  -> --check ОБЯЗАН завершиться успешно
    не настроены                -> --check ОБЯЗАН отказать именно потому,
                                   что интеграция выключена, и сказать это
    всё остальное               -> провал сборки

Так молчаливая поломка не спрячется ни в одной из веток: падение с
трассировкой при выключенной интеграции — это «unexpected», и оно валит
проверку, хотя код возврата такой же ненулевой.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mobile import config as mobile_config

# Строка, которой выключенная интеграция обязана себя назвать.
EXPECTED_DISABLED_PREFIX = "Telegram integration: DISABLED"

VERDICT_ENABLED_OK = "enabled_check_succeeded"
VERDICT_DISABLED_OK = "disabled_as_expected"
VERDICT_ENABLED_FAILED = "enabled_check_failed"
VERDICT_DISABLED_BUT_SUCCEEDED = "disabled_but_check_succeeded"
VERDICT_UNEXPECTED_FAILURE = "unexpected_failure"

OK_VERDICTS = (VERDICT_ENABLED_OK, VERDICT_DISABLED_OK)


def evaluate(cfg, exit_code, output):
    """Сопоставить результат --check с ожидаемым состоянием окружения.

    Чистая функция: ни процессов, ни сети — поэтому все ветки проверяемы
    тестами без настоящего токена.

    Возвращает (ok, verdict, detail).
    """
    text = output or ""
    if cfg.enabled:
        if exit_code == 0:
            return True, VERDICT_ENABLED_OK, (
                "Telegram настроен, стартовая проверка прошла")
        return False, VERDICT_ENABLED_FAILED, (
            f"Telegram настроен, но стартовая проверка отказала "
            f"(код {exit_code})")

    # Интеграция выключена. Отказ ожидается, но только «правильный».
    if exit_code == 0:
        # Хуже всего: выключенная интеграция отрапортовала успех. Это
        # fail open, и молчать о нём нельзя.
        return False, VERDICT_DISABLED_BUT_SUCCEEDED, (
            "Telegram выключен, но стартовая проверка вернула успех")
    if EXPECTED_DISABLED_PREFIX not in text:
        return False, VERDICT_UNEXPECTED_FAILURE, (
            f"отказ не объяснён строкой «{EXPECTED_DISABLED_PREFIX}» "
            f"(код {exit_code})")
    if cfg.reason and cfg.reason not in text:
        return False, VERDICT_UNEXPECTED_FAILURE, (
            "причина отказа не совпала с состоянием окружения")
    return True, VERDICT_DISABLED_OK, cfg.reason


def _default_runner():
    r = subprocess.run([sys.executable, "-m", "mobile.telegram", "--check"],
                       capture_output=True, text=True, cwd=str(ROOT),
                       timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def run_check(cfg=None, runner=None):
    """Запустить --check и оценить результат. runner подменяем в тестах."""
    cfg = cfg if cfg is not None else mobile_config.load()
    code, output = (runner or _default_runner)()
    ok, verdict, detail = evaluate(cfg, code, output)
    return ok, verdict, detail, code, output


def main(argv=None):
    ok, verdict, detail, code, output = run_check()
    for line in (output or "").splitlines():
        if line.strip():
            print(line)
    print(f"preflight verdict: {verdict} (exit {code})")
    print(f"  {detail}")
    if ok:
        print("  ожидаемое состояние — проверка продолжается")
        return 0
    print("  НЕ СООТВЕТСТВУЕТ ожидаемому состоянию — проверка провалена")
    return 1


if __name__ == "__main__":
    sys.exit(main())
