# app/models_v3_additions.py
"""
V3 Model Additions
Add these to your existing models.py file

INSTRUCTIONS:
1. Add the import for 'relationship' if not already present
2. Add the 'assigned_at' column to the Case class
3. Add the CaseClaim class after the existing models
4. Run the migration script to create the new table
"""

# ============================================================
# ADD TO IMPORTS at top of models.py:
# ============================================================
# from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, func
# from sqlalchemy.orm import relationship  # <-- Add this if not present


# ============================================================
# ADD TO Case CLASS (after assigned_to):
# ============================================================
"""
    # V3 Addition - Timestamp when case was assigned
    assigned_at = Column(DateTime, nullable=True, index=True)
"""


# ============================================================
# ADD NEW CLASS after existing models:
# ============================================================

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, func
from sqlalchemy.orm import relationship
from app.database import Base


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
    case = relationship("Case", backref="claims")
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
# OPTIONAL: Add to User class for V3 billing features
# ============================================================
"""
    # V3 Additions
    stripe_customer_id = Column(String, nullable=True)
    max_claims = Column(Integer, default=50)  # Claim limit per user
    is_billing_active = Column(Boolean, default=True)
"""


# ============================================================
# MIGRATION SCRIPT - Run this to create the new table
# ============================================================
"""
Run this in Python console or as a script:

from app.database import engine
from sqlalchemy import text

# Add assigned_at column to cases table
try:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE cases ADD COLUMN assigned_at DATETIME NULL"))
    print("Added assigned_at column to cases table")
except Exception as e:
    print(f"Column may already exist: {e}")

# Create case_claims table
with engine.begin() as conn:
    conn.execute(text('''
        CREATE TABLE IF NOT EXISTS case_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            claimed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            released_at DATETIME NULL,
            score_at_claim INTEGER DEFAULT 0,
            price_cents INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            
            FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    '''))
    
    # Create indexes
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_claims_case_id ON case_claims(case_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_claims_user_id ON case_claims(user_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_claims_active ON case_claims(is_active)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS idx_claims_date ON case_claims(claimed_at)'))
    
print("Created case_claims table with indexes")
"""
