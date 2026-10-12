-- 0005 · Стартовые данные, не зависящие от наблюдений.
-- publishing.submit выключена: V0.1 не делает внешних write-вызовов.
INSERT INTO system_capabilities (capability, enabled, enabled_by, enabled_at, reason)
VALUES ('publishing.submit', FALSE, NULL, NULL, NULL)
ON CONFLICT (capability) DO NOTHING;
