# SmokePing Quick Tunnels Guide

Quick Tunnels provide instant remote access to your SmokePing deployment without requiring a Cloudflare account, domain, or any configuration. This guide explains how to use this feature.

## What are Quick Tunnels?

Quick Tunnels (also known as TryCloudflare) create temporary public URLs that tunnel to your local services. They're perfect for:
- 🚀 **Demos and Testing** - Share your setup instantly
- 🔧 **Remote Troubleshooting** - Access from anywhere
- 📱 **Mobile Access** - Check metrics on the go
- 🎯 **Proof of Concepts** - No setup required

## Key Features

- **No Account Required** - No Cloudflare login needed
- **Instant URLs** - Get public URLs in seconds
- **Zero Configuration** - Works out of the box
- **Edition-Aware** - Automatically detects your SmokePing edition
- **Secure** - HTTPS encryption for all connections

## Quick Start

### 1. Start Your SmokePing Edition

First, ensure you have a SmokePing edition running:

```bash
# For Basic Edition
cd editions/basic
./setup.sh

# For Standard Edition  
cd editions/standard
docker-compose up -d

# For Pro Edition
cd editions/pro
./init-passwords.sh && docker-compose up -d
```

### 2. Create Quick Tunnels

Run the tunnel creation script from anywhere:

```bash
./shared/scripts/create-tunnel.sh
```

You'll see output like:
```
🚀 Creating Quick Tunnels for SmokePing Pro Edition
═══════════════════════════════════════════════════════════
🚇 Starting tunnel for SmokePing Interface...
   ✅ SmokePing Interface: https://brief-texts-matter-actively.trycloudflare.com
🚇 Starting tunnel for Web Administration...
   ✅ Web Administration: https://proud-dolls-buy-gently.trycloudflare.com
🚇 Starting tunnel for Grafana Dashboard...
   ✅ Grafana Dashboard: https://quick-foxes-jump-highly.trycloudflare.com

🌐 Quick Tunnel URLs for SmokePing Pro Edition
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SmokePing Interface: https://brief-texts-matter-actively.trycloudflare.com
Web Administration:  https://proud-dolls-buy-gently.trycloudflare.com
Grafana Dashboard:   https://quick-foxes-jump-highly.trycloudflare.com
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  Note: These are temporary URLs that will change on restart
    For permanent URLs, use the token-based setup instead.
```

### 3. Access Your Services

Simply click or copy the URLs to access your services from anywhere!

## Edition-Specific Services

### Basic Edition
- **SmokePing Interface** - View network latency graphs

### Standard Edition
- **SmokePing Interface** - View network latency graphs
- **Web Administration** - Manage targets and configuration

### Pro Edition
- **SmokePing Interface** - View network latency graphs
- **Web Administration** - Manage targets and configuration
- **Grafana Dashboard** - Advanced analytics and visualizations

## Tunnel Management Commands

### View Current Tunnels
```bash
./shared/scripts/create-tunnel.sh status
# or
./shared/scripts/show-tunnel-urls.sh
```

### Stop All Tunnels
```bash
./shared/scripts/create-tunnel.sh stop
```

### Recreate Tunnels
```bash
./shared/scripts/create-tunnel.sh create
```

### Get Help
```bash
./shared/scripts/create-tunnel.sh help
```

## Important Notes

### Temporary Nature
- URLs are **ephemeral** and change every time tunnels are recreated
- Tunnels may timeout after extended periods of inactivity
- Not suitable for production use or permanent access

### Security Considerations
- Quick Tunnels provide **transport encryption** (HTTPS)
- **No authentication** at the tunnel level
- Services use their own authentication:
  - Basic Edition: No auth (consider this for public data only)
  - Standard Edition: Web admin has login protection
  - Pro Edition: Both web admin and Grafana have authentication

### Performance
- Tunnels add some latency due to routing through Cloudflare
- Bandwidth may be limited compared to direct access
- Perfect for monitoring and management, not high-traffic scenarios

## Comparison with Token-Based Tunnels

| Feature | Quick Tunnels | Token-Based Tunnels |
|---------|--------------|-------------------|
| **Setup Required** | None | Cloudflare account + domain |
| **URL Type** | Random *.trycloudflare.com | Custom subdomain.yourdomain.com |
| **Persistence** | Temporary (changes on restart) | Permanent |
| **Authentication** | Service-level only | Can add Cloudflare Access policies |
| **Use Case** | Testing, demos, temporary access | Production, permanent deployment |

## Troubleshooting

### No SmokePing Edition Running
```
❌ No SmokePing edition is currently running!
Please start a SmokePing edition first...
```
**Solution**: Start one of the SmokePing editions before creating tunnels.

### Tunnel Creation Fails
- Check Docker is running: `docker ps`
- Check network connectivity: `ping 1.1.1.1`
- Check firewall allows outbound HTTPS (port 443)

### URLs Not Showing
- Wait a few seconds for tunnel initialization
- Check container logs: `docker logs tunnel-smokeping`
- Try recreating tunnels: `./shared/scripts/create-tunnel.sh stop && ./shared/scripts/create-tunnel.sh`

### Can't Access Services Through Tunnel
- Verify local access works first: `curl http://localhost:[port]`
- Check service is actually running: `docker ps`
- Ensure you're using the exact URL shown (including https://)

## Advanced Usage

### Custom Networks
The script automatically detects the correct Docker network based on your edition:
- Basic: Uses default bridge network
- Standard: Uses `editions_standard_smokeping-net`
- Pro: Uses `editions_pro_default`

### Running Multiple Editions
Quick Tunnels detect which edition is running. To switch editions:
1. Stop current edition
2. Start new edition
3. Recreate tunnels

### Integration with Scripts
You can integrate Quick Tunnels into your automation:

```bash
# Start edition and create tunnels
cd editions/pro
docker-compose up -d
../../shared/scripts/create-tunnel.sh

# Get URLs programmatically
SMOKEPING_URL=$(docker logs tunnel-smokeping 2>&1 | grep -o 'https://.*\.trycloudflare\.com' | head -1)
echo "SmokePing is available at: $SMOKEPING_URL"
```

## For Production Use

While Quick Tunnels are great for temporary access, consider these options for production:

1. **Token-Based Cloudflare Tunnels** - See [Cloudflare Tunnel Setup Guide](cloudflare-tunnel-setup.md)
2. **VPN Access** - Set up WireGuard or OpenVPN
3. **Reverse Proxy** - Use Nginx or Traefik with Let's Encrypt

## Summary

Quick Tunnels provide the fastest way to get remote access to your SmokePing deployment:
- ✅ No account or setup required
- ✅ Works with all SmokePing editions
- ✅ Secure HTTPS connections
- ✅ Perfect for testing and demos

Just remember they're temporary - for permanent access, use the token-based tunnel setup!