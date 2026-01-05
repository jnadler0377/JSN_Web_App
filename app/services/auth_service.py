# app/services/auth_service.py
from __future__ import annotations

import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import text
from fastapi import HTTPException, Request, Depends
from fastapi.responses import RedirectResponse

from app.config import settings
from app.database import engine, SessionLocal
from app.auth import User, get_password_hash, verify_password

logger = logging.getLogger("pascowebapp.auth")





# ========================================
# Password Hashing
# ========================================

#def hash_password(password: str) -> str:
#    """Hash a password using bcrypt"""
#    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
#    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


#def verify_password(password: str, hashed: str) -> bool:
#    """Verify a password against a hash"""
#    try:
#        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
#    except Exception as exc:
#        logger.error(f"Password verification failed: {exc}")
#        return False


# ========================================
# Session Management
# ========================================

def create_session(user_id: int) -> str:
    """
    Create a new session token for a user
    Returns the session token
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=settings.session_expire_minutes)
    
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO sessions (user_id, token, expires_at, created_at)
                VALUES (:user_id, :token, :expires_at, datetime('now'))
            """),
            {
                "user_id": user_id,
                "token": token,
                "expires_at": expires_at.isoformat(),
            }
        )
    
    return token


def validate_session(token: str) -> Optional[int]:
    """
    Validate a session token
    Returns user_id if valid, None otherwise
    """
    if not token:
        return None
    
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT user_id, expires_at 
                FROM sessions 
                WHERE token = :token
            """),
            {"token": token}
        ).fetchone()
    
    if not result:
        return None
    
    user_id, expires_at_str = result
    expires_at = datetime.fromisoformat(expires_at_str)
    
    if expires_at < datetime.utcnow():
        # Session expired
        delete_session(token)
        return None
    
    return int(user_id)


def delete_session(token: str) -> None:
    """Delete a session token"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM sessions WHERE token = :token"),
            {"token": token}
        )


def delete_all_user_sessions(user_id: int) -> None:
    """Delete all sessions for a user (logout from all devices)"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM sessions WHERE user_id = :user_id"),
            {"user_id": user_id}
        )


# ========================================
# User CRUD Operations
# ========================================

def create_user(email: str, password: str, full_name: str, role: str = "analyst") -> int:
    """
    Create a new user
    Returns user_id
    """
    if role not in ["admin", "analyst", "owner", "subscriber"]:
        raise ValueError(f"Invalid role: {role}")
    
    hashed = get_password_hash(password)
    
    with engine.begin() as conn:
        # Check if email already exists
        existing = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email.lower()}
        ).fetchone()
        
        if existing:
            raise ValueError(f"User with email {email} already exists")
        
        result = conn.execute(
            text("""
                INSERT INTO users (email, hashed_password, full_name, role, created_at)
                VALUES (:email, :password, :name, :role, datetime('now'))
            """),
            {
                "email": email.lower(),
                "password": hashed,
                "name": full_name,
                "role": role,
            }
        )
        
        return result.lastrowid


def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email"""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, email, hashed_password, full_name, role, is_active
                FROM users
                WHERE email = :email
            """),
            {"email": email.lower()}
        ).mappings().fetchone()
    
    return dict(result) if result else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Get user by ID"""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT id, email, full_name, role, is_active, last_login, is_admin
                FROM users
                WHERE id = :user_id
            """),
            {"user_id": user_id}
        ).mappings().fetchone()
    
    return dict(result) if result else None


def update_last_login(user_id: int) -> None:
    """Update user's last login timestamp"""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET last_login = datetime('now') WHERE id = :user_id"),
            {"user_id": user_id}
        )


def authenticate_user(email: str, password: str) -> Tuple[bool, Optional[dict]]:
    """
    Authenticate a user
    Returns (success, user_dict)
    """
    user = get_user_by_email(email)
    
    if not user:
        return False, None
    
    if not user.get("is_active"):
        logger.warning(f"Inactive user attempted login: {email}")
        return False, None
    
    if not verify_password(password, user["hashed_password"]):
        logger.warning(f"Failed login attempt for: {email}")
        return False, None
    
    # Update last login
    update_last_login(user["id"])
    
    return True, user


# ========================================
# FastAPI Dependencies
# ========================================

def get_session_token(request: Request) -> Optional[str]:
    """Extract session token from cookie"""
    return request.cookies.get("session_token")


def get_current_user(request: Request) -> dict:
    """
    Dependency to get current authenticated user
    Raises 401 if not authenticated
    """
    if not settings.enable_multi_user:
        # Multi-user disabled, return default admin user
        return {
            "id": 0,
            "email": "admin@localhost",
            "full_name": "Administrator",
            "role": "admin",
        }
    
    token = get_session_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = validate_session(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return user


def require_role(allowed_roles: list[str]):
    """
    Dependency factory to require specific roles
    
    Usage:
        @app.get("/admin")
        def admin_panel(user: dict = Depends(require_role(["admin"]))):
            ...
    """
    def role_checker(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required roles: {allowed_roles}"
            )
        return user
    
    return role_checker


# ========================================
# Login/Logout Helpers
# ========================================

def login_user(email: str, password: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Login a user
    Returns (success, session_token, error_message)
    """
    success, user = authenticate_user(email, password)
    
    if not success:
        return False, None, "Invalid email or password"
    
    # Create session
    token = create_session(user["id"])
    
    logger.info(f"User logged in: {email} (ID: {user['id']})")
    return True, token, None


def logout_user(token: str) -> None:
    """Logout a user by deleting their session"""
    delete_session(token)


# ========================================
# Role-Based Access Control
# ========================================

def can_view_case(user: dict, case) -> bool:
    """Check if user can view a case"""
    if user["role"] == "admin":
        return True
    
    if user["role"] == "viewer":
        # Viewers can see all cases
        return True
    
    # Analysts and closers can see assigned cases or unassigned cases
    # This would need case assignment logic
    return True  # Simplified for now


def can_edit_case(user: dict, case) -> bool:
    """Check if user can edit a case"""
    if user["role"] == "admin":
        return True
    
    if user["role"] == "viewer":
        return False
    
    # Analysts and closers can edit assigned cases
    return True  # Simplified for now


# ========================================
# Initial Setup
# ========================================

def create_default_admin():
    """
    Create default admin user if no users exist
    Call this on app startup
    """
    if not settings.enable_multi_user:
        return
    
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    
    if count > 0:
        # Users already exist
        return
    
    # Generate secure random password
    import string
    import secrets as sec
    
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()"
    admin_password = ''.join(sec.choice(alphabet) for _ in range(20))
    
    # Create default admin
    try:
        admin_id = create_user(
            email="admin@localhost",
            password=admin_password,
            full_name="System Administrator",
            role="admin"
        )
        
        # Save password to file
        from pathlib import Path
        password_file = Path("ADMIN_PASSWORD.txt")
        with open(password_file, "w") as f:
            f.write("="*60 + "\n")
            f.write("DEFAULT ADMIN CREDENTIALS\n")
            f.write("="*60 + "\n")
            f.write(f"Email: admin@localhost\n")
            f.write(f"Password: {admin_password}\n")
            f.write(f"Generated: {datetime.utcnow().isoformat()}\n")
            f.write("\n")
            f.write("⚠️  IMPORTANT:\n")
            f.write("1. Log in and change this password immediately!\n")
            f.write("2. Delete this file after first login!\n")
            f.write("="*60 + "\n")
        
        logger.critical("="*60)
        logger.critical("🔐 DEFAULT ADMIN CREATED")
        logger.critical(f"📧 Email: admin@localhost")
        logger.critical(f"📄 Password saved to: {password_file.absolute()}")
        logger.critical("⚠️  CHANGE PASSWORD IMMEDIATELY AFTER FIRST LOGIN!")
        logger.critical("⚠️  DELETE ADMIN_PASSWORD.txt AFTER FIRST LOGIN!")
        logger.critical("="*60)
        
    except Exception as exc:
        logger.error(f"Failed to create default admin: {exc}")

# ========================================
# Audit Logging
# ========================================

def log_action(
    user_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    changes_json: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Log an action to the audit trail"""
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO audit_logs 
                    (user_id, action, entity_type, entity_id, changes_json, 
                     ip_address, user_agent, timestamp)
                    VALUES (:user_id, :action, :entity_type, :entity_id, :changes, 
                            :ip, :ua, datetime('now'))
                """),
                {
                    "user_id": user_id,
                    "action": action,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "changes": changes_json,
                    "ip": ip_address,
                    "ua": user_agent,
                }
            )
    except Exception as exc:
        logger.error(f"Failed to log action: {exc}")
