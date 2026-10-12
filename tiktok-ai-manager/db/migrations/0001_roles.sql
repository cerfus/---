-- 0001 · Роли и границы прав. Выполняется суперпользователем.
-- Владелец схемы и рантайм-роль РАЗДЕЛЕНЫ: приложение никогда не владеет
-- таблицами, поэтому не может обойти отзыв прав сменой владельца.

-- :owner_pw, :rw_pw, :ro_pw подставляются из окружения, в git не попадают.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tiktok_owner') THEN
    CREATE ROLE tiktok_owner LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tiktok_rw') THEN
    CREATE ROLE tiktok_rw LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tiktok_ro') THEN
    CREATE ROLE tiktok_ro LOGIN;
  END IF;
END $$;

-- Пароли задаются на верхнем уровне: psql не подставляет переменные внутри
-- dollar-quoted блоков. Значения приходят из окружения и в git не попадают.
ALTER ROLE tiktok_owner PASSWORD :'owner_pw';
ALTER ROLE tiktok_rw    PASSWORD :'rw_pw';
ALTER ROLE tiktok_ro    PASSWORD :'ro_pw';

-- Ни одна из трёх ролей не является суперпользователем и не создаёт БД.
ALTER ROLE tiktok_owner NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE tiktok_rw    NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE tiktok_ro    NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- Рантайм не должен создавать объекты в public и тем самым обходить гранты.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT  USAGE  ON SCHEMA public TO tiktok_rw, tiktok_ro;
GRANT  CREATE ON SCHEMA public TO tiktok_owner;

-- Время хранится и сравнивается в UTC: OQ-1 (семантика таймзон источников)
-- остаётся unverified, и локальная зона не должна влиять ни на что.
ALTER ROLE tiktok_owner SET timezone = 'UTC';
ALTER ROLE tiktok_rw    SET timezone = 'UTC';
ALTER ROLE tiktok_ro    SET timezone = 'UTC';
