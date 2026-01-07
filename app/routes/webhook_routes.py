# app/routes/webhook_routes.py
"""
Webhook Routes - V3 Phase 5
Handles incoming webhooks from Stripe and other services.

SETUP:
1. In Stripe Dashboard, add webhook endpoint: https://yourdomain.com/webhooks/stripe
2. Select events: invoice.paid, invoice.payment_failed, customer.subscription.deleted
3. Copy the webhook signing secret to your .env as STRIPE_WEBHOOK_SECRET
"""

import logging
import json
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy.orm import Session

from app.database import SessionLocal

logger = logging.getLogger("pascowebapp.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Try to import stripe
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    stripe = None


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="Stripe-Signature"),
):
    """
    Handle Stripe webhook events.
    
    Stripe sends various events like:
    - invoice.paid: Invoice was successfully paid
    - invoice.payment_failed: Payment attempt failed
    - invoice.created: New invoice created
    - customer.subscription.deleted: Subscription cancelled
    - payment_method.attached: New payment method added
    """
    from app.config import settings
    
    # Get raw body
    payload = await request.body()
    
    # Get webhook secret from settings
    webhook_secret = getattr(settings, 'stripe_webhook_secret', None)
    
    event = None
    
    if webhook_secret and STRIPE_AVAILABLE and stripe_signature:
        # Verify webhook signature (production mode)
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, webhook_secret
            )
        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            raise HTTPException(status_code=400, detail="Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        # Development mode - parse without verification
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")
        
        logger.warning("Processing webhook without signature verification (dev mode)")
    
    if not event:
        raise HTTPException(status_code=400, detail="Could not parse event")
    
    # Extract event details
    event_type = event.get("type", "")
    event_id = event.get("id", "unknown")
    data = event.get("data", {}).get("object", {})
    
    logger.info(f"Received Stripe webhook: {event_type} ({event_id})")
    
    # Get database session
    db = SessionLocal()
    
    try:
        # Route to appropriate handler
        if event_type == "invoice.paid":
            result = handle_invoice_paid(db, data)
        
        elif event_type == "invoice.payment_failed":
            result = handle_invoice_payment_failed(db, data)
        
        elif event_type == "invoice.created":
            result = handle_invoice_created(db, data)
        
        elif event_type == "invoice.finalized":
            result = handle_invoice_finalized(db, data)
        
        elif event_type == "customer.subscription.deleted":
            result = handle_subscription_deleted(db, data)
        
        elif event_type == "payment_method.attached":
            result = handle_payment_method_attached(db, data)
        
        elif event_type == "customer.created":
            result = handle_customer_created(db, data)
        
        elif event_type == "charge.succeeded":
            result = handle_charge_succeeded(db, data)
        
        elif event_type == "charge.failed":
            result = handle_charge_failed(db, data)
        
        else:
            # Log unhandled events but don't error
            logger.info(f"Unhandled webhook event type: {event_type}")
            result = {"status": "ignored", "event_type": event_type}
        
        # Log audit entry
        log_webhook_event(db, event_type, event_id, result)
        
        return {"status": "ok", "event_id": event_id, "result": result}
        
    except Exception as e:
        logger.exception(f"Error processing webhook {event_type}: {e}")
        # Don't raise - we want to return 200 to Stripe
        return {"status": "error", "event_id": event_id, "error": str(e)}
    
    finally:
        db.close()


# ============================================================
# Event Handlers
# ============================================================

def handle_invoice_paid(db: Session, data: dict) -> dict:
    """Handle invoice.paid event."""
    from app.services.stripe_service import handle_invoice_paid as stripe_handle_paid
    
    stripe_invoice_id = data.get("id")
    if not stripe_invoice_id:
        return {"status": "error", "message": "No invoice ID"}
    
    success = stripe_handle_paid(db, stripe_invoice_id)
    
    return {
        "status": "success" if success else "not_found",
        "stripe_invoice_id": stripe_invoice_id,
    }


def handle_invoice_payment_failed(db: Session, data: dict) -> dict:
    """Handle invoice.payment_failed event."""
    from app.services.stripe_service import handle_invoice_payment_failed as stripe_handle_failed
    
    stripe_invoice_id = data.get("id")
    if not stripe_invoice_id:
        return {"status": "error", "message": "No invoice ID"}
    
    success = stripe_handle_failed(db, stripe_invoice_id)
    
    # Optionally: Send notification to user about failed payment
    if success:
        try:
            from app.models import Invoice
            invoice = db.query(Invoice).filter(
                Invoice.stripe_invoice_id == stripe_invoice_id
            ).first()
            
            if invoice:
                # Could trigger email notification here
                logger.warning(f"Payment failed for user {invoice.user_id}, invoice {invoice.invoice_number}")
        except Exception as e:
            logger.error(f"Error sending payment failure notification: {e}")
    
    return {
        "status": "success" if success else "not_found",
        "stripe_invoice_id": stripe_invoice_id,
    }


def handle_invoice_created(db: Session, data: dict) -> dict:
    """Handle invoice.created event."""
    stripe_invoice_id = data.get("id")
    customer_id = data.get("customer")
    amount_due = data.get("amount_due", 0)
    
    logger.info(f"Stripe invoice created: {stripe_invoice_id} for customer {customer_id}, amount: ${amount_due/100:.2f}")
    
    return {
        "status": "logged",
        "stripe_invoice_id": stripe_invoice_id,
    }


def handle_invoice_finalized(db: Session, data: dict) -> dict:
    """Handle invoice.finalized event."""
    from app.models import Invoice
    
    stripe_invoice_id = data.get("id")
    hosted_url = data.get("hosted_invoice_url")
    
    if stripe_invoice_id and hosted_url:
        invoice = db.query(Invoice).filter(
            Invoice.stripe_invoice_id == stripe_invoice_id
        ).first()
        
        if invoice:
            invoice.stripe_hosted_url = hosted_url
            db.commit()
            logger.info(f"Updated hosted URL for invoice {invoice.invoice_number}")
    
    return {
        "status": "success",
        "stripe_invoice_id": stripe_invoice_id,
    }


def handle_subscription_deleted(db: Session, data: dict) -> dict:
    """Handle customer.subscription.deleted event."""
    from app.services.stripe_service import handle_customer_subscription_deleted
    
    customer_id = data.get("customer")
    if not customer_id:
        return {"status": "error", "message": "No customer ID"}
    
    success = handle_customer_subscription_deleted(db, customer_id)
    
    return {
        "status": "success" if success else "not_found",
        "customer_id": customer_id,
    }


def handle_payment_method_attached(db: Session, data: dict) -> dict:
    """Handle payment_method.attached event."""
    customer_id = data.get("customer")
    payment_method_id = data.get("id")
    
    logger.info(f"Payment method {payment_method_id} attached to customer {customer_id}")
    
    return {
        "status": "logged",
        "customer_id": customer_id,
        "payment_method_id": payment_method_id,
    }


def handle_customer_created(db: Session, data: dict) -> dict:
    """Handle customer.created event."""
    customer_id = data.get("id")
    email = data.get("email")
    
    logger.info(f"Stripe customer created: {customer_id} ({email})")
    
    return {
        "status": "logged",
        "customer_id": customer_id,
    }


def handle_charge_succeeded(db: Session, data: dict) -> dict:
    """Handle charge.succeeded event."""
    charge_id = data.get("id")
    amount = data.get("amount", 0)
    customer_id = data.get("customer")
    
    logger.info(f"Charge succeeded: {charge_id} for ${amount/100:.2f}")
    
    return {
        "status": "logged",
        "charge_id": charge_id,
        "amount_cents": amount,
    }


def handle_charge_failed(db: Session, data: dict) -> dict:
    """Handle charge.failed event."""
    charge_id = data.get("id")
    failure_code = data.get("failure_code")
    failure_message = data.get("failure_message")
    customer_id = data.get("customer")
    
    logger.warning(f"Charge failed: {charge_id} - {failure_code}: {failure_message}")
    
    return {
        "status": "logged",
        "charge_id": charge_id,
        "failure_code": failure_code,
    }


# ============================================================
# Audit Logging
# ============================================================

def log_webhook_event(db: Session, event_type: str, event_id: str, result: dict):
    """
    Log webhook event for audit purposes.
    
    This creates a record in webhook_logs table (if it exists).
    """
    try:
        from sqlalchemy import text
        
        # Try to insert into webhook_logs (table may not exist yet)
        db.execute(
            text("""
                INSERT INTO webhook_logs (event_type, event_id, result, created_at)
                VALUES (:event_type, :event_id, :result, :created_at)
            """),
            {
                "event_type": event_type,
                "event_id": event_id,
                "result": json.dumps(result),
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        db.commit()
    except Exception:
        # Table might not exist - that's OK
        pass


# ============================================================
# Test Endpoint
# ============================================================

@router.post("/stripe/test")
async def stripe_webhook_test(request: Request):
    """
    Test endpoint for webhook development.
    
    Accepts any payload and logs it without verification.
    Only available in development mode.
    """
    from app.config import settings
    
    # Only allow in development
    if getattr(settings, 'environment', 'development') == 'production':
        raise HTTPException(status_code=404, detail="Not found")
    
    payload = await request.body()
    
    try:
        data = json.loads(payload)
        logger.info(f"Test webhook received: {json.dumps(data, indent=2)[:500]}")
        return {"status": "test_received", "data_preview": str(data)[:200]}
    except json.JSONDecodeError:
        return {"status": "invalid_json"}
