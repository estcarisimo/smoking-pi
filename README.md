# Smoking Pi: SmokePing‑on‑Containers 🖥️📈

<div align="center">
  <img src="img/banner.png" alt="Smoking Pi Banner" width="100%"/>
</div>

A modular, multi‑flavour toolkit for running **SmokePing** latency monitoring — from a single‑container Raspberry Pi build to a full Grafana / InfluxDB stack with professional dashboards, DNS monitoring, and zero-touch deployment.

```text
.
├── minimal/           # lightweight Dockerfile (SmokePing + Lighttpd only)
├── grafana-influx/    # Full monitoring stack (SmokePing + InfluxDB + Grafana + Web Admin + Config Manager)  ← ✅ stable
└── docs/              # architecture diagrams, HOWTOs shared by all variants
```

### 30-second explainer
Smoking Pi measures your Internet connection’s **latency and packet loss** and turns them into easy-to-read graphs. Start with a single SmokePing container for quick results, or use the **InfluxDB + Grafana** stack for long-term dashboards and analysis.

**Why care?** Lower latency = snappier video calls, gaming, and web browsing. Jitter and loss explain the “it feels choppy” moments even when average latency looks fine.

> New to this? See **Network Measurements 101** below.

---

## Network Measurements 101
- **Latency (RTT):** time for a probe to go out and the reply to come back. Lower = snappier.
- **Jitter:** how much latency varies between probes. High jitter → choppy audio/video.
- **Packet loss:** when probes get no reply; even small loss can hurt real-time apps.

---

## 🚀 Quick Start Options

### Option A: Full Stack (Recommended)
Professional monitoring with Grafana dashboards, InfluxDB time-series database, web administration interface, and DNS monitoring.

```bash
cd grafana-influx/
./init-passwords.sh     # Generate secure credentials
docker-compose up -d    # Deploy full stack
```

**Access Points:**
- **Grafana**: http://localhost:3000 (admin/admin) - Professional dashboards
- **Web Admin**: http://localhost:8080 - Target management interface  
- **SmokePing**: http://localhost:8081 - Classic SmokePing interface
- **InfluxDB**: http://localhost:8086 - Time-series database

### Option B: Minimal Setup
Single-container deployment ideal for Raspberry Pi or resource-constrained environments.

```bash
cd minimal/
docker build -t smokeping:mini .
docker run -d --name smokeping -p 80:80 smokeping:mini
# browse: http://<docker-host>/cgi-bin/smokeping.cgi
```

---

## 📊 What's New in This Version

### 🆕 **PostgreSQL Database Integration** 
- **Database-First Architecture**: Centralized target management with PostgreSQL
- **Active/Inactive Status**: Toggle targets on/off without deletion
- **Normalized Schema**: All Netflix OCA metadata in proper database columns
- **Seamless Migration**: Zero-downtime transition from YAML to database
- **Hybrid Fallback**: Automatic detection with YAML backup compatibility

### ✅ **Enhanced Dashboard Organization**
- **3 Separate Dashboard Folders**: Side-by-Side Pings, Individual Pings, DNS Resolution Times
- **Professional Percentile Analysis**: P10, P20, P80, P90 latency percentiles
- **DNS Monitoring**: Dedicated dashboards for DNS resolution time analysis

### ✅ **Improved Data Pipeline** 
- **Dual Measurements**: `latency` for ping data, `dns_latency` for DNS resolution times
- **Robust Export Process**: Handles all 37+ RRD files (TopSites, Custom, Netflix, DNS)
- **Template Variable Fixes**: Dashboard dropdowns now properly populate with targets

### ✅ **Zero-Touch Deployment**
- **Web Administration**: Browser-based target management with bulk operations
- **Configuration Management**: Database-first with YAML fallback system
- **Auto-Discovery**: Netflix OCA servers, top sites from multiple ranking sources

---

## 🏗️ Architecture Overview

### Full Stack Components
```
🔐 Init-Passwords (setup) → Generates secure credentials
                                    ↓
🌐 Network Targets → 📊 SmokePing → 💾 InfluxDB → 📈 Grafana
                                  ↗
                    🔧 Config Manager ← 🌐 Web Admin
                            ↕
                      🗄️ PostgreSQL Database
```

**7 Container Architecture (6 main + setup):**
- **Init-Passwords**: Secure credential generation (runs first, then exits)
- **SmokePing**: Network latency probing (FPing + DNS + RRD export)
- **InfluxDB**: Modern time-series database for historical analysis
- **Grafana**: Professional dashboards with percentile analysis
- **Web Admin**: Target management interface with site discovery
- **Config Manager**: Database-aware configuration generation and deployment
- **PostgreSQL**: Centralized target management and metadata storage

### Data Flow
1. **Target Management**: PostgreSQL database stores all monitoring targets and metadata
2. **Configuration Generation**: Config manager reads from database to generate SmokePing config
3. **Network Probing**: SmokePing monitors active targets every 5 minutes
4. **RRD Storage**: Traditional RRD files for immediate access
5. **Data Export**: Python exporter streams data to InfluxDB in real-time
6. **Visualization**: Grafana dashboards with advanced analytics

---

## 📋 Repository Structure

| Folder                | Status         | What's inside                                                |
| --------------------- | -------------- | ------------------------------------------------------------ |
| **`minimal/`**        | ✓ stable       | Debian Slim + SmokePing + Lighttpd + fping. Ideal for Pi/VM. |
| **`grafana-influx/`** | ✓ stable       | Full monitoring stack with 5 containers and professional dashboards. |
| **`docs/`**           | ↘ shared       | Diagrams, ADRs, HOWTOs common to every flavour.              |

---

## 🎯 Monitoring Capabilities

### **Network Latency Monitoring**
- **ICMP Ping**: Traditional ping latency measurement
- **IPv6 Support**: Dual-stack monitoring for modern networks
- **Loss Tracking**: Packet loss percentage with detailed statistics

### **DNS Resolution Monitoring** 
- **Multiple Resolvers**: Google DNS (8.8.8.8), Cloudflare (1.1.1.1), Quad9 (9.9.9.9)
- **Resolution Time Analysis**: Detailed timing statistics for DNS queries
- **Separate Dashboard**: Dedicated DNS monitoring with percentile analysis

### **Target Categories**
- **TopSites**: Popular websites (Tranco, Chrome UX Report, Cloudflare Radar)
- **Custom**: User-defined targets with automatic validation
- **Netflix**: Auto-discovered Netflix OCA (Open Connect Appliance) servers
- **DNS Resolvers**: Public DNS servers for resolution time monitoring

---

## 🔧 Advanced Features

### **Professional Dashboards**
- **Side-by-Side Comparison**: Compare multiple targets on single view
- **Individual Analysis**: Detailed per-target analysis with percentiles
- **DNS Resolution Times**: Specialized DNS monitoring dashboards
- **Real-time Updates**: 30-second refresh intervals

### **Web Administration**
- **Target Discovery**: Auto-discover popular sites and Netflix OCAs
- **Bulk Operations**: Select/deselect multiple targets at once
- **Validation**: Real-time hostname/IP validation with DNS checking
- **Configuration Export**: Generate SmokePing configuration files

### **Database Management**
- **PostgreSQL Integration**: Centralized target management with normalized schema
- **Active/Inactive Targets**: Toggle monitoring without deleting configuration
- **Metadata Storage**: Complete Netflix OCA metadata in structured columns
- **YAML Compatibility**: Seamless fallback for existing deployments
- **RESTful API**: Full CRUD operations for programmatic management

### **Data Management**
- **Triple Database**: PostgreSQL for targets + RRD for immediate access + InfluxDB for analysis
- **Data Classification**: Automatic measurement classification by target type
- **Historical Analysis**: InfluxDB enables complex queries and analysis
- **Data Retention**: Configurable retention policies

---

## 🚦 Getting Started

1. **Choose Your Deployment**: Full stack for production, minimal for testing
2. **Follow Setup Guide**: Each variant has detailed setup instructions
3. **Configure Targets**: Use web admin or edit YAML configuration
4. **Access Dashboards**: View real-time monitoring data
5. **Customize**: Add your own targets and modify dashboards

---

## 📚 Documentation

- **[Full Stack Guide](grafana-influx/README.md)**: Complete setup and configuration
- **[PostgreSQL Migration Guide](POSTGRESQL_MIGRATION.md)**: Database integration and migration
- **[Architecture Details](grafana-influx/ARCHITECTURE.md)**: System design and data flow
- **[Web Admin Guide](web-admin/README.md)**: Target management interface
- **[Config Manager](config-manager/README.md)**: Configuration management
- **[Minimal Setup](minimal/README.md)**: Single-container deployment

---

## 🤝 Contributing

This project welcomes contributions! Whether you're fixing bugs, adding features, or improving documentation, your help is appreciated.

## 📄 License

This project is open source. See individual components for specific licenses.