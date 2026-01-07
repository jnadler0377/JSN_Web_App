# app/routes/analytics_routes.py
"""
Analytics Routes
- Dashboard page (global stats for admins)
- Analytics page (user-specific stats)
- Dashboard metrics API

NOTE: Deal analysis API endpoints are in app/api/v2_endpoints.py
      Do NOT duplicate routes here.
"""

import json
import logging
from datetime import datetime, timedelta

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

logger = logging.getLogger("pascowebapp.analytics")

router = APIRouter(tags=["analytics"])

# Templates - will be set by main.py
templates = None


def init_templates(t):
    """Initialize templates from main app"""
    global templates
    templates = t


# ============================================================
# DASHBOARD PAGE (Admin/Global Stats)
# ============================================================

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    db: Session = Depends(get_db)
):
    """Analytics dashboard page - shows global stats"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
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
def api_dashboard_metrics(request: Request):
    """API endpoint for dashboard metrics (for AJAX refresh)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return get_dashboard_metrics()


# ============================================================
# ANALYTICS PAGE (User-Specific Stats)
# ============================================================

@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    """User Analytics Dashboard - Shows user's claimed cases ONLY"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    # Get user ID
    if isinstance(user, dict):
        user_id = user.get("id")
    else:
        user_id = getattr(user, "id", None)
    
    # Get user's claimed cases ONLY
    user_cases = db.query(Case).filter(
        text("(archived IS NULL OR archived = 0)"),
        Case.assigned_to == user_id
    ).all()
    
    # Calculate user-specific metrics
    total_cases = len(user_cases)
    cases_with_arv = [c for c in user_cases if c.arv and c.arv > 0]
    
    total_arv = sum(c.arv or 0 for c in cases_with_arv)
    total_rehab = sum(c.rehab or 0 for c in cases_with_arv)
    avg_arv = total_arv / len(cases_with_arv) if cases_with_arv else 0
    avg_rehab = total_rehab / len(cases_with_arv) if cases_with_arv else 0
    
    # Calculate potential profit (30% of ARV)
    total_potential_profit = sum((c.arv or 0) * 0.30 for c in cases_with_arv)
    
    # Count high-value cases (ARV > $300k)
    high_value_count = sum(1 for c in cases_with_arv if (c.arv or 0) > 300000)
    
    # Count short sales and high equity
    short_sale_count = 0
    high_equity_count = 0
    for c in cases_with_arv:
        arv = float(c.arv or 0)
        rehab = float(c.rehab or 0)
        closing = float(c.closing_costs or 0)
        max_offer = (arv * 0.70) - rehab - closing
        
        total_liens = 0.0
        try:
            liens_data = json.loads(c.outstanding_liens) if c.outstanding_liens else []
            if isinstance(liens_data, list):
                for lien in liens_data:
                    if isinstance(lien, dict):
                        amt = lien.get("amount", "0")
                        amt_str = str(amt).replace("$", "").replace(",", "")
                        total_liens += float(amt_str) if amt_str else 0
        except:
            pass
        
        if total_liens > max_offer:
            short_sale_count += 1
        
        equity_pct = ((arv - total_liens) / arv * 100) if arv > 0 else 0
        if equity_pct >= 40:
            high_equity_count += 1
    
    # New cases this month
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    new_cases_30d = sum(1 for c in user_cases if c.assigned_at and c.assigned_at >= thirty_days_ago)
    
    metrics = {
        "total_cases": total_cases,
        "active_cases": total_cases,
        "new_cases_30d": new_cases_30d,
        "avg_arv": avg_arv,
        "avg_rehab": avg_rehab,
        "total_potential_profit": total_potential_profit,
        "high_value_count": high_value_count,
        "short_sale_count": short_sale_count,
        "high_equity_count": high_equity_count,
        "owner_occupied_count": 0,
    }
    
    funnel = {
        "total_leads": total_cases,
        "contacted": 0,
        "offer_sent": 0,
        "offer_accepted": 0,
        "closed_won": 0,
        "contact_rate": 0,
        "offer_rate": 0,
        "close_rate": 0,
    }
    
    return templates.TemplateResponse("analytics_dashboard.html", {
        "request": request,
        "user": user,
        "current_user": user,
        "metrics": metrics,
        "funnel": funnel,
        "roi": {},
        "opportunities": [],
    })


# ============================================================
# CASES LIST API
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
            qry = qry.filter(text("assigned_to = :uid")).params(uid=user_id)
        else:
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
# NOTE: The following routes are defined in v2_endpoints.py
# Do NOT add them here to avoid duplicates:
# - /api/v2/cases/{case_id}/analyze
# - /api/v2/cases/bulk-analyze  
# - /api/v2/cases/top-deals
# - /api/v2/analytics/deal-distribution
# ============================================================
