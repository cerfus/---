# -*- coding: utf-8 -*-
"""Проверка поиска лишних копий. Запуск: python test_dupes.py

Самое важное здесь — что удаление ничего не сносит в сомнительных случаях.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))


def разложить(root, раскладка, состояние_корень=None):
    """раскладка: {папка: [имена файлов]}. Память пишем от указанного корня."""
    состояние = {}
    for папка, имена in раскладка.items():
        os.makedirs(os.path.join(root, папка), exist_ok=True)
        for имя in имена:
            путь = os.path.join(root, папка, имя)
            with open(путь, "wb") as fh:
                fh.write(b"\0" * 512)
            исходное = имя.split("_")[-1]
            состояние[("video_files/" + исходное).lower()] = {
                "папка": папка,
                "файл": os.path.join(состояние_корень or root, папка, имя),
                "квартира": int(папка) if папка.isdigit() else None,
                "как": "подпись",
            }
    with open(os.path.join(root, "_состояние.json"), "w", encoding="utf-8") as fh:
        json.dump(состояние, fh, ensure_ascii=False)


def запустить(root, delete=False):
    cmd = [sys.executable, os.path.join(ЗДЕСЬ, "check_dupes.py"), "--out", root]
    if delete:
        cmd.append("--delete")
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ЗДЕСЬ).stdout


def видео(root):
    return sum(1 for папка, _d, имена in os.walk(root)
               for имя in имена if имя.lower().endswith((".mov", ".mp4")))


def случай_настоящий_дубль():
    root = tempfile.mkdtemp()
    разложить(root, {"65": ["кв65_05.08_IMG_0905.MOV"]})
    shutil.copy(os.path.join(root, "65", "кв65_05.08_IMG_0905.MOV"),
                os.path.join(root, "65", "кв65_01.01_IMG_0905.MOV"))
    запустить(root, delete=True)
    осталось = видео(root)
    shutil.rmtree(root)
    return осталось == 1, "настоящий дубль удаляется (осталось %d, ждём 1)" % осталось


def случай_папку_перенесли():
    root = tempfile.mkdtemp()
    # Память записана от старого места — раскладку переносили
    разложить(root, {"65": ["кв65_05.08_IMG_0905.MOV"],
                     "70": ["кв70_05.08_IMG_7281.MOV"]},
              состояние_корень=r"X:\Старое\Стекла")
    запустить(root, delete=True)
    осталось = видео(root)
    shutil.rmtree(root)
    return осталось == 2, "перенос папки не ломает учёт (осталось %d, ждём 2)" % осталось


def случай_чужая_память():
    root = tempfile.mkdtemp()
    разложить(root, {"65": ["кв65_05.08_IMG_0905.MOV"],
                     "70": ["кв70_05.08_IMG_7281.MOV"]})
    with open(os.path.join(root, "_состояние.json"), "w", encoding="utf-8") as fh:
        json.dump({"чужое/видео.mp4": {"папка": "777",
                                       "файл": r"X:\Другое\777\кв777.mp4"}},
                  fh, ensure_ascii=False)
    вывод = запустить(root, delete=True)
    осталось = видео(root)
    shutil.rmtree(root)
    ок = осталось == 2 and "СТОП" in вывод
    return ок, "чужая память останавливает удаление (осталось %d, ждём 2)" % осталось


def случай_остатки_без_памяти():
    """Файлы в папках есть, памяти нет — раскладка должна остановиться."""
    from common import clean_layout, count_placed
    root = tempfile.mkdtemp()
    разложить(root, {"65": ["кв65_05.08_IMG_0905.MOV"],
                     "70": ["кв70_05.08_IMG_7281.MOV"],
                     "неопознанно": ["неопознанно_IMG_1.MOV"]})
    сколько = count_placed(root)
    # Посторонний файл рядом сноситься не должен
    чужой = os.path.join(root, "мои заметки.txt")
    open(чужой, "w").close()
    clean_layout(root)
    осталось = видео(root)
    цел = os.path.exists(чужой)
    shutil.rmtree(root)
    ок = сколько == 3 and осталось == 0 and цел
    return ок, ("учёт=%d осталось=%d чужой цел=%s" % (сколько, осталось, цел))


def main():
    failed = 0
    for случай in (случай_настоящий_дубль, случай_папку_перенесли,
                   случай_чужая_память, случай_остатки_без_памяти):
        ок, описание = случай()
        if not ок:
            failed += 1
            print("ПЛОХО %s" % описание)
    print("Проверок: 4, провалов: %d" % failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
