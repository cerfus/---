#!/usr/bin/env python3
"""Политика Tier 1 — признаков, извлекаемых из самого видеофайла.

Tier 1 отличается от Tier 0/0.5 источником, а не сложностью: Tier 0 берёт
числа у аналитических источников, Tier 1 открывает файл. Из этого следует
главное правило фазы: НЕТ ФАЙЛА — НЕТ ПРИЗНАКА. Ни подпись, ни хештеги, ни
статистика не могут заменить наблюдение кадра.

Каждый признак объявляет, ЧЕМ он извлекается. Признак без экстрактора не
становится значением по умолчанию — он становится unavailable с машинной
причиной. Лучше двадцать четыре честных unavailable, чем один придуманный.
"""
VISUAL_FEATURE_POLICY_VERSION = "visual-feature-policy-1.0.0"
TIER_1 = "tier_1"

# ── статусы качества ────────────────────────────────────────────────────────
# unavailable НЕ равен false. Отсутствие наблюдения и наблюдение отсутствия —
# разные вещи, и статус существует ровно для того, чтобы их не смешивать.
FEATURE_STATUS = ("observed", "derived", "unavailable", "insufficient_baseline",
                  "invalid_asset", "insufficient_evidence")
ABSENT_STATUS = ("unavailable", "insufficient_baseline", "invalid_asset",
                 "insufficient_evidence")

# машинные причины отсутствия
NO_ASSET = "asset_not_supplied"
ASSET_INVALID = "asset_invalid"
NO_DETECTOR = "no_validated_detector"
NO_OCR = "no_ocr_extractor"
NO_EVIDENCE = "evidence_missing_in_probe"

# ── каталог Tier 1 ──────────────────────────────────────────────────────────
# "from" — поле промера, которое даёт значение. None означает, что значения
# нет и взяться ему неоткуда: такой признак всегда unavailable, даже при
# полностью валидном файле.
TIER_1_FEATURES = {
    # технические: читаются из контейнера
    "asset_duration_sec": {"group": "technical", "type": "numeric",
                           "from": "duration_sec", "status": "observed"},
    "asset_width":        {"group": "technical", "type": "numeric",
                           "from": "width", "status": "observed"},
    "asset_height":       {"group": "technical", "type": "numeric",
                           "from": "height", "status": "observed"},
    "aspect_ratio":       {"group": "technical", "type": "numeric",
                           "from": "_aspect", "status": "derived"},
    "fps":                {"group": "technical", "type": "numeric",
                           "from": "fps", "status": "observed"},
    "frame_count":        {"group": "technical", "type": "numeric",
                           "from": "frame_count", "status": "observed"},

    # визуальная структура: считается по объявленному правилу разницы яркости
    "shot_count":            {"group": "visual_structure", "type": "numeric",
                              "from": "_shot_count", "status": "derived"},
    "scene_change_count":    {"group": "visual_structure", "type": "numeric",
                              "from": "_scene_changes", "status": "derived"},
    "average_shot_duration": {"group": "visual_structure", "type": "numeric",
                              "from": "_avg_shot", "status": "derived"},
    "first_frame_type":      {"group": "visual_structure", "type": "categorical",
                              "from": "_first_frame_type", "status": "derived"},

    # звук: наличие дорожки читается, содержание — нет
    "audio_present":  {"group": "audio", "type": "boolean",
                       "from": "_audio_present", "status": "observed"},
    "speech_present": {"group": "audio", "type": "boolean", "from": None,
                       "blocked": NO_DETECTOR,
                       "note": "детектора речи в слое нет; наличие аудиодорожки "
                               "речью не является"},
    "music_present":  {"group": "audio", "type": "boolean", "from": None,
                       "blocked": NO_DETECTOR,
                       "note": "детектора музыки в слое нет"},

    # текст на экране
    "text_present":           {"group": "on_screen_text", "type": "boolean",
                               "from": None, "blocked": NO_OCR,
                               "note": "OCR-экстрактор отсутствует"},
    "ocr_available":          {"group": "on_screen_text", "type": "boolean",
                               "from": "_ocr_available", "status": "observed",
                               "about": "extractor",
                               "note": "свойство попытки извлечения, а не ролика"},
    "ocr_confidence_summary": {"group": "on_screen_text", "type": "text",
                               "from": None, "blocked": NO_OCR,
                               "note": "нет OCR — нет и распределения уверенности"},

    # люди в кадре
    "person_present": {"group": "human", "type": "boolean", "from": None,
                       "blocked": NO_DETECTOR,
                       "note": "детектора людей в слое нет"},
    "face_present":   {"group": "human", "type": "boolean", "from": None,
                       "blocked": NO_DETECTOR,
                       "note": "каскад Haar доступен, но его точность на этом "
                               "аккаунте не измерена; см. TIER_1_CANDIDATES"},

    # начало ролика — наблюдаемая замена смыслового «крючка»
    "opening_duration_sec":   {"group": "opening", "type": "numeric",
                               "from": "_opening_window", "status": "observed"},
    "opening_visual_presence": {"group": "opening", "type": "boolean",
                                "from": "_opening_visual", "status": "derived"},
    "opening_scene_change":   {"group": "opening", "type": "numeric",
                               "from": "_opening_changes", "status": "derived"},
    "opening_text_present":   {"group": "opening", "type": "boolean",
                               "from": None, "blocked": NO_OCR,
                               "note": "нужен OCR по кадрам окна начала"},
    "opening_speech_present": {"group": "opening", "type": "boolean",
                               "from": None, "blocked": NO_DETECTOR,
                               "note": "нужен детектор речи по окну начала"},
    "opening_person_present": {"group": "opening", "type": "boolean",
                               "from": None, "blocked": NO_DETECTOR,
                               "note": "нужен детектор людей по окну начала"},
}

# Экстракторы, которые технически достижимы, но НЕ включены без измерения
# качества. Тот же приём, что с механическими парами в Phase 5.1: кандидат
# отделён от принятого и сам по себе ничего не разрешает.
TIER_1_CANDIDATES = {
    "face_present": "OpenCV поставляет каскад Haar для фронтальных лиц. "
                    "Его полнота и точность на этом аккаунте не измерены, "
                    "размеченной выборки нет. Включение требует отдельного "
                    "решения владельца и версии политики.",
    "text_present": "OCR ставится отдельным пакетом. Без измерения точности "
                    "на вертикальном видео с наложенным текстом включать его "
                    "означало бы выдавать шум за наблюдение.",
}

# ── семантические признаки: запрещены без видео ─────────────────────────────
# Именно эти имена нельзя получить из подписи, хештегов и статистики.
# Список зеркалит таблицу semantic_feature_names; расхождение ловится тестом.
SEMANTIC_FEATURES = {
    "hook":            "смысловой крючок требует интерпретации кадров и звука",
    "hook_type":       "смысловой крючок требует интерпретации кадров и звука",
    "topic":           "тема требует интерпретации содержания, а не подписи",
    "emotion":         "эмоция требует интерпретации изображения и звука",
    "visual_style":    "стиль требует интерпретации изображения",
    "story_structure": "структура требует интерпретации содержания",
    "cta":             "призыв требует интерпретации речи и текста на экране",
    "editing_style":   "монтаж требует интерпретации последовательности кадров",
    "character":       "персонаж требует интерпретации изображения",
}
SEMANTIC_REASON_CODE = "semantic_without_video_evidence"

# Источники, которых НЕ достаточно для семантического признака. Перечислены
# поимённо, потому что именно они всегда под рукой и именно ими соблазн
# подменить наблюдение.
INSUFFICIENT_SEMANTIC_SOURCES = ("caption", "caption_len", "hashtags", "title",
                                 "description", "url", "published_at",
                                 "duration_sec", "views", "likes", "comments",
                                 "shares", "engagement_rate", "completion_rate")

# Куда семантический признак не имеет права попасть без видеодоказательства.
SEMANTIC_PROMOTION_TARGETS = ("FACT", "HYPOTHESIS", "RECOMMENDATION",
                              "content_dna_evidence", "experiment_basis")


class SemanticLeakage(Exception):
    """Попытка утвердить семантический признак без опоры на видеофайл."""


def is_semantic(feature_name):
    return feature_name in SEMANTIC_FEATURES


def semantic_claim_allowed(feature_name, source_basis):
    """Можно ли утверждать семантический признак при таком основании.

    Единственная точка решения. Основание обязано называть конкретный
    промеренный файл: sha256 ассета. Подпись, хештеги и статистика
    основанием не являются — сколько бы их ни было.

    Возвращает (allowed, reason_code, detail).
    """
    if not is_semantic(feature_name):
        return True, None, None
    basis = source_basis or {}
    sha = basis.get("asset_sha256")
    ok_sha = (isinstance(sha, str) and len(sha) == 64
              and all(c in "0123456789abcdef" for c in sha))
    used = sorted(k for k in basis if k in INSUFFICIENT_SEMANTIC_SOURCES)
    if not ok_sha:
        return False, SEMANTIC_REASON_CODE, {
            "feature": feature_name, "why": SEMANTIC_FEATURES[feature_name],
            "insufficient_sources_used": used,
            "required": "source_basis.asset_sha256 валидного ассета",
            "forbidden": list(SEMANTIC_PROMOTION_TARGETS)}
    return True, None, None


def assert_semantic_claim_allowed(feature_name, source_basis):
    ok, code, detail = semantic_claim_allowed(feature_name, source_basis)
    if not ok:
        raise SemanticLeakage(f"{code}: {feature_name} :: {detail['required']}")
    return True


def semantic_promotion_allowed(feature_name, target, has_video_evidence):
    """Может ли семантический признак попасть в потребителя вывода."""
    if target not in SEMANTIC_PROMOTION_TARGETS:
        raise ValueError(f"UNKNOWN_TARGET: {target!r}")
    if not is_semantic(feature_name):
        return True, None
    if not has_video_evidence:
        return False, SEMANTIC_REASON_CODE
    return True, None


# ── правило типа первого кадра ──────────────────────────────────────────────
# Пороги объявлены здесь и входят в версию политики: правка порога — новая
# версия, а не тихое изменение смысла прежних записей.
FIRST_FRAME_BLACK_MAX = 0.05
FIRST_FRAME_WHITE_MIN = 0.95


def first_frame_type(mean_luma):
    if mean_luma is None:
        return None
    if mean_luma <= FIRST_FRAME_BLACK_MAX:
        return "black"
    if mean_luma >= FIRST_FRAME_WHITE_MIN:
        return "white"
    return "image"
