-- Enable pgvector in ecommerce_master. Run as a Postgres superuser, e.g.:
--   docker exec -i terabit-fintech-postgres-1 psql -U financial -d ecommerce_master -f - < scripts/enable_pgvector.sql
-- The shared postgres:16-alpine image does not include pgvector; use
-- pgvector/pgvector:pg16 (or install the OS package) if CREATE EXTENSION fails.

\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector;
GRANT USAGE ON SCHEMA public TO ecommerce_reader;
