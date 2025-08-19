#!/usr/bin/env python3
"""
SmokePing Python API Client

A comprehensive Python client for interacting with the SmokePing API system.
This client provides both synchronous and asynchronous access to all API endpoints
with proper error handling, authentication, and data validation.

Usage:
    from smokeping_client import SmokePingClient
    
    client = SmokePingClient("http://localhost:5000")
    targets = client.get_targets()
    print(f"Found {len(targets)} targets")

Requirements:
    pip install requests aiohttp pydantic
"""

import json
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass
from enum import Enum

try:
    import requests
    import aiohttp
    from pydantic import BaseModel, Field, validator
except ImportError:
    print("Missing dependencies. Install with: pip install requests aiohttp pydantic")
    exit(1)


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base exception for API errors"""
    pass


class AuthenticationError(APIError):
    """Authentication failed"""
    pass


class NotFoundError(APIError):
    """Resource not found"""
    pass


class ValidationError(APIError):
    """Data validation failed"""
    pass


class ServiceUnavailableError(APIError):
    """Service temporarily unavailable"""
    pass


# Data Models
class TargetStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProbeType(str, Enum):
    FPING = "FPing"
    ECHOPING = "EchoPing"


@dataclass
class Category:
    id: int
    name: str
    display_name: str
    description: Optional[str] = None


@dataclass
class Probe:
    id: int
    name: str
    binary_path: str
    step_seconds: int = 300
    pings: int = 20
    forks: int = 5
    is_default: bool = False


@dataclass
class Target:
    id: Optional[int]
    name: str
    host: str
    title: str
    is_active: bool = True
    category: Optional[Category] = None
    probe: Optional[Probe] = None
    site: Optional[str] = None
    cachegroup: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TargetCreate(BaseModel):
    """Model for creating new targets"""
    name: str = Field(..., min_length=1, max_length=100)
    host: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    category_id: int = Field(..., gt=0)
    probe_id: int = Field(..., gt=0)
    is_active: bool = True
    site: Optional[str] = Field(None, max_length=100)
    cachegroup: Optional[str] = Field(None, max_length=100)

    @validator('name')
    def name_must_be_valid(cls, v):
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Name must contain only alphanumeric characters, underscores, and hyphens')
        return v


class SmokePingClient:
    """
    Synchronous client for SmokePing API
    
    Example:
        client = SmokePingClient("http://localhost:5000")
        
        # Check health
        health = client.get_health()
        print(f"Service status: {health['status']}")
        
        # List targets
        targets = client.get_targets(active_only=True)
        for target in targets:
            print(f"Target: {target.name} -> {target.host}")
            
        # Create new target
        new_target = TargetCreate(
            name="example",
            host="example.com",
            title="Example Website",
            category_id=1,
            probe_id=1
        )
        created = client.create_target(new_target)
        print(f"Created target with ID: {created.id}")
    """
    
    def __init__(self, base_url: str, api_token: Optional[str] = None, timeout: int = 30):
        """
        Initialize the SmokePing API client
        
        Args:
            base_url: Base URL for the API (e.g., "http://localhost:5000")
            api_token: API authentication token (future implementation)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.timeout = timeout
        self.session = requests.Session()
        
        # Set default headers
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'SmokePing-Python-Client/1.0'
        })
        
        # Add authentication header if token provided
        if api_token:
            self.session.headers.update({
                'Authorization': f'Bearer {api_token}'
            })
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """
        Make HTTP request with error handling
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            **kwargs: Additional request parameters
            
        Returns:
            Parsed JSON response
            
        Raises:
            APIError: For various API error conditions
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                timeout=self.timeout,
                **kwargs
            )
            
            # Handle different response codes
            if response.status_code == 401:
                raise AuthenticationError("Authentication failed - check API token")
            elif response.status_code == 404:
                raise NotFoundError(f"Resource not found: {endpoint}")
            elif response.status_code == 400:
                error_msg = "Bad request"
                try:
                    error_data = response.json()
                    error_msg = error_data.get('error', error_msg)
                except:
                    pass
                raise ValidationError(error_msg)
            elif response.status_code >= 500:
                raise ServiceUnavailableError(f"Server error: {response.status_code}")
            elif not response.ok:
                raise APIError(f"Request failed with status {response.status_code}")
            
            # Parse JSON response
            try:
                return response.json()
            except json.JSONDecodeError:
                raise APIError("Invalid JSON response from server")
                
        except requests.exceptions.Timeout:
            raise APIError(f"Request timeout after {self.timeout} seconds")
        except requests.exceptions.ConnectionError:
            raise APIError(f"Failed to connect to {self.base_url}")
        except requests.exceptions.RequestException as e:
            raise APIError(f"Request failed: {str(e)}")
    
    # Health and Status Methods
    def get_health(self) -> Dict[str, Any]:
        """Get service health status"""
        return self._make_request('GET', '/health')
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        return self._make_request('GET', '/status')
    
    def is_healthy(self) -> bool:
        """Check if service is healthy"""
        try:
            health = self.get_health()
            return health.get('status') == 'healthy'
        except APIError:
            return False
    
    def get_database_status(self) -> Dict[str, Any]:
        """Get database status information"""
        status = self.get_status()
        return status.get('database', {})
    
    def is_database_available(self) -> bool:
        """Check if database is available"""
        try:
            db_status = self.get_database_status()
            return db_status.get('available', False)
        except APIError:
            return False
    
    # Configuration Methods
    def get_config(self, config_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get configuration data
        
        Args:
            config_type: Optional config type ('targets', 'probes', 'sources')
            
        Returns:
            Configuration data
        """
        endpoint = '/config'
        if config_type:
            endpoint += f'/{config_type}'
        return self._make_request('GET', endpoint)
    
    def update_config(self, config_type: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update configuration (YAML mode only)
        
        Args:
            config_type: Configuration type ('targets', 'probes', 'sources')
            config_data: Configuration data to update
            
        Returns:
            Update result
        """
        return self._make_request('PUT', f'/config/{config_type}', json=config_data)
    
    # Target Management Methods
    def get_targets(self, active_only: bool = False, category: Optional[str] = None) -> List[Target]:
        """
        Get targets from database
        
        Args:
            active_only: Filter to only active targets
            category: Filter by category name
            
        Returns:
            List of Target objects
        """
        params = {}
        if active_only:
            params['active_only'] = 'true'
        if category:
            params['category'] = category
        
        response = self._make_request('GET', '/targets', params=params)
        targets = []
        
        for target_data in response.get('targets', []):
            # Convert category and probe data if present
            category_obj = None
            if 'category' in target_data and target_data['category']:
                cat_data = target_data['category']
                category_obj = Category(
                    id=cat_data['id'],
                    name=cat_data['name'],
                    display_name=cat_data.get('display_name', ''),
                    description=cat_data.get('description')
                )
            
            probe_obj = None
            if 'probe' in target_data and target_data['probe']:
                probe_data = target_data['probe']
                probe_obj = Probe(
                    id=probe_data['id'],
                    name=probe_data['name'],
                    binary_path=probe_data.get('binary_path', ''),
                    step_seconds=probe_data.get('step_seconds', 300),
                    pings=probe_data.get('pings', 20),
                    forks=probe_data.get('forks', 5),
                    is_default=probe_data.get('is_default', False)
                )
            
            # Parse timestamps
            created_at = None
            updated_at = None
            if target_data.get('created_at'):
                try:
                    created_at = datetime.fromisoformat(target_data['created_at'].replace('Z', '+00:00'))
                except ValueError:
                    pass
            if target_data.get('updated_at'):
                try:
                    updated_at = datetime.fromisoformat(target_data['updated_at'].replace('Z', '+00:00'))
                except ValueError:
                    pass
            
            target = Target(
                id=target_data.get('id'),
                name=target_data['name'],
                host=target_data['host'],
                title=target_data['title'],
                is_active=target_data.get('is_active', True),
                category=category_obj,
                probe=probe_obj,
                site=target_data.get('site'),
                cachegroup=target_data.get('cachegroup'),
                created_at=created_at,
                updated_at=updated_at
            )
            targets.append(target)
        
        return targets
    
    def get_target_by_id(self, target_id: int) -> Optional[Target]:
        """Get specific target by ID"""
        targets = self.get_targets()
        for target in targets:
            if target.id == target_id:
                return target
        return None
    
    def get_target_by_name(self, name: str) -> Optional[Target]:
        """Get specific target by name"""
        targets = self.get_targets()
        for target in targets:
            if target.name == name:
                return target
        return None
    
    def create_target(self, target_data: TargetCreate) -> Target:
        """
        Create new target
        
        Args:
            target_data: Target creation data
            
        Returns:
            Created Target object
        """
        response = self._make_request('POST', '/targets', json=target_data.dict())
        
        # Parse response into Target object
        category_obj = None
        if 'category' in response:
            cat_data = response['category']
            category_obj = Category(
                id=cat_data['id'],
                name=cat_data['name'],
                display_name=cat_data.get('display_name', ''),
                description=cat_data.get('description')
            )
        
        probe_obj = None
        if 'probe' in response:
            probe_data = response['probe']
            probe_obj = Probe(
                id=probe_data['id'],
                name=probe_data['name'],
                binary_path=probe_data.get('binary_path', ''),
                step_seconds=probe_data.get('step_seconds', 300),
                pings=probe_data.get('pings', 20),
                forks=probe_data.get('forks', 5),
                is_default=probe_data.get('is_default', False)
            )
        
        created_at = None
        updated_at = None
        if response.get('created_at'):
            created_at = datetime.fromisoformat(response['created_at'].replace('Z', '+00:00'))
        if response.get('updated_at'):
            updated_at = datetime.fromisoformat(response['updated_at'].replace('Z', '+00:00'))
        
        return Target(
            id=response['id'],
            name=response['name'],
            host=response['host'],
            title=response['title'],
            is_active=response.get('is_active', True),
            category=category_obj,
            probe=probe_obj,
            site=response.get('site'),
            cachegroup=response.get('cachegroup'),
            created_at=created_at,
            updated_at=updated_at
        )
    
    def update_target(self, target_id: int, update_data: Dict[str, Any]) -> Target:
        """
        Update existing target
        
        Args:
            target_id: Target ID to update
            update_data: Fields to update
            
        Returns:
            Updated Target object
        """
        response = self._make_request('PUT', f'/targets/{target_id}', json=update_data)
        
        # Convert response to Target object (similar to create_target)
        # ... (implementation similar to create_target method)
        
        return Target(
            id=response['id'],
            name=response['name'],
            host=response['host'],
            title=response['title'],
            is_active=response.get('is_active', True),
            # ... other fields
        )
    
    def delete_target(self, target_id: int) -> bool:
        """
        Delete target
        
        Args:
            target_id: Target ID to delete
            
        Returns:
            True if deletion successful
        """
        try:
            self._make_request('DELETE', f'/targets/{target_id}')
            return True
        except NotFoundError:
            return False
    
    def toggle_target(self, target_id: int) -> Target:
        """
        Toggle target active/inactive status
        
        Args:
            target_id: Target ID to toggle
            
        Returns:
            Updated Target object
        """
        response = self._make_request('POST', f'/targets/{target_id}/toggle')
        
        # Return simplified Target object with updated status
        return Target(
            id=response['id'],
            name=response['name'],
            host='',  # Not returned in toggle response
            title='',  # Not returned in toggle response
            is_active=response['is_active']
        )
    
    # Categories and Probes
    def get_categories(self) -> List[Category]:
        """Get all target categories"""
        response = self._make_request('GET', '/categories')
        categories = []
        
        for cat_data in response.get('categories', []):
            category = Category(
                id=cat_data['id'],
                name=cat_data['name'],
                display_name=cat_data['display_name'],
                description=cat_data.get('description')
            )
            categories.append(category)
        
        return categories
    
    def get_probes(self) -> List[Probe]:
        """Get all probe configurations"""
        response = self._make_request('GET', '/probes')
        probes = []
        
        for probe_data in response.get('probes', []):
            probe = Probe(
                id=probe_data['id'],
                name=probe_data['name'],
                binary_path=probe_data['binary_path'],
                step_seconds=probe_data.get('step_seconds', 300),
                pings=probe_data.get('pings', 20),
                forks=probe_data.get('forks', 5),
                is_default=probe_data.get('is_default', False)
            )
            probes.append(probe)
        
        return probes
    
    def get_default_probe(self) -> Optional[Probe]:
        """Get the default probe"""
        probes = self.get_probes()
        for probe in probes:
            if probe.is_default:
                return probe
        return None
    
    # Service Operations
    def generate_config(self) -> Dict[str, Any]:
        """Generate SmokePing configuration files"""
        return self._make_request('POST', '/generate')
    
    def restart_smokeping(self) -> Dict[str, Any]:
        """Restart SmokePing service"""
        return self._make_request('POST', '/restart')
    
    def refresh_oca(self) -> Dict[str, Any]:
        """Refresh Netflix OCA data"""
        return self._make_request('POST', '/oca/refresh')
    
    def apply_changes(self) -> Dict[str, Any]:
        """Apply configuration changes (generate + restart)"""
        try:
            # Generate configuration
            generate_result = self.generate_config()
            if not generate_result.get('success', False):
                return generate_result
            
            # Restart service
            restart_result = self.restart_smokeping()
            
            return {
                'success': True,
                'message': 'Configuration applied successfully',
                'generate_result': generate_result,
                'restart_result': restart_result
            }
        except APIError as e:
            return {
                'success': False,
                'message': f'Failed to apply changes: {str(e)}'
            }
    
    # Utility Methods
    def wait_for_service(self, max_attempts: int = 30, interval: int = 2) -> bool:
        """
        Wait for service to become healthy
        
        Args:
            max_attempts: Maximum number of attempts
            interval: Interval between attempts in seconds
            
        Returns:
            True if service becomes healthy, False if timeout
        """
        for attempt in range(max_attempts):
            try:
                if self.is_healthy():
                    logger.info(f"Service is healthy after {attempt + 1} attempts")
                    return True
            except APIError:
                pass
            
            if attempt < max_attempts - 1:
                logger.info(f"Waiting for service... (attempt {attempt + 1}/{max_attempts})")
                time.sleep(interval)
        
        logger.error(f"Service did not become healthy after {max_attempts} attempts")
        return False
    
    def get_target_summary(self) -> Dict[str, Any]:
        """Get summary of targets by category and status"""
        targets = self.get_targets()
        summary = {
            'total': len(targets),
            'active': sum(1 for t in targets if t.is_active),
            'inactive': sum(1 for t in targets if not t.is_active),
            'by_category': {}
        }
        
        for target in targets:
            if target.category:
                cat_name = target.category.name
                if cat_name not in summary['by_category']:
                    summary['by_category'][cat_name] = {'total': 0, 'active': 0, 'inactive': 0}
                
                summary['by_category'][cat_name]['total'] += 1
                if target.is_active:
                    summary['by_category'][cat_name]['active'] += 1
                else:
                    summary['by_category'][cat_name]['inactive'] += 1
        
        return summary


class SmokePingAsyncClient:
    """
    Asynchronous client for SmokePing API
    
    Example:
        import asyncio
        
        async def main():
            async with SmokePingAsyncClient("http://localhost:5000") as client:
                health = await client.get_health()
                print(f"Service status: {health['status']}")
                
                targets = await client.get_targets()
                print(f"Found {len(targets)} targets")
        
        asyncio.run(main())
    """
    
    def __init__(self, base_url: str, api_token: Optional[str] = None, timeout: int = 30):
        """Initialize async client"""
        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'SmokePing-Python-AsyncClient/1.0'
        }
        
        if self.api_token:
            headers['Authorization'] = f'Bearer {self.api_token}'
        
        self.session = aiohttp.ClientSession(
            headers=headers,
            timeout=self.timeout
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make async HTTP request"""
        if not self.session:
            raise APIError("Client not initialized - use async context manager")
        
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        try:
            async with self.session.request(method, url, **kwargs) as response:
                if response.status == 401:
                    raise AuthenticationError("Authentication failed")
                elif response.status == 404:
                    raise NotFoundError(f"Resource not found: {endpoint}")
                elif response.status == 400:
                    error_msg = "Bad request"
                    try:
                        error_data = await response.json()
                        error_msg = error_data.get('error', error_msg)
                    except:
                        pass
                    raise ValidationError(error_msg)
                elif response.status >= 500:
                    raise ServiceUnavailableError(f"Server error: {response.status}")
                elif not response.ok:
                    raise APIError(f"Request failed with status {response.status}")
                
                return await response.json()
                
        except aiohttp.ClientTimeout:
            raise APIError("Request timeout")
        except aiohttp.ClientError as e:
            raise APIError(f"Request failed: {str(e)}")
    
    async def get_health(self) -> Dict[str, Any]:
        """Get service health status"""
        return await self._make_request('GET', '/health')
    
    async def get_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        return await self._make_request('GET', '/status')
    
    async def get_targets(self, active_only: bool = False, category: Optional[str] = None) -> List[Target]:
        """Get targets (async version)"""
        params = {}
        if active_only:
            params['active_only'] = 'true'
        if category:
            params['category'] = category
        
        response = await self._make_request('GET', '/targets', params=params)
        # Convert response to Target objects (same logic as sync version)
        # ... implementation similar to sync version
        return []  # Simplified for example


# Web Admin Client
class WebAdminClient:
    """
    Client for Web Admin API Gateway
    
    Provides access to web admin specific endpoints like site discovery,
    bandwidth estimation, and enhanced status information.
    """
    
    def __init__(self, base_url: str = "http://localhost:8080", api_token: Optional[str] = None):
        """Initialize Web Admin client"""
        self.base_url = base_url.rstrip('/')
        self.api_token = api_token
        self.session = requests.Session()
        
        if api_token:
            self.session.headers.update({
                'Authorization': f'Bearer {api_token}'
            })
    
    def get_status(self) -> Dict[str, Any]:
        """Get enhanced status through web admin"""
        response = self.session.get(f"{self.base_url}/api/status")
        response.raise_for_status()
        return response.json()
    
    def get_bandwidth_estimate(self) -> Dict[str, Any]:
        """Get bandwidth usage estimates"""
        response = self.session.get(f"{self.base_url}/api/bandwidth")
        response.raise_for_status()
        return response.json()
    
    def validate_hostname(self, hostname: str) -> Dict[str, Any]:
        """Validate hostname with DNS and IPv6 info"""
        response = self.session.post(
            f"{self.base_url}/api/validate-hostname",
            json={'hostname': hostname}
        )
        response.raise_for_status()
        return response.json()
    
    def fetch_top_sites(self, source: str, limit: int = 100, country: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch top sites from external sources
        
        Args:
            source: Source name ('tranco', 'crux', 'cloudflare')
            limit: Number of sites to fetch
            country: Country code for regional sites
        """
        params = {'limit': limit}
        if country:
            params['country'] = country
        
        response = self.session.get(
            f"{self.base_url}/sources/api/fetch/{source}",
            params=params
        )
        response.raise_for_status()
        return response.json()
    
    def import_sites(self, sites: List[Dict[str, str]], replace_existing: bool = False) -> Dict[str, Any]:
        """Import discovered sites as targets"""
        data = {
            'selected_sites': sites,
            'replace_existing': replace_existing
        }
        response = self.session.post(
            f"{self.base_url}/sources/api/update",
            json=data
        )
        response.raise_for_status()
        return response.json()
    
    def apply_changes(self) -> Dict[str, Any]:
        """Apply configuration changes (generate + restart)"""
        response = self.session.post(f"{self.base_url}/api/apply")
        response.raise_for_status()
        return response.json()


# Example Usage and Scripts
def example_basic_usage():
    """Basic usage example"""
    print("=== SmokePing Python Client Example ===")
    
    # Initialize client
    client = SmokePingClient("http://localhost:5000")
    
    try:
        # Check health
        health = client.get_health()
        print(f"Service status: {health['status']}")
        
        # Get system status
        status = client.get_status()
        print(f"Database available: {status.get('database', {}).get('available', False)}")
        
        # List targets
        targets = client.get_targets()
        print(f"Total targets: {len(targets)}")
        
        # Get only active targets
        active_targets = client.get_targets(active_only=True)
        print(f"Active targets: {len(active_targets)}")
        
        # Get target summary
        summary = client.get_target_summary()
        print(f"Target summary: {summary}")
        
        # List categories
        categories = client.get_categories()
        for category in categories:
            print(f"Category: {category.name} ({category.display_name})")
        
    except APIError as e:
        print(f"API Error: {e}")


def example_target_management():
    """Target management example"""
    print("=== Target Management Example ===")
    
    client = SmokePingClient("http://localhost:5000")
    
    try:
        # Create new target
        new_target_data = TargetCreate(
            name="example_com",
            host="example.com",
            title="Example Website",
            category_id=1,
            probe_id=1,
            is_active=True
        )
        
        created_target = client.create_target(new_target_data)
        print(f"Created target: {created_target.name} (ID: {created_target.id})")
        
        # Update target
        if created_target.id:
            updated_target = client.update_target(
                created_target.id,
                {"title": "Updated Example Website"}
            )
            print(f"Updated target: {updated_target.title}")
            
            # Toggle target status
            toggled_target = client.toggle_target(created_target.id)
            print(f"Toggled target status: {toggled_target.is_active}")
            
            # Delete target
            deleted = client.delete_target(created_target.id)
            print(f"Target deleted: {deleted}")
        
    except APIError as e:
        print(f"API Error: {e}")


def example_site_discovery():
    """Site discovery and import example"""
    print("=== Site Discovery Example ===")
    
    web_client = WebAdminClient("http://localhost:8080")
    config_client = SmokePingClient("http://localhost:5000")
    
    try:
        # Fetch top sites from Tranco
        tranco_sites = web_client.fetch_top_sites('tranco', limit=5)
        print(f"Fetched {len(tranco_sites['sites'])} sites from Tranco")
        
        # Prepare sites for import
        sites_to_import = []
        for site in tranco_sites['sites'][:3]:  # Import top 3
            sites_to_import.append({
                'domain': site['domain'],
                'title': site['title'],
                'category': 'websites'
            })
        
        # Import sites
        import_result = web_client.import_sites(sites_to_import)
        print(f"Import result: {import_result['message']}")
        
        # Apply changes
        apply_result = web_client.apply_changes()
        print(f"Apply result: {apply_result.get('message', 'Applied successfully')}")
        
        # Verify import
        targets = config_client.get_targets(category='websites')
        print(f"Total website targets after import: {len(targets)}")
        
    except Exception as e:
        print(f"Error: {e}")


async def example_async_usage():
    """Async client usage example"""
    print("=== Async Client Example ===")
    
    async with SmokePingAsyncClient("http://localhost:5000") as client:
        try:
            # Get health status
            health = await client.get_health()
            print(f"Async health check: {health['status']}")
            
            # Get system status
            status = await client.get_status()
            print(f"Async status check: {status['status']}")
            
        except APIError as e:
            print(f"Async API Error: {e}")


def example_error_handling():
    """Error handling example"""
    print("=== Error Handling Example ===")
    
    client = SmokePingClient("http://localhost:5000")
    
    # Test various error conditions
    try:
        # Try to get non-existent target
        target = client.get_target_by_id(99999)
        print(f"Target found: {target}")
    except NotFoundError:
        print("Target not found (expected)")
    
    try:
        # Try invalid target creation
        invalid_target = TargetCreate(
            name="",  # Invalid empty name
            host="invalid_host",
            title="Invalid Target",
            category_id=999,  # Non-existent category
            probe_id=1
        )
        client.create_target(invalid_target)
    except ValidationError as e:
        print(f"Validation error (expected): {e}")
    except APIError as e:
        print(f"API error (expected): {e}")
    
    # Test service availability
    if not client.is_healthy():
        print("Service is not healthy")
    else:
        print("Service is healthy")


def main():
    """Main function demonstrating all examples"""
    print("SmokePing Python Client Examples")
    print("=" * 40)
    
    # Run synchronous examples
    example_basic_usage()
    print()
    
    example_target_management()
    print()
    
    example_site_discovery()
    print()
    
    example_error_handling()
    print()
    
    # Run async example
    import asyncio
    asyncio.run(example_async_usage())


if __name__ == "__main__":
    main()