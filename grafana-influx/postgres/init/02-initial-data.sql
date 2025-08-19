-- Initial Data Population for SmokePing Target Management
-- This script populates the database with initial categories, probes, and sources

-- Insert target categories
INSERT INTO target_categories (name, display_name, description) VALUES 
('custom', 'Custom Targets', 'User-defined custom monitoring targets'),
('dns_resolvers', 'DNS Resolvers', 'Public DNS resolver monitoring targets'),
('netflix_oca', 'Netflix OCA', 'Netflix Open Connect Appliance monitoring targets'),
('top_sites', 'Top Sites', 'Popular website monitoring targets');

-- Insert probe configurations
INSERT INTO probes (name, binary_path, step_seconds, pings, forks, is_default) VALUES 
('FPing', '/usr/bin/fping', 300, 10, NULL, TRUE),
('FPing6', '/usr/bin/fping6', 300, 10, NULL, FALSE),
('DNS', '/usr/bin/dig', 300, 5, 5, FALSE);

-- Insert sources (current categories only)
INSERT INTO sources (name, display_name) VALUES 
('topsites', 'Top Sites'),
('netflix', 'Netflix'),
('custom', 'Custom'),
('dns', 'DNS');

-- Insert initial system metadata
INSERT INTO system_metadata (key, value) VALUES 
('schema_version', '1.0'),
('migration_status', 'initialized'),
('last_migration', CURRENT_TIMESTAMP::text),
('bootstrap_completed', 'false');