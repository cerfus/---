# -*- coding: utf-8 -*-
"""Раскладка выгрузки обычным текстом (MAX и подобные).

Запуск: python test_text_export.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))

ЛОГ = """[05.08.2026, 19:04] Тема: Кв 60  Об1-стп1,2 Об2-стп 1
[05.08.2026, 19:04] Тема: <прикреплён: video_0001.mp4>
[05.08.2026, 19:04] Тема: <прикреплён: video_0002.mp4>
[05.08.2026, 19:04] Тема: <прикреплён: video_0003.mp4>
[05.08.2026, 19:05] Андрей: Квартира 65 Об2 стп 1 -1.5 Об2 стп 2 -1 Общ 2.5
[05.08.2026, 19:05] Андрей: video_0004.mp4
[05.08.2026, 19:05] Андрей: video_0005.mp4
[06.08.2026, 10:00] Палкан: 178кв об2сп1
[06.08.2026, 10:00] Палкан: video_0006.mp4
"""

ЖДЁМ = {
    "video_0001.mp4": "60", "video_0002.mp4": "60", "video_0003.mp4": "60",
    "video_0004.mp4": "65", "video_0005.mp4": "65",
    "video_0006.mp4": "178",
}


def main():
    источник = tempfile.mkdtemp()
    приёмник = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(источник, "media"))
        with open(os.path.join(источник, "чат.txt"), "w", encoding="utf-8") as fh:
            fh.write(ЛОГ)
        for i in range(1, 7):
            with open(os.path.join(источник, "media", "video_%04d.mp4" % i), "wb") as fh:
                fh.write(b"\0" * 512)

        subprocess.run([sys.executable, os.path.join(ЗДЕСЬ, "sort_export.py"),
                        "--from", источник, "--out", приёмник],
                       capture_output=True, text=True, cwd=ЗДЕСЬ)

        разложено = {}
        for папка in os.listdir(приёмник):
            путь = os.path.join(приёмник, папка)
            if os.path.isdir(путь):
                for имя in os.listdir(путь):
                    исходное = имя.split("_")[-2] + "_" + имя.split("_")[-1]
                    разложено[исходное] = папка

        failed = 0
        for файл, папка in sorted(ЖДЁМ.items()):
            получено = разложено.get(файл)
            if получено != папка:
                failed += 1
                print("ПЛОХО %-18s ждём кв %s, получили %s" % (файл, папка, получено))
        print("Проверок: %d, провалов: %d" % (len(ЖДЁМ), failed))
        return 1 if failed else 0
    finally:
        shutil.rmtree(источник, ignore_errors=True)
        shutil.rmtree(приёмник, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
