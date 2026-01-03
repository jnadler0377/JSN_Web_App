# app/services/notification_service.py
"""
Notification Service - Real-time notification management
✅ Create, read, update, delete notifications
✅ Real-time broadcasting
✅ User-specific filtering
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine, SessionLocal

logger = logging.getLogger("pascowebapp.notifications")


# ========================================
# IN-MEMORY EVENT LISTENERS
# ========================================

# Store active SSE connections {user_id: [queue1, queue2, ...]}
_notification_listeners: Dict[int, List[Any]] = {}


def add_listener(user_id: int, queue: Any) -> None:
    """Add a listener queue for a user"""
    if user_id not in _notification_listeners:
        _notification_listeners[user_id] = []
    _notification_listeners[user_id].append(queue)
    logger.info(f"Added listener for user {user_id}. Total: {len(_notification_listeners[user_id])}")


def remove_listener(user_id: int, queue: Any) -> None:
    """Remove a listener queue for a user"""
    if user_id in _notification_listeners:
        try:
            _notification_listeners[user_id].remove(queue)
            if len(_notification_listeners[user_id]) == 0:
                del _notification_listeners[user_id]
            logger.info(f"Removed listener for user {user_id}")
        except ValueError:
            pass


async def broadcast_to_user(user_id: int, notification: Dict[str, Any]) -> None:
    """Broadcast notification to all active listeners for a user"""
    if user_id not in _notification_listeners:
        return
    
    dead_queues = []
    for queue in _notification_listeners[user_id]:
        try:
            await queue.put(notification)
        except Exception as e:
            logger.warning(f"Failed to broadcast to queue: {e}")
            dead_queues.append(queue)
    
    # Clean up dead queues
    for queue in dead_queues:
        remove_listener(user_id, queue)


# ========================================
# NOTIFICATION CRUD
# ========================================

def create_notification(
    user_id: Optional[int],
    title: str,
    message: str,
    notification_type: str = "info",
    link: Optional[str] = None,
    db: Optional[Session] = None
) -> Dict[str, Any]:
    """
    Create a new notification
    
    Args:
        user_id: User to notify (None for all users)
        title: Notification title
        message: Notification message
        notification_type: Type (info, success, warning, error)
        link: Optional link to navigate to
        db: Database session (optional)
    
    Returns:
        Created notification dict
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO notifications (user_id, title, message, type, link, is_read, created_at)
                    VALUES (:user_id, :title, :message, :type, :link, 0, :created_at)
                """),
                {
                    "user_id": user_id,
                    "title": title,
                    "message": message,
                    "type": notification_type,
                    "link": link,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            
            notification_id = result.lastrowid
        
        # Fetch the created notification
        notification = get_notification_by_id(notification_id, db)
        
        logger.info(f"Created notification {notification_id} for user {user_id}: {title}")
        
        return notification
    
    finally:
        if should_close:
            db.close()


def get_notification_by_id(notification_id: int, db: Optional[Session] = None) -> Optional[Dict[str, Any]]:
    """Get notification by ID"""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT id, user_id, title, message, type, link, is_read, created_at
                    FROM notifications
                    WHERE id = :id
                """),
                {"id": notification_id}
            ).mappings().fetchone()
        
        return dict(result) if result else None
    
    finally:
        if should_close:
            db.close()


def get_user_notifications(
    user_id: int,
    unread_only: bool = False,
    limit: int = 50,
    db: Optional[Session] = None
) -> List[Dict[str, Any]]:
    """
    Get notifications for a user
    
    Args:
        user_id: User ID
        unread_only: Only return unread notifications
        limit: Maximum number to return
        db: Database session (optional)
    
    Returns:
        List of notification dicts
    """
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        query = """
            SELECT id, user_id, title, message, type, link, is_read, created_at
            FROM notifications
            WHERE user_id = :user_id OR user_id IS NULL
        """
        
        if unread_only:
            query += " AND is_read = 0"
        
        query += " ORDER BY created_at DESC LIMIT :limit"
        
        with engine.connect() as conn:
            result = conn.execute(
                text(query),
                {"user_id": user_id, "limit": limit}
            ).mappings().fetchall()
        
        return [dict(row) for row in result]
    
    finally:
        if should_close:
            db.close()


def get_unread_count(user_id: int, db: Optional[Session] = None) -> int:
    """Get count of unread notifications for a user"""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT COUNT(*) as count
                    FROM notifications
                    WHERE (user_id = :user_id OR user_id IS NULL) AND is_read = 0
                """),
                {"user_id": user_id}
            ).fetchone()
        
        return int(result[0]) if result else 0
    
    finally:
        if should_close:
            db.close()


def mark_as_read(notification_id: int, db: Optional[Session] = None) -> bool:
    """Mark a notification as read"""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE notifications
                    SET is_read = 1
                    WHERE id = :id
                """),
                {"id": notification_id}
            )
        
        success = result.rowcount > 0
        
        if success:
            logger.info(f"Marked notification {notification_id} as read")
        
        return success
    
    finally:
        if should_close:
            db.close()


def mark_all_as_read(user_id: int, db: Optional[Session] = None) -> int:
    """Mark all notifications as read for a user"""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE notifications
                    SET is_read = 1
                    WHERE (user_id = :user_id OR user_id IS NULL) AND is_read = 0
                """),
                {"user_id": user_id}
            )
        
        count = result.rowcount
        
        logger.info(f"Marked {count} notifications as read for user {user_id}")
        
        return count
    
    finally:
        if should_close:
            db.close()


def delete_notification(notification_id: int, db: Optional[Session] = None) -> bool:
    """Delete a notification"""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True
    
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("DELETE FROM notifications WHERE id = :id"),
                {"id": notification_id}
            )
        
        success = result.rowcount > 0
        
        if success:
            logger.info(f"Deleted notification {notification_id}")
        
        return success
    
    finally:
        if should_close:
            db.close()


# ========================================
# NOTIFICATION TRIGGERS (Examples)
# ========================================

def notify_case_assigned(case_id: int, case_number: str, assigned_to: int) -> None:
    """Notify user when a case is assigned to them"""
    notification = create_notification(
        user_id=assigned_to,
        title="New Case Assigned",
        message=f"Case {case_number} has been assigned to you",
        notification_type="info",
        link=f"/cases/{case_id}"
    )
    
    # Broadcast in real-time
    import asyncio
    try:
        asyncio.create_task(broadcast_to_user(assigned_to, notification))
    except RuntimeError:
        # Not in async context, skip broadcast
        pass


def notify_status_changed(case_id: int, case_number: str, old_status: str, new_status: str, assigned_to: Optional[int] = None) -> None:
    """Notify user when case status changes"""
    if assigned_to:
        notification = create_notification(
            user_id=assigned_to,
            title="Case Status Updated",
            message=f"Case {case_number} status changed from {old_status} to {new_status}",
            notification_type="info",
            link=f"/cases/{case_id}"
        )
        
        # Broadcast in real-time
        import asyncio
        try:
            asyncio.create_task(broadcast_to_user(assigned_to, notification))
        except RuntimeError:
            pass


def notify_high_value_deal(case_id: int, case_number: str, score: int) -> None:
    """Notify all admins when a high-value deal is found"""
    # Get all admin users
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id FROM users WHERE is_admin = 1")
        ).fetchall()
        
        admin_ids = [row[0] for row in result]
    
    for admin_id in admin_ids:
        notification = create_notification(
            user_id=admin_id,
            title="🔥 High-Value Deal Alert",
            message=f"Case {case_number} scored {score}/100 - Check it out!",
            notification_type="success",
            link=f"/cases/{case_id}"
        )
        
        # Broadcast in real-time
        import asyncio
        try:
            asyncio.create_task(broadcast_to_user(admin_id, notification))
        except RuntimeError:
            pass


def notify_deal_analysis_complete(case_id: int, case_number: str, user_id: int) -> None:
    """Notify user when deal analysis is complete"""
    notification = create_notification(
        user_id=user_id,
        title="Deal Analysis Complete",
        message=f"Analysis for case {case_number} is ready",
        notification_type="success",
        link=f"/cases/{case_id}"
    )
    
    # Broadcast in real-time
    import asyncio
    try:
        asyncio.create_task(broadcast_to_user(user_id, notification))
    except RuntimeError:
        pass
