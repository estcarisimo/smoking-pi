# SmokePing Configuration Manager

<div align="center">
  <img src="../img/logo.jpg" alt="Smoking Pi Logo" width="100"/>
</div>

This module provides centralized configuration management for SmokePing targets and probes with **database-first architecture**, PostgreSQL integration, YAML fallback compatibility, and automated generation.

## Directory Structure

```
config-manager/
├── config/              # YAML fallback configuration files
│   ├── sources.yaml    # All available target sources (fallback)
│   ├── targets.yaml    # Currently active targets (fallback)  
│   └── probes.yaml     # Probe configurations
├── templates/          # Jinja2 templates
│   └── smokeping_targets.j2  # SmokePing Targets file template
├── models.py           # PostgreSQL database models
└── scripts/            # Management scripts
    ├── config_generator.py   # Generate SmokePing configs from database/YAML
    ├── migrate_yaml_to_db.py # Migrate YAML configurations to PostgreSQL
    └── oca_fetcher.py       # Fetch Netflix OCA servers
```

## Configuration Sources

### Primary: PostgreSQL Database
The config manager primarily reads from PostgreSQL database:
- **targets table**: All monitoring targets with active/inactive status
- **target_categories table**: Target categorization (dns_resolvers, top_sites, netflix_oca, custom)
- **Normalized schema**: Proper database relationships and constraints
- **Real-time updates**: Configuration generated dynamically from database

### Fallback: YAML Files
Automatic fallback when database unavailable:

#### sources.yaml
Defines all available sources for monitoring targets:
- **static**: Built-in targets (websites, DNS resolvers)
- **dynamic**: Auto-discovered targets (Netflix OCA, top sites)
- **custom**: User-defined targets

#### targets.yaml  
Contains the currently active targets selected from sources. In database mode, this file is used for fallback only.

**Includes DNS Resolver Configuration:**
```yaml
dns_resolvers:
  - category: dns_resolvers
    host: 8.8.8.8
    lookup: google.com
    name: GoogleDNS
    probe: DNS
    title: Google DNS
  - category: dns_resolvers
    host: 1.1.1.1
    lookup: cloudflare.com
    name: CloudflareDNS
    probe: DNS
    title: Cloudflare DNS
  - category: dns_resolvers
    host: 9.9.9.9
    lookup: quad9.net
    name: Quad9DNS
    probe: DNS
    title: Quad9 DNS
```

#### probes.yaml
Defines available SmokePing probe types and their configurations (FPing, DNS, etc.)

## Usage

### Database Mode (Default)

1. **Generate SmokePing Configuration from Database**:
   ```bash
   python scripts/config_generator.py
   ```
   Automatically reads from PostgreSQL database and generates SmokePing configuration.

2. **Migrate YAML to Database**:
   ```bash
   python scripts/migrate_yaml_to_db.py
   ```
   One-time migration from existing YAML configuration to PostgreSQL database.

3. **Fetch Netflix OCA Servers**:
   ```bash
   python scripts/oca_fetcher.py
   ```
   Updates database with latest Netflix OCA server information.

4. **Update Active Targets**:
   Use the web administration interface with database-first target management.

### YAML Fallback Mode

When database is unavailable, the system automatically falls back to YAML configuration:
- Configuration generator reads from YAML files
- Web interface operates in YAML-only mode
- Migration tools help transition back to database

## Integration

### Database-First Architecture
- **PostgreSQL Integration**: Direct database connectivity with SQLAlchemy models
- **Web Admin Interface**: Shared database for real-time target management
- **Grafana Integration**: Dashboard template variables read from same PostgreSQL database
- **Config Generation**: Real-time configuration generation from database state
- **Active/Inactive Control**: Only active targets are included in generated configurations

### Container Integration
The generated configuration files are used by SmokePing containers in both variants:
- **grafana-influx**: Full database integration with PostgreSQL container
- **minimal**: Optional database mode with YAML fallback

### Environment Variables
- `DATABASE_URL`: PostgreSQL connection string for database mode
- `CONFIG_DIR`: Directory for YAML fallback files
- `SMOKEPING_CONFIG_DIR`: Output directory for generated SmokePing configurations