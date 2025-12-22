#!/usr/bin/env python3
"""
SmokePing RRD to ClickHouse Data Exporter.

Exports SmokePing RRD data to ClickHouse time-series database for advanced
analytics and visualization. Supports real-time streaming and batch processing
with comprehensive error handling and monitoring.

Author: SmokePing Team
Version: 2.0.0
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import click
import clickhouse_connect
import numpy as np
import pandas as pd
import structlog
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from tqdm import tqdm

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# Prometheus metrics
export_counter = Counter('smokeping_exports_total', 'Total exports processed')
export_errors = Counter('smokeping_export_errors_total', 'Total export errors')
export_duration = Histogram('smokeping_export_duration_seconds', 'Export duration')
rrd_files_processed = Gauge('smokeping_rrd_files_processed', 'Number of RRD files processed')
clickhouse_rows_inserted = Counter('smokeping_clickhouse_rows_total', 'Total rows inserted to ClickHouse')


def rrdtool_info(rrd_path: str) -> Dict:
    """Get RRD info using rrdtool info command via subprocess."""
    try:
        result = subprocess.run(
            ["rrdtool", "info", rrd_path],
            capture_output=True,
            text=True,
            check=True
        )
        
        info = {}
        for line in result.stdout.strip().split('\n'):
            if ' = ' in line:
                key, value = line.split(' = ', 1)
                key = key.strip()
                value = value.strip()
                
                # Parse value based on type
                if value.startswith('"') and value.endswith('"'):
                    # String value
                    info[key] = value[1:-1]
                elif value.replace('.', '').replace('-', '').isdigit():
                    # Numeric value
                    if '.' in value:
                        info[key] = float(value)
                    else:
                        info[key] = int(value)
                else:
                    # Keep as string
                    info[key] = value
        
        return info
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"rrdtool info failed for {rrd_path}: {e.stderr}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse rrdtool info output for {rrd_path}: {e}")


def rrdtool_fetch(rrd_path: str, cf: str, start: str, end: str) -> Tuple:
    """Fetch data from RRD using rrdtool fetch command via subprocess."""
    try:
        result = subprocess.run(
            ["rrdtool", "fetch", rrd_path, cf, "--start", start, "--end", end],
            capture_output=True,
            text=True,
            check=True
        )
        
        lines = result.stdout.strip().split('\n')
        if len(lines) < 2:
            return None
        
        # First line contains column headers (data sources)
        header = lines[0].strip()
        # Keep all data source names from the header
        data_sources = header.split() if header else []
        
        # Parse time range and step from the second line if it exists
        # Format: "timestamp: value1 value2 ..."
        start_timestamp = None
        end_timestamp = None
        step = None
        values = []
        
        for line in lines[1:]:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            if ':' in line:
                parts = line.split(':', 1)
                timestamp = int(parts[0].strip())
                
                if start_timestamp is None:
                    start_timestamp = timestamp
                end_timestamp = timestamp
                
                # Parse values
                value_str = parts[1].strip()
                row_values = []
                
                for val in value_str.split():
                    if val.upper() == 'NAN' or val == '-nan' or val == 'U':
                        row_values.append(None)
                    else:
                        try:
                            row_values.append(float(val))
                        except ValueError:
                            row_values.append(None)
                
                values.append(row_values)
        
        # Calculate step from consecutive timestamps
        if len(values) >= 2 and start_timestamp and end_timestamp:
            # Find first two valid timestamps to calculate step
            timestamps = []
            for i, line in enumerate(lines[1:]):
                if ':' in line:
                    ts = int(line.split(':', 1)[0].strip())
                    timestamps.append(ts)
                    if len(timestamps) >= 2:
                        break
            
            if len(timestamps) >= 2:
                step = timestamps[1] - timestamps[0]
            else:
                step = 300  # Default to 5 minutes
        else:
            step = 300  # Default to 5 minutes
        
        return ((start_timestamp, end_timestamp, step), data_sources, values)
        
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"rrdtool fetch failed for {rrd_path}: {e.stderr}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse rrdtool fetch output for {rrd_path}: {e}")


class ExporterSettings(BaseSettings):
    """Configuration settings for the ClickHouse exporter."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # ClickHouse connection
    clickhouse_url: str = Field(default="http://clickhouse:8123", description="ClickHouse HTTP URL")
    clickhouse_user: str = Field(default="smokeping", description="ClickHouse username")
    clickhouse_password: str = Field(..., description="ClickHouse password")
    clickhouse_db: str = Field(default="smokeping", description="ClickHouse database name")
    clickhouse_timeout: int = Field(default=30, ge=1, description="ClickHouse connection timeout")

    # RRD data source
    rrd_dir: Path = Field(default=Path("/var/lib/smokeping"), description="SmokePing RRD directory")
    export_interval: int = Field(default=300, ge=60, description="Export interval in seconds")
    batch_size: int = Field(default=1000, ge=100, description="Batch size for inserts")

    # Processing options
    lookback_hours: int = Field(default=2, ge=1, description="Hours to look back for new data")
    max_age_days: int = Field(default=30, ge=1, description="Maximum age of data to process")
    parallel_workers: int = Field(default=4, ge=1, le=16, description="Number of parallel workers")

    # Monitoring
    metrics_port: int = Field(default=9090, ge=1024, le=65535, description="Prometheus metrics port")
    log_level: str = Field(default="INFO", description="Logging level")
    dry_run: bool = Field(default=False, description="Dry run mode - no actual inserts")

    @field_validator("rrd_dir")
    @classmethod
    def validate_rrd_dir(cls, v: Path) -> Path:
        """Validate RRD directory exists and is readable."""
        if not v.exists():
            raise ValueError(f"RRD directory does not exist: {v}")
        if not v.is_dir():
            raise ValueError(f"RRD path is not a directory: {v}")
        return v


class RRDDataPoint(BaseModel):
    """Individual RRD data point."""
    timestamp: datetime = Field(..., description="Data point timestamp")
    target: str = Field(..., description="Target name")
    category: str = Field(..., description="Target category")
    host: str = Field(..., description="Target host")
    measurement_type: str = Field(default="latency", description="Measurement type")
    
    # Latency metrics
    min_latency: Optional[float] = Field(default=None, description="Minimum latency in ms")
    max_latency: Optional[float] = Field(default=None, description="Maximum latency in ms")
    avg_latency: Optional[float] = Field(default=None, description="Average latency in ms")
    median_latency: Optional[float] = Field(default=None, description="Median latency in ms")
    
    # Percentile metrics
    p10_latency: Optional[float] = Field(default=None, description="10th percentile latency")
    p20_latency: Optional[float] = Field(default=None, description="20th percentile latency")
    p80_latency: Optional[float] = Field(default=None, description="80th percentile latency")
    p90_latency: Optional[float] = Field(default=None, description="90th percentile latency")
    p95_latency: Optional[float] = Field(default=None, description="95th percentile latency")
    p99_latency: Optional[float] = Field(default=None, description="99th percentile latency")
    
    # Loss metrics
    packet_loss: Optional[float] = Field(default=None, description="Packet loss percentage")
    packets_sent: Optional[int] = Field(default=None, description="Packets sent")
    packets_received: Optional[int] = Field(default=None, description="Packets received")
    
    # Metadata
    rrd_file: str = Field(..., description="Source RRD file path")


class ClickHouseExporter:
    """
    SmokePing RRD to ClickHouse exporter.
    
    Processes RRD files from SmokePing and exports data to ClickHouse
    for advanced analytics and visualization.
    """

    def __init__(self, settings: ExporterSettings) -> None:
        """Initialize the exporter with settings."""
        self.settings = settings
        self.console = Console()
        self.running = True
        self.processed_files: Set[str] = set()
        self.last_processed_timestamp: Optional[int] = None
        
        # Setup signal handling
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Setup logging
        self._setup_logging()
        
        # Initialize ClickHouse client
        self.clickhouse_client = self._init_clickhouse_client()
        
        logger.info("ClickHouse exporter initialized", settings=settings.model_dump(exclude={'clickhouse_password'}))

    def _setup_logging(self) -> None:
        """Configure logging based on settings."""
        import logging
        
        log_level = getattr(logging, self.settings.log_level.upper())
        logging.getLogger().setLevel(log_level)
        
        logger.info("Logging configured", level=self.settings.log_level)

    def _init_clickhouse_client(self) -> clickhouse_connect.driver.Client:
        """Initialize ClickHouse client connection."""
        try:
            # Parse URL to get host and port
            from urllib.parse import urlparse
            parsed = urlparse(self.settings.clickhouse_url)
            
            client = clickhouse_connect.get_client(
                host=parsed.hostname,
                port=parsed.port or 8123,
                database=self.settings.clickhouse_db,
                username=self.settings.clickhouse_user,
                password=self.settings.clickhouse_password,
                connect_timeout=self.settings.clickhouse_timeout
            )
            
            # Test connection
            result = client.query("SELECT 1").result_rows
            logger.info("ClickHouse connection established", result=result)
            
            return client
            
        except Exception as e:
            logger.error("Failed to connect to ClickHouse", error=str(e))
            raise

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info("Shutdown signal received", signal=signum)
        self.running = False

    def _get_last_processed_timestamp(self) -> Optional[int]:
        """Get the latest timestamp from ClickHouse to avoid reprocessing."""
        try:
            result = self.clickhouse_client.query(
                "SELECT MAX(timestamp) as max_ts FROM smokeping.latency"
            ).result_rows
            
            if result and result[0][0]:
                # Convert datetime to timestamp
                max_datetime = result[0][0]
                if hasattr(max_datetime, 'timestamp'):
                    return int(max_datetime.timestamp())
                else:
                    # Handle string datetime
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(max_datetime))
                    return int(dt.timestamp())
            
            return None
            
        except Exception as e:
            logger.warning("Failed to get last processed timestamp", error=str(e))
            return None

    def _update_last_processed_timestamp(self, timestamp: int) -> None:
        """Update the last processed timestamp."""
        self.last_processed_timestamp = timestamp
        logger.debug("Updated last processed timestamp", timestamp=timestamp)

    def find_rrd_files(self) -> List[Path]:
        """Find all RRD files in the SmokePing directory."""
        rrd_files = []
        
        try:
            for rrd_file in self.settings.rrd_dir.rglob("*.rrd"):
                if rrd_file.is_file():
                    rrd_files.append(rrd_file)
            
            logger.info("Found RRD files", count=len(rrd_files))
            rrd_files_processed.set(len(rrd_files))
            
            return rrd_files
            
        except Exception as e:
            logger.error("Failed to find RRD files", error=str(e))
            export_errors.inc()
            return []

    def parse_rrd_file(self, rrd_file: Path) -> List[RRDDataPoint]:
        """Parse RRD file and extract data points."""
        try:
            # Extract target information from file path
            # Typical path: /var/lib/smokeping/category/target.rrd
            relative_path = rrd_file.relative_to(self.settings.rrd_dir)
            path_parts = relative_path.parts
            
            if len(path_parts) < 2:
                logger.warning("Unexpected RRD file path structure", path=str(rrd_file))
                return []
            
            category = path_parts[-2]
            target_name = rrd_file.stem
            
            # Get RRD info
            info = rrdtool_info(str(rrd_file))
            step = info.get('step', 300)  # Default 5-minute step
            
            # Calculate time range - only process new data since last run
            end_time = int(time.time())
            
            # Get last processed timestamp to avoid reprocessing
            if self.last_processed_timestamp is None:
                self.last_processed_timestamp = self._get_last_processed_timestamp()
            
            # Use last processed timestamp or fallback to lookback hours
            if self.last_processed_timestamp:
                # Start from last processed time plus buffer to avoid missing data
                start_time = self.last_processed_timestamp - 600  # 10 min buffer
                logger.debug("Using incremental processing", 
                           start_time=start_time, 
                           last_processed=self.last_processed_timestamp)
            else:
                # First run - use lookback hours
                start_time = end_time - (self.settings.lookback_hours * 3600)
                logger.info("First run - using full lookback", 
                          start_time=start_time, 
                          lookback_hours=self.settings.lookback_hours)
            
            # Fetch data from RRD
            try:
                data = rrdtool_fetch(
                    str(rrd_file),
                    'AVERAGE',
                    str(start_time),
                    str(end_time)
                )
                
                if not data or len(data) != 3:
                    logger.warning("No data returned from RRD", file=str(rrd_file))
                    return []
                
                (start_timestamp, end_timestamp, step), data_sources, values = data
                
                if not data_sources or not values:
                    logger.debug("Empty data from RRD", file=str(rrd_file))
                    return []
                
                data_points = []
                
                for i, row in enumerate(values):
                    timestamp_seconds = start_timestamp + (i * step)
                    timestamp = datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc)
                    
                    # Skip very old data
                    if timestamp < datetime.now(timezone.utc) - timedelta(days=self.settings.max_age_days):
                        continue
                    
                    # Skip NaN values
                    if not row or all(val is None or np.isnan(val) for val in row if val is not None):
                        continue
                    
                    # Map RRD data sources to our fields
                    # SmokePing typically has: loss, median, ping1, ping2, ..., ping20
                    data_point = RRDDataPoint(
                        timestamp=timestamp,
                        target=target_name,
                        category=category,
                        host=target_name,  # Assume target name is the host
                        rrd_file=str(rrd_file)
                    )
                    
                    # Map data based on available sources
                    for j, source in enumerate(data_sources):
                        if j < len(row) and row[j] is not None and not np.isnan(row[j]):
                            value = float(row[j])
                            
                            if source == 'loss':
                                data_point.packet_loss = value * 100  # Convert to percentage
                            elif source == 'median':
                                data_point.median_latency = value * 1000  # Convert to milliseconds
                            elif source.startswith('ping'):
                                # Individual ping values could be used for percentiles
                                # For now, use median as average
                                if data_point.avg_latency is None:
                                    data_point.avg_latency = value * 1000
                    
                    # Calculate some derived metrics if we have individual pings
                    ping_values = []
                    for j, source in enumerate(data_sources):
                        if source.startswith('ping') and j < len(row) and row[j] is not None:
                            if not np.isnan(row[j]):
                                ping_values.append(row[j] * 1000)  # Convert to ms
                    
                    if ping_values:
                        ping_array = np.array(ping_values)
                        data_point.min_latency = float(np.min(ping_array))
                        data_point.max_latency = float(np.max(ping_array))
                        data_point.avg_latency = float(np.mean(ping_array))
                        
                        # Calculate percentiles
                        if len(ping_array) >= 5:  # Need enough samples
                            data_point.p10_latency = float(np.percentile(ping_array, 10))
                            data_point.p20_latency = float(np.percentile(ping_array, 20))
                            data_point.p80_latency = float(np.percentile(ping_array, 80))
                            data_point.p90_latency = float(np.percentile(ping_array, 90))
                            data_point.p95_latency = float(np.percentile(ping_array, 95))
                            data_point.p99_latency = float(np.percentile(ping_array, 99))
                        
                        data_point.packets_sent = len(ping_array)
                        data_point.packets_received = len(ping_array) - int(ping_array.size * (data_point.packet_loss or 0) / 100)
                    
                    data_points.append(data_point)
                    
                    # Track the latest timestamp processed
                    timestamp_seconds = int(timestamp.timestamp())
                    if (self.last_processed_timestamp is None or 
                        timestamp_seconds > self.last_processed_timestamp):
                        self._update_last_processed_timestamp(timestamp_seconds)
                
                logger.debug("Parsed RRD file", file=str(rrd_file), points=len(data_points))
                return data_points
                
            except Exception as e:
                logger.error("Failed to fetch RRD data", file=str(rrd_file), error=str(e))
                return []
                
        except Exception as e:
            logger.error("Failed to parse RRD file", file=str(rrd_file), error=str(e))
            export_errors.inc()
            return []

    def insert_data_points(self, data_points: List[RRDDataPoint]) -> bool:
        """Insert data points into ClickHouse."""
        if not data_points:
            return True
        
        if self.settings.dry_run:
            logger.info("Dry run - would insert data points", count=len(data_points))
            return True
        
        try:
            # Convert to DataFrame for batch insert
            df_data = []
            for point in data_points:
                df_data.append({
                    'timestamp': point.timestamp,
                    'target': point.target,
                    'category': point.category,
                    'host': point.host,
                    'measurement_type': point.measurement_type,
                    'min_latency': point.min_latency,
                    'max_latency': point.max_latency,
                    'avg_latency': point.avg_latency,
                    'median_latency': point.median_latency,
                    'p10_latency': point.p10_latency,
                    'p20_latency': point.p20_latency,
                    'p80_latency': point.p80_latency,
                    'p90_latency': point.p90_latency,
                    'p95_latency': point.p95_latency,
                    'p99_latency': point.p99_latency,
                    'packet_loss': point.packet_loss,
                    'packets_sent': point.packets_sent,
                    'packets_received': point.packets_received,
                    'rrd_file': point.rrd_file
                })
            
            df = pd.DataFrame(df_data)
            
            # Insert to ClickHouse
            self.clickhouse_client.insert_df('latency', df)
            
            clickhouse_rows_inserted.inc(len(data_points))
            logger.info("Inserted data points to ClickHouse", count=len(data_points))
            
            return True
            
        except Exception as e:
            logger.error("Failed to insert data points", error=str(e), count=len(data_points))
            export_errors.inc()
            return False

    def process_all_files(self) -> None:
        """Process all RRD files and export to ClickHouse."""
        with export_duration.time():
            rrd_files = self.find_rrd_files()
            
            if not rrd_files:
                logger.warning("No RRD files found")
                return
            
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console
            ) as progress:
                
                task = progress.add_task("Processing RRD files...", total=len(rrd_files))
                
                for rrd_file in rrd_files:
                    if not self.running:
                        logger.info("Shutdown requested, stopping processing")
                        break
                    
                    progress.update(task, description=f"Processing {rrd_file.name}")
                    
                    try:
                        data_points = self.parse_rrd_file(rrd_file)
                        
                        if data_points:
                            # Process in batches
                            for i in range(0, len(data_points), self.settings.batch_size):
                                batch = data_points[i:i + self.settings.batch_size]
                                self.insert_data_points(batch)
                        
                        self.processed_files.add(str(rrd_file))
                        export_counter.inc()
                        
                    except Exception as e:
                        logger.error("Failed to process RRD file", file=str(rrd_file), error=str(e))
                        export_errors.inc()
                    
                    progress.advance(task)
            
            logger.info("Processing completed", 
                       processed=len(self.processed_files),
                       total=len(rrd_files))

    def run_continuous(self) -> None:
        """Run the exporter in continuous mode."""
        logger.info("Starting continuous export", interval=self.settings.export_interval)
        
        # Start Prometheus metrics server
        start_http_server(self.settings.metrics_port)
        logger.info("Prometheus metrics server started", port=self.settings.metrics_port)
        
        while self.running:
            try:
                start_time = time.time()
                
                logger.info("Starting export cycle")
                self.process_all_files()
                
                duration = time.time() - start_time
                logger.info("Export cycle completed", duration=duration)
                
                # Sleep until next interval
                sleep_time = max(0, self.settings.export_interval - duration)
                if sleep_time > 0:
                    logger.info("Sleeping until next cycle", seconds=sleep_time)
                    time.sleep(sleep_time)
                
            except Exception as e:
                logger.error("Export cycle failed", error=str(e))
                export_errors.inc()
                time.sleep(60)  # Wait before retrying
        
        logger.info("Exporter stopped")


@click.command()
@click.option('--config-file', '-c', type=click.Path(exists=True), 
              help='Configuration file path')
@click.option('--dry-run', is_flag=True, help='Dry run mode - no actual inserts')
@click.option('--once', is_flag=True, help='Run once and exit')
@click.option('--verbose', '-v', is_flag=True, help='Verbose logging')
def main(config_file: Optional[str], dry_run: bool, once: bool, verbose: bool) -> None:
    """SmokePing RRD to ClickHouse exporter."""
    
    # Load settings
    if config_file:
        settings = ExporterSettings(_env_file=config_file)
    else:
        settings = ExporterSettings()
    
    # Override settings from CLI options
    if dry_run:
        settings.dry_run = True
    if verbose:
        settings.log_level = "DEBUG"
    
    try:
        exporter = ClickHouseExporter(settings)
        
        if once:
            logger.info("Running single export cycle")
            exporter.process_all_files()
        else:
            logger.info("Running continuous export")
            exporter.run_continuous()
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Exporter failed", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()