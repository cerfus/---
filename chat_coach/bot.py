#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Телеграм-бот: разбор переписки.

Запуск: python bot.py (нужны BOT_TOKEN и ANTHROPIC_API_KEY в окружении).

Работает на long polling — для этого не нужны ни домен, ни сертификат,
а сломаться тут нечему. Вебхук имеет смысл, когда упрёмся в нагрузку.

Про хранение: разобранная переписка живёт только в оперативной памяти и
только до конца разбора. Сразу после него в состоянии остаётся одна
обезличенная выборка — на случай уточняющих вопросов, — а сами сообщения
забываются. На диск не попадает ничего, кроме чисел.
"""

import asyncio
import html
import logging
import os
import shutil
import tempfile

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

import analysis
import console          # noqa: F401  — правит вывод на Windows при импорте
import limits
import metrics
import parser as export_parser
import report
import storage
import texts

# Телеграм не даёт боту скачать файл больше 20 МБ — это ограничение
# Bot API, а не наше.
ПРЕДЕЛ_ФАЙЛА = 20 * 1024 * 1024
МИНИМУМ_СООБЩЕНИЙ = 20
# Вставляют кусок, а не всю историю, поэтому порог ниже, чем у файла.
МИНИМУМ_ВСТАВКИ = 10
# Потолок накопителя: вставка идёт несколькими сообщениями, но бесконечной
# памятью для чужого текста бот быть не должен.
ПРЕДЕЛ_КУСКОВ = 20
ПРЕДЕЛ_ВСТАВКИ = 100 * 1024
ПРЕДЕЛ_СООБЩЕНИЯ = 3500          # у Телеграма 4096, оставляем запас на разметку

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
журнал = logging.getLogger("chat_coach")

база = storage.База(os.getenv("CHAT_COACH_DB", "данные/бот.sqlite3"))
диспетчер = Dispatcher(storage=MemoryStorage())


class Шаги(StatesGroup):
    вставка = State()
    выбор_себя = State()
    выбор_цели = State()
    готово = State()


# --------------------------------------------------------------------------
# Мелочи
# --------------------------------------------------------------------------

def _кнопки(пары, в_ряд=1):
    """пары: [(подпись, данные), ...]"""
    ряды, ряд = [], []
    for подпись, данные in пары:
        ряд.append(InlineKeyboardButton(text=подпись, callback_data=данные))
        if len(ряд) == в_ряд:
            ряды.append(ряд)
            ряд = []
    if ряд:
        ряды.append(ряд)
    return InlineKeyboardMarkup(inline_keyboard=ряды)


НАЧАЛЬНЫЕ_КНОПКИ = _кнопки([("Как выгрузить переписку", "как_выгрузить"),
                            (texts.КНОПКА_ВСТАВИТЬ, "вставить")])
КНОПКА_ВЫГРУЗКИ = НАЧАЛЬНЫЕ_КНОПКИ
КНОПКА_ГОТОВО = _кнопки([(texts.КНОПКА_ГОТОВО, "вставка_готово")])


def _нарезать(текст):
    """Режет длинный текст по границам строк под предел Телеграма."""
    куски, текущий = [], ""
    for строка in текст.split("\n"):
        # Одна строка длиннее предела — рубим её саму, иначе кусок уедет
        # за лимит и Телеграм откажется его принимать.
        while len(строка) > ПРЕДЕЛ_СООБЩЕНИЯ:
            if текущий:
                куски.append(текущий)
                текущий = ""
            куски.append(строка[:ПРЕДЕЛ_СООБЩЕНИЯ])
            строка = строка[ПРЕДЕЛ_СООБЩЕНИЯ:]
        if len(текущий) + len(строка) + 1 > ПРЕДЕЛ_СООБЩЕНИЯ:
            куски.append(текущий)
            текущий = ""
        текущий += строка + "\n"
    if текущий.strip():
        куски.append(текущий)
    return [кусок for кусок in куски if кусок.strip()]


async def _послать(сообщение, текст, моноширинно=False, кнопки=None):
    """Шлёт текст, разбивая длинный на части по границам строк."""
    куски = _нарезать(текст or "")
    if not куски:
        return
    for номер, кусок in enumerate(куски):
        готовый = html.escape(кусок)
        if моноширинно:
            готовый = "<pre>%s</pre>" % готовый
        await сообщение.answer(готовый,
                               reply_markup=кнопки if номер == len(куски) - 1
                               else None)


async def _убрать(сообщение):
    """Снимает временную плашку «думаю». Не смогли — и ладно."""
    try:
        await сообщение.delete()
    except Exception:                              # noqa: BLE001
        pass


async def _остатки(user_id):
    разборов = await asyncio.to_thread(limits.осталось, база, user_id, limits.РАЗБОР)
    вопросов = await asyncio.to_thread(limits.осталось, база, user_id, limits.ВОПРОС)
    return разборов, вопросов


# --------------------------------------------------------------------------
# Начало
# --------------------------------------------------------------------------

@диспетчер.message(CommandStart())
async def начало(сообщение: Message, state: FSMContext):
    await state.clear()
    await asyncio.to_thread(база.увидеть, сообщение.from_user.id)
    await _послать(сообщение, texts.ПРИВЕТ, кнопки=КНОПКА_ВЫГРУЗКИ)


@диспетчер.callback_query(F.data == "как_выгрузить")
async def как_выгрузить(запрос: CallbackQuery):
    await запрос.answer()
    await _послать(запрос.message, texts.КАК_ВЫГРУЗИТЬ)


@диспетчер.message(Command("помощь"))
async def помощь(сообщение: Message):
    await _послать(сообщение, texts.КАК_ВЫГРУЗИТЬ)


@диспетчер.message(Command("лимит"))
async def лимит(сообщение: Message):
    разборов, вопросов = await _остатки(сообщение.from_user.id)
    await сообщение.answer(texts.ПОДСКАЗКА_ЛИМИТА % (разборов, вопросов))


@диспетчер.message(Command("удалить"))
async def удалить(сообщение: Message, state: FSMContext):
    await state.clear()
    await asyncio.to_thread(база.забыть, сообщение.from_user.id)
    await сообщение.answer(texts.УДАЛЕНО)


# --------------------------------------------------------------------------
# Приём файла
# --------------------------------------------------------------------------

def _разобрать_файл(путь):
    """Синхронная часть: читаем выгрузку и сразу же забываем файл."""
    return export_parser.read_export(путь)


def _безопасное_имя(имя):
    """Имя файла приходит от пользователя, в путь его как есть класть нельзя.

    «../../что-нибудь.json» иначе запишется мимо временной папки — а вместе
    с ним уедет и всё остальное, что мы про эту папку думаем.
    Расширение при этом обязано уцелеть: по нему выбирается разбор.
    """
    имя = os.path.basename((имя or "").replace("\\", "/")).strip()
    имя = имя.lstrip(".")
    return имя or "выгрузка"


async def _спросить_цель(сообщение):
    await сообщение.answer(
        texts.ЦЕЛЬ_ВОПРОС,
        reply_markup=_кнопки([(подпись, "цель:" + код)
                              for код, подпись in texts.ЦЕЛИ.items()]))


async def _принять(сообщение, state, сообщения, вставка=False, явная=False):
    """Общий хвост для файла и вставки: проверить и спросить, кто есть кто.

    Оба пути приходят сюда с одним и тем же списком сообщений, поэтому
    дальше они неотличимы — разница только в пороге, словах и в том,
    размечены ли стороны руками.
    """
    минимум = МИНИМУМ_ВСТАВКИ if вставка else МИНИМУМ_СООБЩЕНИЙ
    if not сообщения:
        await _послать(сообщение,
                       texts.ВСТАВКА_ПУСТО if вставка else texts.ПУСТО)
        return False
    if len(сообщения) < минимум:
        сколько = len(сообщения)
        if вставка:
            беда = texts.ВСТАВКА_МАЛО % (
                сколько, metrics.склонение(сколько, "сообщение", "сообщения",
                                           "сообщений"), минимум)
        else:
            беда = texts.МАЛО % (сколько, минимум)
        await _послать(сообщение, беда)
        return False

    if явная:
        # Разметили «я:» и «она:» — спрашивать, кто из двоих кто, незачем.
        await state.set_state(Шаги.выбор_цели)
        await state.update_data(сообщения=сообщения, куски=None,
                                я=export_parser.МОЯ_СТОРОНА,
                                она=export_parser.ЕЁ_СТОРОНА)
        await _спросить_цель(сообщение)
        return True

    двое, групповой = export_parser.two_sides(сообщения)
    if len(двое) < 2:
        await _послать(сообщение,
                       texts.ВСТАВКА_ПУСТО if вставка else texts.ПУСТО)
        return False
    if групповой:
        await _послать(сообщение, texts.ГРУППОВОЙ)

    await state.set_state(Шаги.выбор_себя)
    await state.update_data(сообщения=сообщения, двое=двое, куски=None)
    await сообщение.answer(
        texts.КТО_ТЫ,
        reply_markup=_кнопки([(двое[0], "я:0"), (двое[1], "я:1")], в_ряд=2))
    return True


@диспетчер.message(F.document)
async def файл(сообщение: Message, state: FSMContext):
    документ = сообщение.document
    if документ.file_size and документ.file_size > ПРЕДЕЛ_ФАЙЛА:
        await _послать(сообщение, texts.ФАЙЛ_БОЛЬШОЙ, кнопки=КНОПКА_ВЫГРУЗКИ)
        return

    имя = (документ.file_name or "").lower()
    if not имя.endswith((".json", ".zip", ".html", ".htm", ".txt", ".log")):
        await _послать(сообщение, texts.ФАЙЛ_НЕ_ТОТ, кнопки=КНОПКА_ВЫГРУЗКИ)
        return

    подождать = await сообщение.answer(texts.СЧИТАЮ)
    папка = tempfile.mkdtemp(prefix="chat_coach_")
    try:
        путь = os.path.join(папка, _безопасное_имя(документ.file_name))
        await сообщение.bot.download(документ, destination=путь)
        сообщения = await asyncio.to_thread(_разобрать_файл, путь)
    except Exception as сбой:                      # noqa: BLE001
        журнал.warning("не смог прочитать файл: %s", сбой)
        await _послать(сообщение, texts.ФАЙЛ_НЕ_ТОТ, кнопки=КНОПКА_ВЫГРУЗКИ)
        return
    finally:
        # Файл нужен был ровно на время разбора. Удаляем всегда — и когда
        # получилось, и когда нет.
        shutil.rmtree(папка, ignore_errors=True)
        await _убрать(подождать)

    await _принять(сообщение, state, сообщения)


@диспетчер.callback_query(Шаги.выбор_себя, F.data.startswith("я:"))
async def кто_я(запрос: CallbackQuery, state: FSMContext):
    await запрос.answer()
    данные = await state.get_data()
    двое = данные["двое"]
    номер = int(запрос.data.split(":")[1])
    я = двое[номер]
    она = двое[1 - номер]

    await state.set_state(Шаги.выбор_цели)
    await state.update_data(я=я, она=она)
    await _спросить_цель(запрос.message)


# --------------------------------------------------------------------------
# Вставка переписки текстом
# --------------------------------------------------------------------------

def _собрать_куски(куски):
    """Из накопленных сообщений делает одну переписку."""
    return export_parser.read_pasted("\n".join(куски or []))


@диспетчер.callback_query(F.data == "вставить")
async def вставить(запрос: CallbackQuery, state: FSMContext):
    await запрос.answer()
    await state.set_state(Шаги.вставка)
    await state.update_data(куски=[])
    await _послать(запрос.message, texts.ВСТАВКА_КАК)


@диспетчер.message(Шаги.вставка, F.text & ~F.text.startswith("/"))
async def вставка_кусок(сообщение: Message, state: FSMContext):
    данные = await state.get_data()
    куски = данные.get("куски") or []
    if (len(куски) >= ПРЕДЕЛ_КУСКОВ
            or sum(len(кусок) for кусок in куски) >= ПРЕДЕЛ_ВСТАВКИ):
        await _послать(сообщение, texts.ВСТАВКА_ПЕРЕПОЛНЕНО,
                       кнопки=КНОПКА_ГОТОВО)
        return

    куски = куски + [сообщение.text]
    await state.update_data(куски=куски)
    сообщения, _явная = _собрать_куски(куски)
    if not сообщения:
        await _послать(сообщение, texts.ВСТАВКА_ПУСТО)
        return
    сколько = len(сообщения)
    await _послать(сообщение, texts.ВСТАВКА_ПРИНЯЛ % (
        сколько, metrics.склонение(сколько, "сообщение", "сообщения",
                                   "сообщений")), кнопки=КНОПКА_ГОТОВО)


@диспетчер.callback_query(Шаги.вставка, F.data == "вставка_готово")
async def вставка_готово(запрос: CallbackQuery, state: FSMContext):
    await запрос.answer()
    данные = await state.get_data()
    сообщения, явная = _собрать_куски(данные.get("куски"))
    await _принять(запрос.message, state, сообщения, вставка=True, явная=явная)


# --------------------------------------------------------------------------
# Разбор
# --------------------------------------------------------------------------

@диспетчер.callback_query(Шаги.выбор_цели, F.data.startswith("цель:"))
async def разбор(запрос: CallbackQuery, state: FSMContext):
    await запрос.answer()
    user_id = запрос.from_user.id
    код = запрос.data.split(":", 1)[1]
    цель = texts.ЦЕЛИ.get(код, "разобраться, что я делаю не так")

    можно = await asyncio.to_thread(limits.потратить, база, user_id, limits.РАЗБОР)
    if not можно:
        await _послать(запрос.message,
                       texts.ЛИМИТ % limits.текст_лимита(limits.РАЗБОР),
                       кнопки=_кнопки([("Хочу премиум", "премиум:разборы")]))
        return

    данные = await state.get_data()
    сообщения, я, она = данные["сообщения"], данные["я"], данные["она"]

    try:
        итог = await asyncio.to_thread(metrics.analyse, сообщения, я, она)
    except ValueError as сбой:
        await asyncio.to_thread(limits.вернуть, база, user_id, limits.РАЗБОР)
        await _послать(запрос.message, texts.ОШИБКА % сбой)
        return

    await _послать(запрос.message, report.текст_отчёта(итог), моноширинно=True)

    отобранное = await asyncio.to_thread(analysis.выборка, сообщения, я, она)
    # Дальше переписка не нужна: держим только обезличенную выборку.
    await state.update_data(сообщения=None, итог=итог, отобранное=отобранное)

    думаю = await запрос.message.answer(texts.ДУМАЮ)
    try:
        разбор_ии = await asyncio.to_thread(
            analysis.разобрать, None, итог, цель, отобранное=отобранное)
    except analysis.ОшибкаРазбора as сбой:
        await asyncio.to_thread(limits.вернуть, база, user_id, limits.РАЗБОР)
        await _убрать(думаю)
        await _послать(запрос.message, texts.ОШИБКА % сбой)
        return
    finally:
        await asyncio.to_thread(база.записать_отчёт, user_id, итог)

    await _убрать(думаю)
    await _послать(запрос.message, analysis.текст_разбора(разбор_ии))

    await state.set_state(Шаги.готово)
    await _послать(запрос.message, texts.ГОТОВО)


# --------------------------------------------------------------------------
# Вопросы по разобранной переписке
# --------------------------------------------------------------------------

@диспетчер.message(Шаги.готово, F.text & ~F.text.startswith("/"))
async def вопрос(сообщение: Message, state: FSMContext):
    # Вставили новую переписку вместо вопроса — разбираем её, а не отвечаем.
    # Проверка идёт до списания квоты: иначе вставка съедала бы вопрос.
    новая, явная = export_parser.похоже_на_переписку(сообщение.text,
                                                     МИНИМУМ_ВСТАВКИ)
    if новая:
        await _послать(сообщение, texts.ПОХОЖЕ_НА_ПЕРЕПИСКУ)
        await _принять(сообщение, state, новая, вставка=True, явная=явная)
        return

    user_id = сообщение.from_user.id
    можно = await asyncio.to_thread(limits.потратить, база, user_id, limits.ВОПРОС)
    if not можно:
        await _послать(сообщение, texts.ЛИМИТ % limits.текст_лимита(limits.ВОПРОС),
                       кнопки=_кнопки([("Хочу премиум", "премиум:вопросы")]))
        return

    данные = await state.get_data()
    думаю = await сообщение.answer(texts.ДУМАЮ)
    try:
        ответ = await asyncio.to_thread(
            analysis.спросить, данные["итог"], данные["отобранное"],
            сообщение.text)
    except analysis.ОшибкаРазбора as сбой:
        await asyncio.to_thread(limits.вернуть, база, user_id, limits.ВОПРОС)
        await _убрать(думаю)
        await _послать(сообщение, texts.ОШИБКА % сбой)
        return
    await _убрать(думаю)
    await _послать(сообщение, ответ)


@диспетчер.message(F.text & ~F.text.startswith("/"))
async def текст_без_разбора(сообщение: Message, state: FSMContext):
    """Человек прислал текст, ничего не нажимая.

    Если это похоже на переписку — разбираем сразу, не заставляя ходить
    по кнопкам: чем меньше шагов до первого результата, тем лучше.
    """
    сообщения, явная = export_parser.похоже_на_переписку(сообщение.text,
                                                         МИНИМУМ_ВСТАВКИ)
    if сообщения:
        await _послать(сообщение, texts.ПОХОЖЕ_НА_ПЕРЕПИСКУ)
        await _принять(сообщение, state, сообщения, вставка=True, явная=явная)
        return
    await _послать(сообщение, texts.НЕТ_РАЗБОРА, кнопки=НАЧАЛЬНЫЕ_КНОПКИ)


@диспетчер.callback_query(F.data.startswith("премиум:"))
async def премиум(запрос: CallbackQuery):
    await запрос.answer()
    откуда = запрос.data.split(":", 1)[1]
    await asyncio.to_thread(база.хочет_премиум, запрос.from_user.id, откуда)
    await запрос.message.answer(texts.ПРЕМИУМ_ЗАПИСАН)


# --------------------------------------------------------------------------

async def main():
    токен = os.getenv("BOT_TOKEN")
    if not токен:
        raise SystemExit("Не задан BOT_TOKEN. Возьми его у @BotFather.")
    бот = Bot(токен, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    журнал.info("бот запущен, модель %s", analysis.МОДЕЛЬ)
    await диспетчер.start_polling(бот)


if __name__ == "__main__":
    asyncio.run(main())
