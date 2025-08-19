# Web Admin API Documentation

Complete reference for the Web Admin interface and API gateway - the frontend service that provides user interface and proxies requests to the config-manager backend.

## 📋 Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Web Interface Routes](#web-interface-routes)
- [API Gateway Endpoints](#api-gateway-endpoints)
- [Sources Management](#sources-management)
- [Targets Management](#targets-management)
- [Authentication & Sessions](#authentication--sessions)
- [Error Handling](#error-handling)
- [Examples](#examples)

## Overview

The Web Admin service provides both a user-friendly web interface and an API gateway that enhances the config-manager backend with additional functionality like site discovery, bandwidth estimation, and bulk operations.

### Key Features

- **Web UI**: Browser-based target management interface
- **API Gateway**: Proxy and enhancement layer for config-manager
- **Site Discovery**: Integration with Tranco, CrUX, and Cloudflare Radar
- **Bulk Operations**: Select and manage multiple targets at once
- **Session Management**: User sessions and preferences
- **Real-time Updates**: Live status updates and validation

## Architecture

### Service Stack

```text
┌─────────────────┐
│   Web Browser   │
└─────────────────┘
         │ HTTP
         ▼
┌─────────────────┐  Port 8080
│   Web Admin     │  ┌─ Web UI Routes
│                 │  ├─ API Gateway
│                 │  ├─ Session Management
│                 │  └─ External API Integration
└─────────────────┘
         │ HTTP
         ▼
┌─────────────────┐  Port 5000
│ Config Manager  │  ┌─ REST API
│                 │  ├─ Database Operations
│                 │  └─ SmokePing Management
└─────────────────┘
```

### Request Flow

1. **Web UI**: User interacts with browser interface
2. **API Gateway**: Web admin processes and validates requests
3. **Proxy**: Forwards configuration requests to config-manager
4. **Enhancement**: Adds additional functionality (site discovery, validation)
5. **Response**: Returns enriched data to user interface

## Web Interface Routes

### Dashboard

**Route**: `GET /`

The main dashboard providing system overview and quick access to management functions.

**Features**:
- System status overview
- Target count by category
- Quick action buttons
- Recent activity log

### Target Management

**Route**: `GET /targets/`

Target management interface with CRUD operations and bulk actions.

**Features**:
- Target list with filtering
- Add/edit/delete targets
- Bulk select and operations
- Active/inactive status toggle
- Real-time validation

### Site Discovery

**Route**: `GET /sources/`

Site discovery interface for finding popular websites and services.

**Features**:
- Top sites from multiple sources
- Country-specific filtering
- Bulk import capabilities
- Preview before adding

### Country Selection

**Route**: `GET /countries/`

Country selection interface for region-specific monitoring.

**Features**:
- Geographic target selection
- Country-specific top sites
- Regional performance monitoring

### Authentication

**Route**: `GET /auth/`

Authentication and user management interface.

**Features**:
- Session management
- User preferences
- Security settings

## API Gateway Endpoints

### Base URL

```
http://localhost:8080
```

All API gateway endpoints are prefixed with `/api/`.

### System Status

Get enhanced system status including web admin metrics.

**Endpoint**: `GET /api/status`

**Response**:
```json
{
  "config_manager": {
    "status": "healthy",
    "database": {
      "available": true,
      "targets_count": 25
    },
    "smokeping": {
      "running": true
    }
  },
  "web_admin": {
    "status": "healthy",
    "sessions_active": 3,
    "external_apis": {
      "tranco": "available",
      "cloudflare": "available",
      "crux": "available"
    }
  },
  "last_check": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Status retrieved successfully
- `500`: Status check failed

### Apply Configuration

Apply configuration changes with automatic generation and restart.

**Endpoint**: `POST /api/apply`

**Response**:
```json
{
  "success": true,
  "message": "Configuration applied successfully",
  "steps_completed": [
    "Configuration generated",
    "SmokePing restarted"
  ],
  "applied_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Configuration applied successfully
- `500`: Application failed

### Bandwidth Estimation

Get bandwidth usage estimates for current monitoring targets.

**Endpoint**: `GET /api/bandwidth`

**Response**:
```json
{
  "total_targets": 25,
  "active_targets": 20,
  "estimated_bandwidth": {
    "per_probe": "0.05 KB",
    "per_minute": "1 KB",
    "per_hour": "60 KB",
    "per_day": "1.44 MB"
  },
  "probe_interval": 300,
  "packets_per_probe": 20
}
```

**Status Codes**:
- `200`: Bandwidth estimate calculated successfully
- `500`: Calculation failed

### Hostname Validation

Validate hostname with IPv6 support and DNS resolution.

**Endpoint**: `POST /api/validate-hostname`

**Request Body**:
```json
{
  "hostname": "example.com"
}
```

**Response**:
```json
{
  "hostname": "example.com",
  "valid": true,
  "ipv4": "93.184.216.34",
  "ipv6": "2606:2800:220:1:248:1893:25c8:1946",
  "dns_resolution_time": 45,
  "supports_ipv6": true,
  "validation_details": {
    "format_valid": true,
    "dns_resolvable": true,
    "reachable": true
  }
}
```

**Status Codes**:
- `200`: Validation completed
- `400`: Invalid hostname format
- `500`: Validation failed

### SmokePing Logs

Retrieve SmokePing container logs.

**Endpoint**: `GET /api/smokeping/logs`

**Query Parameters**:
- `lines` (optional): Number of log lines to retrieve (default: 100)
- `follow` (optional): Stream logs in real-time

**Response**:
```json
{
  "logs": [
    "2024-01-01 12:00:00: SmokePing started successfully",
    "2024-01-01 12:00:30: Probing 20 targets",
    "2024-01-01 12:01:00: All probes completed successfully"
  ],
  "container_name": "grafana-influx-smokeping-1",
  "retrieved_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Logs retrieved successfully
- `500`: Log retrieval failed

### SmokePing Restart

Restart the SmokePing service via web admin.

**Endpoint**: `POST /api/smokeping/restart`

**Response**:
```json
{
  "success": true,
  "message": "SmokePing restarted successfully",
  "restart_method": "docker_compose",
  "restarted_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Restart successful
- `500`: Restart failed

### OCA Management

Refresh Netflix Open Connect Appliance data.

**Endpoint**: `POST /api/ocas/refresh`

**Response**:
```json
{
  "success": true,
  "message": "OCA data refreshed successfully",
  "ocas_found": 15,
  "refreshed_at": "2024-01-01T12:00:00"
}
```

**Endpoint**: `GET /api/ocas/status`

**Response**:
```json
{
  "total_ocas": 15,
  "active_ocas": 12,
  "last_refresh": "2024-01-01T11:00:00",
  "refresh_status": "completed"
}
```

**Status Codes**:
- `200`: Operation successful
- `500`: OCA operation failed

### Target Categories

Get HTML fragment for target category display.

**Endpoint**: `GET /api/targets/{category}`

**Parameters**:
- `category` (required): Category name (e.g., "websites", "netflix_oca")

**Response**:
```html
<div class="target-category">
  <h3>Websites</h3>
  <div class="targets">
    <div class="target-item">
      <span class="name">google</span>
      <span class="host">google.com</span>
      <span class="status active">Active</span>
    </div>
  </div>
</div>
```

**Status Codes**:
- `200`: HTML fragment returned
- `404`: Category not found
- `500`: Retrieval failed

### Delete Targets

Delete multiple targets from configuration.

**Endpoint**: `DELETE /api/targets/delete`

**Request Body**:
```json
{
  "targets": ["target1", "target2", "target3"]
}
```

**Response**:
```json
{
  "success": true,
  "message": "3 targets deleted successfully",
  "deleted_targets": ["target1", "target2", "target3"],
  "deleted_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Targets deleted successfully
- `400`: Invalid request data
- `500`: Deletion failed

## Sources Management

### Base Path: `/sources/`

Site discovery and top sites management.

### Fetch Top Sites

Fetch top sites from external sources.

**Endpoint**: `GET /sources/api/fetch/{source}`

**Parameters**:
- `source` (required): One of `tranco`, `crux`, `cloudflare`

**Query Parameters**:
- `limit` (optional): Number of sites to fetch (default: 100)
- `country` (optional): Country code for regional sites

**Examples**:
- `GET /sources/api/fetch/tranco?limit=50`
- `GET /sources/api/fetch/crux?country=US&limit=25`

**Response**:
```json
{
  "source": "tranco",
  "sites": [
    {
      "rank": 1,
      "domain": "google.com",
      "title": "Google Search"
    },
    {
      "rank": 2,
      "domain": "youtube.com",
      "title": "YouTube"
    }
  ],
  "total_fetched": 50,
  "fetched_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Sites fetched successfully
- `400`: Invalid source or parameters
- `429`: Rate limit exceeded
- `500`: Fetch failed

### Update Targets

Update targets with selected sites from discovery.

**Endpoint**: `POST /sources/api/update`

**Request Body**:
```json
{
  "selected_sites": [
    {
      "domain": "example.com",
      "title": "Example Site",
      "category": "websites"
    }
  ],
  "replace_existing": false
}
```

**Response**:
```json
{
  "success": true,
  "message": "Targets updated successfully",
  "added_targets": 5,
  "skipped_targets": 2,
  "updated_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `200`: Targets updated successfully
- `400`: Invalid request data
- `500`: Update failed

## Targets Management

### Base Path: `/targets/`

Direct target management interface.

### Add Target

Add a new monitoring target.

**Endpoint**: `GET /targets/add` (form) or `POST /targets/add` (submit)

**Request Body** (POST):
```json
{
  "name": "example",
  "host": "example.com",
  "title": "Example Website",
  "category": "websites",
  "probe": "FPing"
}
```

**Response**:
```json
{
  "success": true,
  "message": "Target added successfully",
  "target": {
    "name": "example",
    "host": "example.com",
    "title": "Example Website"
  }
}
```

### Delete Target

Delete a specific target.

**Endpoint**: `POST /targets/delete/{name}`

**Parameters**:
- `name` (required): Target name to delete

**Response**:
```json
{
  "success": true,
  "message": "Target deleted successfully",
  "deleted_target": "example"
}
```

### Get All Targets (API)

Get all targets via API with database awareness.

**Endpoint**: `GET /targets/api/targets`

**Query Parameters**:
- `active_only` (optional): Filter to active targets only
- `category` (optional): Filter by category

**Response**:
```json
{
  "targets": [
    {
      "id": 1,
      "name": "google",
      "host": "google.com",
      "title": "Google Search",
      "is_active": true,
      "category": "websites"
    }
  ],
  "mode": "database",
  "total_count": 25
}
```

### Toggle Target Status

Toggle target active/inactive status (database mode only).

**Endpoint**: `POST /targets/api/targets/{id}/toggle`

**Parameters**:
- `id` (required): Target ID to toggle

**Response**:
```json
{
  "success": true,
  "target_id": 1,
  "new_status": "inactive",
  "message": "Target deactivated successfully"
}
```

### Management Status

Get target management status and capabilities.

**Endpoint**: `GET /targets/api/status`

**Response**:
```json
{
  "mode": "database",
  "capabilities": {
    "individual_toggle": true,
    "bulk_operations": true,
    "real_time_updates": true
  },
  "target_counts": {
    "total": 25,
    "active": 20,
    "inactive": 5
  }
}
```

## Authentication & Sessions

### Session Management

The web admin uses Flask sessions for state management:

```python
# Session data structure
{
  "user_preferences": {
    "theme": "light",
    "default_category": "websites",
    "auto_refresh": true
  },
  "csrf_token": "generated_token",
  "last_activity": "2024-01-01T12:00:00"
}
```

### Security Features

- **CSRF Protection**: All forms include CSRF tokens
- **Session Timeout**: Automatic session expiration
- **Input Validation**: Server-side validation for all inputs
- **XSS Protection**: Output sanitization

### Future Authentication

Planned authentication features include:

- **API Token Authentication**: Token-based API access
- **User Management**: Multi-user support
- **Role-Based Access**: Different permission levels
- **OAuth Integration**: External authentication providers

## Error Handling

### Web Interface Errors

Web interface errors are displayed as flash messages:

```python
flash('Error message', 'error')
flash('Success message', 'success')
flash('Warning message', 'warning')
flash('Info message', 'info')
```

### API Error Responses

API errors follow the same format as config-manager:

```json
{
  "error": "Descriptive error message",
  "timestamp": "2024-01-01T12:00:00",
  "source": "web-admin"
}
```

### Fallback Behavior

When config-manager is unavailable:

- **Direct File Access**: Falls back to YAML file manipulation
- **Limited Functionality**: Some features disabled
- **User Notification**: Clear indication of reduced functionality

## Examples

### Web Interface Usage

```bash
# Access main dashboard
open http://localhost:8080/

# Manage targets
open http://localhost:8080/targets/

# Discover top sites
open http://localhost:8080/sources/
```

### API Gateway Usage

```bash
# Get enhanced status
curl http://localhost:8080/api/status

# Apply configuration changes
curl -X POST http://localhost:8080/api/apply

# Validate hostname
curl -X POST http://localhost:8080/api/validate-hostname \
  -H "Content-Type: application/json" \
  -d '{"hostname": "example.com"}'

# Get bandwidth estimate
curl http://localhost:8080/api/bandwidth

# Fetch top sites
curl "http://localhost:8080/sources/api/fetch/tranco?limit=10"
```

### Bulk Operations

```bash
# Refresh OCA data
curl -X POST http://localhost:8080/api/ocas/refresh

# Delete multiple targets
curl -X DELETE http://localhost:8080/api/targets/delete \
  -H "Content-Type: application/json" \
  -d '{"targets": ["target1", "target2"]}'

# Update with discovered sites
curl -X POST http://localhost:8080/sources/api/update \
  -H "Content-Type: application/json" \
  -d '{
    "selected_sites": [
      {"domain": "example.com", "title": "Example", "category": "websites"}
    ]
  }'
```

### Integration Examples

```bash
# Complete workflow: discover and add sites
curl "http://localhost:8080/sources/api/fetch/tranco?limit=5" | \
  jq '.sites | map({domain: .domain, title: .title, category: "websites"})' | \
  curl -X POST http://localhost:8080/sources/api/update \
    -H "Content-Type: application/json" \
    -d @-

# Apply changes
curl -X POST http://localhost:8080/api/apply
```

## Integration with Config Manager

The web admin seamlessly integrates with the config-manager backend:

### Request Proxying

```python
# Example proxy implementation
response = requests.get(f"{CONFIG_MANAGER_URL}/targets")
return jsonify(response.json())
```

### Error Handling

```python
try:
    response = requests.get(f"{CONFIG_MANAGER_URL}/status")
    return response.json()
except requests.RequestException:
    # Fallback to direct file access
    return fallback_status()
```

### Enhancement Layer

The web admin adds value through:

- **User Interface**: Intuitive web interface
- **Site Discovery**: External API integration
- **Validation**: Real-time hostname validation
- **Bulk Operations**: Multi-target operations
- **Monitoring**: Enhanced status reporting

For more implementation details, see the [Config Manager API documentation](config-manager.md).