"""
SQLAlchemy models for SmokePing Target Management Database
"""

from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Boolean, Text, DateTime, 
    DECIMAL, ForeignKey, create_engine, text
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker, Session
from sqlalchemy.sql import func
import os

Base = declarative_base()

class TargetCategory(Base):
    """Target categories (custom, dns_resolvers, netflix_oca, top_sites)"""
    __tablename__ = 'target_categories'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    # Relationships
    targets = relationship("Target", back_populates="category")
    
    def __repr__(self):
        return f"<TargetCategory(name='{self.name}', display_name='{self.display_name}')>"

class Probe(Base):
    """Probe configurations (FPing, FPing6, DNS)"""
    __tablename__ = 'probes'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    binary_path = Column(String(200), nullable=False)
    step_seconds = Column(Integer, nullable=False, default=300)
    pings = Column(Integer, nullable=False, default=10)
    forks = Column(Integer, nullable=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    # Relationships
    targets = relationship("Target", back_populates="probe")
    
    def __repr__(self):
        return f"<Probe(name='{self.name}', binary_path='{self.binary_path}')>"

class Target(Base):
    """Monitoring targets with all metadata as proper columns"""
    __tablename__ = 'targets'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    host = Column(String(500), nullable=False)
    title = Column(String(200), nullable=False)
    category_id = Column(Integer, ForeignKey('target_categories.id'), nullable=False)
    probe_id = Column(Integer, ForeignKey('probes.id'), nullable=False)
    lookup = Column(String(200), nullable=True)  # DNS query domain
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    # Netflix OCA specific metadata columns
    asn = Column(String(20), nullable=True)
    cache_id = Column(String(20), nullable=True)
    city = Column(String(100), nullable=True)
    domain = Column(String(500), nullable=True)
    iata_code = Column(String(10), nullable=True)
    latitude = Column(DECIMAL(10, 8), nullable=True)
    longitude = Column(DECIMAL(11, 8), nullable=True)
    location_code = Column(String(20), nullable=True)
    raw_city = Column(String(200), nullable=True)
    metadata_type = Column(String(50), nullable=True)
    
    # Relationships
    category = relationship("TargetCategory", back_populates="targets")
    probe = relationship("Probe", back_populates="targets")
    
    def __repr__(self):
        return f"<Target(name='{self.name}', host='{self.host}', active={self.is_active})>"
    
    def to_dict(self):
        """Convert target to dictionary for API responses"""
        result = {
            'id': self.id,
            'name': self.name,
            'host': self.host,
            'title': self.title,
            'category': self.category.name if self.category else None,
            'probe': self.probe.name if self.probe else None,
            'lookup': self.lookup,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        
        # Add Netflix OCA metadata if present
        if self.asn or self.cache_id or self.city:
            result['metadata'] = {
                'asn': self.asn,
                'cache_id': self.cache_id,
                'city': self.city,
                'domain': self.domain,
                'iata_code': self.iata_code,
                'latitude': float(self.latitude) if self.latitude else None,
                'longitude': float(self.longitude) if self.longitude else None,
                'location_code': self.location_code,
                'raw_city': self.raw_city,
                'type': self.metadata_type
            }
        
        return result

class Source(Base):
    """Website source categories (topsites, netflix, custom, dns)"""
    __tablename__ = 'sources'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.current_timestamp())
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    def __repr__(self):
        return f"<Source(name='{self.name}', display_name='{self.display_name}', enabled={self.enabled})>"

class SystemMetadata(Base):
    """System metadata and configuration"""
    __tablename__ = 'system_metadata'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    def __repr__(self):
        return f"<SystemMetadata(key='{self.key}', value='{self.value}')>"

# Database connection and session management
class DatabaseManager:
    """Database connection and session management"""
    
    def __init__(self, database_url: Optional[str] = None):
        if database_url is None:
            database_url = os.getenv('DATABASE_URL', 'postgresql://smokeping:password@localhost:5432/smokeping_targets')
        
        self.engine = create_engine(database_url, echo=False)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
    
    def create_tables(self):
        """Create all tables"""
        Base.metadata.create_all(bind=self.engine)
    
    def get_session(self) -> Session:
        """Get database session"""
        return self.SessionLocal()
    
    def close(self):
        """Close database connection"""
        self.engine.dispose()

# Global database manager instance
db_manager = None

def get_db_manager() -> DatabaseManager:
    """Get global database manager instance"""
    global db_manager
    if db_manager is None:
        db_manager = DatabaseManager()
    return db_manager

def get_db_session() -> Session:
    """Get database session"""
    return get_db_manager().get_session()

# Repository classes for data access
class TargetRepository:
    """Repository for target operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_all(self, active_only: bool = False, category_name: Optional[str] = None) -> List[Target]:
        """Get all targets with optional filtering"""
        query = self.session.query(Target).join(TargetCategory).join(Probe)
        
        if active_only:
            query = query.filter(Target.is_active == True)
        
        if category_name:
            query = query.filter(TargetCategory.name == category_name)
        
        return query.all()
    
    def get_by_id(self, target_id: int) -> Optional[Target]:
        """Get target by ID"""
        return self.session.query(Target).filter(Target.id == target_id).first()
    
    def get_by_name(self, name: str) -> Optional[Target]:
        """Get target by name"""
        return self.session.query(Target).filter(Target.name == name).first()
    
    def create(self, target_data: dict) -> Target:
        """Create new target"""
        target = Target(**target_data)
        self.session.add(target)
        self.session.commit()
        self.session.refresh(target)
        return target
    
    def update(self, target_id: int, target_data: dict) -> Optional[Target]:
        """Update existing target"""
        target = self.get_by_id(target_id)
        if target:
            for key, value in target_data.items():
                setattr(target, key, value)
            self.session.commit()
            self.session.refresh(target)
        return target
    
    def delete(self, target_id: int) -> bool:
        """Delete target"""
        target = self.get_by_id(target_id)
        if target:
            self.session.delete(target)
            self.session.commit()
            return True
        return False
    
    def toggle_active(self, target_id: int) -> Optional[Target]:
        """Toggle target active status"""
        target = self.get_by_id(target_id)
        if target:
            target.is_active = not target.is_active
            self.session.commit()
            self.session.refresh(target)
        return target

class CategoryRepository:
    """Repository for category operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_all(self) -> List[TargetCategory]:
        """Get all categories"""
        return self.session.query(TargetCategory).all()
    
    def get_by_name(self, name: str) -> Optional[TargetCategory]:
        """Get category by name"""
        return self.session.query(TargetCategory).filter(TargetCategory.name == name).first()

class ProbeRepository:
    """Repository for probe operations"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_all(self) -> List[Probe]:
        """Get all probes"""
        return self.session.query(Probe).all()
    
    def get_by_name(self, name: str) -> Optional[Probe]:
        """Get probe by name"""
        return self.session.query(Probe).filter(Probe.name == name).first()
    
    def get_default(self) -> Optional[Probe]:
        """Get default probe"""
        return self.session.query(Probe).filter(Probe.is_default == True).first()