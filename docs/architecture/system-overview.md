# System Architecture Overview

Comprehensive overview of the SmokePing system architecture, including service interactions, data flow, and deployment patterns.

## 📋 Table of Contents

- [Architecture Summary](#architecture-summary)
- [Service Components](#service-components)
- [Data Flow Architecture](#data-flow-architecture)
- [Network Architecture](#network-architecture)
- [Database Architecture](#database-architecture)
- [Security Architecture](#security-architecture)
- [Deployment Patterns](#deployment-patterns)
- [Scalability Considerations](#scalability-considerations)

## Architecture Summary

The SmokePing system implements a modern, containerized microservices architecture designed for comprehensive network monitoring with professional-grade dashboards and database-driven configuration management.

### Core Design Principles

- **Microservices Architecture**: Independent, loosely-coupled services
- **Database-First Design**: PostgreSQL as the primary configuration store
- **Container-Native**: Docker-first deployment with orchestration support
- **API-Driven**: RESTful APIs for all service interactions
- **Zero-Touch Deployment**: Automated setup with secure credential generation
- **Fallback Resilience**: Graceful degradation when services are unavailable

### High-Level Architecture

```text
🔐 Init-Passwords (setup) → Generates secure credentials
                    ↓
┌─────────────┐  Config YAML   ┌──────────────┐
│Config Manager│────────────────▶│  SmokePing   │─┐
└─────────────┘                │   (8081)     │ │
       ▲                       └──────────────┘ │
       │ Database-first               │         │ RRD files
       │                              │         │
┌─────────────┐   ┌──────────────┐    │         ▼
│ Web Admin   │──▶│ PostgreSQL   │    │ ┌──────────────┐  Flux queries  ┌─────────────┐
│   (8080)    │   │   (5432)     │    │ │ InfluxDB 2.x │──────────────▶│  Grafana    │
└─────────────┘   └──────────────┘    │ │   (8086)     │                │  (3000)     │
                         ▲             │ └──────────────┘                └─────────────┘
                         │             │       ▲                                ▲
                         └─────────────┘       │ exporter.py               │ PostgreSQL
                                               └───────────────────────────┘ Template Vars
```

## Service Components

### 1. Init-Passwords Container

**Purpose**: Secure credential generation and environment setup

**Lifecycle**: Run-once initialization container

**Key Functions**:
- Generate cryptographically secure passwords and tokens
- Auto-detect system timezone
- Create environment configuration file
- Initialize shared secrets for all services

**Generated Credentials**:
- `INFLUX_TOKEN` - InfluxDB API authentication
- `INFLUX_PASSWORD` - InfluxDB admin password
- `POSTGRES_PASSWORD` - PostgreSQL database password
- `SECRET_KEY` - Flask web application secret
- `API_TOKEN` - API authentication token (planned)

**Dependencies**: None (runs first)

### 2. PostgreSQL Database

**Purpose**: Centralized configuration and metadata storage

**Port**: 5432 (internal), 127.0.0.1:5432 (host)

**Key Features**:
- Normalized schema for targets, categories, and probes
- Active/inactive status management
- Rich metadata storage
- ACID compliance for configuration changes
- Migration tracking and fallback support

**Data Storage**:
- Target configurations with relationships
- Category definitions and descriptions
- Probe configurations and parameters
- System metadata and migration status

**Dependencies**: init-passwords

### 3. Config Manager

**Purpose**: Configuration management backend service

**Port**: 5000 (internal), 127.0.0.1:5000 (host)

**Key Features**:
- REST API for configuration management
- Database-first with YAML fallback
- Automatic SmokePing configuration generation
- Service restart management
- Health monitoring and status reporting

**API Categories**:
- Configuration CRUD operations
- Target management (database mode)
- Service operations (generate, restart)
- Health and status endpoints

**Dependencies**: init-passwords, postgres

### 4. Web Admin

**Purpose**: User interface and API gateway

**Port**: 8080, 0.0.0.0:8080 (accessible from host)

**Key Features**:
- Browser-based management interface
- API gateway with request enhancement
- Site discovery from external sources
- Bulk operations and target validation
- Session management and CSRF protection

**Components**:
- Web UI for target management
- Sources interface for site discovery
- API proxy to config-manager
- External API integrations

**Dependencies**: init-passwords, smokeping, config-manager, postgres

### 5. SmokePing

**Purpose**: Network latency monitoring and data collection

**Network**: Host mode for comprehensive network access

**Key Features**:
- ICMP ping monitoring with fping/fping6
- DNS resolution time monitoring
- RRD data storage for immediate access
- Real-time data export to InfluxDB
- Multi-probe support (FPing, EchoPing)

**Data Collection**:
- Latency measurements every 5 minutes
- Packet loss percentages
- DNS resolution times
- Historical RRD data

**Dependencies**: init-passwords, influxdb

### 6. InfluxDB

**Purpose**: Time-series database for historical analysis

**Port**: 8086

**Key Features**:
- Modern time-series database
- Flux query language support
- Automated data retention policies
- High-performance data ingestion
- API-driven data access

**Data Structure**:
- `latency` measurement for ping data
- `dns_latency` measurement for DNS data
- Rich tagging for data classification
- Millisecond precision timestamps

**Dependencies**: init-passwords

### 7. Grafana

**Purpose**: Professional dashboards and data visualization

**Port**: 3000

**Key Features**:
- Professional dashboard suite
- PostgreSQL template variables
- Real-time data visualization
- Percentile analysis and statistics
- Auto-provisioned datasources and dashboards

**Dashboard Categories**:
- Side-by-side ping comparisons
- Individual target analysis
- DNS resolution time monitoring
- System overview and health

**Dependencies**: init-passwords, influxdb

## Data Flow Architecture

### Configuration Data Flow

```text
1. User Input → Web Admin Interface
2. Web Admin → Config Manager API
3. Config Manager → PostgreSQL Database
4. Config Manager → SmokePing Configuration Files
5. SmokePing → Configuration Reload
```

**Detailed Flow**:
1. **User Input**: User adds/modifies targets via web interface
2. **API Gateway**: Web admin validates and forwards to config-manager
3. **Database Update**: Config manager updates PostgreSQL with changes
4. **Config Generation**: Automatic SmokePing configuration generation
5. **Service Restart**: SmokePing service reloaded with new configuration

### Monitoring Data Flow

```text
1. SmokePing → Network Probes → RRD Files
2. RRD Exporter → Parse RRD Data → InfluxDB
3. Grafana → Query InfluxDB → Dashboard Visualization
4. PostgreSQL → Template Variables → Grafana Dashboards
```

**Detailed Flow**:
1. **Network Probing**: SmokePing performs network measurements
2. **RRD Storage**: Immediate storage in Round Robin Database files
3. **Data Export**: Python exporter streams data to InfluxDB
4. **Data Classification**: Automatic measurement categorization
5. **Visualization**: Grafana queries InfluxDB for dashboard data
6. **Dynamic Filtering**: PostgreSQL provides template variables

### Monitoring Metrics Flow

```text
┌─────────────┐    5min intervals    ┌─────────────┐
│  SmokePing  │ ──────────────────▶ │ RRD Files   │
│             │                     │             │
└─────────────┘                     └─────────────┘
       │                                   │
       │ Network Probes                    │ 60sec export
       ▼                                   ▼
┌─────────────┐                     ┌─────────────┐
│   Targets   │                     │ RRD Exporter│
│  (Network)  │                     │ (Python)    │
└─────────────┘                     └─────────────┘
                                           │
                                           │ HTTP POST
                                           ▼
                                    ┌─────────────┐
                                    │  InfluxDB   │
                                    │             │
                                    └─────────────┘
                                           │
                                           │ Flux Queries
                                           ▼
                                    ┌─────────────┐
                                    │   Grafana   │
                                    │ Dashboards  │
                                    └─────────────┘
```

## Network Architecture

### Container Networking

**Default Bridge Network**:
- All containers communicate via Docker's default bridge
- Service discovery via container names
- Internal DNS resolution
- No external network exposure except designated ports

**Port Mappings**:
```yaml
# External Access
8080:8080   # Web Admin (public interface)
3000:3000   # Grafana (dashboards)
8086:8086   # InfluxDB (API access)

# Internal Access Only
127.0.0.1:5432:5432   # PostgreSQL (localhost only)
127.0.0.1:5000:5000   # Config Manager (localhost only)

# Host Network
host        # SmokePing (network monitoring access)
```

### Service Discovery

**Internal Communication**:
```text
web-admin → config-manager:5000
config-manager → postgres:5432
smokeping → influxdb:8086
grafana → influxdb:8086
grafana → postgres:5432
```

**External Access Points**:
- Web Admin: `http://localhost:8080` (primary interface)
- Grafana: `http://localhost:3000` (dashboards)
- SmokePing: `http://localhost:8081` (classic interface)
- InfluxDB: `http://localhost:8086` (API access)

### Network Security

**Security Layers**:
1. **Container Isolation**: Each service runs in isolated container
2. **Network Segmentation**: Internal Docker network separation
3. **Port Restrictions**: Limited external port exposure
4. **Host-Only Binding**: Sensitive services bound to localhost only

## Database Architecture

### PostgreSQL Schema

**Core Tables**:
```sql
-- Target configurations
targets (id, name, host, title, is_active, category_id, probe_id, 
         site, cachegroup, created_at, updated_at)

-- Category definitions  
categories (id, name, display_name, description, created_at)

-- Probe configurations
probes (id, name, binary_path, step_seconds, pings, forks, 
        is_default, created_at)

-- System metadata
system_metadata (key, value, created_at, updated_at)
```

**Relationships**:
- targets → categories (many-to-one)
- targets → probes (many-to-one)
- Foreign key constraints ensure referential integrity

### InfluxDB Schema

**Measurements**:
```text
latency
├── tags: target, probe_type
├── fields: median, loss, ping_1..20
└── timestamp

dns_latency  
├── tags: target, resolver
├── fields: resolution_time
└── timestamp
```

**Data Retention**:
- Default: Infinite retention
- Configurable: Automatic downsampling
- Performance: Optimized for time-series queries

### Data Relationships

```text
PostgreSQL (Configuration)
├── targets (active monitoring config)
├── categories (target organization)
└── probes (monitoring methods)

InfluxDB (Time-Series Data)
├── latency (ping measurements)
└── dns_latency (DNS measurements)

RRD Files (Immediate Access)
├── /var/lib/smokeping/*.rrd
└── Traditional SmokePing storage
```

## Security Architecture

### Authentication Layers

**Current State**:
- No authentication (internal network trust model)
- Session-based web UI state management
- Input validation and CSRF protection

**Planned Implementation**:
- API token authentication for all endpoints
- Token generation during initialization
- Secure token storage and rotation

### Network Security

**Container Security**:
- Non-root user execution where possible
- Minimal container images with security patches
- No unnecessary packages or services

**Network Security**:
- Internal Docker network isolation
- Localhost-only binding for sensitive services
- No direct internet exposure of management interfaces

### Data Security

**Database Security**:
- Encrypted password storage
- Connection encryption support
- Regular backup capabilities

**Configuration Security**:
- Secure credential generation
- Environment-based secret management
- No hardcoded passwords or tokens

## Deployment Patterns

### Standard Deployment

**Single-Host Deployment**:
```bash
cd grafana-influx/
./init-passwords-docker.sh
docker-compose up -d
```

**Characteristics**:
- All services on single Docker host
- Shared Docker volumes for persistence
- Internal network communication
- Suitable for small to medium deployments

### High-Availability Deployment

**Multi-Host with External Database**:
```yaml
# Production docker-compose.yml example
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_URL: postgresql://user:pass@external-db:5432/db
    deploy:
      replicas: 0  # Use external PostgreSQL cluster
  
  config-manager:
    deploy:
      replicas: 2
      placement:
        constraints: [node.role == worker]
  
  web-admin:
    deploy:
      replicas: 3
      placement:
        constraints: [node.role == worker]
```

**Characteristics**:
- External PostgreSQL cluster
- Load-balanced web interfaces
- Shared storage for RRD files
- Container orchestration (Docker Swarm/Kubernetes)

### Edge Deployment

**Distributed Monitoring**:
```text
Edge Site 1     Edge Site 2     Central Site
┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ SmokePing   │ │ SmokePing   │ │ Grafana     │
│ InfluxDB    │ │ InfluxDB    │ │ InfluxDB    │
└─────────────┘ └─────────────┘ │ PostgreSQL  │
       │               │        └─────────────┘
       └───────────────┼──────────────┘
                       │
               Data Aggregation
```

**Characteristics**:
- Multiple monitoring locations
- Local data collection and storage
- Central data aggregation and visualization
- Network-resilient operation

## Scalability Considerations

### Horizontal Scaling

**Stateless Services** (Easily Scalable):
- Web Admin (multiple instances behind load balancer)
- Config Manager (with database connection pooling)
- Grafana (with shared storage)

**Stateful Services** (Scaling Considerations):
- PostgreSQL (requires clustering or read replicas)
- InfluxDB (clustering available in Enterprise)
- SmokePing (single instance per monitoring location)

### Performance Optimization

**Database Performance**:
- Connection pooling
- Query optimization with indexes
- Regular maintenance and vacuuming
- Read replicas for reporting queries

**Time-Series Performance**:
- InfluxDB retention policies
- Data downsampling for long-term storage
- Query optimization
- Monitoring resource usage

### Resource Planning

**Minimum Requirements**:
- 2 CPU cores
- 4 GB RAM
- 20 GB storage
- 1 Gbps network

**Recommended Requirements**:
- 4+ CPU cores
- 8+ GB RAM
- 100+ GB SSD storage
- 10 Gbps network

**Scaling Indicators**:
- CPU usage > 70% sustained
- Memory usage > 80%
- Database query response time > 100ms
- InfluxDB write queue backup

### Monitoring and Alerting

**System Metrics**:
- Container resource usage
- Database performance metrics
- Network connectivity status
- Disk space utilization

**Application Metrics**:
- API response times
- Database query performance
- Target monitoring success rates
- Dashboard load times

**Alerting Thresholds**:
- Service unavailability
- High error rates
- Resource exhaustion
- Configuration inconsistencies

For implementation details and configuration examples, see the [API documentation](../api/) and [examples](../examples/).