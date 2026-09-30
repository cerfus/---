-- 0015 · Видеоассеты, Tier 1 и запрет семантики без видео.
--
-- Phase 6 добавляет физический слой: сам видеофайл. До сих пор система знала
-- о ролике только то, что рассказали два аналитических источника. Признаки
-- вида hook / topic / emotion объявлены недоступными с Phase 4, и это
-- держалось на честности генератора. Теперь запрет становится структурным.
--
-- Бинарные видео в PostgreSQL НЕ хранятся: в базе только метаданные, ссылка
-- и хеш. Источник истины по ингесту — JSONL, как и во всех прочих слоях.

-- ═══ 1. Реестр ассетов ═══
-- Таблица append-only, поэтому изменяемого «текущего» статуса в ней нет:
-- строка описывает состояние файла НА МОМЕНТ РЕГИСТРАЦИИ и больше никогда
-- не меняется. «Текущий ассет» вычисляется представлением ниже. Иначе
-- пометка прежней версии как superseded требовала бы UPDATE, а его рантайм
-- не имеет и иметь не должен.
CREATE TABLE video_assets (
  asset_id          BIGSERIAL PRIMARY KEY,
  video_id          TEXT NOT NULL REFERENCES videos(video_id),
  -- Детерминированный идентификатор СОДЕРЖИМОГО: uuid5 от sha256.
  -- Одни и те же байты дают один asset_uid, в том числе под разными
  -- video_id — это и есть требование «одинаковый SHA-256 → тот же asset».
  asset_uid         TEXT NOT NULL,
  asset_version     INT NOT NULL CHECK (asset_version >= 1),
  source            TEXT NOT NULL CHECK (source IN
        ('owner_upload','manual_export','not_supplied')),
  source_uri        TEXT NOT NULL CHECK (length(trim(source_uri)) > 0),
  sha256            TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  byte_size         BIGINT CHECK (byte_size IS NULL OR byte_size > 0),
  mime_type         TEXT,
  duration_sec      NUMERIC(10,3) CHECK (duration_sec IS NULL OR duration_sec > 0),
  width             INT CHECK (width IS NULL OR width > 0),
  height            INT CHECK (height IS NULL OR height > 0),
  fps               NUMERIC(9,4) CHECK (fps IS NULL OR fps > 0),
  frame_count       BIGINT CHECK (frame_count IS NULL OR frame_count > 0),
  acquired_at       TIMESTAMPTZ,
  extractor_version TEXT NOT NULL,
  policy_version    TEXT NOT NULL,
  asset_status      TEXT NOT NULL CHECK (asset_status IN
        ('valid','invalid','missing')),
  status_reason     TEXT,
  evidence_refs     TEXT[] NOT NULL,
  extra             JSONB NOT NULL DEFAULT '{}'::jsonb,
  registered_at     TIMESTAMPTZ NOT NULL,
  run_id            UUID NOT NULL,

  -- Одна версия на ролик — одна строка.
  CONSTRAINT uq_asset_version UNIQUE (video_id, asset_version),

  -- ВАЛИДНЫЙ ассет обязан быть полностью промерен. Именно это условие не
  -- даёт битому или недокачанному файлу стать текущим: представление
  -- выбирает только valid, а valid без полного набора полей невозможен.
  CONSTRAINT ck_asset_valid_complete CHECK (
        asset_status <> 'valid' OR (
            sha256 IS NOT NULL AND byte_size IS NOT NULL
            AND mime_type IS NOT NULL AND duration_sec IS NOT NULL
            AND width IS NOT NULL AND height IS NOT NULL
            AND fps IS NOT NULL AND frame_count IS NOT NULL
            AND acquired_at IS NOT NULL)),
  -- Отсутствие и брак обязаны называть причину.
  CONSTRAINT ck_asset_reason CHECK (
        asset_status = 'valid' OR status_reason IS NOT NULL),
  -- Файла нет — нет и хеша. Хеш у missing означал бы, что файл всё-таки был.
  CONSTRAINT ck_asset_missing_no_hash CHECK (
        asset_status <> 'missing' OR (sha256 IS NULL AND byte_size IS NULL)),
  CONSTRAINT ck_asset_missing_source CHECK (
        (asset_status = 'missing') = (source = 'not_supplied'))
);

-- Одни и те же байты нельзя зарегистрировать за роликом дважды.
CREATE UNIQUE INDEX uq_asset_content ON video_assets(video_id, sha256)
  WHERE sha256 IS NOT NULL;
CREATE INDEX ix_asset_video ON video_assets(video_id);
CREATE INDEX ix_asset_uid ON video_assets(asset_uid);

COMMENT ON TABLE video_assets IS
  'Реестр видеофайлов. Append-only; бинарных данных не содержит — только '
  'ссылку, хеш и промеренные метаданные. Источник истины — '
  'data/assets/manifest.jsonl.';

-- Текущий ассет ролика: последняя ВАЛИДНАЯ версия. Битые и отсутствующие
-- сюда не попадают никогда.
CREATE VIEW video_assets_current AS
SELECT a.*
  FROM video_assets a
  JOIN (SELECT video_id, max(asset_version) AS v
          FROM video_assets WHERE asset_status = 'valid'
         GROUP BY video_id) latest
    ON latest.video_id = a.video_id AND latest.v = a.asset_version
 WHERE a.asset_status = 'valid';

COMMENT ON VIEW video_assets_current IS
  'Последняя валидная версия ассета на ролик. Битый или отсутствующий файл '
  'текущим не становится.';

-- append-only: права + триггер, как у video_features
GRANT SELECT, INSERT ON video_assets TO tiktok_rw;
GRANT SELECT ON video_assets TO tiktok_ro;
GRANT SELECT ON video_assets_current TO tiktok_rw, tiktok_ro;
GRANT USAGE, SELECT ON SEQUENCE video_assets_asset_id_seq TO tiktok_rw;
REVOKE UPDATE, DELETE ON video_assets FROM tiktok_rw, tiktok_ro, PUBLIC;

CREATE FUNCTION video_assets_append_only() RETURNS TRIGGER AS $$
DECLARE owner_name TEXT;
BEGIN
  SELECT pg_get_userbyid(relowner) INTO owner_name
    FROM pg_class WHERE oid = 'video_assets'::regclass;
  IF current_user <> owner_name THEN
    RAISE EXCEPTION 'VIDEO_ASSETS_IS_APPEND_ONLY: % запрещён для роли %',
                    TG_OP, current_user;
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_va_append_only
  BEFORE UPDATE OR DELETE ON video_assets
  FOR EACH ROW EXECUTE FUNCTION video_assets_append_only();

-- ═══ 2. Tier 1 и новые статусы качества ═══
-- unavailable никогда не равен false — для этого статус и отделён от
-- значения: при любом отсутствующем статусе все три колонки значения NULL
-- (ck_vf_absent_is_null ниже пересоздаётся с учётом новых статусов).
ALTER TABLE video_features DROP CONSTRAINT video_features_tier_check;
ALTER TABLE video_features ADD CONSTRAINT video_features_tier_check
  CHECK (tier IN ('tier_0','tier_0.5','tier_1'));

ALTER TABLE video_features DROP CONSTRAINT video_features_feature_status_check;
ALTER TABLE video_features ADD CONSTRAINT video_features_feature_status_check
  CHECK (feature_status IN ('observed','derived','unavailable',
                            'insufficient_baseline','invalid_asset',
                            'insufficient_evidence'));

ALTER TABLE video_features DROP CONSTRAINT video_features_feature_type_check;
ALTER TABLE video_features ADD CONSTRAINT video_features_feature_type_check
  CHECK (feature_type IN ('numeric','boolean','categorical','text','timestamp',
                          'unavailable','insufficient_baseline',
                          'invalid_asset','insufficient_evidence'));

ALTER TABLE video_features DROP CONSTRAINT ck_vf_absent_is_null;
ALTER TABLE video_features ADD CONSTRAINT ck_vf_absent_is_null CHECK (
      feature_status NOT IN ('unavailable','insufficient_baseline',
                             'invalid_asset','insufficient_evidence')
      OR (feature_value IS NULL AND feature_value_numeric IS NULL
          AND feature_value_bool IS NULL));

ALTER TABLE video_features DROP CONSTRAINT ck_vf_reason;
ALTER TABLE video_features ADD CONSTRAINT ck_vf_reason CHECK (
      feature_status NOT IN ('unavailable','insufficient_baseline',
                             'invalid_asset','insufficient_evidence')
      OR status_reason IS NOT NULL);

ALTER TABLE video_features DROP CONSTRAINT ck_vf_numeric;
ALTER TABLE video_features ADD CONSTRAINT ck_vf_numeric CHECK (
      feature_type <> 'numeric'
      OR feature_status IN ('unavailable','insufficient_baseline',
                            'invalid_asset','insufficient_evidence')
      OR feature_value_numeric IS NOT NULL);

ALTER TABLE video_features DROP CONSTRAINT ck_vf_bool;
ALTER TABLE video_features ADD CONSTRAINT ck_vf_bool CHECK (
      feature_type <> 'boolean'
      OR feature_status IN ('unavailable','insufficient_baseline',
                            'invalid_asset','insufficient_evidence')
      OR feature_value_bool IS NOT NULL);

-- ═══ 3. Реестр семантических признаков и запрет без видео ═══
-- Признак «о чём ролик» нельзя получить из подписи и хештегов. До сих пор
-- это гарантировал только генератор. Теперь пара «семантический признак +
-- утверждающий статус» без ссылки на промеренный ассет отвергается базой.
CREATE TABLE semantic_feature_names (
  feature_name   TEXT PRIMARY KEY,
  reason         TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  policy_version TEXT NOT NULL
);

INSERT INTO semantic_feature_names (feature_name, reason, policy_version) VALUES
  ('hook',            'смысловой крючок требует интерпретации кадров и звука', 'visual-feature-policy-1.0.0'),
  ('hook_type',       'смысловой крючок требует интерпретации кадров и звука', 'visual-feature-policy-1.0.0'),
  ('topic',           'тема требует интерпретации содержания, а не подписи',   'visual-feature-policy-1.0.0'),
  ('emotion',         'эмоция требует интерпретации изображения и звука',      'visual-feature-policy-1.0.0'),
  ('visual_style',    'стиль требует интерпретации изображения',               'visual-feature-policy-1.0.0'),
  ('story_structure', 'структура требует интерпретации содержания',            'visual-feature-policy-1.0.0'),
  ('cta',             'призыв требует интерпретации речи и текста на экране',  'visual-feature-policy-1.0.0'),
  ('editing_style',   'монтаж требует интерпретации последовательности кадров','visual-feature-policy-1.0.0'),
  ('character',       'персонаж требует интерпретации изображения',            'visual-feature-policy-1.0.0');

GRANT SELECT ON semantic_feature_names TO tiktok_rw, tiktok_ro;
REVOKE INSERT, UPDATE, DELETE ON semantic_feature_names
  FROM tiktok_rw, tiktok_ro, PUBLIC;

COMMENT ON TABLE semantic_feature_names IS
  'Признаки, которые невозможно получить из подписи, хештегов и статистики. '
  'Утверждающий статус по ним требует ссылки на промеренный видеоассет.';

CREATE FUNCTION vf_semantic_guard() RETURNS TRIGGER AS $$
DECLARE why TEXT;
BEGIN
  SELECT reason INTO why FROM semantic_feature_names
   WHERE feature_name = NEW.feature_name;
  IF why IS NULL THEN
    RETURN NEW;                       -- признак не семантический
  END IF;
  IF NEW.feature_status IN ('unavailable','insufficient_evidence',
                            'invalid_asset','insufficient_baseline') THEN
    RETURN NEW;                       -- честное отсутствие разрешено всегда
  END IF;
  -- Утверждающий статус: нужна опора на конкретный промеренный файл.
  IF NOT (NEW.source_basis ? 'asset_sha256')
     OR NEW.source_basis->>'asset_sha256' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION
      'SEMANTIC_WITHOUT_VIDEO_EVIDENCE: признак % со статусом % не имеет '
      'source_basis.asset_sha256 (%)', NEW.feature_name, NEW.feature_status, why
      USING ERRCODE = '23514';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM video_assets
                  WHERE video_id = NEW.video_id
                    AND sha256 = NEW.source_basis->>'asset_sha256'
                    AND asset_status = 'valid') THEN
    RAISE EXCEPTION
      'SEMANTIC_WITHOUT_VIDEO_EVIDENCE: asset_sha256 % не зарегистрирован '
      'валидным ассетом ролика %',
      NEW.source_basis->>'asset_sha256', NEW.video_id
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_vf_semantic BEFORE INSERT ON video_features
  FOR EACH ROW EXECUTE FUNCTION vf_semantic_guard();

-- Content DNA: доказательство, перечисляющее семантические признаки,
-- обязано опираться на промеренный ассет.
CREATE FUNCTION dna_semantic_guard() RETURNS TRIGGER AS $$
DECLARE nm TEXT; bad TEXT;
BEGIN
  IF NOT (NEW.evidence ? 'feature_names') THEN
    RETURN NEW;
  END IF;
  IF jsonb_typeof(NEW.evidence->'feature_names') <> 'array' THEN
    RAISE EXCEPTION 'DNA_FEATURE_NAMES_MALFORMED: ожидается массив'
      USING ERRCODE = '23514';
  END IF;
  FOR nm IN SELECT jsonb_array_elements_text(NEW.evidence->'feature_names') LOOP
    IF EXISTS (SELECT 1 FROM semantic_feature_names WHERE feature_name = nm) THEN
      bad := nm;
      IF NOT (NEW.evidence ? 'asset_sha256')
         OR NEW.evidence->>'asset_sha256' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION
          'SEMANTIC_WITHOUT_VIDEO_EVIDENCE: Content DNA ссылается на признак % '
          'без evidence.asset_sha256', bad
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END LOOP;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_dna_semantic BEFORE INSERT OR UPDATE ON content_dna
  FOR EACH ROW EXECUTE FUNCTION dna_semantic_guard();

-- ═══ 4. Схема будущего слоя семантических аннотаций ═══
-- Интерфейс подготовлен, но доказанным слоем признаков не является и стать
-- им не может: is_authoritative закрыт CHECK-ом, а не соглашением.
CREATE TABLE semantic_annotations (
  annotation_id     BIGSERIAL PRIMARY KEY,
  video_id          TEXT NOT NULL REFERENCES videos(video_id),
  asset_sha256      TEXT NOT NULL CHECK (asset_sha256 ~ '^[0-9a-f]{64}$'),
  feature_name      TEXT NOT NULL REFERENCES semantic_feature_names(feature_name),
  annotation_value  TEXT NOT NULL,
  annotator         TEXT NOT NULL CHECK (annotator IN ('owner','llm_vision')),
  annotator_version TEXT NOT NULL,
  confidence        NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
  evidence          JSONB NOT NULL,
  policy_version    TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL,
  run_id            UUID NOT NULL,
  is_authoritative  BOOLEAN NOT NULL DEFAULT FALSE,
  CONSTRAINT ck_sa_not_authoritative CHECK (is_authoritative = FALSE),
  -- аннотация обязана называть кадры или временные отрезки
  CONSTRAINT ck_sa_evidence CHECK (
        evidence ? 'frame_indices' OR evidence ? 'time_ranges'),
  CONSTRAINT uq_sa UNIQUE (video_id, asset_sha256, feature_name,
                           annotator, annotator_version)
);

GRANT SELECT, INSERT ON semantic_annotations TO tiktok_rw;
GRANT SELECT ON semantic_annotations TO tiktok_ro;
GRANT USAGE, SELECT ON SEQUENCE semantic_annotations_annotation_id_seq TO tiktok_rw;
REVOKE UPDATE, DELETE ON semantic_annotations FROM tiktok_rw, tiktok_ro, PUBLIC;

COMMENT ON TABLE semantic_annotations IS
  'Подготовленный интерфейс будущего слоя интерпретации. НЕ является '
  'доказанным слоем признаков: is_authoritative закрыт CHECK-ом. Попадание '
  'в Content DNA требует отдельного решения владельца и отдельной политики.';
