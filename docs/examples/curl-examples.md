# cURL Usage Examples

Comprehensive cURL examples for interacting with the SmokePing system APIs, covering all major operations and workflows.

## 📋 Table of Contents

- [Getting Started](#getting-started)
- [Health and Status](#health-and-status)
- [Configuration Management](#configuration-management)
- [Target Management](#target-management)
- [Site Discovery](#site-discovery)
- [Service Operations](#service-operations)
- [Database Operations](#database-operations)
- [Workflows](#workflows)
- [Error Handling](#error-handling)
- [Authentication Examples](#authentication-examples)

## Getting Started

### Prerequisites

Ensure the SmokePing system is running:

```bash
cd grafana-influx/
docker-compose ps
```

All services should show as "Up" status.

### Environment Setup

Set up environment variables for easier command execution:

```bash
# Service URLs
export CONFIG_MANAGER_URL="http://localhost:5000"
export WEB_ADMIN_URL="http://localhost:8080"

# Future authentication (when implemented)
export API_TOKEN="smokeping_api_your_token_here"

# Helper function for authenticated requests
api_curl() {
    curl -H "Authorization: Bearer $API_TOKEN" "$@"
}
```

## Health and Status

### Basic Health Checks

Check if services are responsive:

```bash
# Config Manager health
curl http://localhost:5000/health

# Expected response:
# {
#   "status": "healthy",
#   "service": "config-manager", 
#   "timestamp": "2024-01-01T12:00:00.000000"
# }

# Web Admin status
curl http://localhost:8080/api/status

# Comprehensive system status
curl http://localhost:5000/status | jq '.'
```

### Detailed Status Information

```bash
# Get full system status with formatting
curl -s http://localhost:5000/status | jq '{
  status: .status,
  database: .database,
  smokeping: .smokeping,
  target_count: .database.targets_count
}'

# Check database connectivity
curl -s http://localhost:5000/status | jq '.database.available'

# Check SmokePing service status
curl -s http://localhost:5000/status | jq '.smokeping.running'
```

## Configuration Management

### Get Configuration Data

```bash
# Get all configurations
curl http://localhost:5000/config | jq '.'

# Get specific configuration types
curl http://localhost:5000/config/targets | jq '.'
curl http://localhost:5000/config/probes | jq '.'
curl http://localhost:5000/config/sources | jq '.'

# Get only active targets
curl -s http://localhost:5000/config/targets | jq '.active_targets'

# Count targets by category
curl -s http://localhost:5000/config/targets | jq '
  .active_targets | to_entries | map({
    category: .key,
    count: (.value | length)
  })'
```

### Update Configuration (YAML Mode)

```bash
# Create new targets configuration
cat > new_targets.json << 'EOF'
{
  "active_targets": {
    "websites": [
      {
        "name": "google",
        "host": "google.com",
        "title": "Google Search",
        "probe": "FPing"
      },
      {
        "name": "cloudflare",
        "host": "1.1.1.1", 
        "title": "Cloudflare DNS",
        "probe": "FPing"
      }
    ]
  }
}
EOF

# Update targets configuration (YAML mode only)
curl -X PUT http://localhost:5000/config/targets \
  -H "Content-Type: application/json" \
  -d @new_targets.json

# Verify the update
curl http://localhost:5000/config/targets | jq '.active_targets.websites'
```

## Target Management

### List and Filter Targets

```bash
# Get all targets (database mode)
curl http://localhost:5000/targets | jq '.'

# Get only active targets
curl "http://localhost:5000/targets?active_only=true" | jq '.targets'

# Filter by category
curl "http://localhost:5000/targets?category=websites" | jq '.targets'

# Combined filters
curl "http://localhost:5000/targets?active_only=true&category=websites" | \
  jq '.targets[] | {name: .name, host: .host, title: .title}'

# Count targets by status
curl -s http://localhost:5000/targets | jq '
  .targets | group_by(.is_active) | map({
    active: .[0].is_active,
    count: length
  })'
```

### Create New Targets

```bash
# Create a single target
curl -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "example",
    "host": "example.com",
    "title": "Example Website",
    "category_id": 1,
    "probe_id": 1,
    "is_active": true
  }'

# Create multiple targets with a script
declare -a sites=(
  "github.com:GitHub"
  "stackoverflow.com:Stack Overflow"
  "reddit.com:Reddit"
)

for site in "${sites[@]}"; do
  host=$(echo $site | cut -d: -f1)
  title=$(echo $site | cut -d: -f2)
  name=$(echo $host | sed 's/\./_/g')
  
  curl -X POST http://localhost:5000/targets \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"$name\",
      \"host\": \"$host\",
      \"title\": \"$title\",
      \"category_id\": 1,
      \"probe_id\": 1
    }"
  
  echo "Created target: $name ($host)"
done
```

### Update and Manage Targets

```bash
# Update a target's title and status
curl -X PUT http://localhost:5000/targets/1 \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Updated Example Website",
    "is_active": false
  }'

# Toggle target status
curl -X POST http://localhost:5000/targets/1/toggle

# Get specific target details
curl http://localhost:5000/targets | jq '.targets[] | select(.id == 1)'

# Delete a target
curl -X DELETE http://localhost:5000/targets/1

# Bulk status update (activate all targets in a category)
target_ids=$(curl -s "http://localhost:5000/targets?category=websites" | \
  jq -r '.targets[] | select(.is_active == false) | .id')

for id in $target_ids; do
  curl -X POST http://localhost:5000/targets/$id/toggle
  echo "Activated target ID: $id"
done
```

## Site Discovery

### Fetch Top Sites from External Sources

```bash
# Fetch from Tranco (no authentication needed)
curl "http://localhost:8080/sources/api/fetch/tranco?limit=10" | jq '.'

# Fetch from CrUX with country filter
curl "http://localhost:8080/sources/api/fetch/crux?country=us&limit=5" | \
  jq '.sites[] | {rank: .rank, domain: .domain, title: .title}'

# Fetch from Cloudflare Radar (requires API token)
curl "http://localhost:8080/sources/api/fetch/cloudflare?country=US&limit=10" | jq '.'

# Compare top sites across sources
echo "=== Top 5 Sites Comparison ==="
echo "Tranco:"
curl -s "http://localhost:8080/sources/api/fetch/tranco?limit=5" | \
  jq -r '.sites[] | "\(.rank). \(.domain)"'

echo -e "\nCrUX (US):"
curl -s "http://localhost:8080/sources/api/fetch/crux?country=us&limit=5" | \
  jq -r '.sites[] | "\(.rank). \(.domain)"'
```

### Bulk Import Discovered Sites

```bash
# Fetch sites and prepare for import
sites_data=$(curl -s "http://localhost:8080/sources/api/fetch/tranco?limit=10" | \
  jq '.sites | map({
    domain: .domain,
    title: .title,
    category: "websites"
  })')

# Import the sites
curl -X POST http://localhost:8080/sources/api/update \
  -H "Content-Type: application/json" \
  -d "{\"selected_sites\": $sites_data}"

# Verify import
curl "http://localhost:5000/targets?category=websites" | \
  jq '.targets | length'
```

## Service Operations

### Configuration Generation and Deployment

```bash
# Generate SmokePing configuration files
curl -X POST http://localhost:5000/generate

# Check generation result
curl -s -X POST http://localhost:5000/generate | jq '{
  success: .success,
  target_count: .target_count,
  files: .files_generated
}'

# Restart SmokePing service
curl -X POST http://localhost:5000/restart

# Apply changes through web admin (generate + restart)
curl -X POST http://localhost:8080/api/apply
```

### Netflix OCA Management

```bash
# Refresh Netflix OCA data
curl -X POST http://localhost:5000/oca/refresh

# Check OCA status
curl http://localhost:8080/api/ocas/status | jq '.'

# List Netflix OCA targets
curl "http://localhost:5000/targets?category=netflix_oca" | \
  jq '.targets[] | {name: .name, host: .host, site: .site}'
```

## Database Operations

### Categories and Probes

```bash
# Get all categories
curl http://localhost:5000/categories | jq '.categories'

# Get category with target count
curl -s http://localhost:5000/categories | jq '.categories[] | {
  name: .name,
  display_name: .display_name,
  description: .description
}'

# Get all probes
curl http://localhost:5000/probes | jq '.probes'

# Find default probe
curl -s http://localhost:5000/probes | jq '.probes[] | select(.is_default == true)'
```

### Database Status and Health

```bash
# Check database availability
curl -s http://localhost:5000/status | jq '.database.available'

# Get database statistics
curl -s http://localhost:5000/status | jq '.database | {
  available: .available,
  migrated: .migrated,
  targets: .targets_count,
  categories: .categories_count,
  probes: .probes_count
}'

# Check migration status
curl -s http://localhost:5000/status | jq '.database.migration_date'
```

## Workflows

### Complete Target Lifecycle

```bash
#!/bin/bash
# Complete target management workflow

echo "=== SmokePing Target Management Workflow ==="

# 1. Check system status
echo "1. Checking system status..."
curl -s http://localhost:5000/status | jq '.status'

# 2. List existing targets
echo "2. Current targets:"
curl -s http://localhost:5000/targets | jq '.targets | length'

# 3. Add new target
echo "3. Adding new target..."
new_target=$(curl -s -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cloudflare_dns",
    "host": "1.1.1.1",
    "title": "Cloudflare Public DNS",
    "category_id": 4,
    "probe_id": 1
  }')

target_id=$(echo $new_target | jq -r '.id')
echo "Created target with ID: $target_id"

# 4. Verify target creation
echo "4. Verifying target..."
curl -s http://localhost:5000/targets | jq ".targets[] | select(.id == $target_id)"

# 5. Generate configuration
echo "5. Generating configuration..."
curl -s -X POST http://localhost:5000/generate | jq '.success'

# 6. Restart service
echo "6. Restarting SmokePing..."
curl -s -X POST http://localhost:5000/restart | jq '.success'

# 7. Final status
echo "7. Final system status:"
curl -s http://localhost:5000/status | jq '{
  status: .status,
  targets: .database.targets_count,
  smokeping: .smokeping.running
}'

echo "=== Workflow Complete ==="
```

### Bulk Site Discovery and Import

```bash
#!/bin/bash
# Discover and import top sites from multiple sources

echo "=== Bulk Site Discovery and Import ==="

# Create temporary directory for data
mkdir -p /tmp/smokeping_discovery

# Fetch from multiple sources
echo "1. Fetching from Tranco..."
curl -s "http://localhost:8080/sources/api/fetch/tranco?limit=20" > \
  /tmp/smokeping_discovery/tranco.json

echo "2. Fetching from CrUX..."
curl -s "http://localhost:8080/sources/api/fetch/crux?country=global&limit=20" > \
  /tmp/smokeping_discovery/crux.json

# Process and combine data
echo "3. Processing site data..."
jq -s '
  .[0].sites + .[1].sites | 
  unique_by(.domain) | 
  sort_by(.rank) | 
  .[0:15] | 
  map({
    domain: .domain,
    title: .title,
    category: "websites"
  })
' /tmp/smokeping_discovery/tranco.json /tmp/smokeping_discovery/crux.json > \
  /tmp/smokeping_discovery/combined.json

# Import sites
echo "4. Importing sites..."
curl -X POST http://localhost:8080/sources/api/update \
  -H "Content-Type: application/json" \
  -d @/tmp/smokeping_discovery/combined.json

# Apply configuration
echo "5. Applying configuration..."
curl -s -X POST http://localhost:8080/api/apply | jq '.success'

# Show results
echo "6. Import results:"
curl -s "http://localhost:5000/targets?category=websites" | \
  jq '.targets | length'

# Cleanup
rm -rf /tmp/smokeping_discovery

echo "=== Discovery and Import Complete ==="
```

## Error Handling

### Common Error Scenarios

```bash
# Test error handling for invalid requests

# 1. Invalid target data
echo "Testing invalid target creation..."
curl -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "",
    "host": "invalid_host",
    "category_id": 999
  }' | jq '.'

# 2. Non-existent target
echo "Testing non-existent target update..."
curl -X PUT http://localhost:5000/targets/99999 \
  -H "Content-Type: application/json" \
  -d '{"title": "Updated"}' | jq '.'

# 3. Database unavailable simulation
echo "Testing database unavailable scenario..."
# Stop PostgreSQL temporarily
docker-compose stop postgres
sleep 2

# Try database operation (should fallback to YAML)
curl http://localhost:5000/targets | jq '.'

# Restart PostgreSQL
docker-compose start postgres
sleep 5

# 4. Service restart failure
echo "Testing service restart..."
curl -X POST http://localhost:5000/restart | jq '.'
```

### Error Response Parsing

```bash
# Helper function to parse and display errors
parse_error() {
  local response="$1"
  echo $response | jq -r '
    if .error then
      "ERROR: " + .error + " (at " + .timestamp + ")"
    elif .success == false then
      "FAILURE: " + .message
    else
      "SUCCESS: " + (.message // "Operation completed")
    end
  '
}

# Example usage
response=$(curl -s -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{"invalid": "data"}')

parse_error "$response"
```

## Authentication Examples

### Future Authentication Implementation

When API token authentication is implemented:

```bash
# Set API token
export API_TOKEN="smokeping_api_your_generated_token"

# Authenticated requests
curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:5000/targets

# Alternative query parameter method
curl "http://localhost:5000/targets?api_key=$API_TOKEN"

# Authenticated POST request
curl -X POST http://localhost:5000/targets \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "authenticated_target",
    "host": "example.com",
    "title": "Authenticated Target",
    "category_id": 1,
    "probe_id": 1
  }'

# Test authentication failure
curl -H "Authorization: Bearer invalid_token" \
  http://localhost:5000/targets
# Expected: 401 Unauthorized
```

### Token Management

```bash
# Get API token from environment
source .env
echo "API Token: $API_TOKEN"

# Test token validity
test_token() {
  local token="$1"
  local response=$(curl -s -w "%{http_code}" \
    -H "Authorization: Bearer $token" \
    http://localhost:5000/health)
  
  local http_code="${response: -3}"
  local body="${response%???}"
  
  if [ "$http_code" = "200" ]; then
    echo "Token valid: $body"
  else
    echo "Token invalid or expired (HTTP $http_code)"
  fi
}

# Usage
test_token "$API_TOKEN"
```

## Performance and Monitoring

### Performance Testing

```bash
# Benchmark API response times
benchmark_api() {
  local endpoint="$1"
  local requests=10
  
  echo "Benchmarking: $endpoint"
  
  for i in $(seq 1 $requests); do
    time_ms=$(curl -s -w "%{time_total}" -o /dev/null $endpoint)
    echo "Request $i: ${time_ms}s"
  done
}

# Test endpoints
benchmark_api "http://localhost:5000/health"
benchmark_api "http://localhost:5000/status"
benchmark_api "http://localhost:5000/targets"
```

### Monitoring Scripts

```bash
#!/bin/bash
# Continuous monitoring script

monitor_services() {
  while true; do
    echo "=== $(date) ==="
    
    # Check service health
    for service in health status targets; do
      response=$(curl -s http://localhost:5000/$service)
      if echo $response | jq -e . >/dev/null 2>&1; then
        echo "✓ $service: OK"
      else
        echo "✗ $service: FAILED"
      fi
    done
    
    # Check target count
    count=$(curl -s http://localhost:5000/targets | jq '.targets | length')
    echo "Active targets: $count"
    
    echo "---"
    sleep 30
  done
}

# Run monitoring
monitor_services
```

## Troubleshooting Commands

### Debug Service Issues

```bash
# Check service connectivity
curl -v http://localhost:5000/health

# Test internal service communication
docker exec -it grafana-influx-web-admin-1 \
  curl http://config-manager:5000/health

# Check container logs
docker-compose logs config-manager
docker-compose logs web-admin

# Test database connectivity
curl -s http://localhost:5000/status | \
  jq '.database | {available, migrated, targets_count}'

# Verify configuration generation
curl -s -X POST http://localhost:5000/generate | \
  jq '{success, target_count, files_generated}'
```

### Network Debugging

```bash
# Test port connectivity
nc -zv localhost 5000  # Config Manager
nc -zv localhost 8080  # Web Admin
nc -zv localhost 3000  # Grafana
nc -zv localhost 8086  # InfluxDB

# Check Docker network
docker network ls
docker network inspect grafana-influx_default

# Test service resolution
docker exec -it grafana-influx-web-admin-1 nslookup config-manager
docker exec -it grafana-influx-web-admin-1 nslookup postgres
```

For more advanced examples and integration patterns, see the [Python client examples](python-client.py) and [Postman collection](postman-collection.json).