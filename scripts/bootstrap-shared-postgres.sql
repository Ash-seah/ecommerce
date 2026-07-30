\set ON_ERROR_STOP on

-- Run with psql's default autocommit enabled. CREATE DATABASE cannot execute
-- inside a transaction block, function, or DO block; \gexec emits it as a
-- standalone statement only when the database does not already exist.
\if :{?owner_password}
\else
  \echo 'Missing required psql variable: owner_password'
  \quit 2
\endif
\if :{?reader_password}
\else
  \echo 'Missing required psql variable: reader_password'
  \quit 2
\endif

SELECT
  :'owner_password' ~ '^[a-fA-F0-9]{64,}$' AS owner_password_valid,
  :'reader_password' ~ '^[a-fA-F0-9]{64,}$' AS reader_password_valid,
  :'owner_password' <> :'reader_password' AS passwords_are_distinct
\gset
\if :owner_password_valid
\else
  \echo 'owner_password must be a generated hex secret of at least 32 bytes'
  \quit 2
\endif
\if :reader_password_valid
\else
  \echo 'reader_password must be a generated hex secret of at least 32 bytes'
  \quit 2
\endif
\if :passwords_are_distinct
\else
  \echo 'owner_password and reader_password must be different'
  \quit 2
\endif

SELECT format('CREATE ROLE ecommerce_owner LOGIN PASSWORD %L', :'owner_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ecommerce_owner')
\gexec

SELECT format('CREATE ROLE ecommerce_reader LOGIN PASSWORD %L', :'reader_password')
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ecommerce_reader')
\gexec

-- Rotate to the supplied dedicated credentials on every run and constrain role capabilities.
SELECT format(
  'ALTER ROLE ecommerce_owner WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
  :'owner_password'
)
\gexec
SELECT format(
  'ALTER ROLE ecommerce_reader WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
  :'reader_password'
)
\gexec

SELECT 'CREATE DATABASE ecommerce_master OWNER ecommerce_owner'
WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = 'ecommerce_master')
\gexec

ALTER DATABASE ecommerce_master OWNER TO ecommerce_owner;
REVOKE ALL ON DATABASE ecommerce_master FROM PUBLIC;
GRANT CONNECT, CREATE, TEMPORARY ON DATABASE ecommerce_master TO ecommerce_owner;
GRANT CONNECT ON DATABASE ecommerce_master TO ecommerce_reader;

\connect ecommerce_master

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO ecommerce_owner;
GRANT USAGE ON SCHEMA public TO ecommerce_reader;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ecommerce_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ecommerce_reader;

ALTER DEFAULT PRIVILEGES FOR ROLE ecommerce_owner IN SCHEMA public
  REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE ecommerce_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO ecommerce_reader;

\echo 'ecommerce_master and its isolated roles are ready.'
