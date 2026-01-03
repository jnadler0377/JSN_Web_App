# app/config.py
"""
Application Configuration
✅ NO CIRCULAR IMPORTS - Only imports from standard library and pydantic
✅ NO DUPLICATE DEFINITIONS - Clean, single source of truth
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root
BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """
    Application configuration with environment variable support
    
    All settings can be overridden via environment variables or .env file.
    Environment variables take precedence over .env file values.
    """
    
    # ========== Database ==========
    database_url: str = f"sqlite:///{BASE_DIR}/foreclosures.db"
    
    # ========== External APIs ==========
    google_maps_api_key: str = ""
    batchdata_api_key: str = ""
    batchdata_base_url: str = "https://api.batchdata.com/api/v1"
    
    # ========== Feature Flags ==========
    enable_skip_trace: bool = True
    enable_property_lookup: bool = True
    enable_ocr: bool = False  # Requires additional setup (tesseract or AWS Textract)
    enable_comparables: bool = True
    enable_analytics: bool = True
    enable_multi_user: bool = False  # Set to True to enable user authentication
    
    # ========== Performance ==========
    max_workers: int = 4
    cache_ttl_seconds: int = 3600
    request_timeout_seconds: int = 30
    
    # ========== Redis (optional) ==========
    redis_url: Optional[str] = None  # e.g., "redis://localhost:6379/0"
    enable_redis_cache: bool = False
    
    # ========== Celery (optional) ==========
    celery_broker_url: Optional[str] = None  # e.g., "redis://localhost:6379/1"
    celery_result_backend: Optional[str] = None
    enable_celery: bool = False
    
    # ========== Security ==========
    secret_key: str = os.getenv("SECRET_KEY", "CHANGE-ME-IN-PRODUCTION-USE-LONG-RANDOM-STRING")
    session_expire_minutes: int = 60 * 24  # 24 hours
    bcrypt_rounds: int = 12
    
    # ========== Logging ==========
    log_level: str = "INFO"
    log_file: Optional[str] = None  # None = console only, otherwise path to log file
    
    # ========== Email Notifications (optional) ==========
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    
    # ========== Application ==========
    app_name: str = "JSN Holdings Foreclosure Manager"
    items_per_page: int = 25
    upload_max_size_mb: int = 50
    
    # ========== OCR Settings ==========
    ocr_engine: str = "tesseract"  # Options: "tesseract" or "aws_textract"
    aws_region: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    
    # ========== Scraper Settings ==========
    pasco_user: str = ""
    pasco_pass: str = ""
    scraper_headless: bool = True
    scraper_max_records: int = 0  # 0 = unlimited
    
    # ========== Pydantic Configuration ==========
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore unknown env vars
    )
    
    # ========== Computed Properties ==========
    
    @property
    def database_path(self) -> Path:
        """
        Extract file path from sqlite URL
        
        Returns:
            Path object pointing to the database file
        """
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.replace("sqlite:///", ""))
        return BASE_DIR / "foreclosures.db"
    
    @property
    def is_redis_enabled(self) -> bool:
        """Check if Redis caching is configured and enabled"""
        return self.enable_redis_cache and self.redis_url is not None
    
    @property
    def is_celery_enabled(self) -> bool:
        """Check if Celery background tasks are configured and enabled"""
        return self.enable_celery and self.celery_broker_url is not None
    
    # ========== Validation ==========
    
    def validate_secret_key(self) -> None:
        """
        Validate that secret key is properly configured for production
        
        Raises:
            ValueError: If secret key is not set or too short
        """
        if not self.secret_key or self.secret_key == "CHANGE-ME-IN-PRODUCTION-USE-LONG-RANDOM-STRING":
            raise ValueError(
                "SECRET_KEY must be set in production! "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        
        if len(self.secret_key) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters for security. "
                f"Current length: {len(self.secret_key)}"
            )


# ========== Global Settings Instance ==========
# Create single instance - do not duplicate!
settings = Settings()


# ========== Validation on Import ==========
# Uncomment this in production to enforce secret key validation:
# settings.validate_secret_key()
