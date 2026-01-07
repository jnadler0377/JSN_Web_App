# app/routes/claim_routes.py
"""
Case Claiming Routes - V3
Handles case claim/release endpoints and claim management
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Body, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user
from app.services.claim_service import (
    claim_case,
    release_case,
    bulk_claim_cases,
    bulk_release_cases,
    get_user_claims,
    get_user_claim_count,
    get_claim_for_case,
    get_claim_stats,
)
from app.services.pricing_service import get_tier_info, get_all_tiers

logger = logging.getLogger("pascowebapp.claims")

router = APIRouter(prefix="/api/v3", tags=["claims"])


def require_auth(request: Request):
    """Require authentication for claim endpoints."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ============================================================
# CLAIM ENDPOINTS
# ============================================================

@router.post("/cases/claim")
def api_claim_cases(
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """
    Claim one or more cases for the current user.
    
    Request body:
        case_ids: List of case IDs to claim
    
    Returns:
        For single case: claim details
        For multiple cases: summary of claimed/failed
    """
    user = require_auth(request)
    
    case_ids = body.get("case_ids", [])
    if not case_ids:
        raise HTTPException(status_code=400, detail="No cases specified")
    
    # Ensure case_ids is a list
    if isinstance(case_ids, int):
        case_ids = [case_ids]
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    if len(case_ids) == 1:
        # Single case claim
        success, message, claim = claim_case(db, case_ids[0], user_id)
        if not success:
            raise HTTPException(status_code=400, detail=message)
        
        tier_info = get_tier_info(claim.score_at_claim) if claim else {}
        
        return {
            "status": "success",
            "message": message,
            "claim_id": claim.id if claim else None,
            "case_id": case_ids[0],
            "score": claim.score_at_claim if claim else 0,
            "price_cents": claim.price_cents if claim else 0,
            "price_display": claim.price_display if claim else "$0.00",
            "tier": tier_info.get("tier", "unknown"),
        }
    else:
        # Bulk claim
        claimed, failed, errors = bulk_claim_cases(db, case_ids, user_id)
        
        status = "success" if failed == 0 else ("partial" if claimed > 0 else "error")
        
        return {
            "status": status,
            "claimed": claimed,
            "failed": failed,
            "errors": errors[:10],  # Limit error messages
            "message": f"Claimed {claimed} case(s)" + (f", {failed} failed" if failed > 0 else ""),
        }


@router.post("/cases/release")
def api_release_cases(
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """
    Release one or more claimed cases.
    
    Request body:
        case_ids: List of case IDs to release
    """
    user = require_auth(request)
    
    case_ids = body.get("case_ids", [])
    if not case_ids:
        raise HTTPException(status_code=400, detail="No cases specified")
    
    # Ensure case_ids is a list
    if isinstance(case_ids, int):
        case_ids = [case_ids]
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, 'is_admin', False)
    
    if len(case_ids) == 1:
        # Single case release
        success, message = release_case(db, case_ids[0], user_id, is_admin)
        if not success:
            raise HTTPException(status_code=400, detail=message)
        
        return {
            "status": "success",
            "message": message,
            "case_id": case_ids[0],
        }
    else:
        # Bulk release
        released, failed, errors = bulk_release_cases(db, case_ids, user_id, is_admin)
        
        status = "success" if failed == 0 else ("partial" if released > 0 else "error")
        
        return {
            "status": status,
            "released": released,
            "failed": failed,
            "errors": errors[:10],
            "message": f"Released {released} case(s)" + (f", {failed} failed" if failed > 0 else ""),
        }


@router.post("/cases/{case_id}/claim")
def api_claim_single_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Claim a single case by ID."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    success, message, claim = claim_case(db, case_id, user_id)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    tier_info = get_tier_info(claim.score_at_claim) if claim else {}
    
    return {
        "status": "success",
        "message": message,
        "claim_id": claim.id if claim else None,
        "case_id": case_id,
        "score": claim.score_at_claim if claim else 0,
        "price_cents": claim.price_cents if claim else 0,
        "price_display": claim.price_display if claim else "$0.00",
        "tier": tier_info.get("tier", "unknown"),
    }


@router.post("/cases/{case_id}/release")
def api_release_single_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Release a single case by ID."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, 'is_admin', False)
    
    success, message = release_case(db, case_id, user_id, is_admin)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    return {
        "status": "success",
        "message": message,
        "case_id": case_id,
    }


# ============================================================
# CLAIM QUERIES
# ============================================================

@router.get("/claims")
def api_get_my_claims(
    request: Request,
    active_only: bool = Query(True),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db)
):
    """Get current user's claims."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    claims = get_user_claims(db, user_id, active_only, limit)
    
    return {
        "claims": [
            {
                "id": c.id,
                "case_id": c.case_id,
                "case_number": c.case.case_number if c.case else None,
                "address": (c.case.address_override or c.case.address) if c.case else None,
                "claimed_at": c.claimed_at.isoformat() if c.claimed_at else None,
                "released_at": c.released_at.isoformat() if c.released_at else None,
                "score_at_claim": c.score_at_claim,
                "price_cents": c.price_cents,
                "price_display": c.price_display,
                "is_active": c.is_active,
                "duration_days": c.duration_days,
            }
            for c in claims
        ],
        "total": len(claims),
        "active_count": sum(1 for c in claims if c.is_active),
    }


@router.get("/claims/count")
def api_get_claim_count(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get current user's active claim count."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    count = get_user_claim_count(db, user_id)
    
    return {
        "active_claims": count,
        "max_claims": None,  # No limit
        "remaining": None,   # Unlimited
    }


@router.get("/cases/{case_id}/claim-status")
def api_get_case_claim_status(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get claim status for a specific case."""
    user = require_auth(request)
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    from app.models import Case
    case = db.query(Case).filter(Case.id == case_id).first()
    
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    claim = get_claim_for_case(db, case_id)
    
    is_owner = case.assigned_to == user_id
    is_claimed = case.assigned_to is not None
    
    return {
        "case_id": case_id,
        "is_claimed": is_claimed,
        "is_owner": is_owner,
        "can_claim": not is_claimed,
        "can_release": is_owner,
        "claimed_at": claim.claimed_at.isoformat() if claim else None,
        "score": claim.score_at_claim if claim else None,
        "price_cents": claim.price_cents if claim else None,
        "price_display": claim.price_display if claim else None,
    }


# ============================================================
# PRICING INFO
# ============================================================

@router.get("/pricing")
def api_get_pricing():
    """Get pricing tiers for claims."""
    return {
        "tiers": get_all_tiers(),
        "billing_frequency": "daily",
        "currency": "USD",
    }


# ============================================================
# ADMIN ENDPOINTS
# ============================================================

@router.get("/admin/claims/stats")
def api_get_claim_stats(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get overall claim statistics (admin only)."""
    user = require_auth(request)
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, 'is_admin', False)
    
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return get_claim_stats(db)


@router.post("/admin/cases/{case_id}/reassign")
def api_admin_reassign_case(
    case_id: int,
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """
    Admin tool to reassign a case to another user.
    
    Request body:
        user_id: New owner's user ID (or null to unassign)
    """
    user = require_auth(request)
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, 'is_admin', False)
    
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    new_user_id = body.get("user_id")
    
    from app.models import Case, CaseClaim
    from datetime import datetime
    
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # Deactivate current claim if exists
    if case.assigned_to:
        current_claim = db.query(CaseClaim).filter(
            CaseClaim.case_id == case_id,
            CaseClaim.is_active == True
        ).first()
        
        if current_claim:
            current_claim.is_active = False
            current_claim.released_at = datetime.utcnow()
    
    # Assign to new user (or clear if new_user_id is None)
    case.assigned_to = new_user_id
    case.assigned_at = datetime.utcnow() if new_user_id else None
    
    # Create new claim if assigning to a user
    if new_user_id:
        from app.services.claim_service import calculate_case_score
        from app.services.pricing_service import calculate_claim_price
        
        score = calculate_case_score(case)
        price = calculate_claim_price(score)
        
        new_claim = CaseClaim(
            case_id=case_id,
            user_id=new_user_id,
            score_at_claim=score,
            price_cents=price,
            is_active=True,
        )
        db.add(new_claim)
    
    db.commit()
    
    logger.info(f"Admin reassigned case {case_id} to user {new_user_id}")
    
    return {
        "status": "success",
        "case_id": case_id,
        "new_owner": new_user_id,
        "message": f"Case reassigned to user {new_user_id}" if new_user_id else "Case unassigned",
    }


# ============================================================
# HTML FORM ENDPOINTS (for non-JS fallback)
# ============================================================

@router.post("/cases/{case_id}/claim-form", include_in_schema=False)
def form_claim_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """HTML form endpoint for claiming a case."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    
    success, message, claim = claim_case(db, case_id, user_id)
    
    # Redirect back to case detail
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@router.post("/cases/{case_id}/release-form", include_in_schema=False)
def form_release_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """HTML form endpoint for releasing a case."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, 'is_admin', False)
    
    success, message = release_case(db, case_id, user_id, is_admin)
    
    # Redirect back to case detail
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)
