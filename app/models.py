# app/models.py
"""
SQLAlchemy Models with User Assignment Support
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
    archived = Column(Integer, default=0, index=True)
    
    # Relationships
    defendants = relationship("Defendant", back_populates="case", cascade="all, delete-orphan")
    dockets = relationship("Docket", back_populates="case", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="case", cascade="all, delete-orphan")

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
