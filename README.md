# SmokePing Network Monitoring - Multi-Edition

<div align="center">
  <img src="img/logo.jpg" alt="Smoking Pi Logo" width="150"/>
  
  **Professional Network Monitoring with Three Deployment Options**
</div>

## 🎯 Choose Your Edition

SmokePing now comes in three editions to match your needs and infrastructure:

| Edition | Use Case | Features | Resources |
|---------|----------|----------|-----------|
| **[Basic](editions/basic/)** | Simple monitoring | LinuxServer SmokePing + YAML config | Minimal |
| **[Standard](editions/standard/)** | Team management | Web admin + PostgreSQL + API | Low-Medium |  
| **[Pro](editions/pro/)** | Advanced monitoring | Full stack + Grafana + Time-series DB | Medium-High |

### 🆕 What's New

- **Unified Setup**: All editions now use `./setup.sh` for consistent experience
- **Auto-Configuration**: Passwords, timezone, and environment automatically configured
- **Database Choice**: Pro edition supports both InfluxDB and ClickHouse
- **YAML Config**: Basic edition uses simple YAML files for target management

## 🚀 Quick Start

### 1. Choose Your Edition

All editions use a unified `setup.sh` script that handles everything automatically:

```bash
# Basic - Simple monitoring with YAML configuration
cd editions/basic
./setup.sh

# Standard - Web management with PostgreSQL  
cd editions/standard
./setup.sh

# Pro - Full monitoring stack (with InfluxDB)
cd editions/pro
./setup.sh

# Pro - With ClickHouse database
cd editions/pro
./setup.sh --database clickhouse
```

The setup script will:
- 🔐 Generate secure passwords automatically
- ⏰ Detect and configure your timezone
- 🐳 Start all required Docker containers
- ✅ Verify services are running properly
- 📋 Display access URLs and credentials

### 2. Quick Maintenance

```bash
# Stop all containers
docker stop $(docker ps -a | grep smokeping | awk '{print $1}')

# Clean up resources
docker-compose -f editions/<edition>/docker-compose.yml down -v

# See full guide: shared/docs/maintenance.md
```

## 📊 Edition Comparison

### 🟢 Basic Edition
- **Perfect for**: Home users, simple setups
- **Container**: LinuxServer.io SmokePing (well-maintained)
- **Configuration**: Easy-to-edit YAML files with automatic validation
- **Interface**: Classic SmokePing web interface
- **Database**: RRD files
- **Setup time**: 2 minutes

**YAML Configuration Benefits:**
- Human-readable format (no complex SmokePing syntax)
- Automatic validation on startup
- Version control friendly
- Edit `config/targets.yaml` to add/remove monitoring targets

### 🟡 Standard Edition  
- **Perfect for**: Small teams, managed environments
- **Features**: Web admin, PostgreSQL database, REST API
- **Configuration**: Database-driven with web interface
- **Management**: Target management, bulk operations
- **Authentication**: Secure login system
- **Setup time**: 5 minutes

### 🔴 Pro Edition
- **Perfect for**: Advanced monitoring
- **Features**: Everything + Grafana dashboards, InfluxDB
- **Monitoring**: Advanced metrics, IPv6, DNS timing
- **Dashboards**: Professional visualizations, percentile analysis
- **Time-series**: InfluxDB or ClickHouse support
- **Integrations**: Netflix CDN monitoring, OCA endpoints
- **Setup time**: 10 minutes

## 🏗️ Architecture

```
smoking-pi/
├── editions/
│   ├── basic/           # SmokePing + YAML configuration
│   ├── standard/        # + PostgreSQL + Web Admin + API
│   └── pro/             # + Grafana + InfluxDB/ClickHouse + Advanced monitoring
├── shared/
│   ├── modules/         # Reusable Docker containers
│   │   ├── smokeping/   # Custom SmokePing images
│   │   ├── config-manager/  # Configuration API service
│   │   ├── web-admin/   # Web management interface
│   │   ├── grafana/     # Custom Grafana with dashboards
│   │   ├── postgres/    # PostgreSQL with initialization
│   │   ├── influxdb/    # InfluxDB time-series database
│   │   └── smokeping-exporters/  # RRD to time-series exporters
│   ├── scripts/         # Utility scripts (setup, management, tunnels)
│   └── cloudflare-tunnel/   # Secure remote access configuration
└── README.md           # Project documentation
```

## 🔒 Security Features

- **Auto-generated passwords** for all services
- **Secure cookie signing** for web interfaces
- **PostgreSQL authentication** with strong passwords
- **Cloudflare Tunnel integration** for secure remote access
- **Docker network isolation** between editions

## 🔄 Database Choice (Pro Edition)

The Pro Edition supports both InfluxDB and ClickHouse as time-series databases:

```bash
# Use InfluxDB (default)
echo "TSDB_TYPE=influxdb" >> .env

# Or use ClickHouse for better performance
echo "TSDB_TYPE=clickhouse" >> .env
docker-compose -f docker-compose.yml -f docker-compose.clickhouse.yml up -d
```

**InfluxDB**: Industry standard, easier setup, Flux query language  
**ClickHouse**: Higher performance, SQL queries, better compression

## 🌐 Remote Access

### Quick Access (No Account Required!)

Get instant remote access with temporary URLs:

```bash
# Create temporary tunnel URLs
./shared/scripts/create-tunnel.sh

# Your services will be available at:
# SmokePing: https://[random].trycloudflare.com
# Web Admin: https://[random].trycloudflare.com  
# Grafana:   https://[random].trycloudflare.com (Pro only)
```

**Quick Tunnels Guide**: [Quick Tunnels Documentation](shared/docs/quick-tunnels.md)

### Permanent Access (Requires Cloudflare Account)

For production use with custom domains:

```bash
# Set up permanent tunnel
cd shared/cloudflare-tunnel
cp .env.template .env
# Add your CLOUDFLARE_TUNNEL_TOKEN
docker-compose up -d
```

**Detailed Setup**: [Cloudflare Tunnel Documentation](shared/docs/cloudflare-tunnel-setup.md)

## 🔧 Utility Scripts Reference

All editions include powerful utility scripts for easy management:

### Container Management

**`manage-containers.sh`** - Complete container lifecycle management
```bash
# Usage examples
./shared/scripts/manage-containers.sh --action start [--edition basic] [--service smokeping]
./shared/scripts/manage-containers.sh --action stop --edition pro
./shared/scripts/manage-containers.sh --action restart --volumes
./shared/scripts/manage-containers.sh --action status --verbose
./shared/scripts/manage-containers.sh --action logs --service grafana
./shared/scripts/manage-containers.sh --action remove --volumes --dry-run
```

**Features:**
- **Actions**: start, stop, restart, remove, status, logs
- **Edition Detection**: Automatically detects current edition or specify with `--edition`
- **Service-Specific**: Target individual services with `--service`
- **Volume Management**: Include/exclude volumes with `--volumes`
- **Safety Features**: Dry-run mode and verbose output
- **Pro Integration**: Special handling for InfluxDB token synchronization

### Credentials and Access

**`show-passwords.sh`** - Display all service credentials and access information
```bash
# From any edition directory
./show-passwords.sh

# From anywhere in project
./shared/scripts/show-passwords.sh
```

**Shows:**
- **Web Admin**: URL, username, password (Standard/Pro)
- **Grafana**: URL, admin credentials (Pro only)
- **InfluxDB**: URL, admin password, API token (Pro only)
- **PostgreSQL**: Database credentials (Standard/Pro)
- **SmokePing**: Web interface URL (all editions)
- **Health Checks**: Service status and troubleshooting tips

### Remote Access (Tunnels)

**`create-tunnel.sh`** - Temporary Cloudflare tunnel management
```bash
# Create and start tunnel for current edition
./shared/scripts/create-tunnel.sh create

# Start existing tunnel
./shared/scripts/create-tunnel.sh start

# Stop tunnel
./shared/scripts/create-tunnel.sh stop

# Show tunnel status
./shared/scripts/create-tunnel.sh status

# Show available commands
./shared/scripts/create-tunnel.sh help
```

**`show-tunnel-urls.sh`** - Display active tunnel information
```bash
# Show all active tunnels with status
./shared/scripts/show-tunnel-urls.sh
```

**Features:**
- **No Account Required**: Uses temporary *.trycloudflare.com URLs
- **Auto-Detection**: Automatically detects running edition and services
- **Service-Specific**: Creates tunnels for all available services
- **Status Monitoring**: Color-coded tunnel status and management commands

### Usage Examples

```bash
# Complete workflow example
cd editions/standard
./setup.sh                                          # Start edition
./show-passwords.sh                                 # Get credentials
../shared/scripts/create-tunnel.sh create           # Create remote access
../shared/scripts/show-tunnel-urls.sh               # View tunnel URLs
../shared/scripts/manage-containers.sh --action status --verbose  # Check status
../shared/scripts/manage-containers.sh --action stop              # Stop when done
```

## 🔄 Edition Management

### Upgrade Path
```
Basic → Standard → Pro
```

### Edition Switching

Simply start the desired edition after stopping the current one:

```bash
# Stop current edition
docker-compose down

# Switch to different edition
cd ../pro && ./setup.sh --database clickhouse
```

## 📚 Documentation

- **[Basic Edition](editions/basic/README.md)** - Simple setup guide with YAML configuration
- **[Standard Edition](editions/standard/README.md)** - Web management with PostgreSQL database
- **[Pro Edition](editions/pro/README.md)** - Full monitoring stack with Grafana dashboards
- **[Utility Scripts](shared/scripts/)** - Container management, credentials, and tunnel scripts
- **[Quick Tunnels](shared/docs/quick-tunnels.md)** - Instant remote access (no account needed)
- **[Cloudflare Tunnels](shared/docs/cloudflare-tunnel-setup.md)** - Permanent remote access setup

## 🛠️ Development

### Code Quality
- **Python**: Modern stack with `uv`, Pydantic models, type hints
- **Documentation**: NumPy-style docstrings throughout
- **Linting**: Automated code quality with ruff
- **Testing**: Comprehensive test coverage

### Contributing
1. Fork the repository
2. Create feature branch: `git checkout -b feature/amazing-feature`
3. Make changes and add tests
4. Follow code quality standards
5. Submit pull request

## 🔧 Troubleshooting

### Common Issues

| Problem | Solution |
|---------|----------|
| Port conflicts | Change ports in `.env` file |
| Permission errors | Check Docker group membership |
| Database connection | Verify passwords in `.env` |
| Services not starting | Check Docker logs: `docker-compose logs` |
| Stuck containers/networks | See [Maintenance Guide](shared/docs/maintenance.md) |

### Getting Help

- 📖 **Documentation**: Check edition-specific READMEs
- 🐛 **Issues**: GitHub Issues for bug reports
- 💬 **Discussions**: GitHub Discussions for questions
- 📧 **Security**: security@smoking-pi.dev for security issues

## 📈 Monitoring Capabilities

### All Editions
- ✅ Network latency monitoring
- ✅ Packet loss detection  
- ✅ Historical data storage
- ✅ Web interface graphs

### Standard & Pro Only
- ✅ Database-driven configuration
- ✅ Web admin interface
- ✅ REST API for automation
- ✅ Bulk target management

### Pro Only
- ✅ Advanced Grafana dashboards
- ✅ Time-series database (InfluxDB/ClickHouse)
- ✅ IPv6 monitoring support
- ✅ DNS resolution timing
- ✅ Netflix CDN monitoring
- ✅ Percentile analysis
- ✅ Multi-probe support

## 🏆 Why SmokePing Multi-Edition?

- **🎯 Right-sized**: Choose features that match your needs
- **📈 Scalable**: Upgrade as your requirements grow
- **🔒 Secure**: Auto-generated passwords and secure defaults
- **🛠️ Maintained**: Built on well-maintained base images
- **🌐 Accessible**: Remote access built-in
- **📊 Professional**: Enterprise-grade monitoring capabilities

---

<div align="center">
  <b>Start with Basic, grow to Pro</b><br>
  Professional network monitoring for everyone
</div>