# 🗺️ SmokePing-InfluxDB-Grafana Architecture

## 🔄 **Complete System Architecture**

```
                      🌐 Network Targets (37+ monitored)
           ┌─────────────────────────────────────────────────────────┐
           │  TopSites: Google, Facebook, Amazon, NYT, Apple        │
           │  Custom: UBA, Lisa, Google6 (IPv6)                     │
           │  Netflix: OCAs (Chicago, Washington)                   │
           │  DNS Resolvers: 8.8.8.8, 1.1.1.1, 9.9.9.9           │
           └─────────────────┬───────────────────────────────────────┘
                             │ probes every 5min
                             ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                  📊 SmokePing Container                         │
   │  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ ┌────────────┐ │
   │  │    FPing    │  │     DNS     │  │ Lighttpd  │ │    RRD     │ │
   │  │   Probe     │  │   Probe     │  │+fcgiwrap  │ │  Exporter  │ │
   │  └─────┬───────┘  └─────┬───────┘  └───────────┘ └─────┬──────┘ │
   │        │                │                              │        │
   │        └────────────────┼──────────────────────────────┼──────┐ │
   │                         │                              │      │ │
   │        ┌────────────────▼──────────────────────────────┼────┐ │ │
   │        │           📁 RRD Files (37 files)             │    │ │ │
   │        │  • /var/lib/smokeping/TopSites/               │    │ │ │
   │        │  • /var/lib/smokeping/Custom/                 │    │ │ │
   │        │  • /var/lib/smokeping/Netflix/                │    │ │ │
   │        │  • /var/lib/smokeping/DNS_Resolvers/          │    │ │ │
   │        │  • Fields: median, loss, ping1-5 values      │    │ │ │
   │        └───────────────────────────────────────────────┼────┘ │ │
   │                                                        │      │ │
   │        ┌───────────────────────────────────────────────▼────┐ │ │
   │        │        🚀 Python RRD→InfluxDB Exporter            │ │ │
   │        │  • Processes all 37 RRD files every 60s           │ │ │
   │        │  • Directory-based classification:                │ │ │
   │        │    - /DNS_Resolvers/ → dns_latency measurement    │ │ │
   │        │    - /other/ → latency measurement                │ │ │
   │        │  • Real-time export to InfluxDB                   │ │ │
   │        └────────────────┬───────────────────────────────────┘ │ │
   └─────────────────────────┼─────────────────────────────────────┘ │
                             │ HTTP POST :8086                       │
                             ▼                                       │
   ┌─────────────────────────────────────────────────────────────────┤
   │                  💾 InfluxDB 2.x Container                      │
   │  ┌─────────────────────────────────────────────────────────────┐│
   │  │              📊 Bucket: "latency"                           ││
   │  │                                                             ││
   │  │  Measurement: "latency"         Measurement: "dns_latency" ││
   │  │  ┌──────────────────────────┐    ┌─────────────────────────┐││
   │  │  │ Targets:                 │    │ Targets:                │││
   │  │  │ • Amazon, Apple, Google  │    │ • GoogleDNS             │││
   │  │  │ • Facebook, NYT, UBA     │    │ • CloudflareDNS         │││
   │  │  │ • Netflix OCAs, Lisa     │    │ • Quad9DNS              │││
   │  │  │ • Google6 (IPv6)         │    │                         │││
   │  │  │                          │    │ Fields: median, loss,   │││
   │  │  │ Fields: median, loss,    │    │ ping1-5 (seconds)       │││
   │  │  │ ping1-20 (seconds)       │    │                         │││
   │  │  └──────────────────────────┘    └─────────────────────────┘││
   │  └─────────────────────────────────────────────────────────────┘│
   └─────────────────────────┬───────────────────────────────────────┘
                             │ Flux queries :3000                     
                             ▼                                        
   ┌─────────────────────────────────────────────────────────────────┐
   │                   📈 Grafana Container                          │
   │  ┌─────────────────────────────────────────────────────────────┐│
   │  │            📊 3 Dashboard Folders                           ││
   │  │                                                             ││
   │  │  ┌──────────────┐ ┌──────────────┐ ┌───────────────────────┐││
   │  │  │Side-by-Side  │ │ Individual   │ │ DNS Resolution Times  │││
   │  │  │    Pings     │ │    Pings     │ │                       │││
   │  │  │              │ │              │ │ • GoogleDNS           │││
   │  │  │ • Multi-     │ │ • Per-target │ │ • CloudflareDNS       │││
   │  │  │   target     │ │   detailed   │ │ • Quad9DNS            │││
   │  │  │   comparison │ │   analysis   │ │                       │││
   │  │  │ • Percentiles│ │ • Percentiles│ │ • Resolution times    │││
   │  │  │   P10-P90    │ │   P10-P90    │ │ • Percentile analysis │││
   │  │  │              │ │              │ │ • Template variables  │││
   │  │  │ Query:       │ │ Query:       │ │ Query:                │││
   │  │  │ latency      │ │ latency      │ │ dns_latency           │││
   │  │  │ measurement  │ │ measurement  │ │ measurement           │││
   │  │  └──────────────┘ └──────────────┘ └───────────────────────┘││
   │  └─────────────────────────────────────────────────────────────┘│
   └─────────────────────────────────────────────────────────────────┘
                                                                     
   ┌─────────────────────────────────────────────────────────────────┐
   │              🌐 Web Admin Container :8080                       │
   │  • Target management interface                                  │
   │  • Bulk operations (select/deselect)                           │
   │  • Auto-discovery (Netflix OCA, Top Sites)                     │
   │  • Real-time validation                                        │
   └─────────────────────────────────────────────────────────────────┘
                                       
   ┌─────────────────────────────────────────────────────────────────┐
   │              🔧 Config Manager Container                        │
   │  • YAML-based configuration                                    │
   │  • SmokePing config generation                                 │
   │  • DNS resolver management                                     │
   │  • Template-based config files                                 │
   └─────────────────────────────────────────────────────────────────┘
```

## 🔄 **Enhanced Data Flow**

1. **🌐 Network Probing**
   - **FPing Probe**: Monitors 34+ targets (TopSites, Custom, Netflix) every 5 minutes
   - **DNS Probe**: Measures resolution time for 3 DNS servers (Google, Cloudflare, Quad9)
   - **Results**: median, loss, ping1-5 values for DNS, ping1-20 for ICMP

2. **📁 RRD Storage (37+ files)**
   - **Directory-based organization**:
     - `/var/lib/smokeping/TopSites/` → Website latency (Google, Facebook, Amazon, etc.)
     - `/var/lib/smokeping/Custom/` → User-defined targets (UBA, Lisa, Google6)
     - `/var/lib/smokeping/Netflix/` → Netflix OCA servers (Chicago, Washington)
     - `/var/lib/smokeping/DNS_Resolvers/` → DNS resolution timing

3. **🚀 Real-Time Data Export**
   - **Python RRD Exporter**: Processes all 37 RRD files every 60 seconds
   - **Automatic Classification**:
     - DNS_Resolvers directory → `dns_latency` measurement
     - All other directories → `latency` measurement
   - **InfluxDB Integration**: Converts RRD data to InfluxDB line protocol
   - **Error Handling**: Comprehensive logging and error recovery

4. **💾 Dual-Measurement Database**
   - **InfluxDB Bucket**: "latency" contains both measurements
   - **Measurement Structure**:
     - `latency`: 34+ targets (Amazon, Apple, Google, Netflix OCAs, etc.)
     - `dns_latency`: 3 DNS targets (GoogleDNS, CloudflareDNS, Quad9DNS)
   - **Fields**: median, loss, ping1-5/ping1-20, uptime
   - **Tags**: target, category, probe_type

5. **📈 Dashboard Organization**
   - **3 Separate Folders**:
     - Side-by-Side Pings: Multi-target comparison from `latency` measurement
     - Individual Pings: Per-target analysis from `latency` measurement  
     - DNS Resolution Times: DNS-specific dashboards from `dns_latency` measurement
   - **Template Variables**: Auto-populated from InfluxDB tag values
   - **Real-time Updates**: 30-second refresh intervals
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