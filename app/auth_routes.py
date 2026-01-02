# app/auth_routes.py - Authentication routes
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from app.database import get_db, templates
from app.auth import User, verify_password, get_password_hash
from app.services.auth_service import create_session, validate_session, delete_session

router = APIRouter()

def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Get the currently logged-in user from session"""
    session_token = request.cookies.get("session_token")
    if not session_token:
        return None

    user_id = validate_session(session_token)
    if not user_id:
        return None
    
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    return user


def require_auth(request: Request, db: Session = Depends(get_db)) -> User:
    """Require authentication, redirect to login if not authenticated"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    """Require admin privileges"""
    user = require_auth(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Display login page"""
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
):
    """Process login (accepts form or JSON with username/email + password)"""
    content_type = (request.headers.get("content-type") or "").lower()
    data = {}
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    username = (data.get("username") or data.get("email") or "").strip()
    password = (data.get("password") or "").strip()

    if not username or not password:
        if "application/json" in content_type:
            raise HTTPException(status_code=400, detail="username/email and password required")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Username/email and password required"},
        )

    user = db.query(User).filter(
        (User.username == username) | (User.email == username)
    ).first()

    if not user or not user.verify_password(password) or not user.is_active:
        if "application/json" in content_type:
            raise HTTPException(status_code=401, detail="Invalid username/email or password")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username/email or password"},
        )

    # Create session (DB-backed)
    session_token = create_session(user.id)

    # Update last login
    user.last_login = datetime.now()
    db.commit()

    # Set cookie and redirect
    response = RedirectResponse(url="/cases", status_code=303)
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        max_age=86400 * 7  # 7 days
    )
    return response


@router.get("/logout")
def logout(request: Request):
    """Logout user"""
    session_token = request.cookies.get("session_token")
    if session_token:
        delete_session(session_token)
    
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@router.get("/users", response_class=HTMLResponse)
def users_list(request: Request, current_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """List all users (admin only)"""
    users = db.query(User).order_by(User.username).all()
    return templates.TemplateResponse(
        "user_list.html",
        {"request": request, "users": users, "current_user": current_user}
    )


@router.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request, current_user: User = Depends(require_admin)):
    """Display new user form (admin only)"""
    return templates.TemplateResponse(
        "user_form.html",
        {"request": request, "user": None, "error": None, "current_user": current_user}
    )


@router.post("/users/create")
def create_user(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    is_admin: bool = Form(False),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Create a new user (admin only)"""
    # Check if username or email already exists
    existing = db.query(User).filter(
        (User.username == username) | (User.email == email)
    ).first()
    
    if existing:
        return templates.TemplateResponse(
            "user_form.html",
            {
                "request": request,
                "user": None,
                "error": "Username or email already exists",
                "current_user": current_user
            }
        )
    
    # Create user
    user = User(
        username=username,
        email=email,
        hashed_password=get_password_hash(password),
        full_name=full_name,
        is_admin=is_admin
    )
    db.add(user)
    db.commit()
    
    return RedirectResponse(url="/users", status_code=303)


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, current_user: User = Depends(require_auth)):
    """Display user profile"""
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": current_user, "error": None, "success": None}
    )


@router.post("/profile/change-password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Change user password"""
    # Verify current password
    if not current_user.verify_password(current_password):
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "user": current_user,
                "error": "Current password is incorrect",
                "success": None
            }
        )
    
    # Verify new passwords match
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "user": current_user,
                "error": "New passwords do not match",
                "success": None
            }
        )
    
    # Verify password strength
    if len(new_password) < 8:
        return templates.TemplateResponse(
            "profile.html",
            {
                "request": request,
                "user": current_user,
                "error": "Password must be at least 8 characters",
                "success": None
            }
        )
    
    # Update password
    current_user.hashed_password = get_password_hash(new_password)
    db.commit()
    
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": current_user,
            "error": None,
            "success": "Password changed successfully"
        }
    )


@router.post("/users/{user_id}/toggle-active")
def toggle_user_active(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Toggle user active status (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Don't allow disabling yourself
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")
    
    user.is_active = not user.is_active
    db.commit()
    
    return RedirectResponse(url="/users", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Delete a user (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Don't allow deleting yourself
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    
    db.delete(user)
    db.commit()
    
    return RedirectResponse(url="/users", status_code=303)
