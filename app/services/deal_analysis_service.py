# app/services/deal_analysis_service.py
"""
Deal Analysis Service
Calculates deal metrics including score, profit, ROI, and equity

Formula:
- Max Offer = (ARV × 70%) - Rehab - Closing Costs
- Estimated Profit = ARV - (Max Offer + Rehab + Closing Costs)
- Equity % = ((ARV - Total Liens) / ARV) × 100
- ROI % = (Estimated Profit / Max Offer) × 100
"""

import json
import logging
from typing import Optional, Dict, Any, List

from sqlalchemy.orm import Session
from app.models import Case

logger = logging.getLogger("pascowebapp.deal_analysis")


def analyze_deal(case_id: int, db: Session) -> Dict[str, Any]:
    """
    Analyze a single case and return deal metrics.
    
    Args:
        case_id: The case ID to analyze
        db: Database session
    
    Returns:
        Dict with score, metrics, and recommendations
    """
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        return {"error": "Case not found"}
    
    # Get ARV
    arv = float(case.arv) if case.arv else 0.0
    if arv <= 0:
        return {"error": "ARV not set - cannot analyze deal"}
    
    # Get Rehab
    rehab = float(case.rehab) if case.rehab else 0.0
    
    # Get Closing Costs (default to 4.5% of ARV if not set)
    closing_costs = float(case.closing_costs) if case.closing_costs else (arv * 0.045)
    
    # Parse liens
    total_liens = 0.0
    try:
        liens_data = json.loads(case.outstanding_liens) if case.outstanding_liens else []
        if isinstance(liens_data, list):
            for lien in liens_data:
                if isinstance(lien, dict):
                    amt = lien.get("amount", "0")
                    amt_str = str(amt).replace("$", "").replace(",", "")
                    total_liens += float(amt_str) if amt_str else 0
    except Exception as e:
        logger.warning(f"Error parsing liens for case {case_id}: {e}")
    
    # Calculate Max Offer using 70% rule
    max_offer = (arv * 0.70) - rehab - closing_costs
    
    # CORRECT FORMULA: Estimated Profit = ARV - (Max Offer + Rehab + Closing Costs)
    # This equals ARV * 0.30 (the 30% margin from the 70% rule)
    estimated_profit = arv - (max_offer + rehab + closing_costs)
    
    # Equity percentage (how much equity vs liens)
    equity_pct = ((arv - total_liens) / arv * 100) if arv > 0 else 0
    
    # ROI percentage
    roi_pct = (estimated_profit / max_offer * 100) if max_offer > 0 else 0
    
    # Determine deal quality
    if equity_pct >= 40 and roi_pct >= 25:
        deal_quality = "Excellent"
    elif equity_pct >= 30 and roi_pct >= 15:
        deal_quality = "Good"
    elif equity_pct >= 20 and roi_pct >= 10:
        deal_quality = "Fair"
    else:
        deal_quality = "Poor"
    
    # Calculate score (0-100)
    score = 25  # Base score
    
    # Equity contribution (+25 max)
    if equity_pct >= 40:
        score += 25
    elif equity_pct >= 30:
        score += 15
    elif equity_pct >= 20:
        score += 5
    
    # ROI contribution (+15 max)
    if roi_pct > 40:
        score += 15
    elif roi_pct > 30:
        score += 10
    
    # Profit bonus (+10 max)
    if estimated_profit >= 50000:
        score += 10
    elif estimated_profit >= 25000:
        score += 5
    
    # Short sale penalty (liens exceed max offer)
    if total_liens > 0 and total_liens > max_offer:
        score -= 10
    
    # Clamp score to 0-100
    score = max(0, min(100, score))
    
    # Generate recommendations
    recommendations = []
    
    if equity_pct >= 40:
        recommendations.append("High equity - strong position")
    elif equity_pct < 20:
        recommendations.append("Low equity - proceed with caution")
    
    if roi_pct >= 25:
        recommendations.append("Excellent ROI potential")
    elif roi_pct < 10:
        recommendations.append("Low ROI - review rehab estimates")
    
    if estimated_profit >= 50000:
        recommendations.append("High profit potential")
    elif estimated_profit < 0:
        recommendations.append("⚠️ Negative profit - not recommended")
    
    if total_liens > max_offer:
        recommendations.append("⚠️ Liens exceed offer - short sale likely")
    
    if rehab == 0:
        recommendations.append("No rehab estimate - add for accuracy")
    
    if not recommendations:
        recommendations.append("Standard deal - review all factors")
    
    return {
        "case_id": case_id,
        "score": score,
        "metrics": {
            "arv": arv,
            "rehab": rehab,
            "closing_costs": closing_costs,
            "total_liens": total_liens,
            "max_offer": round(max_offer, 2),
            "estimated_profit": round(estimated_profit, 2),
            "equity_pct": round(equity_pct, 2),
            "roi_pct": round(roi_pct, 2),
            "deal_quality": deal_quality,
        },
        "recommendations": recommendations,
    }


def bulk_analyze_cases(
    limit: Optional[int] = None,
    min_score: int = 0,
    db: Session = None
) -> List[Dict[str, Any]]:
    """
    Analyze multiple cases and return results.
    
    Args:
        limit: Maximum number of cases to return (None = all)
        min_score: Minimum score to include in results
        db: Database session
    
    Returns:
        List of analysis results sorted by score descending
    """
    if not db:
        return []
    
    # Get cases with ARV set
    query = db.query(Case).filter(Case.arv.isnot(None), Case.arv > 0)
    
    if limit:
        query = query.limit(limit * 2)  # Get extra to filter by score
    
    cases = query.all()
    
    results = []
    for case in cases:
        try:
            analysis = analyze_deal(case.id, db)
            if "error" not in analysis and analysis.get("score", 0) >= min_score:
                # Add case info for display
                analysis["case_number"] = case.case_number
                analysis["address"] = case.address_override or case.address or ""
                results.append(analysis)
        except Exception as e:
            logger.warning(f"Error analyzing case {case.id}: {e}")
    
    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    
    # Apply limit
    if limit:
        results = results[:limit]
    
    return results
