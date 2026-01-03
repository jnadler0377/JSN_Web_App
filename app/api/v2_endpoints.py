# app/api/v2_endpoints.py
"""
Version 2.0 API Endpoints
✅ Real-time Notifications
✅ Deal Analysis
✅ Enhanced Analytics
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from typing import Optional
import asyncio
import json

from app.database import get_db
from app.models import User, Case
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
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    current_user: User = Depends(lambda: {"id": 1})  # TODO: Replace with real auth
):
    """Get user's notifications"""
    notifications = get_user_notifications(
        user_id=current_user["id"],
        unread_only=unread_only,
        limit=limit
    )
    
    unread_count = get_unread_count(current_user["id"])
    
    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total": len(notifications),
    }


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(lambda: {"id": 1})
):
    """Mark a notification as read"""
    success = mark_as_read(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True, "message": "Notification marked as read"}


@router.post("/notifications/mark-all-read")
async def mark_all_notifications_read(
    current_user: User = Depends(lambda: {"id": 1})
):
    """Mark all notifications as read"""
    count = mark_all_as_read(current_user["id"])
    
    return {
        "success": True,
        "message": f"Marked {count} notifications as read",
        "count": count,
    }


@router.delete("/notifications/{notification_id}")
async def delete_notification_endpoint(
    notification_id: int,
    current_user: User = Depends(lambda: {"id": 1})
):
    """Delete a notification"""
    success = delete_notification(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True, "message": "Notification deleted"}


@router.get("/notifications/stream")
async def notification_stream(
    current_user: User = Depends(lambda: {"id": 1})
):
    """
    Server-Sent Events stream for real-time notifications
    
    Usage from frontend:
    const eventSource = new EventSource('/api/v2/notifications/stream');
    eventSource.onmessage = (event) => {
        const notification = JSON.parse(event.data);
        console.log('New notification:', notification);
    };
    """
    user_id = current_user["id"]
    
    async def event_generator():
        queue = asyncio.Queue()
        add_listener(user_id, queue)
        
        try:
            # Send initial heartbeat
            yield f"data: {json.dumps({'type': 'connected', 'user_id': user_id})}\n\n"
            
            while True:
                # Wait for notification or timeout every 30 seconds (heartbeat)
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(notification)}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        
        finally:
            remove_listener(user_id, queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


# ========================================
# DEAL ANALYSIS ENDPOINTS
# ========================================

@router.post("/cases/{case_id}/analyze")
async def analyze_case_endpoint(
    case_id: int,
    db = Depends(get_db)
):
    """
    Analyze a specific case
    
    Returns:
        Complete analysis with score, metrics, and recommendations
    """
    analysis = analyze_deal(case_id, db)
    
    if "error" in analysis:
        raise HTTPException(status_code=404, detail=analysis["error"])
    
    return analysis


@router.post("/cases/bulk-analyze")
async def bulk_analyze_endpoint(
    limit: Optional[int] = Query(None, description="Max cases to analyze"),
    db = Depends(get_db)
):
    """
    Analyze multiple cases
    
    Returns:
        List of analyses sorted by score (highest first)
    """
    results = bulk_analyze_cases(limit=limit, db=db)
    
    return {
        "total_analyzed": len(results),
        "results": results,
    }


@router.get("/cases/top-deals")
async def get_top_deals(
    limit: int = Query(10, le=50),
    min_score: int = Query(60, description="Minimum score threshold"),
    db = Depends(get_db)
):
    """
    Get top-scoring deals
    
    Returns:
        List of highest-scoring cases with analysis
    """
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


@router.get("/analytics/deal-distribution")
async def deal_score_distribution(db = Depends(get_db)):
    """
    Get distribution of deal scores
    
    Returns:
        Score distribution for charting
    """
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


# ========================================
# TESTING ENDPOINTS (Remove in production)
# ========================================

@router.post("/notifications/test")
async def create_test_notification(
    current_user: User = Depends(lambda: {"id": 1})
):
    """Create a test notification (for development)"""
    notification = create_notification(
        user_id=current_user["id"],
        title="Test Notification",
        message="This is a test notification from the API",
        notification_type="info",
        link="/cases"
    )
    
    return {
        "success": True,
        "notification": notification,
    }
