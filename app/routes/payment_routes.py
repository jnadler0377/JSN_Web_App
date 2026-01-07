# app/routes/payment_routes.py
"""
Payment Routes - V3 Stripe Payment Method Management

Handles:
- Payment method setup page
- SetupIntent creation
- Payment method saving/removal
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user
from app.services.stripe_service import (
    is_stripe_configured,
    create_setup_intent_for_user,
    save_payment_method_for_user,
    get_user_payment_method_info,
    remove_user_payment_method,
    get_publishable_key,
)

logger = logging.getLogger("pascowebapp.payment")

router = APIRouter(prefix="/api/v3", tags=["payment"])

# Template reference - set by main.py
templates = None


def init_payment_templates(template_engine):
    """Initialize templates for payment routes."""
    global templates
    templates = template_engine


def require_auth(request: Request):
    """Require authentication."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ============================================================
# API Endpoints
# ============================================================

@router.get("/payment/status")
def api_payment_status(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get current user's payment method status."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    payment_info = get_user_payment_method_info(db, user_id)
    stripe_configured = is_stripe_configured()
    
    from app.models import User
    db_user = db.query(User).filter(User.id == user_id).first()
    
    # Determine if user can claim:
    # - Admins can always claim
    # - If Stripe is NOT configured, anyone can claim (for development/testing)
    # - If Stripe IS configured, user needs a valid payment method
    can_claim = False
    if db_user:
        if db_user.is_admin:
            can_claim = True
        elif not stripe_configured:
            can_claim = True  # Allow claiming when Stripe not configured
        elif db_user.has_valid_payment_method:
            can_claim = True
    
    return {
        "has_payment_method": db_user.has_valid_payment_method if db_user else False,
        "payment_method": payment_info,
        "stripe_configured": stripe_configured,
        "can_claim": can_claim,
    }


@router.post("/payment/setup-intent")
def api_create_setup_intent(
    request: Request,
    db: Session = Depends(get_db)
):
    """Create a Stripe SetupIntent for adding a payment method."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    if not is_stripe_configured():
        raise HTTPException(status_code=503, detail="Payment system not configured")
    
    success, message, setup_data = create_setup_intent_for_user(db, user_id)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return setup_data


@router.post("/payment/save-method")
def api_save_payment_method(
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Save a payment method after successful setup."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    payment_method_id = body.get("payment_method_id")
    if not payment_method_id:
        raise HTTPException(status_code=400, detail="Payment method ID required")
    
    success, message = save_payment_method_for_user(db, user_id, payment_method_id)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"status": "success", "message": message}


@router.delete("/payment/remove-method")
def api_remove_payment_method(
    request: Request,
    db: Session = Depends(get_db)
):
    """Remove the user's saved payment method."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    success, message = remove_user_payment_method(db, user_id)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {"status": "success", "message": message}


@router.get("/payment/config")
def api_payment_config(request: Request):
    """Get Stripe publishable key for frontend."""
    require_auth(request)
    
    return {
        "publishable_key": get_publishable_key(),
        "configured": is_stripe_configured(),
    }
