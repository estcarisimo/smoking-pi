# SmokePing Minimal - Network Latency Monitoring

This variant provides a **minimal Docker deployment** for SmokePing network latency monitoring. Perfect for lightweight setups that need basic SmokePing functionality without additional services.

## 🎯 What's Included

* **SmokePing** – Core latency monitoring with RRD data collection
* **fping/fping6** – IPv4/IPv6 ICMP probes  
* **lighttpd** – Lightweight web server for SmokePing interface
* **Optional Web Admin** – Target management interface (can be enabled)
* **Optional Config Manager** – Automated configuration generation (can be enabled)

## 🚀 Quick Start

### Basic SmokePing Only
```bash
cd minimal
docker-compose up -d
```
- SmokePing: http://localhost:8081

### With Web Administration  
```bash
cd minimal
# Uncomment web-admin and config-manager services in docker-compose.yml
docker-compose up -d
```
- SmokePing: http://localhost:8081
- Web Admin: http://localhost:8080

## 📋 Table of Contents

1. [Quick Deployment](#1-quick-deployment)
2. [Optional Services](#2-optional-services)
3. [Configuration](#3-configuration)
4. [Web Interface](#4-web-interface)
5. [Troubleshooting](#5-troubleshooting)
6. [Comparison with Full Stack](#6-comparison-with-full-stack)

---

## 1. Quick Deployment

### Prerequisites
- Docker & Docker Compose installed
- Ports 8081 (and optionally 8080) available

### Basic Setup
```bash
# Clone repository
git clone https://github.com/estcarisimo/smoking-pi.git
cd smoking-pi/minimal

# Start SmokePing only
docker-compose up -d smokeping

# Check status
docker-compose ps
```

### Access SmokePing
Open browser to: **http://localhost:8081/cgi-bin/smokeping.cgi**

---

## 2. Optional Services

### Enable Web Administration
Edit `docker-compose.yml` and uncomment the web-admin and config-manager sections:

```yaml
# Remove the # comments from these lines:
web-admin:
  build: ../web-admin
  ports:
    - "8080:8080"
  # ... rest of configuration

config-manager:
  build: ../config-manager
  # ... rest of configuration
```

Then restart:
```bash
docker-compose down
docker-compose up -d
```

**Access Points:**
- SmokePing: http://localhost:8081
- Web Admin: http://localhost:8080

### What Web Admin Provides
- **Target Management**: Add/remove monitoring targets
- **Configuration**: YAML-based configuration editing
- **Site Discovery**: Netflix OCA, Tranco, Chrome UX top sites
- **Restart Control**: Restart SmokePing after configuration changes

---

## 3. Configuration

### Environment Variables
Edit `.env` file:
```bash
# Timezone configuration
TZ=America/New_York

# Web Admin Interface (if enabled)
SECRET_KEY=your-secure-secret-key
```

### Custom SmokePing Config
Mount your own configuration:
```yaml
smokeping:
  volumes:
    - ./my-config:/etc/smokeping/config
    - smokeping-data:/var/lib/smokeping
```

### Data Persistence
SmokePing data is stored in the `smokeping-data` Docker volume. To backup:
```bash
docker run --rm -v smokeping-data:/data -v $(pwd):/backup alpine tar czf /backup/smokeping-backup.tar.gz -C /data .
```

---

## 4. Web Interface

### SmokePing Interface (Port 8081)
- **Graphs**: Real-time latency visualization
- **Targets**: View all monitored endpoints
- **History**: Historical latency data and trends

### Web Admin Interface (Port 8080, if enabled)
- **Dashboard**: System overview and target status
- **Targets**: Add/edit/remove monitoring targets
- **Discovery**: Find popular sites automatically
- **Configuration**: YAML config editor

---

## 5. Troubleshooting

### Common Issues

**Port Conflicts**
```bash
# Check what's using the ports
sudo netstat -tlnp | grep -E ':(8080|8081)'

# Stop conflicting services
docker stop $(docker ps -q --filter "publish=8081")
```

**No Data in Graphs**
```bash
# Check SmokePing logs
docker-compose logs smokeping

# Test connectivity
docker exec minimal-smokeping-1 fping 8.8.8.8
```

**Web Admin Access Issues**
```bash
# Ensure services are running
docker-compose ps

# Check web admin logs
docker-compose logs web-admin
```

### Service Management
```bash
# Restart services
docker-compose restart

# View logs
docker-compose logs -f

# Stop everything
docker-compose down

# Remove volumes (DATA LOSS)
docker-compose down -v
```

---

## 6. Comparison with Full Stack

| Feature | Minimal | grafana-influx |
|---------|---------|----------------|
| SmokePing | ✅ | ✅ |
| Web Admin | Optional | ✅ |
| Config Manager | Optional | ✅ |
| InfluxDB | ❌ | ✅ |
| Grafana | ❌ | ✅ |
| Resource Usage | Low | High |
| Use Case | Basic monitoring | Advanced analytics |

### When to Use Minimal
- Limited resources (Raspberry Pi)
- Simple latency monitoring needs
- No advanced visualization requirements
- Want traditional SmokePing RRD graphs

### When to Use Full Stack
- Need advanced dashboards
- Historical data analysis
- Multiple monitoring integrations
- Professional monitoring setup

---

## Architecture

```
minimal/
├── smokeping          # Core monitoring (port 8081)
├── web-admin          # Optional management (port 8080)
└── config-manager     # Optional automation
```

**Data Flow:**
1. SmokePing collects latency data → RRD files
2. Web interface serves graphs from RRD data
3. Optional web admin manages configuration
4. Optional config manager automates target discovery

---

## Support

For issues or questions:
- Check logs: `docker-compose logs`
- Review troubleshooting section above
- Compare with full stack variant if needed

**Maintainer:** Esteban Carisimo