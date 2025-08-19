# External API Integrations

Complete reference for third-party API integrations used by the SmokePing system for site discovery, DNS monitoring, and data enrichment.

## 📋 Table of Contents

- [Overview](#overview)
- [Tranco API Integration](#tranco-api-integration)
- [Cloudflare Radar API](#cloudflare-radar-api)
- [CrUX (Chrome UX Report)](#crux-chrome-ux-report)
- [Netflix OCA Discovery](#netflix-oca-discovery)
- [DNS Resolution APIs](#dns-resolution-apis)
- [Rate Limiting & Error Handling](#rate-limiting--error-handling)
- [Configuration](#configuration)
- [Examples](#examples)

## Overview

The SmokePing system integrates with multiple external APIs to automatically discover popular websites, DNS resolvers, and Netflix Open Connect Appliances (OCAs) for comprehensive network monitoring.

### Integrated Services

| Service | Purpose | Rate Limits | Authentication |
|---------|---------|-------------|----------------|
| **Tranco** | Top sites ranking | None | None required |
| **Cloudflare Radar** | Top sites by country | 600/min | API Token |
| **CrUX** | Chrome UX Report data | None | None required |
| **Netflix OCA** | CDN server discovery | None | None required |
| **Public DNS** | DNS resolver discovery | None | None required |

### Key Features

- **Automatic Discovery**: Find popular monitoring targets
- **Country-Specific Filtering**: Regional top sites
- **Bulk Import**: Add multiple targets efficiently
- **Data Validation**: Verify hostnames and connectivity
- **Caching**: Reduce API calls with intelligent caching
- **Error Handling**: Robust fallback and retry mechanisms

## Tranco API Integration

Tranco provides a research-oriented ranking of the top websites on the Internet.

### Service Details

- **Base URL**: `https://tranco-list.eu`
- **Authentication**: None required
- **Rate Limits**: None officially documented
- **Data Format**: CSV/JSON

### Implementation

**Location**: `/home/smokingpi/smoking-pi/web-admin/app/services/tranco.py`

**Key Functions**:
```python
def fetch_tranco_top_sites(limit=100):
    """Fetch top sites from Tranco ranking."""
    url = f"https://tranco-list.eu/top-1m.csv.zip"
    # Download and parse CSV data
    return sites[:limit]
```

### API Endpoint

**Web Admin**: `GET /sources/api/fetch/tranco`

**Parameters**:
- `limit` (optional): Number of sites to fetch (default: 100, max: 1000)

**Example Request**:
```bash
curl "http://localhost:8080/sources/api/fetch/tranco?limit=50"
```

**Response**:
```json
{
  "source": "tranco",
  "sites": [
    {
      "rank": 1,
      "domain": "google.com",
      "title": "Google"
    },
    {
      "rank": 2,
      "domain": "youtube.com", 
      "title": "YouTube"
    }
  ],
  "total_fetched": 50,
  "fetched_at": "2024-01-01T12:00:00"
}
```

### Data Processing

```python
def process_tranco_data(csv_data, limit):
    sites = []
    for i, line in enumerate(csv_data.splitlines()[:limit]):
        rank, domain = line.strip().split(',', 1)
        sites.append({
            'rank': int(rank),
            'domain': domain,
            'title': domain.replace('.com', '').title()
        })
    return sites
```

### Error Handling

- **Network Errors**: Retry with exponential backoff
- **Parsing Errors**: Skip malformed entries, log warnings
- **Rate Limiting**: Not applicable (no documented limits)

## Cloudflare Radar API

Cloudflare Radar provides Internet traffic insights and top domains by country.

### Service Details

- **Base URL**: `https://api.cloudflare.com/client/v4`
- **Authentication**: API Token required
- **Rate Limits**: 600 requests per minute
- **Documentation**: https://developers.cloudflare.com/radar/

### Implementation

**Location**: `/home/smokingpi/smoking-pi/web-admin/app/services/cloudflare.py`

**Authentication**:
```python
headers = {
    'Authorization': f'Bearer {api_token}',
    'Content-Type': 'application/json'
}
```

**Key Functions**:
```python
def fetch_cloudflare_top_domains(country=None, limit=100):
    """Fetch top domains from Cloudflare Radar."""
    endpoint = "/radar/ranking/domain"
    params = {
        'limit': limit,
        'location': country if country else 'global'
    }
    return make_api_request(endpoint, params)
```

### API Endpoint

**Web Admin**: `GET /sources/api/fetch/cloudflare`

**Parameters**:
- `limit` (optional): Number of domains (default: 100, max: 1000)
- `country` (optional): ISO country code (e.g., 'US', 'GB', 'DE')

**Example Requests**:
```bash
# Global top sites
curl "http://localhost:8080/sources/api/fetch/cloudflare?limit=25"

# US-specific top sites
curl "http://localhost:8080/sources/api/fetch/cloudflare?country=US&limit=25"
```

**Response**:
```json
{
  "source": "cloudflare",
  "country": "US",
  "sites": [
    {
      "rank": 1,
      "domain": "google.com",
      "title": "Google",
      "categories": ["Search Engines"]
    }
  ],
  "total_fetched": 25,
  "fetched_at": "2024-01-01T12:00:00"
}
```

### Rate Limiting

**Implementation**:
```python
import time
from functools import wraps

def rate_limit(calls_per_minute=600):
    """Rate limiting decorator for Cloudflare API."""
    min_interval = 60.0 / calls_per_minute
    last_called = [0.0]
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            left_to_wait = min_interval - elapsed
            if left_to_wait > 0:
                time.sleep(left_to_wait)
            ret = func(*args, **kwargs)
            last_called[0] = time.time()
            return ret
        return wrapper
    return decorator
```

### Error Handling

```python
def handle_cloudflare_error(response):
    if response.status_code == 429:
        # Rate limit exceeded
        retry_after = response.headers.get('Retry-After', 60)
        raise RateLimitError(f"Rate limit exceeded. Retry after {retry_after} seconds")
    elif response.status_code == 401:
        raise AuthenticationError("Invalid API token")
    elif response.status_code >= 500:
        raise ServiceError("Cloudflare API service error")
```

## CrUX (Chrome UX Report)

Chrome User Experience Report provides real-world user experience data.

### Service Details

- **Data Source**: Google Chrome UX Report datasets
- **Authentication**: None required
- **Rate Limits**: None documented
- **Access Method**: GitHub repository with JSON files

### Implementation

**Location**: `/home/smokingpi/smoking-pi/web-admin/app/services/crux.py`

**Data Source**: `https://github.com/zakird/crux-top-lists/`

**Key Functions**:
```python
def fetch_crux_top_sites(country='global', limit=100):
    """Fetch top sites from CrUX data."""
    url = f"https://raw.githubusercontent.com/zakird/crux-top-lists/main/data/{country}_top_sites.json"
    response = requests.get(url)
    data = response.json()
    return data['sites'][:limit]
```

### API Endpoint

**Web Admin**: `GET /sources/api/fetch/crux`

**Parameters**:
- `country` (optional): Country code or 'global' (default: 'global')
- `limit` (optional): Number of sites (default: 100)

**Available Countries**:
- `global` - Worldwide data
- `us` - United States
- `gb` - United Kingdom
- `de` - Germany
- `fr` - France
- `jp` - Japan
- `br` - Brazil
- `in` - India

**Example Requests**:
```bash
# Global top sites
curl "http://localhost:8080/sources/api/fetch/crux?limit=30"

# Germany-specific sites
curl "http://localhost:8080/sources/api/fetch/crux?country=de&limit=20"
```

**Response**:
```json
{
  "source": "crux",
  "country": "de",
  "sites": [
    {
      "rank": 1,
      "domain": "google.de",
      "title": "Google Germany",
      "metrics": {
        "fcp": 1234,
        "lcp": 2345,
        "cls": 0.1
      }
    }
  ],
  "total_fetched": 20,
  "fetched_at": "2024-01-01T12:00:00"
}
```

### Data Processing

```python
def process_crux_data(raw_data, limit):
    sites = []
    for i, site in enumerate(raw_data[:limit]):
        sites.append({
            'rank': i + 1,
            'domain': site['origin'].replace('https://', ''),
            'title': site.get('title', site['origin']),
            'metrics': {
                'fcp': site.get('first_contentful_paint'),
                'lcp': site.get('largest_contentful_paint'),
                'cls': site.get('cumulative_layout_shift')
            }
        })
    return sites
```

## Netflix OCA Discovery

Netflix Open Connect Appliances (OCAs) are CDN servers that can be discovered and monitored.

### Service Details

- **Discovery Method**: DNS queries and public APIs
- **Authentication**: None required
- **Rate Limits**: None documented
- **Data Source**: Netflix public OCA information

### Implementation

**Location**: `/home/smokingpi/smoking-pi/config-manager/scripts/oca_fetcher.py`

**Discovery Methods**:
1. **DNS Resolution**: Query known OCA domains
2. **API Queries**: Public Netflix OCA APIs
3. **Traceroute Analysis**: Network topology discovery

**Key Functions**:
```python
def discover_netflix_ocas():
    """Discover Netflix OCA servers."""
    ocas = []
    
    # Method 1: DNS queries
    oca_domains = [
        'ipv4_1-lagg0.1.cache.netflix.net',
        'ipv4_2-lagg0.1.cache.netflix.net'
    ]
    
    for domain in oca_domains:
        try:
            ip = socket.gethostbyname(domain)
            ocas.append({
                'name': f'netflix_oca_{domain.split(".")[0]}',
                'host': ip,
                'title': f'Netflix OCA {ip}',
                'domain': domain
            })
        except socket.gaierror:
            continue
    
    return ocas
```

### API Endpoint

**Config Manager**: `POST /oca/refresh`

**Example Request**:
```bash
curl -X POST http://localhost:5000/oca/refresh
```

**Response**:
```json
{
  "success": true,
  "message": "OCA data refreshed successfully",
  "ocas_found": 15,
  "ocas_updated": 12,
  "refreshed_at": "2024-01-01T12:00:00"
}
```

### OCA Data Structure

```python
class NetflixOCA:
    def __init__(self, ip, cachegroup, site):
        self.name = f"netflix_oca_{ip.replace('.', '_')}"
        self.host = ip
        self.title = f"Netflix OCA {cachegroup}"
        self.cachegroup = cachegroup  # e.g., "LAX1"
        self.site = site              # e.g., "Los Angeles"
        self.category = "netflix_oca"
        self.probe = "FPing"
```

### Database Integration

```python
def store_oca_in_database(oca_data):
    """Store OCA data in PostgreSQL."""
    category = session.query(Category).filter_by(name='netflix_oca').first()
    probe = session.query(Probe).filter_by(name='FPing').first()
    
    for oca in oca_data:
        target = Target(
            name=oca['name'],
            host=oca['host'],
            title=oca['title'],
            category_id=category.id,
            probe_id=probe.id,
            site=oca.get('site'),
            cachegroup=oca.get('cachegroup')
        )
        session.merge(target)
    
    session.commit()
```

## DNS Resolution APIs

Integration with public DNS resolvers for DNS monitoring capabilities.

### Supported DNS Resolvers

| Provider | Primary | Secondary | IPv6 Primary | IPv6 Secondary |
|----------|---------|-----------|--------------|----------------|
| **Google** | 8.8.8.8 | 8.8.4.4 | 2001:4860:4860::8888 | 2001:4860:4860::8844 |
| **Cloudflare** | 1.1.1.1 | 1.0.0.1 | 2606:4700:4700::1111 | 2606:4700:4700::1001 |
| **Quad9** | 9.9.9.9 | 149.112.112.112 | 2620:fe::fe | 2620:fe::9 |
| **OpenDNS** | 208.67.222.222 | 208.67.220.220 | 2620:119:35::35 | 2620:119:53::53 |

### DNS Probe Configuration

```python
def create_dns_targets():
    """Create DNS resolver monitoring targets."""
    dns_resolvers = [
        {
            'name': 'GoogleDNS',
            'host': '8.8.8.8',
            'title': 'Google Public DNS',
            'probe': 'EchoPing'
        },
        {
            'name': 'CloudflareDNS', 
            'host': '1.1.1.1',
            'title': 'Cloudflare DNS',
            'probe': 'EchoPing'
        },
        {
            'name': 'Quad9DNS',
            'host': '9.9.9.9', 
            'title': 'Quad9 DNS',
            'probe': 'EchoPing'
        }
    ]
    
    return dns_resolvers
```

### DNS Resolution Testing

```python
def test_dns_resolution(hostname, dns_server='8.8.8.8'):
    """Test DNS resolution performance."""
    import dns.resolver
    
    resolver = dns.resolver.Resolver()
    resolver.nameservers = [dns_server]
    
    start_time = time.time()
    try:
        result = resolver.resolve(hostname, 'A')
        resolution_time = (time.time() - start_time) * 1000
        
        return {
            'success': True,
            'resolution_time_ms': resolution_time,
            'ip_addresses': [str(ip) for ip in result],
            'dns_server': dns_server
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'dns_server': dns_server
        }
```

## Rate Limiting & Error Handling

### Rate Limiting Implementation

**Cloudflare API Rate Limiter**:
```python
class CloudflareRateLimiter:
    def __init__(self, requests_per_minute=600):
        self.requests_per_minute = requests_per_minute
        self.min_interval = 60.0 / requests_per_minute
        self.last_request = 0.0
    
    def wait_if_needed(self):
        """Wait if necessary to respect rate limits."""
        elapsed = time.time() - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()
```

### Error Handling Strategies

**Network Errors**:
```python
def retry_with_backoff(func, max_retries=3, base_delay=1):
    """Retry function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)
```

**API Errors**:
```python
def handle_api_error(response, service_name):
    """Handle API error responses."""
    if response.status_code == 429:
        retry_after = response.headers.get('Retry-After', 60)
        raise RateLimitError(f"{service_name} rate limit exceeded")
    elif response.status_code == 401:
        raise AuthenticationError(f"{service_name} authentication failed")
    elif response.status_code >= 500:
        raise ServiceUnavailableError(f"{service_name} service unavailable")
    else:
        raise APIError(f"{service_name} API error: {response.status_code}")
```

### Caching Strategy

**In-Memory Caching**:
```python
from functools import lru_cache
import time

@lru_cache(maxsize=128)
def cached_api_call(url, cache_duration=3600):
    """Cache API responses for specified duration."""
    cache_key = f"{url}_{int(time.time() // cache_duration)}"
    # Implementation details...
```

## Configuration

### Environment Variables

```bash
# Cloudflare API Configuration
CLOUDFLARE_API_TOKEN=your_cloudflare_token_here
CLOUDFLARE_RATE_LIMIT=600  # requests per minute

# External API Settings
EXTERNAL_API_TIMEOUT=30
EXTERNAL_API_RETRIES=3
EXTERNAL_API_CACHE_DURATION=3600
```

### Web Admin Settings

```python
# web-admin configuration
EXTERNAL_APIS = {
    'tranco': {
        'enabled': True,
        'default_limit': 100,
        'max_limit': 1000
    },
    'cloudflare': {
        'enabled': True,
        'token_required': True,
        'rate_limit': 600,
        'default_limit': 100
    },
    'crux': {
        'enabled': True,
        'supported_countries': ['global', 'us', 'gb', 'de', 'fr', 'jp'],
        'default_limit': 100
    }
}
```

## Examples

### Complete Site Discovery Workflow

```bash
# 1. Discover top sites from multiple sources
curl "http://localhost:8080/sources/api/fetch/tranco?limit=10" > tranco.json
curl "http://localhost:8080/sources/api/fetch/cloudflare?country=US&limit=10" > cloudflare.json
curl "http://localhost:8080/sources/api/fetch/crux?country=us&limit=10" > crux.json

# 2. Combine and import selected sites
curl -X POST http://localhost:8080/sources/api/update \
  -H "Content-Type: application/json" \
  -d '{
    "selected_sites": [
      {"domain": "google.com", "title": "Google", "category": "websites"},
      {"domain": "facebook.com", "title": "Facebook", "category": "websites"}
    ]
  }'

# 3. Refresh Netflix OCA data
curl -X POST http://localhost:5000/oca/refresh

# 4. Apply all changes
curl -X POST http://localhost:8080/api/apply
```

### Bulk DNS Resolver Setup

```bash
# Add multiple DNS resolvers
for dns in "8.8.8.8:GoogleDNS" "1.1.1.1:CloudflareDNS" "9.9.9.9:Quad9DNS"; do
  ip=$(echo $dns | cut -d: -f1)
  name=$(echo $dns | cut -d: -f2)
  
  curl -X POST http://localhost:5000/targets \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"$name\",
      \"host\": \"$ip\",
      \"title\": \"$name DNS Server\",
      \"category_id\": 4,
      \"probe_id\": 2
    }"
done
```

### Regional Monitoring Setup

```bash
# Set up country-specific monitoring
countries=("US" "GB" "DE" "FR" "JP")

for country in "${countries[@]}"; do
  # Fetch country-specific top sites
  curl "http://localhost:8080/sources/api/fetch/cloudflare?country=$country&limit=5" | \
    jq '.sites | map({domain: .domain, title: .title, category: "websites"})' | \
    curl -X POST http://localhost:8080/sources/api/update \
      -H "Content-Type: application/json" \
      -d @-
done
```

### Error Handling Examples

```bash
# Handle rate limiting
handle_rate_limit() {
  response=$(curl -s -w "%{http_code}" "http://localhost:8080/sources/api/fetch/cloudflare")
  http_code=${response: -3}
  
  if [ "$http_code" = "429" ]; then
    echo "Rate limited. Waiting 60 seconds..."
    sleep 60
    # Retry the request
    curl "http://localhost:8080/sources/api/fetch/cloudflare"
  fi
}

# Test API availability
test_external_apis() {
  apis=("tranco" "cloudflare" "crux")
  
  for api in "${apis[@]}"; do
    if curl -s -f "http://localhost:8080/sources/api/fetch/$api?limit=1" > /dev/null; then
      echo "$api API: Available"
    else
      echo "$api API: Unavailable"
    fi
  done
}
```

## Best Practices

### API Usage Guidelines

1. **Respect Rate Limits**: Always implement proper rate limiting
2. **Cache Responses**: Cache API responses to reduce external calls
3. **Handle Errors Gracefully**: Implement robust error handling and fallbacks
4. **Monitor Usage**: Track API usage and costs
5. **Validate Data**: Always validate external API data before use

### Security Considerations

1. **API Token Management**: Store tokens securely, rotate regularly
2. **Input Validation**: Validate all external data before processing
3. **Rate Limiting**: Implement client-side rate limiting
4. **Error Disclosure**: Don't expose internal errors to users
5. **Logging**: Log API usage for monitoring and debugging

### Performance Optimization

1. **Parallel Requests**: Use async requests where possible
2. **Connection Pooling**: Reuse HTTP connections
3. **Batch Operations**: Combine multiple API calls when supported
4. **Caching Strategy**: Implement multi-level caching
5. **Timeout Management**: Set appropriate timeouts for external calls

For more integration examples and troubleshooting, see the [examples directory](../examples/).