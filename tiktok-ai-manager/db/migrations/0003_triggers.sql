-- 0003 · Триггеры: инварианты, невыразимые через CHECK.

-- ═══ publishing_queue: машина состояний и защита согласия ═══

CREATE OR REPLACE FUNCTION pq_guard() RETURNS TRIGGER AS $$
DECLARE
  allowed  TEXT[] := ARRAY[
    'draft>awaiting_owner_approval','draft>cancelled',
    'awaiting_owner_approval>approved','awaiting_owner_approval>rejected',
    'awaiting_owner_approval>cancelled',
    'approved>submitting',
    'submitting>submitted_for_review','submitting>failed',
    'submitting>approved','submitting>submission_uncertain',
    'submission_uncertain>submitted_for_review','submission_uncertain>failed'
  ];
  owner_id TEXT;
  cap_on   BOOLEAN;
  frozen   BOOLEAN;
BEGIN
  IF OLD.status IS DISTINCT FROM NEW.status THEN
    -- белый список переходов: всё, чего в нём нет, отвергается
    IF NOT (OLD.status || '>' || NEW.status) = ANY(allowed) THEN
      RAISE EXCEPTION 'ILLEGAL_TRANSITION: % -> %', OLD.status, NEW.status;
    END IF;

    IF NEW.status = 'approved' AND OLD.status = 'awaiting_owner_approval' THEN
      -- одноразовость токена: гашение происходит ровно на этом переходе
      IF OLD.token_used_at IS NOT NULL THEN
        RAISE EXCEPTION 'TOKEN_ALREADY_USED';
      END IF;
      -- время проставляет БД, а не вызывающий: иначе можно подставить
      -- прошлый момент и пройти ck_token_not_expired_at_burn
      NEW.token_used_at := now();
      NEW.approved_at   := now();
      IF NEW.token_expires_at < now() THEN
        RAISE EXCEPTION 'TOKEN_EXPIRED';
      END IF;
      -- согласовать может только зарегистрированный владелец аккаунта
      SELECT a.owner_identity INTO owner_id FROM accounts a WHERE a.account_id = NEW.account_id;
      IF NEW.approved_by IS DISTINCT FROM owner_id THEN
        RAISE EXCEPTION 'APPROVER_IS_NOT_ACCOUNT_OWNER';
      END IF;
      IF NEW.submission_idempotency_key IS NULL THEN
        NEW.submission_idempotency_key := gen_random_uuid();
      END IF;
    END IF;

    IF NEW.status = 'submitting' AND OLD.status = 'approved' THEN
      SELECT COALESCE(c.enabled, FALSE) INTO cap_on
        FROM system_capabilities c WHERE c.capability = 'publishing.submit';
      IF NOT COALESCE(cap_on, FALSE) THEN          -- отсутствие строки = выключено
        RAISE EXCEPTION 'CAPABILITY_DISABLED: publishing.submit';
      END IF;
    END IF;

    IF NEW.status IN ('rejected','cancelled') AND NEW.token_used_at IS NULL THEN
      NEW.token_used_at := LEAST(now(), NEW.token_expires_at);   -- гасим токен
    END IF;
  END IF;

  -- заморозка: после запроса согласования материал публикации неизменяем
  frozen := OLD.status IS DISTINCT FROM 'draft';
  IF frozen THEN
    IF NEW.script_id          IS DISTINCT FROM OLD.script_id
    OR NEW.caption            IS DISTINCT FROM OLD.caption
    OR NEW.media_path         IS DISTINCT FROM OLD.media_path
    OR NEW.planned_publish_at IS DISTINCT FROM OLD.planned_publish_at
    OR NEW.account_id         IS DISTINCT FROM OLD.account_id
    OR NEW.experiment_id      IS DISTINCT FROM OLD.experiment_id
    OR NEW.experiment_arm     IS DISTINCT FROM OLD.experiment_arm
    OR NEW.approval_card      IS DISTINCT FROM OLD.approval_card
    OR NEW.content_hash       IS DISTINCT FROM OLD.content_hash THEN
      RAISE EXCEPTION 'FROZEN_AFTER_APPROVAL_REQUEST';
    END IF;
  END IF;

  -- ключ идемпотентности устойчив при повторах
  IF OLD.submission_idempotency_key IS NOT NULL
     AND NEW.submission_idempotency_key IS DISTINCT FROM OLD.submission_idempotency_key THEN
    RAISE EXCEPTION 'IDEMPOTENCY_KEY_IS_IMMUTABLE';
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pq_guard BEFORE UPDATE ON publishing_queue
  FOR EACH ROW EXECUTE FUNCTION pq_guard();

-- замещающий цикл ссылается только на терминально закрытую запись
CREATE OR REPLACE FUNCTION pq_supersedes_terminal() RETURNS TRIGGER AS $$
DECLARE st TEXT;
BEGIN
  IF NEW.supersedes_queue_id IS NOT NULL THEN
    SELECT status INTO st FROM publishing_queue WHERE queue_id = NEW.supersedes_queue_id;
    IF st NOT IN ('rejected','cancelled','failed') THEN
      RAISE EXCEPTION 'SUPERSEDES_TARGET_NOT_TERMINAL: %', st;
    END IF;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pq_supersedes BEFORE INSERT OR UPDATE ON publishing_queue
  FOR EACH ROW EXECUTE FUNCTION pq_supersedes_terminal();

-- ═══ причинность ═══

CREATE OR REPLACE FUNCTION pattern_causality_guard() RETURNS TRIGGER AS $$
DECLARE e RECORD; n_primary INT;
BEGIN
  IF NEW.causality_established THEN
    SELECT * INTO e FROM experiments WHERE experiment_id = NEW.experiment_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'CAUSALITY_REQUIRES_EXPERIMENT';
    END IF;
    IF e.status <> 'concluded' THEN
      RAISE EXCEPTION 'CAUSALITY_REQUIRES_CONCLUDED_EXPERIMENT: status=%', e.status;
    END IF;
    IF e.conclusion IS NULL OR length(trim(e.conclusion)) = 0
       OR e.limitations IS NULL OR length(trim(e.limitations)) = 0
       OR e.confidence_note IS NULL OR length(trim(e.confidence_note)) = 0 THEN
      RAISE EXCEPTION 'CAUSALITY_REQUIRES_CONCLUSION_LIMITATIONS_CONFIDENCE';
    END IF;
    SELECT count(*) INTO n_primary FROM experiment_results
      WHERE experiment_id = NEW.experiment_id AND metric_role = 'primary';
    IF n_primary = 0 THEN
      RAISE EXCEPTION 'CAUSALITY_REQUIRES_PRIMARY_EXPERIMENT_RESULT';
    END IF;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pattern_causality BEFORE INSERT OR UPDATE ON content_patterns
  FOR EACH ROW EXECUTE FUNCTION pattern_causality_guard();

-- завершённый эксперимент терминален: основание причинности нельзя отозвать
CREATE OR REPLACE FUNCTION experiment_concluded_terminal() RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status = 'concluded' AND NEW.status IS DISTINCT FROM 'concluded' THEN
    RAISE EXCEPTION 'CONCLUDED_IS_TERMINAL';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_exp_concluded BEFORE UPDATE ON experiments
  FOR EACH ROW EXECUTE FUNCTION experiment_concluded_terminal();

-- ═══ доказательства DNA закреплены за существующими снимками ═══

CREATE OR REPLACE FUNCTION dna_snapshot_ids_exist() RETURNS TRIGGER AS $$
DECLARE missing BIGINT;
BEGIN
  SELECT s.id INTO missing
    FROM jsonb_array_elements_text(NEW.evidence->'snapshot_ids') AS t(id)
    CROSS JOIN LATERAL (SELECT t.id::BIGINT AS id) s
   WHERE NOT EXISTS (SELECT 1 FROM video_snapshots v WHERE v.snapshot_id = s.id)
   LIMIT 1;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'EVIDENCE_SNAPSHOT_NOT_FOUND: %', missing;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_dna_snapshots BEFORE INSERT OR UPDATE ON content_dna
  FOR EACH ROW EXECUTE FUNCTION dna_snapshot_ids_exist();
