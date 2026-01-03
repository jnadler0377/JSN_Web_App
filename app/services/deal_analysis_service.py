# app/services/deal_analysis_service.py
"""
Deal Analysis Service - Automated property deal scoring and analysis
✅ Calculate deal scores (0-100)
✅ Compute investment metrics
✅ Generate recommendations
✅ Auto-prioritize leads

All scoring parameters and calculation formulas are in deal_analysis_config.py
"""

from __future__ import annotations

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Case

# Import configuration (from same services folder)
from app.services.deal_analysis_config import (
    SCORE_WEIGHTS,
    EQUITY_THRESHOLDS,
    FREE_AND_CLEAR_BONUS,
    SIZE_THRESHOLDS,
    CONDITION_SCORES,
    AGE_THRESHOLDS,
    AGE_UNKNOWN_SCORE,
    MARKET_FACTORS,
    MARKET_DEFAULT_SCORE,
    URGENCY_THRESHOLDS,
    URGENCY_UNKNOWN_SCORE,
    MAO_PERCENTAGE,
    CLOSING_COST_PERCENTAGE,
    SELLING_COST_PERCENTAGE,
    SALE_PRICE_FACTOR,
    HOLDING_COST_MONTHLY,
    HOLDING_MONTHS,
    DEAL_QUALITY_THRESHOLDS,
    PRIORITY_THRESHOLDS,
    RECOMMENDATION_THRESHOLDS,
    RECOMMENDATION_MESSAGES,
    SCORE_LABELS,
)

logger = logging.getLogger("pascowebapp.deal_analysis")


# ========================================
# DEAL SCORING ALGORITHM
# ========================================

def calculate_deal_score(case: Case) -> int:
    """
    Calculate comprehensive deal score (0-100)
    """
    total_score = 0
    
    # 1. EQUITY SCORE
    equity_score = calculate_equity_score(case)
    total_score += equity_score
    
    # 2. PROPERTY SCORE
    property_score = calculate_property_score(case)
    total_score += property_score
    
    # 3. MARKET SCORE
    market_score = calculate_market_score(case)
    total_score += market_score
    
    # 4. URGENCY SCORE
    urgency_score = calculate_urgency_score(case)
    total_score += urgency_score
    
    # Cap at 100
    return min(100, max(0, int(total_score)))


def calculate_equity_score(case: Case) -> float:
    """
    Calculate equity score based on config thresholds
    """
    max_score = SCORE_WEIGHTS["equity"]
    
    # Get ARV (After Repair Value)
    arv = 0.0
    if case.arv and case.arv > 0:
        arv = float(case.arv)
    elif hasattr(case, 'prop_est_value') and case.prop_est_value and case.prop_est_value > 0:
        arv = float(case.prop_est_value)
    
    if arv <= 0:
        return 0.0
    
    # Get total liens
    total_liens = _get_total_liens(case)
    
    # Calculate equity percentage
    # Formula: Equity % = (ARV - Total Liens) / ARV × 100
    equity = arv - total_liens
    equity_pct = (equity / arv) * 100 if arv > 0 else 0
    
    # Score based on equity percentage (using config thresholds)
    score = 0.0
    for threshold, points in sorted(EQUITY_THRESHOLDS.items(), reverse=True):
        if equity_pct >= threshold:
            score = points
            break
    
    # Bonus for free & clear (check case flags)
    if hasattr(case, 'ql_free_and_clear') and case.ql_free_and_clear:
        score = min(max_score, score + FREE_AND_CLEAR_BONUS)
    
    return min(max_score, score)


def calculate_property_score(case: Case) -> float:
    """
    Calculate property quality score based on config thresholds
    """
    max_score = SCORE_WEIGHTS["property"]
    score = 0.0
    
    # --- SIZE SCORE ---
    sqft = 0
    if hasattr(case, 'prop_sqft') and case.prop_sqft and case.prop_sqft > 0:
        sqft = case.prop_sqft
    
    for threshold, points in sorted(SIZE_THRESHOLDS.items(), reverse=True):
        if sqft >= threshold:
            score += points
            break
    else:
        score += SIZE_THRESHOLDS.get(0, 2)
    
    # --- CONDITION SCORE ---
    if hasattr(case, 'rehab_condition') and case.rehab_condition:
        condition = case.rehab_condition.lower()
        score += CONDITION_SCORES.get(condition, CONDITION_SCORES.get("unknown", 6))
    else:
        score += CONDITION_SCORES.get("unknown", 6)
    
    # --- AGE SCORE ---
    current_year = datetime.now().year
    year_built = 0
    
    if hasattr(case, 'prop_year_built') and case.prop_year_built:
        try:
            year_built = int(case.prop_year_built)
        except (ValueError, TypeError):
            pass
    
    if year_built > 0:
        age = current_year - year_built
        for max_age, points in sorted(AGE_THRESHOLDS.items()):
            if age <= max_age:
                score += points
                break
    else:
        score += AGE_UNKNOWN_SCORE
    
    return min(max_score, score)


def calculate_market_score(case: Case) -> float:
    """
    Calculate market conditions score based on config factors
    """
    max_score = SCORE_WEIGHTS["market"]
    score = 0.0
    
    # Check each market factor from case attributes
    if hasattr(case, 'ql_vacant') and case.ql_vacant:
        score += MARKET_FACTORS.get("vacant", 0)
    
    if hasattr(case, 'ql_owner_occupied') and case.ql_owner_occupied:
        score += MARKET_FACTORS.get("owner_occupied", 0)
    
    if hasattr(case, 'ql_preforeclosure') and case.ql_preforeclosure:
        score += MARKET_FACTORS.get("preforeclosure", 0)
    
    # If no flags, give default score
    if score == 0:
        score = MARKET_DEFAULT_SCORE
    
    return min(max_score, score)


def calculate_urgency_score(case: Case) -> float:
    """
    Calculate urgency score based on config thresholds
    """
    max_score = SCORE_WEIGHTS["urgency"]
    
    if not case.filing_datetime:
        return URGENCY_UNKNOWN_SCORE
    
    try:
        # Handle both string and datetime objects
        if isinstance(case.filing_datetime, str):
            filing_date = datetime.fromisoformat(case.filing_datetime.replace('Z', '+00:00'))
        else:
            filing_date = case.filing_datetime
        
        # Make naive datetime for comparison
        if filing_date.tzinfo is not None:
            filing_date = filing_date.replace(tzinfo=None)
        
        days_since_filing = (datetime.now() - filing_date).days
        
        for max_days, points in sorted(URGENCY_THRESHOLDS.items()):
            if days_since_filing <= max_days:
                return min(max_score, points)
        
        return 1  # Fallback for very old cases
        
    except (ValueError, AttributeError, TypeError):
        return URGENCY_UNKNOWN_SCORE


# ========================================
# HELPER FUNCTIONS
# ========================================

def _get_total_liens(case: Case) -> float:
    """Get total outstanding liens for a case"""
    total_liens = 0.0
    try:
        liens = case.get_outstanding_liens() if hasattr(case, 'get_outstanding_liens') else []
        for lien in liens:
            amount = lien.get('amount') or lien.get('balance') or 0
            try:
                total_liens += float(amount)
            except (ValueError, TypeError):
                continue
    except Exception:
        total_liens = 0.0
    return total_liens


# ========================================
# INVESTMENT METRICS
# ========================================

def calculate_investment_metrics(case: Case) -> Dict[str, Any]:
    """
    Calculate comprehensive investment metrics using config formulas
    
    FORMULAS (from config):
    - Max Offer (MAO) = (ARV × MAO_PERCENTAGE) - Rehab - Closing Costs
    - Equity % = (ARV - Total Liens) / ARV × 100
    - Estimated Profit = Sale Price - Purchase - Rehab - Closing - Selling - Holding
    - ROI % = Profit / Total Investment × 100
    """
    metrics = {
        "max_offer": 0.0,
        "estimated_profit": 0.0,
        "roi_pct": 0.0,
        "equity_pct": 0.0,
        "deal_quality": "Unknown",
    }
    
    # Get ARV (After Repair Value)
    arv = 0.0
    if case.arv and case.arv > 0:
        arv = float(case.arv)
    elif hasattr(case, 'prop_est_value') and case.prop_est_value and case.prop_est_value > 0:
        arv = float(case.prop_est_value)
    
    if arv <= 0:
        return metrics
    
    # Get rehab costs
    rehab = float(case.rehab) if case.rehab else 0.0
    
    # Calculate closing costs
    # Use case value if set, otherwise use percentage from config
    if hasattr(case, 'closing_costs') and case.closing_costs and case.closing_costs > 0:
        closing_costs = float(case.closing_costs)
    else:
        closing_costs = arv * CLOSING_COST_PERCENTAGE
    
    # Get total liens (this is the "purchase price" - what we'd pay to acquire)
    total_liens = _get_total_liens(case)
    
    # ---- CALCULATE MAX OFFER (MAO) ----
    # Formula: MAO = (ARV × MAO_PERCENTAGE) - Rehab - Closing Costs
    max_offer = (arv * MAO_PERCENTAGE) - rehab - closing_costs
    metrics["max_offer"] = max(0, max_offer)
    
    # ---- CALCULATE EQUITY ----
    # Formula: Equity % = (ARV - Total Liens) / ARV × 100
    equity = arv - total_liens
    equity_pct = (equity / arv) * 100 if arv > 0 else 0
    metrics["equity_pct"] = round(equity_pct, 1)
    
    # ---- CALCULATE PROFIT ----
    # Revenue: Estimated Sale Price = ARV × SALE_PRICE_FACTOR
    estimated_sale_price = arv * SALE_PRICE_FACTOR
    
    # Costs:
    purchase_price = total_liens  # What we pay to acquire
    selling_costs = arv * SELLING_COST_PERCENTAGE
    holding_costs = HOLDING_COST_MONTHLY * HOLDING_MONTHS
    
    # Total costs
    total_costs = purchase_price + rehab + closing_costs + selling_costs + holding_costs
    
    # Profit = Revenue - Costs
    estimated_profit = estimated_sale_price - total_costs
    metrics["estimated_profit"] = round(estimated_profit, 0)
    
    # ---- CALCULATE ROI ----
    # Total Investment = Purchase Price + Rehab + Closing Costs
    total_investment = purchase_price + rehab + closing_costs
    
    # ROI % = (Profit / Total Investment) × 100
    roi_pct = 0.0
    if total_investment > 0:
        roi_pct = (estimated_profit / total_investment) * 100
    metrics["roi_pct"] = round(roi_pct, 1)
    
    # ---- DETERMINE DEAL QUALITY ----
    metrics["deal_quality"] = get_deal_quality(roi_pct, equity_pct)
    
    return metrics


def get_deal_quality(roi_pct: float, equity_pct: float) -> str:
    """
    Determine deal quality rating based on config thresholds
    """
    for quality in ["excellent", "good", "fair"]:
        thresholds = DEAL_QUALITY_THRESHOLDS.get(quality, {})
        min_roi = thresholds.get("min_roi", 0)
        min_equity = thresholds.get("min_equity", 0)
        
        if roi_pct >= min_roi and equity_pct >= min_equity:
            return quality.capitalize()
    
    return "Poor"


# ========================================
# DEAL RECOMMENDATIONS
# ========================================

def generate_recommendations(case: Case, score: int, metrics: Dict[str, Any]) -> list[str]:
    """
    Generate actionable recommendations based on config thresholds
    """
    recommendations = []
    thresholds = RECOMMENDATION_THRESHOLDS
    messages = RECOMMENDATION_MESSAGES
    
    # Score-based recommendations
    if score >= thresholds["hot_deal_score"]:
        recommendations.append(messages["hot_deal"])
    elif score >= thresholds["good_deal_score"]:
        recommendations.append(messages["good_deal"])
    elif score >= thresholds["fair_deal_score"]:
        recommendations.append(messages["fair_deal"])
    else:
        recommendations.append(messages["poor_deal"])
    
    # Equity-based recommendations
    equity_pct = metrics.get("equity_pct", 0)
    if equity_pct >= thresholds["high_equity_pct"]:
        recommendations.append(messages["high_equity"])
    elif equity_pct <= thresholds["low_equity_pct"]:
        recommendations.append(messages["low_equity"])
    
    # ROI-based recommendations
    roi_pct = metrics.get("roi_pct", 0)
    if roi_pct >= thresholds["excellent_roi_pct"]:
        recommendations.append(messages["excellent_roi"])
    elif roi_pct <= thresholds["low_roi_pct"]:
        recommendations.append(messages["low_roi"])
    
    # Offer recommendation
    max_offer = metrics.get("max_offer", 0)
    if max_offer > 0:
        offer_msg = messages["max_offer"].format(amount=max_offer)
        recommendations.append(offer_msg)
    
    # Urgency recommendations
    if case.filing_datetime:
        try:
            if isinstance(case.filing_datetime, str):
                filing_date = datetime.fromisoformat(case.filing_datetime.replace('Z', '+00:00'))
            else:
                filing_date = case.filing_datetime
            
            if filing_date.tzinfo is not None:
                filing_date = filing_date.replace(tzinfo=None)
            
            days_since_filing = (datetime.now() - filing_date).days
            
            if days_since_filing <= thresholds["recent_filing_days"]:
                recommendations.append(messages["recent_filing"])
            elif days_since_filing >= thresholds["stale_lead_days"]:
                recommendations.append(messages["stale_lead"])
        except:
            pass
    
    return recommendations


def get_priority_from_score(score: int) -> int:
    """
    Get priority level from score based on config thresholds
    """
    for priority, min_score in sorted(PRIORITY_THRESHOLDS.items()):
        if score >= min_score:
            return priority
    return 4


def get_score_label(score: int) -> Dict[str, Any]:
    """
    Get display label and color for a score based on config
    """
    for key, info in SCORE_LABELS.items():
        if score >= info["min"]:
            return {
                "class": key,
                "label": info["label"],
                "color": info["color"],
            }
    return {"class": "poor", "label": "Poor", "color": "#ef4444"}


# ========================================
# FULL ANALYSIS
# ========================================

def analyze_deal(case_id: int, db: Optional[Session] = None) -> Dict[str, Any]:
    """
    Perform complete deal analysis
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        # Get case
        case = db.query(Case).filter(Case.id == case_id).first()
        if not case:
            return {"error": "Case not found"}
        
        # Calculate score
        score = calculate_deal_score(case)
        
        # Calculate metrics
        metrics = calculate_investment_metrics(case)
        
        # Generate recommendations
        recommendations = generate_recommendations(case, score, metrics)
        
        # Determine priority
        priority = get_priority_from_score(score)
        
        # Get score label
        score_info = get_score_label(score)
        
        analysis = {
            "case_id": case_id,
            "case_number": case.case_number,
            "score": score,
            "score_label": score_info["label"],
            "score_class": score_info["class"],
            "metrics": metrics,
            "recommendations": recommendations,
            "priority": priority,
            "analyzed_at": datetime.utcnow().isoformat(),
        }
        
        logger.info(f"Analyzed case {case_id}: Score={score}, Quality={metrics['deal_quality']}")
        
        return analysis
    
    except Exception as e:
        logger.error(f"Error analyzing case {case_id}: {e}")
        return {"error": str(e)}
    
    finally:
        if should_close:
            db.close()


def bulk_analyze_cases(limit: Optional[int] = None, db: Optional[Session] = None) -> list[Dict[str, Any]]:
    """
    Analyze multiple cases and return results sorted by score
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        # Get active cases
        query = db.query(Case).filter(
            (Case.archived == 0) | (Case.archived == None)
        )
        
        if limit:
            query = query.limit(limit)
        
        cases = query.all()
        
        results = []
        for case in cases:
            try:
                analysis = analyze_deal(case.id, db)
                if "error" not in analysis:
                    results.append(analysis)
            except Exception as e:
                logger.error(f"Error analyzing case {case.id}: {e}")
                continue
        
        # Sort by score (highest first)
        results.sort(key=lambda x: x["score"], reverse=True)
        
        logger.info(f"Bulk analyzed {len(results)} cases")
        
        return results
    
    finally:
        if should_close:
            db.close()


# ========================================
# CONFIGURATION HELPERS
# ========================================

def get_current_config() -> Dict[str, Any]:
    """
    Return current configuration for display/debugging
    """
    return {
        "score_weights": SCORE_WEIGHTS,
        "equity_thresholds": EQUITY_THRESHOLDS,
        "size_thresholds": SIZE_THRESHOLDS,
        "condition_scores": CONDITION_SCORES,
        "age_thresholds": AGE_THRESHOLDS,
        "market_factors": MARKET_FACTORS,
        "urgency_thresholds": URGENCY_THRESHOLDS,
        "mao_percentage": MAO_PERCENTAGE,
        "closing_cost_percentage": CLOSING_COST_PERCENTAGE,
        "selling_cost_percentage": SELLING_COST_PERCENTAGE,
        "sale_price_factor": SALE_PRICE_FACTOR,
        "deal_quality_thresholds": DEAL_QUALITY_THRESHOLDS,
        "priority_thresholds": PRIORITY_THRESHOLDS,
    }
