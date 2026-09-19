#!/usr/bin/env python3
"""Транспорт Telegram: долгий опрос на стандартной библиотеке.

    python3 -m mobile.telegram

ЗАВИСИМОСТЕЙ НЕ ДОБАВЛЯЕТ. Bot API — это обычный HTTPS с JSON, и для двух
методов (getUpdates, sendMessage) библиотека не нужна. Отказ от внешнего
пакета здесь не экономия, а безопасность: у слоя, держащего токен владельца
и доступ к отчётам, меньше кода из чужих рук.

Webhook не используется: он требует публичного адреса и сертификата, а
владелец работает с телефона через обычный запуск. Опрос проще и не
открывает входящий порт.

Транспорт не знает ни одной команды. Он получает апдейт, отдаёт его
роутеру и отправляет то, что тот вернул.
"""
import json
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mobile import audit, router
from mobile import config as mobile_config

API_BASE = "https://api.telegram.org/bot"
BACKOFF_START_SEC = 2
BACKOFF_MAX_SEC = 60


def log(msg):
    """Вывод приложения. Через audit.scrub — чтобы токен не попал в лог
    даже из текста чужой ошибки."""
    print(audit.scrub(str(msg)), flush=True)


class TelegramClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def _call(self, method, params=None, timeout=None):
        url = API_BASE + self.cfg.token + "/" + method
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(
                req, timeout=timeout or mobile_config.HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_updates(self, offset=None):
        params = {"timeout": mobile_config.POLL_TIMEOUT_SEC,
                  "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params)

    def send_message(self, chat_id, text):
        # parse_mode не задаётся намеренно: обычный текст Telegram не
        # разбирает, поэтому данные не могут сломать отправку.
        return self._call("sendMessage",
                          {"chat_id": chat_id, "text": text,
                           "disable_web_page_preview": "true"},
                          timeout=30)

    def get_me(self):
        return self._call("getMe", {}, timeout=30)


def chat_id_of(update):
    for key in ("message", "edited_message"):
        payload = update.get(key) if isinstance(update, dict) else None
        if isinstance(payload, dict):
            chat = payload.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                return chat["id"]
    return None


def preflight():
    """Проверки до запуска. Возвращает (можно_ли_стартовать, cfg)."""
    cfg = mobile_config.load()
    log(mobile_config.status_line(cfg))
    if not cfg.enabled:
        return False, cfg

    # Доступность базы. Мобильный слой без неё бесполезен, но система в
    # целом от этого не страдает — валится только бот.
    try:
        from mobile import service
        ok = service.system_status().get("database")
        log(f"Database: {'OK' if ok else 'UNREACHABLE'}")
        if not ok:
            log("Aborting: database unreachable")
            return False, cfg
    except Exception as exc:
        log(f"Aborting: database check failed: {type(exc).__name__}")
        return False, cfg

    # Публикация обязана быть выключена. Если способности нет — считаем
    # выключенной и идём дальше: fail closed, а не fail open.
    from mobile import service
    q = service.publishing_queue()
    log(f"Publishing: {'ENABLED' if q['publishing_enabled'] else 'DISABLED'}"
        + ("" if q["capability_present"] else " (capability row absent)"))
    if q["publishing_enabled"]:
        log("Aborting: publishing capability is enabled; "
            "Phase 6.5 runs only with publishing disabled")
        return False, cfg
    return True, cfg


class Runner:
    def __init__(self, cfg, client=None, ledger=None):
        self.cfg = cfg
        self.client = client or TelegramClient(cfg)
        self.ledger = ledger or router.UpdateLedger()
        self.offset = None
        self.stopping = False

    def stop(self, *_):
        if not self.stopping:
            log("Shutdown requested, finishing current poll…")
        self.stopping = True

    def process(self, update):
        result = router.handle(update, cfg=self.cfg, ledger=self.ledger,
                               logger=log)
        chat = chat_id_of(update)
        if chat is None:
            return result
        for chunk in result["chunks"]:
            try:
                self.client.send_message(chat, chunk)
            except Exception as exc:
                log(f"[{result['correlation_id']}] send failed: "
                    f"{type(exc).__name__}")
                break
        return result

    def poll_once(self):
        data = self.client.get_updates(self.offset)
        if not data.get("ok"):
            log("getUpdates returned not ok")
            return 0
        updates = data.get("result") or []
        for upd in updates:
            uid = upd.get("update_id")
            if isinstance(uid, int):
                self.offset = uid + 1
            self.process(upd)
        return len(updates)

    def run(self):
        import time
        backoff = BACKOFF_START_SEC
        while not self.stopping:
            try:
                self.poll_once()
                backoff = BACKOFF_START_SEC
            except urllib.error.URLError as exc:
                log(f"Network error: {type(exc).__name__}; retry in {backoff}s")
                for _ in range(backoff):
                    if self.stopping:
                        break
                    time.sleep(1)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
            except Exception as exc:
                log(f"Poll error: {type(exc).__name__}; retry in {backoff}s")
                for _ in range(backoff):
                    if self.stopping:
                        break
                    time.sleep(1)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
        log("Stopped.")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    ok, cfg = preflight()
    if "--check" in argv:
        log("Preflight only, not starting the poll loop.")
        return 0 if ok else 1
    if not ok:
        return 1
    runner = Runner(cfg)
    signal.signal(signal.SIGINT, runner.stop)
    signal.signal(signal.SIGTERM, runner.stop)
    try:
        me = runner.client.get_me()
        name = (me.get("result") or {}).get("username")
        log(f"Bot connected: @{name}" if name else "Bot connected")
    except Exception as exc:
        log(f"Cannot reach Telegram API: {type(exc).__name__}")
        return 1
    log(f"Owner allowlist: 1 user. Polling every "
        f"{mobile_config.POLL_TIMEOUT_SEC}s. Ctrl+C to stop.")
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
