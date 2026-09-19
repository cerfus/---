-- 0014 · Механическая зависимость как ограничение БАЗЫ + период в идентичности
--        отчёта.
--
-- ЧАСТЬ 1. До сих пор запрет на механически зависимые пары жил только в
-- Python. Этого мало: Content DNA и эксперименты пишутся в базу, и запись,
-- обошедшая генератор, никакой проверки бы не встретила. Реестр переносится
-- в таблицу, а запрет — в CHECK и триггеры, то есть туда же, где живут
-- остальные инварианты проекта.
--
-- ЧАСТЬ 2. Идентичность отчёта дополняется period_end. Ключ без него
-- допускал ровно один отчёт на дату начала периода: недельный и месячный
-- отчёты с одним period_start конфликтовали бы между собой, хотя это разные
-- отчёты. Ошибка ещё не проявилась только потому, что существует лишь
-- ежедневный отчёт.

-- ═══ 1. Реестр механически зависимых пар ═══
CREATE TABLE mechanical_metric_pairs (
  pair_id        BIGSERIAL PRIMARY KEY,
  metric_a       TEXT NOT NULL,
  metric_b       TEXT NOT NULL,
  rule_id        TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  mechanism      TEXT NOT NULL CHECK (length(trim(mechanism)) > 0),
  not_an_identity TEXT NOT NULL CHECK (length(trim(not_an_identity)) > 0),
  registered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- канонический порядок: пара не зависит от того, какая метрика названа
  -- первой, поэтому хранится ровно одна её форма
  CONSTRAINT ck_mech_canonical CHECK (metric_a < metric_b),
  CONSTRAINT uq_mech_pair UNIQUE (metric_a, metric_b)
);

COMMENT ON TABLE mechanical_metric_pairs IS
  'Пары метрик, связь между которыми следует из устройства метрик. '
  'Зеркало insights/policies.py::MECHANICAL_DEPENDENCIES; расхождение '
  'ловится тестом. Кандидаты без решения владельца сюда НЕ попадают.';

INSERT INTO mechanical_metric_pairs
  (metric_a, metric_b, rule_id, policy_version, mechanism, not_an_identity)
VALUES (
  'completion_rate', 'duration_sec',
  'mechdep.duration_completion.v1',
  'insights-policy-1.0.0',
  'completion_rate — доля зрителей, досмотревших ролик целиком, то есть '
  'функция выживания, взятая в точке duration_sec. При неизменном '
  'распределении абсолютного времени просмотра эта доля убывает с ростом '
  'длительности по построению метрики.',
  'completion_rate != avg_view_time_sec / duration_sec: проверено на '
  '16 роликах, совпадений 0.');

CREATE FUNCTION is_mechanical_pair(m1 TEXT, m2 TEXT) RETURNS BOOLEAN AS $$
  SELECT EXISTS (SELECT 1 FROM mechanical_metric_pairs
                  WHERE metric_a = least($1, $2)
                    AND metric_b = greatest($1, $2));
$$ LANGUAGE sql STABLE;

-- ═══ 2. content_patterns: механическая пара закономерностью не бывает ═══
-- Колонка mechanical остаётся: она делает намерение видимым, а смену
-- политики — миграцией, а не случайностью. Но допустимое значение теперь
-- ровно одно: claim_type здесь ограничен FACT и HYPOTHESIS, а механически
-- зависимой паре запрещены оба, значит строки с mechanical = TRUE
-- существовать не может.
ALTER TABLE content_patterns
  ADD CONSTRAINT ck_pat_mechanical_no_claim CHECK (mechanical = FALSE);

CREATE FUNCTION pat_mechanical_guard() RETURNS TRIGGER AS $$
BEGIN
  IF is_mechanical_pair(NEW.dimension, NEW.metric) THEN
    RAISE EXCEPTION
      'MECHANICALLY_DEPENDENT: dimension=% metric=% — пара механически '
      'зависима и закономерностью не является', NEW.dimension, NEW.metric
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pat_mechanical BEFORE INSERT OR UPDATE ON content_patterns
  FOR EACH ROW EXECUTE FUNCTION pat_mechanical_guard();

-- ═══ 3. content_dna: механическая пара не бывает доказательством ═══
-- Доказательство, опирающееся на связь пары, обязано назвать пару в
-- evidence->'metric_pair'. Молчание не освобождает: строка уровня FACT и
-- так обязана ссылаться на content_patterns (ck_dna_pattern), а туда
-- механическая пара не проходит по пункту 2.
CREATE FUNCTION dna_mechanical_guard() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.evidence ? 'metric_pair' THEN
    IF jsonb_typeof(NEW.evidence->'metric_pair') <> 'array'
       OR jsonb_array_length(NEW.evidence->'metric_pair') <> 2 THEN
      RAISE EXCEPTION 'DNA_METRIC_PAIR_MALFORMED: ожидается массив из двух метрик'
        USING ERRCODE = '23514';
    END IF;
    IF is_mechanical_pair(NEW.evidence->'metric_pair'->>0,
                          NEW.evidence->'metric_pair'->>1) THEN
      RAISE EXCEPTION
        'MECHANICALLY_DEPENDENT: metric_pair=% — связь следует из устройства '
        'метрик и доказательством Content DNA быть не может',
        NEW.evidence->'metric_pair'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_dna_mechanical BEFORE INSERT OR UPDATE ON content_dna
  FOR EACH ROW EXECUTE FUNCTION dna_mechanical_guard();

-- ═══ 4. experiments: механическая пара не бывает основанием эксперимента ═══
-- Эксперимент меняет variable и смотрит на success_metric. Если они
-- связаны механически, результат известен заранее и измеряет определение
-- метрики, а не гипотезу.
CREATE FUNCTION exp_mechanical_guard() RETURNS TRIGGER AS $$
DECLARE m TEXT;
BEGIN
  IF is_mechanical_pair(NEW.variable, NEW.success_metric) THEN
    RAISE EXCEPTION
      'MECHANICALLY_DEPENDENT: variable=% success_metric=% — связь следует '
      'из устройства метрик и основанием эксперимента быть не может',
      NEW.variable, NEW.success_metric
      USING ERRCODE = '23514';
  END IF;
  FOREACH m IN ARRAY COALESCE(NEW.secondary_metrics, ARRAY[]::TEXT[]) LOOP
    IF is_mechanical_pair(NEW.variable, m) THEN
      RAISE EXCEPTION
        'MECHANICALLY_DEPENDENT: variable=% secondary_metric=%',
        NEW.variable, m
        USING ERRCODE = '23514';
    END IF;
  END LOOP;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_exp_mechanical BEFORE INSERT OR UPDATE ON experiments
  FOR EACH ROW EXECUTE FUNCTION exp_mechanical_guard();

-- ═══ 5. Права на реестр ═══
-- Реестр меняется миграцией и решением владельца, а не рантаймом.
GRANT SELECT ON mechanical_metric_pairs TO tiktok_rw, tiktok_ro;
REVOKE INSERT, UPDATE, DELETE ON mechanical_metric_pairs
  FROM tiktok_rw, tiktok_ro, PUBLIC;

-- ═══ 6. period_end в идентичности отчёта ═══
ALTER TABLE reports DROP CONSTRAINT uq_report_run;
ALTER TABLE reports ADD CONSTRAINT uq_report_run
  UNIQUE (account_id, report_type, period_start, period_end, run_id);

DROP VIEW reports_current;
CREATE VIEW reports_current AS
SELECT r.*
  FROM reports r
  JOIN (SELECT account_id, report_type, period_start, period_end, run_id,
               row_number() OVER (PARTITION BY account_id, report_type,
                                               period_start, period_end
                                  ORDER BY generated_at DESC, run_id) AS rn
          FROM reports) latest
    ON latest.account_id = r.account_id AND latest.report_type = r.report_type
   AND latest.period_start = r.period_start
   AND latest.period_end = r.period_end AND latest.run_id = r.run_id
 WHERE latest.rn = 1;

COMMENT ON VIEW reports_current IS
  'Последний прогон отчёта за каждый (тип, period_start, period_end). '
  'Прежние прогоны остаются в таблице как история.';

GRANT SELECT ON reports_current TO tiktok_rw, tiktok_ro;
