-- 0006 · Точность метки наблюдения.
-- Найдено при реализации: у baseline-раунда R1 метка имеет точность до даты.
-- Записать её как 00:00:00Z без пометки означало бы выдумать точность,
-- которой в источнике нет. Возраст при такой метке обязан быть NULL.
ALTER TABLE video_snapshots
  ADD COLUMN observed_at_precision TEXT NOT NULL DEFAULT 'second'
    CHECK (observed_at_precision IN ('second','date'));

ALTER TABLE video_snapshots
  ADD CONSTRAINT ck_age_requires_precision
    CHECK (observed_at_precision = 'second' OR age_seconds IS NULL);

COMMENT ON COLUMN video_snapshots.observed_at_precision IS
  'second — метка точна; date — известна только дата, возраст не вычисляется';
