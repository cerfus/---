#!/usr/bin/env python3
"""Сборка Tier 1 признаков из промеренных ассетов.

Движок ничего не угадывает. У каждой строки признака есть ровно одно из
двух: значение, выведенное из конкретного промера конкретного файла, — либо
статус отсутствия с машинной причиной. Третьего состояния нет, и значения
по умолчанию не существует.
"""
from assets import policies as A
from features import visual_policies as V

VISUAL_ENGINE_VERSION = "tier1-engine-1.0.0"


def _row(video_id, name, spec, *, value=None, status, reason=None,
         asset=None, evidence=None, extractor_version, extra=None):
    """Одна строка признака. Значение и отсутствие разведены строго."""
    absent = status in V.ABSENT_STATUS
    ftype = spec["type"] if not absent else status
    basis = {"asset_status": asset["asset_status"] if asset else "no_asset"}
    if asset:
        basis.update({"asset_uid": asset["asset_uid"],
                      "asset_sha256": asset["sha256"],
                      "asset_version": asset["asset_version"],
                      "source_uri": asset["source_uri"]})
    return {
        "video_id": video_id,
        "feature_name": name,
        "feature_value": None if absent else _canon(value),
        "feature_type": ftype,
        "feature_status": status,
        "status_reason": reason,
        "tier": V.TIER_1,
        "feature_group": spec["group"],
        "policy_version": V.VISUAL_FEATURE_POLICY_VERSION,
        "engine_version": VISUAL_ENGINE_VERSION,
        "extractor_version": extractor_version,
        "source_basis": basis,
        "reconciliation_basis": {"applies": False,
                                 "why": "признак читается из файла, а не из "
                                        "источников статистики"},
        "evidence_refs": sorted(evidence or []),
        "computed_from": [] if absent else sorted(
            {spec["from"]} - {None}),
        "extra": extra or {},
    }


def _canon(value):
    """Каноническое текстовое представление значения — оно и сравнивается."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _derived(probe, key, spec_name):
    """Вычисляемые признаки, у которых нет прямого поля в промере."""
    scene = probe.get("scene") or {}
    fps = probe.get("fps")
    duration = probe.get("duration_sec")
    if key == "_aspect":
        w, h = probe.get("width"), probe.get("height")
        return (round(w / h, 6), None) if w and h else (None, V.NO_EVIDENCE)
    if key == "_shot_count":
        v = scene.get("shot_count")
        return (v, None) if v is not None else (None, V.NO_EVIDENCE)
    if key == "_scene_changes":
        v = scene.get("scene_change_count")
        return (v, None) if v is not None else (None, V.NO_EVIDENCE)
    if key == "_avg_shot":
        shots = scene.get("shot_count")
        if not shots or not duration:
            return None, V.NO_EVIDENCE
        return round(duration / shots, 6), None
    if key == "_first_frame_type":
        t = V.first_frame_type(scene.get("first_frame_mean_luma"))
        return (t, None) if t else (None, V.NO_EVIDENCE)
    if key == "_audio_present":
        v = probe.get("audio_present")
        # None означает «определить нечем», а не «звука нет»
        return (v, None) if v is not None else (None, V.NO_EVIDENCE)
    if key == "_ocr_available":
        return False, None          # факт о попытке извлечения, не о ролике
    if key == "_opening_window":
        if duration is None:
            return None, V.NO_EVIDENCE
        return round(min(scene.get("opening_window_sec", 0.0), duration), 6), None
    if key == "_opening_visual":
        v = scene.get("opening_frames_decoded")
        return (v > 0, None) if v is not None else (None, V.NO_EVIDENCE)
    if key == "_opening_changes":
        v = scene.get("opening_scene_changes")
        return (v, None) if v is not None else (None, V.NO_EVIDENCE)
    if fps is None:
        return None, V.NO_EVIDENCE
    return None, V.NO_EVIDENCE


def features_for_video(video_id, assets, extractor_version):
    """Полный набор Tier 1 строк для одного ролика."""
    rows = []
    current = A.current_asset(assets, video_id)
    history = [r for r in assets if r["video_id"] == video_id]
    # Последняя известная версия нужна для ТОЧНОЙ причины отсутствия:
    # «файл не давали» и «файл дали, но он битый» — разные состояния, и
    # смешивать их в одном unavailable нельзя.
    latest = max(history, key=lambda r: r["asset_version"]) if history else None

    for name, spec in sorted(V.TIER_1_FEATURES.items()):
        # 1. признак, у которого экстрактора нет в принципе
        if spec.get("from") is None:
            rows.append(_row(video_id, name, spec, status="unavailable",
                             reason=f"{spec['blocked']}: {spec['note']}",
                             asset=current or latest,
                             evidence=[f"policy:{V.VISUAL_FEATURE_POLICY_VERSION}"
                                       f"#{name}.blocked={spec['blocked']}"],
                             extractor_version=extractor_version,
                             extra={"candidate": V.TIER_1_CANDIDATES.get(name)}))
            continue

        # 2. валидного файла нет
        if current is None:
            if latest is None:
                status, reason = "unavailable", f"{V.NO_ASSET}: ассет не зарегистрирован"
            elif latest["asset_status"] == "missing":
                status, reason = "unavailable", f"{V.NO_ASSET}: {latest['status_reason']}"
            else:
                status, reason = "invalid_asset", f"{V.ASSET_INVALID}: {latest['status_reason']}"
            rows.append(_row(video_id, name, spec, status=status, reason=reason,
                             asset=latest,
                             evidence=[f"asset_manifest:{video_id}#"
                                       f"v{latest['asset_version']}" if latest
                                       else f"asset_manifest:{video_id}#none"],
                             extractor_version=extractor_version))
            continue

        # 3. файл есть — считаем
        probe = (current.get("extra") or {}).get("probe") or {}
        merged = {**current, **probe}
        key = spec["from"]
        if key.startswith("_"):
            value, why = _derived(merged, key, name)
        else:
            value = merged.get(key)
            why = None if value is not None else V.NO_EVIDENCE

        if why is not None:
            rows.append(_row(video_id, name, spec,
                             status="insufficient_evidence",
                             reason=f"{why}: промер не содержит основания для "
                                    f"{name}",
                             asset=current,
                             evidence=[f"asset:{current['sha256']}#probe"],
                             extractor_version=extractor_version))
            continue

        ev = [f"asset:{current['sha256']}#{key}"]
        scene = probe.get("scene") or {}
        if spec["group"] in ("visual_structure", "opening"):
            ev.append("frames:" + (
                f"stride={scene.get('stride')},analysed={scene.get('frames_analysed')}"
                f",read={scene.get('frames_read')}"))
            if scene.get("scene_change_frames") is not None:
                ev.append("scene_change_frames:"
                          + ",".join(str(i) for i in
                                     scene["scene_change_frames"][:32]))
        if spec["group"] == "opening":
            ev.append(f"time_range:0-{scene.get('opening_window_sec')}s")
        rows.append(_row(video_id, name, spec, value=value,
                         status=spec["status"], asset=current, evidence=ev,
                         extractor_version=extractor_version,
                         extra={"about": spec["about"]} if "about" in spec else {}))
    return rows


def build_all(videos, assets, extractor_version):
    rows = []
    for v in sorted(videos, key=lambda x: x["video_id"]):
        rows += features_for_video(v["video_id"], assets, extractor_version)
    rows.sort(key=lambda r: (r["video_id"], r["feature_name"]))
    return rows
