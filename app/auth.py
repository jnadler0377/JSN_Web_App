# app/auth.py
"""
Authentication utilities - Password hashing and verification
✅ NO CIRCULAR IMPORTS - Only imports from config and standard library
"""

from __future__ import annotations

import bcrypt
import logging
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    # Type hints only, not imported at runtime
    pass

logger = logging.getLogger("pascowebapp.auth")


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt
    
    Args:
        password: Plain text password
    
    Returns:
        Bcrypt hash of the password
    
    Example:
        >>> hashed = get_password_hash("my_password")
        >>> hashed.startswith("$2b$")
        True
    """
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a bcrypt hash
    
    Args:
        plain_password: Plain text password to verify
        hashed_password: Bcrypt hash to verify against
    
    Returns:
        True if password matches hash, False otherwise
    
    Example:
        >>> hashed = get_password_hash("test123")
        >>> verify_password("test123", hashed)
        True
        >>> verify_password("wrong", hashed)
        False
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception as exc:
        logger.error(f"Password verification failed: {exc}")
        return False


# ========================================
# Re-export User model for backward compatibility
# ========================================

def __getattr__(name: str):
    """
    Lazy import of User model to avoid circular imports
    
    This allows: from app.auth import User
    Without creating circular import at module load time
    """
    if name == "User":
        from app.models import User as UserModel
        return UserModel
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
