# app/services/claim_service.py
"""
Claim Service - V3 Case Claiming
Handles case claiming, releasing, and claim management
"""

import logging
from datetime import datetime
from typing import Optional, Tuple, List, TYPE_CHECKING

from sqlalchemy.orm import Session
from sqlalchemy import and_, func

if TYPE_CHECKING:
    from app.models import Case, User, CaseClaim

logger = logging.getLogger("pascowebapp.claims")


def calculate_case_score(case: "Case", db: Session = None) -> int:
    """
    Calculate deal score for a case.
    Uses the deal analysis logic if available, otherwise estimates.
    
    Args:
        case: The case to score
        db: Database session (required for analyze_deal)
    
    Returns:
        Score from 0-100
    """
    try:
        # Try to use the existing deal analysis service
        from app.services.deal_analysis_service import analyze_deal
        if db:
            analysis = analyze_deal(case.id, db)
            if "error" not in analysis:
                return analysis.get('score', 25)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Could not analyze deal for case {case.id}: {e}")
    
    # Fallback: Simple score based on available data
    score = 25  # Base score (matches deal_analysis_service)
    
    # Boost for having ARV
    if getattr(case, 'arv', None) and case.arv > 0:
        score += 15
    
    # Boost for having address
    if getattr(case, 'address', None) or getattr(case, 'address_override', None):
        score += 10
    
    # Boost for having property data
    if getattr(case, 'parcel_id', None):
        score += 10
    
    # Cap at 100
    return min(score, 100)


def claim_case(
    db: Session,
    case_id: int,
    user_id: int
) -> Tuple[bool, str, Optional["CaseClaim"]]:
    """
    Claim a case for a user with transactional locking.
    
    Args:
        db: Database session
        case_id: ID of case to claim
        user_id: ID of user claiming
    
    Returns:
        Tuple of (success, message, claim_record)
    """
    from app.models import Case, User, CaseClaim
    from app.services.pricing_service import calculate_claim_price
    from app.services.permission_service import check_claim_limit
    from app.services.stripe_service import check_user_can_claim
    
    try:
        # Get user first
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False, "User not found", None
        
        # Check if user can claim (has valid payment method)
        can_claim, payment_msg = check_user_can_claim(db, user_id)
        if not can_claim:
            return False, payment_msg, None
        
        # Check claim limit
        active_claims = db.query(CaseClaim).filter(
            and_(
                CaseClaim.user_id == user_id,
                CaseClaim.is_active == True
            )
        ).count()
        
        can_claim, limit_msg = check_claim_limit(user, active_claims)
        if not can_claim:
            return False, limit_msg, None
        
        # Lock the case row for update (prevents race conditions)
        case = db.query(Case).filter(Case.id == case_id).with_for_update().first()
        
        if not case:
            return False, "Case not found", None
        
        if case.assigned_to is not None:
            if case.assigned_to == user_id:
                return False, "You already own this case", None
            return False, "Case is already claimed by another user", None
        
        # Calculate score and price at claim time (frozen values)
        score = calculate_case_score(case, db)
        price_cents = calculate_claim_price(score)
        
        # Create claim record
        claim = CaseClaim(
            case_id=case_id,
            user_id=user_id,
            claimed_at=datetime.utcnow(),
            score_at_claim=score,
            price_cents=price_cents,
            is_active=True,
        )
        
        # Update case ownership
        case.assigned_to = user_id
        case.assigned_at = datetime.utcnow()
        
        db.add(claim)
        db.commit()
        db.refresh(claim)
        
        logger.info(f"User {user_id} claimed case {case_id} (score: {score}, price: {price_cents})")
        
        return True, "Case claimed successfully", claim
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error claiming case {case_id}: {e}")
        return False, f"Error claiming case: {str(e)}", None


def release_case(
    db: Session,
    case_id: int,
    user_id: int,
    is_admin: bool = False
) -> Tuple[bool, str]:
    """
    Release a claimed case.
    
    Args:
        db: Database session
        case_id: ID of case to release
        user_id: ID of user releasing
        is_admin: Whether user is admin (can release any case)
    
    Returns:
        Tuple of (success, message)
    """
    from app.models import Case, CaseClaim
    
    try:
        # Lock the case row
        case = db.query(Case).filter(Case.id == case_id).with_for_update().first()
        
        if not case:
            return False, "Case not found"
        
        if case.assigned_to is None:
            return False, "Case is not claimed"
        
        if not is_admin and case.assigned_to != user_id:
            return False, "You do not own this case"
        
        # Deactivate the claim record
        active_claim = db.query(CaseClaim).filter(
            and_(
                CaseClaim.case_id == case_id,
                CaseClaim.is_active == True
            )
        ).first()
        
        if active_claim:
            active_claim.is_active = False
            active_claim.released_at = datetime.utcnow()
        
        # Store previous owner for logging
        previous_owner = case.assigned_to
        
        # Clear case ownership
        case.assigned_to = None
        case.assigned_at = None
        
        db.commit()
        
        logger.info(f"Case {case_id} released by user {user_id} (was owned by {previous_owner})")
        
        return True, "Case released successfully"
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error releasing case {case_id}: {e}")
        return False, f"Error releasing case: {str(e)}"


def bulk_claim_cases(
    db: Session,
    case_ids: List[int],
    user_id: int
) -> Tuple[int, int, List[str]]:
    """
    Claim multiple cases at once.
    
    Args:
        db: Database session
        case_ids: List of case IDs to claim
        user_id: ID of user claiming
    
    Returns:
        Tuple of (claimed_count, failed_count, error_messages)
    """
    claimed = 0
    failed = 0
    errors = []
    
    for case_id in case_ids:
        success, message, _ = claim_case(db, case_id, user_id)
        if success:
            claimed += 1
        else:
            failed += 1
            errors.append(f"Case {case_id}: {message}")
    
    return claimed, failed, errors


def bulk_release_cases(
    db: Session,
    case_ids: List[int],
    user_id: int,
    is_admin: bool = False
) -> Tuple[int, int, List[str]]:
    """
    Release multiple cases at once.
    
    Args:
        db: Database session
        case_ids: List of case IDs to release
        user_id: ID of user releasing
        is_admin: Whether user is admin
    
    Returns:
        Tuple of (released_count, failed_count, error_messages)
    """
    released = 0
    failed = 0
    errors = []
    
    for case_id in case_ids:
        success, message = release_case(db, case_id, user_id, is_admin)
        if success:
            released += 1
        else:
            failed += 1
            errors.append(f"Case {case_id}: {message}")
    
    return released, failed, errors


def get_user_claims(
    db: Session,
    user_id: int,
    active_only: bool = True,
    limit: int = 100
) -> List["CaseClaim"]:
    """
    Get all claims for a user.
    
    Args:
        db: Database session
        user_id: User ID
        active_only: Only return active claims
        limit: Maximum number of claims to return
    
    Returns:
        List of CaseClaim objects
    """
    from app.models import CaseClaim
    
    query = db.query(CaseClaim).filter(CaseClaim.user_id == user_id)
    
    if active_only:
        query = query.filter(CaseClaim.is_active == True)
    
    return query.order_by(CaseClaim.claimed_at.desc()).limit(limit).all()


def get_user_claim_count(db: Session, user_id: int) -> int:
    """
    Get count of active claims for a user.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Number of active claims
    """
    from app.models import CaseClaim
    
    return db.query(CaseClaim).filter(
        and_(
            CaseClaim.user_id == user_id,
            CaseClaim.is_active == True
        )
    ).count()


def get_claim_for_case(db: Session, case_id: int) -> Optional["CaseClaim"]:
    """
    Get the active claim for a case.
    
    Args:
        db: Database session
        case_id: Case ID
    
    Returns:
        Active CaseClaim or None
    """
    from app.models import CaseClaim
    
    return db.query(CaseClaim).filter(
        and_(
            CaseClaim.case_id == case_id,
            CaseClaim.is_active == True
        )
    ).first()


def get_claim_stats(db: Session) -> dict:
    """
    Get overall claim statistics.
    
    Args:
        db: Database session
    
    Returns:
        Dict with claim statistics
    """
    from app.models import Case, CaseClaim
    
    total_cases = db.query(Case).count()
    claimed_cases = db.query(Case).filter(Case.assigned_to.isnot(None)).count()
    available_cases = total_cases - claimed_cases
    
    active_claims = db.query(CaseClaim).filter(CaseClaim.is_active == True).count()
    total_claims_ever = db.query(CaseClaim).count()
    
    return {
        "total_cases": total_cases,
        "claimed_cases": claimed_cases,
        "available_cases": available_cases,
        "active_claims": active_claims,
        "total_claims_ever": total_claims_ever,
        "claim_rate": round((claimed_cases / total_cases * 100) if total_cases > 0 else 0, 1),
    }
