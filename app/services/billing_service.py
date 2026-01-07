# app/services/billing_service.py
"""
Billing Service - V3 Phase 4
Handles invoice generation, billing calculations, and payment tracking
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, text

logger = logging.getLogger("pascowebapp.billing")


def generate_invoice_number(user_id: int, date: datetime) -> str:
    """
    Generate a unique invoice number.
    Format: INV-{YYYYMMDD}-{USER_ID}-{SEQUENCE}
    
    Args:
        user_id: User ID
        date: Invoice date
    
    Returns:
        Invoice number string
    """
    date_str = date.strftime("%Y%m%d")
    return f"INV-{date_str}-{user_id:05d}"


def get_billable_claims_for_date(
    db: Session,
    user_id: int,
    billing_date: datetime
) -> List:
    """
    Get all active claims for a user on a given date.
    
    Args:
        db: Database session
        user_id: User to bill
        billing_date: Date to bill for
    
    Returns:
        List of CaseClaim objects
    """
    from app.models import CaseClaim
    
    # Get claims that were active on the billing date
    # Claimed before or on billing date, and (not released OR released after billing date)
    start_of_day = billing_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = billing_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    claims = db.query(CaseClaim).filter(
        and_(
            CaseClaim.user_id == user_id,
            CaseClaim.claimed_at <= end_of_day,
            # Either still active OR released after the billing date
            (
                (CaseClaim.is_active == True) |
                (CaseClaim.released_at > start_of_day)
            )
        )
    ).all()
    
    return claims


def check_invoice_exists(
    db: Session,
    user_id: int,
    invoice_date: datetime
) -> bool:
    """
    Check if an invoice already exists for this user and date.
    
    Args:
        db: Database session
        user_id: User ID
        invoice_date: Date to check
    
    Returns:
        True if invoice exists
    """
    from app.models import Invoice
    
    start_of_day = invoice_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = invoice_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    existing = db.query(Invoice).filter(
        and_(
            Invoice.user_id == user_id,
            Invoice.invoice_date >= start_of_day,
            Invoice.invoice_date <= end_of_day
        )
    ).first()
    
    return existing is not None


def generate_daily_invoice(
    db: Session,
    user_id: int,
    billing_date: datetime = None,
    force: bool = False
) -> Tuple[bool, str, Optional[Dict]]:
    """
    Generate a daily invoice for a user.
    
    Args:
        db: Database session
        user_id: User to bill
        billing_date: Date to bill for (defaults to yesterday)
        force: Generate even if invoice exists
    
    Returns:
        Tuple of (success, message, invoice_data)
    """
    from app.models import User, Invoice, InvoiceLine, CaseClaim, Case
    
    if billing_date is None:
        billing_date = datetime.utcnow() - timedelta(days=1)
    
    # Check if user exists and is billable
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found", None
    
    if not getattr(user, 'is_billing_active', True):
        return False, "Billing is disabled for this user", None
    
    # Check for existing invoice (idempotency)
    if not force and check_invoice_exists(db, user_id, billing_date):
        return False, "Invoice already exists for this date", None
    
    # Get billable claims
    claims = get_billable_claims_for_date(db, user_id, billing_date)
    
    if not claims:
        return False, "No billable claims for this date", None
    
    try:
        # Create invoice
        invoice_number = generate_invoice_number(user_id, billing_date)
        
        invoice = Invoice(
            user_id=user_id,
            invoice_number=invoice_number,
            invoice_date=billing_date,
            due_date=billing_date + timedelta(days=30),
            status="pending",
            subtotal_cents=0,
            tax_cents=0,
            total_cents=0,
        )
        db.add(invoice)
        db.flush()  # Get invoice ID
        
        # Create line items for each claim
        total_cents = 0
        line_items = []
        
        for claim in claims:
            # Get case info
            case = db.query(Case).filter(Case.id == claim.case_id).first()
            case_number = case.case_number if case else f"Case #{claim.case_id}"
            address = (case.address_override or case.address or "Unknown") if case else "Unknown"
            
            # Calculate line amount (1 day at the frozen price)
            line_amount = claim.price_cents
            total_cents += line_amount
            
            line = InvoiceLine(
                invoice_id=invoice.id,
                claim_id=claim.id,
                case_id=claim.case_id,
                description=f"{case_number} - {address[:50]}",
                quantity=1,
                unit_price_cents=claim.price_cents,
                amount_cents=line_amount,
                case_number=case_number,
                score_at_invoice=claim.score_at_claim,
                service_date=billing_date,
            )
            db.add(line)
            line_items.append({
                "claim_id": claim.id,
                "case_id": claim.case_id,
                "case_number": case_number,
                "amount_cents": line_amount,
            })
        
        # Update invoice totals
        invoice.subtotal_cents = total_cents
        invoice.total_cents = total_cents  # No tax for now
        
        db.commit()
        
        logger.info(f"Generated invoice {invoice_number} for user {user_id}: ${total_cents/100:.2f}")
        
        return True, "Invoice generated successfully", {
            "invoice_id": invoice.id,
            "invoice_number": invoice_number,
            "total_cents": total_cents,
            "total_display": f"${total_cents/100:.2f}",
            "line_count": len(line_items),
            "lines": line_items,
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error generating invoice for user {user_id}: {e}")
        return False, f"Error generating invoice: {str(e)}", None


def generate_daily_invoices_for_all(
    db: Session,
    billing_date: datetime = None
) -> Dict:
    """
    Generate daily invoices for all users with active claims.
    
    Args:
        db: Database session
        billing_date: Date to bill for
    
    Returns:
        Summary of generation results
    """
    from app.models import User, CaseClaim
    
    if billing_date is None:
        billing_date = datetime.utcnow() - timedelta(days=1)
    
    # Get all users with active claims
    user_ids = db.query(CaseClaim.user_id).filter(
        CaseClaim.is_active == True
    ).distinct().all()
    
    user_ids = [uid[0] for uid in user_ids]
    
    results = {
        "billing_date": billing_date.isoformat(),
        "users_processed": 0,
        "invoices_generated": 0,
        "total_billed_cents": 0,
        "errors": [],
        "details": [],
    }
    
    for user_id in user_ids:
        results["users_processed"] += 1
        success, message, invoice_data = generate_daily_invoice(db, user_id, billing_date)
        
        if success and invoice_data:
            results["invoices_generated"] += 1
            results["total_billed_cents"] += invoice_data["total_cents"]
            results["details"].append({
                "user_id": user_id,
                "invoice_number": invoice_data["invoice_number"],
                "total_cents": invoice_data["total_cents"],
            })
        elif "already exists" not in message:
            results["errors"].append({
                "user_id": user_id,
                "error": message,
            })
    
    logger.info(
        f"Daily billing complete: {results['invoices_generated']} invoices, "
        f"${results['total_billed_cents']/100:.2f} total"
    )
    
    return results


def get_user_invoices(
    db: Session,
    user_id: int,
    status: str = None,
    limit: int = 50
) -> List:
    """
    Get invoices for a user.
    
    Args:
        db: Database session
        user_id: User ID
        status: Filter by status (pending, paid, failed)
        limit: Maximum number to return
    
    Returns:
        List of Invoice objects
    """
    from app.models import Invoice
    
    query = db.query(Invoice).filter(Invoice.user_id == user_id)
    
    if status:
        query = query.filter(Invoice.status == status)
    
    return query.order_by(Invoice.invoice_date.desc()).limit(limit).all()


def get_invoice_by_id(db: Session, invoice_id: int):
    """Get invoice by ID with line items."""
    from app.models import Invoice
    
    return db.query(Invoice).filter(Invoice.id == invoice_id).first()


def get_invoice_details(db: Session, invoice_id: int) -> Optional[Dict]:
    """
    Get full invoice details with line items.
    
    Args:
        db: Database session
        invoice_id: Invoice ID
    
    Returns:
        Dict with invoice details or None
    """
    from app.models import Invoice, InvoiceLine, User
    
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return None
    
    user = db.query(User).filter(User.id == invoice.user_id).first()
    
    lines = db.query(InvoiceLine).filter(
        InvoiceLine.invoice_id == invoice_id
    ).order_by(InvoiceLine.id).all()
    
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
        "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
        "status": invoice.status,
        "subtotal_cents": invoice.subtotal_cents,
        "tax_cents": invoice.tax_cents,
        "total_cents": invoice.total_cents,
        "subtotal_display": invoice.subtotal_display,
        "total_display": invoice.total_display,
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
        "stripe_invoice_id": invoice.stripe_invoice_id,
        "stripe_hosted_url": invoice.stripe_hosted_url,
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
        } if user else None,
        "lines": [
            {
                "id": line.id,
                "description": line.description,
                "case_number": line.case_number,
                "quantity": line.quantity,
                "unit_price_cents": line.unit_price_cents,
                "unit_price_display": line.unit_price_display,
                "amount_cents": line.amount_cents,
                "amount_display": line.amount_display,
                "score_at_invoice": line.score_at_invoice,
                "service_date": line.service_date.isoformat() if line.service_date else None,
            }
            for line in lines
        ],
        "line_count": len(lines),
    }


def mark_invoice_paid(
    db: Session,
    invoice_id: int,
    payment_intent: str = None
) -> Tuple[bool, str]:
    """
    Mark an invoice as paid.
    
    Args:
        db: Database session
        invoice_id: Invoice ID
        payment_intent: Stripe payment intent ID
    
    Returns:
        Tuple of (success, message)
    """
    from app.models import Invoice
    
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return False, "Invoice not found"
    
    if invoice.status == "paid":
        return False, "Invoice already paid"
    
    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    if payment_intent:
        invoice.stripe_payment_intent = payment_intent
    
    db.commit()
    
    logger.info(f"Invoice {invoice.invoice_number} marked as paid")
    
    return True, "Invoice marked as paid"


def mark_invoice_failed(
    db: Session,
    invoice_id: int,
    reason: str = None
) -> Tuple[bool, str]:
    """
    Mark an invoice as failed.
    
    Args:
        db: Database session
        invoice_id: Invoice ID
        reason: Failure reason
    
    Returns:
        Tuple of (success, message)
    """
    from app.models import Invoice
    
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return False, "Invoice not found"
    
    invoice.status = "failed"
    db.commit()
    
    logger.warning(f"Invoice {invoice.invoice_number} marked as failed: {reason}")
    
    return True, "Invoice marked as failed"


def get_billing_summary(db: Session) -> Dict:
    """
    Get overall billing summary for admin dashboard.
    
    Args:
        db: Database session
    
    Returns:
        Dict with billing statistics
    """
    from app.models import Invoice, InvoiceLine, CaseClaim
    
    # Total invoices by status
    invoice_counts = db.query(
        Invoice.status,
        func.count(Invoice.id),
        func.sum(Invoice.total_cents)
    ).group_by(Invoice.status).all()
    
    status_summary = {}
    total_billed = 0
    total_collected = 0
    
    for status, count, amount in invoice_counts:
        status_summary[status] = {
            "count": count,
            "amount_cents": amount or 0,
            "amount_display": f"${(amount or 0)/100:.2f}",
        }
        total_billed += amount or 0
        if status == "paid":
            total_collected += amount or 0
    
    # Active claims count
    active_claims = db.query(CaseClaim).filter(CaseClaim.is_active == True).count()
    
    # Total claims value
    daily_revenue = db.query(func.sum(CaseClaim.price_cents)).filter(
        CaseClaim.is_active == True
    ).scalar() or 0
    
    # Recent invoices
    recent_invoices = db.query(Invoice).order_by(
        Invoice.created_at.desc()
    ).limit(10).all()
    
    return {
        "status_summary": status_summary,
        "total_billed_cents": total_billed,
        "total_billed_display": f"${total_billed/100:.2f}",
        "total_collected_cents": total_collected,
        "total_collected_display": f"${total_collected/100:.2f}",
        "collection_rate": round((total_collected / total_billed * 100) if total_billed > 0 else 0, 1),
        "active_claims": active_claims,
        "estimated_daily_revenue_cents": daily_revenue,
        "estimated_daily_revenue_display": f"${daily_revenue/100:.2f}",
        "recent_invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "user_id": inv.user_id,
                "total_display": inv.total_display,
                "status": inv.status,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            }
            for inv in recent_invoices
        ],
    }
