-- Blastproof's own database roles (backend plan §5). Runs once, when the data volume is first created,
-- as the Compose superuser, connected to database "blastproof".
-- Passwords equal the role names: throwaway local dev values. Production uses Neon roles and Fly secrets.

-- Owns the schema and runs migrations (BLASTPROOF_MIGRATOR_URL). Never used by the running app.
CREATE ROLE blastproof_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'blastproof_owner';

-- The running app (BLASTPROOF_DATABASE_URL). Table privileges are granted by step 4's migration.
CREATE ROLE blastproof_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'blastproof_app';

ALTER DATABASE blastproof OWNER TO blastproof_owner;
ALTER SCHEMA public OWNER TO blastproof_owner;

-- Only the two roles (and the superuser) may connect; nobody but the owner may create objects.
-- REVOKE ALL also removes PUBLIC's default TEMPORARY right, so the app cannot create temporary
-- tables. That is deliberate (least privilege; nothing in the stack needs them). If a library ever
-- does, grant TEMPORARY to blastproof_app here and log the decision.
REVOKE ALL ON DATABASE blastproof FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE blastproof TO blastproof_app;
GRANT USAGE ON SCHEMA public TO blastproof_app;
