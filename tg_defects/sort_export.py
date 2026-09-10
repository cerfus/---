#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Раскладывает уже скачанные видео по папкам с номерами квартир.

Нужен, если my.telegram.org не даёт api_id. Тогда видео выгружаются
средствами самого Телеграма:

    Telegram Desktop -> открыть группу "Видео дефектов"
    -> три точки справа сверху -> Экспорт истории чата
    -> отметить "Видеофайлы", снять лишнее
    -> формат: JSON (подписи читаются и из HTML-выгрузки)
    -> размер файла поставить побольше -> Экспортировать

Потом натравить этот скрипт на полученную папку:

    python sort_export.py --from "C:\\Users\\Вы\\Downloads\\ChatExport_2026-09-09"
"""

import argparse
import html as html_module
import json
import os
import re
import shutil
import urllib.parse

from common import (
    REPORT_FILE,
    UNKNOWN_DIR,
    Recognizer,
    append_report,
    check_counts,
    count_defects,
    save_expected,
    forget_unknown,
    default_out,
    has_ffmpeg,
    load_state,
    log,
    neighbour_texts,
    read_setting,
    save_state,
    write_setting,
    unique_path,
)
from ru_numbers import find_apartment

VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm",
             ".3gp", ".wmv", ".mpg", ".mpeg")


def flatten_text(value):
    """В выгрузке текст бывает строкой, а бывает списком кусков."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


def read_export_json(export_dir):
    """Читает result.json: путь к файлу -> сведения о сообщении."""
    path = os.path.join(export_dir, "result.json")
    if not os.path.exists(path):
        return {}, []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (ValueError, OSError) as exc:
        log("! Не смог прочитать result.json (%s), попробую HTML." % exc)
        return {}, []

    skipped_files = 0
    collected = []
    for message in data.get("messages", []):
        relative = message.get("file") or ""
        # Если при выгрузке не отметили "Видеофайлы", Телеграм вместо пути
        # пишет "(File not included...)" — это не файл.
        if relative.startswith("("):
            skipped_files += 1
            relative = ""
        key = os.path.normpath(relative).replace("\\", "/").lower() if relative else ""
        collected.append({
            "файл": key,
            "текст": flatten_text(message.get("text")),
            "дата": (message.get("date") or "").replace("T", " ")[:16],
            "автор": message.get("from") or "",
        })

    info, order = _collect(collected)
    log("Прочитал result.json: сообщений с файлами %d." % len(order))
    if skipped_files:
        log("! В выгрузке %d сообщений без самих файлов — похоже, при экспорте"
            % skipped_files)
        log("  не была отмечена галочка «Видеофайлы» или не хватило лимита размера.")
    return info, order


# --------------------------------------------------------------------------
# Выгрузка в HTML: подписи лежат в messages*.html рядом со ссылками на видео
# --------------------------------------------------------------------------

_TAGS = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="([^"]+\.(?:mp4|mov|avi|mkv|webm|m4v|3gp))"', re.I)
_TEXT = re.compile(r'<div class="text">(.*?)</div>', re.S)
_FROM = re.compile(r'<div class="from_name">(.*?)</div>', re.S)
_DATE = re.compile(r'<div class="pull_right date details" title="([^"]*)"')


def _plain(fragment):
    """Из куска HTML делает обычный текст."""
    if not fragment:
        return ""
    fragment = re.sub(r"<br\s*/?>", " ", fragment)
    return html_module.unescape(_TAGS.sub("", fragment)).strip()


def _html_files(export_dir):
    """messages.html, messages2.html, ... по-человечески по порядку."""
    found = []
    for name in os.listdir(export_dir):
        match = re.fullmatch(r"messages(\d*)\.html", name, re.I)
        if match:
            found.append((int(match.group(1) or 1), os.path.join(export_dir, name)))
    return [path for _number, path in sorted(found)]


def _collect(collected):
    """Из списка сообщений делает сведения по файлам и порядок, как в чате."""
    рядом = neighbour_texts(collected)
    info, order = {}, []
    for item in collected:
        if not item["файл"] or item["файл"] in info:
            continue
        order.append(item["файл"])
        info[item["файл"]] = {
            "подпись": item["текст"],
            "рядом": рядом.get(item["файл"], ""),
            "дата": item["дата"],
            "автор": item["автор"],
        }
    return info, order


def read_export_html(export_dir):
    """Читает messages*.html: путь к файлу -> сведения о сообщении."""
    pages = _html_files(export_dir)
    if not pages:
        return {}, []

    # Сначала собираем сообщения подряд, чтобы потом видеть соседей.
    collected = []
    for page in pages:
        try:
            with open(page, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            log("! Не смог прочитать %s: %s" % (os.path.basename(page), exc))
            continue
        blocks = content.split('<div class="message ')[1:]
        for block in blocks:
            link = _HREF.search(block)
            relative = ""
            if link:
                relative = urllib.parse.unquote(link.group(1))
                relative = os.path.normpath(relative).replace("\\", "/").lower()
            author = _FROM.search(block)
            date = _DATE.search(block)
            collected.append({
                "файл": relative,
                "текст": _plain(_TEXT.search(block).group(1)) if _TEXT.search(block) else "",
                "автор": _plain(author.group(1)) if author else "",
                "дата": (date.group(1) if date else "")[:16],
            })

    info, order = _collect(collected)
    log("Прочитал выгрузку HTML (%d страниц): сообщений с файлами %d."
        % (len(pages), len(order)))
    return info, order


def find_videos(export_dir, order):
    """Видео в порядке чата: сначала те, что есть в выгрузке, потом остальные.

    Порядок важен: видео одной квартиры идут подряд, и по нему достраиваются
    ролики без подписи.
    """
    on_disk = {}
    for folder, _dirs, names in os.walk(export_dir):
        for name in names:
            if name.lower().endswith(VIDEO_EXT):
                path = os.path.join(folder, name)
                key = os.path.relpath(path, export_dir).replace("\\", "/").lower()
                on_disk[key] = path

    ordered, taken = [], set()
    for key in order:
        if key in on_disk and key not in taken:
            ordered.append(on_disk[key])
            taken.add(key)
    for key in sorted(on_disk):
        if key not in taken:
            ordered.append(on_disk[key])
            taken.add(key)
    return ordered


def detect_from_text(video_path, meta):
    """Быстрый разбор: подпись, имя файла, однозначный соседний текст."""
    caption = (meta.get("подпись") or "").strip()
    number, how = find_apartment(caption)
    if number:
        return number, "подпись (%s)" % how, caption

    name = os.path.basename(video_path)
    number, how = find_apartment(name)
    if number:
        return number, "имя файла (%s)" % how, name

    neighbour = (meta.get("рядом") or "").strip()
    if neighbour:
        number, how = find_apartment(neighbour)
        if number:
            return number, "соседнее сообщение (%s)" % how, neighbour

    return None, "не определено", caption


def detect_from_video(video_path, recognizer):
    """Дорогой разбор: речь и текст на кадрах. Только для остатка."""
    speech = recognizer.transcribe(video_path)
    if speech:
        number, how = find_apartment(speech)
        if number:
            return number, "речь в видео (%s)" % how, speech

    on_screen = recognizer.read_frames(video_path)
    if on_screen:
        number, how = find_apartment(on_screen)
        if number:
            return number, "текст на кадре (%s)" % how, on_screen

    return None, "не определено", speech


def spread_by_caption(found):
    """Достраивает видео без подписи по подписи предыдущего ролика.

    Подпись перечисляет дефекты («Об1 стп1,2 Об2 стп1» — три стеклопакета),
    и ровно столько видео снимают для квартиры. Поэтому следующие ролики
    без подписи относятся к ней же — но не больше, чем обещано в подписи.
    Счётчик не даёт достройке уползти на соседнюю квартиру.
    """
    current, left = None, 0
    for item in found:
        caption = (item["подпись"] or "").strip()
        if item["квартира"]:
            promised = count_defects(caption)
            if item["квартира"] == current:
                left += promised          # та же квартира, подпись добавляет дефекты
            else:
                current, left = item["квартира"], promised
            left = max(left - 1, 0)       # это видео занимает один слот
        elif left > 0 and current:
            item["квартира"] = current
            item["как"] = "достроено по подписи кв %d" % current
            left -= 1
    return found


def ask_folder():
    log("")
    log("Укажи папку с выгрузкой из Телеграма.")
    log("Это папка вида  ChatExport_2026-09-09  — обычно в Загрузках.")
    log("Проще всего: открой её в проводнике, скопируй путь из адресной")
    log("строки и вставь сюда правой кнопкой мыши.")
    log("")
    previous = read_setting("export", "last_folder", "")
    if previous and os.path.isdir(previous):
        log("В прошлый раз была: %s" % previous)
        log("Нажми Enter, чтобы взять её же, или вставь другой путь.")
    while True:
        entered = input("Папка: ").strip().strip('"')
        if not entered and previous and os.path.isdir(previous):
            return previous
        if entered and os.path.isdir(entered):
            return entered
        log("  Такой папки нет. Проверь путь и попробуй ещё раз.")


HINTS_FILE = "_подсказки.txt"


def write_hints(root, found, promised):
    """Для каждого нераспознанного видео подсказывает соседние квартиры.

    Видео одной квартиры идут в чате подряд, так что ролик почти наверняка
    относится к той квартире, что стоит до него или сразу после. Если у
    такой квартиры вдобавок недобор — это почти наверняка её видео.
    """
    unknown = [i for i, item in enumerate(found) if not item["квартира"]]
    if not unknown:
        return

    # Сколько видео уже лежит у каждой квартиры
    actual = {}
    for item in found:
        if item["квартира"]:
            actual[item["квартира"]] = actual.get(item["квартира"], 0) + 1

    def недобор(flat):
        want = promised.get(flat)
        if want is None:
            return 0
        return max(want - actual.get(flat, 0), 0)

    lines = ["Подсказки для папки «%s»" % UNKNOWN_DIR,
             "-" * 62,
             "Видео одной квартиры идут подряд, поэтому смотри на соседей.",
             "«недобор N» — у этой квартиры не хватает N видео, скорее всего её.",
             ""]
    for index in unknown:
        before = after = None
        for j in range(index - 1, -1, -1):
            if found[j]["квартира"]:
                before = found[j]["квартира"]
                break
        for j in range(index + 1, len(found)):
            if found[j]["квартира"]:
                after = found[j]["квартира"]
                break

        подсказка = []
        for flat, где in ((before, "до"), (after, "после")):
            if flat is None:
                continue
            не_хватает = недобор(flat)
            подсказка.append("%s — кв %d%s" % (
                где, flat, (" (недобор %d)" % не_хватает) if не_хватает else ""))
        lines.append("%-36s %s" % (os.path.basename(found[index]["путь"]),
                                   ", ".join(подсказка) or "соседей нет"))

    path = os.path.join(root, HINTS_FILE)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        log("Подсказки по нераспознанным видео: %s" % path)
    except OSError as exc:
        log("! Не смог записать подсказки: %s" % exc)


def fill_backwards(found):
    """Добирает недостающие видео из тех, что идут ПЕРЕД подписью.

    Одни авторы пишут подпись первым сообщением альбома, другие — последним.
    Прямая достройка ловит первый случай, эта — второй. Берём строго столько,
    сколько подпись ещё недосчиталась, и только подряд идущие ролики вплотную
    перед ней, поэтому лишнего забрать не можем.
    """
    promised, actual = {}, {}
    for item in found:
        number, caption = item["квартира"], (item["подпись"] or "").strip()
        if number:
            actual[number] = actual.get(number, 0) + 1
            if caption and find_apartment(caption)[0] == number:
                promised[number] = promised.get(number, 0) + count_defects(caption)

    added = 0
    for index, item in enumerate(found):
        number = item["квартира"]
        caption = (item["подпись"] or "").strip()
        if not number or not caption or find_apartment(caption)[0] != number:
            continue
        short = promised.get(number, 0) - actual.get(number, 0)
        if short <= 0:
            continue
        # Идём назад по нераспознанным, пока не упрёмся в чужое видео.
        position = index - 1
        while short > 0 and position >= 0 and not found[position]["квартира"]:
            found[position]["квартира"] = number
            found[position]["как"] = "достроено назад по подписи кв %d" % number
            actual[number] = actual.get(number, 0) + 1
            short -= 1
            added += 1
            position -= 1
    return found, added


def main():
    parser = argparse.ArgumentParser(
        description="Раскладывает скачанные видео по папкам с номерами квартир")
    parser.add_argument("--from", dest="source", default=None,
                        help="папка с выгрузкой из Телеграма (спросит, если не указать)")
    parser.add_argument("--out", default=default_out(),
                        help="куда раскладывать (по умолчанию %s)" % default_out())
    parser.add_argument("--move", action="store_true",
                        help="переносить файлы, а не копировать (экономит место)")
    parser.add_argument("--speech", action="store_true",
                        help="распознавать речь в видео без подписи (долго; "
                             "в этих видео номер обычно не проговаривают)")
    parser.add_argument("--whisper-model", default="small",
                        help="модель распознавания речи: tiny/base/small/medium/large-v3")
    parser.add_argument("--seconds", type=int, default=40,
                        help="сколько секунд начала видео слушать")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"],
                        help="на чём распознавать речь (по умолчанию auto, "
                             "при неудаче сам перейдёт на cpu)")
    parser.add_argument("--ocr", action="store_true",
                        help="дополнительно читать номер с кадров")
    parser.add_argument("--no-spread", action="store_true",
                        help="не достраивать видео без подписи по соседней подписи")
    parser.add_argument("--per-flat", type=int, default=4,
                        help="сколько видео ожидается в квартире (по числу окон, "
                             "по умолчанию 4; 0 — не проверять)")
    parser.add_argument("--redo-unknown", action="store_true",
                        help="заново обработать то, что попало в 'неопознанно'")
    args = parser.parse_args()

    source = args.source or ask_folder()
    if not os.path.isdir(source):
        raise SystemExit("Папка не найдена: %s" % source)
    write_setting("export", "last_folder", source)

    root = args.out
    os.makedirs(root, exist_ok=True)

    state = load_state(root)
    if args.redo_unknown:
        state = forget_unknown(root, state)

    info, order = read_export_json(source)
    if not info:
        info, order = read_export_html(source)   # выгрузка в HTML
    if not info:
        log("! Подписей к видео нет: в папке ни result.json, ни messages.html.")
        log("  Номер будет определяться только по имени файла.")

    videos = find_videos(source, order)
    log("Нашёл видео: %d" % len(videos))
    if not videos:
        raise SystemExit("В этой папке видео нет. Проверь, что выгрузка "
                         "делалась с галочкой «Видеофайлы».")

    # Шаг 1. Дешёвый разбор по тексту — он же задаёт опору для достройки.
    found = []
    for video_path in videos:
        relative = os.path.relpath(video_path, source).replace("\\", "/").lower()
        meta = info.get(relative, {})
        number, how, source_text = detect_from_text(video_path, meta)
        found.append({"путь": video_path, "ключ": relative, "мета": meta,
                      "квартира": number, "как": how, "текст": source_text,
                      "подпись": (meta.get("подпись") or "")})

    by_caption = sum(1 for item in found if item["квартира"])
    log("По подписям и именам файлов: %d из %d" % (by_caption, len(found)))

    # Шаг 2. Достройка: видео одной квартиры идут подряд, а подпись говорит,
    # сколько их должно быть.
    if not args.no_spread:
        found = spread_by_caption(found)
        spread = sum(1 for item in found
                     if item["квартира"] and item["как"].startswith("достроено"))
        if spread:
            log("Достроено вперёд по подписям: %d" % spread)
        found, added = fill_backwards(found)
        if added:
            log("Достроено назад (подпись после видео): %d" % added)

    # Шаг 3. Речь — только по остатку и только если попросили.
    left = [item for item in found if not item["квартира"]]
    if left and args.speech:
        log("Осталось без номера: %d — слушаю их." % len(left))
        recognizer = Recognizer(args.whisper_model, args.seconds, args.ocr,
                                enabled=True, device=args.device)
        if not has_ffmpeg():
            log("! ffmpeg не найден — распознать речь не выйдет.")
        for position, item in enumerate(left, 1):
            log("[%d/%d] слушаю %s" % (position, len(left),
                                       os.path.basename(item["путь"])))
            number, how, text = detect_from_video(item["путь"], recognizer)
            if number:
                item["квартира"], item["как"] = number, how
            if text:
                item["текст"] = text
    elif left:
        log("Осталось без номера: %d (распознавание речи выключено, "
            "включается ключом --speech)" % len(left))

    # Шаг 4. Раскладываем по папкам.
    counters = {"разложено": 0, "пропущено": 0, "неопознано": 0, "ошибок": 0}
    for position, item in enumerate(found, 1):
        relative, video_path = item["ключ"], item["путь"]
        if relative in state and os.path.exists(state[relative].get("файл", "")):
            counters["пропущено"] += 1
            continue

        apartment = item["квартира"]
        folder_name = str(apartment) if apartment else UNKNOWN_DIR
        target_dir = os.path.join(root, folder_name)
        os.makedirs(target_dir, exist_ok=True)

        meta = item["мета"]
        prefix = "кв%s" % apartment if apartment else "неопознанно"
        stamp = (meta.get("дата") or "").replace(":", "-").replace(" ", "_")
        base = os.path.splitext(os.path.basename(video_path))[0]
        ext = os.path.splitext(video_path)[1] or ".mp4"
        final_path = unique_path(target_dir, "%s_%s%s%s" % (
            prefix, stamp + "_" if stamp else "", base, ext))

        try:
            if args.move:
                shutil.move(video_path, final_path)
            else:
                shutil.copy2(video_path, final_path)
        except (OSError, shutil.Error) as exc:
            log("  ! %s: не удалось положить файл: %s"
                % (os.path.basename(video_path), exc))
            counters["ошибок"] += 1
            continue

        log("[%d/%d] %-34s -> %s" % (position, len(found),
                                     os.path.basename(video_path)[:34],
                                     folder_name))
        counters["разложено"] += 1
        if not apartment:
            counters["неопознано"] += 1

        state[relative] = {"папка": folder_name, "файл": final_path,
                           "квартира": apartment, "как": item["как"]}
        save_state(root, state)
        append_report(root, [
            os.path.basename(video_path),
            meta.get("дата", ""),
            meta.get("автор", ""),
            apartment or "",
            item["как"],
            (item["текст"] or "").replace("\n", " ")[:300],
            final_path,
        ])

    log("")
    log("Готово. Разложено: %(разложено)d, пропущено (уже было): %(пропущено)d, "
        "без номера: %(неопознано)d, ошибок: %(ошибок)d" % counters)
    log("Отчёт: %s" % os.path.join(root, REPORT_FILE))
    if counters["неопознано"]:
        log("Видео из папки '%s' разложи руками — в отчёте видно, "
            "что о них известно." % UNKNOWN_DIR)

    # Сколько видео обещали подписи — по этому и будем сверять.
    promised = {}
    for item in found:
        number, caption = item["квартира"], (item["подпись"] or "").strip()
        if number and caption and find_apartment(caption)[0] == number:
            promised[number] = promised.get(number, 0) + count_defects(caption)
    if promised:
        save_expected(root, promised)

    write_hints(root, found, promised)

    log("")
    check_counts(root, args.per_flat)


if __name__ == "__main__":
    main()
