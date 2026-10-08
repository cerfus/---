#!/usr/bin/env python3
"""Готова ли программа к работе — и что сделать, если нет.

    python scripts/doctor.py

Каждая проверка либо проходит, либо говорит, ЧТО сделать: команду, а не
трассировку. Ничего не устанавливает и ничего не меняет — только смотрит.
Подключение к базе — с коротким таймаутом: на Windows обращение к
недоступному серверу может висеть долго, и проверка готовности не имеет
права зависнуть сама.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL, INFO = "OK", "!!", "XX", "--"
CONNECT_TIMEOUT_SEC = 3


def _jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def db_lags(db_videos, db_last, repo_videos, repo_last):
    """Отстаёт ли база от данных проекта: меньше роликов или старее замеры."""
    return db_videos < repo_videos or (db_last or "")[:10] < (repo_last or "")[:10]


def checks():
    """[(состояние, что проверено, что сделать)]. Ничего не печатает."""
    out = []
    add = lambda st, what, fix="": out.append((st, what, fix))

    v = sys.version_info
    add(OK if v >= (3, 11) else FAIL, f"Python {v.major}.{v.minor}",
        "" if v >= (3, 11) else "нужен Python 3.11 или новее: python.org/downloads")

    videos = ROOT / "data" / "videos.jsonl"
    if not videos.exists():
        add(FAIL, "данные проекта", "нет data/videos.jsonl — запускайте из папки проекта, "
                                     "затем git pull")
        return out
    repo_videos = len(_jsonl(videos))
    add(OK, f"данные проекта: роликов {repo_videos}")

    try:
        from zoneinfo import ZoneInfo
        ZoneInfo("Europe/Moscow")
        add(OK, "часовые пояса (московское время в разборе)")
    except Exception:
        add(WARN, "часовые пояса недоступны — разбор без московского времени",
            "python -m pip install -r requirements.txt")

    try:
        import psycopg                                     # noqa: F401
        has_pg = True
        add(OK, "драйвер PostgreSQL (psycopg)")
    except ImportError:
        has_pg = False
        add(WARN, "нет драйвера PostgreSQL — статус и отчёт из базы недоступны, "
                  "разбор, идеи и дашборд работают", "python -m pip install -r requirements.txt")

    env = ROOT / ".env"
    if not env.exists() and not os.environ.get("TIKTOK_DSN_RO"):
        add(WARN, "нет файла .env с подключением к базе",
            "скопируйте .env.example в .env и впишите пароли ролей")
        has_env = False
    else:
        has_env = True

    if has_pg and has_env:
        from core import config
        try:
            import psycopg
            with psycopg.connect(config.dsn("ro"), connect_timeout=CONNECT_TIMEOUT_SEC) as c, \
                    c.cursor() as cur:
                cur.execute("SELECT count(*) FROM videos")
                db_videos = cur.fetchone()[0]
                cur.execute("SELECT max(observed_at) FROM video_snapshots")
                db_last = cur.fetchone()[0]
            add(OK, f"база отвечает: роликов {db_videos}")
            repo_last = max(s["observed_at"] for f in sorted((ROOT / "data" / "snapshots")
                                                             .glob("*.jsonl"))
                            for s in _jsonl(f))
            db_last_s = db_last.isoformat() if db_last else ""
            if db_lags(db_videos, db_last_s, repo_videos, repo_last):
                add(WARN, f"база отстаёт от данных проекта: в базе {db_videos} роликов "
                          f"по {db_last_s[:10] or '—'}, в проекте {repo_videos} по "
                          f"{repo_last[:10]}",
                    "меню → «Обновить программу из GitHub» (или «Обновить базу "
                    "после ручного git pull»)")
            else:
                add(OK, "база в актуальном состоянии")
        except Exception as exc:
            add(WARN, f"база не отвечает ({type(exc).__name__}) — статус и отчёт "
                      "недоступны, остальное работает",
                "проверьте, что служба PostgreSQL запущена и пароли в .env верны")

    import shutil
    add(OK if shutil.which("git") else INFO,
        "git — для пункта «Обновить программу из GitHub»" if shutil.which("git")
        else "git не найден — обновлять придётся вручную",
        "" if shutil.which("git") else "по желанию: Git for Windows, git-scm.com")

    try:
        import anthropic                                   # noqa: F401
        from core import config
        config.load_dotenv()
        key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        add(OK if key else INFO,
            "идеи от модели Claude: " + ("ключ задан" if key else "ключа нет — идеи шаблонами"),
            "" if key else "по желанию: ANTHROPIC_API_KEY в .env")
    except ImportError:
        add(INFO, "идеи от модели Claude не подключены — идеи шаблонами",
            "по желанию: python -m pip install -r requirements-advisor.txt")
    return out


def render(results, brief=False):
    bad = [r for r in results if r[0] in (WARN, FAIL)]
    if brief:
        if not bad:
            return "  Готово к работе."
        lines = ["  Перед работой:"]
        for st, what, fix in bad:
            lines.append(f"  [{st}] {what}" + (f"\n       → {fix}" if fix else ""))
        return "\n".join(lines)
    lines = ["ГОТОВНОСТЬ ПРОГРАММЫ", ""]
    for st, what, fix in results:
        lines.append(f"  [{st}] {what}" + (f"\n       → {fix}" if fix else ""))
    lines += ["", "  Всё готово." if not bad else
              f"  Есть что поправить: {len(bad)}. Команды — после стрелок."]
    return "\n".join(lines)


def main():
    res = checks()
    print(render(res))
    return 1 if any(r[0] == FAIL for r in res) else 0


if __name__ == "__main__":
    from core import console
    console.setup()
    sys.exit(main())
