# app/services/deal_analysis_config.py
"""
Deal Analysis Configuration
===========================
Adjust these values to tune the AI deal scoring model.

All scoring weights, thresholds, and CALCULATION FORMULAS are defined here.
"""

# ========================================
# SCORE CATEGORY WEIGHTS
# ========================================
# These determine how much each category contributes to the total score
# Total should equal 100

SCORE_WEIGHTS = {
    "equity": 40,      # Most important - financial potential
    "property": 30,    # Property quality factors
    "market": 20,      # Market conditions
    "urgency": 10,     # Time sensitivity
}


# ========================================
# EQUITY SCORING (40 points max by default)
# ========================================
# Equity % = (ARV - Total Liens) / ARV * 100

EQUITY_THRESHOLDS = {
    # equity_percentage: points_awarded
    80: 40,   # 80%+ equity (free & clear territory)
    60: 35,   # 60-79% equity
    40: 25,   # 40-59% equity
    20: 15,   # 20-39% equity
    0: 5,     # Below 20% equity
}

# Bonus points for free & clear properties
FREE_AND_CLEAR_BONUS = 5


# ========================================
# PROPERTY SCORING (30 points max by default)
# ========================================

# --- Property Size (Square Footage) ---
SIZE_THRESHOLDS = {
    2500: 10,   # 2,500+ sq ft
    2000: 8,    # 2,000-2,499 sq ft
    1500: 6,    # 1,500-1,999 sq ft
    1000: 4,    # 1,000-1,499 sq ft
    0: 2,       # Under 1,000 sq ft
}

# --- Property Condition ---
CONDITION_SCORES = {
    "excellent": 10,
    "good": 8,
    "fair": 5,
    "poor": 3,
    "unknown": 6,   # Default when condition not specified
}

# --- Property Age ---
AGE_THRESHOLDS = {
    10: 10,    # 0-10 years old (new construction)
    20: 8,     # 11-20 years old
    40: 6,     # 21-40 years old
    60: 4,     # 41-60 years old
    999: 2,    # 60+ years old
}

AGE_UNKNOWN_SCORE = 5


# ========================================
# MARKET SCORING (20 points max by default)
# ========================================

MARKET_FACTORS = {
    "vacant": 8,           # Vacant properties (easier to access/deal)
    "preforeclosure": 7,   # Active preforeclosure (motivated seller)
    "owner_occupied": 5,   # Owner occupied (can negotiate directly)
}

MARKET_DEFAULT_SCORE = 5


# ========================================
# URGENCY SCORING (10 points max by default)
# ========================================

URGENCY_THRESHOLDS = {
    30: 10,    # Filed within last 30 days (hot lead)
    60: 7,     # 31-60 days
    90: 5,     # 61-90 days
    180: 3,    # 91-180 days
    9999: 1,   # 180+ days (stale lead)
}

URGENCY_UNKNOWN_SCORE = 5


# ========================================
# INVESTMENT CALCULATION FORMULAS
# ========================================
# These control how investment metrics are calculated
#
# FORMULAS:
# ---------
# Max Offer (MAO)    = (ARV × MAO_PERCENTAGE) - Rehab - Closing Costs
# Equity %           = (ARV - Total Liens) / ARV × 100
# Estimated Profit   = (ARV × SALE_PRICE_FACTOR) - Purchase Price - Rehab - Closing - Selling Costs
# ROI %              = Estimated Profit / Total Investment × 100
#
# WHERE:
# - ARV = After Repair Value (from case.arv or case.prop_est_value)
# - Total Liens = Sum of all outstanding liens
# - Purchase Price = Total Liens (assuming buying at lien amount)
# - Total Investment = Purchase Price + Rehab + Closing Costs

# --- Maximum Allowable Offer (MAO) ---
# Formula: MAO = (ARV × MAO_PERCENTAGE) - Rehab - Closing Costs
MAO_PERCENTAGE = 0.70            # 70% of ARV (the "70% rule")

# --- Closing Costs ---
# Used when case.closing_costs is not set
# Formula: Closing Costs = ARV × CLOSING_COST_PERCENTAGE
CLOSING_COST_PERCENTAGE = 0.02   # 3% of ARV

# --- Selling Costs ---
# Agent commissions + closing costs when selling
# Formula: Selling Costs = ARV × SELLING_COST_PERCENTAGE  
SELLING_COST_PERCENTAGE = 0.045   # 6% of ARV

# --- Sale Price Estimate ---
# Conservative estimate of actual sale price
# Formula: Estimated Sale Price = ARV × SALE_PRICE_FACTOR
SALE_PRICE_FACTOR = 0.95         # Assume 95% of ARV (conservative)

# --- Holding Costs (optional, set to 0 to disable) ---
# Monthly costs while holding property × expected months
HOLDING_COST_MONTHLY = 0         # Set to monthly cost (taxes, insurance, utilities)
HOLDING_MONTHS = 0               # Expected months to hold before sale


# ========================================
# PROFIT CALCULATION BREAKDOWN
# ========================================
# 
# ESTIMATED PROFIT FORMULA:
# -------------------------
# Revenue:
#   + Estimated Sale Price    = ARV × SALE_PRICE_FACTOR
#
# Costs:
#   - Purchase Price          = Total Liens (what you pay to acquire)
#   - Rehab Costs             = case.rehab (repair estimate)
#   - Closing Costs (buy)     = ARV × CLOSING_COST_PERCENTAGE
#   - Selling Costs           = ARV × SELLING_COST_PERCENTAGE
#   - Holding Costs           = HOLDING_COST_MONTHLY × HOLDING_MONTHS
#
# Profit = Revenue - All Costs
#
# ROI FORMULA:
# ------------
# Total Investment = Purchase Price + Rehab + Closing Costs
# ROI % = (Profit / Total Investment) × 100


# ========================================
# DEAL QUALITY RATINGS
# ========================================

DEAL_QUALITY_THRESHOLDS = {
    "excellent": {
        "min_roi": 50,      # ROI >= 50%
        "min_equity": 40,   # Equity >= 40%
    },
    "good": {
        "min_roi": 30,      # ROI >= 30%
        "min_equity": 25,   # Equity >= 25%
    },
    "fair": {
        "min_roi": 15,      # ROI >= 15%
        "min_equity": 15,   # Equity >= 15%
    },
    # Anything below "fair" thresholds = "poor"
}


# ========================================
# PRIORITY ASSIGNMENT
# ========================================

PRIORITY_THRESHOLDS = {
    1: 80,   # Score 80-100 = Priority 1 (High)
    2: 60,   # Score 60-79 = Priority 2 (Medium)
    3: 40,   # Score 40-59 = Priority 3 (Low)
    4: 0,    # Score 0-39 = Priority 4 (Very Low)
}


# ========================================
# RECOMMENDATION THRESHOLDS
# ========================================

RECOMMENDATION_THRESHOLDS = {
    "hot_deal_score": 80,
    "good_deal_score": 60,
    "fair_deal_score": 40,
    
    "high_equity_pct": 50,
    "low_equity_pct": 10,
    
    "excellent_roi_pct": 40,
    "low_roi_pct": 10,
    
    "recent_filing_days": 30,
    "stale_lead_days": 180,
}


# ========================================
# RECOMMENDATION MESSAGES
# ========================================

RECOMMENDATION_MESSAGES = {
    "hot_deal": "🔥 HOT DEAL - Priority contact recommended",
    "good_deal": "✅ Good opportunity - Follow up soon",
    "fair_deal": "⚠️ Fair deal - Consider with caution",
    "poor_deal": "❌ Low score - May not be worth pursuing",
    
    "high_equity": "💰 High equity - Strong negotiating position",
    "low_equity": "⚠️ Low equity - Short sale likely required",
    
    "excellent_roi": "📈 Excellent ROI potential",
    "low_roi": "📉 Low ROI - Tight margins",
    
    "max_offer": "💵 Max offer: ${amount:,.0f} (70% rule)",
    
    "recent_filing": "⏰ Recent filing - Act quickly",
    "stale_lead": "🕐 Older case - May be stale lead",
}


# ========================================
# SCORE DISPLAY LABELS
# ========================================

SCORE_LABELS = {
    "excellent": {"min": 80, "label": "Excellent", "color": "#22c55e"},
    "good": {"min": 60, "label": "Good", "color": "#3b82f6"},
    "fair": {"min": 40, "label": "Fair", "color": "#f59e0b"},
    "poor": {"min": 0, "label": "Poor", "color": "#ef4444"},
}
