# Покрытие Tier 1

`VIDEO_ASSET_STATUS: UNAVAILABLE`

* роликов: **16**, из них с текущим валидным ассетом: **0**
* строк признаков: **384** (24 признаков x 16 роликов)
* со значением: **0**, без значения: **384**
* заполненность: **0.0%**

policy: `visual-feature-policy-1.0.0` · extractor: `tier1-probe-1.0.0+cv2=5.0.0+numpy=2.4.6+ffmpeg=7.0.2-static`
asset_hash: `d1de44f5d9554367…` · tier1_hash: `d1ca25c17ec6993e…`

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

## По признакам

| признак | группа | со значением | без значения |
|---|---|---:|---:|
| aspect_ratio | technical | 0 | 16 |
| asset_duration_sec | technical | 0 | 16 |
| asset_height | technical | 0 | 16 |
| asset_width | technical | 0 | 16 |
| audio_present | audio | 0 | 16 |
| average_shot_duration | visual_structure | 0 | 16 |
| face_present | human | 0 | 16 |
| first_frame_type | visual_structure | 0 | 16 |
| fps | technical | 0 | 16 |
| frame_count | technical | 0 | 16 |
| music_present | audio | 0 | 16 |
| ocr_available | on_screen_text | 0 | 16 |
| ocr_confidence_summary | on_screen_text | 0 | 16 |
| opening_duration_sec | opening | 0 | 16 |
| opening_person_present | opening | 0 | 16 |
| opening_scene_change | opening | 0 | 16 |
| opening_speech_present | opening | 0 | 16 |
| opening_text_present | opening | 0 | 16 |
| opening_visual_presence | opening | 0 | 16 |
| person_present | human | 0 | 16 |
| scene_change_count | visual_structure | 0 | 16 |
| shot_count | visual_structure | 0 | 16 |
| speech_present | audio | 0 | 16 |
| text_present | on_screen_text | 0 | 16 |

## Причины отсутствия

| код | строк |
|---|---:|
| `asset_not_supplied` | 240 |
| `no_validated_detector` | 96 |
| `no_ocr_extractor` | 48 |

---

Отчёт пересобирается командой `python3 -m assets.ingest`. Значение признака появляется только из промера реального файла; подпись, хештеги и статистика источником Tier 1 не являются.
