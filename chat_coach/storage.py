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
from datetime import datetime

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

    # -------------------------------------------------------------- забыть

    def забыть(self, user_id):
        """Стирает всё про человека по команде /удалить."""
        with self._соединение() as соединение:
            for таблица in ("usage", "reports", "premium_interest", "users"):
                соединение.execute(
                    "DELETE FROM %s WHERE user_id=?" % таблица, (user_id,))
