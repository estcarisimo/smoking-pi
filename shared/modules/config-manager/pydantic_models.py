"""
Pydantic models for SmokePing Configuration Manager.

This module provides data validation, serialization, and API contract definitions
using Pydantic models. These models work alongside SQLAlchemy ORM models to
provide type safety and validation.

Author: SmokePing Team
Version: 2.0.0
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    IPvAnyAddress,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings


class ProbeType(str, Enum):
    """Supported probe types for network monitoring."""
    
    FPING = "FPing"
    FPING6 = "FPing6" 
    DNS = "DNS"
    HTTP = "HTTP"
    HTTPS = "HTTPS"


class TargetStatus(str, Enum):
    """Target monitoring status."""
    
    ACTIVE = "active"
    INACTIVE = "inactive"
    DISABLED = "disabled"


class CategoryType(str, Enum):
    """Target category types."""
    
    CUSTOM = "custom"
    DNS_RESOLVERS = "dns_resolvers"
    NETFLIX_OCA = "netflix_oca"
    TOP_SITES = "top_sites"


# Configuration Models
class DatabaseSettings(BaseSettings):
    """Database configuration settings."""
    
    model_config = ConfigDict(
        env_prefix="DATABASE_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )
    
    url: str = Field(
        description="Database connection URL",
        examples=["postgresql://user:pass@localhost:5432/smokeping"]
    )
    pool_size: int = Field(default=5, ge=1, le=20)
    max_overflow: int = Field(default=10, ge=0, le=50)
    pool_timeout: int = Field(default=30, ge=1)
    pool_recycle: int = Field(default=3600, ge=60)


class AppSettings(BaseSettings):
    """Application configuration settings."""
    
    model_config = ConfigDict(
        env_prefix="SMOKEPING_",
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )
    
    # API Configuration
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=5000, ge=1, le=65535)
    debug: bool = Field(default=False)
    
    # Security
    secret_key: Optional[str] = Field(default=None, min_length=32)
    
    # Logging
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    
    # Docker Integration
    enable_docker: bool = Field(default=True)
    docker_socket: str = Field(default="/var/run/docker.sock")
    
    # External Services
    config_manager_url: Optional[HttpUrl] = None
    
    # Database
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)


# Base Models
class TimestampMixin(BaseModel):
    """Mixin for models with timestamp fields."""
    
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class BaseResponse(BaseModel):
    """Base response model for API endpoints."""
    
    success: bool = Field(description="Request success status")
    message: Optional[str] = Field(default=None, description="Response message")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# Core Entity Models
class TargetCategoryBase(BaseModel):
    """Base model for target categories."""
    
    name: str = Field(
        min_length=1,
        max_length=50,
        description="Category unique identifier",
        examples=["dns_resolvers", "netflix_oca"]
    )
    display_name: str = Field(
        min_length=1,
        max_length=100,
        description="Human-readable category name",
        examples=["DNS Resolvers", "Netflix Open Connect"]
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Category description"
    )


class TargetCategoryCreate(TargetCategoryBase):
    """Model for creating new target categories."""
    
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate category name format."""
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Name must contain only alphanumeric characters, hyphens, and underscores")
        return v.lower()


class TargetCategory(TargetCategoryBase, TimestampMixin):
    """Complete target category model."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int = Field(description="Category ID")


class ProbeBase(BaseModel):
    """Base model for monitoring probes."""
    
    name: str = Field(
        min_length=1,
        max_length=50,
        description="Probe name",
        examples=["FPing", "FPing6", "DNS"]
    )
    binary_path: str = Field(
        min_length=1,
        max_length=200,
        description="Path to probe binary",
        examples=["/usr/bin/fping", "/usr/bin/fping6"]
    )
    step_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="Probe interval in seconds"
    )
    pings: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of pings per probe"
    )
    forks: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Number of parallel processes"
    )
    is_default: bool = Field(
        default=False,
        description="Whether this is the default probe"
    )


class ProbeCreate(ProbeBase):
    """Model for creating new probes."""
    
    @field_validator("binary_path")
    @classmethod
    def validate_binary_path(cls, v: str) -> str:
        """Validate binary path format."""
        if not v.startswith("/"):
            raise ValueError("Binary path must be absolute")
        return v


class Probe(ProbeBase, TimestampMixin):
    """Complete probe model."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int = Field(description="Probe ID")


class TargetBase(BaseModel):
    """Base model for monitoring targets."""
    
    name: str = Field(
        min_length=1,
        max_length=100,
        description="Target name",
        examples=["GoogleDNS", "CloudflareDNS", "ExampleWebsite"]
    )
    host: str = Field(
        min_length=1,
        max_length=255,
        description="Target hostname or IP address",
        examples=["8.8.8.8", "example.com", "2001:4860:4860::8888"]
    )
    probe: str = Field(
        default="FPing",
        description="Probe type to use",
        examples=["FPing", "FPing6", "DNS"]
    )
    active: bool = Field(
        default=True,
        description="Whether target is actively monitored"
    )
    category_id: Optional[int] = Field(
        default=None,
        description="Category ID this target belongs to"
    )
    description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Target description"
    )
    
    # Monitoring configuration
    step_seconds: Optional[int] = Field(
        default=None,
        ge=60,
        le=3600,
        description="Override probe interval for this target"
    )
    pings: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description="Override ping count for this target"
    )
    
    # Location data (for OCA targets)
    latitude: Optional[Decimal] = Field(
        default=None,
        ge=-90,
        le=90,
        description="Target latitude"
    )
    longitude: Optional[Decimal] = Field(
        default=None,
        ge=-180,
        le=180,
        description="Target longitude"
    )
    city: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Target city"
    )
    country: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Target country"
    )
    
    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate target name format."""
        # Remove spaces and replace with underscores for smokeping compatibility
        v = v.replace(" ", "_")
        if not v.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise ValueError("Name must contain only alphanumeric characters, dots, hyphens, and underscores")
        return v


class TargetCreate(TargetBase):
    """Model for creating new monitoring targets."""
    
    @model_validator(mode="after")
    def validate_coordinates(self) -> "TargetCreate":
        """Validate that latitude and longitude are both provided or both None."""
        lat, lon = self.latitude, self.longitude
        if (lat is None) != (lon is None):
            raise ValueError("Latitude and longitude must both be provided or both be None")
        return self


class TargetUpdate(BaseModel):
    """Model for updating existing targets."""
    
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    probe: Optional[str] = Field(default=None)
    active: Optional[bool] = Field(default=None)
    category_id: Optional[int] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=500)
    step_seconds: Optional[int] = Field(default=None, ge=60, le=3600)
    pings: Optional[int] = Field(default=None, ge=1, le=100)
    latitude: Optional[Decimal] = Field(default=None, ge=-90, le=90)
    longitude: Optional[Decimal] = Field(default=None, ge=-180, le=180)
    city: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, max_length=100)


class Target(TargetBase, TimestampMixin):
    """Complete target model."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int = Field(description="Target ID")
    category: Optional[TargetCategory] = Field(
        default=None,
        description="Target category information"
    )


# Response Models
class TargetResponse(BaseResponse):
    """Response model for target operations."""
    
    data: Optional[Target] = Field(default=None)


class TargetListResponse(BaseResponse):
    """Response model for target list operations."""
    
    data: List[Target] = Field(default_factory=list)
    total: int = Field(default=0, description="Total number of targets")
    page: int = Field(default=1, ge=1, description="Current page number")
    per_page: int = Field(default=20, ge=1, le=100, description="Items per page")


class CategoryResponse(BaseResponse):
    """Response model for category operations."""
    
    data: Optional[TargetCategory] = Field(default=None)


class CategoryListResponse(BaseResponse):
    """Response model for category list operations."""
    
    data: List[TargetCategory] = Field(default_factory=list)
    total: int = Field(default=0)


class ProbeResponse(BaseResponse):
    """Response model for probe operations."""
    
    data: Optional[Probe] = Field(default=None)


class ProbeListResponse(BaseResponse):
    """Response model for probe list operations."""
    
    data: List[Probe] = Field(default_factory=list)
    total: int = Field(default=0)


# Health and Status Models
class HealthStatus(BaseModel):
    """Health check response model."""
    
    status: str = Field(description="Service status", examples=["healthy", "unhealthy"])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    version: str = Field(description="Service version")
    database: bool = Field(description="Database connectivity status")
    docker: bool = Field(description="Docker connectivity status")
    uptime_seconds: float = Field(description="Service uptime in seconds")


class ContainerStatus(BaseModel):
    """Docker container status model."""
    
    name: str = Field(description="Container name")
    status: str = Field(description="Container status")
    health: Optional[str] = Field(default=None, description="Container health status")
    image: str = Field(description="Container image")
    created: datetime = Field(description="Container creation time")


class SystemInfo(BaseModel):
    """System information model."""
    
    version: str = Field(description="Application version")
    python_version: str = Field(description="Python version")
    platform: str = Field(description="Operating system platform")
    containers: List[ContainerStatus] = Field(description="Container statuses")
    database_connected: bool = Field(description="Database connection status")
    total_targets: int = Field(description="Total number of targets")
    active_targets: int = Field(description="Number of active targets")


# Bulk Operations Models
class BulkTargetCreate(BaseModel):
    """Model for bulk target creation."""
    
    targets: List[TargetCreate] = Field(
        min_length=1,
        max_length=100,
        description="List of targets to create"
    )
    category_id: Optional[int] = Field(
        default=None,
        description="Default category for all targets"
    )
    default_probe: str = Field(
        default="FPing",
        description="Default probe for all targets"
    )


class BulkOperationResult(BaseModel):
    """Result model for bulk operations."""
    
    total: int = Field(description="Total items processed")
    successful: int = Field(description="Successfully processed items")
    failed: int = Field(description="Failed items")
    errors: List[str] = Field(default_factory=list, description="Error messages")


# Configuration Generation Models
class SmokePingConfig(BaseModel):
    """SmokePing configuration model."""
    
    general: Dict[str, Any] = Field(description="General configuration")
    database: Dict[str, Any] = Field(description="Database configuration")
    presentation: Dict[str, Any] = Field(description="Presentation configuration")
    probes: Dict[str, Any] = Field(description="Probe configurations")
    targets: Dict[str, Any] = Field(description="Target configurations")


class ConfigGenerationRequest(BaseModel):
    """Request model for configuration generation."""
    
    include_inactive: bool = Field(
        default=False,
        description="Include inactive targets in configuration"
    )
    categories: Optional[List[str]] = Field(
        default=None,
        description="Only include specific categories"
    )
    output_format: str = Field(
        default="smokeping",
        pattern="^(smokeping|yaml|json)$",
        description="Output format"
    )


class ConfigGenerationResponse(BaseResponse):
    """Response model for configuration generation."""
    
    config: Union[str, Dict[str, Any]] = Field(description="Generated configuration")
    targets_included: int = Field(description="Number of targets included")
    categories_included: List[str] = Field(description="Categories included")