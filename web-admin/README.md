# SmokePing Web Administration Interface

<div align="center">
  <img src="../img/logo.jpg" alt="Smoking Pi Logo" width="100"/>
</div>

A lightweight Flask web application for managing SmokePing targets through a browser interface with bulk operations and auto-discovery capabilities.

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
- Centralized YAML-based configuration
- Auto-generates SmokePing format files
- Validates probe references
- One-click configuration deployment

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

The web interface manages configurations in `/app/config/`:

- **sources.yaml**: Available target sources
- **targets.yaml**: Currently active targets  
- **probes.yaml**: SmokePing probe definitions

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `production` | Flask environment |
| `CONFIG_DIR` | `/app/config` | Configuration directory |
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

### Target Management  
- `POST /api/validate-hostname` - Validate hostname/IP

### Source Integration
- `GET /sources/api/fetch/<source>` - Fetch top sites
- `POST /sources/api/update` - Update active targets

## Integration with SmokePing

The web interface integrates with the config-manager system:

1. **Configuration Generation**: Updates YAML files in config-manager
2. **SmokePing Deployment**: Calls config-generator to update SmokePing files
3. **Service Restart**: Restarts SmokePing to apply changes

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