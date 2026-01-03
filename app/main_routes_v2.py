# =====================================================
# V2.0 ROUTE ADDITIONS FOR main.py
# Copy these routes into your main.py file
# =====================================================

# -----------------------------------------------------
# ADD THESE IMPORTS AT THE TOP OF main.py
# -----------------------------------------------------

from fastapi.responses import StreamingResponse
import asyncio

# Import V2 services
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

# -----------------------------------------------------
# ADD THESE ROUTES (paste at end of main.py)
# -----------------------------------------------------

# ==========================================
# V2.0 ANALYTICS DASHBOARD ROUTE
# ==========================================

@app.get("/analytics", response_class=HTMLResponse)
def analytics_dashboard(request: Request, db: Session = Depends(get_db)):
    """V2.0 Enhanced Analytics Dashboard"""
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    # Import analytics service
    from app.services.analytics_service import (
        get_dashboard_metrics,
        get_conversion_funnel,
        get_roi_analysis,
        get_top_opportunities
    )
    
    # Gather metrics
    metrics = get_dashboard_metrics()
    funnel = get_conversion_funnel()
    roi = get_roi_analysis()
    opportunities = get_top_opportunities(limit=10)
    
    return templates.TemplateResponse("analytics_dashboard.html", {
        "request": request,
        "user": user,
        "metrics": metrics,
        "funnel": funnel,
        "roi": roi,
        "opportunities": opportunities,
    })


# ==========================================
# V2.0 NOTIFICATIONS API
# ==========================================

@app.get("/api/v2/notifications")
def api_get_notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get user's notifications"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    notifications = get_user_notifications(
        user_id=user.id,
        unread_only=unread_only,
        limit=limit
    )
    
    unread_count = get_unread_count(user.id)
    
    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total": len(notifications),
    }


@app.post("/api/v2/notifications/{notification_id}/read")
def api_mark_notification_read(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Mark a notification as read"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    success = mark_as_read(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True}


@app.post("/api/v2/notifications/mark-all-read")
def api_mark_all_read(
    request: Request,
    db: Session = Depends(get_db)
):
    """Mark all notifications as read"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    count = mark_all_as_read(user.id)
    
    return {"success": True, "count": count}


@app.delete("/api/v2/notifications/{notification_id}")
def api_delete_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Delete a notification"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    success = delete_notification(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True}


@app.get("/api/v2/notifications/stream")
async def notification_stream(request: Request, db: Session = Depends(get_db)):
    """Server-Sent Events stream for real-time notifications"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_id = user.id
    
    async def event_generator():
        queue = asyncio.Queue()
        add_listener(user_id, queue)
        
        try:
            # Send initial connection message
            yield f"data: {{\"type\": \"connected\", \"user_id\": {user_id}}}\n\n"
            
            while True:
                try:
                    # Wait for notification or timeout (heartbeat)
                    notification = await asyncio.wait_for(queue.get(), timeout=30.0)
                    import json
                    yield f"data: {json.dumps(notification)}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield f"data: {{\"type\": \"heartbeat\"}}\n\n"
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


@app.post("/api/v2/notifications/test")
def api_create_test_notification(
    request: Request,
    db: Session = Depends(get_db)
):
    """Create a test notification (for development)"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    notification = create_notification(
        user_id=user.id,
        title="Test Notification",
        message="This is a test notification from the API",
        notification_type="info",
        link="/cases"
    )
    
    return {"success": True, "notification": notification}


# ==========================================
# V2.0 DEAL ANALYSIS API
# ==========================================

@app.post("/api/v2/cases/{case_id}/analyze")
def api_analyze_case(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Analyze a specific case and return score + metrics"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    analysis = analyze_deal(case_id, db)
    
    if "error" in analysis:
        raise HTTPException(status_code=404, detail=analysis["error"])
    
    return analysis


@app.post("/api/v2/cases/bulk-analyze")
def api_bulk_analyze(
    request: Request,
    limit: int = None,
    db: Session = Depends(get_db)
):
    """Analyze multiple cases"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    results = bulk_analyze_cases(limit=limit, db=db)
    
    return {
        "total_analyzed": len(results),
        "results": results,
    }


@app.get("/api/v2/cases/top-deals")
def api_get_top_deals(
    request: Request,
    limit: int = 10,
    min_score: int = 60,
    db: Session = Depends(get_db)
):
    """Get top-scoring deals"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Analyze all active cases
    results = bulk_analyze_cases(db=db)
    
    # Filter by minimum score
    filtered = [r for r in results if r["score"] >= min_score]
    
    # Return top N
    top_deals = filtered[:limit]
    
    return {
        "total_deals": len(filtered),
        "top_deals": top_deals,
        "min_score": min_score,
    }


@app.get("/api/v2/analytics/deal-distribution")
def api_deal_distribution(
    request: Request,
    db: Session = Depends(get_db)
):
    """Get distribution of deal scores"""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
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
