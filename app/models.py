# app/models.py
"""
SQLAlchemy Models with User Assignment Support + V3 Claiming
"""

from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
import json


class User(Base):
    __tablename__ = "users"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, default="")
    role = Column(String, default="analyst")
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    last_login = Column(DateTime, nullable=True)
    
    # V3 Billing fields
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_payment_method_id = Column(String, nullable=True)  # Default payment method
    has_valid_payment_method = Column(Boolean, default=False)  # Can user claim cases?
    max_claims = Column(Integer, default=50)  # Claim limit per user
    is_billing_active = Column(Boolean, default=True)
    
    def verify_password(self, password: str) -> bool:
        """Verify a password against the hash"""
        import bcrypt
        return bcrypt.checkpw(password.encode('utf-8'), self.hashed_password.encode('utf-8'))
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password"""
        import bcrypt
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


class Case(Base):
    __tablename__ = "cases"
    
    id = Column(Integer, primary_key=True, index=True)
    case_number = Column(String, unique=True, index=True, nullable=False)
    filing_datetime = Column(String, default="")
    style = Column(String, default="")
    division = Column(String, default="")
    judge = Column(String, default="")
    parcel_id = Column(String, default="")
    address = Column(String, default="")
    address_override = Column(String, default="")
    subscriber_case_link = Column(String, default="")
    
    # Document paths
    verified_complaint_path = Column(String, default="")
    value_calc_path = Column(String, default="")
    mortgage_path = Column(String, default="")
    current_deed_path = Column(String, default="")
    previous_deed_path = Column(String, default="")
    appraiser_doc1_path = Column(String, default="")
    appraiser_doc2_path = Column(String, default="")
    
    # Financial fields
    arv = Column(Float, default=0.0)
    rehab = Column(Float, default=0.0)
    rehab_condition = Column(String, default="Good")
    closing_costs = Column(Float, default=0.0)
    outstanding_liens = Column(Text, default="[]", nullable=False)
    property_overrides = Column(Text, default="{}")
    
    # Assignment & Status
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_at = Column(DateTime, nullable=True, index=True)  # V3: When case was assigned
    archived = Column(Integer, default=0, index=True)
    
    # Relationships
    defendants = relationship("Defendant", back_populates="case", cascade="all, delete-orphan")
    dockets = relationship("Docket", back_populates="case", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="case", cascade="all, delete-orphan")
    claims = relationship("CaseClaim", back_populates="case", cascade="all, delete-orphan")  # V3

    # JSON helpers
    def get_outstanding_liens(self):
        try:
            return json.loads(self.outstanding_liens or "[]")
        except Exception:
            return []

    def set_outstanding_liens(self, liens_list):
        try:
            self.outstanding_liens = json.dumps(liens_list or [])
        except Exception:
            self.outstanding_liens = "[]"


class Defendant(Base):
    __tablename__ = "defendants"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"))
    name = Column(String, default="")
    
    case = relationship("Case", back_populates="defendants")


class Docket(Base):
    __tablename__ = "dockets"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"))
    docket_datetime = Column(String, default="")
    docket_text = Column(Text, default="")
    link = Column(String, default="")
    file_name = Column(String, default="")
    file_url = Column(String, default="")
    description = Column(String, default="")
    
    case = relationship("Case", back_populates="dockets")


class Note(Base):
    __tablename__ = "notes"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True, nullable=False)
    content = Column(Text, default="")
    created_at = Column(String, default="")
    
    case = relationship("Case", back_populates="notes")


# ============================================================
# V3: Case Claim Model
# ============================================================

class CaseClaim(Base):
    """
    Tracks case claims for billing and audit purposes.
    Each time a user claims a case, a new record is created.
    The score and price are frozen at claim time for billing accuracy.
    """
    __tablename__ = "case_claims"
    
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Timestamps
    claimed_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    released_at = Column(DateTime, nullable=True)
    
    # Frozen values at time of claim (for billing)
    score_at_claim = Column(Integer, default=0)
    price_cents = Column(Integer, default=0)  # Daily price in cents
    
    # Status
    is_active = Column(Boolean, default=True, index=True)
    
    # Relationships
    case = relationship("Case", back_populates="claims")
    user = relationship("User", backref="claims")
    
    def __repr__(self):
        return f"<CaseClaim {self.id}: case={self.case_id}, user={self.user_id}, active={self.is_active}>"
    
    @property
    def price_display(self) -> str:
        """Return formatted price string."""
        return f"${self.price_cents / 100:.2f}"
    
    @property
    def duration_days(self) -> int:
        """Return number of days this claim has been active."""
        from datetime import datetime
        end = self.released_at or datetime.utcnow()
        delta = end - self.claimed_at
        return max(1, delta.days)  # Minimum 1 day


# ============================================================
# V3 Phase 4: Invoice Models for Billing
# ============================================================

class Invoice(Base):
    """
    Tracks invoices generated for user billing.
    One invoice per user per billing period (daily).
    """
    __tablename__ = "invoices"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Invoice details
    invoice_number = Column(String, unique=True, nullable=False, index=True)
    invoice_date = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    due_date = Column(DateTime, nullable=True)
    
    # Amounts (in cents for precision)
    subtotal_cents = Column(Integer, default=0)
    tax_cents = Column(Integer, default=0)
    total_cents = Column(Integer, default=0)
    
    # Status
    status = Column(String, default="pending", index=True)  # pending, paid, failed, cancelled
    
    # Stripe integration
    stripe_invoice_id = Column(String, nullable=True, index=True)
    stripe_payment_intent = Column(String, nullable=True)
    stripe_hosted_url = Column(String, nullable=True)
    
    # Timestamps
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    
    # Relationships
    user = relationship("User", backref="invoices")
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Invoice {self.invoice_number}: user={self.user_id}, total=${self.total_cents/100:.2f}, status={self.status}>"
    
    @property
    def total_display(self) -> str:
        """Return formatted total string."""
        return f"${(self.total_cents or 0) / 100:.2f}"
    
    @property
    def subtotal_display(self) -> str:
        """Return formatted subtotal string."""
        return f"${(self.subtotal_cents or 0) / 100:.2f}"


class InvoiceLine(Base):
    """
    Individual line items on an invoice.
    Each line corresponds to one day of one claim.
    """
    __tablename__ = "invoice_lines"
    
    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id = Column(Integer, ForeignKey("case_claims.id", ondelete="SET NULL"), nullable=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="SET NULL"), nullable=True)
    
    # Line item details
    description = Column(String, nullable=False)
    quantity = Column(Integer, default=1)  # Number of days
    unit_price_cents = Column(Integer, default=0)  # Price per day
    amount_cents = Column(Integer, default=0)  # Total for line (quantity * unit_price)
    
    # Reference data (frozen at invoice time)
    case_number = Column(String, nullable=True)
    score_at_invoice = Column(Integer, default=0)
    
    # Timestamps
    service_date = Column(DateTime, nullable=True)  # The day being billed for
    created_at = Column(DateTime, server_default=func.now())
    
    # Relationships
    invoice = relationship("Invoice", back_populates="lines")
    claim = relationship("CaseClaim", backref="invoice_lines")
    
    def __repr__(self):
        return f"<InvoiceLine {self.id}: invoice={self.invoice_id}, amount=${self.amount_cents/100:.2f}>"
    
    @property
    def amount_display(self) -> str:
        """Return formatted amount string."""
        return f"${self.amount_cents / 100:.2f}"
    
    @property
    def unit_price_display(self) -> str:
        """Return formatted unit price string."""
        return f"${self.unit_price_cents / 100:.2f}"

