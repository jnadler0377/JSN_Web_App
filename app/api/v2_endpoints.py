# app/api/v2_endpoints.py
"""
Version 2.0 API Endpoints
✅ Real-time Notifications
✅ Deal Analysis
✅ Enhanced Analytics (USER-FILTERED)
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from typing import Optional
from sqlalchemy.orm import Session
import asyncio
import json

from app.database import get_db
from app.models import User, Case
from app.services.auth_service import get_current_user
from app.services.notification_service import (
    get_user_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
    delete_notification,
    create_notification,
    add_listener,
    remove_listener,
)
from app.services.deal_analysis_service import (
    analyze_deal,
    bulk_analyze_cases,
)

router = APIRouter(prefix="/api/v2", tags=["v2"])


# ========================================
# NOTIFICATIONS ENDPOINTS
# ========================================

@router.get("/notifications")
async def list_notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db)
):
    """Get user's notifications"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id if hasattr(user, 'id') else user.get('id')
    
    notifications = get_user_notifications(
        user_id=user_id,
        unread_only=unread_only,
        limit=limit
    )
    
    unread_count = get_unread_count(user_id)
    
    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total": len(notifications),
    }


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Mark a notification as read"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    success = mark_as_read(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True, "message": "Notification marked as read"}


@router.post("/notifications/mark-all-read")
async def mark_all_notifications_read(
    request: Request,
    db: Session = Depends(get_db)
):
    """Mark all notifications as read"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id if hasattr(user, 'id') else user.get('id')
    count = mark_all_as_read(user_id)
    
    return {
        "success": True,
        "message": f"Marked {count} notifications as read",
        "count": count,
    }


@router.delete("/notifications/{notification_id}")
async def delete_notification_endpoint(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Delete a notification"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    success = delete_notification(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True, "message": "Notification deleted"}


@router.get("/notifications/stream")
async def notification_stream(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Server-Sent Events stream for real-time notifications
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id if hasattr(user, 'id') else user.get('id')
    
    async def event_generator():
        queue = asyncio.Queue()
        add_listener(user_id, queue)
        
        try:
            # Send initial heartbeat
            yield f"data: {json.dumps({'type': 'connected', 'user_id': user_id})}\n\n"
            
            while True:
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(notification)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        
        finally:
            remove_listener(user_id, queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ========================================
# DEAL ANALYSIS ENDPOINTS
# ========================================

@router.post("/cases/{case_id}/analyze")
async def analyze_case_endpoint(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Analyze a specific case"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    analysis = analyze_deal(case_id, db)
    
    if "error" in analysis:
        raise HTTPException(status_code=404, detail=analysis["error"])
    
    return analysis


@router.post("/cases/bulk-analyze")
async def bulk_analyze_endpoint(
    request: Request,
    limit: Optional[int] = Query(None, description="Max cases to analyze"),
    db: Session = Depends(get_db)
):
    """Analyze multiple cases"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    results = bulk_analyze_cases(limit=limit, db=db)
    
    return {
        "total_analyzed": len(results),
        "results": results,
    }


@router.get("/cases/top-deals")
async def get_top_deals(
    request: Request,
    limit: int = Query(10, le=50),
    min_score: int = Query(0, description="Minimum score threshold"),
    db: Session = Depends(get_db)
):
    """Get top-scoring deals from USER'S CLAIMED CASES ONLY"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id if hasattr(user, 'id') else user.get('id')
    
    # Get user's claimed cases with ARV ONLY
    cases = db.query(Case).filter(
        (Case.archived == None) | (Case.archived == 0),
        Case.arv.isnot(None),
        Case.arv > 0,
        Case.assigned_to == user_id  # ALWAYS filter by user
    ).all()
    
    top_deals = []
    for case in cases:
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
        estimated_profit = arv - (max_offer + rehab + closing_costs)
        equity_pct = ((arv - total_liens) / arv * 100) if arv > 0 else 0
        roi_pct = (estimated_profit / max_offer * 100) if max_offer > 0 else 0
        
        # Calculate score
        score = 25
        if equity_pct >= 40:
            score += 25
        elif equity_pct >= 30:
            score += 15
        elif equity_pct >= 20:
            score += 5
        
        if roi_pct > 40:
            score += 15
        elif roi_pct > 30:
            score += 10
        
        if estimated_profit >= 50000:
            score += 10
        elif estimated_profit >= 25000:
            score += 5
        
        if total_liens > 0 and total_liens > max_offer:
            score -= 10
        
        score = max(0, min(100, score))
        
        if score >= min_score:
            top_deals.append({
                "id": case.id,
                "case_id": case.id,
                "case_number": case.case_number or "",
                "address": case.address_override or case.address or "",
                "score": score,
                "score_class": "excellent" if score >= 80 else "good" if score >= 60 else "fair" if score >= 40 else "poor",
                "arv": arv,
                "metrics": {
                    "estimated_profit": round(estimated_profit, 2),
                    "max_offer": round(max_offer, 2),
                    "roi_pct": round(roi_pct, 1),
                    "equity_pct": round(equity_pct, 1),
                },
            })
    
    top_deals.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "total_deals": len(top_deals),
        "top_deals": top_deals[:limit],
        "min_score": min_score,
        "user_id": user_id,
        "version": "v3_user_filtered",
    }


@router.get("/analytics/deal-distribution")
async def deal_score_distribution(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get distribution of deal scores for USER'S CLAIMED CASES ONLY"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id if hasattr(user, 'id') else user.get('id')
    
    # Get user's claimed cases with ARV ONLY
    cases = db.query(Case).filter(
        (Case.archived == None) | (Case.archived == 0),
        Case.arv.isnot(None),
        Case.arv > 0,
        Case.assigned_to == user_id  # ALWAYS filter by user
    ).all()
    
    distribution = {
        "excellent": 0,
        "good": 0,
        "fair": 0,
        "poor": 0,
    }
    
    for case in cases:
        arv = float(case.arv or 0)
        rehab = float(case.rehab or 0)
        closing_costs = float(case.closing_costs or 0)
        
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
        
        max_offer = (arv * 0.70) - rehab - closing_costs
        estimated_profit = arv - (max_offer + rehab + closing_costs)
        equity_pct = ((arv - total_liens) / arv * 100) if arv > 0 else 0
        roi_pct = (estimated_profit / max_offer * 100) if max_offer > 0 else 0
        
        score = 25
        if equity_pct >= 40:
            score += 25
        elif equity_pct >= 30:
            score += 15
        elif equity_pct >= 20:
            score += 5
        
        if roi_pct > 40:
            score += 15
        elif roi_pct > 30:
            score += 10
        
        if estimated_profit >= 50000:
            score += 10
        elif estimated_profit >= 25000:
            score += 5
        
        if total_liens > 0 and total_liens > max_offer:
            score -= 10
        
        score = max(0, min(100, score))
        
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
        "total_analyzed": len(cases),
        "user_id": user_id,
        "version": "v3_user_filtered",
    }


# ========================================
# TESTING ENDPOINTS
# ========================================

@router.post("/notifications/test")
async def create_test_notification(
    request: Request,
    db: Session = Depends(get_db)
):
    """Create a test notification (for development)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id if hasattr(user, 'id') else user.get('id')
    
    notification = create_notification(
        user_id=user_id,
        title="Test Notification",
        message="This is a test notification from the API",
        notification_type="info",
        link="/cases"
    )
    
    return {
        "success": True,
        "notification": notification,
    }
