# app/jobs/daily_billing_job.py
"""
Daily Billing Job - V3 Phase 5
Automated job for generating and sending invoices.

USAGE:
1. Manual execution:
   python -m app.jobs.daily_billing_job

2. As a cron job (run daily at midnight):
   0 0 * * * cd /path/to/app && python -m app.jobs.daily_billing_job

3. Using APScheduler (in your FastAPI app):
   from apscheduler.schedulers.asyncio import AsyncIOScheduler
   scheduler = AsyncIOScheduler()
   scheduler.add_job(run_daily_billing, 'cron', hour=0, minute=0)
   scheduler.start()

4. Using Celery:
   @celery.task
   def daily_billing_task():
       run_daily_billing()
"""

import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("pascowebapp.billing_job")


def run_daily_billing(
    billing_date: datetime = None,
    sync_to_stripe: bool = True,
    dry_run: bool = False
) -> Dict:
    """
    Run the daily billing job.
    
    This function:
    1. Generates invoices for all users with active claims
    2. Syncs invoices to Stripe (if enabled)
    3. Returns a summary of the billing run
    
    Args:
        billing_date: Date to bill for (defaults to yesterday)
        sync_to_stripe: Whether to create Stripe invoices
        dry_run: If True, don't actually create invoices
    
    Returns:
        Dict with billing run results
    """
    from app.database import SessionLocal
    from app.services.billing_service import generate_daily_invoices_for_all
    
    if billing_date is None:
        billing_date = datetime.utcnow() - timedelta(days=1)
    
    logger.info("=" * 60)
    logger.info(f"Starting daily billing job for {billing_date.strftime('%Y-%m-%d')}")
    logger.info(f"Stripe sync: {sync_to_stripe}, Dry run: {dry_run}")
    logger.info("=" * 60)
    
    results = {
        "billing_date": billing_date.isoformat(),
        "started_at": datetime.utcnow().isoformat(),
        "dry_run": dry_run,
        "invoices_generated": 0,
        "invoices_synced_to_stripe": 0,
        "total_billed_cents": 0,
        "errors": [],
        "details": [],
    }
    
    if dry_run:
        logger.info("DRY RUN - No invoices will be created")
        # In dry run, just count what would be generated
        db = SessionLocal()
        try:
            from app.models import CaseClaim
            from app.services.billing_service import get_billable_claims_for_date
            
            # Get all users with active claims
            user_ids = db.query(CaseClaim.user_id).filter(
                CaseClaim.is_active == True
            ).distinct().all()
            
            for (user_id,) in user_ids:
                claims = get_billable_claims_for_date(db, user_id, billing_date)
                if claims:
                    total = sum(c.price_cents for c in claims)
                    results["invoices_generated"] += 1
                    results["total_billed_cents"] += total
                    results["details"].append({
                        "user_id": user_id,
                        "claims": len(claims),
                        "total_cents": total,
                    })
                    logger.info(f"  Would invoice user {user_id}: {len(claims)} claims, ${total/100:.2f}")
            
            logger.info(f"DRY RUN complete: Would generate {results['invoices_generated']} invoices")
            
        finally:
            db.close()
        
        results["completed_at"] = datetime.utcnow().isoformat()
        return results
    
    # Actual billing run
    db = SessionLocal()
    try:
        # Step 1: Generate local invoices
        logger.info("Step 1: Generating local invoices...")
        billing_results = generate_daily_invoices_for_all(db, billing_date)
        
        results["invoices_generated"] = billing_results["invoices_generated"]
        results["total_billed_cents"] = billing_results["total_billed_cents"]
        results["errors"].extend(billing_results.get("errors", []))
        
        logger.info(f"  Generated {results['invoices_generated']} invoices")
        logger.info(f"  Total billed: ${results['total_billed_cents']/100:.2f}")
        
        # Step 2: Sync to Stripe (if enabled)
        if sync_to_stripe and results["invoices_generated"] > 0:
            logger.info("Step 2: Syncing to Stripe...")
            stripe_results = sync_invoices_to_stripe(db, billing_results.get("details", []))
            
            results["invoices_synced_to_stripe"] = stripe_results["synced"]
            results["errors"].extend(stripe_results.get("errors", []))
            
            logger.info(f"  Synced {results['invoices_synced_to_stripe']} invoices to Stripe")
        
        # Step 3: Handle overdue accounts
        logger.info("Step 3: Checking overdue accounts...")
        overdue_results = handle_overdue_accounts(db)
        results["overdue_accounts"] = overdue_results
        
        logger.info(f"  Found {overdue_results.get('count', 0)} overdue accounts")
        
    except Exception as e:
        logger.exception(f"Billing job failed: {e}")
        results["errors"].append({"error": str(e), "type": "job_failure"})
    
    finally:
        db.close()
    
    results["completed_at"] = datetime.utcnow().isoformat()
    
    # Summary
    logger.info("=" * 60)
    logger.info("Daily billing job complete")
    logger.info(f"  Invoices generated: {results['invoices_generated']}")
    logger.info(f"  Invoices synced to Stripe: {results['invoices_synced_to_stripe']}")
    logger.info(f"  Total billed: ${results['total_billed_cents']/100:.2f}")
    logger.info(f"  Errors: {len(results['errors'])}")
    logger.info("=" * 60)
    
    return results


def sync_invoices_to_stripe(db, invoice_details: List[Dict]) -> Dict:
    """
    Sync generated invoices to Stripe.
    
    Args:
        db: Database session
        invoice_details: List of invoice details from billing service
    
    Returns:
        Dict with sync results
    """
    from app.services.stripe_service import create_stripe_invoice, is_stripe_configured
    
    results = {
        "synced": 0,
        "skipped": 0,
        "errors": [],
    }
    
    if not is_stripe_configured():
        logger.warning("Stripe not configured - skipping sync")
        results["skipped"] = len(invoice_details)
        return results
    
    from app.models import Invoice
    
    for detail in invoice_details:
        invoice_id = detail.get("invoice_id")
        if not invoice_id:
            continue
        
        # Get the invoice
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            continue
        
        # Skip if already synced
        if invoice.stripe_invoice_id:
            results["skipped"] += 1
            continue
        
        # Create Stripe invoice
        success, result = create_stripe_invoice(db, invoice_id)
        
        if success:
            results["synced"] += 1
            logger.info(f"  Synced invoice {invoice.invoice_number} to Stripe: {result}")
        else:
            results["errors"].append({
                "invoice_id": invoice_id,
                "invoice_number": invoice.invoice_number,
                "error": result,
            })
            logger.error(f"  Failed to sync invoice {invoice.invoice_number}: {result}")
    
    return results


def handle_overdue_accounts(db, grace_days: int = 7) -> Dict:
    """
    Handle accounts with overdue invoices.
    
    Actions:
    - Flag accounts with invoices overdue by more than grace_days
    - Optionally: Lock claims, send notifications, etc.
    
    Args:
        db: Database session
        grace_days: Days after due date before taking action
    
    Returns:
        Dict with overdue account info
    """
    from app.models import Invoice, User, CaseClaim
    from sqlalchemy import and_
    
    cutoff_date = datetime.utcnow() - timedelta(days=grace_days)
    
    # Find users with overdue unpaid invoices
    overdue_invoices = db.query(Invoice).filter(
        and_(
            Invoice.status.in_(["pending", "failed"]),
            Invoice.due_date < cutoff_date,
        )
    ).all()
    
    overdue_users = set()
    total_overdue_cents = 0
    
    for invoice in overdue_invoices:
        overdue_users.add(invoice.user_id)
        total_overdue_cents += invoice.total_cents
    
    results = {
        "count": len(overdue_users),
        "total_overdue_cents": total_overdue_cents,
        "total_overdue": f"${total_overdue_cents/100:.2f}",
        "user_ids": list(overdue_users),
    }
    
    # Optional: Take action on overdue accounts
    # Uncomment to enable automatic lockout
    """
    for user_id in overdue_users:
        # Mark user's billing as inactive
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_billing_active = False
            logger.warning(f"Deactivated billing for user {user_id} due to overdue invoices")
        
        # Optionally: Release their claims
        # claims = db.query(CaseClaim).filter(
        #     CaseClaim.user_id == user_id,
        #     CaseClaim.is_active == True
        # ).all()
        # for claim in claims:
        #     claim.is_active = False
        #     claim.released_at = datetime.utcnow()
    
    db.commit()
    """
    
    return results


def run_backfill_billing(
    start_date: datetime,
    end_date: datetime = None,
    sync_to_stripe: bool = False
) -> List[Dict]:
    """
    Backfill billing for a date range.
    
    Useful for catching up after system was down or for initial setup.
    
    Args:
        start_date: Start of date range
        end_date: End of date range (defaults to yesterday)
        sync_to_stripe: Whether to sync to Stripe
    
    Returns:
        List of results for each day
    """
    if end_date is None:
        end_date = datetime.utcnow() - timedelta(days=1)
    
    logger.info(f"Backfilling billing from {start_date.date()} to {end_date.date()}")
    
    results = []
    current_date = start_date
    
    while current_date <= end_date:
        logger.info(f"Processing {current_date.date()}...")
        
        day_result = run_daily_billing(
            billing_date=current_date,
            sync_to_stripe=sync_to_stripe,
        )
        results.append(day_result)
        
        current_date += timedelta(days=1)
    
    logger.info(f"Backfill complete: processed {len(results)} days")
    return results


# ============================================================
# CLI Interface
# ============================================================

def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Daily Billing Job")
    parser.add_argument(
        "--date",
        type=str,
        help="Billing date (YYYY-MM-DD), defaults to yesterday"
    )
    parser.add_argument(
        "--no-stripe",
        action="store_true",
        help="Skip Stripe sync"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually create invoices"
    )
    parser.add_argument(
        "--backfill-from",
        type=str,
        help="Backfill from date (YYYY-MM-DD)"
    )
    
    args = parser.parse_args()
    
    # Parse dates
    billing_date = None
    if args.date:
        billing_date = datetime.strptime(args.date, "%Y-%m-%d")
    
    # Run backfill if requested
    if args.backfill_from:
        start_date = datetime.strptime(args.backfill_from, "%Y-%m-%d")
        end_date = billing_date or (datetime.utcnow() - timedelta(days=1))
        
        results = run_backfill_billing(
            start_date=start_date,
            end_date=end_date,
            sync_to_stripe=not args.no_stripe,
        )
        
        # Print summary
        total_invoices = sum(r["invoices_generated"] for r in results)
        total_amount = sum(r["total_billed_cents"] for r in results)
        print(f"\nBackfill complete:")
        print(f"  Days processed: {len(results)}")
        print(f"  Total invoices: {total_invoices}")
        print(f"  Total amount: ${total_amount/100:.2f}")
        
    else:
        # Run single day
        results = run_daily_billing(
            billing_date=billing_date,
            sync_to_stripe=not args.no_stripe,
            dry_run=args.dry_run,
        )
        
        # Print summary
        print(f"\nBilling job complete:")
        print(f"  Invoices generated: {results['invoices_generated']}")
        print(f"  Total billed: ${results['total_billed_cents']/100:.2f}")
        if results['errors']:
            print(f"  Errors: {len(results['errors'])}")


if __name__ == "__main__":
    # Add parent directory to path for imports
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    
    main()
