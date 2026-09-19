-- 0007 · Таблица результатов сверки.
--
-- ПОЧЕМУ ТАБЛИЦА, А НЕ MATERIALIZED VIEW.
-- Утверждённая архитектура предполагала сверку представлением, исходя из того,
-- что классификация выразима на SQL. Правило both_lagged этого не допускает:
-- оно требует поиска по произвольному числу предшествующих наблюдений
-- канонического источника, точного равенства с ОДНИМ из прошлых значений,
-- вывода порядка из монотонности при неточной метке времени и сборки
-- доказательства. Реализация этого на SQL создала бы вторую копию правила
-- рядом с проверенной python-реализацией, и две копии неизбежно разойдутся.
-- Поэтому результат считается один раз и сохраняется как воспроизводимые
-- данные с построчным content_hash, а не пересчитывается двумя способами.
--
-- Существующие таблицы не изменяются. Таблица append-only.

CREATE TABLE reconciliation_results (
  result_id             BIGSERIAL PRIMARY KEY,
  reconciliation_run_id UUID NOT NULL,
  video_id              TEXT NOT NULL REFERENCES videos(video_id),
  metric                TEXT NOT NULL,
  slice_key             TEXT NOT NULL,
  classification        TEXT NOT NULL CHECK (classification IN
        ('both_matched','both_expected_transform','both_lagged',
         'single_source','untested_overlap','both_discrepancy','unavailable')),
  rule_id               TEXT NOT NULL,
  source_a              TEXT NOT NULL,
  source_b              TEXT NOT NULL,
  value_a               NUMERIC(20,6),
  value_b               NUMERIC(20,6),
  observation_id_a      TEXT,
  observation_id_b      TEXT,
  observed_at_a         TIMESTAMPTZ,
  observed_at_b         TIMESTAMPTZ,
  canonical_source      TEXT CHECK (canonical_source IN ('supermetrics','metricool')),
  lagging_source        TEXT CHECK (lagging_source IN ('supermetrics','metricool')),
  lag_basis             TEXT,
  competing_explanation TEXT,
  evidence              JSONB NOT NULL,
  engine_version        TEXT NOT NULL,
  policy_version        TEXT NOT NULL,
  content_hash          TEXT NOT NULL,
  computed_at           TIMESTAMPTZ NOT NULL,

  CONSTRAINT uq_recon UNIQUE (reconciliation_run_id, video_id, metric, slice_key),

  -- both_lagged обязан нести полное доказательство и конкурирующее объяснение
  CONSTRAINT ck_lag_complete CHECK (classification <> 'both_lagged' OR (
        canonical_source IS NOT NULL AND lagging_source IS NOT NULL
        AND canonical_source <> lagging_source
        AND lag_basis IS NOT NULL AND competing_explanation IS NOT NULL
        AND evidence ? 'lag_evidence')),
  -- противоречие не назначает канонический источник
  CONSTRAINT ck_discrepancy_no_canonical CHECK (
        classification <> 'both_discrepancy' OR canonical_source IS NULL),
  -- отстающий источник бывает только у доказанного отставания
  CONSTRAINT ck_lagging_only_on_lag CHECK (
        lagging_source IS NULL OR classification = 'both_lagged'),
  -- у сравнения двух источников значения обязаны присутствовать
  CONSTRAINT ck_pair_values CHECK (
        classification NOT IN ('both_matched','both_expected_transform',
                               'both_lagged','both_discrepancy')
        OR (value_a IS NOT NULL AND value_b IS NOT NULL))
);

CREATE INDEX ix_recon_run ON reconciliation_results(reconciliation_run_id);
CREATE INDEX ix_recon_video_metric ON reconciliation_results(video_id, metric);
CREATE INDEX ix_recon_class ON reconciliation_results(classification);

-- append-only: рантайм добавляет, но не правит и не удаляет
GRANT SELECT, INSERT ON reconciliation_results TO tiktok_rw;
GRANT SELECT ON reconciliation_results TO tiktok_ro;
GRANT USAGE, SELECT ON SEQUENCE reconciliation_results_result_id_seq TO tiktok_rw;
REVOKE UPDATE, DELETE ON reconciliation_results FROM tiktok_rw, tiktok_ro, PUBLIC;
