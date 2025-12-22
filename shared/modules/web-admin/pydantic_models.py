#!/usr/bin/env python3
"""
Pydantic models for SmokePing Web Administration Interface.

Provides type-safe data validation and serialization for web forms,
API responses, and configuration management in the admin interface.

Author: SmokePing Team  
Version: 2.0.0
"""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    IPvAnyAddress,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, Enum):
    """Available logging levels."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuthMethod(str, Enum):
    """Available authentication methods."""
    BASIC = "basic"
    SESSION = "session"
    TOKEN = "token"
    DISABLED = "disabled"


class AppSettings(BaseSettings):
    """
    Application configuration with environment variable support.
    
    Loads configuration from environment variables and .env files
    with sensible defaults for production deployment.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Server configuration
    host: str = Field(default="0.0.0.0", description="Server bind address")
    port: int = Field(default=8080, ge=1, le=65535, description="Server port")
    debug: bool = Field(default=False, description="Enable debug mode")
    secret_key: str = Field(
        default="dev-key-change-in-production",
        min_length=32,
        description="Flask secret key for sessions"
    )

    # Database configuration
    database_url: str = Field(
        default="postgresql://smokeping:password@postgres:5432/smokeping",
        description="PostgreSQL connection string"
    )
    database_pool_size: int = Field(default=5, ge=1, description="Database connection pool size")
    database_max_overflow: int = Field(default=10, ge=0, description="Database max overflow connections")

    # Authentication
    auth_method: AuthMethod = Field(default=AuthMethod.SESSION, description="Authentication method")
    basic_auth_username: Optional[str] = Field(default=None, description="Basic auth username")
    basic_auth_password: Optional[str] = Field(default=None, description="Basic auth password")
    session_timeout: int = Field(default=3600, ge=300, description="Session timeout in seconds")

    # Logging
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Logging level")
    log_file: Optional[Path] = Field(default=None, description="Log file path")
    enable_request_logging: bool = Field(default=False, description="Enable HTTP request logging")

    # Features
    enable_api: bool = Field(default=True, description="Enable REST API endpoints")
    enable_monitoring: bool = Field(default=True, description="Enable monitoring dashboards")
    enable_docker_integration: bool = Field(default=True, description="Enable Docker container management")
    enable_config_generation: bool = Field(default=True, description="Enable config generation")

    # Caching
    cache_type: str = Field(default="simple", description="Cache backend type")
    cache_timeout: int = Field(default=300, ge=0, description="Default cache timeout in seconds")

    # External services
    config_manager_url: str = Field(
        default="http://smokeping-standard-config-manager:5000",
        description="Config manager service URL"
    )
    smokeping_url: str = Field(
        default="http://smokeping:8080/smokeping/smokeping.cgi",
        description="SmokePing CGI URL"
    )
    grafana_url: Optional[HttpUrl] = Field(default=None, description="Grafana dashboard URL")

    # File paths
    templates_dir: Path = Field(default=Path("templates"), description="Templates directory")
    static_dir: Path = Field(default=Path("static"), description="Static files directory")
    upload_dir: Path = Field(default=Path("uploads"), description="File upload directory")

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        """Validate that secret key is not the default in production."""
        if not v or v == "dev-key-change-in-production":
            import os
            if os.environ.get("FLASK_ENV") == "production":
                raise ValueError("Secret key must be changed for production deployment")
        return v


# Form Models
class LoginForm(BaseModel):
    """Login form validation."""
    username: str = Field(..., min_length=1, max_length=100, description="Username")
    password: str = Field(..., min_length=1, max_length=500, description="Password")
    remember_me: bool = Field(default=False, description="Remember login")


class TargetForm(BaseModel):
    """Target creation/update form."""
    name: str = Field(..., min_length=1, max_length=100, description="Target name")
    host: str = Field(..., min_length=1, max_length=255, description="Target hostname or IP")
    category: str = Field(..., min_length=1, max_length=100, description="Target category")
    probe: str = Field(default="FPing", max_length=50, description="Probe type")
    description: Optional[str] = Field(default=None, max_length=500, description="Target description")
    latitude: Optional[float] = Field(default=None, ge=-90, le=90, description="Latitude coordinate")
    longitude: Optional[float] = Field(default=None, ge=-180, le=180, description="Longitude coordinate")
    active: bool = Field(default=True, description="Target is active")
    
    @model_validator(mode="after")
    def validate_coordinates(self) -> "TargetForm":
        """Validate that latitude and longitude are both provided or both None."""
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Both latitude and longitude must be provided together")
        return self


class CategoryForm(BaseModel):
    """Category creation/update form."""
    name: str = Field(..., min_length=1, max_length=100, description="Category name")
    description: Optional[str] = Field(default=None, max_length=500, description="Category description")
    color: Optional[str] = Field(
        default=None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Category color (hex format)"
    )
    sort_order: int = Field(default=0, description="Display sort order")


class ProbeForm(BaseModel):
    """Probe configuration form."""
    name: str = Field(..., min_length=1, max_length=50, description="Probe name")
    probe_type: str = Field(..., min_length=1, max_length=50, description="Probe type")
    binary: Optional[str] = Field(default=None, max_length=255, description="Probe binary path")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Probe-specific parameters")
    timeout: Optional[int] = Field(default=None, ge=1, le=300, description="Probe timeout in seconds")


class BulkImportForm(BaseModel):
    """Bulk target import form."""
    import_format: str = Field(..., description="Import file format (csv, yaml, json)")
    file_content: str = Field(..., min_length=1, description="File content to import")
    category: str = Field(..., min_length=1, description="Default category for imported targets")
    probe: str = Field(default="FPing", description="Default probe for imported targets")
    overwrite_existing: bool = Field(default=False, description="Overwrite existing targets")


# Response Models
class DashboardStats(BaseModel):
    """Dashboard statistics."""
    total_targets: int = Field(..., ge=0, description="Total number of targets")
    active_targets: int = Field(..., ge=0, description="Number of active targets")
    categories: int = Field(..., ge=0, description="Number of categories")
    probes: int = Field(..., ge=0, description="Number of probes")
    last_config_generation: Optional[datetime] = Field(
        default=None, description="Last configuration generation time"
    )
    system_uptime: float = Field(..., ge=0, description="System uptime in seconds")


class ServiceStatus(BaseModel):
    """External service status."""
    name: str = Field(..., description="Service name")
    url: str = Field(..., description="Service URL")
    status: str = Field(..., description="Service status")
    response_time: Optional[float] = Field(default=None, description="Response time in milliseconds")
    last_check: datetime = Field(..., description="Last status check time")
    healthy: bool = Field(..., description="Service is healthy")


class SystemHealth(BaseModel):
    """Overall system health status."""
    status: str = Field(..., description="Overall system status")
    services: List[ServiceStatus] = Field(default_factory=list, description="Individual service statuses")
    database_connected: bool = Field(..., description="Database connectivity status")
    config_manager_available: bool = Field(..., description="Config manager availability")
    smokeping_running: bool = Field(..., description="SmokePing process status")
    last_updated: datetime = Field(..., description="Last health check time")


# API Response Models
class WebUIResponse(BaseModel):
    """Standard web UI response wrapper."""
    success: bool = Field(..., description="Operation success status")
    message: Optional[str] = Field(default=None, description="Response message")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Response data")
    errors: Optional[List[str]] = Field(default=None, description="Error messages")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")


class PaginatedResponse(BaseModel):
    """Paginated response for lists."""
    success: bool = Field(default=True, description="Operation success status")
    data: List[Dict[str, Any]] = Field(..., description="List of items")
    total: int = Field(..., ge=0, description="Total number of items")
    page: int = Field(..., ge=1, description="Current page number")
    per_page: int = Field(..., ge=1, description="Items per page")
    pages: int = Field(..., ge=1, description="Total number of pages")
    has_prev: bool = Field(..., description="Has previous page")
    has_next: bool = Field(..., description="Has next page")

    @model_validator(mode="after")
    def calculate_pagination(self) -> "PaginatedResponse":
        """Calculate pagination metadata."""
        import math
        self.pages = max(1, math.ceil(self.total / self.per_page))
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        return self


# Configuration Models
class ConfigGenerationOptions(BaseModel):
    """Options for configuration generation."""
    include_inactive: bool = Field(default=False, description="Include inactive targets")
    output_format: str = Field(default="smokeping", description="Output configuration format")
    validate_config: bool = Field(default=True, description="Validate generated configuration")
    restart_services: bool = Field(default=False, description="Restart services after generation")


class ImportResult(BaseModel):
    """Result of bulk import operation."""
    success: bool = Field(..., description="Import success status")
    total_processed: int = Field(..., ge=0, description="Total items processed")
    successful_imports: int = Field(..., ge=0, description="Successfully imported items")
    failed_imports: int = Field(..., ge=0, description="Failed import attempts")
    errors: List[str] = Field(default_factory=list, description="Import error messages")
    warnings: List[str] = Field(default_factory=list, description="Import warnings")
    imported_targets: List[str] = Field(default_factory=list, description="Names of imported targets")


# User and Session Models
class User(BaseModel):
    """User model for authentication."""
    username: str = Field(..., min_length=1, max_length=100, description="Username")
    email: Optional[EmailStr] = Field(default=None, description="User email address")
    is_admin: bool = Field(default=False, description="User has admin privileges")
    last_login: Optional[datetime] = Field(default=None, description="Last login timestamp")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Account creation time")
    active: bool = Field(default=True, description="User account is active")

    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return True

    def is_active_user(self) -> bool:
        """Check if user account is active."""
        return self.active

    def is_anonymous(self) -> bool:
        """Check if user is anonymous."""
        return False

    def get_id(self) -> str:
        """Get user identifier for Flask-Login."""
        return self.username


# Monitoring Models
class MetricPoint(BaseModel):
    """Individual metric data point."""
    timestamp: datetime = Field(..., description="Metric timestamp")
    value: float = Field(..., description="Metric value")
    target: Optional[str] = Field(default=None, description="Target name")


class ChartData(BaseModel):
    """Chart data for dashboards."""
    labels: List[str] = Field(..., description="Chart labels")
    datasets: List[Dict[str, Any]] = Field(..., description="Chart datasets")
    options: Dict[str, Any] = Field(default_factory=dict, description="Chart options")


class NetworkMap(BaseModel):
    """Network topology map data."""
    nodes: List[Dict[str, Any]] = Field(..., description="Network nodes")
    edges: List[Dict[str, Any]] = Field(..., description="Network connections")
    layout: str = Field(default="force", description="Map layout algorithm")


# Notification Models
class NotificationLevel(str, Enum):
    """Notification severity levels."""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class Notification(BaseModel):
    """User notification message."""
    level: NotificationLevel = Field(..., description="Notification severity")
    title: str = Field(..., min_length=1, max_length=100, description="Notification title")
    message: str = Field(..., min_length=1, max_length=1000, description="Notification message")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Notification timestamp")
    dismissible: bool = Field(default=True, description="Notification can be dismissed")
    auto_dismiss: Optional[int] = Field(
        default=None, ge=1, description="Auto-dismiss after seconds"
    )