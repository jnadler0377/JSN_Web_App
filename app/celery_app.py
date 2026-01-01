# app/celery_app.py
from __future__ import annotations

import logging
from typing import List
from celery import Celery
from celery.schedules import crontab

from app.config import settings

logger = logging.getLogger("pascowebapp.celery")

# Initialize Celery
celery_app = Celery(
    "foreclosure_manager",
    broker=settings.celery_broker_url or "redis://localhost:6379/1",
    backend=settings.celery_result_backend or "redis://localhost:6379/2",
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/New_York",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
)

# Periodic tasks schedule
celery_app.conf.beat_schedule = {
    "daily-scraper": {
        "task": "app.celery_app.run_daily_scraper",
        "schedule": crontab(hour=2, minute=0),  # 2 AM daily
    },
    "cleanup-expired-sessions": {
        "task": "app.celery_app.cleanup_expired_sessions",
        "schedule": crontab(hour=3, minute=0),  # 3 AM daily
    },
    "check-upcoming-auctions": {
        "task": "app.celery_app.check_upcoming_auctions",
        "schedule": crontab(hour=8, minute=0),  # 8 AM daily
    },
}


# ========================================
# Task Definitions
# ========================================

@celery_app.task(bind=True, name="app.celery_app.run_scraper")
def run_scraper(self, since_days: int = 7, run_pasco: bool = True, run_pinellas: bool = True):
    """
    Background task to run the foreclosure scraper
    """
    from app.services.update_cases_service import run_update_cases_job
    import asyncio
    
    job_id = self.request.id
    logger.info(f"Starting scraper task {job_id}: since_days={since_days}")
    
    try:
        # Run async job in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            run_update_cases_job(job_id, since_days, run_pasco=run_pasco, run_pinellas=run_pinellas)
        )
        loop.close()
        
        logger.info(f"Scraper task {job_id} completed successfully")
        return {"status": "success", "job_id": job_id}
    
    except Exception as exc:
        logger.error(f"Scraper task {job_id} failed: {exc}", exc_info=True)
        raise


@celery_app.task(bind=True, name="app.celery_app.bulk_skip_trace")
def bulk_skip_trace(self, case_ids: List[int]):
    """
    Background task for bulk skip tracing
    """
    from app.database import SessionLocal
    from app.services.skiptrace_service import (
        get_case_address_components,
        batchdata_skip_trace,
        save_skiptrace_row,
    )
    
    job_id = self.request.id
    logger.info(f"Starting bulk skip trace {job_id}: {len(case_ids)} cases")
    
    db = SessionLocal()
    success_count = 0
    error_count = 0
    
    try:
        for idx, case_id in enumerate(case_ids):
            try:
                # Update task progress
                self.update_state(
                    state="PROGRESS",
                    meta={"current": idx + 1, "total": len(case_ids), "status": f"Processing case {case_id}"}
                )
                
                case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
                if not case:
                    logger.warning(f"Case {case_id} not found")
                    error_count += 1
                    continue
                
                street, city, state, postal = get_case_address_components(case)
                skip_data = batchdata_skip_trace(street, city, state, postal)
                save_skiptrace_row(case.id, skip_data)
                
                success_count += 1
                logger.info(f"Skip traced case {case_id} ({idx + 1}/{len(case_ids)})")
                
            except Exception as exc:
                logger.error(f"Failed to skip trace case {case_id}: {exc}")
                error_count += 1
        
        return {
            "status": "completed",
            "success": success_count,
            "errors": error_count,
            "total": len(case_ids),
        }
    
    finally:
        db.close()


@celery_app.task(bind=True, name="app.celery_app.bulk_property_lookup")
def bulk_property_lookup(self, case_ids: List[int]):
    """
    Background task for bulk property data lookup
    """
    from app.database import SessionLocal
    from app.services.skiptrace_service import (
        get_case_address_components,
        batchdata_property_lookup_all_attributes,
        save_property_for_case,
    )
    
    job_id = self.request.id
    logger.info(f"Starting bulk property lookup {job_id}: {len(case_ids)} cases")
    
    db = SessionLocal()
    success_count = 0
    error_count = 0
    
    try:
        for idx, case_id in enumerate(case_ids):
            try:
                self.update_state(
                    state="PROGRESS",
                    meta={"current": idx + 1, "total": len(case_ids)}
                )
                
                case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
                if not case:
                    error_count += 1
                    continue
                
                street, city, state, postal = get_case_address_components(case)
                prop_data = batchdata_property_lookup_all_attributes(street, city, state, postal)
                save_property_for_case(case.id, prop_data)
                
                success_count += 1
                
            except Exception as exc:
                logger.error(f"Failed property lookup for case {case_id}: {exc}")
                error_count += 1
        
        return {"status": "completed", "success": success_count, "errors": error_count}
    
    finally:
        db.close()


@celery_app.task(name="app.celery_app.run_daily_scraper")
def run_daily_scraper():
    """Scheduled daily scraper run"""
    return run_scraper.delay(since_days=1, run_pasco=True, run_pinellas=True)


@celery_app.task(name="app.celery_app.cleanup_expired_sessions")
def cleanup_expired_sessions():
    """Clean up expired user sessions"""
    from app.database import SessionLocal, engine
    from datetime import datetime
    
    db = SessionLocal()
    try:
        now = datetime.utcnow().isoformat()
        result = db.execute(
            "DELETE FROM sessions WHERE expires_at < :now",
            {"now": now}
        )
        db.commit()
        deleted = result.rowcount
        logger.info(f"Cleaned up {deleted} expired sessions")
        return {"deleted": deleted}
    finally:
        db.close()


@celery_app.task(name="app.celery_app.check_upcoming_auctions")
def check_upcoming_auctions():
    """
    Check for upcoming auction dates and send notifications
    This is a placeholder - implement notification logic as needed
    """
    from app.database import SessionLocal
    from datetime import datetime, timedelta
    
    db = SessionLocal()
    try:
        # Find cases with auctions in next 7 days
        # Note: You'd need to add auction_date field to cases or property table
        logger.info("Checking for upcoming auctions")
        
        # Placeholder logic - implement based on your data model
        # cases_with_auctions = db.query(Case).filter(
        #     Case.auction_date.between(datetime.now(), datetime.now() + timedelta(days=7))
        # ).all()
        
        return {"status": "checked"}
    
    finally:
        db.close()


@celery_app.task(bind=True, name="app.celery_app.process_document_ocr")
def process_document_ocr(self, case_id: int, document_path: str, document_type: str):
    """
    Background task for OCR processing of uploaded documents
    """
    from app.services.ocr_service import extract_document_data
    
    job_id = self.request.id
    logger.info(f"Starting OCR for case {case_id}, document: {document_path}")
    
    try:
        result = extract_document_data(document_path, document_type)
        
        # Save extracted data to database
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(
                """
                INSERT INTO document_metadata 
                (case_id, document_type, file_path, ocr_completed, ocr_text, extracted_data_json, uploaded_at)
                VALUES (:case_id, :doc_type, :path, 1, :text, :data, datetime('now'))
                """,
                {
                    "case_id": case_id,
                    "doc_type": document_type,
                    "path": document_path,
                    "text": result.get("full_text", ""),
                    "data": json.dumps(result.get("structured_data", {})),
                }
            )
            db.commit()
        finally:
            db.close()
        
        return {"status": "success", "extracted_fields": len(result.get("structured_data", {}))}
    
    except Exception as exc:
        logger.error(f"OCR failed for {document_path}: {exc}", exc_info=True)
        raise


# ========================================
# Helper function to check Celery availability
# ========================================

def is_celery_available() -> bool:
    """Check if Celery broker is available"""
    if not settings.is_celery_enabled:
        return False
    
    try:
        celery_app.control.inspect().ping(timeout=1.0)
        return True
    except Exception:
        return False
