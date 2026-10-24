-- The company database's two login roles (backend plan §8 step 3, BE-03, BE-09). Runs once, when
-- the data volume is first created, as the Compose superuser. Passwords equal the role names:
-- throwaway local dev values. On Neon, demo_company/seed.py creates the same roles with SQL (D-077).
--
-- Grants are made by demo_company/seed.py, because the tables do not exist yet when this runs:
-- - blastproof_reader: CONNECT, and SELECT on the company tables (reads through query_database).
-- - blastproof_writer: CONNECT and TEMPORARY, SELECT/INSERT/UPDATE/DELETE on the company tables,
--   and INSERT/SELECT on blastproof.applied_actions (commits and shadow runs). TEMPORARY is needed
--   for the shadow run's before-hash temp tables (D-018, D-076).
-- Neither role may run DDL or manage roles.

CREATE ROLE blastproof_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'blastproof_reader';
CREATE ROLE blastproof_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'blastproof_writer';
