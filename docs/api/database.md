# Database API Reference

Complete reference for PostgreSQL database operations in the SmokePing system, including schema, CRUD operations, and migration functionality.

## 📋 Table of Contents

- [Overview](#overview)
- [Database Schema](#database-schema)
- [Operating Modes](#operating-modes)
- [Target Management](#target-management)
- [Category Management](#category-management)
- [Probe Management](#probe-management)
- [Migration & Fallback](#migration--fallback)
- [Database Connections](#database-connections)
- [Examples](#examples)

## Overview

The SmokePing system uses PostgreSQL as the primary configuration database, providing structured target management with relationships, metadata, and active/inactive status control.

### Key Features

- **Normalized Schema**: Proper database relationships and constraints
- **Active/Inactive Status**: Toggle targets without deletion
- **Metadata Storage**: Rich target information including timestamps
- **Migration Support**: Seamless YAML-to-database migration
- **Fallback Capability**: Automatic YAML mode when database unavailable
- **Transaction Safety**: ACID compliance for configuration changes

### Database Connection

**Connection String**: `postgresql://smokeping:${POSTGRES_PASSWORD}@postgres:5432/smokeping_targets`

**Connection Pool**: Managed by SQLAlchemy with automatic reconnection

## Database Schema

### Core Tables

#### targets
Primary table for monitoring targets.

```sql
CREATE TABLE targets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    host VARCHAR(255) NOT NULL,
    title VARCHAR(255),
    is_active BOOLEAN DEFAULT true,
    category_id INTEGER REFERENCES categories(id),
    probe_id INTEGER REFERENCES probes(id),
    
    -- Netflix OCA specific fields
    site VARCHAR(100),
    cachegroup VARCHAR(100),
    
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT targets_name_unique UNIQUE(name),
    CONSTRAINT targets_host_check CHECK(length(host) > 0)
);
```

**Indexes**:
- `idx_targets_name` on `name`
- `idx_targets_category` on `category_id`
- `idx_targets_active` on `is_active`
- `idx_targets_probe` on `probe_id`

#### categories
Target categorization system.

```sql
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT categories_name_check CHECK(length(name) > 0)
);
```

**Default Categories**:
```sql
INSERT INTO categories (name, display_name, description) VALUES
('websites', 'Websites', 'Popular websites and services'),
('custom', 'Custom', 'User-defined targets'),
('netflix_oca', 'Netflix OCA', 'Netflix Open Connect Appliances'),
('dns_resolvers', 'DNS Resolvers', 'DNS resolution monitoring');
```

#### probes
Probe configuration definitions.

```sql
CREATE TABLE probes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    binary_path VARCHAR(255) NOT NULL,
    step_seconds INTEGER DEFAULT 300,
    pings INTEGER DEFAULT 20,
    forks INTEGER DEFAULT 5,
    is_default BOOLEAN DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT probes_name_check CHECK(length(name) > 0),
    CONSTRAINT probes_step_positive CHECK(step_seconds > 0),
    CONSTRAINT probes_pings_positive CHECK(pings > 0)
);
```

**Default Probes**:
```sql
INSERT INTO probes (name, binary_path, step_seconds, pings, forks, is_default) VALUES
('FPing', '/usr/bin/fping', 300, 20, 5, true),
('EchoPing', '/usr/bin/echoping', 300, 20, 5, false);
```

#### system_metadata
System configuration and migration tracking.

```sql
CREATE TABLE system_metadata (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Migration Marker**:
```sql
INSERT INTO system_metadata (key, value) VALUES
('yaml_migration_completed', '2024-01-01T12:00:00.000000');
```

## Operating Modes

### Database Mode (Primary)

When PostgreSQL is available and migration is complete:

**Detection Logic**:
1. Check database connectivity
2. Verify `system_metadata` table exists
3. Confirm `yaml_migration_completed` marker exists
4. Set `use_database = True`

**Features Available**:
- Individual target CRUD operations
- Active/inactive status management
- Rich metadata and relationships
- Query filtering and categorization
- Automatic configuration regeneration
- Transaction safety

**API Endpoints Active**:
- `GET /targets` - List targets with filtering
- `POST /targets` - Create new target
- `PUT /targets/{id}` - Update target
- `DELETE /targets/{id}` - Delete target
- `POST /targets/{id}/toggle` - Toggle status
- `GET /categories` - List categories
- `GET /probes` - List probes

### YAML Fallback Mode

When database is unavailable or migration incomplete:

**Fallback Triggers**:
- Database connection failure
- Missing `system_metadata` table
- Missing migration marker
- Database operation errors

**Features Available**:
- File-based configuration management
- Bulk configuration updates
- YAML backup and versioning
- Direct file manipulation

**API Endpoints Active**:
- `GET /config` - Get YAML configurations
- `PUT /config/{type}` - Update YAML files
- `POST /generate` - Generate from YAML
- `POST /restart` - Restart SmokePing

## Target Management

### Create Target

Add a new monitoring target to the database.

**Endpoint**: `POST /targets`

**Database Operation**:
```python
def create_target(data):
    target = Target(
        name=data['name'],
        host=data['host'],
        title=data['title'],
        category_id=data['category_id'],
        probe_id=data['probe_id'],
        is_active=data.get('is_active', True)
    )
    session.add(target)
    session.commit()
    return target
```

**SQL Generated**:
```sql
INSERT INTO targets (name, host, title, category_id, probe_id, is_active, created_at, updated_at)
VALUES ('example', 'example.com', 'Example Site', 1, 1, true, NOW(), NOW())
RETURNING id;
```

**Validation Rules**:
- `name` must be unique
- `host` must be valid hostname or IP
- `category_id` must exist in categories table
- `probe_id` must exist in probes table

### Update Target

Modify an existing target.

**Endpoint**: `PUT /targets/{id}`

**Database Operation**:
```python
def update_target(target_id, data):
    target = session.query(Target).filter_by(id=target_id).first()
    if not target:
        return None
    
    for key, value in data.items():
        setattr(target, key, value)
    
    target.updated_at = datetime.utcnow()
    session.commit()
    return target
```

**SQL Generated**:
```sql
UPDATE targets 
SET title = 'Updated Title', 
    updated_at = NOW()
WHERE id = 1;
```

### Delete Target

Remove a target from the database.

**Endpoint**: `DELETE /targets/{id}`

**Database Operation**:
```python
def delete_target(target_id):
    target = session.query(Target).filter_by(id=target_id).first()
    if not target:
        return False
    
    session.delete(target)
    session.commit()
    return True
```

**SQL Generated**:
```sql
DELETE FROM targets WHERE id = 1;
```

### Toggle Target Status

Toggle active/inactive status without deletion.

**Endpoint**: `POST /targets/{id}/toggle`

**Database Operation**:
```python
def toggle_target(target_id):
    target = session.query(Target).filter_by(id=target_id).first()
    if not target:
        return None
    
    target.is_active = not target.is_active
    target.updated_at = datetime.utcnow()
    session.commit()
    return target
```

**SQL Generated**:
```sql
UPDATE targets 
SET is_active = NOT is_active,
    updated_at = NOW()
WHERE id = 1;
```

### Query Targets

Retrieve targets with filtering and relationships.

**Endpoint**: `GET /targets`

**Database Operation**:
```python
def get_targets(active_only=None, category=None):
    query = session.query(Target)\
        .join(Category)\
        .join(Probe)
    
    if active_only:
        query = query.filter(Target.is_active == True)
    
    if category:
        query = query.filter(Category.name == category)
    
    return query.all()
```

**SQL Generated**:
```sql
SELECT t.*, c.name as category_name, c.display_name, p.name as probe_name
FROM targets t
JOIN categories c ON t.category_id = c.id
JOIN probes p ON t.probe_id = p.id
WHERE t.is_active = true
AND c.name = 'websites'
ORDER BY t.name;
```

## Category Management

### Get Categories

Retrieve all available target categories.

**Endpoint**: `GET /categories`

**Database Operation**:
```python
def get_categories():
    return session.query(Category).order_by(Category.display_name).all()
```

**SQL Generated**:
```sql
SELECT id, name, display_name, description, created_at
FROM categories
ORDER BY display_name;
```

**Response Format**:
```json
{
  "categories": [
    {
      "id": 1,
      "name": "websites",
      "display_name": "Websites",
      "description": "Popular websites and services"
    }
  ]
}
```

### Category Statistics

Get target counts by category.

**Database Operation**:
```python
def get_category_stats():
    return session.query(
        Category.name,
        Category.display_name,
        func.count(Target.id).label('total_targets'),
        func.count(case([(Target.is_active == True, Target.id)])).label('active_targets')
    ).outerjoin(Target)\
     .group_by(Category.id, Category.name, Category.display_name)\
     .all()
```

**SQL Generated**:
```sql
SELECT c.name, c.display_name,
       COUNT(t.id) as total_targets,
       COUNT(CASE WHEN t.is_active = true THEN t.id END) as active_targets
FROM categories c
LEFT JOIN targets t ON c.id = t.category_id
GROUP BY c.id, c.name, c.display_name
ORDER BY c.display_name;
```

## Probe Management

### Get Probes

Retrieve all probe configurations.

**Endpoint**: `GET /probes`

**Database Operation**:
```python
def get_probes():
    return session.query(Probe).order_by(Probe.name).all()
```

**SQL Generated**:
```sql
SELECT id, name, binary_path, step_seconds, pings, forks, is_default, created_at
FROM probes
ORDER BY name;
```

**Response Format**:
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
    }
  ]
}
```

### Default Probe

Get the default probe for new targets.

**Database Operation**:
```python
def get_default_probe():
    return session.query(Probe).filter_by(is_default=True).first()
```

**SQL Generated**:
```sql
SELECT * FROM probes WHERE is_default = true LIMIT 1;
```

## Migration & Fallback

### Migration Detection

Check if YAML-to-database migration is complete.

**Database Operation**:
```python
def check_migration_completed():
    try:
        result = session.query(SystemMetadata)\
            .filter_by(key='yaml_migration_completed')\
            .first()
        return result is not None
    except Exception:
        return False
```

**SQL Generated**:
```sql
SELECT value FROM system_metadata 
WHERE key = 'yaml_migration_completed';
```

### Database Health Check

Verify database connectivity and schema.

**Database Operation**:
```python
def check_database_health():
    try:
        # Test basic connectivity
        session.execute('SELECT 1')
        
        # Verify tables exist
        tables = ['targets', 'categories', 'probes', 'system_metadata']
        for table in tables:
            session.execute(f'SELECT 1 FROM {table} LIMIT 1')
        
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False
```

### Fallback Activation

Automatic fallback to YAML mode when database issues occur.

**Fallback Logic**:
```python
def get_config_data():
    if self.use_database and self._check_database_available():
        try:
            return self._get_config_from_database()
        except Exception as e:
            logger.warning(f"Database error, falling back to YAML: {e}")
            return self._get_config_from_yaml()
    else:
        return self._get_config_from_yaml()
```

### Migration Status

Track migration progress and timestamps.

**Database Operation**:
```python
def record_migration_completion():
    metadata = SystemMetadata(
        key='yaml_migration_completed',
        value=datetime.utcnow().isoformat()
    )
    session.merge(metadata)
    session.commit()
```

**SQL Generated**:
```sql
INSERT INTO system_metadata (key, value, created_at, updated_at)
VALUES ('yaml_migration_completed', '2024-01-01T12:00:00.000000', NOW(), NOW())
ON CONFLICT (key) DO UPDATE SET 
    value = EXCLUDED.value,
    updated_at = NOW();
```

## Database Connections

### Connection Configuration

**Environment Variables**:
```bash
DATABASE_URL=postgresql://smokeping:${POSTGRES_PASSWORD}@postgres:5432/smokeping_targets
POSTGRES_PASSWORD=generated_secure_password
```

**SQLAlchemy Configuration**:
```python
engine = create_engine(
    database_url,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=3600
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)
```

### Connection Pooling

**Pool Settings**:
- **pool_size**: 5 connections in pool
- **max_overflow**: 10 additional connections
- **pool_timeout**: 30 seconds wait time
- **pool_recycle**: 1 hour connection lifetime

### Transaction Management

**Session Handling**:
```python
def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

# Usage with automatic cleanup
with get_session() as session:
    # Database operations
    pass
```

**Error Handling**:
```python
try:
    session.add(target)
    session.commit()
except Exception as e:
    session.rollback()
    logger.error(f"Database operation failed: {e}")
    raise
finally:
    session.close()
```

## Examples

### Target Lifecycle

```bash
# 1. Create new target
curl -X POST http://localhost:5000/targets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cloudflare",
    "host": "1.1.1.1",
    "title": "Cloudflare DNS",
    "category_id": 4,
    "probe_id": 1
  }'

# 2. Update target
curl -X PUT http://localhost:5000/targets/26 \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Cloudflare Primary DNS",
    "is_active": true
  }'

# 3. Toggle target status
curl -X POST http://localhost:5000/targets/26/toggle

# 4. Delete target
curl -X DELETE http://localhost:5000/targets/26
```

### Bulk Operations

```bash
# Get all active targets in websites category
curl "http://localhost:5000/targets?active_only=true&category=websites"

# Get target statistics by category
curl http://localhost:5000/status | jq '.database'

# List all categories with descriptions
curl http://localhost:5000/categories | jq '.categories[]'
```

### Database Administration

```bash
# Check database status
curl http://localhost:5000/status | jq '.database'

# Verify migration completion
docker exec -it grafana-influx-postgres-1 psql -U smokeping -d smokeping_targets \
  -c "SELECT * FROM system_metadata WHERE key = 'yaml_migration_completed';"

# Get table counts
docker exec -it grafana-influx-postgres-1 psql -U smokeping -d smokeping_targets \
  -c "SELECT 
        (SELECT COUNT(*) FROM targets) as targets,
        (SELECT COUNT(*) FROM categories) as categories,
        (SELECT COUNT(*) FROM probes) as probes;"
```

### Performance Monitoring

```bash
# Monitor active connections
docker exec -it grafana-influx-postgres-1 psql -U smokeping -d smokeping_targets \
  -c "SELECT COUNT(*) as active_connections FROM pg_stat_activity;"

# Check table sizes
docker exec -it grafana-influx-postgres-1 psql -U smokeping -d smokeping_targets \
  -c "SELECT 
        schemaname,
        tablename,
        pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
      FROM pg_tables 
      WHERE schemaname = 'public';"
```

## Best Practices

### Performance Optimization

1. **Use Indexes**: Queries on `name`, `category_id`, `is_active` are optimized
2. **Connection Pooling**: Reuse database connections efficiently
3. **Batch Operations**: Group multiple changes in transactions
4. **Regular Maintenance**: Monitor and maintain database health

### Data Integrity

1. **Constraints**: Database enforces referential integrity
2. **Validation**: Application-level validation before database operations
3. **Transactions**: Use transactions for multi-step operations
4. **Backups**: Regular database backups for data protection

### Monitoring

1. **Health Checks**: Regular database connectivity verification
2. **Performance Metrics**: Monitor query performance and connection usage
3. **Error Logging**: Comprehensive error logging for debugging
4. **Fallback Testing**: Regular testing of YAML fallback functionality

For implementation details and code examples, see the [Config Manager API documentation](config-manager.md).