# -*- coding: utf-8 -*-
"""Проверка разбора выгрузок. Запуск: python test_parser.py

Выгрузки собираются прямо здесь, во временной папке: настоящая переписка
в репозиторий не попадает и попасть не может.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime

import parser


def случай_json():
    """result.json: служебное выкидываем, куски текста склеиваем, фото считаем."""
    выгрузка = {
        "name": "Аня",
        "type": "personal_chat",
        "messages": [
            {"id": 1, "type": "service", "date": "2026-08-05T19:00:00",
             "actor": "Аня", "action": "phone_call"},
            {"id": 2, "type": "message", "date": "2026-08-05T19:04:00",
             "date_unixtime": "1754413440", "from": "Иван", "text": "привет"},
            {"id": 3, "type": "message", "date": "2026-08-05T19:06:00",
             "from": "Аня",
             "text": ["глянь ", {"type": "link", "text": "https://site.ru"},
                      " тут смешно"]},
            {"id": 4, "type": "message", "date": "2026-08-05T19:07:00",
             "from": "Аня", "text": "", "photo": "photos/photo_1.jpg"},
        ],
    }
    папка = tempfile.mkdtemp()
    try:
        путь = os.path.join(папка, "result.json")
        with open(путь, "w", encoding="utf-8") as fh:
            json.dump(выгрузка, fh, ensure_ascii=False)
        сообщения = parser.read_export(путь)
    finally:
        shutil.rmtree(папка, ignore_errors=True)

    склеено = сообщения[1]["текст"] if len(сообщения) > 1 else ""
    ок = (len(сообщения) == 3
          and сообщения[0]["автор"] == "Иван"
          and сообщения[0]["дата"] == datetime(2026, 8, 5, 19, 4)
          and склеено == "глянь https://site.ru тут смешно"
          and сообщения[2]["вложение"] is True
          and сообщения[2]["текст"] == "")
    return ок, ("json: сообщений=%d склейка=%r вложение=%s"
                % (len(сообщения), склеено,
                   сообщения[2]["вложение"] if len(сообщения) > 2 else "нет"))


СТРАНИЦА = """<html><body>
<div class="message default clearfix" id="message1">
 <div class="body">
  <div class="pull_right date details" title="05.08.2026 19:04:05 UTC+03:00">19:04</div>
  <div class="from_name">Иван</div>
  <div class="text">привет, как ты?</div>
 </div>
</div>
<div class="message default clearfix joined" id="message2">
 <div class="body">
  <div class="pull_right date details" title="05.08.2026 19:05:00 UTC+03:00">19:05</div>
  <div class="text">я тут подумал&#33;</div>
 </div>
</div>
<div class="message service" id="service3">
 <div class="body details">5 августа 2026</div>
</div>
<div class="message default clearfix" id="message4">
 <div class="body">
  <div class="pull_right date details" title="05.08.2026 21:30:00 UTC+03:00">21:30</div>
  <div class="from_name">Аня</div>
  <div class="text">привет&#10;и тебе</div>
 </div>
</div>
</body></html>"""


def случай_html():
    """messages.html: склеенные сообщения наследуют автора, служебное выкидываем."""
    папка = tempfile.mkdtemp()
    try:
        with open(os.path.join(папка, "messages.html"), "w", encoding="utf-8") as fh:
            fh.write(СТРАНИЦА)
        сообщения = parser.read_export(папка)
    finally:
        shutil.rmtree(папка, ignore_errors=True)

    авторы = [с["автор"] for с in сообщения]
    ок = (len(сообщения) == 3
          and авторы == ["Иван", "Иван", "Аня"]
          and сообщения[1]["текст"] == "я тут подумал!"
          and сообщения[2]["дата"] == datetime(2026, 8, 5, 21, 30))
    return ок, "html: авторы=%s сообщений=%d" % (авторы, len(сообщения))


ЛОГ = """[05.08.2026, 19:04] Иван: привет
[05.08.2026, 19:06] Аня: привет!
это вторая строка того же сообщения
06.08.2026, 10:00 - Иван: как дела?
[06.08.2026, 10:30] Аня: <прикреплён: photo_1.jpg>
"""


def случай_текст():
    """Текстовый лог: продолжение строки, формат через тире, пометка вложения."""
    папка = tempfile.mkdtemp()
    try:
        путь = os.path.join(папка, "чат.txt")
        with open(путь, "w", encoding="utf-8") as fh:
            fh.write(ЛОГ)
        сообщения = parser.read_export(путь)
    finally:
        shutil.rmtree(папка, ignore_errors=True)

    многострочное = сообщения[1]["текст"] if len(сообщения) > 1 else ""
    ок = (len(сообщения) == 4
          and многострочное == "привет!\nэто вторая строка того же сообщения"
          and сообщения[2]["автор"] == "Иван"
          and сообщения[2]["дата"] == datetime(2026, 8, 6, 10, 0)
          and сообщения[3]["вложение"] is True)
    return ок, ("текст: сообщений=%d многострочное=%r"
                % (len(сообщения), многострочное))


def случай_собеседники():
    """Двое главных находятся, а групповой чат опознаётся как групповой."""
    def чат(пары):
        return [{"автор": имя, "дата": None, "текст": "ы", "вложение": False}
                for имя, сколько in пары for _ in range(сколько)]

    двое, группа_нет = parser.two_sides(чат([("Иван", 10), ("Аня", 9), ("Бот", 1)]))
    _, группа_да = parser.two_sides(чат([("Иван", 10), ("Аня", 10), ("Петя", 5)]))

    ок = (sorted(двое) == ["Аня", "Иван"]
          and группа_нет is False and группа_да is True)
    return ок, ("собеседники: двое=%s личный=%s групповой=%s"
                % (двое, not группа_нет, группа_да))


def случай_архив():
    """zip с папкой выгрузки читается, а вылезти за её пределы не даёт."""
    import zipfile

    папка = tempfile.mkdtemp()
    try:
        путь = os.path.join(папка, "ChatExport.zip")
        выгрузка = {"messages": [
            {"id": 1, "type": "message", "date": "2026-08-05T19:04:00",
             "from": "Иван", "text": "привет"},
            {"id": 2, "type": "message", "date": "2026-08-05T19:06:00",
             "from": "Аня", "text": "привет!"},
        ]}
        with zipfile.ZipFile(путь, "w") as зип:
            зип.writestr("ChatExport_2026-09-13/result.json",
                         json.dumps(выгрузка, ensure_ascii=False))
            # Запись, которая пытается записаться выше папки распаковки.
            зип.writestr("../сбежал.txt", "меня тут быть не должно")

        сообщения = parser.read_export(путь)
        сбежал = os.path.exists(os.path.join(папка, "сбежал.txt"))
        ок = len(сообщения) == 2 and сообщения[0]["автор"] == "Иван" and not сбежал
        return ок, "архив: сообщений=%d, файл сбежал=%s" % (len(сообщения), сбежал)
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def случай_дата():
    """Разные написания даты приводятся к одному виду, мусор — к None."""
    проверки = [
        ("2026-08-05T19:04:05", datetime(2026, 8, 5, 19, 4, 5)),
        ("05.08.2026 19:04:05 UTC+03:00", datetime(2026, 8, 5, 19, 4, 5)),
        ("05.08.26, 19:04", datetime(2026, 8, 5, 19, 4)),
        ("31.02.2026 10:00", None),        # такого дня не бывает
        ("вчера вечером", None),
    ]
    плохие = [текст for текст, ждём in проверки if parser.parse_date(текст) != ждём]
    return not плохие, "дата: не разобрано %s" % (плохие or "ничего")


def main():
    случаи = (случай_json, случай_html, случай_текст, случай_архив,
              случай_собеседники, случай_дата)
    провалов = 0
    for случай in случаи:
        ок, описание = случай()
        if not ок:
            провалов += 1
            print("ПЛОХО  %s" % описание)
    print("Проверок: %d, провалов: %d" % (len(случаи), провалов))
    return 1 if провалов else 0


if __name__ == "__main__":
    raise SystemExit(main())
