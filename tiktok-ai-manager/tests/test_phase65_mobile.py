#!/usr/bin/env python3
"""Phase 6.5 — мобильный пульт. Тесты A-H.

Ни один тест не обращается к Telegram: роутер принимает обычный словарь,
поэтому весь слой проверяется без сети и без настоящего токена. Токены в
фикстурах заведомо поддельные и проверяются на то, что НЕ вытекают.

Журнал пишется во временный файл: настоящий data/mobile/audit.jsonl тесты
не засоряют.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg
from core import config as core_config
from mobile import audit, auth, commands
from mobile import config as mobile_config
from mobile import formatters as F
from mobile import router
from mobile import service as S

RESULTS = []
OWNER = 424242
STRANGER = 999999

# Поддельный токен правдоподобной формы: проверяем, что он не вытекает.
FAKE_TOKEN = "1234567890:AAFfakeTOKENvalue_for_tests_only_0123456789"
FAKE_DB_PASSWORD = "s3cr3t-db-password-for-tests"

ALL_COMMANDS = ("/start", "/status", "/report", "/insights", "/ideas",
                "/scripts", "/experiments", "/queue", "/refresh", "/help")

TRACKED_TABLES = ("videos", "video_snapshots", "video_features", "video_assets",
                  "insights", "reports", "ideas", "scripts", "experiments",
                  "publishing_queue", "publishing_history", "content_dna",
                  "system_capabilities")

TMP = None


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name:<62}{detail}")


def cfg_enabled():
    return mobile_config.MobileConfig(token=FAKE_TOKEN, owner_id=OWNER)


def update(text, user_id=OWNER, update_id=1):
    return {"update_id": update_id,
            "message": {"message_id": update_id,
                        "from": {"id": user_id, "is_bot": False},
                        "chat": {"id": user_id, "type": "private"},
                        "text": text}}


def audit_path():
    return str(TMP / "audit.jsonl")


def run(text, user_id=OWNER, update_id=1, cfg=None, ledger=None):
    return router.handle(update(text, user_id, update_id),
                         cfg=cfg or cfg_enabled(), ledger=ledger,
                         audit_path=audit_path())


def db_snapshot():
    counts = {}
    with psycopg.connect(core_config.dsn("ro")) as c, c.cursor() as cur:
        for t in TRACKED_TABLES:
            cur.execute(f"SELECT count(*) FROM {t}")
            counts[t] = cur.fetchone()[0]
        cur.execute("SELECT capability, enabled FROM system_capabilities ORDER BY 1")
        counts["_capabilities"] = tuple(cur.fetchall())
    return counts


# ══════════════════════════════════════════════════════════════════════ A
def test_A_authorization():
    print("\nA — авторизация")
    r = run("/status", user_id=OWNER)
    check("A1 владелец допущен", r["status"] == "success", r["status"])

    r = run("/status", user_id=STRANGER)
    check("A2 посторонний отвергнут", r["status"] == "denied", r["status"])
    check("A3 ответ постороннему безопасен",
          r["text"] == auth.DENY_MESSAGE, r["text"])
    check("A4 в ответе нет внутренних данных",
          not any(w in r["text"].lower() for w in
                  ("videos", "insights", "database", "owner", "postgres",
                   "16", "publishing")), r["text"])

    # нет токена -> интеграции нет
    c = mobile_config.load(env={})
    check("A5 без токена интеграция выключена", c.enabled is False)
    check("A6 названа причина отсутствия токена",
          c.reason == mobile_config.DISABLED_NO_TOKEN, c.reason)
    r = run("/status", cfg=c)
    check("A7 при выключенной интеграции даже владелец не проходит",
          r["status"] == "denied")

    # токен есть, владельца нет -> fail closed
    c = mobile_config.load(env={mobile_config.TOKEN_VAR: FAKE_TOKEN})
    check("A8 токен без владельца — выключено (fail closed)",
          c.enabled is False and c.reason == mobile_config.DISABLED_NO_OWNER,
          c.reason)
    check("A9 обращение к токену выключенной конфигурации запрещено",
          _raises(lambda: c.token, RuntimeError))

    c = mobile_config.load(env={mobile_config.TOKEN_VAR: FAKE_TOKEN,
                                mobile_config.OWNER_VAR: "не-число"})
    check("A10 нечисловой owner id — выключено",
          c.enabled is False and c.reason == mobile_config.DISABLED_BAD_OWNER)

    c = mobile_config.load(env={mobile_config.TOKEN_VAR: FAKE_TOKEN,
                                mobile_config.OWNER_VAR: str(OWNER)})
    check("A11 корректная пара включает интеграцию",
          c.enabled is True and c.owner_id == OWNER)

    # апдейт без отправителя
    r = router.handle({"update_id": 7, "message": {"chat": {"id": 1},
                                                   "text": "/status"}},
                      cfg=cfg_enabled(), audit_path=audit_path())
    check("A12 апдейт без отправителя отвергнут", r["status"] == "denied")
    check("A13 канальный пост владельцем не считается",
          auth.extract_user_id({"channel_post": {"chat": {"id": OWNER},
                                                 "text": "/status"}}) is None)

    events = audit.read_all(audit_path())
    denied = [e for e in events if e["status"] == "denied"]
    reasons = {e["details"].get("deny_reason") for e in denied}
    # Проверяется не количество, а ПОКРЫТИЕ причин: счётчик ломается от
    # любой новой проверки выше, а перечень причин — ровно то, что важно.
    check("A14 в журнале есть отказ по каждой причине",
          reasons == {auth.DENY_NOT_OWNER, auth.DENY_NOT_CONFIGURED,
                      auth.DENY_NO_USER}, str(sorted(r or "-" for r in reasons)))
    check("A15 в журнале записана машинная причина отказа",
          all("deny_reason" in e["details"] for e in denied))
    check("A16 отказ постороннему не выполнил ни одной команды",
          all(e["details"].get("mutating") is None for e in denied))


# ══════════════════════════════════════════════════════════════════════ B
def test_B_command_routing():
    print("\nB — маршрутизация команд")
    for i, cmd in enumerate(ALL_COMMANDS):
        r = run(cmd, update_id=100 + i)
        check(f"B1 {cmd} отвечает успехом", r["status"] == "success", r["status"])
        check(f"B2 {cmd} возвращает непустой текст",
              bool(r["text"].strip()), f"{len(r['text'])} символов")
        check(f"B3 {cmd} укладывается в лимит Telegram",
              all(len(ch) <= mobile_config.TELEGRAM_MAX_MESSAGE
                  for ch in r["chunks"]),
              f"{len(r['chunks'])} сообщ., макс "
              f"{max(len(ch) for ch in r['chunks'])}")

    check("B4 неизвестная команда не падает",
          run("/nosuchcommand", update_id=200)["status"] == "ignored")
    check("B5 обычный текст не считается командой",
          run("привет", update_id=201)["status"] == "ignored")
    check("B6 команда с именем бота распознаётся",
          commands.parse("/status@my_manager_bot") == "/status")
    check("B7 регистр не важен", commands.parse("/STATUS") == "/status")
    check("B8 /help перечисляет все команды реестра",
          all(c.name in run("/help", update_id=202)["text"]
              for c in commands.ordered()))

    # запрещённые на этой фазе имена
    for bad in commands.FORBIDDEN:
        r = run(bad, update_id=300)
        check(f"B9 {bad} отвергнута", r["status"] == "denied", r["status"])
        check(f"B10 {bad} отсутствует в реестре", bad not in commands.REGISTRY)

    check("B11 длинный текст режется по границам",
          len(F.split_message("x" * 9000, 3500)) == 3,
          str(len(F.split_message("x" * 9000, 3500))))
    check("B12 нарезка не теряет содержимое",
          "".join(F.split_message("a\nb\nc", 3500)).replace("\n", "") == "abc")


# ══════════════════════════════════════════════════════════════════════ C
def test_C_no_mutation():
    print("\nC — команды ничего не меняют")
    before = db_snapshot()
    for i, cmd in enumerate(ALL_COMMANDS):
        run(cmd, update_id=400 + i)
    after = db_snapshot()
    check("C1 количество строк во всех таблицах не изменилось",
          before == after,
          str({k: (before[k], after[k]) for k in before if before[k] != after[k]}))
    for t in TRACKED_TABLES:
        check(f"C2 {t}: {before[t]} строк без изменений", before[t] == after[t])

    check("C3 ни одна команда не помечена изменяющей",
          all(not c.mutating for c in commands.REGISTRY.values()),
          str([c.name for c in commands.REGISTRY.values() if c.mutating]))
    check("C4 сервисный слой ходит под ролью только для чтения",
          S.READ_ONLY_ROLE == "ro")

    # структурная гарантия: у роли нет прав на изменение
    with psycopg.connect(core_config.dsn("ro")) as c, c.cursor() as cur:
        cur.execute("""SELECT count(*) FROM information_schema.role_table_grants
                        WHERE grantee='tiktok_ro'
                          AND privilege_type IN ('INSERT','UPDATE','DELETE')""")
        check("C5 у роли ro нет INSERT/UPDATE/DELETE ни на одну таблицу",
              cur.fetchone()[0] == 0)
    with psycopg.connect(core_config.dsn("ro"), autocommit=True) as c, c.cursor() as cur:
        try:
            cur.execute("INSERT INTO ideas (account_id, origin, title, status,"
                        " created_at) VALUES (1,'x','x','proposed',now())")
            check("C6 попытка записи под ролью ro отвергается базой", False,
                  "запись прошла")
        except psycopg.Error as e:
            check("C6 попытка записи под ролью ro отвергается базой",
                  "permission denied" in str(e), str(e).split("\n")[0][:46])

    src = "\n".join((ROOT / "mobile" / f).read_text(encoding="utf-8")
                    for f in ("router.py", "commands.py", "formatters.py",
                              "telegram.py", "auth.py"))
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        check(f"C7 в обработчиках нет SQL «{verb.strip()}»", verb not in src)


# ══════════════════════════════════════════════════════════════════════ D
def test_D_publishing_safety():
    print("\nD — публикация остаётся выключенной")
    def submit_flag():
        with psycopg.connect(core_config.dsn("ro")) as c, c.cursor() as cur:
            cur.execute("SELECT enabled FROM system_capabilities"
                        " WHERE capability='publishing.submit'")
            row = cur.fetchone()
            return row[0] if row else None

    before = submit_flag()
    check("D1 до запуска publishing.submit = FALSE", before is False, str(before))
    for i, cmd in enumerate(ALL_COMMANDS):
        run(cmd, update_id=500 + i)
    after = submit_flag()
    check("D2 после всех команд publishing.submit = FALSE", after is False,
          str(after))
    check("D3 значение не изменилось", before == after)

    q = S.publishing_queue()
    check("D4 сервис сообщает о выключенной публикации",
          q["publishing_enabled"] is False)
    text = run("/queue", update_id=520)["text"]
    check("D5 /queue показывает DISABLED", "DISABLED" in text)
    check("D6 /queue не обещает отправку", "Submitting: DISABLED" in text)

    # отсутствие записи о способности трактуется как выключено
    saved = S.publishing_queue
    try:
        S.publishing_queue = lambda: {"publishing_enabled": False,
                                      "capability_present": False,
                                      "by_status": {}, "total": 0}
        t = F.queue(S.publishing_queue())
        check("D7 отсутствие capability = DISABLED (fail closed)",
              "DISABLED" in t and "capability row absent" in t)
    finally:
        S.publishing_queue = saved

    check("D8 команд публикации нет в реестре",
          not any(n in commands.REGISTRY for n in
                  ("/publish", "/submit", "/send", "/post", "/approve")))


# ══════════════════════════════════════════════════════════════════════ E
def test_E_error_handling():
    print("\nE — ошибки не раскрывают внутренности")
    saved = S.system_status
    logged = []
    try:
        def boom():
            raise RuntimeError(
                f"connection to postgresql://tiktok_ro:{FAKE_DB_PASSWORD}"
                "@127.0.0.1:5432/tiktok_manager failed")
        S.system_status = boom
        r = router.handle(update("/status", update_id=600), cfg=cfg_enabled(),
                          audit_path=audit_path(), logger=logged.append)
    finally:
        S.system_status = saved

    check("E1 статус ответа — ошибка", r["status"] == "error", r["status"])
    check("E2 пользователь получил безопасное сообщение",
          "Temporary system error" in r["text"], r["text"][:50])
    check("E3 в ответе есть correlation ID",
          r["correlation_id"] in r["text"])
    check("E4 в ответе нет трассировки",
          not any(w in r["text"] for w in
                  ("Traceback", "RuntimeError", "File \"", "line ")))
    check("E5 в ответе нет строки подключения",
          "postgresql://" not in r["text"] and FAKE_DB_PASSWORD not in r["text"])

    ev = [e for e in audit.read_all(audit_path()) if e["status"] == "error"][-1]
    check("E6 ошибка записана в журнал", ev["command"] == "/status")
    check("E7 в журнале назван тип ошибки",
          ev["details"].get("error_type") == "RuntimeError")
    check("E8 пароль вычищен из журнала",
          FAKE_DB_PASSWORD not in json.dumps(ev, ensure_ascii=False),
          ev["details"].get("error", "")[:60])
    check("E9 подробность ушла в лог приложения", len(logged) == 1)
    check("E10 в логе приложения пароля тоже нет",
          FAKE_DB_PASSWORD not in "\n".join(logged))


# ══════════════════════════════════════════════════════════════════════ F
def test_F_secret_safety():
    print("\nF — секреты не вытекают")
    for i, cmd in enumerate(ALL_COMMANDS):
        run(cmd, update_id=700 + i)
    blob = json.dumps(audit.read_all(audit_path()), ensure_ascii=False)
    for secret, label in ((FAKE_TOKEN, "TELEGRAM_BOT_TOKEN"),
                          (FAKE_DB_PASSWORD, "DB_PASSWORD")):
        check(f"F1 {label} отсутствует в журнале", secret not in blob)
    check("F2 в журнале нет ключа approval_token",
          "approval_token" not in blob or "«вырезано»" in blob)

    ev = audit.event("/status", "success", telegram_user_id=OWNER,
                     details={"token": FAKE_TOKEN,
                              "nested": {"password": FAKE_DB_PASSWORD},
                              "text": f"bot {FAKE_TOKEN} here",
                              "dsn": f"postgresql://u:{FAKE_DB_PASSWORD}@h/db"})
    s = json.dumps(ev, ensure_ascii=False)
    check("F3 значение ключа token вырезано", FAKE_TOKEN not in s)
    check("F4 вложенный пароль вырезан", FAKE_DB_PASSWORD not in s)
    check("F5 токен вырезан и из свободного текста",
          "AAFfakeTOKENvalue" not in s)
    check("F6 пароль вырезан из строки подключения",
          "postgresql://u:" + FAKE_DB_PASSWORD not in s)

    check("F7 конфигурация не печатает токен",
          FAKE_TOKEN not in repr(cfg_enabled())
          and FAKE_TOKEN not in str(cfg_enabled()), repr(cfg_enabled()))

    src = "\n".join((ROOT / "mobile" / p.name).read_text(encoding="utf-8")
                    for p in sorted((ROOT / "mobile").glob("*.py")))
    check("F8 в исходниках нет зашитого owner id",
          str(OWNER) not in src and "TELEGRAM_OWNER_ID=" not in src)
    check("F9 токен берётся только из окружения",
          'os.environ' in (ROOT / "mobile" / "config.py").read_text(encoding="utf-8"))

    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check("F10 .env игнорируется git", ".env" in gi)
    check("F11 журнал обращений игнорируется git", "data/mobile/" in gi)
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    check("F12 .env.example содержит пустые переменные Telegram",
          "TELEGRAM_BOT_TOKEN=" in example and "TELEGRAM_OWNER_ID=" in example)
    check("F13 .env.example не содержит настоящих значений",
          all(l.split("=", 1)[1].strip() in ("", "CHANGE_ME")
              for l in example.splitlines()
              if l.startswith("TELEGRAM_")),
          str([l for l in example.splitlines() if l.startswith("TELEGRAM_")]))


# ══════════════════════════════════════════════════════════════════════ G
def test_G_deterministic_output():
    print("\nG — один и тот же state даёт один и тот же вывод")
    for i, cmd in enumerate(ALL_COMMANDS):
        a = run(cmd, update_id=800 + i)["text"]
        b = run(cmd, update_id=850 + i)["text"]
        check(f"G1 {cmd} воспроизводится дословно", a == b,
              "" if a == b else "разошлось")
    r1 = run("/status", update_id=880)
    r2 = run("/status", update_id=881)
    check("G2 correlation ID у каждого вызова свой",
          r1["correlation_id"] != r2["correlation_id"])
    check("G3 correlation ID не попадает в тело успешного ответа",
          r1["correlation_id"] not in r1["text"])
    check("G4 ответ не содержит момента вызова",
          "generated_at" not in r1["text"].lower())


# ══════════════════════════════════════════════════════════════════════ H
def test_H_duplicate_update():
    print("\nH — повторная доставка апдейта")
    ledger = router.UpdateLedger()
    first = run("/status", update_id=900, ledger=ledger)
    second = run("/status", update_id=900, ledger=ledger)
    check("H1 первый апдейт обработан", first["status"] == "success")
    check("H2 повтор помечен как дубликат в журнале",
          any(e["status"] == "duplicate" and e["update_id"] == 900
              for e in audit.read_all(audit_path())))
    check("H3 читающая команда при повторе безопасна",
          second["status"] == "success" and second["text"] == first["text"])
    check("H4 журнал связывает повтор с первым обращением",
          any(e["details"].get("first_correlation_id") == first["correlation_id"]
              for e in audit.read_all(audit_path())
              if e["status"] == "duplicate"))

    # изменяющая команда повторно не исполняется — проверяем на месте
    calls = []
    probe = commands.Command("/probe_mutating", "тестовая",
                             lambda u: calls.append(1) or "ok", mutating=True)
    commands.REGISTRY[probe.name] = probe
    try:
        led = router.UpdateLedger()
        r1 = run("/probe_mutating", update_id=910, ledger=led)
        r2 = run("/probe_mutating", update_id=910, ledger=led)
        check("H5 изменяющая команда исполнена один раз", len(calls) == 1,
              f"вызовов {len(calls)}")
        check("H6 повтор изменяющей команды отклонён",
              r2["status"] == "duplicate" and r1["status"] == "success",
              f"{r1['status']} / {r2['status']}")
    finally:
        commands.REGISTRY.pop(probe.name, None)

    check("H7 разные update_id обрабатываются независимо",
          run("/status", update_id=920, ledger=ledger)["status"] == "success")
    check("H8 учёт апдейтов ограничен по объёму",
          router.UpdateLedger(capacity=2).capacity == 2)
    led = router.UpdateLedger(capacity=2)
    for uid in (1, 2, 3):
        led.remember(uid, "c")
    check("H9 старые записи вытесняются",
          not led.seen(1) and led.seen(3))


def _raises(fn, exc):
    try:
        fn()
        return False
    except exc:
        return True


if __name__ == "__main__":
    TMP = Path(tempfile.mkdtemp(prefix="phase65_"))
    print(f"журнал теста: {TMP}")
    for fn in (test_A_authorization, test_B_command_routing,
               test_C_no_mutation, test_D_publishing_safety,
               test_E_error_handling, test_F_secret_safety,
               test_G_deterministic_output, test_H_duplicate_update):
        fn()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\nпроверок: {len(RESULTS)} | провалов: {len(failed)}")
    for n in failed:
        print(f"  FAILED: {n}")
    sys.exit(1 if failed else 0)
