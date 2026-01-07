# app/services/pricing_service.py
"""
Pricing Service - V3 Billing
Price = Score as dollar amount (score 52 = $52 one-time)
You own the case forever once claimed.
"""

from typing import Dict


def calculate_claim_price(score: int) -> int:
    """
    Calculate price in cents based on case score.
    Price equals the score as a dollar amount (one-time payment).
    
    Args:
        score: Case price/score (0-100)
    
    Returns:
        Price in cents (score * 100)
    """
    # Score IS the price in dollars
    # score 52 = $52 = 5200 cents
    return max(score, 1) * 100  # Minimum $1


def get_price_display(score: int) -> str:
    """
    Get formatted price display for a score.
    
    Args:
        score: Case price/score (0-100)
    
    Returns:
        Formatted price string like "$52"
    """
    return f"${max(score, 1)}"


def get_price_daily_display(score: int) -> str:
    """
    Get formatted price display (kept for backwards compatibility).
    
    Args:
        score: Case price/score (0-100)
    
    Returns:
        Formatted string like "$52"
    """
    return f"${max(score, 1)}"


def get_tier_info(score: int) -> Dict:
    """
    Get pricing information for a score.
    
    Args:
        score: Case price/score (0-100)
    
    Returns:
        Dict with pricing details
    """
    price_cents = calculate_claim_price(score)
    
    return {
        "score": score,
        "price": score,
        "price_cents": price_cents,
        "price_display": get_price_display(score),
    }


def format_price(cents: int) -> str:
    """
    Format cents as dollar string.
    
    Args:
        cents: Amount in cents
    
    Returns:
        Formatted string like "$52.00"
    """
    return f"${cents / 100:.2f}"


def format_price_short(cents: int) -> str:
    """
    Format cents as short dollar string (no cents if whole dollar).
    
    Args:
        cents: Amount in cents
    
    Returns:
        Formatted string like "$52" or "$2.50"
    """
    dollars = cents / 100
    if dollars == int(dollars):
        return f"${int(dollars)}"
    return f"${dollars:.2f}"


def score_to_price_display(score: int) -> str:
    """
    Convert score to price display format.
    
    Args:
        score: Case price/score (0-100)
    
    Returns:
        Formatted string like "$52"
    """
    return f"${max(score, 1)}"


def get_pricing_tier(score: int) -> str:
    """
    Get the tier name for a score/price.
    
    Args:
        score: Case price (0-100)
    
    Returns:
        Tier name (excellent, good, fair, poor)
    """
    if score >= 80:
        return "excellent"
    elif score >= 60:
        return "good"
    elif score >= 40:
        return "fair"
    return "poor"


def get_all_tiers() -> list:
    """
    Get all pricing tiers for display.
    
    Returns:
        List of tier information dicts
    """
    return [
        {
            "tier": "excellent",
            "min_score": 80,
            "max_score": 100,
            "label": "Premium",
            "description": "$80-$100",
            "color": "#10b981",  # green
        },
        {
            "tier": "good", 
            "min_score": 60,
            "max_score": 79,
            "label": "High Value",
            "description": "$60-$79",
            "color": "#3b82f6",  # blue
        },
        {
            "tier": "fair",
            "min_score": 40,
            "max_score": 59,
            "label": "Standard",
            "description": "$40-$59",
            "color": "#f59e0b",  # amber
        },
        {
            "tier": "poor",
            "min_score": 0,
            "max_score": 39,
            "label": "Entry Level",
            "description": "$1-$39",
            "color": "#ef4444",  # red
        },
    ]
