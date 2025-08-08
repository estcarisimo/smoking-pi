# True Zero-Touch IPv6/IPv4 Deployment

This SmokePing deployment automatically detects and configures IPv6 support **at runtime** with no setup scripts required.

## 🚀 Zero-Touch Quick Start

```bash
# That's it - truly zero-touch!
docker-compose up -d
```

No scripts to run, no configuration to modify, no environment detection needed. The system automatically:

- ✅ **Detects IPv6 at container startup** (not at build time)
- ✅ **Tests global IPv6 connectivity** to ensure functionality  
- ✅ **Dynamically enables/disables FPing6 probe** based on capability
- ✅ **Uses host networking** for automatic IPv6 inheritance
- ✅ **Handles IPv6 targets gracefully** when IPv6 is unavailable

## 🔍 How It Works

### Host Network Mode
- SmokePing container uses `network_mode: host`
- Automatically inherits **all** host network capabilities
- IPv6 addresses, routes, and connectivity are identical to host
- No Docker network configuration needed

### Runtime Detection
The container entrypoint automatically:

1. **IPv6 Interface Check**: Scans `/proc/net/if_inet6` for active interfaces
2. **Connectivity Test**: Pings `2001:4860:4860::8888` and `2606:4700:4700::1111` 
3. **Dynamic Configuration**: Adds/removes FPing6 probe as needed
4. **Target Management**: Enables/disables IPv6 targets based on capability

### Deployment Scenarios

| Environment | Detection Result | Behavior |
|-------------|------------------|----------|
| **IPv6-Enabled Network** | ✅ IPv6 Active | FPing6 probe enabled, Google6 target monitored |
| **IPv4-Only Network** | ❌ No IPv6 | FPing6 disabled, IPv6 targets commented out |
| **Corporate/Restricted** | 🔄 Auto-adapt | Graceful fallback based on actual connectivity |
| **Cloud Instance** | 🌐 Provider-dependent | Works with any cloud IPv6 configuration |

## 📊 Benefits

### True Zero-Touch
- **No setup scripts** - Just `docker-compose up -d`
- **No configuration files** to modify
- **No environment variables** to set
- **No network planning** required

### Environment Portability  
- **Same docker-compose.yml** works everywhere
- **Raspberry Pi** → Cloud → Corporate networks
- **IPv6-enabled ISPs** and **IPv4-only environments**
- **Dynamic cloud** instances with changing IP configurations

### Graceful Degradation
- **IPv6 targets** automatically disabled when unavailable
- **No failed probes** or error messages
- **Consistent behavior** regardless of network environment
- **Performance optimized** - only runs what's supported

## 🔧 Technical Implementation

### Container Startup Flow
```bash
[entrypoint] Configuring IPv6 support (zero-touch detection)…
[entrypoint] IPv6 detected and reachable - enabling FPing6 probe
[entrypoint] FPing6 probe added to configuration
[entrypoint] Starting SmokePing…
```

### Configuration Changes
- **IPv6 Available**: FPing6 probe added dynamically
- **IPv4 Only**: IPv6 targets commented out automatically
- **No persistent changes** - configuration resets on restart

### Host Network Benefits
- **Direct IPv6 access** - no Docker networking complexity
- **Same performance** as running on host
- **Full protocol support** - IPv6, IPv4, multicast, etc.
- **DNS resolution** identical to host

## 🐛 Troubleshooting

### Check Container IPv6 Status
```bash
# View detection logs
docker logs grafana-influx-smokeping-1 | grep IPv6

# Test IPv6 from inside container
docker exec grafana-influx-smokeping-1 ping6 -c 1 2001:4860:4860::8888
```

### Force IPv6 Re-detection
```bash
# Restart container to re-run detection
docker-compose restart smokeping
```

### Verify Host IPv6
```bash
# Check host IPv6 interfaces
cat /proc/net/if_inet6

# Test host IPv6 connectivity  
ping6 -c 1 google.com
```

## 🎯 SmokePing Integration

### Automatic Target Management
- **Google6**: Enabled automatically when IPv6 is detected
- **Custom IPv6 targets**: Added via web interface work seamlessly
- **Probe selection**: Web form automatically detects IPv6 capability
- **Dashboard data**: IPv6 metrics appear without configuration

### Service Dependencies
- **InfluxDB**: Standard Docker networking (IPv4)
- **Grafana**: Standard Docker networking (IPv4)  
- **Web Admin**: Standard Docker networking (IPv4)
- **SmokePing**: Host networking (IPv4 + IPv6)

This approach provides the ultimate in zero-touch deployment while maintaining full IPv6 capability when available.