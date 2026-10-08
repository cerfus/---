-- 0009 · video_features: длинный append-only формат с версией в ключе.
--
-- ДЕФЕКТ, КОТОРЫЙ ИСПРАВЛЯЕТСЯ.
-- Было: PRIMARY KEY (video_id) — одна строка на ролик, extractor_version в
-- ключ не входит, рантайму выдан UPDATE. Пересчёт признаков под новой версией
-- политики обязан был перезаписать ту же строку, и прежний результат исчезал
-- безвозвратно. Историческая воспроизводимость нарушалась.
--
-- Стало: идентичность записи — (video_id, feature_name, policy_version).
-- Новая версия политики создаёт НОВЫЕ строки и сосуществует со старой.
-- computed_at и run_id в идентичность не входят.
--
-- Таблица пуста, поэтому пересоздаётся целиком: слой совместимости ради
-- нуля строк был бы мусором. Ниже стоит защита — при непустой таблице
-- миграция отказывается работать, а не портит данные.

DO $$
DECLARE n BIGINT;
BEGIN
  SELECT count(*) INTO n FROM video_features;
  IF n <> 0 THEN
    RAISE EXCEPTION 'MIGRATION_REFUSED: video_features содержит % строк; '
                    'пересоздание уничтожило бы их. Нужен отдельный перенос.', n;
  END IF;
END $$;

DROP TABLE video_features;

CREATE TABLE video_features (
  id                   BIGSERIAL PRIMARY KEY,
  video_id             TEXT NOT NULL REFERENCES videos(video_id),
  feature_name         TEXT NOT NULL,
  -- Значение хранится трижды: каноническим текстом для сравнения и
  -- воспроизводимости, и в типизированных колонках для запросов.
  feature_value        TEXT,
  feature_value_numeric NUMERIC,
  feature_value_bool   BOOLEAN,
  -- Тип ЗНАЧЕНИЯ и СОСТОЯНИЕ доступности разнесены намеренно: свёрнутые в
  -- один перечень, они не дали бы отличить «отсутствует числовой признак»
  -- от «признак категориальный». Требуемое §2 различение несёт
  -- feature_status, который строго точнее.
  feature_type         TEXT NOT NULL CHECK (feature_type IN
        ('numeric','boolean','categorical','text','timestamp',
         'unavailable','insufficient_baseline')),
  feature_status       TEXT NOT NULL CHECK (feature_status IN
        ('observed','derived','unavailable','insufficient_baseline')),
  status_reason        TEXT,
  tier                 TEXT NOT NULL CHECK (tier IN ('tier_0','tier_0.5')),
  policy_version       TEXT NOT NULL,
  extractor_version    TEXT NOT NULL,
  source_basis         JSONB NOT NULL,
  reconciliation_basis JSONB NOT NULL,
  evidence_refs        TEXT[] NOT NULL,
  computed_from        TEXT[] NOT NULL,
  extra                JSONB,
  computed_at          TIMESTAMPTZ NOT NULL,
  run_id               UUID NOT NULL,

  -- ГЛАВНЫЙ ИНВАРИАНТ: одна версия конкретного признака на ролик.
  CONSTRAINT uq_video_feature_version UNIQUE (video_id, feature_name, policy_version),

  -- отсутствующее значение обязано быть NULL во всех представлениях
  CONSTRAINT ck_vf_absent_is_null CHECK (
        feature_status NOT IN ('unavailable','insufficient_baseline')
        OR (feature_value IS NULL AND feature_value_numeric IS NULL
            AND feature_value_bool IS NULL)),
  -- отсутствие обязано объяснять причину
  CONSTRAINT ck_vf_reason CHECK (
        feature_status NOT IN ('unavailable','insufficient_baseline')
        OR status_reason IS NOT NULL),
  -- типизированная колонка соответствует объявленному типу
  CONSTRAINT ck_vf_numeric CHECK (
        feature_type <> 'numeric' OR feature_status IN ('unavailable','insufficient_baseline')
        OR feature_value_numeric IS NOT NULL),
  CONSTRAINT ck_vf_bool CHECK (
        feature_type <> 'boolean' OR feature_status IN ('unavailable','insufficient_baseline')
        OR feature_value_bool IS NOT NULL),
  -- Tier 0 — наблюдение, не производное
  CONSTRAINT ck_vf_tier0_not_derived CHECK (
        tier <> 'tier_0' OR feature_status <> 'derived'),
  CONSTRAINT ck_vf_names CHECK (
        length(trim(feature_name)) > 0 AND length(trim(policy_version)) > 0)
);

CREATE INDEX ix_vf_video ON video_features(video_id);
CREATE INDEX ix_vf_name_policy ON video_features(feature_name, policy_version);
CREATE INDEX ix_vf_policy ON video_features(policy_version);
CREATE INDEX ix_vf_tier ON video_features(tier);
CREATE INDEX ix_vf_numeric ON video_features(feature_name, feature_value_numeric)
  WHERE feature_value_numeric IS NOT NULL;

-- ── append-only: два независимых замка ──────────────────────────────────────
-- 1. права: рантайм не получает UPDATE и DELETE
GRANT SELECT, INSERT ON video_features TO tiktok_rw;
GRANT SELECT ON video_features TO tiktok_ro;
GRANT USAGE, SELECT ON SEQUENCE video_features_id_seq TO tiktok_rw;
REVOKE UPDATE, DELETE ON video_features FROM tiktok_rw, tiktok_ro, PUBLIC;

-- 2. триггер: физический запрет для всех, кроме владельца таблицы.
--    Права уже закрывают рантайм; триггер закрывает и случайную правку
--    из-под владельца, оставляя ему только путь через миграцию.
CREATE OR REPLACE FUNCTION video_features_append_only() RETURNS TRIGGER AS $$
DECLARE owner_name TEXT;
BEGIN
  SELECT pg_get_userbyid(relowner) INTO owner_name
    FROM pg_class WHERE oid = 'video_features'::regclass;
  IF current_user <> owner_name THEN
    RAISE EXCEPTION 'VIDEO_FEATURES_IS_APPEND_ONLY: % запрещён для роли %',
                    TG_OP, current_user;
  END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_vf_append_only
  BEFORE UPDATE OR DELETE ON video_features
  FOR EACH ROW EXECUTE FUNCTION video_features_append_only();

COMMENT ON TABLE video_features IS
  'Длинный append-only слой признаков. Идентичность: '
  '(video_id, feature_name, policy_version). Новая версия политики создаёт '
  'новые строки и никогда не затирает прежние.';
