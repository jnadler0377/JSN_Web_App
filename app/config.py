# app/config.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root
BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Application configuration with environment variable support"""
    
    # ========== Database ==========
    database_url: str = f"sqlite:///{BASE_DIR}/foreclosures.db"
    
    # ========== External APIs ==========
    google_maps_api_key: str = ""
    batchdata_api_key: str = ""
    batchdata_base_url: str = "https://api.batchdata.com/api/v1"
    
    # ========== Feature Flags ==========
    enable_skip_trace: bool = True
    enable_property_lookup: bool = True
    enable_ocr: bool = False  # Requires additional setup
    enable_comparables: bool = True
    enable_analytics: bool = True
    enable_multi_user: bool = False  # Set to True when ready
    
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
    secret_key: str = "CHANGE_THIS_IN_PRODUCTION_TO_RANDOM_STRING"
    session_expire_minutes: int = 60 * 24  # 24 hours
    bcrypt_rounds: int = 12
    
    # ========== Logging ==========
    log_level: str = "INFO"
    log_file: Optional[str] = None  # None = console only
    
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
    ocr_engine: str = "tesseract"  # tesseract or aws_textract
    aws_region: Optional[str] = None
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    
    # ========== Scraper Settings ==========
    pasco_user: str = ""
    pasco_pass: str = ""
    scraper_headless: bool = True
    scraper_max_records: int = 0  # 0 = unlimited
    
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    @property
    def database_path(self) -> Path:
        """Extract file path from sqlite URL"""
        if self.database_url.startswith("sqlite:///"):
            return Path(self.database_url.replace("sqlite:///", ""))
        return BASE_DIR / "foreclosures.db"
    
    @property
    def is_redis_enabled(self) -> bool:
        return self.enable_redis_cache and self.redis_url is not None
    
    @property
    def is_celery_enabled(self) -> bool:
        return self.enable_celery and self.celery_broker_url is not None


# Global settings instance
 # ... all your existing fields ...
    
    log_level: str = "INFO"
    google_maps_api_key: str = ""
    batchdata_api_key: str = ""
    items_per_page: int = 25
    
    # ... rest of fields ...
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # ADD THESE PROPERTIES for backward compatibility:
    @property
    def LOG_LEVEL(self):
        return self.log_level.upper()
    
    @property
    def GOOGLE_MAPS_API_KEY(self):
        return self.google_maps_api_key
    
    @property
    def BATCHDATA_API_KEY(self):
        return self.batchdata_api_key
    
    @property
    def ITEMS_PER_PAGE(self):
        return self.items_per_page
    
    @property
    def APP_NAME(self):
        return self.app_name
    
    @property
    def DATABASE_URL(self):
        return self.database_url

settings = Settings()
