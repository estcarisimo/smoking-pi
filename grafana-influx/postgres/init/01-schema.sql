-- PostgreSQL Schema for SmokePing Target Management
-- This script initializes the database schema with all required tables

-- Create target_categories table
CREATE TABLE target_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create probes table
CREATE TABLE probes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    binary_path VARCHAR(200) NOT NULL,
    step_seconds INTEGER NOT NULL DEFAULT 300,
    pings INTEGER NOT NULL DEFAULT 10,
    forks INTEGER NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create targets table with all metadata as proper columns
CREATE TABLE targets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    host VARCHAR(500) NOT NULL,
    title VARCHAR(200) NOT NULL,
    category_id INTEGER NOT NULL REFERENCES target_categories(id),
    probe_id INTEGER NOT NULL REFERENCES probes(id),
    lookup VARCHAR(200) NULL, -- DNS query domain (dns targets only)
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Netflix OCA specific metadata columns
    asn VARCHAR(20) NULL,
    cache_id VARCHAR(20) NULL,
    city VARCHAR(100) NULL,
    domain VARCHAR(500) NULL,
    iata_code VARCHAR(10) NULL,
    latitude DECIMAL(10,8) NULL,
    longitude DECIMAL(11,8) NULL,
    location_code VARCHAR(20) NULL,
    raw_city VARCHAR(200) NULL,
    metadata_type VARCHAR(50) NULL
);

-- Create sources table (current categories only)
CREATE TABLE sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create system_metadata table (version tracking, etc.)
CREATE TABLE system_metadata (
    id SERIAL PRIMARY KEY,
    key VARCHAR(100) UNIQUE NOT NULL,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for performance
CREATE INDEX idx_targets_category_active ON targets(category_id, is_active);
CREATE INDEX idx_targets_name ON targets(name);
CREATE INDEX idx_targets_host ON targets(host);
CREATE INDEX idx_targets_active ON targets(is_active);
CREATE INDEX idx_targets_category ON targets(category_id);
CREATE INDEX idx_targets_probe ON targets(probe_id);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updated_at columns
CREATE TRIGGER update_target_categories_updated_at BEFORE UPDATE ON target_categories FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_probes_updated_at BEFORE UPDATE ON probes FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_targets_updated_at BEFORE UPDATE ON targets FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_sources_updated_at BEFORE UPDATE ON sources FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
CREATE TRIGGER update_system_metadata_updated_at BEFORE UPDATE ON system_metadata FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();