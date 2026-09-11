# -*- coding: utf-8 -*-
"""Проверка раскладки по заполненному вручную списку.

Запуск: python test_by_list.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
СПИСОК = "_список.csv"

# Номер стоит только у первой строки квартиры — как и заполняет человек.
НОМЕРА = {1: "60", 4: "кв 65", 6: "178", 7: "нет"}
ЖДЁМ = {"video_0001.mp4": "60", "video_0002.mp4": "60", "video_0003.mp4": "60",
        "video_0004.mp4": "65", "video_0005.mp4": "65",
        "video_0006.mp4": "178",
        # "нет" — явный отказ: квартиру строкой выше не наследуем
        "video_0007.mp4": "неопознанно"}


def запустить(*args):
    return subprocess.run([sys.executable, os.path.join(ЗДЕСЬ, "by_list.py")]
                          + list(args), capture_output=True, text=True, cwd=ЗДЕСЬ)


def проверить_порядок():
    """Номера, выбивающиеся из порядка, должны находиться."""
    from by_list import проверить_подряд

    def с(имя, кв):
        return [0, имя, "", "", кв, ""]

    случаи = [
        ("одиночка между своими",
         [с("a", "37"), с("b", "37"), с("c", "58"), с("d", "37"), с("e", "37")],
         ["c"]),
        ("номер в двух местах",
         [с("a", "37"), с("b", "37"), с("c", "58"), с("d", "58"), с("e", "37")],
         ["a", "e"]),
        ("всё подряд — претензий нет",
         [с("a", "37"), с("b", "37"), с("c", "58"), с("d", "58")],
         []),
        ("пустые клетки не мешают",
         [с("a", "37"), с("b", ""), с("c", ""), с("d", "58"), с("e", "58")],
         []),
    ]
    сбоев = 0
    for имя, строки, ждём in случаи:
        получено = [ф for ф, _ in проверить_подряд(строки)]
        if получено != ждём:
            сбоев += 1
            print("ПЛОХО %-26s ждём=%s получили=%s" % (имя, ждём, получено))
    return сбоев


def main():
    источник = tempfile.mkdtemp()
    приёмник = tempfile.mkdtemp()
    try:
        # Видео сняты подряд, группами
        for i, сдвиг in enumerate([0, 60, 120, 600, 660, 1200, 1260], 1):
            путь = os.path.join(источник, "video_%04d.mp4" % i)
            with open(путь, "wb") as fh:
                fh.write(b"\0" * 512)
            когда = 1780000000 + сдвиг
            os.utime(путь, (когда, когда))

        запустить("--from", источник)
        путь_списка = os.path.join(источник, СПИСОК)
        if not os.path.exists(путь_списка):
            print("ПЛОХО список не создан")
            return 1

        # Вписываем номер именно в колонку «квартира», а не в конец строки
        строки = open(путь_списка, encoding="utf-8-sig").read().splitlines()
        заголовок = строки[0].split(";")
        колонка = заголовок.index("квартира")
        новые = [строки[0]]
        for i, строка in enumerate(строки[1:], 1):
            поля = строка.split(";")
            while len(поля) <= колонка:
                поля.append("")
            поля[колонка] = НОМЕРА.get(i, "")
            новые.append(";".join(поля))
        with open(путь_списка, "w", encoding="utf-8-sig") as fh:
            fh.write("\n".join(новые) + "\n")

        запустить("--from", источник, "--out", приёмник, "--sort")

        разложено = {}
        for папка in os.listdir(приёмник):
            путь = os.path.join(приёмник, папка)
            if os.path.isdir(путь):
                for имя in os.listdir(путь):
                    разложено[имя.split("_", 1)[1]] = папка

        failed = 0
        for файл, папка in sorted(ЖДЁМ.items()):
            получено = разложено.get(файл)
            if получено != папка:
                failed += 1
                print("ПЛОХО %-18s ждём кв %s, получили %s" % (файл, папка, получено))
        failed += проверить_порядок()
        print("Проверок: %d, провалов: %d" % (len(ЖДЁМ) + 4, failed))
        return 1 if failed else 0
    finally:
        shutil.rmtree(источник, ignore_errors=True)
        shutil.rmtree(приёмник, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
