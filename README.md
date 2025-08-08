# Smoking Pi: SmokePing‑on‑Containers 🖥️📈

<div align="center">
  <img src="img/logo.jpg" alt="Smoking Pi Logo" width="200"/>
</div>

A modular, multi‑flavour toolkit for running **SmokePing** latency monitoring — from a single‑container Raspberry Pi build to a full Grafana / InfluxDB stack with professional dashboards, DNS monitoring, and zero-touch deployment.

```text
.
├── minimal/           # lightweight Dockerfile (SmokePing + Lighttpd only)
├── grafana-influx/    # Full monitoring stack (SmokePing + InfluxDB + Grafana + Web Admin + Config Manager)  ← ✅ stable
└── docs/              # architecture diagrams, HOWTOs shared by all variants
```

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
- **Configuration Management**: Centralized YAML-based configuration system
- **Auto-Discovery**: Netflix OCA servers, top sites from multiple ranking sources

---

## 🏗️ Architecture Overview

### Full Stack Components
```
🌐 Network Targets → 📊 SmokePing → 💾 InfluxDB → 📈 Grafana
                                  ↗
                    🔧 Config Manager ← 🌐 Web Admin
```

**5 Container Architecture:**
- **SmokePing**: Network latency probing (FPing + DNS + RRD export)
- **InfluxDB**: Modern time-series database for historical analysis
- **Grafana**: Professional dashboards with percentile analysis
- **Web Admin**: Target management interface with site discovery
- **Config Manager**: Automated configuration generation and deployment

### Data Flow
1. **Network Probing**: SmokePing monitors targets every 5 minutes
2. **RRD Storage**: Traditional RRD files for immediate access
3. **Data Export**: Python exporter streams data to InfluxDB in real-time
4. **Visualization**: Grafana dashboards with advanced analytics

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

### **Data Management**
- **Dual Database**: RRD files for immediate access + InfluxDB for analysis
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
- **[Architecture Details](grafana-influx/ARCHITECTURE.md)**: System design and data flow
- **[Web Admin Guide](web-admin/README.md)**: Target management interface
- **[Config Manager](config-manager/README.md)**: Configuration management
- **[Minimal Setup](minimal/README.md)**: Single-container deployment

---

## 🤝 Contributing

This project welcomes contributions! Whether you're fixing bugs, adding features, or improving documentation, your help is appreciated.

## 📄 License

This project is open source. See individual components for specific licenses.