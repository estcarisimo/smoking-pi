# Authentication & Security Guide

Complete guide for authentication, security, and API token management in the SmokePing system.

## 📋 Table of Contents

- [Current Security State](#current-security-state)
- [Planned Authentication](#planned-authentication)
- [API Token System](#api-token-system)
- [Security Architecture](#security-architecture)
- [Implementation Roadmap](#implementation-roadmap)
- [Session Management](#session-management)
- [Security Best Practices](#security-best-practices)
- [Configuration](#configuration)

## Current Security State

### Authentication Status

**Current Implementation**: **No authentication required**

The SmokePing system currently operates without authentication mechanisms, designed for internal Docker network use where network-level security provides the primary protection.

**Security Measures in Place**:
- **Network Isolation**: APIs only accessible within Docker network
- **Input Validation**: Server-side validation on all endpoints
- **CSRF Protection**: Form-based operations include CSRF tokens
- **Error Sanitization**: No stack traces exposed in production
- **Rate Limiting**: External API calls are rate-limited

### Access Control

**Current Access Model**:
- **Open Access**: All API endpoints accessible without credentials
- **Network-Based**: Security relies on Docker network isolation
- **Session-Based**: Web UI uses Flask sessions for state management
- **Trust Model**: Assumes trusted internal network environment

### Security Considerations

**Current Vulnerabilities**:
- No user authentication or authorization
- No API key validation
- No request signing or encryption
- No audit logging for API access
- No role-based access control

**Mitigating Factors**:
- Internal Docker network deployment
- Input validation and sanitization
- No direct internet exposure by default
- Comprehensive error handling

## Planned Authentication

### Future Authentication System

Based on the pending requirements, the system will implement:

> **From peding.md**: "I want the API to strictly use a token for all methods to allow users to make API interactions"

### Authentication Requirements

1. **API Token Authentication**: Token-based authentication for all API methods
2. **Token Generation**: Automatic token creation during system initialization
3. **Token Discovery**: Integration with password reveal scripts
4. **Secure Storage**: Encrypted token storage and transmission
5. **Token Rotation**: Ability to regenerate and rotate tokens

### Authentication Flow

```text
┌─────────────────┐    1. Request with Token    ┌─────────────────┐
│     Client      │──────────────────────────▶│   API Gateway   │
│                 │                            │                 │
└─────────────────┘                            └─────────────────┘
         ▲                                              │
         │                                              │ 2. Validate Token
         │                                              ▼
         │                                     ┌─────────────────┐
         │                                     │ Token Validator │
         │                                     │                 │
         │                                     └─────────────────┘
         │                                              │
         │ 4. API Response                              │ 3. Allow/Deny
         │                                              ▼
┌─────────────────┐    ← ← ← ← ← ← ← ← ← ← ← ← ←  ┌─────────────────┐
│   API Service   │                            │  Config Manager │
│                 │                            │                 │
└─────────────────┘                            └─────────────────┘
```

## API Token System

### Token Generation

**Integration with Init-Passwords Container**:

The init-passwords container will be enhanced to generate API tokens along with other credentials.

**Enhanced `init-passwords-docker.sh`**:
```bash
#!/bin/bash

# Generate API token
API_TOKEN=$(openssl rand -base64 48 | tr -d '=')

# Generate other credentials
INFLUX_PASSWORD=$(openssl rand -base64 32 | tr -d '=')
INFLUX_TOKEN=$(openssl rand -base64 32 | tr -d '=')
SECRET_KEY=$(openssl rand -base64 48 | tr -d '=')
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '=')

# Add to .env file
cat >> .env << EOF
# API Authentication
API_TOKEN=${API_TOKEN}
EOF
```

### Token Structure

**Token Format**:
```
smokeping_api_<random_base64_string>
```

**Example**:
```
smokeping_api_dGVzdF90b2tlbl9leGFtcGxlX3JhbmRvbV9zdHJpbmc
```

**Token Properties**:
- **Length**: 64+ characters
- **Encoding**: Base64
- **Prefix**: `smokeping_api_` for identification
- **Entropy**: 256+ bits of randomness
- **Expiration**: Configurable (default: no expiration)

### Token Storage

**Environment Variables**:
```bash
# Primary API token
API_TOKEN=smokeping_api_dGVzdF90b2tlbl9leGFtcGxlX3JhbmRvbV9zdHJpbmc

# Token metadata
API_TOKEN_CREATED_AT=2024-01-01T12:00:00Z
API_TOKEN_EXPIRES_AT=  # Empty for no expiration
```

**Database Storage** (Future):
```sql
CREATE TABLE api_tokens (
    id SERIAL PRIMARY KEY,
    token_hash VARCHAR(256) NOT NULL,
    token_prefix VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    permissions JSONB DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    last_used_at TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);
```

### Token Discovery

**Enhanced Password Reveal Script**:

**`show-passwords.sh`**:
```bash
#!/bin/bash

echo "=== SmokePing System Credentials ==="
echo ""

# Source environment file
if [ -f .env ]; then
    source .env
    
    echo "InfluxDB Admin Password: ${DOCKER_INFLUXDB_INIT_PASSWORD}"
    echo "InfluxDB API Token: ${INFLUX_TOKEN}"
    echo "PostgreSQL Password: ${POSTGRES_PASSWORD}"
    echo "Web Admin Secret Key: ${SECRET_KEY}"
    echo "API Authentication Token: ${API_TOKEN}"
    echo ""
    echo "API Usage Example:"
    echo "curl -H 'Authorization: Bearer ${API_TOKEN}' http://localhost:5000/status"
else
    echo "Error: .env file not found. Run init-passwords-docker.sh first."
fi
```

## Security Architecture

### Authentication Middleware

**Flask Middleware Implementation**:
```python
from functools import wraps
from flask import request, jsonify
import os

def require_api_token(f):
    """Decorator to require API token authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None
        
        # Check Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        
        # Check API key parameter (fallback)
        if not token:
            token = request.args.get('api_key')
        
        # Validate token
        if not token or not validate_api_token(token):
            return jsonify({
                'error': 'Valid API token required',
                'timestamp': datetime.utcnow().isoformat()
            }), 401
        
        return f(*args, **kwargs)
    return decorated_function

def validate_api_token(token):
    """Validate API token against stored value."""
    expected_token = os.environ.get('API_TOKEN')
    if not expected_token:
        return False
    
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(token, expected_token)
```

### Token Validation

**Validation Process**:
1. **Extract Token**: From Authorization header or query parameter
2. **Format Check**: Verify token format and prefix
3. **Constant-Time Comparison**: Prevent timing attacks
4. **Expiration Check**: Verify token hasn't expired (if applicable)
5. **Rate Limiting**: Apply per-token rate limits
6. **Audit Logging**: Log token usage

**Implementation**:
```python
import hmac
import hashlib
from datetime import datetime

class TokenValidator:
    def __init__(self, secret_key):
        self.secret_key = secret_key
        self.expected_token = os.environ.get('API_TOKEN')
    
    def validate(self, token):
        """Comprehensive token validation."""
        if not token:
            return False, "Token missing"
        
        if not token.startswith('smokeping_api_'):
            return False, "Invalid token format"
        
        if not self.expected_token:
            return False, "No API token configured"
        
        # Constant-time comparison
        if not hmac.compare_digest(token, self.expected_token):
            return False, "Invalid token"
        
        # Check expiration (if implemented)
        if self.is_expired(token):
            return False, "Token expired"
        
        return True, "Valid"
    
    def is_expired(self, token):
        """Check if token has expired."""
        expires_at = os.environ.get('API_TOKEN_EXPIRES_AT')
        if not expires_at:
            return False  # No expiration set
        
        try:
            expiry = datetime.fromisoformat(expires_at)
            return datetime.utcnow() > expiry
        except ValueError:
            return False  # Invalid expiry format, assume valid
```

### Request Authentication

**Header-Based Authentication** (Recommended):
```bash
curl -H "Authorization: Bearer smokeping_api_<token>" http://localhost:5000/targets
```

**Query Parameter Authentication** (Fallback):
```bash
curl "http://localhost:5000/targets?api_key=smokeping_api_<token>"
```

**Request Body Authentication** (For POST/PUT):
```bash
curl -X POST http://localhost:5000/targets \
  -H "Authorization: Bearer smokeping_api_<token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "example", "host": "example.com"}'
```

## Implementation Roadmap

### Phase 1: Token Generation and Storage

**Timeline**: 1-2 weeks

**Tasks**:
1. ✅ Enhance `init-passwords-docker.sh` to generate API tokens
2. ✅ Update `.env` template with API token variables
3. ✅ Modify `show-passwords.sh` to display API token
4. ✅ Update Docker Compose environment variables

**Deliverables**:
- API token generation in init script
- Token storage in environment variables
- Token discovery via password script

### Phase 2: Authentication Middleware

**Timeline**: 2-3 weeks

**Tasks**:
1. Implement Flask authentication middleware
2. Add token validation logic
3. Update all API endpoints with authentication decorators
4. Add error handling for authentication failures

**Deliverables**:
- Authentication middleware for config-manager
- Authentication middleware for web-admin
- Comprehensive token validation
- User-friendly error messages

### Phase 3: Enhanced Security Features

**Timeline**: 3-4 weeks

**Tasks**:
1. Implement token expiration
2. Add audit logging for API access
3. Add rate limiting per token
4. Implement token rotation
5. Add role-based permissions (future)

**Deliverables**:
- Token lifecycle management
- Security audit logging
- Rate limiting implementation
- Token rotation capability

### Phase 4: Documentation and Testing

**Timeline**: 1-2 weeks

**Tasks**:
1. Update all API documentation with authentication examples
2. Create authentication testing suite
3. Update README files with security information
4. Create security configuration guide

**Deliverables**:
- Complete authentication documentation
- Test suite for authentication
- Security configuration guide
- Migration guide for existing installations

## Session Management

### Web UI Sessions

**Current Implementation**:
```python
# Flask session configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_KEY_PREFIX'] = 'smokeping:'
```

**Session Data Structure**:
```python
session = {
    'user_id': 'default_user',
    'preferences': {
        'theme': 'light',
        'default_category': 'websites',
        'auto_refresh': True
    },
    'csrf_token': 'generated_csrf_token',
    'last_activity': '2024-01-01T12:00:00',
    'api_token': 'smokeping_api_<token>'  # Future
}
```

### Session Security

**Security Measures**:
- **HTTP-Only Cookies**: Prevent XSS attacks
- **Secure Cookies**: HTTPS-only transmission (when SSL enabled)
- **SameSite Cookies**: CSRF protection
- **Session Timeout**: Automatic expiration
- **Session Regeneration**: New session ID on login

**Configuration**:
```python
app.config.update(
    SESSION_COOKIE_SECURE=True,      # HTTPS only
    SESSION_COOKIE_HTTPONLY=True,    # No JavaScript access
    SESSION_COOKIE_SAMESITE='Lax',   # CSRF protection
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24)
)
```

## Security Best Practices

### Token Management

**Best Practices**:
1. **Secure Generation**: Use cryptographically secure random generators
2. **Adequate Length**: Minimum 256 bits of entropy
3. **Secure Storage**: Environment variables or encrypted storage
4. **Transmission Security**: HTTPS only in production
5. **Regular Rotation**: Periodic token renewal
6. **Audit Trail**: Log all token usage

### API Security

**Security Measures**:
1. **Input Validation**: Validate all input parameters
2. **Output Sanitization**: Sanitize all output data
3. **Rate Limiting**: Prevent abuse and DoS attacks
4. **HTTPS Enforcement**: Encrypt all communications
5. **Error Handling**: Don't leak sensitive information
6. **Audit Logging**: Comprehensive access logging

### Network Security

**Recommendations**:
1. **Firewall Rules**: Restrict access to API ports
2. **VPN Access**: Use VPN for remote access
3. **Network Segmentation**: Isolate monitoring network
4. **TLS Termination**: Use reverse proxy for TLS
5. **Certificate Management**: Regular certificate renewal

### Monitoring and Alerting

**Security Monitoring**:
1. **Failed Authentication Attempts**: Alert on repeated failures
2. **Unusual API Usage**: Monitor for suspicious patterns
3. **Token Usage**: Track token usage patterns
4. **System Access**: Log all administrative access
5. **Configuration Changes**: Alert on configuration modifications

## Configuration

### Environment Variables

```bash
# Authentication Configuration
API_TOKEN=smokeping_api_<generated_token>
API_TOKEN_CREATED_AT=2024-01-01T12:00:00Z
API_TOKEN_EXPIRES_AT=  # Empty for no expiration

# Security Settings
ENFORCE_HTTPS=false  # Set to true in production
SESSION_TIMEOUT=86400  # 24 hours in seconds
RATE_LIMIT_PER_TOKEN=1000  # Requests per hour
AUDIT_LOGGING=true

# Web UI Security
SECRET_KEY=<generated_secret_key>
CSRF_PROTECTION=true
SESSION_SECURE_COOKIES=false  # Set to true with HTTPS
```

### Docker Compose Configuration

```yaml
services:
  config-manager:
    environment:
      - API_TOKEN=${API_TOKEN}
      - ENFORCE_AUTHENTICATION=true
      - AUDIT_LOGGING=true
  
  web-admin:
    environment:
      - API_TOKEN=${API_TOKEN}
      - SECRET_KEY=${SECRET_KEY}
      - SESSION_SECURE_COOKIES=${ENFORCE_HTTPS:-false}
```

### Nginx Configuration (Production)

```nginx
server {
    listen 443 ssl http2;
    server_name smokeping.example.com;
    
    ssl_certificate /path/to/certificate.crt;
    ssl_certificate_key /path/to/private.key;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    
    location /api/ {
        proxy_pass http://localhost:5000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location / {
        proxy_pass http://localhost:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Migration Guide

### Upgrading to Authenticated System

**For Existing Installations**:

1. **Backup Current Configuration**:
   ```bash
   docker-compose exec config-manager cp -r /app/config /app/config.backup
   ```

2. **Update System**:
   ```bash
   git pull origin main
   ./init-passwords-docker.sh  # Regenerate with API token
   docker-compose down
   docker-compose up -d
   ```

3. **Verify Authentication**:
   ```bash
   # Test authenticated access
   source .env
   curl -H "Authorization: Bearer $API_TOKEN" http://localhost:5000/status
   ```

4. **Update Client Applications**:
   ```bash
   # Add API token to existing scripts
   API_TOKEN="your_generated_token"
   curl -H "Authorization: Bearer $API_TOKEN" http://localhost:5000/targets
   ```

### Testing Authentication

```bash
# Test without token (should fail)
curl http://localhost:5000/targets
# Expected: 401 Unauthorized

# Test with valid token (should succeed)
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:5000/targets
# Expected: 200 OK with target data

# Test with invalid token (should fail)
curl -H "Authorization: Bearer invalid_token" http://localhost:5000/targets
# Expected: 401 Unauthorized
```

## Troubleshooting

### Common Authentication Issues

**Token Not Working**:
1. Check token format and prefix
2. Verify environment variable is set
3. Check for extra whitespace or characters
4. Ensure proper header format

**Authentication Bypass**:
1. Verify middleware is properly applied
2. Check for missing decorators on endpoints
3. Ensure environment variables are loaded
4. Test with curl to isolate issues

**Session Issues**:
1. Check SECRET_KEY configuration
2. Verify session storage permissions
3. Clear browser cookies and sessions
4. Check session timeout settings

For more security examples and configurations, see the [examples directory](../examples/).