#!/usr/bin/env python3
"""Переносимая проверка сборки: .bat/.py не слабее .sh.

Зачем этот файл. Проверка существует теперь в двух видах — оболочечном
(Linux) и переносимом (Windows и Linux). Разойтись им нельзя: шаг,
добавленный в .sh и забытый в .py, означал бы, что на Windows проверка
молча меньше. Ровно так уже случалось — ISSUE-2, когда set -e обрывал
сборку и последние четыре шага не выполнялись, а отчёт об этом молчал.

Поэтому здесь сверяется состав: каждый сценарий и каждый модуль,
упомянутый в .sh, обязан быть упомянут и в .py. Обратное не требуется —
переносимый вариант вправе проверять больше.
"""
import ast
import importlib.util
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SH = ROOT / "scripts" / "verify_all.sh"
PY = ROOT / "scripts" / "verify_all.py"
BAT = ROOT / "scripts" / "verify_all.bat"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def scripts_in(text):
    """Все упомянутые .py-сценарии."""
    return set(re.findall(r"[A-Za-z0-9_./-]+\.py", text))


def modules_in(text):
    """Все вызовы вида `-m пакет.модуль`, в обоих синтаксисах."""
    return set(re.findall(r'-m["\s,]+([A-Za-z0-9_.]+)', text))


def code_only(src):
    """Исходник без комментариев и строк документации.

    Искать запрещённые вызовы простым вхождением подстроки нельзя: слова
    «sha256sum» и «awk» стоят в пояснениях к тому, ЧЕМ они заменены, и
    наивная проверка ловила бы собственный комментарий вместо вызова.
    Токенизатор отделяет код от текста надёжно — в отличие от регулярного
    выражения, он не спутает решётку внутри строки с началом комментария.
    """
    doc_lines = set()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                doc = node.body[0]
                doc_lines.update(range(doc.lineno, doc.end_lineno + 1))
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.start[0] in doc_lines:
            continue
        out.append(tok.string)
    return " ".join(out)


SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}


def executables_invoked(src):
    """Чем именно запускаются дочерние процессы.

    Искать слово «python3» вхождением подстроки нельзя: строка
    `#!/usr/bin/env python3` — это shebang, адресованный ядру при прямом
    запуске файла, а не вызов интерпретатора из кода. Подстрока их не
    различает, и проверка ложно срабатывала на собственной первой строке
    драйвера. Поэтому у каждого subprocess-вызова берётся первый элемент
    списка аргументов — то самое, что станет исполняемым файлом.
    """
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name not in SPAWNERS:
            continue
        first = node.args[0]
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
            first = first.elts[0]
        out.append(first)
    return out


def describes_sys_executable(node):
    """Узел вида sys.executable."""
    return (isinstance(node, ast.Attribute) and node.attr == "executable"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def literal_of(node):
    """Строковая константа узла либо None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(
        node.value, str) else None


def function_source(src, name):
    """Текст одной функции — чтобы проверить именно её охрану."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def bat_commands(text):
    """Строки .bat без комментариев REM."""
    return "\n".join(l for l in text.splitlines()
                     if not l.strip().lower().startswith("rem"))


def main():
    sh_text = SH.read_text(encoding="utf-8")
    py_text = PY.read_text(encoding="utf-8")
    bat_bytes = BAT.read_bytes()

    print("=== A. состав шагов не беднее оболочечного варианта ===")
    sh_scripts = scripts_in(sh_text)
    py_scripts = scripts_in(py_text)
    # сам себя .py упоминать не обязан
    sh_scripts -= {"scripts/verify_all.py"}
    missing = sorted(s for s in sh_scripts if s not in py_scripts)
    check("A1 все сценарии из .sh есть в .py", not missing,
          f"нет: {missing}" if missing else f"{len(sh_scripts)} шт.")

    sh_mods, py_mods = modules_in(sh_text), modules_in(py_text)
    missing_m = sorted(m for m in sh_mods if m not in py_mods)
    check("A2 все модули из .sh есть в .py", not missing_m,
          f"нет: {missing_m}" if missing_m else f"{sorted(sh_mods)}")

    print("\n=== B. обязательные проверки на месте ===")
    for name, needle in (("B1 сверка источников", "reconcile/run.py"),
                         ("B2 аналитика", "analytics/run.py"),
                         ("B3 признаки", "features/run.py"),
                         ("B4 выводы", "insights/run.py"),
                         ("B5 схема БД", "db/verify_schema.py"),
                         ("B6 слепок состояния", "db/state_hash.py"),
                         ("B7 нормализация", "normalize/normalize.py"),
                         ("B8 наблюдения", "normalize/observations.py"),
                         ("B9 ингест ассетов", "assets.ingest"),
                         ("B10 регрессия EXP-004", "test_exp004_regression.py"),
                         ("B11 both_lagged", "test_reconciliation.py"),
                         ("B12 воспроизводимость EXP-004", "exp004.py")):
        check(name, needle in py_text)
    check("B13 проверка publishing.submit", "publishing.submit" in py_text)
    check("B14 publishing.submit сверяется с False", "is False" in py_text)

    print("\n=== C. код возврата не подавлен (урок ISSUE-2) ===")
    mobile_lines = [l for l in py_text.splitlines() if "mobile." in l]
    check("C1 вызывается семантическая mobile.verify",
          any("mobile.verify" in l for l in mobile_lines))
    check("C2 сырой --check напрямую не вызывается",
          not any("mobile.telegram" in l and "--check" in l
                  for l in mobile_lines))
    check("C3 нет подавления через «|| true»", "|| true" not in py_text)
    check("C4 провал даёт ненулевой код",
          "return 1 if failed else 0" in py_text)

    print("\n=== D. поведение драйвера ===")
    spec = importlib.util.spec_from_file_location("va_portable", PY)
    va = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(va)

    check("D1 фильтр head равен sed -n '1,2p'", va.head("a\nb\nc", 2) == "a\nb")
    check("D2 фильтр tail равен tail -2", va.tail("a\nb\nc", 2) == "b\nc")
    check("D3 field равен grep|awk '{print $2}'",
          va.field("шум\n  h: ЗНАЧ\n", "h:", 1) == "ЗНАЧ")
    check("D4 last_field равен tail -1|awk '{print $NF}'",
          va.last_field_of_last_line("a b\n  СЛЕПОК: ZZ\n") == "ZZ")
    check("D5 count_checks понимает «проверок»",
          va.count_checks("проверок: 34 | провалов: 0") == 34)
    check("D6 count_checks понимает «тестов»",
          va.count_checks("тестов: 14 | провалов: 0") == 14)

    # секреты не должны просачиваться в вывод: его копируют целиком
    dirty = "postgresql://tiktok_ro:РеальныйПароль@localhost:5432/db"
    check("D7 пароль из DSN вычищается",
          "РеальныйПароль" not in va.scrub_text(dirty), va.scrub_text(dirty))
    tok = "123456789:AAErTyUiOpAsDfGhJkLzXcVbNmQwErTyUiOp"
    check("D8 токен Telegram вычищается", tok not in va.scrub_text(tok))

    print("\n=== E. .bat пригоден для cmd.exe ===")
    try:
        bat_bytes.decode("ascii")
        ascii_ok = True
    except UnicodeDecodeError:
        ascii_ok = False
    # кириллица в .bat разбирается в кодовой странице консоли и ломается;
    # весь русский текст поэтому печатает Python, а не сам .bat
    check("E1 файл целиком ASCII", ascii_ok)
    check("E2 перевод строки CRLF",
          bat_bytes.count(b"\r\n") > 0
          and bat_bytes.count(b"\n") == bat_bytes.count(b"\r\n"))
    check("E3 нет BOM", not bat_bytes.startswith(b"\xef\xbb\xbf"))
    bat = bat_bytes.decode("ascii")
    check("E4 вызывает переносимый драйвер", "verify_all.py" in bat)
    check("E5 передаёт аргументы дальше", "%*" in bat)
    check("E6 возвращает код драйвера", "exit /b %RC%" in bat)
    # слово bash стоит в пояснении «bash не нужен»; проверяем команды,
    # а не комментарии, иначе тест ловил бы собственный текст файла
    cmds = bat_commands(bat).lower()
    check("E7 в командах нет bash и wsl",
          "bash" not in cmds and "wsl" not in cmds, cmds.strip()[:60])
    check("E8 не зовёт python3", "python3" not in cmds)
    check("E9 не пользуется sed/grep/awk",
          not any(w in cmds for w in ("sed ", "grep ", "awk ", "findstr")))
    # .bat здесь выполнить нечем, поэтому конструкция, которую cmd.exe
    # разбирает ненадёжно, запрещена заранее: скобочный блок IF со
    # скобкой внутри кавычек. Поток управления построен на GOTO.
    blocks = [l for l in bat_commands(bat).splitlines()
              if l.rstrip().endswith("(")]
    check("E10 нет скобочных блоков IF", not blocks, str(blocks))

    print("\n=== F. переносимость драйвера ===")
    py_code = code_only(py_text)
    # bash допустим ровно в одном месте — запасной подъём кластера на
    # Linux, и обязан быть закрыт проверкой на Windows
    ensure = function_source(py_text, "ensure_db")
    check("F1a bash встречается только в ensure_db",
          py_code.count('"bash"') == code_only(ensure).count('"bash"'),
          f"всего {py_code.count(chr(34) + 'bash' + chr(34))}")
    check("F1b вызов bash закрыт проверкой на Windows",
          'os.name != "nt"' in ensure)
    # F2 смотрит на исполняемые вызовы, а не на текст файла: shebang в
    # первой строке — не запуск интерпретатора и в расчёт не берётся
    execs = executables_invoked(py_text)
    py_runners = [e for e in execs if describes_sys_executable(e)]
    hardcoded = [literal_of(e) for e in execs if literal_of(e)]
    check("F2a дочерний Python берётся из sys.executable",
          len(py_runners) >= 1, f"таких вызовов: {len(py_runners)}")
    check("F2b python3 как исполняемый файл не вызывается",
          "python3" not in hardcoded, str(hardcoded))
    check("F2c python тоже не зашит строкой",
          "python" not in hardcoded, str(hardcoded))
    first_line = py_text.lstrip("﻿").splitlines()[0]
    py3_lines = [i for i, l in enumerate(py_text.splitlines(), 1)
                 if "python3" in l]
    check("F2d python3 встречается только в shebang",
          py3_lines in ([], [1]) and first_line.startswith("#!"),
          f"строки: {py3_lines}")
    check("F3 нет вызовов sed/grep/awk/sha256sum",
          not any(w in py_code for w in ("sha256sum", "sed ", "awk", "grep")),
          "проверено по коду без комментариев")
    check("F4 пересборка БД по умолчанию не выполняется",
          "do_rebuild" in py_text and 'action="store_true"' in py_text)
    check("F5 пропущенный шаг помечается, а не выдаётся за пройденный",
          'SKIP = "OK", "FAIL", "ПРОПУЩЕН"' in py_text)

    print("\n=== G. поведение на Windows проверено запуском ===")
    # Не чтением исходника, а исполнением: подменяем os.name и убеждаемся,
    # что запасной путь через bash на Windows не выбирается ни при каком
    # состоянии базы. Подключение заведомо ломаем — это и есть ветка,
    # в которой .sh-вариант полез бы поднимать кластер.
    import os as _os
    import subprocess as _sp

    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _sp.CompletedProcess(args, 0, "", "")

    class OsWithName:
        """Подмена ТОЛЬКО ссылки внутри драйвера.

        Присвоить os.name напрямую нельзя: модуль один на весь процесс, и
        значение «nt» тут же ломает pathlib и ctypes — проверено, падало
        импортом psycopg. Поэтому подменяется имя в пространстве драйвера,
        а настоящий модуль остаётся нетронутым.
        """
        def __init__(self, real, name):
            self._real, self.name = real, name

        def __getattr__(self, item):
            return getattr(self._real, item)

    import contextlib

    def quiet(fn):
        """Собственный вывод ensure_db здесь не нужен.

        Соединение ломается намеренно, и её «[FAIL] база недоступна» —
        ожидаемый результат опыта, а не провал теста. В отчёте эта строка
        читалась бы как настоящая поломка, поэтому глушится.
        """
        with contextlib.redirect_stdout(io.StringIO()):
            return fn()

    # Отказ соединения изображается подменой psycopg.connect, а НЕ
    # заведомо мёртвым адресом. Живого сокета тут быть не должно: на
    # Linux порт 1 отвечает отказом за 2 мс, а на Windows обращение к
    # закрытому порту может висеть до таймаута стека — из-за этого шаг
    # 11и и замирал. Подмена делает опыт мгновенным и одинаковым всюду.
    import psycopg as _pg

    def refuse(*a, **k):
        raise _pg.OperationalError("проверочный отказ: база недоступна")

    saved_os = va.os
    saved_run = va.subprocess.run
    saved_connect = _pg.connect
    try:
        _pg.connect = refuse
        va.subprocess.run = fake_run

        va.os = OsWithName(saved_os, "nt")
        va.RESULTS.clear(); calls.clear()
        quiet(va.ensure_db)
        win_bash = [c for c in calls if c and str(c[0]) == "bash"]
        check("G1 на Windows bash не вызывается ни разу", not win_bash,
              str(win_bash))
        check("G2 недоступная база — это провал, а не молчание",
              va.RESULTS and va.RESULTS[-1][2] == va.FAIL)

        va.os = OsWithName(saved_os, "posix")
        va.RESULTS.clear(); calls.clear()
        quiet(va.ensure_db)
        nix_bash = [c for c in calls if c and str(c[0]) == "bash"]
        check("G3 на Linux запасной подъём кластера сохранён", bool(nix_bash),
              str(nix_bash[:1]))
    finally:
        va.os = saved_os
        va.subprocess.run = saved_run
        _pg.connect = saved_connect
        va.RESULTS.clear()

    print("\n=== H. зависший шаг снимается, проверка идёт дальше ===")
    check("H1 у дочерних запусков есть потолок времени",
          "timeout=limit" in py_text and "TimeoutExpired" in py_text)
    check("H2 потолок задан с запасом к самому тяжёлому шагу",
          va.STEP_TIMEOUT_SEC >= 120, f"{va.STEP_TIMEOUT_SEC} с")

    import tempfile
    hang = Path(tempfile.mkdtemp()) / "hang.py"
    hang.write_text("import time\nprint('поехали')\ntime.sleep(600)\n",
                    encoding="utf-8")

    started = __import__("time").time()
    rc, out = va.run(str(hang), timeout=2)
    spent = __import__("time").time() - started
    check("H3 зависший процесс снимается, а не ждётся вечно",
          rc == va.RC_TIMEOUT and spent < 60, f"код {rc}, {spent:.1f} с")
    check("H4 накопленный вывод сохраняется", "поехали" in out)
    check("H5 таймаут объясняется словами, а не кодом 124",
          "таймаут" in va.rc_detail(va.RC_TIMEOUT).lower()
          or "не уложился" in va.rc_detail(va.RC_TIMEOUT))

    # Главное: после снятого шага проверка продолжается, а не падает.
    # Потолок на время опыта опускается до 2 с — иначе сам тест ждал бы
    # штатные 600 и воспроизвёл бы ровно ту беду, которую проверяет.
    good = hang.parent / "ok.py"
    good.write_text("print('проверок: 1 | провалов: 0')\n", encoding="utf-8")
    saved_limit = va.STEP_TIMEOUT_SEC
    va.RESULTS.clear()
    try:
        va.STEP_TIMEOUT_SEC = 2
        with contextlib.redirect_stdout(io.StringIO()):
            stuck = va.script_step("H", "зависающий шаг", [str(hang)])
            nxt = va.script_step("H+", "следующий шаг", [str(good)])
    finally:
        va.STEP_TIMEOUT_SEC = saved_limit
    check("H6 снятый шаг — это FAIL",
          stuck is False and va.RESULTS[0][2] == va.FAIL, str(va.RESULTS[0][3]))
    check("H7 после снятого шага выполняется следующий",
          nxt is True and len(va.RESULTS) == 2,
          f"шагов записано: {len(va.RESULTS)}")
    va.RESULTS.clear()

    print("\n=== I. файлы одинаковы на Windows и Linux ===")
    # Python на Windows в текстовом режиме пишет \r\n вместо \n, а str(path)
    # даёт обратные слеши. Оба эффекта меняли производные файлы при каждом
    # запуске на ПК, и git pull отказывался обновлять проект. Запустить здесь
    # Windows нечем, поэтому правило проверяется по исходникам: каждая запись
    # в текстовом режиме обязана явно задать newline="\n", а путь в данные —
    # писаться через as_posix().
    bad_writes, bad_paths = [], []
    for f in sorted(ROOT.rglob("*.py")):
        if "tests" in f.parts or "__pycache__" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = n.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            kw = {k.arg: k.value for k in n.keywords}
            if name == "write_text" or name == "open":
                mode = None
                if name == "open":
                    args = n.args if isinstance(fn, ast.Attribute) else n.args[1:]
                    if args and isinstance(args[0], ast.Constant):
                        mode = args[0].value
                    if isinstance(kw.get("mode"), ast.Constant):
                        mode = kw["mode"].value
                    if not (isinstance(mode, str) and ("w" in mode or "a" in mode)
                            and "b" not in mode):
                        continue
                nl = kw.get("newline")
                if not (isinstance(nl, ast.Constant) and nl.value == "\n"):
                    bad_writes.append(f"{f.relative_to(ROOT).as_posix()}:{n.lineno}")
            if (name == "str" and n.args and isinstance(n.args[0], ast.Call)
                    and isinstance(n.args[0].func, ast.Attribute)
                    and n.args[0].func.attr == "relative_to"):
                bad_paths.append(f"{f.relative_to(ROOT).as_posix()}:{n.lineno}")
    check("I1 каждая запись в текстовом режиме задаёт newline=\"\\n\"",
          not bad_writes, str(bad_writes[:5]))
    check("I2 путь в данные пишется через as_posix(), а не str(...relative_to)",
          not bad_paths, str(bad_paths[:5]))

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
