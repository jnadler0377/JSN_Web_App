# app/routes/notification_routes.py
"""
Notification Routes
- Get notifications
- Mark read/unread
- Delete notifications
- Real-time SSE stream
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user

# Try to import notification service (may not exist)
try:
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
    NOTIFICATION_SERVICE_AVAILABLE = True
except ImportError:
    NOTIFICATION_SERVICE_AVAILABLE = False

logger = logging.getLogger("pascowebapp.notifications")

router = APIRouter(prefix="/api/v2", tags=["notifications"])


@router.get("/notifications")
def api_get_notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get user's notifications"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not NOTIFICATION_SERVICE_AVAILABLE:
        return {"notifications": [], "unread_count": 0, "total": 0}
    
    notifications = get_user_notifications(
        user_id=user.get("id") or getattr(user, "id", 0),
        unread_only=unread_only,
        limit=limit
    )
    
    unread_count = get_unread_count(user.get("id") or getattr(user, "id", 0))
    
    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total": len(notifications),
    }


@router.post("/notifications/{notification_id}/read")
def api_mark_notification_read(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Mark a notification as read"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not NOTIFICATION_SERVICE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Notification service not available")
    
    success = mark_as_read(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True}


@router.post("/notifications/mark-all-read")
def api_mark_all_read(
    request: Request,
    db: Session = Depends(get_db)
):
    """Mark all notifications as read"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not NOTIFICATION_SERVICE_AVAILABLE:
        return {"success": True, "count": 0}
    
    user_id = user.get("id") or getattr(user, "id", 0)
    count = mark_all_as_read(user_id)
    
    return {"success": True, "count": count}


@router.delete("/notifications/{notification_id}")
def api_delete_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Delete a notification"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not NOTIFICATION_SERVICE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Notification service not available")
    
    success = delete_notification(notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"success": True}


@router.get("/notifications/stream")
async def notification_stream(request: Request, db: Session = Depends(get_db)):
    """Server-Sent Events stream for real-time notifications"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not NOTIFICATION_SERVICE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Notification service not available")
    
    user_id = user.get("id") or getattr(user, "id", 0)
    
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


@router.post("/notifications/test")
def api_create_test_notification(
    request: Request,
    db: Session = Depends(get_db)
):
    """Create a test notification (for development)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not NOTIFICATION_SERVICE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Notification service not available")
    
    user_id = user.get("id") or getattr(user, "id", 0)
    notification = create_notification(
        user_id=user_id,
        title="Test Notification",
        message="This is a test notification from the API",
        notification_type="info",
        link="/cases"
    )
    
    return {"success": True, "notification": notification}
