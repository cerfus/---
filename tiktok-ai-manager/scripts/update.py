#!/usr/bin/env python3
"""Обновить программу из GitHub одной командой — без потери своих данных.

    python scripts/update.py             обновить и дозагрузить базу
    python scripts/update.py --no-sync   только git, без базы
    python scripts/update.py --push      отправить свои эксперименты и идеи в GitHub

Обновление по шагам:
 1. Свои файлы — журнал экспериментов и сохранённые идеи, их пишет меню на
    этом ПК — коммитятся локально. Так они не мешают git pull и не теряются.
 2. Остальные локальные изменения отслеживаемых файлов (обычно данные,
    пересчитанные на этом ПК) убираются в git stash с подписью: эталон
    лежит в GitHub, а локальная копия остаётся восстановимой.
 3. git pull слиянием, не rebase. Журнал экспериментов сливается по
    правилу union (.gitattributes): обе стороны только дописывают строки.
 4. Не слилось — слияние отменяется (git merge --abort), всё как было.
 5. Пришло новое — база дозагружается (sync_db), если она настроена.

Ничего не удаляется, история не переписывается, push — только по --push
и только своей ветки в её же upstream.
"""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GIT_TIMEOUT_SEC = int(os.environ.get("TIKTOK_GIT_TIMEOUT_SEC", "180"))
OWN_FILE = "experiments/register.jsonl"
OWN_DIR = "data/ideas/"
FALLBACK_NAME, FALLBACK_EMAIL = "AI Manager (PC)", "ai-manager@localhost"


def git(*args, cwd=ROOT):
    """(код, вывод). Без интерактивных вопросов: спрятанный запрос пароля
    повесил бы окно молча, а так git сразу скажет, чего не хватает."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SEC)
        return p.returncode, p.stdout or ""
    except FileNotFoundError:
        return 127, "git не найден"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]}: нет ответа за {GIT_TIMEOUT_SEC} с"


def _ok(*args, cwd=ROOT):
    rc, out = git(*args, cwd=cwd)
    return out.strip() if rc == 0 else None


def own(rel):
    """Файл пишет этот ПК (меню), а не GitHub."""
    return rel == OWN_FILE or rel.startswith(OWN_DIR)


def local_changes(root=ROOT):
    """(свои, чужие) — пути от корня репозитория. Чужие — только
    отслеживаемые: неотслеживаемые файлы pull не трогает."""
    prefix = _ok("rev-parse", "--show-prefix", cwd=root) or ""
    rc, out = git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".",
                  cwd=root)
    mine, other = [], []
    items = out.split("\0")
    i = 0
    while i < len(items):
        entry = items[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy or "C" in xy:          # у переименования есть второй путь
            i += 1
        rel = path[len(prefix):] if path.startswith(prefix) else path
        if own(rel):
            mine.append(path)
        elif xy != "??":
            other.append(path)
    return mine, other


def _identity(root):
    """-c user.name/-c user.email, если на ПК они не заданы: коммит своих
    файлов не должен падать из-за ненастроенного git."""
    flags = []
    if not _ok("config", "user.name", cwd=root):
        flags += ["-c", f"user.name={FALLBACK_NAME}"]
    if not _ok("config", "user.email", cwd=root):
        flags += ["-c", f"user.email={FALLBACK_EMAIL}"]
    return flags


def commit_own(paths, root=ROOT, say=print):
    if not paths:
        return True
    top = [f":(top){p}" for p in paths]
    rc, out = git("add", "--", *top, cwd=root)
    if rc == 0:
        rc, out = git(*_identity(root), "commit", "-q", "-m",
                      "Мои эксперименты и идеи (с ПК)", "--", *top, cwd=root)
    if rc != 0:
        say(f"  не удалось сохранить свои файлы в git:\n{_tail(out)}")
        return False
    say(f"  свои файлы сохранены коммитом: {len(paths)}")
    return True


def _tail(out, n=8):
    from mobile import audit
    return audit.scrub("\n".join("    " + l for l in out.strip().splitlines()[-n:]))


def _db_configured():
    try:
        import psycopg                                  # noqa: F401
    except ImportError:
        return False
    return (ROOT / ".env").exists() or bool(os.environ.get("TIKTOK_DSN_RO"))


def update(root=ROOT, sync=True, say=print):
    """0 — обновлено или уже актуально; 1 — остановлено, всё как было."""
    say("ОБНОВЛЕНИЕ ПРОГРАММЫ ИЗ GITHUB\n")
    if git("--version", cwd=root)[0] != 0:
        say("  git не найден. Установите Git for Windows (git-scm.com), "
            "затем повторите.")
        return 1
    branch = _ok("rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    if not branch or branch == "HEAD":
        say("  сейчас не выбрана ветка (detached HEAD) — обновлять нечего. "
            "Выберите ветку: git switch <имя>")
        return 1
    upstream = _ok("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=root)
    if not upstream:
        say(f"  у ветки {branch} нет связи с GitHub (upstream). "
            f"Настройте: git branch --set-upstream-to=origin/{branch}")
        return 1
    if _ok("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=root):
        say("  в репозитории незавершённое слияние — сначала git merge --abort")
        return 1
    say(f"  ветка {branch} ← {upstream}")

    mine, other = local_changes(root)
    if not commit_own(mine, root, say):
        return 1
    if other:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rc, out = git("stash", "push", "-m",
                      f"AI Manager: локальные изменения перед обновлением {stamp}",
                      "--", *[f":(top){p}" for p in other], cwd=root)
        if rc != 0:
            say(f"  не удалось отложить локальные изменения:\n{_tail(out)}")
            return 1
        say(f"  локальные изменения ({len(other)} файл.) отложены в git stash — "
            "их эталон в GitHub; вернуть: git stash pop")

    before = _ok("rev-parse", "HEAD", cwd=root)
    # слияние создаёт коммит — ему тоже нужно имя, даже если git на ПК не настроен
    rc, out = git(*_identity(root), "pull", "--no-rebase", "--no-edit", cwd=root)
    if rc != 0:
        aborted = False
        if _ok("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=root):
            aborted = git("merge", "--abort", cwd=root)[0] == 0
        say("  обновление не прошло" + (" — слияние отменено, всё как было" if aborted
                                       else "") + f":\n{_tail(out)}")
        if aborted:
            say("  Свои эксперименты и идеи сохранены коммитом — ничего не потеряно. "
                "Покажите этот текст в сессии Claude, разберём.")
        if "Authentication" in out or "could not read Username" in out:
            say("  похоже на вход в GitHub: выполните git pull в окне CMD один раз "
                "вручную — Windows запомнит вход.")
        return 1
    after = _ok("rev-parse", "HEAD", cwd=root)
    if before == after:
        say("  обновлений нет — у вас последняя версия.")
        return 0
    log = _ok("log", "--oneline", "--no-decorate", "--no-merges", "-n", "12",
              f"{before}..{after}", cwd=root) or ""
    changed = (_ok("diff", "--name-only", before, after, cwd=root) or "").splitlines()
    say(f"  получено коммитов: {len(log.splitlines())}")
    for line in log.splitlines():
        say(f"    {line[:100]}")
    if sync and _db_configured():
        say("\n  дозагрузка базы …")
        rc = subprocess.run([sys.executable, "scripts/sync_db.py"], cwd=str(ROOT)).returncode
        if rc != 0:
            say("  база не обновилась — подробности выше; программа обновлена.")
    elif sync:
        say("  база не настроена — дозагрузка пропущена.")
    if any(p.lower().endswith(".bat") for p in changed):
        say("\n  ВАЖНО: обновились файлы запуска. Закройте это окно и запустите "
            "AI-Manager.bat заново.")
    else:
        say("\n  Перезапустите меню, чтобы новые пункты появились.")
    return 0


def push(root=ROOT, say=print):
    """Отправить свои эксперименты и идеи в GitHub (своя ветка → её upstream)."""
    say("ОТПРАВКА СВОИХ ЭКСПЕРИМЕНТОВ И ИДЕЙ В GITHUB\n")
    if not _ok("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=root):
        say("  у ветки нет связи с GitHub (upstream) — отправлять некуда.")
        return 1
    mine, _ = local_changes(root)
    if not commit_own(mine, root, say):
        return 1
    ahead = _ok("rev-list", "--count", "@{u}..HEAD", cwd=root)
    if ahead == "0":
        say("  отправлять нечего — в GitHub уже всё есть.")
        return 0
    rc, out = git("push", cwd=root)
    if rc != 0:
        say(f"  GitHub не принял отправку:\n{_tail(out)}")
        if "rejected" in out or "fetch first" in out:
            say("  в GitHub есть новое: сначала пункт «Обновить программу из GitHub», "
                "потом отправка.")
        return 1
    say(f"  отправлено коммитов: {ahead}")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Обновление программы из GitHub.")
    ap.add_argument("--no-sync", action="store_true", help="не дозагружать базу")
    ap.add_argument("--push", action="store_true",
                    help="отправить свои эксперименты и идеи в GitHub")
    args = ap.parse_args(argv)
    return push() if args.push else update(sync=not args.no_sync)


if __name__ == "__main__":
    from core import console
    console.setup()
    sys.exit(main())
