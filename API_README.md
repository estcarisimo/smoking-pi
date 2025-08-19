# SmokePing Config Manager REST API

This document describes the new REST API architecture for the SmokePing Config Manager system.

## Architecture Overview

The system has been refactored to use a REST API architecture with two main components:

| Component          | Role                                                                                                                                                |
|--------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| **config-manager** | *Backend service*  <br>• Expose a REST API  <br>• Read & write SmokePing config files  <br>• Restart SmokePing whenever configs change              |
| **web-admin**      | *Frontend + proxy*  <br>• Provide the user-facing UI **and** its own REST endpoints  <br>• Forward all config-related requests to **config-manager**<br>• Display current target status fetched from **config-manager** |

## Setup Instructions

### 1. Start the Services

The system uses Docker Compose to orchestrate all services:

```bash
cd /home/smokingpi/smoking-pi/grafana-influx
docker-compose up -d
```

This will start:
- **config-manager** on port 5000 (REST API)
- **web-admin** on port 8080 (Web UI + API Gateway)
- **smokeping** on port 8081 (SmokePing web interface)
- **grafana** on port 3000 (Dashboards)
- **influxdb** on port 8086 (Database)

### 2. Verify Services

Check that the config-manager API is running:

```bash
curl http://localhost:5000/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "config-manager",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

Check the web-admin interface:
```bash
curl http://localhost:8080/api/status
```

### 3. Access the Interfaces

- **Web Admin UI**: http://localhost:8080
- **Config Manager API**: http://localhost:5000
- **API Documentation**: http://localhost:5000/docs
- **SmokePing Interface**: http://localhost:8081/cgi-bin/smokeping.cgi
- **Grafana Dashboards**: http://localhost:3000

## API Endpoints

### Config Manager API (Port 5000)

#### Health & Status
- `GET /health` - Health check
- `GET /status` - Comprehensive service status

#### Configuration Management
- `GET /config` - Get all configurations
- `GET /config/{type}` - Get specific configuration (targets, probes, sources)
- `PUT /config/{type}` - Update specific configuration

#### Operations
- `POST /generate` - Generate SmokePing configuration files
- `POST /restart` - Restart SmokePing service
- `POST /oca/refresh` - Refresh Netflix OCA servers

### Web Admin API Gateway (Port 8080)

The web-admin acts as an API gateway, proxying requests to config-manager while providing additional web UI functionality:

- `GET /api/status` - System status (proxied + enhanced)
- `POST /api/apply` - Apply configuration changes
- `POST /api/ocas/refresh` - Refresh OCA data
- `GET /api/bandwidth` - Bandwidth usage estimates

## Usage Examples

### 1. Get Current Configuration

```bash
# Get all configurations
curl http://localhost:5000/config

# Get only targets configuration
curl http://localhost:5000/config/targets
```

### 2. Update Configuration

```bash
# Update targets configuration
curl -X PUT http://localhost:5000/config/targets \
  -H "Content-Type: application/json" \
  -d @new_targets.json
```

### 3. Generate and Deploy Configuration

```bash
# Generate SmokePing configuration files
curl -X POST http://localhost:5000/generate

# Restart SmokePing service
curl -X POST http://localhost:5000/restart
```

### 4. Using the Web Admin Gateway

```bash
# Apply configuration changes (generates + restarts)
curl -X POST http://localhost:8080/api/apply

# Get system status with additional info
curl http://localhost:8080/api/status
```

## Configuration Files

The system manages three main configuration files:

### targets.yaml
Contains SmokePing monitoring targets organized by category:
```yaml
active_targets:
  websites:
    - name: google
      host: google.com
      title: Google Search
      probe: FPing
  netflix_oca:
    - name: netflix_oca_1
      host: 192.168.1.100
      title: Netflix OCA Server
      probe: FPing
metadata:
  last_updated: "2024-01-01T12:00:00"
  total_targets: 2
```

### probes.yaml
Defines SmokePing probe configurations:
```yaml
probes:
  FPing:
    binary: /usr/bin/fping
    packetsize: 56
    step: 300
  EchoPing:
    binary: /usr/bin/echoping
    timeout: 20
```

### sources.yaml
Configuration for top site sources (Tranco, CrUX, Cloudflare):
```yaml
dynamic:
  tranco:
    enabled: true
    default_limit: 100
  crux:
    enabled: true
    countries: ["us", "gb", "de"]
```

## Error Handling

The API provides comprehensive error handling:

- **400 Bad Request**: Invalid configuration data or malformed requests
- **404 Not Found**: Endpoint or resource not found
- **405 Method Not Allowed**: HTTP method not supported
- **500 Internal Server Error**: Server-side errors with detailed messages

All errors include descriptive messages and timestamps for debugging.

## Logging

Both services provide detailed logging:

- **config-manager**: Logs all API requests, configuration changes, and operations
- **web-admin**: Logs proxy requests, UI interactions, and fallback operations

Logs include:
- HTTP method, URL, and response status
- Request duration
- Error details and stack traces
- Configuration change events

## Fallback Mechanisms

The web-admin includes fallback mechanisms for resilience:

1. **Config API Unavailable**: Falls back to direct file access
2. **Service Restart Failure**: Attempts multiple restart methods
3. **Network Issues**: Retries with exponential backoff

## Development

### Adding New Endpoints

1. **Config Manager**: Add endpoints to `/home/smokingpi/smoking-pi/config-manager/api.py`
2. **Web Admin**: Add proxy methods to `/home/smokingpi/smoking-pi/web-admin/app/services/config_api.py`
3. **Update Documentation**: Modify this README and the OpenAPI spec

### Testing

```bash
# Test config-manager directly
curl -v http://localhost:5000/status

# Test through web-admin gateway
curl -v http://localhost:8080/api/status

# Check logs
docker-compose logs config-manager
docker-compose logs web-admin
```

### Debugging

1. **Check service health**: `curl http://localhost:5000/health`
2. **Verify configurations**: `curl http://localhost:5000/config`
3. **Monitor logs**: `docker-compose logs -f config-manager web-admin`
4. **Test connectivity**: `docker exec -it grafana-influx-web-admin-1 curl http://config-manager:5000/health`

## Migration from Direct File Access

The refactoring maintains compatibility while moving to REST API:

1. **Web UI**: No changes required - uses same endpoints
2. **External Scripts**: Update to use REST API endpoints
3. **Configuration**: Same YAML structure maintained
4. **Docker Compose**: Updated to expose config-manager port

## Security Considerations

- **Internal Network**: API only accessible within Docker network
- **No Authentication**: Currently designed for internal use only
- **Input Validation**: All configuration updates validated before saving
- **Error Sanitization**: Stack traces not exposed in production responses

## Performance

- **Caching**: Configuration data cached to minimize file I/O
- **Async Operations**: Long-running operations (restart, generate) handled asynchronously
- **Connection Pooling**: HTTP client reuses connections for better performance
- **Timeouts**: All requests have appropriate timeouts to prevent hanging

## Troubleshooting

### Common Issues

1. **Config Manager Not Responding**
   ```bash
   docker-compose restart config-manager
   curl http://localhost:5000/health
   ```

2. **Web Admin Can't Connect to Config Manager**
   ```bash
   docker exec -it grafana-influx-web-admin-1 curl http://config-manager:5000/health
   ```

3. **Configuration Changes Not Applied**
   ```bash
   curl -X POST http://localhost:5000/generate
   curl -X POST http://localhost:5000/restart
   ```

4. **Permission Issues**
   ```bash
   docker-compose logs config-manager | grep -i permission
   ```

### Log Locations

- **Config Manager**: `docker-compose logs config-manager`
- **Web Admin**: `docker-compose logs web-admin`
- **SmokePing**: `docker-compose logs smokeping`

For more detailed information, see the OpenAPI documentation at http://localhost:5000/docs