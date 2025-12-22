# SmokePing Standard Edition

Professional network monitoring with PostgreSQL database, web administration interface, and REST API.

## Features

- 🎯 **Web Admin Interface**: Manage targets, sources, and configuration through a modern UI
- 🗄️ **PostgreSQL Database**: Reliable configuration storage with backup/restore
- 🔌 **REST API**: Programmatic access for automation and integration
- 📊 **SmokePing Core**: Industry-standard network latency monitoring
- 🔒 **Secure Access**: Username/password authentication for admin interface
- 🔄 **Hot Reload**: Apply configuration changes without restarts

## Quick Start

### One-Command Setup

```bash
# Clone the repository
git clone <repository-url>
cd smoking-pi/editions/standard

# Run the setup script
./setup.sh
```

That's it! The setup script will:
- ✅ Generate secure passwords for PostgreSQL and Web Admin
- ✅ Configure your timezone automatically
- ✅ Initialize the PostgreSQL database
- ✅ Start all services (PostgreSQL, SmokePing, Web Admin, Config Manager)
- ✅ Display access credentials and URLs

### What the Setup Does

1. **Password Generation**: Creates secure passwords for all services
2. **Database Initialization**: Sets up PostgreSQL with the targets schema
3. **Service Orchestration**: Starts all containers in the correct order
4. **Health Verification**: Ensures all services are running properly
5. **Credential Display**: Shows how to access each service

### Access Points

After setup completes, you'll have access to:

- **🌐 Web Admin**: http://localhost:8080
  - Modern UI for target management
  - Bulk operations support
  - Real-time validation
  
- **📈 SmokePing**: http://localhost:8081
  - Classic SmokePing interface
  - View latency graphs and statistics
  - No authentication required

- **🔌 API Endpoints**: http://localhost:8080/api
  - REST API for automation
  - Full CRUD operations
  - JSON format

## Default Credentials

The setup script generates secure passwords automatically. To view them:

```bash
# View all credentials
cat .env

# Or specifically:
grep WEB_ADMIN_PASSWORD .env
grep POSTGRES_PASSWORD .env
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Web Browser   │────▶│   Web Admin UI  │
└─────────────────┘     └────────┬────────┘
                                 │
                                 ▼
┌─────────────────┐     ┌─────────────────┐
│  Config Manager │◀────│   PostgreSQL    │
│      (API)      │     │    Database     │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│    SmokePing    │────▶│  Target Hosts   │
│     Engine      │     │   (Network)     │
└─────────────────┘     └─────────────────┘
```

## Managing Targets

### Via Web Interface

1. Access http://localhost:8080
2. Login with credentials from `.env`
3. Navigate to Targets section
4. Add/Edit/Delete targets with the UI

### Via API

```bash
# Get all targets
curl http://localhost:8080/api/targets

# Add a new target
curl -X POST http://localhost:8080/api/targets \
  -H "Content-Type: application/json" \
  -d '{"name": "google-dns", "host": "8.8.8.8"}'
```

## Configuration

### Environment Variables

All configuration is in `.env` (auto-generated):

| Variable | Description | Default |
|----------|-------------|---------|
| `TZ` | Timezone | Auto-detected |
| `POSTGRES_PASSWORD` | Database password | Auto-generated |
| `WEB_ADMIN_PASSWORD` | Admin interface password | Auto-generated |
| `SECRET_KEY` | Session security key | Auto-generated |
| `WEB_ADMIN_PORT` | Web admin port | 8080 |
| `SMOKEPING_PORT` | SmokePing port | 8081 |

### Database Schema

The PostgreSQL database stores:
- **Targets**: Monitoring endpoints
- **Sources**: Configuration sources
- **Countries**: Geographic organization
- **Probes**: SmokePing probe configurations

## Maintenance

### View Logs

```bash
# All services
docker-compose logs

# Specific service
docker-compose logs web-admin
docker-compose logs postgres
```

### Stop Services

```bash
# Stop all containers
docker-compose down

# Stop and remove data
docker-compose down -v
```

### Backup Database

```bash
# Backup
docker-compose exec postgres pg_dump -U smokeping smokeping_targets > backup.sql

# Restore
docker-compose exec -T postgres psql -U smokeping smokeping_targets < backup.sql
```

## Upgrading

### From Basic Edition

```bash
# Use migration script
cd smoking-pi
./shared/scripts/migrate-to-edition.sh standard
```

### To Pro Edition

Ready for Grafana dashboards and time-series analysis? Upgrade to Pro:

```bash
./shared/scripts/migrate-to-edition.sh pro
```

## Troubleshooting

### Common Issues

1. **Port conflicts**: Change ports in `.env` file
2. **Database connection failed**: Check PostgreSQL container logs
3. **Web admin not accessible**: Verify firewall rules
4. **Targets not updating**: Check config-manager logs

### Reset Everything

```bash
# Stop and remove all data
docker-compose down -v

# Re-run setup
./setup.sh
```

## Technical Details

- **Web Framework**: Flask with Pydantic validation
- **Database**: PostgreSQL 15 with persistent storage
- **API**: RESTful with OpenAPI documentation
- **Config Generation**: Jinja2 templates
- **Container Base**: Official Python and PostgreSQL images