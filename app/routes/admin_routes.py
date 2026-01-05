# app/routes/admin_routes.py
"""
Admin Panel Routes
- User management (create, update, delete, toggle status)
- Case assignment (single and bulk)
"""

from fastapi import APIRouter, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import engine, get_db
from app.services.auth_service import get_current_user, create_user
from app.auth import get_password_hash

router = APIRouter()

# Templates - will be set by main.py
templates = None

def init_templates(t):
    """Initialize templates from main app"""
    global templates
    templates = t


def require_admin(request: Request):
    """Check if current user is admin"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("role") != "admin" and not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ============================================================
# ADMIN PANEL PAGE
# ============================================================

@router.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, db: Session = Depends(get_db)):
    """Admin panel page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    if user.get("role") != "admin" and not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    with engine.connect() as conn:
        # Get users with assignment counts
        users_result = conn.execute(text('''
            SELECT u.id, u.email, u.full_name, u.role, u.is_active, u.last_login,
                   COUNT(c.id) as assigned_count
            FROM users u
            LEFT JOIN cases c ON c.assigned_to = u.id AND (c.archived IS NULL OR c.archived = 0)
            GROUP BY u.id
            ORDER BY u.id
        ''')).fetchall()
        
        users = [{
            "id": r[0], "email": r[1], "full_name": r[2], "role": r[3],
            "is_active": bool(r[4]) if r[4] is not None else True,
            "last_login": r[5], "assigned_count": r[6] or 0
        } for r in users_result]
        
        # Get cases
        cases_result = conn.execute(text('''
            SELECT id, case_number, address, address_override, filing_datetime, assigned_to
            FROM cases WHERE archived IS NULL OR archived = 0
            ORDER BY id DESC LIMIT 500
        ''')).fetchall()
        
        cases = [{
            "id": r[0], "case_number": r[1], "address": r[2],
            "address_override": r[3], "filing_datetime": r[4], "assigned_to": r[5]
        } for r in cases_result]
        
        stats = {
            "total_users": len(users),
            "active_users": sum(1 for u in users if u["is_active"]),
            "total_cases": len(cases),
            "unassigned_cases": sum(1 for c in cases if not c["assigned_to"])
        }
    
    return templates.TemplateResponse("admin.html", {
        "request": request, "user": user, "current_user": user,
        "users": users, "cases": cases, "stats": stats
    })


# ============================================================
# USER MANAGEMENT API
# ============================================================

@router.post("/api/admin/users")
def api_create_user(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Create a new user"""
    user = require_admin(request)
    
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    full_name = body.get("full_name", "").strip()
    role = body.get("role", "analyst")
    
    if not all([email, password, full_name]):
        raise HTTPException(status_code=400, detail="All fields required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters")
    if role not in ["admin", "analyst", "owner", "subscriber"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    
    try:
        user_id = create_user(email, password, full_name, role)
        return {"status": "success", "user_id": user_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/admin/users/{user_id}")
def api_update_user(user_id: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Update an existing user"""
    user = require_admin(request)
    
    updates, params = [], {"user_id": user_id}
    if body.get("email"):
        updates.append("email = :email")
        params["email"] = body["email"].lower()
    if body.get("full_name"):
        updates.append("full_name = :full_name")
        params["full_name"] = body["full_name"]
    if body.get("role") in ["admin", "analyst", "owner", "subscriber"]:
        updates.append("role = :role")
        params["role"] = body["role"]
    if body.get("password") and len(body["password"]) >= 8:
        updates.append("hashed_password = :password")
        params["password"] = get_password_hash(body["password"])
    
    if updates:
        with engine.begin() as conn:
            conn.execute(text(f"UPDATE users SET {', '.join(updates)} WHERE id = :user_id"), params)
    return {"status": "success"}


@router.delete("/api/admin/users/{user_id}")
def api_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a user"""
    user = require_admin(request)
    if user.get("id") == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    with engine.begin() as conn:
        conn.execute(text("UPDATE cases SET assigned_to = NULL WHERE assigned_to = :id"), {"id": user_id})
        conn.execute(text("DELETE FROM sessions WHERE user_id = :id"), {"id": user_id})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    return {"status": "success"}


@router.post("/api/admin/users/{user_id}/toggle-status")
def api_toggle_status(user_id: int, request: Request, db: Session = Depends(get_db)):
    """Toggle user active/inactive status"""
    user = require_admin(request)
    if user.get("id") == user_id:
        raise HTTPException(status_code=400, detail="Cannot disable yourself")
    
    with engine.begin() as conn:
        result = conn.execute(text("SELECT is_active FROM users WHERE id = :id"), {"id": user_id}).fetchone()
        new_status = 0 if result and result[0] else 1
        conn.execute(text("UPDATE users SET is_active = :s WHERE id = :id"), {"id": user_id, "s": new_status})
        if new_status == 0:
            conn.execute(text("DELETE FROM sessions WHERE user_id = :id"), {"id": user_id})
    return {"status": "success", "is_active": bool(new_status)}


# ============================================================
# CASE ASSIGNMENT API
# ============================================================

@router.post("/api/admin/cases/{case_id}/assign")
def api_assign_case(case_id: int, request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Assign a case to a user"""
    user = require_admin(request)
    
    user_id = body.get("user_id")
    with engine.begin() as conn:
        conn.execute(text("UPDATE cases SET assigned_to = :uid WHERE id = :cid"),
                    {"cid": case_id, "uid": user_id if user_id else None})
    return {"status": "success"}


@router.post("/api/admin/cases/bulk-assign")
def api_bulk_assign(request: Request, body: dict = Body(...), db: Session = Depends(get_db)):
    """Bulk assign multiple cases to a user"""
    user = require_admin(request)
    
    case_ids = body.get("case_ids", [])
    user_id = body.get("user_id")
    if not case_ids:
        raise HTTPException(status_code=400, detail="No cases selected")
    
    placeholders = ", ".join([f":id_{i}" for i in range(len(case_ids))])
    params = {"user_id": user_id if user_id else None}
    for i, cid in enumerate(case_ids):
        params[f"id_{i}"] = cid
    
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE cases SET assigned_to = :user_id WHERE id IN ({placeholders})"), params)
    return {"status": "success", "updated": len(case_ids)}
