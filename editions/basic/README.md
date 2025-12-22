# SmokePing Basic Edition

Simple network monitoring with SmokePing using the well-maintained LinuxServer.io container.

## Features

- 🎯 **Simple Setup**: Single `docker-compose up` command to start
- 📊 **Network Monitoring**: Track latency to Internet sites and local network
- 🔧 **Easy Configuration**: Edit YAML files to add/remove targets
- 🛡️ **Maintained Base**: Uses LinuxServer.io's well-maintained SmokePing image
- 🔄 **Auto Updates**: Regular updates from LinuxServer.io team

## Quick Start

### One-Command Setup

```bash
# Clone the repository
git clone <repository-url>
cd smoking-pi/editions/basic

# Run the setup script
./setup.sh
```

That's it! The setup script will:
- ✅ Generate secure passwords automatically
- ✅ Configure your timezone
- ✅ Start all services
- ✅ Display access URLs and next steps

### What the Setup Does

1. **Environment Configuration**: Automatically detects your timezone and generates a secure `.env` file
2. **Password Generation**: Creates unique passwords for all services (stored in `.env`)
3. **Service Startup**: Launches SmokePing in Docker containers
4. **Health Checks**: Verifies services are running properly

### Access the Interface

After setup completes, access SmokePing at:
- **URL**: http://localhost:8080 (or the port shown by setup)
- **Authentication**: None required for Basic edition

## Configuration

### YAML Configuration (Recommended)

The Basic edition supports easy-to-edit YAML configuration. Edit `config/targets.yaml` to add your monitoring targets:

```yaml
targets:
  my_sites:
    title: "My Websites"
    hosts:
      - name: MyWebsite
        host: example.com
        title: "My Website (example.com)"
      
      - name: MyAPI
        host: api.example.com
        title: "My API Server"
```

**Key Benefits:**
- Human-readable YAML format
- Automatic validation on startup
- Version control friendly
- No need to learn SmokePing syntax

### YAML Structure

```yaml
targets:
  group_name:                    # Group identifier (no spaces)
    title: "Display Name"        # Human-readable group name
    hosts:                       # List of hosts in this group
      - name: HostIdentifier     # Unique host name (no spaces)
        host: hostname.com       # Hostname or IP address
        title: "Display Name"    # Human-readable host name
        probe: FPing            # Optional: probe type (default: FPing)
```

### Making Changes

1. Edit `config/targets.yaml` with your preferred text editor
2. Restart the container to apply changes:
   ```bash
   docker-compose restart
   ```
3. The converter automatically validates and converts YAML to SmokePing format

### Advanced Configuration

For advanced users who prefer native SmokePing format, you can still edit `config/Targets` directly. However, any changes will be overwritten when the container restarts if `targets.yaml` exists.

### Supported Target Types

- **DNS Servers**: Monitor DNS response times
- **Web Servers**: Monitor website availability and latency  
- **Network Equipment**: Monitor routers, switches, access points
- **Cloud Services**: Monitor cloud provider endpoints

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | 1000 | User ID for file permissions |
| `PGID` | 1000 | Group ID for file permissions |
| `TZ` | UTC | Timezone for timestamps |
| `SMOKEPING_PORT` | 8080 | Web interface port |

## Monitoring Targets

The basic edition comes pre-configured with these targets in `config/targets.yaml`:

- **Internet Sites Group**:
  - Google DNS (8.8.8.8)
  - Cloudflare DNS (1.1.1.1)
  - OpenDNS (208.67.222.222)
  - Quad9 DNS (9.9.9.9)

- **Local Network Group**:
  - Default Gateway (auto-detected)
  - Home Router (192.168.1.1 - adjust as needed)

- **Popular Websites Group**:
  - Google (www.google.com)
  - GitHub (github.com)
  - Wikipedia (www.wikipedia.org)

## Upgrading

Ready for more features? Upgrade to:

- **[Standard Edition](../standard/)**: Add PostgreSQL database, web admin interface, and API management
- **[Pro Edition](../pro/)**: Full monitoring stack with Grafana dashboards and time-series database

## Data Storage

All data is stored in Docker volumes:
- `smokeping-basic-config`: SmokePing configuration
- `smokeping-basic-data`: Historical monitoring data

## Troubleshooting

### Common Issues

1. **Port already in use**: Change `SMOKEPING_PORT` in `.env` file
2. **Permission errors**: Adjust `PUID` and `PGID` in `.env` file
3. **Network connectivity**: Check Docker network configuration

### Getting Help

- Check the [SmokePing documentation](https://oss.oetiker.ch/smokeping/)
- Review [LinuxServer.io container docs](https://docs.linuxserver.io/images/docker-smokeping)
- File issues in the project repository

## Technical Details

- **Base Image**: basic-smokeping (built on linuxserver/smokeping:latest)
- **Web Interface**: Built-in SmokePing CGI interface
- **Data Format**: RRD (Round Robin Database) files
- **Update Schedule**: Automatic updates from LinuxServer.io