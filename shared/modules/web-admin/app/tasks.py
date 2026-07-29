"""
Background tasks for the web admin
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

def refresh_ocas_task():
    """Background task to refresh Netflix OCA servers via config-manager API"""
    logger.info("Starting scheduled OCA refresh...")

    try:
        from app.services.config_api import ConfigAPIGateway

        result = ConfigAPIGateway().refresh_oca_data()
        if result.get('success'):
            logger.info("Scheduled OCA refresh completed successfully")
        else:
            logger.error(f"Scheduled OCA refresh failed: {result.get('message')}")

    except Exception as e:
        logger.error(f"Error during scheduled OCA refresh: {str(e)}")

def cleanup_cache_task():
    """Background task to clean up expired cache files"""
    logger.info("Starting scheduled cache cleanup...")

    cache_directories = [
        Path('/tmp/cloudflare_cache'),
        Path('/tmp/crux_cache'),
        Path('/tmp/tranco_cache')
    ]

    # Files older than 24 hours (in seconds)
    max_age = 24 * 60 * 60
    current_time = time.time()

    total_cleaned = 0

    try:
        for cache_dir in cache_directories:
            if not cache_dir.exists():
                continue

            logger.info(f"Cleaning cache directory: {cache_dir}")

            for cache_file in cache_dir.iterdir():
                if cache_file.is_file():
                    file_age = current_time - cache_file.stat().st_mtime

                    if file_age > max_age:
                        try:
                            cache_file.unlink()
                            total_cleaned += 1
                            logger.debug(f"Removed expired cache file: {cache_file}")
                        except Exception as e:
                            logger.error(f"Failed to remove cache file {cache_file}: {e}")

        logger.info(f"Scheduled cache cleanup completed. Removed {total_cleaned} expired files")

    except Exception as e:
        logger.error(f"Error during scheduled cache cleanup: {str(e)}")
