-- PostgreSQL Privileges for Grafana Integration
-- This script grants the smokeping user additional privileges required for Grafana
-- to access PostgreSQL system tables and INFORMATION_SCHEMA

-- Grant access to information_schema for metadata queries
GRANT USAGE ON SCHEMA information_schema TO smokeping;
GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO smokeping;

-- Grant access to pg_catalog for system table queries
GRANT USAGE ON SCHEMA pg_catalog TO smokeping;
GRANT SELECT ON ALL TABLES IN SCHEMA pg_catalog TO smokeping;

-- Specific grants for common PostgreSQL system tables that Grafana may need
GRANT SELECT ON pg_catalog.pg_tables TO smokeping;
GRANT SELECT ON pg_catalog.pg_views TO smokeping;
GRANT SELECT ON pg_catalog.pg_type TO smokeping;
GRANT SELECT ON pg_catalog.pg_class TO smokeping;
GRANT SELECT ON pg_catalog.pg_namespace TO smokeping;
GRANT SELECT ON pg_catalog.pg_attribute TO smokeping;

-- Grant SELECT on sequences for potential future use
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO smokeping;

-- Ensure smokeping can connect to the database
GRANT CONNECT ON DATABASE smokeping_targets TO smokeping;

-- Grant usage on public schema (should already have this, but being explicit)
GRANT USAGE ON SCHEMA public TO smokeping;

-- Grant select on all existing and future tables in public schema
GRANT SELECT ON ALL TABLES IN SCHEMA public TO smokeping;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO smokeping;

-- Log completion
INSERT INTO system_metadata (key, value) VALUES ('grafana_privileges_applied', 'true')
ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = CURRENT_TIMESTAMP;