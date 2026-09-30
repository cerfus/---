"""Конфигурация. Секреты только из окружения или .env — никогда из кода и git."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_NAME = os.environ.get("TIKTOK_DB", "tiktok_manager")

# Имена переменных окружения с DSN для каждой роли.
DSN_VARS = {
    "owner": "TIKTOK_DSN_OWNER",
    "rw": "TIKTOK_DSN_RW",
    "ro": "TIKTOK_DSN_RO",
}


def load_dotenv(path=None):
    """Минимальный загрузчик .env без зависимостей. Значения не логируются."""
    p = Path(path or ROOT / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def dsn(role="rw"):
    load_dotenv()
    var = DSN_VARS[role]
    v = os.environ.get(var)
    if not v:
        raise RuntimeError(f"не задана переменная окружения {var} (см. .env.example)")
    return v


def redact(text):
    """Убирает пароль из строки подключения перед выводом куда бы то ни было."""
    import re
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", str(text))
