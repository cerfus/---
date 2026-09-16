# -*- coding: utf-8 -*-
"""
Хранилище: пользователи, квоты, сохранённые отчёты.

Главное правило этого файла: сюда не попадает ни одного куска переписки.
В выгрузке лежат сообщения двух людей, и второй согласия не давал, поэтому
храним только числа — индекс, количества, длительности. Даже если база
утечёт, читать в ней будет нечего.

Имена таблиц и колонок английские, а обращаются к ним русские методы:
SQL остаётся скучным и переносимым (Postgres, когда понадобится, примет
его почти как есть), а код вокруг — в общем стиле проекта.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta

СХЕМА = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    tier        TEXT    NOT NULL DEFAULT 'free',
    created_at  TEXT    NOT NULL,
    seen_at     TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    user_id  INTEGER NOT NULL,
    kind     TEXT    NOT NULL,
    period   TEXT    NOT NULL,
    amount   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, kind, period)
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    score_all   INTEGER,
    score_now   INTEGER,
    messages    INTEGER,
    days        INTEGER,
    numbers     TEXT
);

CREATE TABLE IF NOT EXISTS premium_interest (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    source      TEXT
);

-- Купленное: остаток по каждому виду. Не сгорает по календарю — человек
-- заплатил за штуки, а не за неделю.
CREATE TABLE IF NOT EXISTS credits (
    user_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    amount  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, kind)
);

-- Платежи. provider держим отдельной колонкой, хотя провайдер пока один:
-- вторым однажды встанет платёжка для веб-версии, и переделывать схему
-- ради этого не придётся.
--
-- UNIQUE(provider, external_id) — здесь вся идемпотентность. Телеграм
-- повторяет апдейт, если бот не ответил вовремя, и без этого ограничения
-- один платёж зачислялся бы дважды.
CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    provider    TEXT    NOT NULL,
    external_id TEXT    NOT NULL,
    package     TEXT    NOT NULL,
    price       INTEGER NOT NULL,
    currency    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    refunded_at TEXT,
    UNIQUE (provider, external_id)
);
"""

# Что из разбора вообще можно положить в базу. Список закрытый — и это
# не перестраховка: любое новое поле проходит через него осознанно,
# иначе однажды сюда приедет цитата из переписки.
ЧИСЛА = ("моих_сообщений", "её_сообщений", "разговоров", "начала_она",
         "начал_кто_то", "оборвалось_на_мне", "мой_ответ_сек", "её_ответ_сек",
         "моя_длина", "её_длина", "её_вопросы", "её_эмоции", "ночных",
         "моих_серий_подряд")


class База:
    """Тонкая обёртка над SQLite.

    Соединение открывается на каждую операцию: бот ходит сюда из рабочих
    потоков (asyncio.to_thread), а одно общее соединение между потоками
    SQLite не любит.
    """

    def __init__(self, путь):
        self.путь = путь
        папка = os.path.dirname(os.path.abspath(путь))
        if папка:
            os.makedirs(папка, exist_ok=True)
        self.создать()

    def _соединение(self):
        соединение = sqlite3.connect(self.путь, timeout=10)
        соединение.row_factory = sqlite3.Row
        # WAL — чтобы чтение не ждало записи, когда пользователей станет много.
        соединение.execute("PRAGMA journal_mode=WAL")
        return соединение

    def создать(self):
        with self._соединение() as соединение:
            соединение.executescript(СХЕМА)

    # ---------------------------------------------------------------- люди

    def увидеть(self, user_id):
        """Отмечает, что пользователь заходил. Возвращает его тариф."""
        сейчас = datetime.now().isoformat(timespec="seconds")
        with self._соединение() as соединение:
            соединение.execute(
                "INSERT INTO users(user_id, created_at, seen_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET seen_at=excluded.seen_at",
                (user_id, сейчас, сейчас))
            строка = соединение.execute(
                "SELECT tier FROM users WHERE user_id=?", (user_id,)).fetchone()
        return строка["tier"] if строка else "free"

    def тариф(self, user_id):
        with self._соединение() as соединение:
            строка = соединение.execute(
                "SELECT tier FROM users WHERE user_id=?", (user_id,)).fetchone()
        return строка["tier"] if строка else "free"

    def поставить_тариф(self, user_id, тариф):
        сейчас = datetime.now().isoformat(timespec="seconds")
        with self._соединение() as соединение:
            соединение.execute(
                "INSERT INTO users(user_id, tier, created_at, seen_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET tier=excluded.tier",
                (user_id, тариф, сейчас, сейчас))

    # ---------------------------------------------------------------- квоты

    def потрачено(self, user_id, вид, период):
        with self._соединение() as соединение:
            строка = соединение.execute(
                "SELECT amount FROM usage WHERE user_id=? AND kind=? AND period=?",
                (user_id, вид, период)).fetchone()
        return строка["amount"] if строка else 0

    def потратить(self, user_id, вид, период, лимит):
        """Списывает одну попытку. Возвращает True, если она была.

        Проверка и списание — один запрос: иначе два сообщения, присланные
        подряд, успевали бы проскочить оба мимо лимита.
        """
        if лимит <= 0:
            return False
        with self._соединение() as соединение:
            курсор = соединение.execute(
                "INSERT INTO usage(user_id, kind, period, amount) VALUES(?,?,?,1) "
                "ON CONFLICT(user_id, kind, period) DO UPDATE SET amount=amount+1 "
                "WHERE amount < ?",
                (user_id, вид, период, лимит))
            return курсор.rowcount > 0

    def вернуть(self, user_id, вид, период):
        """Возвращает попытку, если работа сорвалась не по вине человека."""
        with self._соединение() as соединение:
            соединение.execute(
                "UPDATE usage SET amount = MAX(amount - 1, 0) "
                "WHERE user_id=? AND kind=? AND period=?",
                (user_id, вид, период))

    # -------------------------------------------------------------- отчёты

    def записать_отчёт(self, user_id, итог):
        """Кладёт в базу только числа из разбора — см. ЧИСЛА."""
        числа = {ключ: итог["метрики"].get(ключ) for ключ in ЧИСЛА}
        with self._соединение() as соединение:
            соединение.execute(
                "INSERT INTO reports(user_id, created_at, score_all, score_now, "
                "messages, days, numbers) VALUES(?,?,?,?,?,?,?)",
                (user_id, datetime.now().isoformat(timespec="seconds"),
                 итог.get("индекс"),
                 (итог.get("тренд") or {}).get("конец", итог.get("индекс")),
                 итог.get("всего"), итог.get("дней"),
                 json.dumps(числа, ensure_ascii=False)))

    def история(self, user_id, сколько=10):
        with self._соединение() as соединение:
            строки = соединение.execute(
                "SELECT created_at, score_all, score_now, messages FROM reports "
                "WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, сколько)).fetchall()
        return [dict(строка) for строка in строки]

    # ------------------------------------------------------------ деньги

    def зачислить(self, user_id, провайдер, внешний_id, пакет, цена, валюта,
                  сколько):
        """Кладёт купленное на счёт. False — этот платёж уже зачтён.

        Повторный апдейт от Телеграма — обычное дело, а не сбой: он шлёт
        его снова, пока бот не ответит. Поэтому вся защита от двойного
        начисления держится на UNIQUE(provider, external_id), и обе записи
        идут одной транзакцией: не легло в payments — не легло и на счёт.

        «сколько» — {вид: штук}: сколько чего кладём. Что именно входит
        в пакет, знает payments.py, а не хранилище.
        """
        сейчас = datetime.now().isoformat(timespec="seconds")
        with self._соединение() as соединение:
            курсор = соединение.execute(
                "INSERT OR IGNORE INTO payments(user_id, provider, external_id,"
                " package, price, currency, created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, провайдер, внешний_id, пакет, цена, валюта, сейчас))
            if not курсор.rowcount:
                return False
            for вид, штук in сколько.items():
                соединение.execute(
                    "INSERT INTO credits(user_id, kind, amount) VALUES(?,?,?) "
                    "ON CONFLICT(user_id, kind) DO UPDATE SET "
                    "amount = amount + excluded.amount",
                    (user_id, вид, штук))
        return True

    def остаток_купленного(self, user_id, вид):
        with self._соединение() as соединение:
            строка = соединение.execute(
                "SELECT amount FROM credits WHERE user_id=? AND kind=?",
                (user_id, вид)).fetchone()
        return строка["amount"] if строка else 0

    def потратить_купленное(self, user_id, вид):
        """Списывает одну купленную штуку. True, если она была.

        Проверка и списание одним запросом — тем же приёмом, что и квоты:
        иначе два сообщения подряд успевают списать один и тот же остаток.
        """
        with self._соединение() as соединение:
            курсор = соединение.execute(
                "UPDATE credits SET amount = amount - 1 "
                "WHERE user_id=? AND kind=? AND amount > 0", (user_id, вид))
            return курсор.rowcount > 0

    def вернуть_купленное(self, user_id, вид):
        """Кладёт штуку обратно, если работа сорвалась не по вине человека.

        Без WHERE-совпадения строки ничего не произойдёт — и правильно:
        возвращаем только то, что раньше отсюда и взяли.
        """
        with self._соединение() as соединение:
            соединение.execute(
                "UPDATE credits SET amount = amount + 1 "
                "WHERE user_id=? AND kind=?", (user_id, вид))

    def найти_платёж(self, провайдер, внешний_id):
        with self._соединение() as соединение:
            строка = соединение.execute(
                "SELECT * FROM payments WHERE provider=? AND external_id=?",
                (провайдер, внешний_id)).fetchone()
        return dict(строка) if строка else None

    def пометить_возврат(self, провайдер, внешний_id, сколько):
        """Отмечает возврат и снимает начисленное.

        False — платежа нет или он уже возвращён.
        """
        сейчас = datetime.now().isoformat(timespec="seconds")
        with self._соединение() as соединение:
            строка = соединение.execute(
                "SELECT user_id, refunded_at FROM payments "
                "WHERE provider=? AND external_id=?",
                (провайдер, внешний_id)).fetchone()
            if строка is None or строка["refunded_at"]:
                return False
            соединение.execute(
                "UPDATE payments SET refunded_at=? "
                "WHERE provider=? AND external_id=?",
                (сейчас, провайдер, внешний_id))
            for вид, штук in сколько.items():
                # MAX(...,0): купленное могло быть уже потрачено, и уводить
                # остаток в минус нельзя — человек не должен уйти в долг
                # за то, за что заплатил и чем успел воспользоваться.
                соединение.execute(
                    "UPDATE credits SET amount = MAX(amount - ?, 0) "
                    "WHERE user_id=? AND kind=?",
                    (штук, строка["user_id"], вид))
        return True

    # ------------------------------------------------------------- премиум

    def хочет_премиум(self, user_id, откуда=""):
        with self._соединение() as соединение:
            соединение.execute(
                "INSERT INTO premium_interest(user_id, created_at, source) "
                "VALUES(?,?,?)",
                (user_id, datetime.now().isoformat(timespec="seconds"), откуда))

    def спрос_на_премиум(self):
        """Сколько человек нажали «хочу премиум» и сколько раз всего."""
        with self._соединение() as соединение:
            строка = соединение.execute(
                "SELECT COUNT(*) AS всего, COUNT(DISTINCT user_id) AS людей "
                "FROM premium_interest").fetchone()
        return {"всего": строка["всего"], "людей": строка["людей"]}

    # --------------------------------------------------------------- сводка

    def сводка(self, дней=7):
        """Числа про бота целиком: воронка, спрос, деньги.

        Всё это пишется с первого дня и до сих пор не читалось ни разу.
        Ни текста, ни имён здесь нет и быть не может — в базе их и нет,
        это тот же закрытый список ЧИСЛА, только сложенный по всем.

        Про «нажали /start»: увидеть() зовётся ровно в обработчике
        /start, так что это именно нажавшие, а не все, кто что-то писал.
        Подписи в отчёте называют измеренное, а не «пользователей».
        """
        порог = (datetime.now() - timedelta(days=дней)).isoformat(
            timespec="seconds")
        с = {}
        with self._соединение() as соединение:
            def спросить(запрос, *значения):
                return соединение.execute(запрос, значения).fetchone()

            люди = спросить(
                "SELECT COUNT(*) AS всего,"
                " SUM(created_at >= ?) AS новых,"
                " SUM(seen_at > created_at) AS возвращались FROM users",
                порог)
            с["людей"] = люди["всего"]
            с["новых"] = люди["новых"] or 0
            с["возвращались"] = люди["возвращались"] or 0

            # Воронка считается по разным людям, а не по строкам: три
            # разбора одного человека — это один дошедший, а не три.
            разборы = спросить(
                "SELECT COUNT(*) AS всего, COUNT(DISTINCT user_id) AS людей,"
                " SUM(created_at >= ?) AS свежих FROM reports", порог)
            с["разборов"] = разборы["всего"]
            с["дошли_до_разбора"] = разборы["людей"]
            с["разборов_за_срок"] = разборы["свежих"] or 0
            с["вернулись_за_вторым"] = спросить(
                "SELECT COUNT(*) AS сколько FROM"
                " (SELECT user_id FROM reports GROUP BY user_id"
                "  HAVING COUNT(*) > 1)")["сколько"]

            с["медиана_индекса"] = self._медиана(соединение, "score_now")
            с["медиана_объёма"] = self._медиана(соединение, "messages")

            премиум = спросить(
                "SELECT COUNT(*) AS всего, COUNT(DISTINCT user_id) AS людей"
                " FROM premium_interest")
            с["премиум_нажатий"] = премиум["всего"]
            с["премиум_людей"] = премиум["людей"]
            с["премиум_откуда"] = [
                (строка["source"] or "?", строка["сколько"])
                for строка in соединение.execute(
                    "SELECT source, COUNT(*) AS сколько FROM premium_interest"
                    " GROUP BY source ORDER BY сколько DESC")]

            деньги = спросить(
                "SELECT COUNT(*) AS всего, COUNT(DISTINCT user_id) AS людей,"
                " COALESCE(SUM(price), 0) AS звёзд,"
                " SUM(refunded_at IS NOT NULL) AS возвратов,"
                " COALESCE(SUM(CASE WHEN refunded_at IS NOT NULL"
                "                   THEN price ELSE 0 END), 0) AS вернули"
                " FROM payments")
            с["платежей"] = деньги["всего"]
            с["платили_людей"] = деньги["людей"]
            с["возвратов"] = деньги["возвратов"] or 0
            # Оставшееся, а не собранное: возвращённое деньгами не было.
            с["звёзд"] = деньги["звёзд"] - деньги["вернули"]
            с["вернули_звёзд"] = деньги["вернули"]

            с["непотрачено"] = спросить(
                "SELECT COALESCE(SUM(amount), 0) AS сколько FROM credits"
            )["сколько"]
        с["дней"] = дней
        return с

    @staticmethod
    def _медиана(соединение, колонка):
        """Медиана колонки reports. None, если разборов ещё не было.

        Медиана, а не среднее: одна переписка на шесть тысяч сообщений
        перекосит среднее так, что оно перестанет описывать хоть кого-то.
        """
        значения = [строка[0] for строка in соединение.execute(
            "SELECT %s FROM reports WHERE %s IS NOT NULL ORDER BY %s"
            % (колонка, колонка, колонка))]
        if not значения:
            return None
        середина = len(значения) // 2
        if len(значения) % 2:
            return значения[середина]
        return (значения[середина - 1] + значения[середина]) // 2

    # -------------------------------------------------------------- забыть

    def забыть(self, user_id):
        """Стирает всё про человека по команде /удалить.

        Платежи не удаляются, а обезличиваются. Без строки платежа нельзя
        вернуть деньги, и обещание «сотру всё» не должно на деле означать
        «и возврат теперь невозможен». Купленные разборы при этом сгорают
        вместе с остальным — об этом сказано прямо в тексте команды.
        """
        with self._соединение() as соединение:
            for таблица in ("usage", "reports", "premium_interest", "credits",
                            "users"):
                соединение.execute(
                    "DELETE FROM %s WHERE user_id=?" % таблица, (user_id,))
            соединение.execute(
                "UPDATE payments SET user_id=0 WHERE user_id=?", (user_id,))
