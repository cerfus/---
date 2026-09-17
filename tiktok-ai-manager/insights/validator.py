#!/usr/bin/env python3
"""Валидатор формулировок выводов.

Проверяет НЕ смысл, а форму: наличие запрещённых причинных конструкций,
обязательной модальности у гипотез и указания размера выборки. Это
эвристика, и она честно заявлена эвристикой: перефразированием её обойти
можно. Структурные гарантии живут в CHECK-ограничениях, здесь — второй,
более слабый рубеж.
"""
import re

VALIDATOR_VERSION = "claim-validator-1.0.0"

# Причинные конструкции. Запрещены в любом выводе, кроме опирающегося на
# завершённый эксперимент.
CAUSAL_PATTERNS = [
    (r"\bвызыва(ет|ют|л|ли)\b", "вызывает"),
    (r"\bприв(одит|одят|ёл|ели)\s+к\b", "приводит к"),
    (r"\bулучша(ет|ют|л|ли)\b", "улучшает"),
    (r"\bповыша(ет|ют|л|ли)\b", "повышает"),
    (r"\bснижа(ет|ют|л|ли)\b", "снижает"),
    (r"\bвлия(ет|ют|л|ли)\s+на\b", "влияет на"),
    (r"\bобеспечива(ет|ют)\b", "обеспечивает"),
    (r"\bблагодаря\b", "благодаря"),
    (r"\bиз-за\b", "из-за"),
    (r"\bпотому\s+что\b", "потому что"),
    (r"\bпоэтому\b", "поэтому"),
    (r"\bследовательно\b", "следовательно"),
    (r"\bcauses?\b", "causes"),
    (r"\bimproves?\b", "improves"),
    (r"\bleads?\s+to\b", "leads to"),
    (r"\bresponsible\s+for\b", "responsible for"),
    (r"\bdrives?\b", "drives"),
    (r"\bbecause\s+of\b", "because of"),
]

# Оценочные и рекомендательные конструкции: Phase 5 измеряет и описывает.
JUDGEMENT_PATTERNS = [
    (r"\bлучш(е|ий|ая|ие)\b", "лучше"),
    (r"\bхудш(е|ий|ая|ие)\b", "хуже"),
    (r"\bстоит\s+(снимать|делать|публиковать)\b", "стоит делать"),
    (r"\bнужно\s+(снимать|делать|публиковать)\b", "нужно делать"),
    (r"\bрекоменду(ю|ем|ется)\s+снимать\b", "рекомендуется снимать"),
    (r"\bточно\s+зайдёт\b", "точно зайдёт"),
    (r"\bвирусн(ый|ая|ое)\s+формат\b", "виральный формат"),
    (r"\bbest\b", "best"), (r"\bworst\b", "worst"), (r"\bwinning\b", "winning"),
]

# Модальность, обязательная для гипотезы.
MODALITY = re.compile(r"\b(может|могут|возможно|предположительно|вероятно)\b",
                      re.IGNORECASE)

# Указание размера выборки: n=NN или «на N роликах/наблюдениях».
SAMPLE_MENTION = re.compile(r"(\bn\s*=\s*\d+)|(\bна\s+\d+\s+"
                            r"(ролик|наблюден|значен))", re.IGNORECASE)

CAUSALITY_DISCLAIMER = "Причинность не установлена"

REQUIRES_SAMPLE = {"FACT", "HYPOTHESIS"}
REQUIRES_MODALITY = {"HYPOTHESIS"}
REQUIRES_DISCLAIMER = {"HYPOTHESIS"}


def find_violations(statement, claim_type, causality_established=False):
    """Список нарушений формы. Пустой список — формулировка допустима."""
    v = []
    text = statement or ""
    if not causality_established:
        for pattern, label in CAUSAL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                v.append(f"причинная конструкция «{label}» без завершённого эксперимента")
    for pattern, label in JUDGEMENT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            v.append(f"оценочная конструкция «{label}»")
    if claim_type in REQUIRES_SAMPLE and not SAMPLE_MENTION.search(text):
        v.append("не указан размер выборки")
    if claim_type in REQUIRES_MODALITY and not MODALITY.search(text):
        v.append("гипотеза без модальности (может / возможно)")
    if claim_type in REQUIRES_DISCLAIMER and CAUSALITY_DISCLAIMER.lower() not in text.lower():
        v.append(f"гипотеза без пометки «{CAUSALITY_DISCLAIMER}»")
    return v


def validate(statement, claim_type, causality_established=False):
    return not find_violations(statement, claim_type, causality_established)


def assert_valid(statement, claim_type, causality_established=False):
    v = find_violations(statement, claim_type, causality_established)
    if v:
        raise ValueError(f"CLAIM_REJECTED: {'; '.join(v)} :: {statement!r}")
    return statement
