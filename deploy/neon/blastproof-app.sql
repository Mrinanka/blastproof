-- Sets up the production database for Blastproof's own data: the Neon project blastproof-app
-- (backend plan §15, docs/deploy.md). Paste into the Neon SQL Editor on that project's `neondb`
-- database, signed in as the default `neondb_owner` role, after replacing the two placeholders.
--
-- It mirrors docker/blastproof-init/01-roles.sql (guarded by backend/tests/test_neon_app_sql.py).
-- Make each password with:  python -c "import secrets; print(secrets.token_urlsafe(32))"
-- Plain text only, at least 12 characters (Neon's rule). Use the same two passwords in the Fly
-- secrets BLASTPROOF_MIGRATOR_URL (owner) and BLASTPROOF_DATABASE_URL / CHECKPOINTER_DATABASE_URL
-- (app). Never commit them, and never paste this file back with the passwords filled in.

-- Roles made with SQL are not in neon_superuser (Neon's roles docs), so neither can do more than
-- its own job.
CREATE ROLE blastproof_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '<OWNER_PASSWORD>';
CREATE ROLE blastproof_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '<APP_PASSWORD>';

-- ALTER DATABASE ... OWNER needs membership in the new owner role.
GRANT blastproof_owner TO neondb_owner;
ALTER DATABASE neondb OWNER TO blastproof_owner;
ALTER SCHEMA public OWNER TO blastproof_owner;

-- Only the two roles (and the project owner) may connect; nobody but the owner may create objects.
-- REVOKE ALL also removes PUBLIC's default TEMPORARY right, so the app cannot create temporary
-- tables (deliberate: least privilege).
REVOKE ALL ON DATABASE neondb FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE neondb TO blastproof_app;
GRANT USAGE ON SCHEMA public TO blastproof_app;

-- pgvector is not a trusted extension locally; on Neon the project owner can create it, so the
-- first migration's CREATE EXTENSION IF NOT EXISTS vector is then a no-op (D-082).
CREATE EXTENSION IF NOT EXISTS vector;
