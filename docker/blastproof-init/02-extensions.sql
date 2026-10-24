-- Extensions Blastproof's schema needs, created as the Compose superuser (backend plan §5, step 4).
-- pgvector is not a trusted extension (pg_available_extension_versions: superuser = t, trusted = f),
-- so blastproof_owner cannot create it. The first migration still runs
-- CREATE EXTENSION IF NOT EXISTS vector, which is then a no-op (D-082).
-- Runs only when the data volume is first created. For an existing volume, run once:
--   docker compose exec blastproof-db psql -U blastproof -d blastproof -f /docker-entrypoint-initdb.d/02-extensions.sql
CREATE EXTENSION IF NOT EXISTS vector;
