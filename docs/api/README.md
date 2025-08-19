# SmokePing System API Documentation

Complete API reference for the SmokePing network monitoring system with database-first architecture, web administration, and professional monitoring capabilities.

## 📋 Table of Contents

- [System Overview](#system-overview)
- [API Architecture](#api-architecture)
- [Quick Start](#quick-start)
- [Service Discovery](#service-discovery)
- [Authentication](#authentication)
- [API References](#api-references)
- [Examples & Guides](#examples--guides)

## System Overview

The SmokePing system provides comprehensive network monitoring through a modular, containerized architecture with REST APIs for configuration management, target administration, and data visualization.

### Key Components

| Service | Port | Purpose | API Type |
|---------|------|---------|----------|
| **Config Manager** | 5000 | Configuration management backend | REST API |
| **Web Admin** | 8080 | User interface and API gateway | Web UI + REST |
| **SmokePing** | 8081 | Network monitoring service | Web Interface |
| **Grafana** | 3000 | Data visualization dashboards | Web Interface |
| **InfluxDB** | 8086 | Time-series database | HTTP API |
| **PostgreSQL** | 5432 | Configuration database | Database |

### Architecture Diagram

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

## API Architecture

### Two-Tier Design

1. **Config Manager (Backend)**: Core REST API for configuration management
   - Database-first with YAML fallback
   - Target CRUD operations
   - Configuration generation and deployment
   - Service health monitoring

2. **Web Admin (Frontend + Gateway)**: User interface with API proxy
   - Web UI for target management
   - API gateway that forwards to config-manager
   - Site discovery and bulk operations
   - Authentication and session management

### Operating Modes

#### Database Mode (PostgreSQL Available)
- Individual target CRUD operations
- Active/inactive status management
- Rich metadata and relationships
- Query filtering and categorization
- Automatic configuration regeneration

#### YAML Fallback Mode
- File-based configuration management
- Bulk configuration updates
- Backup and versioning
- Direct file manipulation

## Quick Start

### 1. Service Deployment

```bash
cd grafana-influx/
./init-passwords-docker.sh   # Generate secure credentials including API tokens
docker-compose up -d         # Deploy full stack
```

### 2. Verify API Health

```bash
# Check config manager
curl http://localhost:5000/health

# Check web admin
curl http://localhost:8080/api/status

# Check interactive documentation
open http://localhost:5000/docs
```

### 3. Basic API Usage

```bash
# Get all targets
curl http://localhost:5000/targets

# Get system status
curl http://localhost:5000/status

# Get configuration
curl http://localhost:5000/config
```

## Service Discovery

### Health Endpoints

All services provide health check endpoints:

- **Config Manager**: `GET /health`
- **Web Admin**: `GET /api/status`
- **InfluxDB**: `GET /health`
- **SmokePing**: Available via web interface

### Service Status

Get comprehensive system status:

```bash
curl http://localhost:5000/status
```

Returns:
- Database connection status
- Configuration file status
- SmokePing service status
- Target counts and metadata

## Authentication

> **Current State**: No authentication implemented
> 
> **Future Enhancement**: API token authentication planned (see [Authentication Guide](authentication.md))

**Security Considerations**:
- APIs designed for internal Docker network use
- Input validation on all endpoints
- No authentication middleware currently
- Rate limiting on external API calls

**Planned Features**:
- API token generation in init-passwords container
- Token-based authentication for all methods
- Password script integration for token retrieval

## API References

### Core APIs

- **[Config Manager API](config-manager.md)** - Backend REST API reference
- **[Database API](database.md)** - PostgreSQL mode endpoints
- **[Web Admin API](web-admin.md)** - Frontend and gateway documentation

### Integration APIs

- **[External Integrations](external-integrations.md)** - Third-party API integrations
- **[Authentication](authentication.md)** - Security and authentication guide

### Architecture Documentation

- **[System Overview](../architecture/system-overview.md)** - Detailed architecture
- **[Data Flow](../architecture/data-flow.md)** - System data flow

## Examples & Guides

### Practical Examples

- **[cURL Examples](../examples/curl-examples.md)** - Command-line usage
- **[Python Client](../examples/python-client.py)** - Programming integration
- **[Postman Collection](../examples/postman-collection.json)** - API testing

### Common Workflows

#### Target Management

```bash
# Add new target
curl -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "google",
    "host": "google.com",
    "title": "Google Search",
    "category_id": 1,
    "probe_id": 1
  }'

# Toggle target status
curl -X POST http://localhost:5000/targets/1/toggle
```

#### Configuration Management

```bash
# Generate SmokePing config
curl -X POST http://localhost:5000/generate

# Restart SmokePing
curl -X POST http://localhost:5000/restart

# Apply changes via web admin
curl -X POST http://localhost:8080/api/apply
```

#### Monitoring and Status

```bash
# Get target categories
curl http://localhost:5000/categories

# Get probe configurations
curl http://localhost:5000/probes

# Check bandwidth estimates
curl http://localhost:8080/api/bandwidth
```

## Interactive Documentation

### Built-in API Explorer

Access the interactive Swagger UI documentation:

- **Config Manager**: http://localhost:5000/docs
- **OpenAPI Spec**: http://localhost:5000/api/docs

### Features

- Complete endpoint documentation
- Request/response schemas
- Interactive testing interface
- Real-time API exploration

## Error Handling

### Standard HTTP Status Codes

- **200**: Success
- **201**: Created (for POST operations)
- **400**: Bad Request (validation errors)
- **404**: Not Found
- **405**: Method Not Allowed
- **500**: Internal Server Error

### Error Response Format

```json
{
  "error": "Descriptive error message",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

## Rate Limiting

- **Internal APIs**: No rate limiting (trusted Docker network)
- **External APIs**: Rate limited (Cloudflare, Tranco, CrUX)
- **Error Handling**: HTTP 429 responses with retry-after headers

## Performance Considerations

- **Caching**: Configuration data cached to minimize I/O
- **Connection Pooling**: HTTP clients reuse connections
- **Async Operations**: Long-running operations handled asynchronously
- **Timeouts**: Appropriate timeouts to prevent hanging requests

## Development & Debugging

### Adding New Endpoints

1. **Backend**: Add to `config-manager/api.py`
2. **Frontend**: Add proxy methods to `web-admin/app/services/config_api.py`
3. **Documentation**: Update this documentation and OpenAPI spec

### Testing & Debugging

```bash
# Direct API testing
curl -v http://localhost:5000/status

# Through web admin gateway
curl -v http://localhost:8080/api/status

# Monitor logs
docker-compose logs -f config-manager web-admin

# Container connectivity
docker exec -it grafana-influx-web-admin-1 curl http://config-manager:5000/health
```

## Support

### Documentation

- Complete API reference in this directory
- Interactive Swagger documentation
- Architecture guides and examples

### Troubleshooting

- Check service health endpoints
- Review Docker logs
- Verify database connectivity
- Test API endpoints individually

For detailed implementation guides, see the specific API documentation files in this directory.