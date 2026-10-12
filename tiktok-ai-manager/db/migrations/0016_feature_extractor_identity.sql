-- 0016 · extractor_version входит в идентичность признака.
--
-- ДЕФЕКТ, найденный при сборке Tier 1.
-- Ключ (video_id, feature_name, policy_version) не различает результаты
-- РАЗНЫХ ЭКСТРАКТОРОВ под одной политикой. Для Tier 0 это было незаметно:
-- там экстрактор — арифметика над числами источников, и его версия менялась
-- вместе с политикой. Tier 1 читает файл сторонним декодером, и версия
-- декодера меняется независимо от политики: обновился cv2 — те же кадры
-- могут прочитаться иначе.
--
-- Последствие было бы ровно тем же, что у отчётов до 0013: ON CONFLICT
-- DO NOTHING молча отбрасывал бы новый результат, на диске лежало бы одно,
-- в базе другое, и расхождение никто бы не заметил. Таблица append-only,
-- UPDATE рантайму не выдан, то есть «обновить на месте» было бы нельзя
-- даже намеренно.
--
-- ИСПРАВЛЕНИЕ. Идентичность расширяется до
-- (video_id, feature_name, policy_version, extractor_version).
-- Это РАСШИРЕНИЕ ключа: прежние 720 строк Tier 0/0.5 остаются валидными и
-- не трогаются. «Какое значение в силе» отвечает представление ниже.

ALTER TABLE video_features DROP CONSTRAINT uq_video_feature_version;
ALTER TABLE video_features ADD CONSTRAINT uq_video_feature_version
  UNIQUE (video_id, feature_name, policy_version, extractor_version);

CREATE INDEX ix_vf_extractor ON video_features(feature_name, extractor_version);

COMMENT ON CONSTRAINT uq_video_feature_version ON video_features IS
  'Идентичность признака. Новая версия политики ИЛИ экстрактора создаёт '
  'новую строку и никогда не затирает прежнюю.';

-- Действующее значение признака: последний по времени расчёт.
-- Прежние версии остаются в таблице как история и доступны запросом.
CREATE VIEW video_features_current AS
SELECT f.*
  FROM video_features f
  JOIN (SELECT video_id, feature_name, policy_version, extractor_version,
               row_number() OVER (PARTITION BY video_id, feature_name
                                  ORDER BY computed_at DESC, policy_version DESC,
                                           extractor_version DESC) AS rn
          FROM video_features) latest
    ON latest.video_id = f.video_id AND latest.feature_name = f.feature_name
   AND latest.policy_version = f.policy_version
   AND latest.extractor_version = f.extractor_version
 WHERE latest.rn = 1;

COMMENT ON VIEW video_features_current IS
  'Действующее значение каждого признака: последний расчёт. История прежних '
  'версий политики и экстрактора сохраняется в таблице.';

GRANT SELECT ON video_features_current TO tiktok_rw, tiktok_ro;
