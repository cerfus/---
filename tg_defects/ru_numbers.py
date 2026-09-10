"""Распознавание номера квартиры в русском тексте (цифрами и словами)."""

import re

# Основы слов (стемы) -> значение. Проверяются по startswith,
# поэтому покрывают падежи: "двухсотой", "пятидесятого", "двадцать".
HUNDREDS = [
    ("девятьсот", 900), ("девятисот", 900), ("девятисот", 900),
    ("восемьсот", 800), ("восьмисот", 800),
    ("семьсот", 700), ("семисот", 700),
    ("шестьсот", 600), ("шестисот", 600),
    ("пятьсот", 500), ("пятисот", 500),
    ("четыреста", 400), ("четырехсот", 400), ("четырёхсот", 400),
    ("триста", 300), ("трехсот", 300), ("трёхсот", 300),
    ("двести", 200), ("двухсот", 200),
    ("сотый", 100), ("сотая", 100), ("сотой", 100),
    ("сто", 100), ("ста", 100),
]

TEENS = [
    ("одиннадцат", 11), ("двенадцат", 12), ("тринадцат", 13),
    ("четырнадцат", 14), ("пятнадцат", 15), ("шестнадцат", 16),
    ("семнадцат", 17), ("восемнадцат", 18), ("девятнадцат", 19),
]

TENS = [
    ("девяност", 90),
    ("восемьдесят", 80), ("восьмидесят", 80),
    ("семьдесят", 70), ("семидесят", 70),
    ("шестьдесят", 60), ("шестидесят", 60),
    ("пятьдесят", 50), ("пятидесят", 50),
    ("сорок", 40), ("сорока", 40),
    ("тридцат", 30), ("двадцат", 20),
]

UNITS = [
    ("десят", 10),
    ("девят", 9), ("девят", 9),
    ("восем", 8), ("восьм", 8),
    ("седьм", 7), ("сем", 7),
    ("шест", 6),
    ("пят", 5),
    ("четыр", 4), ("четверт", 4), ("четвёрт", 4),
    ("трет", 3), ("три", 3),
    ("втор", 2), ("двум", 2), ("две", 2), ("два", 2),
    ("перв", 1), ("одна", 1), ("одно", 1), ("один", 1),
    ("нол", 0), ("нул", 0),
]

# Слова, которые можно пропустить между "квартира" и числом.
FILLER = ("номер", "№", "под", "это", "значит", "у", "нас", "сейчас", "here",
          "которая", "которой", "будет", "в", "на", "квартира", "квартире",
          "квартиры", "квартиру", "кв", "кв.")

# Сортируем стемы по длине — сначала самые длинные, чтобы "сто" не съедало "стольник".
for _table in (HUNDREDS, TEENS, TENS, UNITS):
    _table.sort(key=lambda p: -len(p[0]))


def _match(token, table):
    for stem, value in table:
        if token.startswith(stem):
            return value
    return None


def _words_to_number(tokens):
    """Складывает число из слов: "двести пятьдесят три" -> 253. None, если не вышло."""
    total = 0
    stage = 0  # 0 = ждём сотни, 1 = ждём десятки, 2 = ждём единицы, 3 = конец
    got = False
    for token in tokens:
        if stage >= 3:
            break
        value = None
        if stage <= 0:
            value = _match(token, HUNDREDS)
            if value is not None:
                total += value
                stage = 1
                got = True
                continue
        if stage <= 1:
            value = _match(token, TEENS)
            if value is not None:
                total += value
                stage = 3
                got = True
                continue
            value = _match(token, TENS)
            if value is not None:
                total += value
                stage = 2
                got = True
                continue
        value = _match(token, UNITS)
        if value is not None:
            total += value
            stage = 3
            got = True
            continue
        if got:
            break
        if token in FILLER:
            continue
        break
    return total if got else None


# "кв 200", "квартира №200", "кв. 200"
_SEP = r"[\s._\-#:]*"
_DIGIT_AFTER = re.compile(
    r"(?:кварт[а-я]*|кв)" + _SEP + r"(?:№|n|no|номер[а-я]*)?" + _SEP + r"(\d{1,4})(?!\d)",
    re.IGNORECASE,
)
# "200 квартира", "200-я квартира"
_DIGIT_BEFORE = re.compile(
    r"(?<!\d)(\d{1,4})" + _SEP + r"(?:-?\s*[аяыои]?\s*[йя])?" + _SEP + r"(?:кварт[а-я]*|кв(?![\s.]*м))(?![а-я])",
    re.IGNORECASE,
)
# "№200" без слова "квартира" — запасной вариант
_HASH_ONLY = re.compile(r"№\s*(\d{1,4})(?!\d)")

# "66кв", "178кв" — номер вплотную к "кв". Такой номер вернее того, что
# стоит после: в "66кв 1об" единица — это номер окна, а не квартиры.
_DIGIT_GLUED = re.compile(
    r"(?<!\d)(\d{1,4})(?:кварт[а-я]*|кв(?![\s.]*м))(?![а-яa-z])",
    re.IGNORECASE,
)

_WORD_ANCHOR = re.compile(r"(?:кварт[а-я]*|\bкв\b(?![\s.]*м))", re.IGNORECASE)
_TOKEN = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def find_apartment(text):
    """Ищет номер квартиры в тексте. Возвращает (номер, способ) или (None, None)."""
    if not text:
        return None, None
    low = text.lower().replace("ё", "е")
    # Приводим стемы к тому же виду
    for pattern, tag in ((_DIGIT_GLUED, "цифры"), (_DIGIT_AFTER, "цифры"),
                         (_DIGIT_BEFORE, "цифры")):
        for m in pattern.finditer(low):
            number = int(m.group(1))
            if 1 <= number <= 9999:
                return number, tag

    # Числительные словами после слова "квартира"
    for anchor in _WORD_ANCHOR.finditer(low):
        tail = low[anchor.end():anchor.end() + 80]
        tokens = _TOKEN.findall(tail)[:6]
        if tokens and tokens[0].isdigit():
            number = int(tokens[0])
            if 1 <= number <= 9999:
                return number, "цифры"
        number = _words_to_number(tokens)
        if number and 1 <= number <= 9999:
            return number, "словами"
        # Числительное перед словом: "двухсотая квартира"
        before = _TOKEN.findall(low[max(0, anchor.start() - 60):anchor.start()])[-4:]
        while before:
            number = _words_to_number(before)
            if number and 1 <= number <= 9999:
                return number, "словами"
            before = before[1:]

    for m in _HASH_ONLY.finditer(low):
        number = int(m.group(1))
        if 1 <= number <= 9999:
            return number, "решетка"
    return None, None
