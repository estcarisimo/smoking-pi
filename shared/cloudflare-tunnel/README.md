# Cloudflare Tunnel Setup for SmokePing

This guide explains how to set up secure remote access to your SmokePing deployment using Cloudflare Tunnels.

> **💡 Looking for instant access without setup?** Try [Quick Tunnels](../docs/quick-tunnels.md) instead - no account or domain required!

## Quick Start

1. **Copy environment template**:
   ```bash
   cp .env.template .env
   ```

2. **Get your Cloudflare Tunnel token** (see detailed steps below)

3. **Edit `.env` file** and add your tunnel token:
   ```bash
   CLOUDFLARE_TUNNEL_TOKEN=your-actual-token-here
   ```

4. **Start the tunnel**:
   ```bash
   docker-compose up -d
   ```

## Getting Your Tunnel Token

### Step 1: Access Cloudflare Zero Trust Dashboard
1. Go to [https://one.dash.cloudflare.com/](https://one.dash.cloudflare.com/)
2. Log in with your Cloudflare account
3. Select your domain from the dropdown

### Step 2: Create a Tunnel
1. Navigate to **Zero Trust** → **Networks** → **Tunnels**
2. Click **Create a tunnel**
3. Choose **Cloudflared** as the connector
4. Enter a tunnel name (e.g., `smokeping-production`)
5. Click **Save tunnel**

### Step 3: Get Your Token
1. After creating the tunnel, you'll see the token on the next screen
2. Copy the entire token (it's a long string starting with `ey...`)
3. Paste it into your `.env` file as `CLOUDFLARE_TUNNEL_TOKEN`

### Step 4: Configure Public Hostnames
Configure which services to expose based on your SmokePing edition:

#### Basic Edition
- **SmokePing Interface**: `http://localhost:80`
  - Subdomain: `smokeping`
  - Service: `http://localhost:80`

#### Standard Edition  
- **SmokePing Interface**: `http://localhost:80`
- **Web Admin**: `http://localhost:8080`
  - Subdomain: `smokeping`
  - Service: `http://localhost:80`
  - Subdomain: `admin`
  - Service: `http://localhost:8080`

#### Pro Edition
- **SmokePing Interface**: `http://localhost:80`  
- **Web Admin**: `http://localhost:8080`
- **Grafana Dashboard**: `http://localhost:3000`
  - Subdomain: `smokeping`
  - Service: `http://localhost:80`
  - Subdomain: `admin`
  - Service: `http://localhost:8080`
  - Subdomain: `grafana`
  - Service: `http://localhost:3000`

### Step 5: Save and Test
1. Click **Save tunnel**
2. Your services should now be accessible via:
   - `https://smokeping.yourdomain.com`
   - `https://admin.yourdomain.com` (Standard/Pro)
   - `https://grafana.yourdomain.com` (Pro only)

## Security Recommendations

### Enable Access Policies
1. In Zero Trust Dashboard, go to **Access** → **Applications**
2. Create applications for each service
3. Set up authentication policies (email verification, Google OAuth, etc.)

### Example Access Policy
```yaml
Name: SmokePing Admin Access
Subdomain: admin
Domain: yourdomain.com
Policy:
  - Rule Name: Require Email
  - Action: Allow  
  - Include: Emails ending in @yourcompany.com
```

## Troubleshooting

### Tunnel Won't Connect
1. **Check your token**: Make sure it's copied completely
2. **Verify domain**: Ensure your domain is added to Cloudflare
3. **Check logs**: `docker logs smokeping-tunnel`

### Service Not Accessible
1. **Verify local access**: Test `http://localhost:PORT` locally first
2. **Check public hostname config**: Ensure service URLs match local services
3. **Review tunnel logs**: Look for connection errors

### Common Issues
- **404 errors**: Usually means the service URL in tunnel config is wrong
- **Connection timeouts**: Service might not be running locally
- **SSL errors**: Check if you're using the correct protocol (http/https)

## Advanced Configuration

### Custom Tunnel Names
Edit `.env` to customize the tunnel name:
```bash
TUNNEL_NAME=my-production-smokeping
```

### Multiple Environments
Create separate tunnel tokens for different environments:
- `tunnel-production.env`
- `tunnel-staging.env`
- `tunnel-development.env`

## Support

For more detailed Cloudflare Tunnel documentation:
- [Official Cloudflare Tunnel Docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [Zero Trust Dashboard](https://one.dash.cloudflare.com/)

For SmokePing-specific issues:
- Check the main [SmokePing Documentation](../../README.md)
- Review edition-specific READMEs in `editions/*/`