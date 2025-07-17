# 🗺️ SmokePing-InfluxDB-Grafana Architecture

## 🔄 **Data Flow Overview**

```
                      🌐 Network Targets
           ┌─────────────────────────────────────────┐
           │  • Google, Facebook, Amazon, NYT       │
           │  • Apple, UBA, Netflix OCAs            │
           │  • DNS: 8.8.8.8, 1.1.1.1, 9.9.9.9    │
           └─────────────────┬───────────────────────┘
                             │ probes every 5min
                             ▼
   ┌─────────────────────────────────────────────────────────┐
   │                  📊 SmokePing                           │
   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
   │  │    FPing    │  │     DNS     │  │   Lighttpd      │  │
   │  │   Probe     │  │   Probe     │  │   +fcgiwrap     │  │
   │  └─────┬───────┘  └─────┬───────┘  └─────────────────┘  │
   │        │                │                               │
   │        └────────────────┼─────────────────────────────┐ │
   │                         │                             │ │
   │        ┌────────────────▼─────────────────────────────┤ │
   │        │           📁 RRD Files                       │ │
   │        │  • /var/lib/smokeping/                       │ │
   │        │  • /var/lib/smokeping/resolvers/             │ │
   │        │  • median, loss, ping1-20 values            │ │
   │        └────────────────┬─────────────────────────────┘ │
   │                         │                               │
   └─────────────────────────┼───────────────────────────────┘
                             │ monitors & exports            :8080
                             ▼                               │
   ┌─────────────────────────────────────────────────────────┤
   │              🚀 RRD Exporter (Python)                   │
   │  • Watches RRD files for changes                        │
   │  • Classifies targets by directory:                     │
   │    - /resolvers/ → dns_latency measurement              │
   │    - /other/ → latency measurement                      │
   │  • Converts RRD → InfluxDB line protocol               │
   └─────────────────────────┬───────────────────────────────┘
                             │ HTTP POST
                             ▼
   ┌─────────────────────────────────────────────────────────┐
   │                  💾 InfluxDB 2.x                        │
   │  ┌─────────────────────────────────────────────────────┐│
   │  │              📊 Bucket: "latency"                   ││
   │  │                                                     ││
   │  │  Measurement: "latency"      Measurement: "dns_latency"│
   │  │  ┌─────────────────────────┐  ┌─────────────────────┐││
   │  │  │ • Amazon, Apple, etc.   │  │ • Google (DNS)      │││
   │  │  │ • Google (ping)         │  │ • Cloudflare        │││
   │  │  │ • Facebook, NYT, UBA    │  │ • Quad9             │││
   │  │  │ • Netflix OCAs          │  │                     │││
   │  │  │                         │  │ Fields: median,     │││
   │  │  │ Fields: median, loss,   │  │ loss, ping1-20      │││
   │  │  │ ping1-20 values         │  │                     │││
   │  │  └─────────────────────────┘  └─────────────────────┘││
   │  └─────────────────────────────────────────────────────┘│
   └─────────────────────────┬───────────────────────────────┘
                             │ Flux queries                  :8086
                             ▼
   ┌─────────────────────────────────────────────────────────┐
   │                   📈 Grafana                            │
   │  ┌─────────────────────────────────────────────────────┐│
   │  │                 📊 Dashboards                       ││
   │  │                                                     ││
   │  │  ┌─────────────────┐ ┌─────────────────┐ ┌─────────┐││
   │  │  │ Latency &       │ │ Side-by-Side    │ │ DNS     │││
   │  │  │ Loss            │ │ Compare         │ │ Resolvers│││
   │  │  │ (Percentiles)   │ │ (Mosaic)        │ │         │││
   │  │  │                 │ │                 │ │         │││
   │  │  │ Filters:        │ │ Filters:        │ │ Filters:│││
   │  │  │ latency targets │ │ latency targets │ │ DNS only│││
   │  │  └─────────────────┘ └─────────────────┘ └─────────┘││
   │  └─────────────────────────────────────────────────────┘│
   └─────────────────────────────────────────────────────────┘
                                                             :3000
```

## 🔄 **Data Flow Steps**

1. **🌐 Network Probing**
   - SmokePing runs FPing probes every 5 minutes for ping latency
   - DNS probe measures resolution time for DNS servers
   - Results include median, loss, and individual ping values (ping1-20)

2. **📁 RRD Storage**
   - Classic RRD files store time-series data
   - Directory structure determines classification:
     - `/var/lib/smokeping/` → ping latency targets
     - `/var/lib/smokeping/resolvers/` → DNS latency targets

3. **🚀 Data Export**
   - Python exporter monitors RRD files for changes
   - Automatically classifies targets by directory
   - Converts RRD data to InfluxDB line protocol
   - Sends HTTP POST requests to InfluxDB

4. **💾 Time-Series Database**
   - InfluxDB stores data in "latency" bucket
   - Two measurements: `latency` and `dns_latency`
   - Rich field data: median, loss, ping1-20 values
   - Efficient querying with Flux language

5. **📈 Visualization**
   - Grafana dashboards query InfluxDB using Flux
   - Three specialized dashboards with proper filtering
   - Real-time percentile analysis and comparison views

## 🎯 **Key Features**

### **🔧 Smart Classification**
- Automatic separation of ping vs DNS latency data
- Directory-based routing ensures proper data organization
- No manual configuration needed

### **📊 Rich Dashboards**
- **Latency & Loss**: Percentile analysis with filled areas
- **Side-by-Side Compare**: Mosaic view of all ping targets
- **DNS Resolvers**: Dedicated DNS performance monitoring

### **🛡️ Filtered Views**
- Dashboard queries exclude inappropriate targets
- Ping dashboards don't show DNS targets
- DNS dashboard only shows resolver data

### **🔄 Dual Storage**
- Classic RRD files for SmokePing compatibility
- Modern InfluxDB for advanced analytics
- Best of both worlds approach

## 📋 **Component Ports**

| Service | Port | Protocol | Purpose |
|---------|------|----------|---------|
| SmokePing | 8080 | HTTP | Web interface & RRD graphs |
| InfluxDB | 8086 | HTTP | Time-series database API |
| Grafana | 3000 | HTTP | Dashboard interface |

## 🔐 **Security Features**

- Generated random passwords for all services
- Environment-based configuration
- No hardcoded credentials
- Gitignored secrets management

## 🚀 **Performance Optimizations**

- Efficient RRD file monitoring
- Batched InfluxDB writes
- Grafana query caching
- Percentile pre-computation