-- 0002 · Схема V0.1: 17 таблиц утверждённой архитектуры.
-- Все моменты времени — TIMESTAMPTZ. Семантика таймзон источников (OQ-1)
-- НЕ подтверждена, поэтому наблюдения хранятся как пришли, в UTC, и никуда
-- не переводятся.

-- 1 ─────────────────────────────────────────────────────────────── accounts
CREATE TABLE accounts (
  account_id          BIGSERIAL PRIMARY KEY,
  platform            TEXT NOT NULL DEFAULT 'tiktok',
  handle              TEXT NOT NULL,
  owner_identity      TEXT NOT NULL,
  account_type        TEXT,
  timezone            TEXT NOT NULL,
  timezone_semantics  TEXT NOT NULL DEFAULT 'unverified'
        CHECK (timezone_semantics IN ('unverified','verified_utc','verified_local')),
  metricool_brand_id  BIGINT,
  supermetrics_ds_id  TEXT,
  connected_at        TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_accounts_handle UNIQUE (platform, handle)
);

-- 2 ─────────────────────────────────────────────────────────────── videos
CREATE TABLE videos (
  video_id             TEXT PRIMARY KEY,
  account_id           BIGINT NOT NULL REFERENCES accounts(account_id),
  published_at         TIMESTAMPTZ NOT NULL,
  published_at_src     TEXT NOT NULL,
  url                  TEXT,
  share_url            TEXT,
  embed_url            TEXT,
  thumbnail_url        TEXT,
  thumbnail_sha256     TEXT,
  thumbnail_fetched_at TIMESTAMPTZ,
  caption              TEXT,
  caption_len          INT,
  duration_sec         NUMERIC(8,3) NOT NULL,
  duration_src         TEXT NOT NULL,
  media_type           TEXT,
  is_pre_connection    BOOLEAN NOT NULL,
  first_seen_run_id    UUID NOT NULL,
  CONSTRAINT ck_videos_duration CHECK (duration_sec > 0)
);
CREATE INDEX ix_videos_account_published ON videos(account_id, published_at DESC);
CREATE INDEX ix_videos_duration ON videos(duration_sec);

-- 3 ───────────────────────────────────────────────────── video_snapshots
-- По одной строке на источник. Источники НИКОГДА не сливаются при записи:
-- расхождению нечего затирать.
CREATE TABLE video_snapshots (
  snapshot_id            BIGSERIAL PRIMARY KEY,
  video_id               TEXT NOT NULL REFERENCES videos(video_id),
  source                 TEXT NOT NULL CHECK (source IN ('supermetrics','metricool')),
  fetched_at             TIMESTAMPTZ NOT NULL,
  observed_at            TIMESTAMPTZ NOT NULL,
  observed_at_authority  TEXT NOT NULL
        CHECK (observed_at_authority IN ('source_cache_time','client_fetch_time')),
  age_seconds            BIGINT,
  age_bucket             TEXT NOT NULL
        CHECK (age_bucket IN ('T+10m','T+1h','T+6h','T+24h','T+48h','T+7d','T+30d','backfill')),
  bucket_offset_sec      BIGINT,
  views                  BIGINT,
  reach                  BIGINT,
  likes                  BIGINT,
  comments               BIGINT,
  shares                 BIGINT,
  favorites              BIGINT,
  completion_rate        NUMERIC(8,6),
  avg_view_time_sec      NUMERIC(10,4),
  total_time_watched_sec BIGINT,
  src_foryou             NUMERIC(8,6),
  src_hashtag            NUMERIC(8,6),
  src_sound              NUMERIC(8,6),
  src_search             NUMERIC(8,6),
  src_profile            NUMERIC(8,6),
  raw_ref                TEXT NOT NULL,
  run_id                 UUID NOT NULL,
  CONSTRAINT uq_snapshot UNIQUE (video_id, source, observed_at),
  CONSTRAINT ck_snap_nonneg CHECK (
      COALESCE(views,0)>=0 AND COALESCE(reach,0)>=0 AND COALESCE(likes,0)>=0
      AND COALESCE(comments,0)>=0 AND COALESCE(shares,0)>=0 AND COALESCE(favorites,0)>=0),
  CONSTRAINT ck_snap_rate CHECK (completion_rate IS NULL OR completion_rate BETWEEN 0 AND 1)
);
CREATE INDEX ix_snap_video_age ON video_snapshots(video_id, age_bucket);
CREATE INDEX ix_snap_bucket_src ON video_snapshots(age_bucket, source);
CREATE INDEX ix_snap_run ON video_snapshots(run_id);

-- 4 ─────────────────────────────────────────────────────── video_features
CREATE TABLE video_features (
  video_id            TEXT PRIMARY KEY REFERENCES videos(video_id),
  extracted_at        TIMESTAMPTZ NOT NULL,
  extractor_version   TEXT NOT NULL,
  hashtags            TEXT[] NOT NULL DEFAULT '{}',
  mentions            TEXT[] NOT NULL DEFAULT '{}',
  hashtag_count       INT NOT NULL,
  has_emoji           BOOLEAN NOT NULL,
  cta_present         BOOLEAN NOT NULL,
  caption_word_count  INT NOT NULL,
  top_comments        JSONB,
  comments_fetched_at TIMESTAMPTZ,
  transcript          TEXT,
  ocr_text            TEXT,
  scene_count         INT,
  avg_scene_sec       NUMERIC(8,3),
  thumbnail_path      TEXT,
  extraction_status   JSONB NOT NULL,
  run_id              UUID NOT NULL
);
CREATE INDEX ix_features_hashtags ON video_features USING GIN (hashtags);

-- 5 ─────────────────────────────────────────────────────── video_analysis
-- Длинный формат: одна строка = одно суждение об одном поле.
-- Поля, наблюдаемые только в видеоряде, требуют full_video. Видеофайлов нет,
-- поэтому таких строк быть не может — это норма, а не пробел.
CREATE TABLE video_analysis (
  analysis_id       BIGSERIAL PRIMARY KEY,
  video_id          TEXT NOT NULL REFERENCES videos(video_id),
  field_name        TEXT NOT NULL,
  field_value       TEXT NOT NULL,
  observation_basis TEXT NOT NULL CHECK (observation_basis IN
        ('caption_only','thumbnail_only','thumbnail+caption','comments','full_video')),
  input_kinds       TEXT[] NOT NULL,
  confidence        NUMERIC(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  rubric_version    TEXT NOT NULL,
  analyst           TEXT NOT NULL,
  analyzed_at       TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_analysis UNIQUE (video_id, field_name, rubric_version, analyst),
  CONSTRAINT ck_input_kinds CHECK (
      input_kinds <@ ARRAY['caption','thumbnail','comments','metrics','video_file']::TEXT[]
      AND array_length(input_kinds,1) >= 1),
  CONSTRAINT ck_requires_video CHECK (
      field_name NOT IN ('hook','pacing','scene_changes','payoff','payoff_position_sec',
                         'text_on_screen','text_placement','transcript')
      OR observation_basis = 'full_video')
);
CREATE INDEX ix_analysis_field ON video_analysis(field_name, field_value);

-- 6 ──────────────────────────────────────────────────────── dna_versions
CREATE TABLE dna_versions (
  dna_version_id BIGSERIAL PRIMARY KEY,
  account_id     BIGINT NOT NULL REFERENCES accounts(account_id),
  version        INT NOT NULL,
  built_at       TIMESTAMPTZ NOT NULL,
  frozen_at      TIMESTAMPTZ,
  n_videos       INT NOT NULL,
  n_claims       INT NOT NULL,
  run_id         UUID NOT NULL,
  CONSTRAINT uq_dna_version UNIQUE (account_id, version)
);

-- 7 ───────────────────────────────────────────────────────── experiments
CREATE TABLE experiments (
  experiment_id         BIGSERIAL PRIMARY KEY,
  account_id            BIGINT NOT NULL REFERENCES accounts(account_id),
  code                  TEXT NOT NULL UNIQUE,
  hypothesis            TEXT NOT NULL,
  variable              TEXT NOT NULL,
  control_def           TEXT NOT NULL,
  variant_def           TEXT NOT NULL,
  success_metric        TEXT NOT NULL,
  secondary_metrics     TEXT[] NOT NULL DEFAULT '{}',
  guardrail_metrics     TEXT[] NOT NULL DEFAULT '{}',
  target_sample_size    INT NOT NULL CHECK (target_sample_size >= 5),
  min_detectable_effect NUMERIC(8,4),
  age_bucket            TEXT NOT NULL,
  success_criteria      TEXT NOT NULL,
  preregistered_at      TIMESTAMPTZ,
  start_date            DATE,
  end_date              DATE,
  status                TEXT NOT NULL CHECK (status IN
        ('proposed','preregistered','running','concluded','abandoned','blocked')),
  blocked_by            TEXT,
  result                TEXT,
  conclusion            TEXT,
  confidence_note       TEXT,
  limitations           TEXT,
  dna_version_id        BIGINT REFERENCES dna_versions(dna_version_id),
  dna_id                BIGINT,   -- FK добавляется после content_dna (цикл ссылок)
  CONSTRAINT ck_exp_prereg CHECK (
      status IN ('proposed','blocked','abandoned') OR preregistered_at IS NOT NULL),
  CONSTRAINT ck_exp_concluded CHECK (
      status <> 'concluded' OR (conclusion IS NOT NULL AND limitations IS NOT NULL
                                AND confidence_note IS NOT NULL)),
  CONSTRAINT ck_exp_dates CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date),
  CONSTRAINT ck_exp_dna CHECK (status IN ('proposed','blocked') OR dna_version_id IS NOT NULL)
);

-- 8 ──────────────────────────────────────────────────── content_patterns
CREATE TABLE content_patterns (
  pattern_id               BIGSERIAL PRIMARY KEY,
  account_id               BIGINT NOT NULL REFERENCES accounts(account_id),
  dimension                TEXT NOT NULL,
  pattern_key              TEXT NOT NULL,
  metric                   TEXT NOT NULL,
  age_bucket               TEXT NOT NULL,
  n_sample                 INT NOT NULL CHECK (n_sample >= 0),
  n_positive               INT NOT NULL,
  min_sample_required      INT NOT NULL CHECK (min_sample_required >= 1),
  effect_statistic         TEXT NOT NULL,
  effect_value             NUMERIC(10,6) NOT NULL,
  effect_value_no_outliers NUMERIC(10,6),
  n_sample_no_outliers     INT,
  permutation_p            NUMERIC(10,8),
  outlier_rule             TEXT NOT NULL,
  outlier_dependent        BOOLEAN NOT NULL,
  mechanical               BOOLEAN NOT NULL,
  snapshot_basis           JSONB NOT NULL,
  reconciliation_status    TEXT NOT NULL CHECK (reconciliation_status IN
        ('both_matched','both_expected_transform','both_lagged',
         'single_source','untested_overlap','both_discrepancy','unavailable')),
  claim_type               TEXT NOT NULL CHECK (claim_type IN ('FACT','HYPOTHESIS')),
  competing_explanation    TEXT NOT NULL CHECK (length(trim(competing_explanation)) > 0),
  causality_established    BOOLEAN NOT NULL DEFAULT FALSE,
  experiment_id            BIGINT REFERENCES experiments(experiment_id),
  rubric_version           TEXT,
  computed_at              TIMESTAMPTZ NOT NULL,
  run_id                   UUID NOT NULL,
  CONSTRAINT uq_pattern UNIQUE (account_id, dimension, pattern_key, metric, age_bucket, computed_at),
  CONSTRAINT ck_pat_fact_n        CHECK (claim_type <> 'FACT' OR n_sample >= min_sample_required),
  CONSTRAINT ck_pat_fact_outlier  CHECK (claim_type <> 'FACT' OR outlier_dependent = FALSE),
  CONSTRAINT ck_pat_fact_reconcil CHECK (claim_type <> 'FACT' OR reconciliation_status IN
        ('both_matched','both_expected_transform','both_lagged','single_source')),
  CONSTRAINT ck_pat_causality     CHECK (causality_established = FALSE OR experiment_id IS NOT NULL),
  CONSTRAINT ck_pat_mechanical    CHECK (mechanical = FALSE OR causality_established = FALSE)
);
CREATE INDEX ix_patterns_dim ON content_patterns(account_id, dimension, computed_at DESC);

-- 9 ─────────────────────────────────────────────────────────── content_dna
CREATE TABLE content_dna (
  dna_id                BIGSERIAL PRIMARY KEY,
  account_id            BIGINT NOT NULL REFERENCES accounts(account_id),
  dna_version_id        BIGINT NOT NULL REFERENCES dna_versions(dna_version_id),
  version               INT NOT NULL,
  built_at              TIMESTAMPTZ NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL,
  n_videos              INT NOT NULL,
  section               TEXT NOT NULL CHECK (section IN (
        'successful_formats','weak_formats','hook_patterns','topic_patterns',
        'visual_patterns','text_patterns','pacing_patterns','payoff_patterns',
        'successful_combinations','failed_combinations','unresolved_hypotheses')),
  statement             TEXT NOT NULL,
  claim_type            TEXT NOT NULL CHECK (claim_type IN ('FACT','HYPOTHESIS')),
  n_sample              INT NOT NULL,
  min_sample_required   INT NOT NULL CHECK (min_sample_required >= 1),
  evidence_count        INT NOT NULL CHECK (evidence_count >= 3),
  strength              TEXT NOT NULL CHECK (strength IN ('weak','moderate','strong')),
  source                TEXT NOT NULL,
  source_count          INT NOT NULL CHECK (source_count BETWEEN 1 AND 2),
  reconciliation_status TEXT NOT NULL CHECK (reconciliation_status IN
        ('both_matched','both_expected_transform','both_lagged',
         'single_source','untested_overlap','both_discrepancy','unavailable')),
  pattern_id            BIGINT REFERENCES content_patterns(pattern_id),
  evidence              JSONB NOT NULL,
  CONSTRAINT uq_dna UNIQUE (account_id, version, section, statement),
  CONSTRAINT ck_dna_fact       CHECK (claim_type <> 'FACT' OR n_sample >= min_sample_required),
  CONSTRAINT ck_dna_pattern    CHECK (claim_type <> 'FACT' OR pattern_id IS NOT NULL),
  CONSTRAINT ck_dna_reconcil   CHECK (claim_type <> 'FACT' OR reconciliation_status IN
        ('both_matched','both_expected_transform','both_lagged','single_source')),
  CONSTRAINT ck_dna_ev_videos  CHECK (jsonb_array_length(evidence->'video_ids') >= 3),
  CONSTRAINT ck_dna_ev_count   CHECK (evidence_count = jsonb_array_length(evidence->'video_ids')),
  CONSTRAINT ck_dna_ev_subset  CHECK (evidence_count <= n_sample),
  CONSTRAINT ck_dna_ev_pinned  CHECK (
        jsonb_array_length(evidence->'snapshot_ids') = jsonb_array_length(evidence->'video_ids'))
);

-- цикл ссылок замыкается здесь
ALTER TABLE experiments ADD CONSTRAINT fk_exp_dna
  FOREIGN KEY (dna_id) REFERENCES content_dna(dna_id);

-- 10 ────────────────────────────────────────────────────────────── ideas
CREATE TABLE ideas (
  idea_id            BIGSERIAL PRIMARY KEY,
  account_id         BIGINT NOT NULL REFERENCES accounts(account_id),
  origin             TEXT NOT NULL CHECK (origin IN ('generated','manual')),
  title              TEXT NOT NULL,
  description        TEXT NOT NULL,
  rationale          TEXT NOT NULL,
  expected_mechanism TEXT NOT NULL,
  falsifier          TEXT NOT NULL,
  supporting_evidence JSONB,
  pattern_id         BIGINT REFERENCES content_patterns(pattern_id),
  dna_id             BIGINT REFERENCES content_dna(dna_id),
  dna_version        INT,
  predicted_metric   TEXT NOT NULL,
  predicted_value    NUMERIC(16,4) NOT NULL,
  predicted_at       TIMESTAMPTZ NOT NULL,
  experiment_id      BIGINT REFERENCES experiments(experiment_id),
  status             TEXT NOT NULL CHECK (status IN
        ('proposed','scripted','shot','published','rejected')),
  created_at         TIMESTAMPTZ NOT NULL,
  CONSTRAINT ck_idea_falsifier CHECK (length(trim(falsifier)) > 0),
  CONSTRAINT ck_idea_dna    CHECK (origin <> 'generated' OR dna_id IS NOT NULL),
  CONSTRAINT ck_idea_manual CHECK (origin <> 'manual'
        OR (dna_id IS NULL AND supporting_evidence IS NULL))
);

-- 11 ───────────────────────────────────────────────────────────── scripts
CREATE TABLE scripts (
  script_id            BIGSERIAL PRIMARY KEY,
  idea_id              BIGINT NOT NULL REFERENCES ideas(idea_id),
  version              INT NOT NULL,
  planned_hook         TEXT,          -- ПЛАН, не наблюдение: имя отличается от video_analysis.hook
  body                 TEXT,
  payoff_text          TEXT,
  cta                  TEXT,
  planned_duration_sec NUMERIC(8,3),
  shot_list            TEXT,
  video_id             TEXT REFERENCES videos(video_id),
  created_at           TIMESTAMPTZ NOT NULL,
  CONSTRAINT uq_script UNIQUE (idea_id, version)
);

-- 12 ──────────────────────────────────────────────────── experiment_results
CREATE TABLE experiment_results (
  result_id     BIGSERIAL PRIMARY KEY,
  experiment_id BIGINT NOT NULL REFERENCES experiments(experiment_id),
  group_name    TEXT NOT NULL CHECK (group_name IN ('control','variant')),
  metric        TEXT NOT NULL,
  metric_role   TEXT NOT NULL CHECK (metric_role IN ('primary','secondary','guardrail')),
  n             INT NOT NULL CHECK (n >= 1),
  n_non_null    INT NOT NULL,
  median_value  NUMERIC(18,6),
  mad_value     NUMERIC(18,6),
  p10_value     NUMERIC(18,6),
  p90_value     NUMERIC(18,6),
  age_bucket    TEXT NOT NULL,
  video_ids     TEXT[] NOT NULL,
  computed_at   TIMESTAMPTZ NOT NULL,
  run_id        UUID NOT NULL,
  CONSTRAINT uq_exp_result UNIQUE (experiment_id, group_name, metric, computed_at),
  CONSTRAINT ck_coverage CHECK (n_non_null <= n)
);

-- 13 ──────────────────────────────────────────────────────────── insights
CREATE TABLE insights (
  insight_id            BIGSERIAL PRIMARY KEY,
  account_id            BIGINT NOT NULL REFERENCES accounts(account_id),
  statement             TEXT NOT NULL,
  claim_type            TEXT NOT NULL CHECK (claim_type IN
        ('FACT','HYPOTHESIS','RECOMMENDATION','ANOMALY','DATA_QUALITY')),
  n_sample              INT,
  min_sample_required   INT,
  source_kind           TEXT NOT NULL CHECK (source_kind IN
        ('pattern','experiment','reconciliation','manual','monotonicity')),
  pattern_id            BIGINT REFERENCES content_patterns(pattern_id),
  experiment_id         BIGINT REFERENCES experiments(experiment_id),
  competing_explanation TEXT,
  status                TEXT NOT NULL CHECK (status IN ('active','superseded','retracted')),
  superseded_by         BIGINT REFERENCES insights(insight_id),
  created_at            TIMESTAMPTZ NOT NULL,
  run_id                UUID NOT NULL,
  CONSTRAINT ck_ins_fact CHECK (claim_type <> 'FACT'
        OR (n_sample IS NOT NULL AND min_sample_required IS NOT NULL
            AND n_sample >= min_sample_required)),
  CONSTRAINT ck_ins_hyp  CHECK (claim_type <> 'HYPOTHESIS' OR competing_explanation IS NOT NULL),
  CONSTRAINT ck_ins_sup  CHECK (status <> 'superseded' OR superseded_by IS NOT NULL)
);
CREATE INDEX ix_insights_active ON insights(account_id, status, created_at DESC);

-- 14 ───────────────────────────────────────────────────────────── reports
CREATE TABLE reports (
  report_id           BIGSERIAL PRIMARY KEY,
  account_id          BIGINT NOT NULL REFERENCES accounts(account_id),
  report_type         TEXT NOT NULL CHECK (report_type IN ('daily','weekly')),
  period_start        DATE NOT NULL,
  period_end          DATE NOT NULL,
  generated_at        TIMESTAMPTZ NOT NULL,
  path                TEXT NOT NULL,
  summary             TEXT,
  n_videos_in_period  INT NOT NULL,
  n_new_snapshots     INT NOT NULL,
  data_completeness   JSONB NOT NULL,
  blocked_conclusions JSONB NOT NULL DEFAULT '[]',
  run_id              UUID NOT NULL,
  CONSTRAINT uq_report UNIQUE (account_id, report_type, period_start),
  CONSTRAINT ck_report_period CHECK (period_end >= period_start)
);

-- 15 ────────────────────────────────────────────────── system_capabilities
-- Способность — это ДАННЫЕ, а не схема. Включение = UPDATE с указанием
-- автора и причины, а не ослабляющая миграция.
CREATE TABLE system_capabilities (
  capability TEXT PRIMARY KEY,
  enabled    BOOLEAN NOT NULL DEFAULT FALSE,
  enabled_by TEXT,
  enabled_at TIMESTAMPTZ,
  reason     TEXT,
  CONSTRAINT ck_enabled_audited CHECK (
      enabled = FALSE OR (enabled_by IS NOT NULL AND enabled_at IS NOT NULL
                          AND reason IS NOT NULL))
);

-- 16 ─────────────────────────────────────────────────────── publishing_queue
CREATE TABLE publishing_queue (
  queue_id                   BIGSERIAL PRIMARY KEY,
  account_id                 BIGINT NOT NULL REFERENCES accounts(account_id),
  script_id                  BIGINT NOT NULL REFERENCES scripts(script_id),
  experiment_id              BIGINT REFERENCES experiments(experiment_id),
  experiment_arm             TEXT,
  planned_publish_at         TIMESTAMPTZ,
  media_path                 TEXT,
  caption                    TEXT,
  status                     TEXT NOT NULL CHECK (status IN
        ('draft','awaiting_owner_approval','approved','submitting',
         'submitted_for_review','submission_uncertain','rejected','cancelled','failed')),
  approval_token_hash        BYTEA,
  token_salt                 BYTEA,
  token_expires_at           TIMESTAMPTZ,
  token_used_at              TIMESTAMPTZ,
  approval_card              JSONB,
  content_hash               BYTEA,
  approved_by                TEXT,
  approved_at                TIMESTAMPTZ,
  rejected_by                TEXT,
  rejected_at                TIMESTAMPTZ,
  submission_idempotency_key UUID,
  submission_started_at      TIMESTAMPTZ,
  submission_lease_until     TIMESTAMPTZ,
  retry_count                INT NOT NULL DEFAULT 0,
  supersedes_queue_id        BIGINT REFERENCES publishing_queue(queue_id),
  correlation_id             UUID NOT NULL,
  created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT ck_token_not_expired_at_burn CHECK (
        token_used_at IS NULL OR token_used_at <= token_expires_at),
  CONSTRAINT ck_token_present CHECK (status <> 'awaiting_owner_approval'
        OR (approval_token_hash IS NOT NULL AND token_salt IS NOT NULL
            AND token_expires_at IS NOT NULL AND approval_card IS NOT NULL
            AND content_hash IS NOT NULL)),
  CONSTRAINT ck_token_burned CHECK (
        status NOT IN ('approved','submitting','submitted_for_review',
                       'submission_uncertain','failed')
        OR token_used_at IS NOT NULL),
  CONSTRAINT ck_approved_by CHECK (
        status NOT IN ('approved','submitting','submitted_for_review','submission_uncertain')
        OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CONSTRAINT ck_rejected_by CHECK (
        status <> 'rejected' OR (rejected_by IS NOT NULL AND rejected_at IS NOT NULL)),
  CONSTRAINT ck_retry_cap CHECK (retry_count BETWEEN 0 AND 3),
  CONSTRAINT ck_lease CHECK (status <> 'submitting'
        OR (submission_started_at IS NOT NULL AND submission_lease_until IS NOT NULL)),
  CONSTRAINT ck_idem_key CHECK (
        status NOT IN ('submitting','submitted_for_review','submission_uncertain')
        OR submission_idempotency_key IS NOT NULL),
  CONSTRAINT ck_arm_pairing CHECK ((experiment_id IS NULL) = (experiment_arm IS NULL)
        AND (experiment_arm IS NULL OR experiment_arm IN ('control','variant'))),
  CONSTRAINT ck_no_self_super CHECK (supersedes_queue_id IS DISTINCT FROM queue_id),
  CONSTRAINT uq_idem       UNIQUE (submission_idempotency_key),
  CONSTRAINT uq_token_hash UNIQUE (approval_token_hash),
  CONSTRAINT uq_supersedes UNIQUE (supersedes_queue_id)
);

-- Один активный цикл публикации на сценарий. Терминальные отказы
-- (rejected/cancelled/failed) остаются в истории и не блокируют новый цикл.
CREATE UNIQUE INDEX uq_active_queue_per_script ON publishing_queue(script_id)
  WHERE status NOT IN ('rejected','cancelled','failed');
CREATE INDEX ix_claimable ON publishing_queue(approved_at) WHERE status = 'approved';
CREATE INDEX ix_lease_expiry ON publishing_queue(submission_lease_until)
  WHERE status = 'submitting';

-- 17 ────────────────────────────────────────────────────── publishing_history
CREATE TABLE publishing_history (
  history_id                 BIGSERIAL PRIMARY KEY,
  publishing_queue_id        BIGINT NOT NULL REFERENCES publishing_queue(queue_id),
  video_id                   TEXT REFERENCES videos(video_id),
  action                     TEXT NOT NULL CHECK (action IN
        ('created','submitted_for_approval','approved','rejected','submitted','failed')),
  actor                      TEXT NOT NULL,
  requested_at               TIMESTAMPTZ,
  requested_by               TEXT NOT NULL,
  occurred_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  request_payload            JSONB,
  response_payload           JSONB,
  resulting_status           TEXT NOT NULL,
  error                      TEXT,
  external_post_id           TEXT,
  correlation_id             UUID NOT NULL,
  submission_idempotency_key UUID,
  CONSTRAINT ck_submit_fields CHECK (action <> 'submitted'
        OR (requested_at IS NOT NULL AND request_payload IS NOT NULL))
);
CREATE INDEX ix_history_queue ON publishing_history(publishing_queue_id, occurred_at);
