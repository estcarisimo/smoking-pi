# Config Manager REST API Reference

Complete reference for the Config Manager REST API - the core backend service for SmokePing configuration management.

## 📋 Table of Contents

- [Overview](#overview)
- [Base URL](#base-url)
- [Health & Status](#health--status)
- [Configuration Management](#configuration-management)
- [Database Operations](#database-operations)
- [Service Operations](#service-operations)
- [Error Handling](#error-handling)
- [Examples](#examples)

## Overview

The Config Manager provides a REST API for managing SmokePing configuration with support for both database-driven and YAML-based configurations. It automatically detects PostgreSQL availability and switches between database and YAML modes.

### Features

- **Database-First Architecture**: PostgreSQL-backed target management
- **YAML Fallback**: Automatic fallback to file-based configuration
- **Atomic Operations**: Configuration changes with automatic regeneration
- **Service Integration**: Direct SmokePing service management
- **Health Monitoring**: Comprehensive status reporting

## Base URL

```
http://localhost:5000
```

**Docker Internal**: `http://config-manager:5000`

## Health & Status

### Health Check

Check if the service is running and responsive.

**Endpoint**: `GET /health`

**Response**:
```json
{
  "status": "healthy",
  "service": "config-manager",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

**Status Codes**:
- `200`: Service is healthy

### Service Status

Get comprehensive status of all system components.

**Endpoint**: `GET /status`

**Response**:
```json
{
  "status": "healthy",
  "database": {
    "available": true,
    "migrated": true,
    "targets_count": 25,
    "categories_count": 4,
    "probes_count": 2,
    "migration_date": "2024-01-01T10:00:00"
  },
  "configs": {
    "targets": true,
    "probes": true,
    "sources": true
  },
  "generated_files": {
    "targets_file": true,
    "probes_file": true
  },
  "smokeping": {
    "running": true,
    "status": "running",
    "container_name": "grafana-influx-smokeping-1"
  },
  "mode": "database",
  "last_check": "2024-01-01T12:00:00.000000"
}
```

**Status Values**:
- `healthy`: All systems operational
- `partial`: Some components have issues
- `error`: Critical system failure

**Status Codes**:
- `200`: Status retrieved successfully
- `500`: Status check failed

## Configuration Management

### Get All Configurations

Retrieve all configuration data (targets, probes, sources).

**Endpoint**: `GET /config`

**Response**:
```json
{
  "targets": {
    "active_targets": {
      "websites": [
        {
          "name": "google",
          "host": "google.com",
          "title": "Google Search",
          "probe": "FPing"
        }
      ]
    },
    "metadata": {
      "total_targets": 25,
      "active_targets": 20,
      "source": "database",
      "last_updated": "2024-01-01T11:30:00"
    }
  },
  "probes": {
    "probes": {
      "FPing": {
        "binary": "/usr/bin/fping",
        "packetsize": 56,
        "step": 300
      }
    }
  },
  "sources": {
    "dynamic": {
      "tranco": {
        "enabled": true,
        "default_limit": 100
      }
    }
  }
}
```

**Status Codes**:
- `200`: Configuration retrieved successfully
- `500`: Configuration retrieval failed

### Get Specific Configuration

Retrieve a specific configuration type.

**Endpoint**: `GET /config/{config_type}`

**Parameters**:
- `config_type` (required): One of `targets`, `probes`, `sources`

**Example**: `GET /config/targets`

**Response**:
```json
{
  "active_targets": {
    "websites": [
      {
        "name": "google",
        "host": "google.com",
        "title": "Google Search",
        "probe": "FPing"
      }
    ],
    "netflix_oca": [
      {
        "name": "netflix_oca_1",
        "host": "192.168.1.100",
        "title": "Netflix OCA Server",
        "probe": "FPing"
      }
    ]
  },
  "metadata": {
    "total_targets": 25,
    "active_targets": 20,
    "source": "database",
    "last_updated": "2024-01-01T11:30:00"
  }
}
```

**Status Codes**:
- `200`: Configuration retrieved successfully
- `400`: Invalid configuration type
- `404`: Configuration not found
- `500`: Retrieval failed

### Update Configuration

Update a specific configuration (YAML mode only).

**Endpoint**: `PUT /config/{config_type}`

**Parameters**:
- `config_type` (required): One of `targets`, `probes`, `sources`

**Request Body**:
```json
{
  "active_targets": {
    "websites": [
      {
        "name": "example",
        "host": "example.com",
        "title": "Example Site",
        "probe": "FPing"
      }
    ]
  }
}
```

**Response**:
```json
{
  "success": true,
  "message": "Configuration updated successfully",
  "backup_created": "/app/config/targets.yaml.backup.1234567890",
  "updated_at": "2024-01-01T12:00:00.000000"
}
```

**Status Codes**:
- `200`: Configuration updated successfully
- `400`: Invalid configuration data or database mode active
- `500`: Update failed

## Database Operations

### Get All Targets

Retrieve all targets from the database.

**Endpoint**: `GET /targets`

**Query Parameters**:
- `active_only` (optional): `true` to filter only active targets
- `category` (optional): Filter by category name

**Examples**:
- `GET /targets` - Get all targets
- `GET /targets?active_only=true` - Get only active targets
- `GET /targets?category=websites` - Get targets in 'websites' category

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
      "category": {
        "id": 1,
        "name": "websites",
        "display_name": "Websites"
      },
      "probe": {
        "id": 1,
        "name": "FPing",
        "binary_path": "/usr/bin/fping"
      },
      "created_at": "2024-01-01T10:00:00",
      "updated_at": "2024-01-01T11:00:00"
    }
  ],
  "metadata": {
    "total_count": 25,
    "filtered_count": 20,
    "filters_applied": {
      "active_only": true
    }
  }
}
```

**Status Codes**:
- `200`: Targets retrieved successfully
- `400`: Database not available
- `500`: Database error

### Create Target

Create a new monitoring target.

**Endpoint**: `POST /targets`

**Request Body**:
```json
{
  "name": "example",
  "host": "example.com",
  "title": "Example Website",
  "category_id": 1,
  "probe_id": 1,
  "is_active": true
}
```

**Response**:
```json
{
  "id": 26,
  "name": "example",
  "host": "example.com",
  "title": "Example Website",
  "is_active": true,
  "category": {
    "id": 1,
    "name": "websites",
    "display_name": "Websites"
  },
  "probe": {
    "id": 1,
    "name": "FPing",
    "binary_path": "/usr/bin/fping"
  },
  "created_at": "2024-01-01T12:00:00",
  "updated_at": "2024-01-01T12:00:00"
}
```

**Status Codes**:
- `201`: Target created successfully
- `400`: Invalid request data or database not available
- `500`: Database error

### Update Target

Update an existing target.

**Endpoint**: `PUT /targets/{target_id}`

**Parameters**:
- `target_id` (required): Target ID to update

**Request Body**:
```json
{
  "title": "Updated Example Website",
  "is_active": false
}
```

**Response**:
```json
{
  "id": 26,
  "name": "example",
  "host": "example.com",
  "title": "Updated Example Website",
  "is_active": false,
  "category": {
    "id": 1,
    "name": "websites",
    "display_name": "Websites"
  },
  "probe": {
    "id": 1,
    "name": "FPing",
    "binary_path": "/usr/bin/fping"
  },
  "created_at": "2024-01-01T12:00:00",
  "updated_at": "2024-01-01T12:05:00"
}
```

**Status Codes**:
- `200`: Target updated successfully
- `400`: Invalid request data or database not available
- `404`: Target not found
- `500`: Database error

### Delete Target

Delete a target from the database.

**Endpoint**: `DELETE /targets/{target_id}`

**Parameters**:
- `target_id` (required): Target ID to delete

**Response**:
```json
{
  "success": true,
  "message": "Target deleted successfully",
  "deleted_target": {
    "id": 26,
    "name": "example",
    "title": "Example Website"
  }
}
```

**Status Codes**:
- `200`: Target deleted successfully
- `400`: Database not available
- `404`: Target not found
- `500`: Database error

### Toggle Target Status

Toggle a target's active/inactive status.

**Endpoint**: `POST /targets/{target_id}/toggle`

**Parameters**:
- `target_id` (required): Target ID to toggle

**Response**:
```json
{
  "id": 1,
  "name": "google",
  "is_active": false,
  "message": "Target deactivated successfully",
  "updated_at": "2024-01-01T12:10:00"
}
```

**Status Codes**:
- `200`: Target status toggled successfully
- `400`: Database not available
- `404`: Target not found
- `500`: Database error

### Get Categories

Retrieve all target categories.

**Endpoint**: `GET /categories`

**Response**:
```json
{
  "categories": [
    {
      "id": 1,
      "name": "websites",
      "display_name": "Websites",
      "description": "Popular websites and services"
    },
    {
      "id": 2,
      "name": "netflix_oca",
      "display_name": "Netflix OCA",
      "description": "Netflix Open Connect Appliances"
    }
  ]
}
```

**Status Codes**:
- `200`: Categories retrieved successfully
- `400`: Database not available
- `500`: Database error

### Get Probes

Retrieve all probe configurations.

**Endpoint**: `GET /probes`

**Response**:
```json
{
  "probes": [
    {
      "id": 1,
      "name": "FPing",
      "binary_path": "/usr/bin/fping",
      "step_seconds": 300,
      "pings": 20,
      "forks": 5,
      "is_default": true
    },
    {
      "id": 2,
      "name": "EchoPing",
      "binary_path": "/usr/bin/echoping",
      "step_seconds": 300,
      "pings": 20,
      "forks": 5,
      "is_default": false
    }
  ]
}
```

**Status Codes**:
- `200`: Probes retrieved successfully
- `400`: Database not available
- `500`: Database error

## Service Operations

### Generate Configuration

Generate SmokePing configuration files from current data.

**Endpoint**: `POST /generate`

**Response**:
```json
{
  "success": true,
  "message": "SmokePing configuration generated successfully",
  "files_generated": [
    "/app/smokeping-config/Targets",
    "/app/smokeping-config/Probes"
  ],
  "target_count": 20,
  "generated_at": "2024-01-01T12:00:00.000000"
}
```

**Status Codes**:
- `200`: Configuration generated successfully
- `500`: Generation failed

### Restart SmokePing

Restart the SmokePing service.

**Endpoint**: `POST /restart`

**Response**:
```json
{
  "success": true,
  "message": "SmokePing service restarted successfully",
  "container_name": "grafana-influx-smokeping-1",
  "restart_method": "docker_compose",
  "restarted_at": "2024-01-01T12:00:00.000000"
}
```

**Status Codes**:
- `200`: Service restarted successfully
- `500`: Restart failed

### Refresh OCA Data

Refresh Netflix Open Connect Appliance server data.

**Endpoint**: `POST /oca/refresh`

**Response**:
```json
{
  "success": true,
  "message": "OCA data refreshed successfully",
  "output": "Successfully refreshed 15 OCA servers",
  "refreshed_at": "2024-01-01T12:00:00.000000"
}
```

**Status Codes**:
- `200`: OCA data refreshed successfully
- `500`: OCA refresh failed

## Error Handling

### Error Response Format

All errors follow a consistent format:

```json
{
  "error": "Descriptive error message",
  "timestamp": "2024-01-01T12:00:00.000000"
}
```

### Common Error Types

#### 400 Bad Request
- Invalid configuration data
- Missing required fields
- Database not available when required
- Invalid query parameters

#### 404 Not Found
- Target not found
- Configuration type not found
- Resource does not exist

#### 405 Method Not Allowed
- HTTP method not supported for endpoint

#### 500 Internal Server Error
- Database connection errors
- File system errors
- Service restart failures
- Configuration generation errors

## Examples

### Basic Workflow

```bash
# 1. Check service health
curl http://localhost:5000/health

# 2. Get system status
curl http://localhost:5000/status

# 3. List all targets
curl http://localhost:5000/targets

# 4. Add a new target
curl -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cloudflare",
    "host": "1.1.1.1",
    "title": "Cloudflare DNS",
    "category_id": 1,
    "probe_id": 1
  }'

# 5. Generate configuration
curl -X POST http://localhost:5000/generate

# 6. Restart SmokePing
curl -X POST http://localhost:5000/restart
```

### Bulk Configuration (YAML Mode)

```bash
# Update targets configuration
curl -X PUT http://localhost:5000/config/targets \
  -H "Content-Type: application/json" \
  -d @targets.json

# Generate and deploy
curl -X POST http://localhost:5000/generate
curl -X POST http://localhost:5000/restart
```

### Monitoring and Maintenance

```bash
# Check active targets only
curl "http://localhost:5000/targets?active_only=true"

# Get specific category
curl "http://localhost:5000/targets?category=websites"

# Refresh OCA data
curl -X POST http://localhost:5000/oca/refresh

# Get comprehensive status
curl http://localhost:5000/status | jq '.'
```

## Interactive Documentation

Access the complete interactive API documentation at:
- **Swagger UI**: http://localhost:5000/docs
- **OpenAPI Spec**: http://localhost:5000/api/docs

For more examples and integration guides, see the [examples directory](../examples/).