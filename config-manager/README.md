# SmokePing Configuration Manager

<div align="center">
  <img src="../img/logo.jpg" alt="Smoking Pi Logo" width="100"/>
</div>

This module provides centralized configuration management for SmokePing targets and probes with YAML-based configuration and automated generation.

## Directory Structure

```
config-manager/
├── config/              # Configuration files
│   ├── sources.yaml    # All available target sources
│   ├── targets.yaml    # Currently active targets
│   └── probes.yaml     # Probe configurations
├── templates/          # Jinja2 templates
│   └── smokeping_targets.j2  # SmokePing Targets file template
└── scripts/            # Management scripts
    ├── config_generator.py   # Generate SmokePing configs
    └── oca_fetcher.py       # Fetch Netflix OCA servers
```

## Configuration Files

### sources.yaml
Defines all available sources for monitoring targets:
- **static**: Built-in targets (websites, DNS resolvers)
- **dynamic**: Auto-discovered targets (Netflix OCA, top sites)
- **custom**: User-defined targets

### targets.yaml
Contains the currently active targets selected from sources. This file is managed by the web UI and should not be edited manually.

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

### probes.yaml
Defines available SmokePing probe types and their configurations (FPing, DNS, etc.)

## Usage

1. **Generate SmokePing Configuration**:
   ```bash
   python scripts/config_generator.py
   ```

2. **Fetch Netflix OCA Servers**:
   ```bash
   python scripts/oca_fetcher.py
   ```

3. **Update Active Targets**:
   Use the web administration interface (when available)

## Integration

The generated configuration files are used by SmokePing containers in both the minimal and grafana-influx variants.