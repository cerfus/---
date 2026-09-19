-- 0013 · Прогон входит в идентичность отчёта.
--
-- ДЕФЕКТ. Ключ (account_id, report_type, period_start) допускал ровно один
-- отчёт за период. Таблица append-only, UPDATE рантайму не выдан, поэтому
-- пересборка отчёта после изменения правил вывода молча не применялась:
-- на диске лежал новый текст, а в базе оставался прежний, с прежним числом
-- заблокированных выводов. База и артефакт расходились.
--
-- ИСПРАВЛЕНИЕ, то же, что принято для insights в 0012: журнал отвечает на
-- вопрос «что система отчитала в прогоне X». Несколько отчётов за один
-- период — это не дубликаты, а след изменения правил. Идемпотентность цела:
-- run_id детерминирован от содержания.

ALTER TABLE reports DROP CONSTRAINT uq_report;
ALTER TABLE reports ADD CONSTRAINT uq_report_run
  UNIQUE (account_id, report_type, period_start, run_id);

CREATE VIEW reports_current AS
SELECT r.*
  FROM reports r
  JOIN (SELECT account_id, report_type, period_start, run_id,
               row_number() OVER (PARTITION BY account_id, report_type, period_start
                                  ORDER BY generated_at DESC, run_id) AS rn
          FROM reports) latest
    ON latest.account_id = r.account_id AND latest.report_type = r.report_type
   AND latest.period_start = r.period_start AND latest.run_id = r.run_id
 WHERE latest.rn = 1;

COMMENT ON VIEW reports_current IS
  'Последний отчёт за каждый период. Прежние прогоны остаются как история.';

GRANT SELECT ON reports_current TO tiktok_rw, tiktok_ro;
