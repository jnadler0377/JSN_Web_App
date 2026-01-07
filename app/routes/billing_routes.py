# app/routes/billing_routes.py
"""
Billing Routes - V3 Phase 4
Handles invoice viewing, billing history, and payment endpoints
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException, Query, Body
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user
from app.services.billing_service import (
    generate_daily_invoice,
    generate_daily_invoices_for_all,
    get_user_invoices,
    get_invoice_details,
    get_billing_summary,
    mark_invoice_paid,
    mark_invoice_failed,
)

logger = logging.getLogger("pascowebapp.billing")

router = APIRouter(prefix="/api/v3", tags=["billing"])


def require_auth(request: Request):
    """Require authentication for billing endpoints."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request):
    """Require admin access."""
    user = require_auth(request)
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ============================================================
# USER BILLING ENDPOINTS
# ============================================================

@router.get("/billing/invoices")
def api_get_my_invoices(
    request: Request,
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db)
):
    """Get current user's invoices."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    invoices = get_user_invoices(db, user_id, status, limit)
    
    return {
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "status": inv.status,
                "total_cents": inv.total_cents,
                "total_display": inv.total_display,
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
                "stripe_hosted_url": inv.stripe_hosted_url,
            }
            for inv in invoices
        ],
        "total": len(invoices),
    }


@router.get("/billing/invoices/{invoice_id}")
def api_get_invoice(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get invoice details."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    
    invoice_data = get_invoice_details(db, invoice_id)
    
    if not invoice_data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Check access - user can only see their own invoices (unless admin)
    if not is_admin and invoice_data.get("user", {}).get("id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return invoice_data


@router.get("/billing/summary")
def api_get_my_billing_summary(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get current user's billing summary."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    from app.models import Invoice, CaseClaim, Case
    from sqlalchemy import func
    from datetime import date, datetime, timedelta
    
    # Get active claims with case info
    active_claims = db.query(CaseClaim, Case).join(
        Case, CaseClaim.case_id == Case.id
    ).filter(
        CaseClaim.user_id == user_id,
        CaseClaim.is_active == True
    ).all()
    
    # Calculate today's total - claims made today
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = today_start + timedelta(days=1)
    
    todays_total = 0
    for claim, case in active_claims:
        if claim.claimed_at and today_start <= claim.claimed_at < today_end:
            todays_total += claim.price_cents or 0
    
    # Calculate pending balance - sum of all UNPAID invoices
    pending_balance = db.query(func.sum(Invoice.total_cents)).filter(
        Invoice.user_id == user_id,
        Invoice.status == "pending"
    ).scalar() or 0
    
    # Total paid - sum of all paid invoices
    total_paid = db.query(func.sum(Invoice.total_cents)).filter(
        Invoice.user_id == user_id,
        Invoice.status == "paid"
    ).scalar() or 0
    
    return {
        "pending_balance_cents": pending_balance,
        "pending_balance_display": f"${pending_balance/100:.2f}",
        "total_paid_cents": total_paid,
        "total_paid_display": f"${total_paid/100:.2f}",
        "active_claims": len(active_claims),
        "todays_total_cents": todays_total,
        "todays_total_display": f"${todays_total/100:.2f}",
        "claims_breakdown": [
            {
                "case_id": claim.case_id,
                "address": case.address_override or case.address or "No address",
                "score": claim.score_at_claim,
                "price": claim.score_at_claim,  # Price = score
                "price_cents": claim.price_cents,
                "price_display": f"${(claim.price_cents or 0)/100:.0f}",
                "claimed_at": claim.claimed_at.isoformat() if claim.claimed_at else None,
            }
            for claim, case in active_claims
        ],
    }


# ============================================================
# ADMIN BILLING ENDPOINTS
# ============================================================

@router.get("/admin/billing/summary")
def api_admin_billing_summary(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get overall billing summary (admin only)."""
    require_admin(request)
    return get_billing_summary(db)


@router.post("/admin/billing/generate-daily")
def api_admin_generate_daily_invoices(
    request: Request,
    body: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """
    Generate daily invoices for all users (admin only).
    
    Request body:
        billing_date: Optional date string (YYYY-MM-DD), defaults to yesterday
    """
    require_admin(request)
    
    billing_date = None
    if body.get("billing_date"):
        try:
            billing_date = datetime.fromisoformat(body["billing_date"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    results = generate_daily_invoices_for_all(db, billing_date)
    
    return {
        "status": "success",
        "results": results,
    }


@router.post("/admin/billing/generate-invoice/{user_id}")
def api_admin_generate_user_invoice(
    user_id: int,
    request: Request,
    body: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """
    Generate invoice for a specific user (admin only).
    
    Request body:
        billing_date: Optional date string (YYYY-MM-DD)
        force: Boolean, generate even if invoice exists
    """
    require_admin(request)
    
    billing_date = None
    if body.get("billing_date"):
        try:
            billing_date = datetime.fromisoformat(body["billing_date"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format")
    
    force = body.get("force", False)
    
    success, message, invoice_data = generate_daily_invoice(db, user_id, billing_date, force)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {
        "status": "success",
        "message": message,
        "invoice": invoice_data,
    }


@router.get("/admin/billing/invoices")
def api_admin_list_invoices(
    request: Request,
    status: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db)
):
    """List all invoices (admin only)."""
    require_admin(request)
    
    from app.models import Invoice, User
    
    query = db.query(Invoice)
    
    if status:
        query = query.filter(Invoice.status == status)
    if user_id:
        query = query.filter(Invoice.user_id == user_id)
    
    invoices = query.order_by(Invoice.invoice_date.desc()).limit(limit).all()
    
    # Get user info for each invoice
    user_ids = list(set(inv.user_id for inv in invoices))
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    
    return {
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "user_id": inv.user_id,
                "user_email": users.get(inv.user_id).email if users.get(inv.user_id) else None,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "status": inv.status,
                "total_cents": inv.total_cents,
                "total_display": inv.total_display,
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
                "stripe_invoice_id": inv.stripe_invoice_id,
                "stripe_payment_intent": inv.stripe_payment_intent,
            }
            for inv in invoices
        ],
        "total": len(invoices),
    }


@router.post("/admin/billing/invoices/{invoice_id}/mark-paid")
def api_admin_mark_paid(
    invoice_id: int,
    request: Request,
    body: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """Mark an invoice as paid (admin only)."""
    require_admin(request)
    
    payment_intent = body.get("payment_intent")
    success, message = mark_invoice_paid(db, invoice_id, payment_intent)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"status": "success", "message": message}


@router.post("/admin/billing/invoices/{invoice_id}/mark-failed")
def api_admin_mark_failed(
    invoice_id: int,
    request: Request,
    body: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """Mark an invoice as failed (admin only)."""
    require_admin(request)
    
    reason = body.get("reason", "Manual override")
    success, message = mark_invoice_failed(db, invoice_id, reason)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"status": "success", "message": message}


# ============================================================
# PRICING INFO
# ============================================================

@router.get("/billing/pricing")
def api_get_pricing_info():
    """Get pricing tier information."""
    from app.services.pricing_service import get_all_tiers
    
    return {
        "tiers": get_all_tiers(),
        "billing_frequency": "daily",
        "currency": "USD",
        "description": "Cases are billed daily based on their deal score at the time of claiming. "
                      "Higher scoring deals have higher daily rates.",
    }
