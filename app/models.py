# app/models.py
"""
Database Models - COMPLETE WITH METHODS
✅ Matches your exact database schema
✅ Includes get_outstanding_liens() method
✅ Includes all lien-related tables
"""

from __future__ import annotations

import json
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Float, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from datetime import datetime
from typing import List, Dict, Any

from app.database import Base


class User(Base):
    """User model"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), default='')
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)
    last_login = Column(TIMESTAMP, nullable=True)
    role = Column(Text, default='analyst')
    
    def verify_password(self, password: str) -> bool:
        from app.auth import verify_password as verify_pw
        return verify_pw(password, self.hashed_password)
    
    def __repr__(self) -> str:
        return f"<User(id={self.id}, username='{self.username}')>"


class Case(Base):
    """Foreclosure case model - all 48 columns with methods"""
    __tablename__ = "cases"
    
    # Core identification
    id = Column(Integer, primary_key=True, nullable=False)
    case_number = Column(String, nullable=False, unique=True, index=True)
    filing_datetime = Column(String)
    
    # Case metadata
    style = Column(String)
    division = Column(String)
    judge = Column(String)
    
    # Property identification
    parcel_id = Column(String)
    address = Column(String)
    address_override = Column(String)
    
    # Links and paths
    subscriber_case_link = Column(String)
    verified_complaint_path = Column(String)
    value_calc_path = Column(String)
    mortgage_path = Column(String)
    current_deed_path = Column(String)
    previous_deed_path = Column(String)
    appraiser_doc1_path = Column(String)
    appraiser_doc2_path = Column(String)
    
    # Financial data
    arv = Column(Float)
    rehab = Column(Float)
    closing_costs = Column(Float)
    close_price = Column(Float)
    
    # Liens (stored as JSON text)
    outstanding_liens = Column(Text, nullable=False, default='[]')
    
    # Status
    archived = Column(Integer, default=0)
    status = Column(Text, default='new')
    
    # PropWire integration
    propwire_url = Column(Text, default='')
    
    # Property details
    prop_est_value = Column(Float, default=0.0)
    prop_sqft = Column(Integer, default=0)
    prop_lot_size = Column(Text, default='')
    prop_est_equity = Column(Text, default='')
    prop_open_mortgages = Column(Text, default='')
    prop_year_built = Column(Text, default='')
    prop_apn = Column(Text, default='')
    prop_pool = Column(Text, default='')
    
    # Mortgage details
    mortgage_amount = Column(Float, default=0)
    mortgage_lender = Column(Text, default='')
    mortgage_borrower = Column(Text, default='')
    mortgage_date = Column(Text, default='')
    mortgage_recording_date = Column(Text, default='')
    mortgage_instrument = Column(Text, default='')
    
    # Skip trace
    skip_trace_json = Column(Text)
    
    # Property condition
    rehab_condition = Column(Text, default='Good')
    property_overrides = Column(Text, default='{}')
    
    # Assignment and workflow
    assigned_to = Column(Integer)
    priority = Column(Integer, default=0)
    last_contact_date = Column(Text)
    next_followup_date = Column(Text)
    estimated_close_date = Column(Text)
    actual_close_date = Column(Text)
    
    # Relationships
    notes = relationship("Note", back_populates="case", cascade="all, delete-orphan")
    defendants = relationship("Defendant", back_populates="case", cascade="all, delete-orphan")
    dockets = relationship("Docket", back_populates="case", cascade="all, delete-orphan")
    property_data = relationship("CaseProperty", backref="case", uselist=False)
    deed_history = relationship("CasePropertyDeedHistory", back_populates="case", cascade="all, delete-orphan")
    mortgage_history = relationship("CasePropertyMortgageHistory", back_populates="case", cascade="all, delete-orphan")
    open_lien_mortgages = relationship("CasePropertyOpenLienMortgage", back_populates="case", cascade="all, delete-orphan")
    involuntary_liens = relationship("CasePropertyInvoluntaryLien", back_populates="case", cascade="all, delete-orphan")
    
    # ========================================
    # METHODS
    # ========================================
    
    def get_outstanding_liens(self) -> List[Dict[str, Any]]:
        """
        Parse and return outstanding liens from JSON
        
        Returns:
            List of lien dictionaries
        """
        if not self.outstanding_liens:
            return []
        
        try:
            # Try to parse as JSON
            liens = json.loads(self.outstanding_liens)
            
            # Ensure it's a list
            if isinstance(liens, list):
                return liens
            elif isinstance(liens, dict):
                return [liens]  # Wrap single lien in list
            else:
                return []
                
        except (json.JSONDecodeError, TypeError, ValueError):
            # If parsing fails, return empty list
            return []
    
    def get_total_liens(self) -> float:
        """
        Calculate total amount of outstanding liens
        
        Returns:
            Total lien amount as float
        """
        liens = self.get_outstanding_liens()
        total = 0.0
        
        for lien in liens:
            # Try different possible amount field names
            amount = lien.get('amount') or lien.get('balance') or lien.get('value') or 0
            try:
                total += float(amount)
            except (ValueError, TypeError):
                continue
        
        return total
    
    def __repr__(self) -> str:
        return f"<Case(id={self.id}, case_number='{self.case_number}')>"


class Defendant(Base):
    """Defendant model"""
    __tablename__ = "defendants"
    
    id = Column(Integer, primary_key=True, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"))
    name = Column(String)
    
    case = relationship("Case", back_populates="defendants")
    
    def __repr__(self) -> str:
        return f"<Defendant(id={self.id}, name='{self.name}')>"


class Docket(Base):
    """Docket entries"""
    __tablename__ = "dockets"
    
    id = Column(Integer, primary_key=True, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"))
    docket_datetime = Column(String)
    docket_text = Column(Text)
    link = Column(String)
    file_name = Column(Text, default='')
    file_url = Column(Text, default='')
    description = Column(Text, default='')
    
    case = relationship("Case", back_populates="dockets")
    
    def __repr__(self) -> str:
        return f"<Docket(id={self.id}, case_id={self.case_id})>"


class Note(Base):
    """Notes"""
    __tablename__ = "notes"
    
    id = Column(Integer, primary_key=True, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    content = Column(Text)
    created_at = Column(String)
    
    case = relationship("Case", back_populates="notes")
    
    def __repr__(self) -> str:
        preview = self.content[:50] + "..." if self.content and len(self.content) > 50 else self.content
        return f"<Note(id={self.id}, preview='{preview}')>"


class Session(Base):
    """User sessions"""
    __tablename__ = "sessions"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(Text, nullable=False, unique=True, index=True)
    expires_at = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    
    def __repr__(self) -> str:
        return f"<Session(id={self.id}, user_id={self.user_id})>"


class PropertyComparable(Base):
    """Property comparables"""
    __tablename__ = "property_comparables"
    
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    comp_address = Column(Text)
    comp_city = Column(Text)
    comp_state = Column(Text)
    comp_zip = Column(Text)
    sale_date = Column(Text)
    sale_price = Column(Float)
    bedrooms = Column(Integer)
    bathrooms = Column(Float)
    sqft = Column(Integer)
    year_built = Column(Integer)
    distance_miles = Column(Float)
    price_per_sqft = Column(Float)
    source = Column(Text)
    fetched_at = Column(Text)
    
    def __repr__(self) -> str:
        return f"<PropertyComparable(id={self.id}, case_id={self.case_id})>"


class CaseProperty(Base):
    """Property data from BatchData API (173 columns)"""
    __tablename__ = "case_property"
    
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    raw_json = Column(Text)
    created_at = Column(Text)
    updated_at = Column(Text)
    
    # Address
    address_street = Column(Text)
    address_city = Column(Text)
    address_state = Column(Text)
    address_zip = Column(Text)
    address_latitude = Column(Float)
    address_longitude = Column(Float)
    
    # Owner
    owner_full_name = Column(Text)
    owner_occupied = Column(Integer)
    
    # Building
    bldg_living_area_sqft = Column(Float)
    bldg_year_built = Column(Integer)
    bldg_calc_bath_count = Column(Float)
    
    # Values
    assessed_total_value = Column(Float)
    market_total_value = Column(Float)
    val_estimated_value = Column(Float)
    
    # Liens
    open_lien_total_balance = Column(Float)
    open_lien_total_count = Column(Integer)
    
    # Quick leads flags
    ql_free_and_clear = Column(Integer)
    ql_high_equity = Column(Integer)
    ql_owner_occupied = Column(Integer)
    ql_preforeclosure = Column(Integer)
    ql_vacant = Column(Integer)
    
    def __repr__(self) -> str:
        return f"<CaseProperty(id={self.id}, case_id={self.case_id})>"


class CasePropertyDeedHistory(Base):
    """Property deed history - 17 columns"""
    __tablename__ = "case_property_deed_history"
    
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    seq = Column(Integer)
    buyers_json = Column(Text)
    sellers_json = Column(Text)
    recording_date = Column(Text)
    sale_date = Column(Text)
    document_number = Column(Text)
    document_type_code = Column(Text)
    document_type = Column(Text)
    sale_price = Column(Float)
    inter_family = Column(Integer)
    mailing_street = Column(Text)
    mailing_city = Column(Text)
    mailing_state = Column(Text)
    mailing_zip = Column(Text)
    mailing_hash = Column(Text)
    
    case = relationship("Case", back_populates="deed_history")
    
    def __repr__(self) -> str:
        return f"<DeedHistory(id={self.id}, case_id={self.case_id}, sale_price={self.sale_price})>"


class CasePropertyMortgageHistory(Base):
    """Property mortgage history - 13 columns"""
    __tablename__ = "case_property_mortgage_history"
    
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    seq = Column(Integer)
    borrowers_json = Column(Text)
    sale_date = Column(Text)
    recording_date = Column(Text)
    due_date = Column(Text)
    lender_name = Column(Text)
    loan_type_code = Column(Text)
    loan_type = Column(Text)
    loan_amount = Column(Float)
    loan_term_months = Column(Integer)
    interest_rate = Column(Float)
    
    case = relationship("Case", back_populates="mortgage_history")
    
    def __repr__(self) -> str:
        return f"<MortgageHistory(id={self.id}, case_id={self.case_id}, loan_amount={self.loan_amount})>"


class CasePropertyOpenLienMortgage(Base):
    """Open lien mortgages - 18 columns"""
    __tablename__ = "case_property_open_lien_mortgage"
    
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    seq = Column(Integer)
    recording_date = Column(Text)
    loan_type_code = Column(Text)
    loan_type = Column(Text)
    due_date = Column(Text)
    loan_amount = Column(Float)
    lender_name = Column(Text)
    loan_term_months = Column(Integer)
    current_estimated_interest_rate = Column(Float)
    assigned_lender_name = Column(Text)
    ltv = Column(Float)
    estimated_payment_amount = Column(Float)
    adjustable_rate_index = Column(Text)
    transaction_type = Column(Text)
    transaction_type_code = Column(Text)
    first_change_date_year_conversion_rider = Column(Integer)
    
    case = relationship("Case", back_populates="open_lien_mortgages")
    
    def __repr__(self) -> str:
        return f"<OpenLienMortgage(id={self.id}, case_id={self.case_id}, loan_amount={self.loan_amount})>"


class CasePropertyInvoluntaryLien(Base):
    """Involuntary liens - 13 columns"""
    __tablename__ = "case_property_involuntary_lien"
    
    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    seq = Column(Integer)
    attorney_company_name = Column(Text)
    book_number = Column(Text)
    document_number = Column(Text)
    document_type = Column(Text)
    document_type_code = Column(Text)
    filing_date = Column(Text)
    lien_type = Column(Text)
    lien_type_code = Column(Text)
    page_number = Column(Text)
    recording_date = Column(Text)
    
    case = relationship("Case", back_populates="involuntary_liens")
    
    def __repr__(self) -> str:
        return f"<InvoluntaryLien(id={self.id}, case_id={self.case_id}, lien_type='{self.lien_type}')>"


class AuditLog(Base):
    """Audit logs"""
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    action = Column(Text, nullable=False)
    entity_type = Column(Text, nullable=False)
    entity_id = Column(Integer)
    changes_json = Column(Text)
    ip_address = Column(Text)
    user_agent = Column(Text)
    timestamp = Column(Text, nullable=False)
    
    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action='{self.action}')>"
