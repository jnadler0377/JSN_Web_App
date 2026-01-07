# app/routes/admin_v3_routes.py
"""
Admin Routes Extension - V3 Phase 6
Additional admin routes for billing and claims management.

Add these routes to your existing admin_routes.py or include this router.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Body
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, text

from app.database import get_db
from app.services.auth_service import get_current_user

logger = logging.getLogger("pascowebapp.admin")

router = APIRouter(prefix="/admin", tags=["admin-v3"])

# Template reference - will be set by main.py
templates = None


def init_admin_v3_templates(template_engine):
    """Initialize templates for admin V3 routes."""
    global templates
    templates = template_engine


def require_admin(request: Request):
    """Require admin access for these routes."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return user


# ============================================================
# Page Routes
# ============================================================

@router.get("/billing", response_class=HTMLResponse)
def admin_billing_page(request: Request, db: Session = Depends(get_db)):
    """Admin billing dashboard page."""
    user = require_admin(request)
    
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    return templates.TemplateResponse("admin_billing.html", {
        "request": request,
        "current_user": user,
    })


@router.get("/claims", response_class=HTMLResponse)
def admin_claims_page(request: Request, db: Session = Depends(get_db)):
    """Admin claims management page."""
    user = require_admin(request)
    
    if not templates:
        raise HTTPException(status_code=500, detail="Templates not configured")
    
    return templates.TemplateResponse("admin_claims.html", {
        "request": request,
        "current_user": user,
    })


# ============================================================
# Claims API Endpoints
# ============================================================

@router.get("/v3/claims")
def api_admin_list_claims(
    request: Request,
    active_only: bool = Query(False),
    user_id: Optional[int] = Query(None),
    case_id: Optional[int] = Query(None),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db)
):
    """List all claims (admin only)."""
    require_admin(request)
    
    from app.models import CaseClaim, User, Case
    
    query = db.query(CaseClaim)
    
    if active_only:
        query = query.filter(CaseClaim.is_active == True)
    
    if user_id:
        query = query.filter(CaseClaim.user_id == user_id)
    
    if case_id:
        query = query.filter(CaseClaim.case_id == case_id)
    
    claims = query.order_by(CaseClaim.claimed_at.desc()).limit(limit).all()
    
    # Get user and case info
    user_ids = list(set(c.user_id for c in claims))
    case_ids = list(set(c.case_id for c in claims))
    
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    cases = {c.id: c for c in db.query(Case).filter(Case.id.in_(case_ids)).all()} if case_ids else {}
    
    return {
        "claims": [
            {
                "id": c.id,
                "case_id": c.case_id,
                "case_number": cases.get(c.case_id).case_number if cases.get(c.case_id) else None,
                "user_id": c.user_id,
                "user_email": users.get(c.user_id).email if users.get(c.user_id) else None,
                "claimed_at": c.claimed_at.isoformat() if c.claimed_at else None,
                "released_at": c.released_at.isoformat() if c.released_at else None,
                "score_at_claim": c.score_at_claim,
                "price_cents": c.price_cents,
                "price_display": f"${(c.price_cents or 0)/100:.2f}",
                "is_active": c.is_active,
            }
            for c in claims
        ],
        "total": len(claims),
    }


@router.get("/v3/claims/stats")
def api_admin_claims_stats(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get claims statistics (admin only)."""
    require_admin(request)
    
    from app.models import CaseClaim
    
    # Total claims
    total_claims = db.query(CaseClaim).count()
    
    # Active claims
    active_claims = db.query(CaseClaim).filter(CaseClaim.is_active == True).count()
    
    # Released claims
    released_claims = total_claims - active_claims
    
    # Daily revenue (sum of active claim prices)
    daily_revenue = db.query(func.sum(CaseClaim.price_cents)).filter(
        CaseClaim.is_active == True
    ).scalar() or 0
    
    # Users with active claims
    users_with_claims = db.query(CaseClaim.user_id).filter(
        CaseClaim.is_active == True
    ).distinct().count()
    
    # Average claims per user
    avg_claims = (active_claims / users_with_claims) if users_with_claims > 0 else 0
    
    # Claims by tier
    tier_breakdown = {}
    tiers = [
        ("excellent", 80, 100),
        ("good", 60, 79),
        ("fair", 40, 59),
        ("poor", 0, 39),
    ]
    
    for tier_name, min_score, max_score in tiers:
        count = db.query(CaseClaim).filter(
            and_(
                CaseClaim.is_active == True,
                CaseClaim.score_at_claim >= min_score,
                CaseClaim.score_at_claim <= max_score,
            )
        ).count()
        tier_breakdown[tier_name] = count
    
    return {
        "total_claims": total_claims,
        "active_claims": active_claims,
        "released_claims": released_claims,
        "daily_revenue_cents": daily_revenue,
        "daily_revenue_display": f"${daily_revenue/100:.2f}",
        "users_with_claims": users_with_claims,
        "avg_claims_per_user": round(avg_claims, 1),
        "tier_breakdown": tier_breakdown,
    }


@router.post("/v3/users/{user_id}/release-all-claims")
def api_admin_release_all_user_claims(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Release all claims for a user (admin only)."""
    require_admin(request)
    
    from app.models import CaseClaim, Case
    
    # Find all active claims for user
    claims = db.query(CaseClaim).filter(
        and_(
            CaseClaim.user_id == user_id,
            CaseClaim.is_active == True
        )
    ).all()
    
    released_count = 0
    now = datetime.utcnow()
    
    for claim in claims:
        claim.is_active = False
        claim.released_at = now
        
        # Clear case assignment
        case = db.query(Case).filter(Case.id == claim.case_id).first()
        if case:
            case.assigned_to = None
            case.assigned_at = None
        
        released_count += 1
    
    db.commit()
    
    logger.info(f"Admin released {released_count} claims for user {user_id}")
    
    return {
        "status": "success",
        "user_id": user_id,
        "released_count": released_count,
    }


@router.post("/v3/users/{user_id}/set-claim-limit")
def api_admin_set_claim_limit(
    user_id: int,
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Set claim limit for a user (admin only)."""
    require_admin(request)
    
    from app.models import User
    
    new_limit = body.get("max_claims")
    if new_limit is None or new_limit < 0:
        raise HTTPException(status_code=400, detail="Invalid claim limit")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    old_limit = user.max_claims
    user.max_claims = new_limit
    db.commit()
    
    logger.info(f"Admin changed claim limit for user {user_id}: {old_limit} -> {new_limit}")
    
    return {
        "status": "success",
        "user_id": user_id,
        "old_limit": old_limit,
        "new_limit": new_limit,
    }


@router.post("/v3/users/{user_id}/toggle-billing")
def api_admin_toggle_billing(
    user_id: int,
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Enable/disable billing for a user (admin only)."""
    require_admin(request)
    
    from app.models import User
    
    is_active = body.get("is_billing_active")
    if is_active is None:
        raise HTTPException(status_code=400, detail="is_billing_active required")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.is_billing_active = bool(is_active)
    db.commit()
    
    logger.info(f"Admin {'enabled' if is_active else 'disabled'} billing for user {user_id}")
    
    return {
        "status": "success",
        "user_id": user_id,
        "is_billing_active": user.is_billing_active,
    }


# ============================================================
# Audit Log
# ============================================================

@router.get("/v3/audit-log")
def api_admin_audit_log(
    request: Request,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db)
):
    """Get audit log entries (admin only)."""
    require_admin(request)
    
    try:
        # Try to query webhook_logs if it exists
        result = db.execute(text("""
            SELECT event_type, event_id, result, created_at 
            FROM webhook_logs 
            ORDER BY created_at DESC 
            LIMIT :limit
        """), {"limit": limit}).fetchall()
        
        return {
            "entries": [
                {
                    "event_type": row[0],
                    "event_id": row[1],
                    "result": row[2],
                    "created_at": row[3],
                }
                for row in result
            ],
            "total": len(result),
        }
    except Exception:
        # Table doesn't exist
        return {"entries": [], "total": 0, "message": "Audit log not available"}


# ============================================================
# System Health
# ============================================================

@router.get("/v3/system/health")
def api_admin_system_health(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get system health status (admin only)."""
    require_admin(request)
    
    from app.models import User, Case, CaseClaim, Invoice
    
    health = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": {},
    }
    
    # Database connectivity
    try:
        db.execute(text("SELECT 1"))
        health["checks"]["database"] = {"status": "ok"}
    except Exception as e:
        health["checks"]["database"] = {"status": "error", "message": str(e)}
        health["status"] = "degraded"
    
    # Record counts
    try:
        health["checks"]["records"] = {
            "users": db.query(User).count(),
            "cases": db.query(Case).count(),
            "active_claims": db.query(CaseClaim).filter(CaseClaim.is_active == True).count(),
            "invoices": db.query(Invoice).count(),
        }
    except Exception as e:
        health["checks"]["records"] = {"status": "error", "message": str(e)}
    
    # Stripe connectivity
    try:
        from app.services.stripe_service import is_stripe_configured
        health["checks"]["stripe"] = {
            "configured": is_stripe_configured(),
            "status": "ok" if is_stripe_configured() else "not_configured",
        }
    except Exception as e:
        health["checks"]["stripe"] = {"status": "error", "message": str(e)}
    
    return health


@router.get("/v3/system/config")
def api_admin_system_config(
    request: Request,
):
    """Get system configuration (admin only)."""
    require_admin(request)
    
    from app.config import settings
    
    # Return safe config values (not secrets)
    return {
        "enable_multi_user": getattr(settings, "enable_multi_user", False),
        "enable_comparables": getattr(settings, "enable_comparables", False),
        "enable_billing": getattr(settings, "enable_billing", False),
        "max_claims_per_user": getattr(settings, "max_claims_per_user", 50),
        "stripe_configured": bool(getattr(settings, "stripe_secret_key", None)),
        "environment": getattr(settings, "environment", "development"),
    }


# ============================================================
# Automated Invoice Charging
# ============================================================

@router.post("/v3/billing/charge-invoice/{invoice_id}")
def api_admin_charge_invoice(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Charge a specific invoice using the user's stored payment method.
    
    This will attempt to charge the invoice immediately using Stripe.
    """
    require_admin(request)
    
    from app.services.stripe_service import charge_invoice_automatically
    
    success, message, payment_id = charge_invoice_automatically(db, invoice_id)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {
        "status": "success",
        "message": message,
        "payment_intent_id": payment_id,
    }


@router.post("/v3/billing/charge-all-pending")
def api_admin_charge_all_pending(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Charge all pending invoices that have valid payment methods.
    
    This is the main automated billing endpoint - call this daily to process
    all outstanding invoices.
    """
    require_admin(request)
    
    from app.services.stripe_service import charge_all_pending_invoices
    
    results = charge_all_pending_invoices(db)
    
    logger.info(f"Bulk invoice charging: {results['successful']} paid, {results['failed']} failed, {results['skipped']} skipped")
    
    return {
        "status": "success",
        "results": results,
    }


@router.get("/v3/users/{user_id}/payment-status")
def api_admin_get_user_payment_status(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get a user's payment method status (admin only)."""
    require_admin(request)
    
    from app.models import User
    from app.services.stripe_service import get_user_payment_method_info
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    payment_info = get_user_payment_method_info(db, user_id)
    
    return {
        "user_id": user_id,
        "email": user.email,
        "has_valid_payment_method": user.has_valid_payment_method,
        "stripe_customer_id": user.stripe_customer_id,
        "payment_method": payment_info,
    }
