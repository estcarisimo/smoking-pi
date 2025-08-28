# Secure Zero-Touch IPv6/IPv4 Deployment

This SmokePing deployment provides secure password generation combined with automatic IPv6 detection and configuration at runtime.

## 🚀 Quick Start (Two-Step Security)

```bash
# Step 1: Generate secure passwords (REQUIRED)
./init-passwords-docker.sh

# Step 2: Deploy the stack
docker-compose up -d

# Step 3: View your credentials
./show-passwords.sh
```

The system automatically:

- 🔐 **Generates secure passwords** for all services (InfluxDB, Grafana, PostgreSQL, Web Admin)
- ✅ **Detects IPv6 at container startup** (not at build time)
- ✅ **Tests global IPv6 connectivity** to ensure functionality  
- ✅ **Dynamically enables/disables FPing6 probe** based on capability
- ✅ **Uses host networking** for automatic IPv6 inheritance
- ✅ **Handles IPv6 targets gracefully** when IPv6 is unavailable
- ✅ **Initializes PostgreSQL database** with schema and default data
- ✅ **Configures database migrations** from existing YAML automatically

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
5. **Database Initialization**: PostgreSQL schema creation with default DNS resolvers
6. **YAML Migration**: Automatic detection and migration of existing YAML configurations

### Deployment Scenarios

| Environment | Detection Result | Behavior |
|-------------|------------------|----------|
| **IPv6-Enabled Network** | ✅ IPv6 Active | FPing6 probe enabled, Google6 target monitored, database initialized |
| **IPv4-Only Network** | ❌ No IPv6 | FPing6 disabled, IPv6 targets commented out, database initialized |
| **Corporate/Restricted** | 🔄 Auto-adapt | Graceful fallback based on connectivity, database migrations work |
| **Cloud Instance** | 🌐 Provider-dependent | Works with any cloud IPv6 configuration, PostgreSQL auto-configured |
| **Fresh Install** | 🆕 No Config | Database initialized with default DNS resolvers and target categories |
| **Existing YAML** | 📦 Migration | Automatic detection and zero-downtime migration to PostgreSQL |

## 📊 Benefits

### Secure Zero-Touch Benefits
- **Secure by default** - All passwords auto-generated, no defaults
- **Fail-safe deployment** - Docker won't start without password generation
- **No configuration files** to modify for IPv6/network setup
- **No manual environment variables** - All generated automatically
- **No network planning** required - Auto-detects capabilities
- **No database setup** - PostgreSQL schema auto-created
- **No data migration scripts** - Existing YAML configs migrate automatically

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
[entrypoint] Initializing PostgreSQL database schema…
[entrypoint] Database initialized with default target categories
[entrypoint] Checking for existing YAML configuration…
[entrypoint] YAML configuration detected - migrating to database
[entrypoint] Migration completed successfully
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