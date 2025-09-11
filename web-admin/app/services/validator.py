"""
Validator Service
Validates hostnames, IP addresses, and other inputs
"""

import re
import socket
import ipaddress
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

class ValidatorService:
    """Service for validating various inputs"""
    
    def __init__(self):
        # Regex for valid hostname
        self.hostname_regex = re.compile(
            r'^(?=.{1,253}$)'  # Total length check
            r'(?!-)'  # Can't start with hyphen
            r'(?!.*--)'  # No double hyphens
            r'(?!.*\.$)'  # Can't end with dot
            r'[a-zA-Z0-9-]{1,63}'  # Label: alphanumeric and hyphens
            r'(?:\.[a-zA-Z0-9-]{1,63})*'  # Additional labels
            r'$'
        )
        
        # Reserved/private domains to warn about
        self.private_domains = {
            'localhost', 'localhost.localdomain',
            'local', 'invalid', 'test', 'example',
            'home', 'lan', 'corp', 'internal'
        }
    
    def validate_hostname(self, hostname: str) -> Tuple[bool, Optional[str]]:
        """
        Validate a hostname or IP address
        Returns (is_valid, error_message)
        """
        if not hostname:
            return False, "Hostname cannot be empty"
        
        hostname = hostname.strip().lower()
        
        # Check length
        if len(hostname) > 253:
            return False, "Hostname too long (max 253 characters)"
        
        # Try to parse as IP address first
        try:
            ip = ipaddress.ip_address(hostname)
            
            # Check for private/reserved IPs
            if ip.is_private:
                logger.warning(f"Private IP address: {hostname}")
                # Allow but warn
                return True, None
            
            if ip.is_reserved or ip.is_loopback:
                return False, f"Reserved or loopback IP address: {hostname}"
            
            return True, None
            
        except ValueError:
            # Not an IP, continue with hostname validation
            pass
        
        # Check hostname format
        if not self.hostname_regex.match(hostname):
            return False, "Invalid hostname format"
        
        # Check for private/test domains
        base_domain = hostname.split('.')[-1] if '.' in hostname else hostname
        if base_domain in self.private_domains:
            logger.warning(f"Private/test domain: {hostname}")
        
        # Try to resolve the hostname
        try:
            # Attempt DNS resolution
            socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            return True, None
            
        except socket.gaierror as e:
            # DNS resolution failed
            if 'Name or service not known' in str(e):
                return False, f"Cannot resolve hostname: {hostname}"
            else:
                return False, f"DNS error: {str(e)}"
                
        except Exception as e:
            logger.error(f"Unexpected error validating {hostname}: {e}")
            return False, f"Validation error: {str(e)}"
    
    def validate_ip_address(self, ip_str: str) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Validate an IP address and return its version
        Returns (is_valid, error_message, ip_version)
        """
        try:
            ip = ipaddress.ip_address(ip_str)
            
            if ip.is_reserved or ip.is_loopback:
                return False, "Reserved or loopback IP address", None
            
            return True, None, ip.version
            
        except ValueError as e:
            return False, str(e), None
    
    def validate_target_name(self, name: str) -> Tuple[bool, Optional[str]]:
        """
        Validate a target name for SmokePing
        Returns (is_valid, error_message)
        """
        if not name:
            return False, "Name cannot be empty"
        
        # SmokePing target names should be alphanumeric with underscores
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{0,19}$', name):
            return False, "Name must start with a letter and contain only letters, numbers, and underscores (max 20 chars)"
        
        # Check for reserved names
        reserved = {'targets', 'probes', 'general', 'database', 'presentation'}
        if name.lower() in reserved:
            return False, f"'{name}' is a reserved name"
        
        return True, None
    
    def sanitize_domain_for_target_name(self, domain: str) -> str:
        """
        Convert a domain name to a valid SmokePing target name
        """
        # Remove common TLDs
        name = domain.lower()
        for tld in ['.com', '.org', '.net', '.edu', '.gov', '.co.uk', '.io', '.co']:
            name = name.replace(tld, '')
        
        # Replace dots and hyphens with underscores
        name = re.sub(r'[.-]', '_', name)
        
        # Remove any remaining non-alphanumeric characters
        name = re.sub(r'[^a-zA-Z0-9_]', '', name)
        
        # Ensure it starts with a letter
        if name and name[0].isdigit():
            name = 'site_' + name
        
        # Truncate to 20 characters
        if len(name) > 20:
            name = name[:20]
        
        # If empty or invalid, generate a generic name
        if not name or not re.match(r'^[a-zA-Z]', name):
            name = 'target'
        
        return name