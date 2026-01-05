# app/routes/analytics_routes.py
"""
Analytics Routes
- Dashboard page
- Analytics page
- Dashboard metrics API
- Deal analysis API endpoints
"""

import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app.models import Case
from app.services.auth_service import get_current_user
from app.services.analytics_service import (
    get_dashboard_metrics,
    get_conversion_funnel,
    get_roi_analysis,
    get_top_opportunities,
    get_cases_by_month,
)
# Try to import config
try:
    from app.config import settings
except ImportError:
    class settings:
        enable_analytics = True

# Try to import deal analyzer (may not exist)
try:
    from app.services.deal_analyzer import analyze_deal, bulk_analyze_cases
    DEAL_ANALYZER_AVAILABLE = True
except ImportError:
    DEAL_ANALYZER_AVAILABLE = False
    analyze_deal = None
    bulk_analyze_cases = None

logger = logging.getLogger("pascowebapp.analytics")

router = APIRouter(tags=["analytics"])

# Templates - will be set by main.py
templates = None


def init_templates(t):
    """Initialize templates from main app"""
    global templates
    templates = t


# ============================================================
# DASHBOARD PAGE
# ============================================================

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Analytics dashboard page"""
    if not settings.enable_analytics:
        return RedirectResponse(url="/cases", status_code=303)
    
    metrics = get_dashboard_metrics()
    monthly_data = get_cases_by_month(months=12)
    funnel = get_conversion_funnel()
    roi = get_roi_analysis()
    opportunities = get_top_opportunities(limit=10)
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "metrics": metrics,
            "monthly_data": monthly_data,
            "funnel": funnel,
            "roi": roi,
            "opportunities": opportunities,
        }
    )


@router.get("/api/dashboard/metrics")
def api_dashboard_metrics(user: dict = Depends(get_current_user)):
    """API endpoint for dashboard metrics (for AJAX refresh)"""
    return get_dashboard_metrics()


# ============================================================
# ANALYTICS PAGE
# ============================================================

@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    """V2.0 Enhanced Analytics Dashboard"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    # Gather metrics
    metrics = get_dashboard_metrics()
    funnel = get_conversion_funnel()
    roi = get_roi_analysis()
    opportunities = get_top_opportunities(limit=10)
    
    return templates.TemplateResponse("analytics_dashboard.html", {
        "request": request,
        "user": user,
        "current_user": user,
        "metrics": metrics,
        "funnel": funnel,
        "roi": roi,
        "opportunities": opportunities,
    })


# ============================================================
# CASES LIST API (V2)
# ============================================================

@router.get("/api/v2/cases")
def api_get_cases_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    show_archived: int = Query(0),
    search: str = Query(None),
    db: Session = Depends(get_db)
):
    """Get paginated list of cases for API consumers"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Build query
    qry = db.query(Case)
    
    # Filter archived
    if show_archived == 0:
        qry = qry.filter((Case.archived == None) | (Case.archived == 0))
    
    # Filter by user permissions
    if user and user.get("role") != "admin" and not getattr(user, "is_admin", False):
        user_id = user.get("id")
        role = user.get("role", "")
        
        if role == "subscriber":
            # Subscribers ONLY see cases assigned to them
            qry = qry.filter(text("assigned_to = :uid")).params(uid=user_id)
        else:
            # Analysts/Owners see assigned cases + unassigned cases
            qry = qry.filter(text("(assigned_to = :uid OR assigned_to IS NULL)")).params(uid=user_id)
    
    # Search filter
    if search:
        search_term = f"%{search}%"
        qry = qry.filter(
            (Case.case_number.ilike(search_term)) |
            (Case.address.ilike(search_term)) |
            (Case.address_override.ilike(search_term))
        )
    
    # Get total count
    total = qry.count()
    
    # Paginate
    offset = (page - 1) * page_size
    cases = qry.order_by(Case.id.desc()).offset(offset).limit(page_size).all()
    
    # Format response
    cases_list = []
    for c in cases:
        cases_list.append({
            "id": c.id,
            "case_number": c.case_number or "",
            "address": c.address or "",
            "address_override": c.address_override or "",
            "parcel_id": c.parcel_id or "",
            "filing_datetime": c.filing_datetime or "",
            "style": c.style or "",
            "arv": float(c.arv or 0),
            "rehab": float(c.rehab or 0),
            "closing_costs": float(c.closing_costs or 0),
            "archived": bool(c.archived) if c.archived else False
        })
    
    return {
        "cases": cases_list,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }


# ============================================================
# DEAL ANALYSIS API
# ============================================================

@router.post("/api/v2/cases/{case_id}/analyze")
def api_analyze_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Analyze a specific case and return score + metrics"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DEAL_ANALYZER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Deal analyzer not available")
    
    analysis = analyze_deal(case_id, db)
    
    if "error" in analysis:
        raise HTTPException(status_code=404, detail=analysis["error"])
    
    return analysis


@router.post("/api/v2/cases/bulk-analyze")
def api_bulk_analyze(
    request: Request,
    limit: int = None,
    db: Session = Depends(get_db)
):
    """Analyze multiple cases"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DEAL_ANALYZER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Deal analyzer not available")
    
    results = bulk_analyze_cases(limit=limit, db=db)
    
    return {
        "total_analyzed": len(results),
        "results": results,
    }


@router.get("/api/v2/cases/top-deals")
def api_get_top_deals(
    request: Request,
    limit: int = 10,
    min_score: int = 0,
    db: Session = Depends(get_db)
):
    """Get top-scoring deals with case details"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Get all active cases with ARV set
    cases = db.query(Case).filter(
        text("(archived IS NULL OR archived = 0)"),
        Case.arv.isnot(None),
        Case.arv > 0
    ).all()
    
    top_deals = []
    for case in cases:
        # Calculate deal score inline
        arv = float(case.arv or 0)
        rehab = float(case.rehab or 0)
        closing_costs = float(case.closing_costs or 0)
        
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
        except:
            pass
        
        if arv <= 0:
            continue
            
        # Calculate metrics (70% rule)
        max_offer = (arv * 0.70) - rehab - closing_costs
        estimated_profit = arv - max_offer - rehab - closing_costs - total_liens
        equity_pct = ((arv - total_liens) / arv * 100) if arv > 0 else 0
        roi_pct = (estimated_profit / max_offer * 100) if max_offer > 0 else 0
        
        # Calculate score
        score = 50  # Base
        if equity_pct >= 40:
            score += 25
        elif equity_pct >= 30:
            score += 15
        elif equity_pct >= 20:
            score += 5
        
        if roi_pct >= 30:
            score += 25
        elif roi_pct >= 20:
            score += 15
        elif roi_pct >= 10:
            score += 5
        
        if estimated_profit >= 50000:
            score += 10
        elif estimated_profit >= 25000:
            score += 5
        
        if total_liens > 0 and total_liens > max_offer:
            score -= 20  # Short sale penalty
        
        score = max(0, min(100, score))
        
        if score >= min_score:
            top_deals.append({
                "id": case.id,
                "case_id": case.id,
                "case_number": case.case_number or "",
                "address": case.address_override or case.address or "",
                "score": score,
                "arv": arv,
                "estimated_profit": round(estimated_profit, 2),
                "max_offer": round(max_offer, 2),
                "total_liens": total_liens,
                "rehab": rehab,
                "closing_costs": closing_costs,
            })
    
    # Sort by score descending
    top_deals.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "total_deals": len(top_deals),
        "top_deals": top_deals[:limit],
        "min_score": min_score,
    }


@router.get("/api/v2/analytics/deal-distribution")
def api_deal_distribution(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get distribution of deal scores"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DEAL_ANALYZER_AVAILABLE:
        return {
            "distribution": {"excellent": 0, "good": 0, "fair": 0, "poor": 0},
            "total_analyzed": 0,
        }
    
    results = bulk_analyze_cases(db=db)
    
    # Group by score ranges
    distribution = {
        "excellent": 0,  # 80-100
        "good": 0,       # 60-79
        "fair": 0,       # 40-59
        "poor": 0,       # 0-39
    }
    
    for result in results:
        score = result["score"]
        if score >= 80:
            distribution["excellent"] += 1
        elif score >= 60:
            distribution["good"] += 1
        elif score >= 40:
            distribution["fair"] += 1
        else:
            distribution["poor"] += 1
    
    return {
        "distribution": distribution,
        "total_analyzed": len(results),
    }
