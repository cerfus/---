-- 0011 · Представление текущих выводов.
--
-- ПРОБЛЕМА, КОТОРУЮ ОНО ЗАКРЫВАЕТ. Таблица insights append-only, и это верно:
-- когда генератор меняется, прежние выводы обязаны остаться как след того,
-- что система думала раньше. Но колонка status отражает состояние НА МОМЕНТ
-- ЗАПИСИ, и строка, переставшая выпускаться новой версией генератора, так и
-- остаётся 'active'. Перевести её в 'superseded' нельзя: это UPDATE, которого
-- у рантайма нет и быть не должно.
--
-- Решение на стороне чтения: актуальность определяется последним прогоном,
-- а не изменяемым флагом. История не теряется, ничего не переписывается.

CREATE VIEW insights_current AS
SELECT i.*
  FROM insights i
  JOIN (SELECT account_id, run_id,
               row_number() OVER (PARTITION BY account_id
                                  ORDER BY max(created_at) DESC, run_id) AS rn
          FROM insights GROUP BY account_id, run_id) latest
    ON latest.account_id = i.account_id AND latest.run_id = i.run_id
 WHERE latest.rn = 1 AND i.status = 'active';

COMMENT ON VIEW insights_current IS
  'Выводы последнего прогона. Прежние прогоны остаются в insights как история: '
  'append-only, ничего не переписывается.';

GRANT SELECT ON insights_current TO tiktok_rw, tiktok_ro;
