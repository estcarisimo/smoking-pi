# 🌐 DNS Resolution Monitoring Guide

## Overview

The SmokePing Full Stack includes comprehensive DNS resolution monitoring with dedicated dashboards, specialized data collection, and troubleshooting capabilities.

## 🎯 DNS Monitoring Features

### **DNS Resolver Targets**
- **Google DNS**: 8.8.8.8 (resolving google.com)
- **Cloudflare DNS**: 1.1.1.1 (resolving cloudflare.com)  
- **Quad9 DNS**: 9.9.9.9 (resolving quad9.net)

### **Metrics Collected**
- **Resolution Time**: DNS query response time in seconds
- **Packet Loss**: Percentage of failed DNS queries
- **Individual Measurements**: ping1-ping5 for statistical analysis
- **Percentile Analysis**: P10, P20, P80, P90 resolution times

## 📊 Dashboard Organization

### **Dedicated DNS Folder**
The DNS Resolution Times folder contains specialized dashboards:

1. **DNS Resolvers – Resolution Time & Loss (Percentiles/Mean)**
   - Multi-panel view with one panel per DNS resolver
   - Real-time percentile analysis (P10-P90)
   - Median and mean resolution times
   - Packet loss percentage tracking

2. **Template Variables**
   - Auto-populated target dropdown (GoogleDNS, CloudflareDNS, Quad9DNS)
   - Dynamic panel creation based on selected targets
   - Multi-select support for comparing resolvers

## 🔧 Technical Implementation

### **Data Classification**
```yaml
# Automatic measurement assignment
/var/lib/smokeping/DNS_Resolvers/*.rrd → dns_latency measurement
/var/lib/smokeping/other/*.rrd → latency measurement
```

### **InfluxDB Schema**
```
Measurement: dns_latency
├── Tags:
│   ├── target: GoogleDNS | CloudflareDNS | Quad9DNS
│   ├── category: dns
│   └── probe_type: dns
└── Fields:
    ├── median: DNS resolution time (seconds)
    ├── loss: Packet loss percentage (0-100)
    ├── ping1-5: Individual measurement samples
    └── uptime: Probe uptime (optional)
```

### **Dashboard Queries**
```flux
// Template variable query
v1.tagValues(
    bucket: "latency", 
    predicate: (r) => r._measurement == "dns_latency", 
    tag: "target"
)

// Median resolution time
from(bucket: "latency")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "dns_latency" and r.target == "${target}")
  |> filter(fn: (r) => r._field == "median")
  |> map(fn: (r) => ({r with _value: r._value * 1000.0})) // Convert to milliseconds
```

## 🚦 Troubleshooting DNS Monitoring

Run everything below from `editions/pro`, so Compose resolves the containers
itself rather than anyone having to know what they are called — the container
names depend on the Compose project, which is `COMPOSE_PROJECT_NAME` from the
env file or, unset, this directory. `smoking-pi status` and
`smoking-pi restart` do the same from anywhere.

### **No Data in DNS Dashboards**

**Symptom**: DNS Resolution Times dashboards show no data or empty panels.

**Diagnostic Steps**:

1. **Verify DNS Probe Status**
   ```bash
   docker compose logs smokeping | grep DNS
   # Should show: "DNS: probing 3 targets with step 300 s"
   ```

2. **Check RRD Files**
   ```bash
   docker compose exec -T smokeping ls -la /var/lib/smokeping/DNS_Resolvers/
   # Should show: CloudflareDNS.rrd, GoogleDNS.rrd, Quad9DNS.rrd
   ```

3. **Verify Export Process**
   ```bash
   docker compose exec -T smokeping ps aux | grep rrd2influx
   # Should show running Python process
   ```

4. **Check InfluxDB Data**
   ```bash
   docker compose exec -T influxdb influx query \
     'from(bucket: "latency") |> range(start: -1h) |> filter(fn: (r) => r._measurement == "dns_latency") |> limit(n: 5)' \
     --org smokingpi
   ```

**Common Fixes**:

- **Template Variable Issues**: Check dashboard template variable configuration
- **Time Range**: Expand dashboard time range to "Last 24 hours"
- **Dashboard Refresh**: Refresh browser or restart Grafana container
- **Export Process**: Restart SmokePing container if export process hangs

### **High DNS Resolution Times**

**Symptom**: DNS resolution times consistently above 100ms.

**Investigation**:
- Check network connectivity to DNS servers
- Verify DNS server responsiveness from host system
- Review SmokePing DNS probe configuration

### **Missing DNS Targets**

**Symptom**: Template variable dropdown shows fewer than 3 DNS targets.

**Fixes**:
1. **Check Configuration**:
   ```bash
   docker compose exec -T smokeping cat /etc/smokeping/config.d/targets | grep -A5 "DNS"
   ```

2. **Restart Services**:
   ```bash
   docker compose restart smokeping grafana
   ```

3. **Verify Template Variable Query**:
   - Access Grafana → Dashboard Settings → Variables
   - Test template variable query in Grafana Explore

## 📈 Performance Optimization

### **Recommended Settings**
- **Refresh Interval**: 30 seconds for real-time monitoring
- **Query Timeout**: 30 seconds for complex percentile calculations
- **Data Retention**: 30 days minimum for trend analysis

### **Dashboard Customization**
```flux
// Custom percentile calculation
from(bucket: "latency")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "dns_latency")
  |> filter(fn: (r) => r._field =~ /ping[0-9]+/)
  |> quantile(q: 0.95, method: "exact_mean") // 95th percentile
```

## 🔍 Advanced Analysis

### **Compare DNS Resolvers**
Use Side-by-Side Pings dashboard with DNS-specific filters:
```flux
r._measurement == "dns_latency" and r.target =~ /(GoogleDNS|CloudflareDNS|Quad9DNS)/
```

### **Resolution Time Trends**
- Monitor 24-hour trends for DNS performance
- Set up alerts for resolution times > 50ms
- Compare weekday vs weekend DNS performance

### **Geographic Analysis**
- Add custom DNS resolvers for regional comparison
- Monitor resolution times from different geographic locations
- Analyze DNS performance during peak hours

## 📚 Additional Resources

- [SmokePing DNS Probe Documentation](https://oss.oetiker.ch/smokeping/doc/smokeping_probes.en.html#DNS)
- [InfluxDB Flux Language Guide](https://docs.influxdata.com/influxdb/latest/query-data/flux/)
- [Grafana Template Variables](https://grafana.com/docs/grafana/latest/variables/)