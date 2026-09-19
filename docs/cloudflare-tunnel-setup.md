# Comprehensive Cloudflare Tunnel Setup Guide

This detailed guide covers everything you need to know about setting up secure remote access to your SmokePing deployment using Cloudflare Tunnels.

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Initial Setup](#initial-setup)
3. [Token Generation (Step-by-Step)](#token-generation)
4. [Edition-Specific Configurations](#edition-specific-configurations)
5. [Security Configuration](#security-configuration)
6. [Advanced Topics](#advanced-topics)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

### Requirements
- **Cloudflare Account**: Free tier is sufficient
- **Domain Name**: Must be managed by Cloudflare
- **SmokePing Installation**: Any edition (Basic, Standard, or Pro)
- **Docker**: For running the tunnel container

### Domain Setup
1. **Add Domain to Cloudflare**:
   - Log into [Cloudflare Dashboard](https://dash.cloudflare.com/)
   - Click "Add site" and enter your domain
   - Follow the nameserver setup instructions
   - Wait for DNS propagation (24-48 hours)

2. **Verify Domain Status**:
   - Domain status should show "Active"
   - SSL/TLS mode should be "Flexible" or "Full"

## Initial Setup

### 1. Navigate to Tunnel Directory
```bash
cd /path/to/smokeping/shared/cloudflare-tunnel
```

### 2. Create Environment File
```bash
cp .env.template .env
```

### 3. Basic Configuration
Edit `.env` with your tunnel token (obtained in next section):
```bash
# Required: Your tunnel token from Cloudflare
CLOUDFLARE_TUNNEL_TOKEN=eyJhI...very-long-token-here...xYz

# Optional: Custom tunnel name
TUNNEL_NAME=smokeping-production

# Optional: Custom config path
TUNNEL_CONFIG_PATH=/path/to/custom/config.yml
```

## Token Generation

### Method 1: Dashboard (Recommended)

#### Step 1: Access Zero Trust Dashboard
1. Go to [https://one.dash.cloudflare.com/](https://one.dash.cloudflare.com/)
2. Log in with your Cloudflare credentials
3. **Important**: Select the correct account if you have multiple
4. Ensure your domain appears in the dropdown

#### Step 2: Navigate to Tunnels
1. Click **Zero Trust** in the sidebar
2. Navigate to **Networks** → **Tunnels**
3. You'll see a list of existing tunnels (if any)

#### Step 3: Create New Tunnel
1. Click **Create a tunnel** button
2. Select **Cloudflared** as the connector type
3. Enter a descriptive name:
   - Production: `smokeping-prod-[location]`
   - Staging: `smokeping-staging`
   - Development: `smokeping-dev`
4. Click **Save tunnel**

#### Step 4: Copy Token
1. On the next screen, you'll see installation instructions
2. Look for the section with `cloudflared tunnel run --token`
3. **Copy the entire token** (starts with `ey` and is very long)
4. **Save this token securely** - you'll need it for deployment

#### Step 5: Configure Public Hostnames
Before completing tunnel setup, configure which services to expose:

1. In the tunnel configuration page, click **Public Hostnames**
2. Click **Add a public hostname**
3. Configure based on your edition (see sections below)

### Method 2: CLI (Advanced Users)

If you prefer command-line setup:

```bash
# Install cloudflared
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Authenticate with Cloudflare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create smokeping-production

# Get tunnel token
cloudflared tunnel token smokeping-production
```

## Edition-Specific Configurations

### Basic Edition Configuration

**Services to Expose**: SmokePing interface only

**Public Hostname Setup**:
```
Subdomain: smokeping
Domain: yourdomain.com
Service: http://localhost:80
```

**Access URLs**:
- SmokePing: `https://smokeping.yourdomain.com`

**Docker Compose**:
```bash
cd shared/cloudflare-tunnel
docker-compose up -d
```

### Standard Edition Configuration

**Services to Expose**: SmokePing + Web Admin

**Public Hostname Setup**:
```
# SmokePing Interface
Subdomain: smokeping  
Domain: yourdomain.com
Service: http://localhost:80

# Web Administration
Subdomain: admin
Domain: yourdomain.com  
Service: http://localhost:8080
```

**Access URLs**:
- SmokePing: `https://smokeping.yourdomain.com`
- Web Admin: `https://admin.yourdomain.com`

**Network Configuration**:
```bash
# Connect tunnel to Standard edition network
docker network connect editions_standard_default smokeping-tunnel
```

### Pro Edition Configuration

**Services to Expose**: SmokePing + Web Admin + Grafana

**Public Hostname Setup**:
```
# SmokePing Interface
Subdomain: smokeping
Domain: yourdomain.com
Service: http://localhost:80

# Web Administration  
Subdomain: admin
Domain: yourdomain.com
Service: http://localhost:8080

# Grafana Dashboards
Subdomain: grafana
Domain: yourdomain.com
Service: http://localhost:3000
```

**Access URLs**:
- SmokePing: `https://smokeping.yourdomain.com`
- Web Admin: `https://admin.yourdomain.com`  
- Grafana: `https://grafana.yourdomain.com`

**Network Configuration**:
```bash
# Connect tunnel to Pro edition network
docker network connect editions_pro_default smokeping-tunnel
```

## Security Configuration

### Access Policies (Recommended)

#### Step 1: Create Application
1. In Zero Trust Dashboard, go to **Access** → **Applications**
2. Click **Add an application**
3. Select **Self-hosted**

#### Step 2: Application Configuration
```yaml
Application Name: SmokePing Admin
Subdomain: admin
Domain: yourdomain.com
Session Duration: 24 hours
```

#### Step 3: Identity Providers
Configure at least one identity provider:

**Email Authentication**:
- Go to **Settings** → **Authentication**
- Enable **One-time PIN**
- Users will receive login codes via email

**Google OAuth**:
- Add **Google** as identity provider
- Configure OAuth credentials from Google Console

**Microsoft Azure AD**:
- Add **Azure AD** as identity provider
- Use your organization's Azure AD tenant

#### Step 4: Access Policies
Create policies for different service access levels:

**Admin Access Policy**:
```yaml
Policy Name: Admin Users
Action: Allow
Include:
  - Emails: admin@yourcompany.com, manager@yourcompany.com
Require:
  - Email domain: yourcompany.com
```

**Read-Only Access Policy**:
```yaml
Policy Name: Monitoring Team
Action: Allow  
Include:
  - Email domain: yourcompany.com
Require:
  - Purpose justification required: true
```

**Grafana Access Policy** (Pro Edition):
```yaml
Policy Name: Dashboard Viewers
Action: Allow
Include:
  - Everyone
Require:
  - Country: United States, Canada, United Kingdom
Session Duration: 8 hours
```

### Advanced Security Options

#### IP Restrictions
```yaml
Policy Rules:
  Include:
    - IP ranges: 192.168.1.0/24, 10.0.0.0/8
  Exclude:  
    - Countries: CN, RU, KP
```

#### Device Posture
```yaml
Require:
  - Certificate authentication
  - Device enrollment required
  - Antivirus running
```

## Advanced Topics

### Custom Tunnel Configuration

Create `config.yml` for advanced routing:

```yaml
tunnel: your-tunnel-id
credentials-file: /path/to/credentials.json

ingress:
  # SmokePing with custom headers
  - hostname: smokeping.yourdomain.com
    service: http://localhost:80
    originRequest:
      httpHostHeader: smokeping.local
      
  # Web Admin with authentication bypass for API
  - hostname: admin.yourdomain.com
    path: /api/*
    service: http://localhost:8080
    originRequest:
      noTLSVerify: true
      
  # Main Web Admin with full features  
  - hostname: admin.yourdomain.com
    service: http://localhost:8080
    
  # Grafana with custom timeout
  - hostname: grafana.yourdomain.com
    service: http://localhost:3000
    originRequest:
      connectTimeout: 30s
      tlsTimeout: 10s
      
  # Catch-all rule (required)
  - service: http_status:404
```

### Load Balancing

For high availability, run multiple tunnel instances:

```yaml
# tunnel-1.yml
tunnel: your-tunnel-id
credentials-file: /path/to/creds.json

# tunnel-2.yml  
tunnel: your-tunnel-id
credentials-file: /path/to/creds.json
```

Run multiple containers:
```bash
docker-compose up --scale cloudflared=3
```

### Monitoring and Logging

#### Enable Tunnel Metrics
```yaml
metrics: localhost:8080
```

#### Structured Logging
```bash
docker-compose logs cloudflared | jq '.'
```

#### Health Checks
```bash
# Check tunnel status
curl http://localhost:8080/ready

# Check metrics
curl http://localhost:8080/metrics
```

### Integration with External Networks

#### VPN Integration
```yaml
ingress:
  - hostname: internal.yourdomain.com
    service: http://192.168.1.100:80
```

#### Service Mesh Integration
```yaml
originRequest:
  bastionMode: true
  proxyAddress: socks5://proxy:1080
```

## Troubleshooting

### Common Issues and Solutions

#### 1. Tunnel Won't Connect

**Symptoms**: Container exits immediately or shows connection errors

**Diagnosis**:
```bash
# Check container logs
docker logs smokeping-tunnel

# Common error messages:
# "Invalid tunnel token"  
# "Tunnel credentials file not found"
# "Failed to create tunnel session"
```

**Solutions**:
- **Invalid Token**: Regenerate token from Cloudflare dashboard
- **Network Issues**: Check firewall allows outbound HTTPS (443)
- **DNS Issues**: Verify domain is active on Cloudflare

#### 2. 502 Bad Gateway Errors

**Symptoms**: Tunnel connects but services return 502 errors

**Diagnosis**:
```bash
# Check if local services are running
curl http://localhost:80        # SmokePing
curl http://localhost:8080      # Web Admin  
curl http://localhost:3000      # Grafana

# Check Docker networks
docker network ls
docker network inspect [network-name]
```

**Solutions**:
- **Service Not Running**: Start the SmokePing edition services
- **Wrong Port**: Verify service ports in tunnel configuration
- **Network Isolation**: Ensure tunnel can reach services

#### 3. SSL/TLS Errors

**Symptoms**: Certificate warnings or SSL handshake failures

**Solutions**:
```yaml
originRequest:
  noTLSVerify: true  # For self-signed certificates
  tlsTimeout: 30s    # Increase timeout
```

#### 4. Authentication Loops

**Symptoms**: Continuous redirects to login page

**Solutions**:
- Check cookie settings in Zero Trust
- Verify identity provider configuration
- Clear browser cookies and try again

#### 5. Performance Issues

**Symptoms**: Slow page loads through tunnel

**Diagnosis**:
```bash
# Test direct vs tunnel access
time curl http://localhost:8080/health
time curl https://admin.yourdomain.com/health
```

**Solutions**:
```yaml
originRequest:
  connectTimeout: 10s
  tlsTimeout: 10s
  keepAliveTimeout: 90s
  keepAliveConnections: 100
```

### Log Analysis

#### Enable Debug Logging
```bash
docker-compose down
docker-compose up --env TUNNEL_LOGLEVEL=debug
```

#### Key Log Messages
- `Connection registered`: Tunnel successfully connected
- `Serve tunnel error`: Service connection issues
- `Authentication failed`: Access policy problems
- `Origin unreachable`: Local service problems

### Advanced Debugging

#### Tunnel Trace
```bash
cloudflared tunnel --loglevel debug run --token YOUR_TOKEN
```

#### Network Analysis
```bash
# Check tunnel connectivity
dig _cloudflare-tunnel.yourdomain.com

# Trace network path
traceroute yourdomain.com
```

#### Performance Monitoring
```bash
# Enable metrics endpoint
curl http://localhost:8080/metrics | grep cloudflared
```

## Support Resources

### Official Documentation
- [Cloudflare Tunnel Docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [Zero Trust Dashboard](https://one.dash.cloudflare.com/)
- [Cloudflared GitHub](https://github.com/cloudflare/cloudflared)

### Community Support
- [Cloudflare Community Forum](https://community.cloudflare.com/)
- [SmokePing Issues](https://github.com/estcarisimo/smoking-pi/issues)

### Professional Support
- [Cloudflare Enterprise Support](https://www.cloudflare.com/enterprise/)
- Contact your Cloudflare account team for mission-critical deployments