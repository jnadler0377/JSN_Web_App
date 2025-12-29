# app/settings.py
from __future__ import annotations

import os
from pathlib import Path

# Try to load .env if python-dotenv is installed; otherwise it's a no-op.
try:
    from dotenv import load_dotenv  # type: ignore

    # Project root = one level above /app
    _ROOT = Path(__file__).resolve().parents[1]
    _DOTENV_PATH = _ROOT / ".env"
    if _DOTENV_PATH.exists():
        load_dotenv(dotenv_path=_DOTENV_PATH)
    else:
        load_dotenv()
except Exception:
    pass


class Settings:
    def __init__(self) -> None:
        # Reads from environment or .env (if loaded)
        self.GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")

        # BatchData
        self.BATCHDATA_API_KEY: str = os.getenv("BATCHDATA_API_KEY", "")
        self.BATCHDATA_BASE_URL: str = os.getenv("BATCHDATA_BASE_URL", "https://api.batchdata.com/api/v1")

        # Logging
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()


settings = Settings()
