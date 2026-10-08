-- 0008 · Аналитический слой Phase 3.
--
-- ПОЧЕМУ ТАБЛИЦЫ. Baseline пересчитывается детерминированно из JSONL, но
-- должен быть запрашиваемым: покрытие, окна и выбросы — это то, что читают
-- джойнами, а не перебором файлов. Все четыре таблицы append-only и несут
-- естественный ключ, поэтому пересборка не плодит строк.
--
-- Phase 3 ТОЛЬКО ИЗМЕРЯЕТ. Запрет причинных утверждений и «паттернов»
-- вынесен в CHECK, а не оставлен на добросовестность: строка с
-- causality_claim = TRUE физически не записывается.

CREATE TABLE analytics_video_baseline (
  id                  BIGSERIAL PRIMARY KEY,
  analytics_run_id    UUID NOT NULL,
  video_id            TEXT NOT NULL REFERENCES videos(video_id),
  published_at        TIMESTAMPTZ NOT NULL,
  duration_sec        NUMERIC(10,3),
  views               BIGINT,
  reach               BIGINT,
  likes               BIGINT,
  comments            BIGINT,
  shares              BIGINT,
  favorites           BIGINT,
  like_rate           NUMERIC(12,8),
  comment_rate        NUMERIC(12,8),
  share_rate          NUMERIC(12,8),
  favorite_rate       NUMERIC(12,8),
  engagement_rate     NUMERIC(12,8),
  avg_view_time_sec           NUMERIC(12,4),
  avg_view_time_sec_derived   NUMERIC(12,4),
  avg_view_time_derivation_matches BOOLEAN,
  completion_rate     NUMERIC(10,6),
  per_second_retention_status TEXT NOT NULL
        CHECK (per_second_retention_status = 'unavailable'),
  age_bucket          TEXT,
  age_seconds_observed_max BIGINT,
  source_basis        JSONB NOT NULL,
  reconciliation_basis JSONB NOT NULL,
  eligibility         JSONB NOT NULL,
  observation_refs    TEXT[] NOT NULL,
  reconciliation_refs TEXT[] NOT NULL,
  snapshot_refs       TEXT[] NOT NULL,
  engine_version      TEXT NOT NULL,
  policy_version      TEXT NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_avb UNIQUE (analytics_run_id, video_id),
  -- доля не бывает отрицательной; NULL допустим и нулём не подменяется
  CONSTRAINT ck_avb_rates CHECK (
        (like_rate IS NULL OR like_rate >= 0) AND
        (comment_rate IS NULL OR comment_rate >= 0) AND
        (share_rate IS NULL OR share_rate >= 0) AND
        (favorite_rate IS NULL OR favorite_rate >= 0) AND
        (engagement_rate IS NULL OR engagement_rate >= 0)),
  CONSTRAINT ck_avb_completion CHECK (
        completion_rate IS NULL OR completion_rate BETWEEN 0 AND 1)
);
CREATE INDEX ix_avb_run ON analytics_video_baseline(analytics_run_id);
CREATE INDEX ix_avb_video ON analytics_video_baseline(video_id);

CREATE TABLE analytics_account_baseline (
  id                  BIGSERIAL PRIMARY KEY,
  analytics_run_id    UUID NOT NULL,
  window_name         TEXT NOT NULL,
  age_bucket          TEXT NOT NULL,
  metric              TEXT NOT NULL,
  n                   INT NOT NULL CHECK (n >= 0),
  total_n             INT NOT NULL CHECK (total_n >= 0),
  eligible_n          INT NOT NULL CHECK (eligible_n >= 0),
  coverage_ratio      NUMERIC(10,8),
  min_sample_required INT NOT NULL,
  sample_status       TEXT NOT NULL
        CHECK (sample_status IN ('sufficient_sample','insufficient_sample')),
  mean                NUMERIC(20,8),
  median              NUMERIC(20,8),
  min_value           NUMERIC(20,8),
  max_value           NUMERIC(20,8),
  p25                 NUMERIC(20,8),
  p75                 NUMERIC(20,8),
  p90                 NUMERIC(20,8),
  p90_status          TEXT NOT NULL,
  iqr_lower_fence     NUMERIC(20,8),
  iqr_upper_fence     NUMERIC(20,8),
  n_outliers          INT,
  outlier_rule        TEXT NOT NULL,
  percentile_method   TEXT NOT NULL,
  window_status       TEXT NOT NULL CHECK (window_status IN ('observed','no_data')),
  window_reason       TEXT,
  engine_version      TEXT NOT NULL,
  policy_version      TEXT NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_aab UNIQUE (analytics_run_id, window_name, metric),
  CONSTRAINT ck_aab_eligible CHECK (eligible_n <= total_n),
  -- окно без данных обязано объяснить, почему их нет
  CONSTRAINT ck_aab_no_data CHECK (window_status <> 'no_data' OR window_reason IS NOT NULL)
);
CREATE INDEX ix_aab_run ON analytics_account_baseline(analytics_run_id);

CREATE TABLE analytics_coverage (
  id                      BIGSERIAL PRIMARY KEY,
  analytics_run_id        UUID NOT NULL,
  scope                   TEXT NOT NULL CHECK (scope IN ('all_metrics','metric')),
  metric                  TEXT,
  total_observations      INT NOT NULL CHECK (total_observations >= 0),
  eligible_observations   INT NOT NULL CHECK (eligible_observations >= 0),
  ineligible_observations INT NOT NULL CHECK (ineligible_observations >= 0),
  coverage_pct            NUMERIC(10,8),
  -- три РАЗНЫХ состояния непригодности, которые нельзя складывать в одно
  data_unavailable        INT NOT NULL,
  data_untested           INT NOT NULL,
  data_discrepant         INT NOT NULL,
  detail                  JSONB NOT NULL,
  engine_version          TEXT NOT NULL,
  policy_version          TEXT NOT NULL,
  computed_at             TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_acov UNIQUE NULLS NOT DISTINCT (analytics_run_id, scope, metric),
  CONSTRAINT ck_acov_sum CHECK (
        eligible_observations + ineligible_observations = total_observations)
);
CREATE INDEX ix_acov_run ON analytics_coverage(analytics_run_id);

CREATE TABLE analytics_association (
  id                  BIGSERIAL PRIMARY KEY,
  analytics_run_id    UUID NOT NULL,
  x_metric            TEXT NOT NULL,
  y_metric            TEXT NOT NULL,
  n                   INT NOT NULL CHECK (n >= 0),
  total_n             INT NOT NULL,
  coverage_ratio      NUMERIC(10,8),
  rho                 NUMERIC(12,8) CHECK (rho IS NULL OR rho BETWEEN -1 AND 1),
  p_two_sided         NUMERIC(14,10),
  method              TEXT NOT NULL,
  significance_method TEXT NOT NULL,
  min_sample_required INT NOT NULL,
  sample_status       TEXT NOT NULL,
  status              TEXT NOT NULL,
  interpretation      TEXT NOT NULL CHECK (interpretation = 'not_interpreted'),
  causality_claim     BOOLEAN NOT NULL,
  is_content_pattern  BOOLEAN NOT NULL,
  limitations         JSONB NOT NULL,
  engine_version      TEXT NOT NULL,
  policy_version      TEXT NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_aassoc UNIQUE (analytics_run_id, x_metric, y_metric),
  -- Phase 3 только измеряет: причинность и «паттерн» здесь невозможны
  CONSTRAINT ck_no_causality CHECK (causality_claim = FALSE),
  CONSTRAINT ck_not_a_pattern CHECK (is_content_pattern = FALSE),
  CONSTRAINT ck_limitations_present CHECK (jsonb_array_length(limitations) >= 1)
);
CREATE INDEX ix_aassoc_run ON analytics_association(analytics_run_id);

-- append-only: рантайм добавляет, но не правит и не удаляет
GRANT SELECT, INSERT ON analytics_video_baseline, analytics_account_baseline,
                        analytics_coverage, analytics_association TO tiktok_rw;
GRANT SELECT ON analytics_video_baseline, analytics_account_baseline,
                analytics_coverage, analytics_association TO tiktok_ro;
GRANT USAGE, SELECT ON SEQUENCE
  analytics_video_baseline_id_seq, analytics_account_baseline_id_seq,
  analytics_coverage_id_seq, analytics_association_id_seq TO tiktok_rw;
REVOKE UPDATE, DELETE ON analytics_video_baseline, analytics_account_baseline,
                         analytics_coverage, analytics_association
  FROM tiktok_rw, tiktok_ro, PUBLIC;
