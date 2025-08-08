# Zero-Touch IPv6/IPv4 Deployment Guide

This SmokePing deployment automatically detects IPv6 capability and configures networking accordingly for true zero-touch deployment across different environments.

## 🚀 Quick Start (Zero-Touch)

```bash
# Clone and navigate to the project
cd grafana-influx

# Run the auto-detection setup
./setup-ipv6.sh

# Start the services
docker-compose up -d
```

That's it! The system will automatically:
- ✅ Detect IPv6 availability on the host
- ✅ Test global IPv6 connectivity  
- ✅ Configure appropriate Docker networking
- ✅ Enable IPv6 monitoring if supported
- ✅ Fall back to IPv4-only if needed

## 🔍 How Auto-Detection Works

The `setup-ipv6.sh` script performs these checks:

1. **IPv6 Interface Detection**: Checks `/proc/net/if_inet6` for active IPv6 interfaces
2. **Global Connectivity Test**: Attempts to reach `2001:4860:4860::8888` (Google) and `2606:4700:4700::1111` (Cloudflare)
3. **Network Configuration**: Dynamically configures `docker-compose.yml`

## 📊 Deployment Scenarios

### Scenario 1: Full IPv6 Support
- **Detection**: Host has IPv6 interface + global connectivity
- **Result**: IPv6 networking enabled with unique ULA subnet
- **Features**: 
  - FPing6 probe available
  - IPv6 targets can be monitored
  - Google6 target works out-of-the-box

### Scenario 2: IPv4-Only Environment  
- **Detection**: No IPv6 or no global connectivity
- **Result**: IPv4-only Docker networking
- **Features**:
  - Standard FPing probe only
  - IPv6 targets automatically skipped
  - No configuration changes needed

## 🌐 Network Configuration Details

### IPv6 Enabled Configuration
```yaml
networks:
  default:
    enable_ipv6: true
    ipam:
      config:
        - subnet: "172.20.0.0/16"      # IPv4 subnet
        - subnet: "fd42:1234:5600::/64" # Unique IPv6 ULA subnet
```

### IPv4-Only Configuration
```yaml
networks:
  default:
    driver: bridge
    ipam:
      config:
        - subnet: "172.20.0.0/16"      # IPv4 subnet only
```

## 🔧 Advanced Usage

### Manual IPv6 Override
If you want to force IPv6 on/off regardless of auto-detection:

```bash
# Force IPv6 on (skip detection)
FORCE_IPV6=true ./setup-ipv6.sh

# Force IPv4 only (skip detection) 
FORCE_IPV4=true ./setup-ipv6.sh
```

### Unique Subnet Generation
Each deployment gets a unique IPv6 subnet based on:
- Hostname of the deployment system
- Current month/year
- SHA256 hash for consistency

This prevents subnet conflicts when deploying on multiple systems.

### Backup and Restore
The script automatically creates `docker-compose.yml.backup` before making changes:

```bash
# Restore original configuration
cp docker-compose.yml.backup docker-compose.yml
```

## 🎯 SmokePing Integration

### IPv6 Targets
When IPv6 is detected and enabled:
- **Google6**: `www.google.com` via FPing6 probe
- **Custom IPv6 targets**: Add via web interface with IPv6 addresses
- **Automatic probe selection**: Web form detects IPv6 and recommends FPing6

### Target Categories
- **websites**: IPv4 targets (Google, Facebook, etc.)
- **Netflix**: OCA servers (IPv4)
- **DNS_Resolvers**: Public DNS servers
- **Custom**: User-defined targets (IPv4 + IPv6)

## 🔒 Security Considerations

- Uses RFC 4193 Unique Local Addresses (ULA) for IPv6
- No external IPv6 address conflicts
- Automatic fallback to secure IPv4-only mode
- No hardcoded network configurations

## 🐛 Troubleshooting

### IPv6 Not Detected
```bash
# Check IPv6 interfaces
cat /proc/net/if_inet6

# Test global connectivity manually
ping6 -c 1 2001:4860:4860::8888

# Re-run detection
./setup-ipv6.sh
```

### Containers Can't Reach IPv6
```bash
# Verify Docker IPv6 configuration
docker network inspect grafana-influx_default

# Test container IPv6
docker exec grafana-influx-smokeping-1 fping -6 www.google.com
```

### Reset Configuration
```bash
# Restore original docker-compose.yml
cp docker-compose.yml.backup docker-compose.yml

# Re-run auto-detection
./setup-ipv6.sh
```

## 📈 Monitoring IPv6 Targets

Once IPv6 is enabled, you can:

1. **View in Grafana**: IPv6 targets appear in dashboards
2. **Add via Web Interface**: Use the Custom Form with IPv6 addresses
3. **API Integration**: IPv6 validation and probe detection built-in

The system provides zero-touch deployment while maintaining full flexibility for IPv6 and IPv4 environments.