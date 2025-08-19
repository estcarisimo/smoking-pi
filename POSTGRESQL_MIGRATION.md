# PostgreSQL Database Migration Guide

This guide covers the new PostgreSQL database integration for centralized target management in Smoking Pi.

## 🎯 Overview

The PostgreSQL integration provides:
- **Centralized target management** with normalized database schema
- **Active/inactive status** for targets without deletion
- **Complete metadata storage** for Netflix OCA and other target types
- **RESTful API** for programmatic target management
- **Seamless YAML fallback** for backward compatibility

## 🚀 Quick Start

### 1. Deploy with PostgreSQL (New Installations)

```bash
cd grafana-influx/
./init-passwords-docker.sh  # Generates PostgreSQL password
docker-compose up -d         # Includes PostgreSQL container
```

The system will automatically:
- Create the PostgreSQL database
- Initialize tables and schema
- Set up the database with default categories and probes

### 2. Migrate Existing YAML Configuration

For existing installations, migrate your YAML configuration to PostgreSQL:

```bash
# Run the migration script
docker exec grafana-influx-config-manager-1 python3 /app/scripts/migrate_yaml_to_db.py

# Verify migration
docker exec grafana-influx-config-manager-1 python3 /app/scripts/migrate_yaml_to_db.py --verify-only
```

The migration script will:
- Backup existing YAML files
- Create database tables
- Import all targets, categories, and probes
- Preserve all metadata including Netflix OCA data
- Mark migration as complete

## 🏗️ Database Schema

### Tables Overview

```sql
-- Target categories (custom, dns_resolvers, netflix_oca, top_sites)
CREATE TABLE target_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    description TEXT
);

-- Probe configurations (FPing, FPing6, DNS)
CREATE TABLE probes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    binary_path VARCHAR(200) NOT NULL,
    step_seconds INTEGER DEFAULT 300,
    pings INTEGER DEFAULT 10,
    forks INTEGER,
    is_default BOOLEAN DEFAULT FALSE
);

-- Monitoring targets with full metadata
CREATE TABLE targets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    host VARCHAR(500) NOT NULL,
    title VARCHAR(200) NOT NULL,
    category_id INTEGER REFERENCES target_categories(id),
    probe_id INTEGER REFERENCES probes(id),
    lookup VARCHAR(200),  -- DNS query domain
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Netflix OCA metadata columns
    asn VARCHAR(20),
    cache_id VARCHAR(20),
    city VARCHAR(100),
    domain VARCHAR(500),
    iata_code VARCHAR(10),
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    location_code VARCHAR(20),
    raw_city VARCHAR(200),
    metadata_type VARCHAR(50)
);
```

## 🔌 API Endpoints

### Target Management

```bash
# List all targets
curl http://localhost:5000/targets

# List only active targets
curl http://localhost:5000/targets?active_only=true

# List targets by category
curl http://localhost:5000/targets?category=custom

# Create new target
curl -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "google",
    "host": "8.8.8.8",
    "title": "Google DNS",
    "category_id": 1,
    "probe_id": 1,
    "is_active": true
  }'

# Update target
curl -X PUT http://localhost:5000/targets/1 \
  -H "Content-Type: application/json" \
  -d '{"title": "Updated Title"}'

# Toggle active status
curl -X POST http://localhost:5000/targets/1/toggle

# Delete target
curl -X DELETE http://localhost:5000/targets/1
```

### Categories and Probes

```bash
# List categories
curl http://localhost:5000/categories

# List probes
curl http://localhost:5000/probes
```

## 🔄 Hybrid Mode Operation

The system automatically detects database availability and operates in hybrid mode:

### Database Mode (Preferred)
- All operations use PostgreSQL
- Full CRUD capabilities
- Active/inactive status management
- Complete metadata support

### YAML Fallback Mode
- Automatic fallback when database unavailable
- Read-only compatibility with existing YAML files
- All existing functionality preserved

### Status Check
```bash
# Check current mode and database status
curl http://localhost:5000/status
```

Response includes:
```json
{
  "using_database": true,
  "database": {
    "available": true,
    "target_count": 42,
    "migration_completed": true
  },
  "status": "healthy"
}
```

## 🛠️ Configuration

### Environment Variables

```bash
# Database connection (auto-generated)
POSTGRES_PASSWORD=<generated-password>

# Database URL for applications
DATABASE_URL=postgresql://smokeping:${POSTGRES_PASSWORD}@postgres:5432/smokeping_targets
```

### Docker Compose Configuration

The PostgreSQL container is automatically included:

```yaml
postgres:
  image: postgres:15
  ports:
    - "127.0.0.1:5432:5432"
  volumes:
    - postgres-data:/var/lib/postgresql/data
    - ./postgres/init:/docker-entrypoint-initdb.d:ro
  environment:
    POSTGRES_DB: smokeping_targets
    POSTGRES_USER: smokeping
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
```

## 📱 Web Interface Integration

### Target Management
- **Database Detection**: Automatically shows database status
- **Active/Inactive Toggle**: Available in database mode
- **Enhanced Target List**: Shows database vs YAML mode
- **Real-time Updates**: Changes immediately reflected

### Source Management
- **Smart Updates**: Uses database operations when available
- **YAML Compatibility**: Falls back for existing functionality
- **Bulk Operations**: Enhanced with database capabilities

## 🔧 Troubleshooting

### Migration Issues

```bash
# Check migration status
docker exec grafana-influx-config-manager-1 python3 /app/scripts/migrate_yaml_to_db.py --verify-only

# View database logs
docker logs grafana-influx-postgres-1

# Check database connectivity
docker exec grafana-influx-config-manager-1 python3 -c "
from models import get_db_session
session = get_db_session()
print('Database connection successful')
session.close()
"
```

### Common Issues

1. **Database not available**: System automatically falls back to YAML mode
2. **Migration incomplete**: Re-run migration script with `--no-backup` flag
3. **Permission issues**: Ensure PostgreSQL container has proper volume permissions

### Reset Database

```bash
# Stop services
docker-compose down

# Remove database volume
docker volume rm grafana-influx_postgres-data

# Restart services (will recreate database)
docker-compose up -d
```

## 📈 Performance Considerations

- **Database Size**: Typical installations use <10MB for target data
- **Query Performance**: Indexed queries for fast target lookups
- **Memory Usage**: PostgreSQL container uses ~30MB additional RAM
- **Backup**: Database included in standard backup procedures

## 🔐 Security

- **Network Access**: PostgreSQL only accessible from application containers
- **Authentication**: Strong generated passwords
- **Data Validation**: Input validation on all API endpoints
- **SQL Injection**: Protected via SQLAlchemy ORM

## 📚 Advanced Usage

### Custom Queries

```python
from models import get_db_session, Target, TargetCategory

session = get_db_session()
try:
    # Get all active Netflix targets
    netflix_targets = session.query(Target).join(TargetCategory).filter(
        TargetCategory.name == 'netflix_oca',
        Target.is_active == True
    ).all()
    
    for target in netflix_targets:
        print(f"{target.name}: {target.host} (City: {target.city})")
finally:
    session.close()
```

### Backup and Restore

```bash
# Backup database
docker exec grafana-influx-postgres-1 pg_dump -U smokeping smokeping_targets > backup.sql

# Restore database
docker exec -i grafana-influx-postgres-1 psql -U smokeping smokeping_targets < backup.sql
```

## 🎉 Benefits

### For Users
- **Simplified Management**: Single interface for all targets
- **Better Organization**: Categories and active/inactive status
- **Enhanced Metadata**: Rich target information storage
- **Improved Performance**: Fast database queries vs file parsing

### For Developers
- **RESTful API**: Standard HTTP API for integration
- **Normalized Schema**: Proper database design
- **ORM Support**: SQLAlchemy models for easy development
- **Migration Tools**: Built-in migration and backup tools

The PostgreSQL integration provides a modern, scalable foundation for target management while maintaining full backward compatibility with existing YAML-based deployments.