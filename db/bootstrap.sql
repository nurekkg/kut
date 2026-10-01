-- Bootstrap ролей БД (ADR-001 §1). Идемпотентен: безопасно запускать при каждом `docker compose up`.
-- Выполняется psql под суперпользователем кластера (POSTGRES_USER); приложение этим логином не пользуется.
-- Пароли берутся только из окружения (\getenv), в командную строку и в лог они не попадают.
-- Схему и права на таблицы создают миграции от имени kut_owner, здесь только роли и уровень БД.

\set ON_ERROR_STOP on
SET log_statement = 'none';
SET log_min_error_statement = 'panic';

\getenv app_env APP_ENV
\getenv owner_pw KUT_OWNER_PASSWORD
\getenv app_pw KUT_APP_PASSWORD
\getenv portal_pw KUT_PORTAL_PASSWORD
\getenv worker_pw KUT_WORKER_PASSWORD
\getenv with_test_db KUT_BOOTSTRAP_TEST_DB

\if :{?app_env}
\else
  \set app_env development
\endif
\if :{?with_test_db}
\else
  \set with_test_db 0
\endif

\if :{?owner_pw}
\else
  DO $$ BEGIN RAISE EXCEPTION 'KUT_OWNER_PASSWORD is not set'; END $$;
\endif
\if :{?app_pw}
\else
  DO $$ BEGIN RAISE EXCEPTION 'KUT_APP_PASSWORD is not set'; END $$;
\endif
\if :{?portal_pw}
\else
  DO $$ BEGIN RAISE EXCEPTION 'KUT_PORTAL_PASSWORD is not set'; END $$;
\endif
\if :{?worker_pw}
\else
  DO $$ BEGIN RAISE EXCEPTION 'KUT_WORKER_PASSWORD is not set'; END $$;
\endif

SELECT (:'owner_pw' = '' OR :'app_pw' = '' OR :'portal_pw' = '' OR :'worker_pw' = '') AS has_empty \gset
\if :has_empty
  DO $$ BEGIN RAISE EXCEPTION 'role passwords must not be empty'; END $$;
\endif

SELECT (:'app_env' = 'production'
        AND concat_ws(' ', :'owner_pw', :'app_pw', :'portal_pw', :'worker_pw') LIKE '%dev_password%') AS dev_in_prod \gset
\if :dev_in_prod
  DO $$ BEGIN RAISE EXCEPTION 'development placeholder passwords are not allowed with APP_ENV=production'; END $$;
\endif

SELECT format('CREATE ROLE %I NOLOGIN', r.name)
FROM (VALUES ('kut_owner'), ('kut_app'), ('kut_portal'), ('kut_worker')) AS r(name)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name) \gexec

-- Ни одна роль не суперпользователь. BYPASSRLS только у kut_worker (Celery).
ALTER ROLE kut_owner  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'owner_pw';
ALTER ROLE kut_app    WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'app_pw';
ALTER ROLE kut_portal WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'portal_pw';
ALTER ROLE kut_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS   PASSWORD :'worker_pw';

SELECT current_database() AS maindb \gset

-- Тестовая БД (dev и CI): тесты пересоздают в ней схему от имени kut_owner, суперпользователь им не нужен.
-- С APP_ENV=production она не создаётся, что бы ни стояло в KUT_BOOTSTRAP_TEST_DB.
SELECT format('CREATE DATABASE %I OWNER kut_owner', :'maindb' || '_test')
WHERE :'with_test_db' = '1' AND :'app_env' <> 'production'
  AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'maindb' || '_test') \gexec

SELECT format('ALTER DATABASE %I OWNER TO kut_owner', datname)
FROM pg_database WHERE datname IN (:'maindb', :'maindb' || '_test') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', datname)
FROM pg_database WHERE datname IN (:'maindb', :'maindb' || '_test') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO kut_owner, kut_app, kut_portal, kut_worker', datname)
FROM pg_database WHERE datname IN (:'maindb', :'maindb' || '_test') \gexec

-- Таблицы, созданные ранее суперпользователем (alembic_version из Фазы 0), переходят к kut_owner,
-- иначе миграции под kut_owner не смогут обновить версию.
SELECT format('ALTER TABLE %I.%I OWNER TO kut_owner', schemaname, tablename)
FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user \gexec

SELECT rolname, rolcanlogin AS login, rolsuper AS superuser, rolbypassrls AS bypassrls
FROM pg_roles WHERE rolname LIKE 'kut\_%' ORDER BY rolname;
