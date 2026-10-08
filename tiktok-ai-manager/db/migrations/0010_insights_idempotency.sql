-- 0010 · Два аддитивных дополнения к insights. Таблица пуста (0 строк),
-- существующие значения и семантика не меняются.
--
-- 1. source_kind получает значение 'analytics'. Перечень задумывался как
--    список ДЕТЕКТОРОВ ('reconciliation', 'monotonicity'), и вывод, полученный
--    из аналитического слоя, в нём отсутствовал. Записать его как 'pattern'
--    означало бы сослаться на content_patterns, которых нет, а как 'manual' —
--    выдать автоматический вывод за ручной. Оба варианта — ложь в данных.
--
-- 2. insights получает content_hash и уникальность по нему. Без естественного
--    ключа повторный прогон на тех же данных плодил бы дубликаты, и слой
--    переставал быть идемпотентным, в отличие от всех остальных.
--    В хеш входит содержание вывода, но НЕ входят created_at и run_id.

ALTER TABLE insights DROP CONSTRAINT insights_source_kind_check;
ALTER TABLE insights ADD CONSTRAINT insights_source_kind_check
  CHECK (source_kind IN ('pattern','experiment','reconciliation','manual',
                         'monotonicity','analytics','features','coverage'));

ALTER TABLE insights ADD COLUMN content_hash TEXT;
UPDATE insights SET content_hash = 'legacy:' || insight_id WHERE content_hash IS NULL;
ALTER TABLE insights ALTER COLUMN content_hash SET NOT NULL;
ALTER TABLE insights ADD CONSTRAINT uq_insight_content
  UNIQUE (account_id, content_hash);

-- Вывод уровня RECOMMENDATION обязан ссылаться на основание: без опоры
-- рекомендация превращается в мнение.
ALTER TABLE insights ADD COLUMN based_on JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE insights ADD CONSTRAINT ck_ins_recommendation_basis
  CHECK (claim_type <> 'RECOMMENDATION' OR jsonb_array_length(based_on) >= 1);

CREATE INDEX ix_insights_claim ON insights(account_id, claim_type, created_at DESC);
