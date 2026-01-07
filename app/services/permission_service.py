# app/services/permission_service.py
"""
Permission Service - V3 Access Control
Handles case ownership and sensitive data access permissions
"""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Case, User


def can_view_sensitive(case: "Case", user: Optional["User"]) -> bool:
    """
    Check if user can view sensitive case data (full address, skip trace, documents).
    
    Returns True if:
    - User is admin
    - User owns the case (assigned_to matches user id)
    
    Non-owners must CLAIM the case to see sensitive data.
    
    Args:
        case: The case to check permissions for
        user: The user requesting access (can be None)
    
    Returns:
        True if user can view sensitive data, False otherwise
    """
    if not user:
        return False
    
    # Admins can see everything
    if getattr(user, 'is_admin', False):
        return True
    
    # Check role for admin
    user_role = getattr(user, 'role', '')
    if user_role == 'admin':
        return True
    
    # Owner can see their claimed case
    user_id = getattr(user, 'id', None)
    if user_id and case.assigned_to is not None and case.assigned_to == user_id:
        return True
    
    # Everyone else must claim to see sensitive data
    return False


def can_claim_case(case: "Case", user: Optional["User"]) -> bool:
    """
    Check if user can claim this case.
    
    Args:
        case: The case to check
        user: The user attempting to claim
    
    Returns:
        True if user can claim, False otherwise
    """
    if not user:
        return False
    
    # Can't claim already claimed cases
    if case.assigned_to is not None:
        return False
    
    # Check if user is active
    if not getattr(user, 'is_active', True):
        return False
    
    # All active users can claim unclaimed cases
    return True


def can_release_case(case: "Case", user: Optional["User"]) -> bool:
    """
    Check if user can release this case.
    
    Args:
        case: The case to check
        user: The user attempting to release
    
    Returns:
        True if user can release, False otherwise
    """
    if not user:
        return False
    
    # Admins can release any case
    if getattr(user, 'is_admin', False):
        return True
    
    # Check role for admin
    user_role = getattr(user, 'role', '')
    if user_role == 'admin':
        return True
    
    # Owners can release their own cases
    user_id = getattr(user, 'id', None)
    return user_id is not None and case.assigned_to == user_id


def get_case_visibility(case: "Case", user: Optional["User"]) -> dict:
    """
    Get detailed visibility flags for a case.
    
    Args:
        case: The case to check
        user: The current user
    
    Returns:
        Dict with visibility flags
    """
    can_view = can_view_sensitive(case, user)
    user_id = getattr(user, 'id', None) if user else None
    is_owner = user_id is not None and case.assigned_to == user_id
    
    return {
        'can_view_sensitive': can_view,
        'can_view_address': can_view,
        'can_view_skip_trace': can_view,
        'can_view_documents': can_view,
        'can_claim': can_claim_case(case, user),
        'can_release': can_release_case(case, user),
        'is_owner': is_owner,
        'is_claimed': case.assigned_to is not None,
        'claimed_by_current_user': is_owner,
    }


def check_claim_limit(user: "User", current_claims: int) -> tuple:
    """
    Check if user has reached their claim limit.
    
    NOTE: Claim limits have been disabled. This function always returns True.
    
    Args:
        user: The user to check
        current_claims: Number of active claims the user has
    
    Returns:
        Tuple of (can_claim: bool, message: str)
    """
    # Claim limits disabled - always allow
    return True, ""
