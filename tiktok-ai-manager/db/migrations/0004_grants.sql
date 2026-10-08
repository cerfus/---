-- 0004 · Права. Владелец схемы — tiktok_owner, приложение — tiktok_rw.
-- Рантайм не владеет таблицами и потому не может отменить собственные отзывы.

GRANT USAGE ON SCHEMA public TO tiktok_rw, tiktok_ro;

-- чтение — всем трём ролям
GRANT SELECT ON ALL TABLES IN SCHEMA public TO tiktok_rw, tiktok_ro;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tiktok_rw;

-- append-only: только INSERT, без UPDATE и DELETE
GRANT INSERT ON
  videos, video_snapshots, video_analysis, content_patterns,
  dna_versions, content_dna, scripts, experiment_results,
  insights, reports, publishing_history
TO tiktok_rw;

-- изменяемые по назначению: INSERT и UPDATE, но без DELETE нигде
GRANT INSERT, UPDATE ON
  accounts, video_features, ideas, experiments,
  publishing_queue, system_capabilities
TO tiktok_rw;

-- DELETE не выдаётся ни на одну таблицу
REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM tiktok_rw, tiktok_ro;
-- явный повторный отзыв на append-only сущностях
REVOKE UPDATE, DELETE ON
  dna_versions, content_dna, scripts, publishing_history,
  videos, video_snapshots, video_analysis, content_patterns,
  experiment_results, insights, reports
FROM tiktok_rw, tiktok_ro, PUBLIC;

-- аналитическая роль читает и ничего не пишет
REVOKE INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM tiktok_ro;
