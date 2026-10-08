# Покрытие Tier 1

`VIDEO_ASSET_STATUS: UNAVAILABLE`

* роликов: **17**, из них с текущим валидным ассетом: **0**
* строк признаков: **408** (24 признаков x 17 роликов)
* со значением: **0**, без значения: **408**
* заполненность: **0.0%**

policy: `visual-feature-policy-1.0.0` · extractor: `tier1-probe-1.0.0+cv2=5.0.0+numpy=2.4.6+ffmpeg=7.0.2-static`
asset_hash: `f6340bbc0d29e0b3…` · tier1_hash: `936d91bb3fb5aa0a…`

Значений нет ни у одного признака: валидных видеофайлов не зарегистрировано. Это честное отсутствие, а не пустой расчёт — каждая строка несёт машинную причину (таблица ниже).

## Каталог приёма

Файлов нет. Контракт: `data/assets/incoming/<video_id>.<ext>`,
поддерживаются .mp4, .mov, .webm, .m4v, .qt.

## По роликам

| video_id | ассет | версия | sha256 | со значением | без значения |
|---|---|---:|---|---:|---:|
| 7628289075081956640 | missing | 1 | — | 0 | 24 |
| 7636554591160519969 | missing | 1 | — | 0 | 24 |
| 7637497223835667744 | missing | 1 | — | 0 | 24 |
| 7638406599832341793 | missing | 1 | — | 0 | 24 |
| 7643545925926915360 | missing | 1 | — | 0 | 24 |
| 7650987964817788192 | missing | 1 | — | 0 | 24 |
| 7651785569323863329 | missing | 1 | — | 0 | 24 |
| 7653084183916547360 | missing | 1 | — | 0 | 24 |
| 7658223227226950944 | missing | 1 | — | 0 | 24 |
| 7669369589641366817 | missing | 1 | — | 0 | 24 |
| 7675678282561375520 | missing | 1 | — | 0 | 24 |
| 7681842224602123553 | missing | 1 | — | 0 | 24 |
| 7681976167829654816 | missing | 1 | — | 0 | 24 |
| 7682248932327476513 | missing | 1 | — | 0 | 24 |
| 7683780392029015328 | missing | 1 | — | 0 | 24 |
| 7683970036201049376 | missing | 1 | — | 0 | 24 |
| 7687082909278145824 | missing | 1 | — | 0 | 24 |

## По признакам

| признак | группа | со значением | без значения |
|---|---|---:|---:|
| aspect_ratio | technical | 0 | 17 |
| asset_duration_sec | technical | 0 | 17 |
| asset_height | technical | 0 | 17 |
| asset_width | technical | 0 | 17 |
| audio_present | audio | 0 | 17 |
| average_shot_duration | visual_structure | 0 | 17 |
| face_present | human | 0 | 17 |
| first_frame_type | visual_structure | 0 | 17 |
| fps | technical | 0 | 17 |
| frame_count | technical | 0 | 17 |
| music_present | audio | 0 | 17 |
| ocr_available | on_screen_text | 0 | 17 |
| ocr_confidence_summary | on_screen_text | 0 | 17 |
| opening_duration_sec | opening | 0 | 17 |
| opening_person_present | opening | 0 | 17 |
| opening_scene_change | opening | 0 | 17 |
| opening_speech_present | opening | 0 | 17 |
| opening_text_present | opening | 0 | 17 |
| opening_visual_presence | opening | 0 | 17 |
| person_present | human | 0 | 17 |
| scene_change_count | visual_structure | 0 | 17 |
| shot_count | visual_structure | 0 | 17 |
| speech_present | audio | 0 | 17 |
| text_present | on_screen_text | 0 | 17 |

## Причины отсутствия

| код | строк |
|---|---:|
| `asset_not_supplied` | 255 |
| `no_validated_detector` | 102 |
| `no_ocr_extractor` | 51 |

---

Отчёт пересобирается командой `python3 -m assets.ingest`. Значение признака появляется только из промера реального файла; подпись, хештеги и статистика источником Tier 1 не являются.
