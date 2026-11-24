-- MedAkis PostgreSQL provisioning (idempotent). Run as superuser through psql stdin.
-- Creates roles and databases only. The 34-table application schema is NOT created here.
-- Roles are created without a password: they cannot log in until one is set with \password.

-- Roles ---------------------------------------------------------------------
SELECT 'CREATE ROLE medakis_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medakis_owner') \gexec
SELECT 'CREATE ROLE medakis_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medakis_app') \gexec
SELECT 'CREATE ROLE medakis_rag LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medakis_rag') \gexec
-- Synthetic data loader. CONNECT only on medakis_dev and medakis_test, never on medakis_demo.
SELECT 'CREATE ROLE medakis_seed LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'medakis_seed') \gexec

-- Maintenance databases are closed to ordinary roles ----------------------------
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;

-- Databases -----------------------------------------------------------------
SELECT 'CREATE DATABASE medakis_dev OWNER medakis_owner ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'medakis_dev') \gexec
SELECT 'CREATE DATABASE medakis_test OWNER medakis_owner ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'medakis_test') \gexec
SELECT 'CREATE DATABASE medakis_demo OWNER medakis_owner ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'medakis_demo') \gexec

-- Per-database settings and access --------------------------------------------
\connect medakis_dev
REVOKE ALL ON DATABASE medakis_dev FROM PUBLIC;
GRANT CONNECT ON DATABASE medakis_dev TO medakis_app, medakis_rag;
ALTER DATABASE medakis_dev SET timezone TO 'UTC';
ALTER DATABASE medakis_dev SET lock_timeout TO '5s';
ALTER DATABASE medakis_dev SET statement_timeout TO '60s';
ALTER SCHEMA public OWNER TO medakis_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO medakis_app, medakis_rag;
GRANT CONNECT ON DATABASE medakis_dev TO medakis_seed;
GRANT USAGE ON SCHEMA public TO medakis_seed;
CREATE EXTENSION IF NOT EXISTS btree_gist;

\connect medakis_test
REVOKE ALL ON DATABASE medakis_test FROM PUBLIC;
GRANT CONNECT ON DATABASE medakis_test TO medakis_app, medakis_rag;
ALTER DATABASE medakis_test SET timezone TO 'UTC';
ALTER DATABASE medakis_test SET lock_timeout TO '5s';
ALTER DATABASE medakis_test SET statement_timeout TO '60s';
ALTER SCHEMA public OWNER TO medakis_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO medakis_app, medakis_rag;
GRANT CONNECT ON DATABASE medakis_test TO medakis_seed;
GRANT USAGE ON SCHEMA public TO medakis_seed;
CREATE EXTENSION IF NOT EXISTS btree_gist;

\connect medakis_demo
REVOKE ALL ON DATABASE medakis_demo FROM PUBLIC;
GRANT CONNECT ON DATABASE medakis_demo TO medakis_app, medakis_rag;
ALTER DATABASE medakis_demo SET timezone TO 'UTC';
ALTER DATABASE medakis_demo SET lock_timeout TO '5s';
ALTER DATABASE medakis_demo SET statement_timeout TO '60s';
ALTER SCHEMA public OWNER TO medakis_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO medakis_app, medakis_rag;
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- medakis_demo: no grant to medakis_seed (seed tool cannot connect).
REVOKE ALL ON DATABASE medakis_demo FROM medakis_seed;
