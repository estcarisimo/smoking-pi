# SmokePing Web Administration Interface

<div align="center">
  <img src="../img/logo.jpg" alt="Smoking Pi Logo" width="100"/>
</div>

A database-first Flask web application for managing SmokePing targets through PostgreSQL with a browser interface, bulk operations, migration tools, and auto-discovery capabilities.

## Features

### 🏠 Dashboard
- Real-time SmokePing status monitoring
- Target count overview by category
- Bandwidth usage estimation
- Quick access to all management functions

### 🌐 Top Sites Picker
- **Tranco**: Research-oriented ranking resistant to manipulation
- **Chrome UX**: Based on real Chrome user experience data  
- **Cloudflare Radar**: Popular domains based on traffic patterns
- Support for country-specific lists
- Interactive selection with 100-target limit
- Bulk operations (select visible, deselect all)

### 🎯 Custom Target Management
- Add/remove custom monitoring targets
- Automatic hostname/IP validation
- IPv4/IPv6 auto-detection
- Real-time DNS resolution checking
- Target name auto-generation

### ⚙️ Configuration Management
- **Database-First Architecture**: Primary target storage in PostgreSQL
- **Active/Inactive Status**: Toggle targets without deletion via web interface
- **Migration Tools**: Seamless YAML-to-database migration with backup
- **Auto-generates SmokePing**: Configuration files from database in real-time
- **Hybrid Fallback**: Automatic detection with YAML compatibility mode
- **RESTful API**: Full CRUD operations with validation

## Architecture

```
web-admin/
├── app/
│   ├── routes/          # Flask blueprints
│   │   ├── dashboard.py # Main overview
│   │   ├── targets.py   # Custom target management  
│   │   ├── sources.py   # Top-site picker
│   │   └── api.py       # AJAX endpoints
│   ├── services/        # Business logic
│   │   ├── tranco.py    # Tranco integration
│   │   ├── crux.py      # Chrome UX integration
│   │   ├── cloudflare.py # Cloudflare Radar
│   │   └── validator.py # Input validation
│   └── templates/       # Jinja2 HTML templates
├── Dockerfile           # Container definition
├── requirements.txt     # Python dependencies
└── app.py              # Application entry point
```

## Configuration

### Database-First Mode (Default)
The web interface manages targets in **PostgreSQL database**:
- **targets**: Main target table with active/inactive status
- **target_categories**: Target categorization (dns_resolvers, top_sites, netflix_oca, custom)
- **Database schema**: Normalized with proper relationships and constraints
- **Migration support**: Automatic migration from existing YAML configurations

### YAML Fallback Mode
Automatic fallback to YAML when database unavailable:
- **sources.yaml**: Available target sources
- **targets.yaml**: Currently active targets  
- **probes.yaml**: SmokePing probe definitions

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `production` | Flask environment |
| `DATABASE_URL` | `postgresql://smokeping:password@postgres:5432/smokeping_targets` | PostgreSQL connection string |
| `CONFIG_DIR` | `/app/config` | YAML fallback configuration directory |
| `PORT` | `8080` | HTTP server port |
| `HOST` | `0.0.0.0` | Bind address |
| `SECRET_KEY` | `dev-secret-key` | Flask secret key |
| `SMOKEPING_RESTART_CMD` | `supervisorctl restart smokeping` | Command to restart SmokePing |

## Usage

### Development
```bash
cd web-admin
pip install -r requirements.txt
export FLASK_ENV=development
python app.py
```

### Production (Docker)
```bash
docker build -t smokeping-admin .
docker run -p 8080:8080 \
  -v /path/to/config:/app/config \
  -e SECRET_KEY=your-secret-key \
  smokeping-admin
```

## API Endpoints

### Status & Control
- `GET /api/status` - Get system status
- `POST /api/apply` - Apply configuration changes
- `GET /api/bandwidth` - Get bandwidth estimates

### Target Management (Database)
- `GET /api/targets` - List all targets with active/inactive status
- `POST /api/targets` - Create new target with validation
- `PUT /api/targets/<id>` - Update target (including active/inactive toggle)
- `DELETE /api/targets/<id>` - Delete target from database
- `POST /api/validate-hostname` - Validate hostname/IP with DNS checking
- `POST /api/migrate` - Migrate YAML configuration to database

### Source Integration  
- `GET /sources/api/fetch/<source>` - Fetch top sites from external sources
- `POST /sources/api/update` - Update active targets in database

## Integration with SmokePing

The web interface integrates with the database-first architecture:

### Database Mode (Default)
1. **Target Management**: Stores all targets directly in PostgreSQL database
2. **Real-time Configuration**: Config-manager reads from database to generate SmokePing files
3. **Active/Inactive Control**: Toggle targets without deletion, immediate config regeneration  
4. **Grafana Integration**: Dashboard template variables populate from same PostgreSQL database
5. **Service Restart**: Automatic SmokePing restart when database changes detected

### YAML Fallback Mode  
1. **Configuration Generation**: Updates YAML files in config-manager when database unavailable
2. **SmokePing Deployment**: Calls config-generator to update SmokePing files from YAML
3. **Migration Support**: Provides tools to migrate YAML configs to database

## Security Considerations

- Input validation for all user inputs
- DNS resolution validation for hostnames
- CSRF protection via Flask-WTF
- Rate limiting on external API calls
- Secure secret key in production

## Browser Compatibility

- Modern browsers with JavaScript enabled
- Bootstrap 5.3 for responsive design
- Progressive enhancement for core functionality