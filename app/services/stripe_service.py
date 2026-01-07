# app/services/stripe_service.py
"""
Stripe Service - V3 Phase 5
Handles Stripe customer creation, invoice syncing, and payment processing.

SETUP INSTRUCTIONS:
1. Install stripe: pip install stripe
2. Add to .env:
   - STRIPE_SECRET_KEY=sk_test_...
   - STRIPE_PUBLISHABLE_KEY=pk_test_...
   - STRIPE_WEBHOOK_SECRET=whsec_...
3. Set up webhook endpoint in Stripe Dashboard pointing to /webhooks/stripe
"""

import logging
from typing import Optional, Tuple, Dict, List
from datetime import datetime

logger = logging.getLogger("pascowebapp.stripe")

# Stripe import with graceful fallback
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    stripe = None
    logger.warning("Stripe library not installed. Run: pip install stripe")


def init_stripe(api_key: str):
    """Initialize Stripe with API key."""
    if not STRIPE_AVAILABLE:
        logger.error("Stripe library not available - run: pip install stripe")
        return False
    
    if not api_key:
        logger.warning("Stripe API key not provided")
        return False
    
    if not api_key.startswith('sk_'):
        logger.warning(f"Stripe API key appears invalid (should start with 'sk_')")
        return False
    
    stripe.api_key = api_key
    logger.info(f"Stripe initialized with key: {api_key[:12]}...")
    return True


def is_stripe_configured() -> bool:
    """Check if Stripe is properly configured."""
    if not STRIPE_AVAILABLE:
        return False
    # Check that api_key is set and looks like a valid key
    return bool(stripe.api_key and stripe.api_key.startswith('sk_'))


# ============================================================
# Customer Management
# ============================================================

def create_stripe_customer(
    email: str,
    name: str = None,
    user_id: int = None,
    metadata: dict = None
) -> Tuple[bool, str]:
    """
    Create a Stripe customer.
    
    Args:
        email: Customer email
        name: Customer name
        user_id: Local user ID for reference
        metadata: Additional metadata
    
    Returns:
        Tuple of (success, customer_id or error message)
    """
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        customer_metadata = metadata or {}
        if user_id:
            customer_metadata["user_id"] = str(user_id)
        
        customer = stripe.Customer.create(
            email=email,
            name=name,
            metadata=customer_metadata,
        )
        
        logger.info(f"Created Stripe customer {customer.id} for {email}")
        return True, customer.id
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating customer: {e}")
        return False, str(e)
    except Exception as e:
        logger.error(f"Error creating Stripe customer: {e}")
        return False, str(e)


def get_or_create_customer(
    db,
    user_id: int
) -> Tuple[bool, str]:
    """
    Get existing Stripe customer or create new one.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Tuple of (success, customer_id or error)
    """
    from app.models import User
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found"
    
    # Return existing customer ID if available
    if user.stripe_customer_id:
        return True, user.stripe_customer_id
    
    # Create new customer
    success, result = create_stripe_customer(
        email=user.email,
        name=user.full_name or user.username,
        user_id=user.id,
    )
    
    if success:
        user.stripe_customer_id = result
        db.commit()
        return True, result
    
    return False, result


def update_stripe_customer(
    customer_id: str,
    email: str = None,
    name: str = None,
    metadata: dict = None
) -> Tuple[bool, str]:
    """Update a Stripe customer."""
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        update_data = {}
        if email:
            update_data["email"] = email
        if name:
            update_data["name"] = name
        if metadata:
            update_data["metadata"] = metadata
        
        if not update_data:
            return True, "No updates needed"
        
        stripe.Customer.modify(customer_id, **update_data)
        logger.info(f"Updated Stripe customer {customer_id}")
        return True, "Customer updated"
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error updating customer: {e}")
        return False, str(e)


# ============================================================
# Invoice Creation
# ============================================================

def create_stripe_invoice(
    db,
    invoice_id: int,
    auto_advance: bool = True,
    collection_method: str = "charge_automatically"
) -> Tuple[bool, str]:
    """
    Create a Stripe invoice from a local invoice.
    
    Args:
        db: Database session
        invoice_id: Local invoice ID
        auto_advance: Auto-finalize the invoice
        collection_method: How to collect payment
    
    Returns:
        Tuple of (success, stripe_invoice_id or error)
    """
    from app.models import Invoice, InvoiceLine, User
    
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    # Get local invoice
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return False, "Invoice not found"
    
    # Don't re-create if already synced
    if invoice.stripe_invoice_id:
        return True, invoice.stripe_invoice_id
    
    # Get or create Stripe customer
    success, customer_id = get_or_create_customer(db, invoice.user_id)
    if not success:
        return False, f"Could not get/create customer: {customer_id}"
    
    try:
        # Get line items
        lines = db.query(InvoiceLine).filter(
            InvoiceLine.invoice_id == invoice_id
        ).all()
        
        if not lines:
            return False, "Invoice has no line items"
        
        # Create invoice items
        for line in lines:
            stripe.InvoiceItem.create(
                customer=customer_id,
                amount=line.amount_cents,
                currency="usd",
                description=line.description,
                metadata={
                    "local_invoice_id": str(invoice.id),
                    "local_line_id": str(line.id),
                    "case_number": line.case_number or "",
                }
            )
        
        # Create the invoice
        stripe_invoice = stripe.Invoice.create(
            customer=customer_id,
            auto_advance=auto_advance,
            collection_method=collection_method,
            metadata={
                "local_invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
            },
            description=f"JSN Holdings - {invoice.invoice_number}",
        )
        
        # Update local invoice with Stripe IDs
        invoice.stripe_invoice_id = stripe_invoice.id
        if hasattr(stripe_invoice, 'hosted_invoice_url'):
            invoice.stripe_hosted_url = stripe_invoice.hosted_invoice_url
        
        db.commit()
        
        logger.info(f"Created Stripe invoice {stripe_invoice.id} for local invoice {invoice.invoice_number}")
        return True, stripe_invoice.id
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating invoice: {e}")
        return False, str(e)
    except Exception as e:
        logger.error(f"Error creating Stripe invoice: {e}")
        return False, str(e)


def finalize_stripe_invoice(stripe_invoice_id: str) -> Tuple[bool, str]:
    """Finalize a draft Stripe invoice."""
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        invoice = stripe.Invoice.finalize_invoice(stripe_invoice_id)
        logger.info(f"Finalized Stripe invoice {stripe_invoice_id}")
        return True, invoice.hosted_invoice_url or stripe_invoice_id
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error finalizing invoice: {e}")
        return False, str(e)


def void_stripe_invoice(stripe_invoice_id: str) -> Tuple[bool, str]:
    """Void a Stripe invoice."""
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        stripe.Invoice.void_invoice(stripe_invoice_id)
        logger.info(f"Voided Stripe invoice {stripe_invoice_id}")
        return True, "Invoice voided"
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error voiding invoice: {e}")
        return False, str(e)


def get_stripe_invoice(stripe_invoice_id: str) -> Optional[Dict]:
    """Get Stripe invoice details."""
    if not is_stripe_configured():
        return None
    
    try:
        invoice = stripe.Invoice.retrieve(stripe_invoice_id)
        return {
            "id": invoice.id,
            "status": invoice.status,
            "total": invoice.total,
            "amount_due": invoice.amount_due,
            "amount_paid": invoice.amount_paid,
            "hosted_invoice_url": invoice.hosted_invoice_url,
            "pdf": invoice.invoice_pdf,
            "created": invoice.created,
            "due_date": invoice.due_date,
        }
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error retrieving invoice: {e}")
        return None


# ============================================================
# Payment Methods
# ============================================================

def create_setup_intent(customer_id: str) -> Tuple[bool, str]:
    """
    Create a SetupIntent for collecting payment method.
    
    Returns client_secret for frontend use.
    """
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=["card"],
        )
        return True, setup_intent.client_secret
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating setup intent: {e}")
        return False, str(e)


def get_customer_payment_methods(customer_id: str) -> List[Dict]:
    """Get payment methods for a customer."""
    if not is_stripe_configured():
        return []
    
    try:
        payment_methods = stripe.PaymentMethod.list(
            customer=customer_id,
            type="card",
        )
        
        return [
            {
                "id": pm.id,
                "brand": pm.card.brand,
                "last4": pm.card.last4,
                "exp_month": pm.card.exp_month,
                "exp_year": pm.card.exp_year,
            }
            for pm in payment_methods.data
        ]
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error listing payment methods: {e}")
        return []


def set_default_payment_method(
    customer_id: str,
    payment_method_id: str
) -> Tuple[bool, str]:
    """Set default payment method for a customer."""
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        stripe.Customer.modify(
            customer_id,
            invoice_settings={
                "default_payment_method": payment_method_id,
            }
        )
        return True, "Default payment method updated"
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error setting default payment method: {e}")
        return False, str(e)


# ============================================================
# Webhook Handlers
# ============================================================

def handle_invoice_paid(db, stripe_invoice_id: str) -> bool:
    """
    Handle Stripe invoice.paid webhook event.
    
    Args:
        db: Database session
        stripe_invoice_id: Stripe invoice ID
    
    Returns:
        True if handled successfully
    """
    from app.models import Invoice
    
    invoice = db.query(Invoice).filter(
        Invoice.stripe_invoice_id == stripe_invoice_id
    ).first()
    
    if not invoice:
        logger.warning(f"No local invoice found for Stripe invoice {stripe_invoice_id}")
        return False
    
    invoice.status = "paid"
    invoice.paid_at = datetime.utcnow()
    db.commit()
    
    logger.info(f"Marked invoice {invoice.invoice_number} as paid")
    return True


def handle_invoice_payment_failed(db, stripe_invoice_id: str) -> bool:
    """
    Handle Stripe invoice.payment_failed webhook event.
    
    Args:
        db: Database session
        stripe_invoice_id: Stripe invoice ID
    
    Returns:
        True if handled successfully
    """
    from app.models import Invoice
    
    invoice = db.query(Invoice).filter(
        Invoice.stripe_invoice_id == stripe_invoice_id
    ).first()
    
    if not invoice:
        logger.warning(f"No local invoice found for Stripe invoice {stripe_invoice_id}")
        return False
    
    invoice.status = "failed"
    db.commit()
    
    logger.warning(f"Invoice {invoice.invoice_number} payment failed")
    return True


def handle_customer_subscription_deleted(db, customer_id: str) -> bool:
    """Handle customer subscription deletion (if using subscriptions)."""
    from app.models import User
    
    user = db.query(User).filter(
        User.stripe_customer_id == customer_id
    ).first()
    
    if user:
        user.is_billing_active = False
        db.commit()
        logger.info(f"Deactivated billing for user {user.id}")
        return True
    
    return False


# ============================================================
# Billing Portal
# ============================================================

def create_billing_portal_session(
    customer_id: str,
    return_url: str
) -> Tuple[bool, str]:
    """
    Create a Stripe Billing Portal session.
    
    Args:
        customer_id: Stripe customer ID
        return_url: URL to return to after portal session
    
    Returns:
        Tuple of (success, portal_url or error)
    """
    if not is_stripe_configured():
        return False, "Stripe not configured"
    
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return True, session.url
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating portal session: {e}")
        return False, str(e)


# ============================================================
# Utility Functions
# ============================================================

def sync_invoice_status(db, invoice_id: int) -> Tuple[bool, str]:
    """
    Sync local invoice status with Stripe.
    
    Args:
        db: Database session
        invoice_id: Local invoice ID
    
    Returns:
        Tuple of (success, status or error)
    """
    from app.models import Invoice
    
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice or not invoice.stripe_invoice_id:
        return False, "Invoice not found or not synced to Stripe"
    
    stripe_data = get_stripe_invoice(invoice.stripe_invoice_id)
    if not stripe_data:
        return False, "Could not retrieve Stripe invoice"
    
    # Map Stripe status to local status
    status_map = {
        "draft": "pending",
        "open": "pending",
        "paid": "paid",
        "uncollectible": "failed",
        "void": "cancelled",
    }
    
    new_status = status_map.get(stripe_data["status"], invoice.status)
    
    if new_status != invoice.status:
        invoice.status = new_status
        if new_status == "paid" and not invoice.paid_at:
            invoice.paid_at = datetime.utcnow()
        db.commit()
    
    return True, new_status


def get_revenue_summary(days: int = 30) -> Dict:
    """
    Get revenue summary from Stripe.
    
    Args:
        days: Number of days to look back
    
    Returns:
        Dict with revenue metrics
    """
    if not is_stripe_configured():
        return {"error": "Stripe not configured"}
    
    try:
        import time
        from datetime import timedelta
        
        # Calculate timestamp for N days ago
        start_time = int((datetime.utcnow() - timedelta(days=days)).timestamp())
        
        # Get balance transactions
        transactions = stripe.BalanceTransaction.list(
            created={"gte": start_time},
            limit=100,
        )
        
        total_revenue = 0
        total_fees = 0
        transaction_count = 0
        
        for txn in transactions.auto_paging_iter():
            if txn.type in ["charge", "payment"]:
                total_revenue += txn.amount
                total_fees += txn.fee
                transaction_count += 1
        
        return {
            "period_days": days,
            "total_revenue_cents": total_revenue,
            "total_revenue": f"${total_revenue/100:.2f}",
            "total_fees_cents": total_fees,
            "total_fees": f"${total_fees/100:.2f}",
            "net_revenue_cents": total_revenue - total_fees,
            "net_revenue": f"${(total_revenue - total_fees)/100:.2f}",
            "transaction_count": transaction_count,
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error getting revenue: {e}")
        return {"error": str(e)}


# ============================================================
# V3 Automated Billing - Payment Method Management
# ============================================================

def get_publishable_key() -> str:
    """Get Stripe publishable key for frontend."""
    import os
    return os.getenv("STRIPE_PUBLISHABLE_KEY", "")


def create_setup_intent_for_user(db, user_id: int) -> Tuple[bool, str, Optional[Dict]]:
    """
    Create a SetupIntent for collecting payment method.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Tuple of (success, message, setup_data)
    """
    if not is_stripe_configured():
        return False, "Stripe is not configured", None
    
    # Ensure customer exists
    success, customer_id = get_or_create_customer(db, user_id)
    if not success:
        return False, customer_id, None
    
    try:
        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=["card"],
            metadata={
                "user_id": str(user_id)
            }
        )
        
        return True, "Setup intent created", {
            "client_secret": setup_intent.client_secret,
            "setup_intent_id": setup_intent.id,
            "publishable_key": get_publishable_key(),
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating setup intent: {e}")
        return False, str(e), None


def save_payment_method_for_user(db, user_id: int, payment_method_id: str) -> Tuple[bool, str]:
    """
    Save a payment method as the user's default.
    
    Args:
        db: Database session
        user_id: User ID
        payment_method_id: Stripe payment method ID
    
    Returns:
        Tuple of (success, message)
    """
    from app.models import User
    
    if not is_stripe_configured():
        return False, "Stripe is not configured"
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found"
    
    if not user.stripe_customer_id:
        # Create customer first
        success, result = get_or_create_customer(db, user_id)
        if not success:
            return False, result
    
    try:
        # Attach payment method to customer
        stripe.PaymentMethod.attach(
            payment_method_id,
            customer=user.stripe_customer_id
        )
        
        # Set as default payment method
        stripe.Customer.modify(
            user.stripe_customer_id,
            invoice_settings={
                "default_payment_method": payment_method_id
            }
        )
        
        # Update user record
        user.stripe_payment_method_id = payment_method_id
        user.has_valid_payment_method = True
        db.commit()
        
        logger.info(f"Saved payment method {payment_method_id} for user {user_id}")
        return True, "Payment method saved successfully"
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error saving payment method: {e}")
        return False, str(e)


def get_user_payment_method_info(db, user_id: int) -> Optional[Dict]:
    """
    Get user's saved payment method details.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Dict with card info or None
    """
    from app.models import User
    
    if not is_stripe_configured():
        return None
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.stripe_payment_method_id:
        return None
    
    try:
        pm = stripe.PaymentMethod.retrieve(user.stripe_payment_method_id)
        
        if pm.type == "card":
            return {
                "id": pm.id,
                "type": "card",
                "brand": pm.card.brand.title(),
                "last4": pm.card.last4,
                "exp_month": pm.card.exp_month,
                "exp_year": pm.card.exp_year,
                "display": f"{pm.card.brand.title()} •••• {pm.card.last4}",
            }
        return {"id": pm.id, "type": pm.type}
        
    except stripe.error.StripeError:
        return None


def remove_user_payment_method(db, user_id: int) -> Tuple[bool, str]:
    """
    Remove user's saved payment method.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Tuple of (success, message)
    """
    from app.models import User
    
    if not is_stripe_configured():
        return False, "Stripe is not configured"
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found"
    
    if user.stripe_payment_method_id:
        try:
            stripe.PaymentMethod.detach(user.stripe_payment_method_id)
        except stripe.error.StripeError:
            pass  # Payment method may already be detached
    
    user.stripe_payment_method_id = None
    user.has_valid_payment_method = False
    db.commit()
    
    return True, "Payment method removed"


# ============================================================
# V3 Automated Billing - Invoice Charging
# ============================================================

def charge_invoice_automatically(db, invoice_id: int) -> Tuple[bool, str, Optional[str]]:
    """
    Automatically charge an invoice using stored payment method.
    
    Args:
        db: Database session
        invoice_id: Invoice ID to charge
    
    Returns:
        Tuple of (success, message, payment_intent_id)
    """
    from app.models import Invoice, User
    
    if not is_stripe_configured():
        return False, "Stripe is not configured", None
    
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return False, "Invoice not found", None
    
    if invoice.status == "paid":
        return False, "Invoice already paid", None
    
    if invoice.total_cents <= 0:
        return False, "Invoice has no amount due", None
    
    user = db.query(User).filter(User.id == invoice.user_id).first()
    if not user:
        return False, "User not found", None
    
    if not user.stripe_customer_id:
        return False, "User has no Stripe customer", None
    
    if not user.stripe_payment_method_id:
        return False, "User has no payment method on file", None
    
    try:
        # Create PaymentIntent and charge immediately
        payment_intent = stripe.PaymentIntent.create(
            amount=invoice.total_cents,
            currency="usd",
            customer=user.stripe_customer_id,
            payment_method=user.stripe_payment_method_id,
            off_session=True,
            confirm=True,
            description=f"Invoice {invoice.invoice_number}",
            metadata={
                "invoice_id": str(invoice.id),
                "invoice_number": invoice.invoice_number,
                "user_id": str(user.id),
            }
        )
        
        if payment_intent.status == "succeeded":
            # Update invoice as paid
            invoice.status = "paid"
            invoice.paid_at = datetime.utcnow()
            invoice.stripe_payment_intent = payment_intent.id
            db.commit()
            
            logger.info(f"Successfully charged invoice {invoice_id}: ${invoice.total_cents/100:.2f}")
            return True, "Payment successful", payment_intent.id
        else:
            # Payment requires action or failed
            invoice.status = "failed"
            invoice.stripe_payment_intent = payment_intent.id
            db.commit()
            
            return False, f"Payment status: {payment_intent.status}", payment_intent.id
            
    except stripe.error.CardError as e:
        # Card was declined
        invoice.status = "failed"
        db.commit()
        
        logger.warning(f"Card declined for invoice {invoice_id}: {e.user_message}")
        return False, f"Card declined: {e.user_message}", None
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error charging invoice {invoice_id}: {e}")
        return False, str(e), None


def charge_all_pending_invoices(db) -> Dict:
    """
    Charge all pending invoices that have valid payment methods.
    
    Args:
        db: Database session
    
    Returns:
        Dict with results summary
    """
    from app.models import Invoice, User
    
    results = {
        "total": 0,
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "total_charged_cents": 0,
        "details": []
    }
    
    # Get all pending invoices
    pending_invoices = db.query(Invoice).filter(Invoice.status == "pending").all()
    results["total"] = len(pending_invoices)
    
    for invoice in pending_invoices:
        # Check if user has valid payment method
        user = db.query(User).filter(User.id == invoice.user_id).first()
        
        if not user or not user.has_valid_payment_method:
            results["skipped"] += 1
            results["details"].append({
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "user_email": user.email if user else "Unknown",
                "amount": f"${(invoice.total_cents or 0)/100:.2f}",
                "status": "skipped",
                "reason": "No valid payment method"
            })
            continue
        
        # Attempt to charge
        success, message, payment_id = charge_invoice_automatically(db, invoice.id)
        
        if success:
            results["successful"] += 1
            results["total_charged_cents"] += invoice.total_cents or 0
            results["details"].append({
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "user_email": user.email,
                "amount": f"${(invoice.total_cents or 0)/100:.2f}",
                "status": "paid",
                "payment_intent": payment_id
            })
        else:
            results["failed"] += 1
            results["details"].append({
                "invoice_id": invoice.id,
                "invoice_number": invoice.invoice_number,
                "user_email": user.email,
                "amount": f"${(invoice.total_cents or 0)/100:.2f}",
                "status": "failed",
                "reason": message
            })
    
    results["total_charged_display"] = f"${results['total_charged_cents']/100:.2f}"
    
    return results


# ============================================================
# V3 Claim Authorization
# ============================================================

def check_user_can_claim(db, user_id: int) -> Tuple[bool, str]:
    """
    Check if user can claim cases (has valid payment method).
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Tuple of (can_claim, message)
    """
    from app.models import User
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "User not found"
    
    # Admins can always claim
    if user.is_admin:
        return True, "Admin access"
    
    # Check if billing is active
    if not user.is_billing_active:
        return False, "Billing is disabled for your account"
    
    # If Stripe is not configured, allow claiming (for development/testing)
    if not is_stripe_configured():
        return True, "Stripe not configured - billing disabled"
    
    # Check for valid payment method (only when Stripe is configured)
    if not user.has_valid_payment_method:
        return False, "Please add a payment method before claiming cases"
    
    return True, "OK"
