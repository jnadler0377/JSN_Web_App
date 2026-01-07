from __future__ import annotations

# ---- Load environment variables from .env file ----
from dotenv import load_dotenv
load_dotenv()

# ---- Windows event loop fix for asyncio subprocess ----
import sys as _sys
import asyncio as _asyncio
if _sys.platform == "win32":
    try:
        _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
# =====================================================
# V2.0 ROUTE ADDITIONS FOR main.py
# Copy these routes into your main.py file
# =====================================================

# -----------------------------------------------------
# ADD THESE IMPORTS AT THE TOP OF main.py
# -----------------------------------------------------

from fastapi.responses import StreamingResponse
import asyncio

# Import V2 services
from app.services.notification_service import (
    get_user_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_as_read,
    delete_notification,
    create_notification,
    add_listener,
    remove_listener,
)
from app.services.deal_analysis_service import (
    analyze_deal,
    bulk_analyze_cases,
)

# Document Management Service
try:
    from app.services.document_manager_service import (
        DocumentManager, DocumentType, get_document_manager
    )
    DOCUMENT_MANAGER_AVAILABLE = True
except ImportError:
    DOCUMENT_MANAGER_AVAILABLE = False
# ---------------- Stdlib ----------------
import asyncio
import csv as _csv
import datetime as _dt
import io
import json
import logging
import os
import re
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus

# ---------------- Third Party ----------------
import requests  # for BatchData skip trace calls

# ---------------- FastAPI / Responses ----------------
from fastapi import (
    FastAPI,
    Request,
    Depends,
    UploadFile,
    File,
    Form,
    Query,
    HTTPException,
    Body,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------- DB / ORM ----------------
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text, bindparam, func
from sqlalchemy.exc import OperationalError

# ---------------- App imports ----------------
from app.config import settings
from app.auth_routes import router as auth_router, require_auth
from app.services.auth_service import get_current_user, create_user
from app.auth import get_password_hash
from app.services.progress_bus import progress_bus
from app.services.report_service import (
    generate_case_report,
    build_case_report_bytes,
    _is_short_sale,
    _report_filename
)
from app.services.update_cases_service import run_update_cases_job, LAST_UPDATE_STATUS
from app.services.skiptrace_service import (
    get_case_address_components,
    batchdata_skip_trace,
    batchdata_property_lookup_all_attributes,
    save_property_for_case,
    save_skiptrace_row,
    load_property_for_case,
    load_skiptrace_for_case,
    normalize_property_payload,
)
from app.api.v2_endpoints import router as v2_router
from app.routes.admin_routes import router as admin_router, init_templates as init_admin_templates
from app.routes.report_routes import router as report_router, init_templates as init_report_templates, init_upload_root as init_report_upload_root
from app.routes.notification_routes import router as notification_router
from app.routes.document_routes import router as document_router, init_document_routes
from app.routes.analytics_routes import router as analytics_router, init_templates as init_analytics_templates
from tools.import_pasco_csv import main as import_pasco_csv_main

# V3: Claim routes and services
from app.routes.claim_routes import router as claim_router
from app.routes.billing_routes import router as billing_router
from app.routes.webhook_routes import router as webhook_router
from app.routes.task_routes import router as task_router, init_templates as init_task_templates
from app.routes.case_routes import router as case_router, init_case_routes
from app.routes.admin_v3_routes import router as admin_v3_router, init_admin_v3_templates
from app.routes.payment_routes import router as payment_router, init_payment_templates
from app.services.permission_service import can_view_sensitive, get_case_visibility
from app.services.claim_service import get_claim_for_case, get_user_claim_count

from .database import Base, engine, SessionLocal
from .models import Case, Defendant, Docket, Note, CaseClaim, Invoice, InvoiceLine
from .utils import ensure_case_folder, compute_offer_70, compute_offer_80
from .schemas import OutstandingLien, OutstandingLiensUpdate
from app.services.progress_bus import shutdown_progress_bus
# ========================================
# PROPERTY DATA PARSER
# ========================================

# Replace the parse_property_data function in main.py with this version:

def parse_property_data(property_payload: dict) -> dict:
    """Parse BatchData property payload into structured format"""
    if not property_payload or not property_payload.get("results"):
        return None
    
    results = property_payload["results"]
    properties = results.get("properties", [])
    
    if not properties:
        return None
    
    prop = properties[0]
    
    # Helper to safely get nested values
    def safe_get(obj, *keys, default=None):
        for key in keys:
            if isinstance(obj, dict):
                obj = obj.get(key)
            else:
                return default
            if obj is None:
                return default
        return obj or default
    
    # === HANDLE MULTIPLE OWNERS ===
    owner_data = prop.get("owner", {})
    owners_list = []
    
    # Check if owner is an array
    if isinstance(owner_data, list):
        # Multiple owner objects
        for owner in owner_data:
            owners_list.append({
                "fullName": owner.get("fullName") or owner.get("name") or "",
                "mailingAddress": {
                    "street": safe_get(owner, "mailingAddress", "street", default=""),
                    "city": safe_get(owner, "mailingAddress", "city", default=""),
                    "state": safe_get(owner, "mailingAddress", "state", default=""),
                    "zipCode": (
                        safe_get(owner, "mailingAddress", "zipCode") or
                        safe_get(owner, "mailingAddress", "zip") or
                        ""
                    ),
                    "county": safe_get(owner, "mailingAddress", "county", default=""),
                }
            })
    elif isinstance(owner_data, dict):
        # Single owner object
        full_name = (
            owner_data.get("fullName") or
            owner_data.get("name") or
            owner_data.get("ownerName") or
            ""
        )
        
        # Check if fullName contains multiple owners (semicolon-separated)
        if ";" in full_name:
            # Split into multiple owners with same address
            for name in full_name.split(";"):
                owners_list.append({
                    "fullName": name.strip(),
                    "mailingAddress": {
                        "street": safe_get(owner_data, "mailingAddress", "street", default=""),
                        "city": safe_get(owner_data, "mailingAddress", "city", default=""),
                        "state": safe_get(owner_data, "mailingAddress", "state", default=""),
                        "zipCode": (
                            safe_get(owner_data, "mailingAddress", "zipCode") or
                            safe_get(owner_data, "mailingAddress", "zip") or
                            ""
                        ),
                        "county": safe_get(owner_data, "mailingAddress", "county", default=""),
                    }
                })
        else:
            # Single owner
            owners_list.append({
                "fullName": full_name,
                "mailingAddress": {
                    "street": safe_get(owner_data, "mailingAddress", "street", default=""),
                    "city": safe_get(owner_data, "mailingAddress", "city", default=""),
                    "state": safe_get(owner_data, "mailingAddress", "state", default=""),
                    "zipCode": (
                        safe_get(owner_data, "mailingAddress", "zipCode") or
                        safe_get(owner_data, "mailingAddress", "zip") or
                        ""
                    ),
                    "county": safe_get(owner_data, "mailingAddress", "county", default=""),
                }
            })
    
    # If no owners found, create empty placeholder
    if not owners_list:
        owners_list = [{
            "fullName": "",
            "mailingAddress": {
                "street": "",
                "city": "",
                "state": "",
                "zipCode": "",
                "county": "",
            }
        }]
    
    parsed = {
        "owners": owners_list,  # Array of owner objects
        "owner": owners_list[0] if owners_list else {},  # Keep for backward compatibility
        "quickList": prop.get("quickList", {}),
        "valuation": {
            "asOfDate": safe_get(prop, "valuation", "asOfDate", default=""),
            "confidenceScore": safe_get(prop, "valuation", "confidenceScore"),
            "equityPercent": safe_get(prop, "valuation", "equityPercent"),
            "estimatedValue": safe_get(prop, "valuation", "estimatedValue"),
            "ltv": safe_get(prop, "valuation", "ltv"),
        },
        "intel": {
            "salePropensity": safe_get(prop, "intel", "salePropensity", default=""),
        },
        "demographics": {
            "age": safe_get(prop, "demographics", "age"),
            "childCount": safe_get(prop, "demographics", "childCount"),
            "gender": safe_get(prop, "demographics", "gender", default=""),
            "income": safe_get(prop, "demographics", "income"),
            "individualOccupation": safe_get(prop, "demographics", "individualOccupation", default=""),
            "maritalStatus": safe_get(prop, "demographics", "maritalStatus", default=""),
            "netWorth": safe_get(prop, "demographics", "netWorth"),
        },
        "properties": {
            "street": (
                safe_get(prop, "address", "street") or
                safe_get(prop, "address", "streetAddress") or
                ""
            ),
            "city": safe_get(prop, "address", "city", default=""),
            "state": safe_get(prop, "address", "state", default=""),
            "zipCode": (
                safe_get(prop, "address", "zipCode") or
                safe_get(prop, "address", "zip") or
                safe_get(prop, "address", "postalCode") or
                ""
            ),
            "county": safe_get(prop, "address", "county", default=""),
        },
        "ids": {
            "apn": (
                safe_get(prop, "ids", "apn") or
                safe_get(prop, "parcelId") or
                safe_get(prop, "apn") or
                ""
            ),
        },
        "foreclosure": {
            "caseNumber": safe_get(prop, "foreclosure", "caseNumber", default=""),
            "currentLenderName": safe_get(prop, "foreclosure", "currentLenderName", default=""),
            "documentType": safe_get(prop, "foreclosure", "documentType", default=""),
            "filingDate": safe_get(prop, "foreclosure", "filingDate", default=""),
        },
        "mortgageHistory": prop.get("mortgageHistory", []),
        "deedHistory": prop.get("deedHistory", []),
    }
    
    return parsed

# ========================================
# NEW ROUTES - Add these to app/main.py
# Features 7-11 implementation
# ========================================

from app.services.auth_service import (
    get_current_user,
    require_role,
    login_user,
    logout_user,
    get_session_token,
    create_user,
)
from app.services.analytics_service import (
    get_dashboard_metrics,
    get_cases_by_month,
    get_conversion_funnel,
    get_roi_analysis,
    get_top_opportunities,
)
from app.services.comparables_service import (
    fetch_and_save_comparables,
    load_comparables_from_db,
    calculate_suggested_arv,
)
from app.services.ocr_service import (
    extract_document_data,
    auto_populate_case_from_ocr,
)

# Resolve project root (adjust if your .env lives somewhere else)
BASE_DIR = Path(__file__).resolve().parent.parent  # e.g. C:\pascowebapp

# Read ONLY from the .env file



# ======================================================================
# App bootstrap
# ======================================================================
app = FastAPI(title="JSN Holdings Foreclosure Manager")
# Include authentication routes
app.include_router(auth_router)
# Include v2 API routes
app.include_router(v2_router)
app.include_router(admin_router)
app.include_router(report_router)
app.include_router(notification_router)
app.include_router(document_router)
app.include_router(analytics_router)
app.include_router(claim_router)  # V3: Case claiming routes
app.include_router(billing_router)  # V3: Billing routes
app.include_router(payment_router)  # V3: Payment method management
app.include_router(task_router)
app.include_router(case_router)  # Case management routes
app.include_router(webhook_router)  # V3 Phase 5: Stripe webhooks
app.include_router(admin_v3_router)  # V3 Phase 6: Admin billing/claims

# Rest of your app setup continues...
logger = logging.getLogger("pascowebapp")
logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATES_DIR = BASE_DIR / "app" / "templates"
UPLOAD_ROOT = BASE_DIR / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
init_admin_templates(templates)
init_report_templates(templates)
init_report_upload_root(UPLOAD_ROOT)
init_task_templates(templates)
init_case_routes(templates, UPLOAD_ROOT)  # Case routes
init_analytics_templates(templates)
init_admin_v3_templates(templates)  # V3 Phase 6
init_payment_templates(templates)  # V3: Payment method management

# V3 Phase 5: Initialize Stripe if configured
try:
    import os
    from app.services.stripe_service import init_stripe
    
    # Try settings first, then fall back to environment variable
    stripe_key = getattr(settings, 'stripe_secret_key', None) or os.getenv('STRIPE_SECRET_KEY')
    
    if stripe_key:
        if init_stripe(stripe_key):
            logger.info("Stripe initialized successfully")
        else:
            logger.warning("Stripe initialization returned False")
    else:
        logger.info("Stripe not configured (no STRIPE_SECRET_KEY)")
except ImportError as e:
    logger.warning(f"Stripe service not available: {e}")
except Exception as e:
    logger.warning(f"Failed to initialize Stripe: {e}")

# Initialize document routes with dependencies
try:
    from app.services.document_manager import DocumentManager, DocumentType, get_document_manager
    init_document_routes(UPLOAD_ROOT, True, get_document_manager, DocumentType)
except ImportError:
    init_document_routes(UPLOAD_ROOT, False, None, None)

# ======================================================================
# Jinja filters / globals
# ======================================================================
def _currency(v):
    try:
        return "${:,.2f}".format(float(v))
    except Exception:
        return "$0.00"

def format_phone(number):
    """Format phone number as (XXX) XXX-XXXX"""
    if not number:
        return "N/A"
    clean = str(number).replace('(', '').replace(')', '').replace('-', '').replace(' ', '')
    if len(clean) == 10:
        return f"({clean[:3]}) {clean[3:6]}-{clean[6:]}"
    elif len(clean) == 11 and clean[0] == '1':
        return f"({clean[1:4]}) {clean[4:7]}-{clean[7:]}"
    return clean

def streetview_url(address: str) -> str:
    """
    Prefer Google Street View Static API if key present, else fall back
    to Static Map with a marker. Reads key from settings (env/.env).
    """
    if not address:
        return ""
    address_q = quote_plus(address)
    key = settings.google_maps_api_key
    if key:
        base = "https://maps.googleapis.com/maps/api/streetview"
        return f"{base}?size=600x360&location={address_q}&key={key}"
    return ""


def _parcel_to_property_card_param(parcel_id: str | None) -> Optional[str]:
    """
    Convert Pasco parcel formats to the property card 'parcel=' digits string.

    Example:
      Input:  '33-24-16-0260-00000-2540'
      Output: '1624330260000002540'
      (the first three 2-digit sets are mirrored: 33-24-16 -> 16 24 33)
    """
    if not parcel_id:
        return None

    s = parcel_id.strip().replace(" ", "")
    parts = s.split("-")

    # If it looks like the standard dash-delimited format with first three 2-digit parts
    if len(parts) >= 3 and all(len(p) == 2 for p in parts[:3]):
        reordered = parts[2] + parts[1] + parts[0] + "".join(parts[3:])
        digits = "".join(ch for ch in reordered if ch.isdigit())
        return digits or None

    # Fallback: digits only
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits or None


def pasco_appraiser_url(parcel_id: str | None) -> Optional[str]:
    """Return the direct property card URL for a given parcel id."""
    param = _parcel_to_property_card_param(parcel_id)
    if not param:
        return None
    return f"https://search.pascopa.com/parcel.aspx?parcel={param}"

def _is_pinellas_parcel(parcel_id: str | None) -> bool:
    if not parcel_id:
        return False
    return re.fullmatch(r"\d{2}-\d{2}-\d{2}-\d{5}-\d{3}-\d{4}", parcel_id.strip()) is not None


def pinellas_appraiser_url(parcel_id: str | None) -> Optional[str]:
    """
    Return the Pinellas property details URL for a given parcel id.
    Example:
      19-29-16-92340-005-0160 -> s=162919923400050160
    """
    if not _is_pinellas_parcel(parcel_id):
        return None
    parts = parcel_id.strip().split("-")
    reordered = parts[2] + parts[1] + parts[0] + "".join(parts[3:])
    digits = "".join(ch for ch in reordered if ch.isdigit())
    if not digits:
        return None
    return (
        "https://www.pcpao.gov/property-details"
        f"?s={digits}&parcel={parcel_id.strip()}"
    )

# ======================================================================
# BatchData Skip Trace config + helpers
# Helpers located app/services/skiptrace_service.py
# ======================================================================

templates.env.filters["currency"] = _currency
templates.env.globals["streetview_url"] = streetview_url
templates.env.globals["pasco_appraiser_url"] = pasco_appraiser_url
templates.env.globals["pinellas_appraiser_url"] = pinellas_appraiser_url

# ======================================================================
# DB session
# ======================================================================
def get_db():
    """
    Standard DB session dependency.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _estimate_rehab_from_property(
    property_data: Optional[dict],
    condition: str,
    property_overrides: Optional[dict] = None,
) -> Optional[float]:
    if (not property_data or not isinstance(property_data, dict)) and not property_overrides:
        return None
    try:
        def _to_float(val: object) -> Optional[float]:
            if val is None:
                return None
            if isinstance(val, (int, float)):
                return float(val)
            s = str(val).strip()
            if not s:
                return None
            s = re.sub(r"[^0-9.]", "", s)
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        year_built = None
        sqft = None
        if property_data and isinstance(property_data, dict):
            props = (property_data.get("results") or {}).get("properties") or []
            if props:
                p = props[0]
                listing = p.get("listing") or {}
                general = p.get("general") or {}
                building = p.get("building") or {}

                year_built = (
                    listing.get("yearBuilt")
                    or general.get("yearBuilt")
                    or p.get("yearBuilt")
                )
                sqft = (
                    listing.get("totalBuildingAreaSquareFeet")
                    or building.get("livingAreaSqft")
                    or general.get("buildingAreaSqft")
                )

        overrides = property_overrides or {}
        override_year = _to_float(overrides.get("year_built"))
        override_sqft = _to_float(overrides.get("sqft"))

        year_val = override_year if override_year else _to_float(year_built)
        area_val = override_sqft if override_sqft else _to_float(sqft)

        year = int(year_val) if year_val else None
        area = float(area_val) if area_val else None
        if not year or not area or area <= 0:
            return None

        if year < 1960:
            base = 50.0
        elif year <= 1989:
            base = 40.0
        elif year <= 2009:
            base = 35.0
        elif year <= 2020:
            base = 30.0
        else:
            base = 20.0

        condition_key = (condition or "Good").strip().lower()
        multipliers = {
            "poor": 1.00,
            "fair": 0.75,
            "good": 0.60,
            "excellent": 0.50,
        }
        mult = multipliers.get(condition_key, 1.00)

        estimate = base * area * mult
        estimate = max(12000.0, min(120000.0, estimate))
        estimate = round(estimate * 2) / 2
        return round(estimate, 2)
    except Exception:
        return None


def _parse_property_overrides(case: Case) -> dict:
    try:
        raw = getattr(case, "property_overrides", "") or ""
        if raw:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    return {}


# ======================================================================
# Skip Trace JSON Cache Helpers (legacy, still safe to keep)
# ======================================================================
def get_cached_skip_trace(case_id: int) -> Optional[dict]:
    """
    Read cached skip-trace JSON from the cases table, if any.
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT skip_trace_json FROM cases WHERE id = :id"),
                {"id": case_id},
            ).mappings().first()
        if row and row.get("skip_trace_json"):
            try:
                return json.loads(row["skip_trace_json"])
            except Exception as exc:
                logger.warning(
                    "Failed to parse skip_trace_json for case %s: %s", case_id, exc
                )
                return None
    except Exception as exc:
        logger.warning(
            "Failed to read skip_trace_json for case %s: %s", case_id, exc
        )
    return None


def set_cached_skip_trace(case_id: int, payload: dict) -> None:
    """
    Persist skip-trace JSON into the cases.skip_trace_json column.
    """
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE cases SET skip_trace_json = :payload WHERE id = :id",
                {"payload": json.dumps(payload), "id": case_id},
            )
    except Exception as exc:
        logger.warning(
            "Failed to write skip_trace_json for case %s: %s", case_id, exc
        )


Base.metadata.create_all(bind=engine)

def format_date(date_str):
    """
    Format date string to MM/DD/YYYY
    Handles various input formats
    """
    if not date_str or date_str in ['N/A', '', 'None']:
        return 'N/A'
    
    # If already a string, try to parse it
    date_str = str(date_str).strip()
    
    # Common date formats to try
    formats_to_try = [
        '%Y-%m-%d',           # 2024-01-15
        '%Y-%m-%dT%H:%M:%S',  # 2024-01-15T10:30:00
        '%Y-%m-%d %H:%M:%S',  # 2024-01-15 10:30:00
        '%m/%d/%Y',           # 01/15/2024
        '%m-%d-%Y',           # 01-15-2024
        '%Y/%m/%d',           # 2024/01/15
        '%d/%m/%Y',           # 15/01/2024
        '%B %d, %Y',          # January 15, 2024
        '%b %d, %Y',          # Jan 15, 2024
    ]
    
    for fmt in formats_to_try:
        try:
            dt = datetime.strptime(date_str.split('.')[0].split('+')[0], fmt)
            return dt.strftime('%m/%d/%Y')
        except (ValueError, AttributeError):
            continue
    
    # If no format worked, return original
    return date_str

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Redirect to login if not authenticated"""
    # Allow these paths without authentication
    public_paths = ["/login", "/static"]
    
    # Check if path is public
    is_public = any(request.url.path.startswith(path) for path in public_paths)
    
    if not is_public:
        if settings.enable_multi_user:
            session_token = request.cookies.get("session_token")
            if not session_token:
                return RedirectResponse(url="/login")
            from app.services.auth_service import validate_session
            if not validate_session(session_token):
                return RedirectResponse(url="/login")
    
    response = await call_next(request)
    return response
# Register the filter with Jinja2
# Find where templates is defined (should be near top of main.py)
# Add this line RIGHT AFTER: templates = Jinja2Templates(directory="app/templates")

templates.env.filters['format_date'] = format_date

# ======================================================================
# Startup: ensure late-added columns exist (sqlite ALTERs)
# ======================================================================
@app.on_event("startup")
def ensure_sqlite_columns():
    Base.metadata.create_all(bind=engine)
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        cols = {c["name"] for c in inspector.get_columns("cases")}
        with engine.begin() as conn:
            if "current_deed_path" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN current_deed_path TEXT DEFAULT ''"
                )
            if "previous_deed_path" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN previous_deed_path TEXT DEFAULT ''"
                )
            # Outstanding liens column (JSON stored as TEXT)
            if "outstanding_liens" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN outstanding_liens TEXT DEFAULT '[]'"
                )
            # Skip trace JSON cache
            if "skip_trace_json" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN skip_trace_json TEXT DEFAULT NULL"
                )
            if "rehab_condition" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN rehab_condition TEXT DEFAULT 'Good'"
                )
            if "property_overrides" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN property_overrides TEXT DEFAULT '{}'"
                )
            # V3: Add assigned_at column for claim tracking
            if "assigned_at" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE cases ADD COLUMN assigned_at DATETIME NULL"
                )
        
        # V3: Create case_claims table if not exists
        if "case_claims" not in tables:
            with engine.begin() as conn:
                conn.exec_driver_sql('''
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
                ''')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_claims_case_id ON case_claims(case_id)')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_claims_user_id ON case_claims(user_id)')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_claims_active ON case_claims(is_active)')
                logger.info("V3: Created case_claims table")
        
        # V3 Phase 4: Create invoices table if not exists
        if "invoices" not in tables:
            with engine.begin() as conn:
                conn.exec_driver_sql('''
                    CREATE TABLE IF NOT EXISTS invoices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        invoice_number TEXT UNIQUE NOT NULL,
                        invoice_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        due_date DATETIME NULL,
                        subtotal_cents INTEGER DEFAULT 0,
                        tax_cents INTEGER DEFAULT 0,
                        total_cents INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'pending',
                        stripe_invoice_id TEXT NULL,
                        stripe_payment_intent TEXT NULL,
                        stripe_hosted_url TEXT NULL,
                        paid_at DATETIME NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                ''')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_invoices_user_id ON invoices(user_id)')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date)')
                logger.info("V3: Created invoices table")
        
        # V3 Phase 4: Create invoice_lines table if not exists
        if "invoice_lines" not in tables:
            with engine.begin() as conn:
                conn.exec_driver_sql('''
                    CREATE TABLE IF NOT EXISTS invoice_lines (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        invoice_id INTEGER NOT NULL,
                        claim_id INTEGER NULL,
                        case_id INTEGER NULL,
                        description TEXT NOT NULL,
                        quantity INTEGER DEFAULT 1,
                        unit_price_cents INTEGER DEFAULT 0,
                        amount_cents INTEGER DEFAULT 0,
                        case_number TEXT NULL,
                        score_at_invoice INTEGER DEFAULT 0,
                        service_date DATETIME NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
                        FOREIGN KEY (claim_id) REFERENCES case_claims(id) ON DELETE SET NULL,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE SET NULL
                    )
                ''')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice ON invoice_lines(invoice_id)')
                logger.info("V3: Created invoice_lines table")
        
        # V3 Phase 5: Create webhook_logs table if not exists
        if "webhook_logs" not in tables:
            with engine.begin() as conn:
                conn.exec_driver_sql('''
                    CREATE TABLE IF NOT EXISTS webhook_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        result TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_webhook_logs_type ON webhook_logs(event_type)')
                conn.exec_driver_sql('CREATE INDEX IF NOT EXISTS idx_webhook_logs_date ON webhook_logs(created_at)')
                logger.info("V3: Created webhook_logs table")
        
        # V3: Add user billing columns if not exists
        if "users" in tables:
            user_cols = {c["name"] for c in inspector.get_columns("users")}
            with engine.begin() as conn:
                if "stripe_customer_id" not in user_cols:
                    conn.exec_driver_sql("ALTER TABLE users ADD COLUMN stripe_customer_id TEXT NULL")
                if "max_claims" not in user_cols:
                    conn.exec_driver_sql("ALTER TABLE users ADD COLUMN max_claims INTEGER DEFAULT 50")
                if "is_billing_active" not in user_cols:
                    conn.exec_driver_sql("ALTER TABLE users ADD COLUMN is_billing_active BOOLEAN DEFAULT 1")
        # Dockets table: add columns for uploaded files if missing
        if "dockets" in tables:
            docket_cols = {c["name"] for c in inspector.get_columns("dockets")}
            with engine.begin() as conn:
                if "file_name" not in docket_cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE dockets ADD COLUMN file_name TEXT DEFAULT ''"
                    )
                if "file_url" not in docket_cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE dockets ADD COLUMN file_url TEXT DEFAULT ''"
                    )
                if "description" not in docket_cols:
                    conn.exec_driver_sql(
                        "ALTER TABLE dockets ADD COLUMN description TEXT DEFAULT ''"
                    )
    except OperationalError:
        # first run or non-sqlite; ignore
        pass

# ========================================
# AUTHENTICATION ROUTES (Feature 9)
# ========================================

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Login page"""
    if not settings.enable_multi_user:
        return RedirectResponse(url="/cases", status_code=303)
    
    return templates.TemplateResponse("auth/login.html", {"request": request})


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Process login"""
    success, token, error = login_user(email, password)
    
    if not success:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": error},
            status_code=400,
        )
    
    # Set secure cookie
    response = RedirectResponse(url="/cases", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=settings.session_expire_minutes * 60,
    )
    
    return response


@app.get("/logout")
async def logout(request: Request):
    """Logout current user"""
    from app.services.auth_service import get_session_token
    from sqlalchemy import text
    
    token = get_session_token(request)
    
    if token:
        # Delete session from database
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM sessions WHERE token = :token"),
                {"token": token}
            )
    
    # Redirect to login and clear cookie
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.get("/profile", response_class=HTMLResponse)
async def user_profile(request: Request):
    """User profile page"""
    from app.services.auth_service import get_session_token, validate_session
    from sqlalchemy import text
    
    token = get_session_token(request)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    
    user_id = validate_session(token)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    
    # Get user from database
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, email, full_name, role FROM users WHERE id = :id"),
            {"id": user_id}
        ).fetchone()
    
    if not result:
        return RedirectResponse(url="/login", status_code=303)
    
    user = {
        "id": result[0],
        "email": result[1],
        "full_name": result[2],
        "role": result[3]
    }
    
    return templates.TemplateResponse(
        "auth/profile.html",
        {"request": request, "user": user}
    )
    
    # If multi-user is enabled, try to get current user
    try:
        from app.services.auth_service import get_current_user
        user = get_current_user(request)
        
        return templates.TemplateResponse(
            "auth/profile.html",
            {"request": request, "user": user}
        )
    except Exception as e:
        # Not logged in or error
        return RedirectResponse(url="/login", status_code=303)


# ========================================
# COMPARABLES ANALYSIS (Feature 7)
# ========================================





# ========================================
# DOCUMENT OCR (Feature 11)
# ========================================



# ========================================
# BULK OPERATIONS
# ========================================





# ========================================
# ADMIN ROUTES (Feature 9)
# ========================================

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_list(
    request: Request,
    user: dict = Depends(require_role(["admin"])),
):
    """Admin: List all users"""
    with engine.connect() as conn:
        users = conn.execute(
            text("""
                SELECT id, email, full_name, role, is_active, created_at, last_login
                FROM users
                ORDER BY created_at DESC
            """)
        ).mappings().fetchall()
    
    return templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "user": user, "users": [dict(u) for u in users]}
    )


@app.post("/admin/users/create")
def admin_create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    user: dict = Depends(require_role(["admin"])),
):
    """Admin: Create new user"""
    try:
        user_id = create_user(email, password, full_name, role)
        return RedirectResponse(url="/admin/users", status_code=303)
    except ValueError as exc:
        return templates.TemplateResponse(
            "admin/users.html",
            {"request": request, "error": str(exc)},
            status_code=400,
        )


@app.post("/admin/users/update")
def admin_update_user(
    request: Request,
    user_id: int = Form(...),
    email: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(None),
    role: str = Form(...),
    is_active: str = Form(None),
    user: dict = Depends(require_role(["admin"])),
):
    """Admin: Update user"""
    try:
        with engine.connect() as conn:
            if password:
                hashed = get_password_hash(password)
                conn.execute(
                    text("""
                        UPDATE users 
                        SET email = :email, full_name = :full_name, password_hash = :password,
                            role = :role, is_active = :is_active
                        WHERE id = :user_id
                    """),
                    {
                        "user_id": user_id,
                        "email": email,
                        "full_name": full_name,
                        "password": hashed,
                        "role": role,
                        "is_active": is_active == "1",
                    }
                )
            else:
                conn.execute(
                    text("""
                        UPDATE users 
                        SET email = :email, full_name = :full_name, role = :role, is_active = :is_active
                        WHERE id = :user_id
                    """),
                    {
                        "user_id": user_id,
                        "email": email,
                        "full_name": full_name,
                        "role": role,
                        "is_active": is_active == "1",
                    }
                )
            conn.commit()
        return RedirectResponse(url="/admin/users", status_code=303)
    except Exception as exc:
        return templates.TemplateResponse(
            "admin/users.html",
            {"request": request, "error": str(exc)},
            status_code=400,
        )


@app.post("/admin/users/toggle")
def admin_toggle_user(
    request: Request,
    user_id: int = Form(...),
    is_active: str = Form(...),
    user: dict = Depends(require_role(["admin"])),
):
    """Admin: Toggle user active status"""
    try:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE users SET is_active = :is_active WHERE id = :user_id"),
                {"user_id": user_id, "is_active": is_active == "1"}
            )
            conn.commit()
        return RedirectResponse(url="/admin/users", status_code=303)
    except Exception as exc:
        return RedirectResponse(url="/admin/users", status_code=303)


# ========================================
# STARTUP EVENT
# ========================================

@app.on_event("startup")
def startup_event():
    """Initialize multi-user system on startup"""
    from app.services.auth_service import create_default_admin
    
    if settings.enable_multi_user:
        create_default_admin()

# --------------------------------------------------------
#  SKIP TRACE NORMALIZED TABLE (CREATE ON STARTUP)
# --------------------------------------------------------
@app.on_event("startup")
# --------------------------------------------------------
#  SKIP TRACE NORMALIZED TABLES (CREATE ON STARTUP)
# --------------------------------------------------------
@app.on_event("startup")
def ensure_skiptrace_tables():
    """
    Ensure the skip-trace tables exist:

      - case_skiptrace         (1 row per case: owner + property address)
      - case_skiptrace_phone   (N rows per case: all phones)
      - case_skiptrace_email   (N rows per case: all emails)
    """
    try:
        with engine.begin() as conn:
            # Base summary table (leave existing extra columns alone if already created)
            conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS case_skiptrace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id INTEGER NOT NULL UNIQUE,
                    owner_name TEXT,
                    prop_street TEXT,
                    prop_city TEXT,
                    prop_state TEXT,
                    prop_zip TEXT,
                    FOREIGN KEY(case_id) REFERENCES cases(id)
                )
                """
            )

            # Phones: one row per phone record
            conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS case_skiptrace_phone (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id INTEGER NOT NULL,
                    number TEXT,
                    type TEXT,
                    carrier TEXT,
                    last_reported TEXT,
                    score INTEGER,
                    tested INTEGER,
                    reachable INTEGER,
                    dnc INTEGER,
                    FOREIGN KEY(case_id) REFERENCES cases(id)
                )
                """
            )

            # Emails: one row per email record
            conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS case_skiptrace_email (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id INTEGER NOT NULL,
                    email TEXT,
                    tested INTEGER,
                    FOREIGN KEY(case_id) REFERENCES cases(id)
                )
                """
            )
    except OperationalError:
        # sqlite / first run quirks; ignore
        pass
    except Exception as exc:
        logger.warning("Failed to ensure skip-trace tables: %s", exc)

# --------------------------------------------------------
#  SESSION TABLE (CREATE ON STARTUP)
# --------------------------------------------------------
@app.on_event("startup")
def ensure_sessions_table():
    """
    Ensure the sessions table exists for token-based auth.
    """
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)"
            )
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)"
            )
    except Exception as exc:
        logger.warning("Failed to ensure sessions table: %s", exc)
# --------------------------------------------------------
#  PROPERTY LOOKUP TABLE (CREATE ON STARTUP)
# --------------------------------------------------------
# --------------------------------------------------------
#  PROPERTY DETAIL TABLE (CREATE/MIGRATE ON STARTUP)
# --------------------------------------------------------
@app.on_event("startup")
def ensure_property_table():
    """
    Ensure case_property exists with all expected columns.
    If the table already exists (older schema), add any missing columns.
    """
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        desired_ddl = """
            CREATE TABLE IF NOT EXISTS case_property (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id INTEGER NOT NULL UNIQUE,

                -- BatchData property id
                batch_property_id TEXT,

                -- Address block
                address_validity        TEXT,
                address_house_number    TEXT,
                address_street          TEXT,
                address_city            TEXT,
                address_county          TEXT,
                address_state           TEXT,
                address_zip             TEXT,
                address_zip_plus4       TEXT,
                address_latitude        REAL,
                address_longitude       REAL,
                address_county_fips     TEXT,
                address_hash            TEXT,

                -- Demographics block
                demo_age                     INTEGER,
                demo_household_size          INTEGER,
                demo_income                  INTEGER,
                demo_net_worth               INTEGER,
                demo_discretionary_income    INTEGER,
                demo_homeowner_renter_code   TEXT,
                demo_homeowner_renter        TEXT,
                demo_gender_code             TEXT,
                demo_gender                  TEXT,
                demo_child_count             INTEGER,
                demo_has_children            INTEGER,
                demo_marital_status_code     TEXT,
                demo_marital_status          TEXT,
                demo_single_parent           INTEGER,
                demo_religious               INTEGER,
                demo_religious_affil_code    TEXT,
                demo_religious_affil         TEXT,
                demo_education_code          TEXT,
                demo_education               TEXT,
                demo_occupation              TEXT,
                demo_occupation_code         TEXT,

                -- Foreclosure block
                fc_status_code          TEXT,
                fc_status               TEXT,
                fc_recording_date       TEXT,
                fc_filing_date          TEXT,
                fc_case_number          TEXT,
                fc_auction_date         TEXT,
                fc_auction_time         TEXT,
                fc_auction_location     TEXT,
                fc_auction_city         TEXT,
                fc_auction_min_bid      REAL,
                fc_document_number      TEXT,
                fc_book_number          TEXT,
                fc_page_number          TEXT,
                fc_document_type_code   TEXT,
                fc_document_type        TEXT,

                -- Full deed history + full payload backup
                deed_history_json   TEXT,
                raw_json            TEXT,

                created_at          TEXT,
                updated_at          TEXT,

                FOREIGN KEY(case_id) REFERENCES cases(id)
            )
        """

        with engine.begin() as conn:
            # 1) Create table if it doesn't exist at all
            if "case_property" not in tables:
                conn.exec_driver_sql(desired_ddl)
                return

            # 2) If it DOES exist (older version), add missing columns
            existing_cols = {c["name"] for c in inspector.get_columns("case_property")}

            columns_to_add = [
                ("batch_property_id", "TEXT"),
                ("address_validity", "TEXT"),
                ("address_house_number", "TEXT"),
                ("address_street", "TEXT"),
                ("address_city", "TEXT"),
                ("address_county", "TEXT"),
                ("address_state", "TEXT"),
                ("address_zip", "TEXT"),
                ("address_zip_plus4", "TEXT"),
                ("address_latitude", "REAL"),
                ("address_longitude", "REAL"),
                ("address_county_fips", "TEXT"),
                ("address_hash", "TEXT"),
                ("demo_age", "INTEGER"),
                ("demo_household_size", "INTEGER"),
                ("demo_income", "INTEGER"),
                ("demo_net_worth", "INTEGER"),
                ("demo_discretionary_income", "INTEGER"),
                ("demo_homeowner_renter_code", "TEXT"),
                ("demo_homeowner_renter", "TEXT"),
                ("demo_gender_code", "TEXT"),
                ("demo_gender", "TEXT"),
                ("demo_child_count", "INTEGER"),
                ("demo_has_children", "INTEGER"),
                ("demo_marital_status_code", "TEXT"),
                ("demo_marital_status", "TEXT"),
                ("demo_single_parent", "INTEGER"),
                ("demo_religious", "INTEGER"),
                ("demo_religious_affil_code", "TEXT"),
                ("demo_religious_affil", "TEXT"),
                ("demo_education_code", "TEXT"),
                ("demo_education", "TEXT"),
                ("demo_occupation", "TEXT"),
                ("demo_occupation_code", "TEXT"),
                ("fc_status_code", "TEXT"),
                ("fc_status", "TEXT"),
                ("fc_recording_date", "TEXT"),
                ("fc_filing_date", "TEXT"),
                ("fc_case_number", "TEXT"),
                ("fc_auction_date", "TEXT"),
                ("fc_auction_time", "TEXT"),
                ("fc_auction_location", "TEXT"),
                ("fc_auction_city", "TEXT"),
                ("fc_auction_min_bid", "REAL"),
                ("fc_document_number", "TEXT"),
                ("fc_book_number", "TEXT"),
                ("fc_page_number", "TEXT"),
                ("fc_document_type_code", "TEXT"),
                ("fc_document_type", "TEXT"),
                ("deed_history_json", "TEXT"),
                ("raw_json", "TEXT"),
                ("created_at", "TEXT"),
                ("updated_at", "TEXT"),
            ]

            for col_name, col_type in columns_to_add:
                if col_name not in existing_cols:
                    conn.exec_driver_sql(
                        f"ALTER TABLE case_property ADD COLUMN {col_name} {col_type}"
                    )
    except Exception as exc:
        logger.warning("Failed to ensure/migrate case_property table: %s", exc)



# ======================================================================
# Helpers: shell runner + scraper glue
# ======================================================================


# ======================================================================
# Routes: home, list, detail
# ======================================================================
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/cases", status_code=303)


# V3: User billing page
@app.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, db: Session = Depends(get_db)):
    """User billing page showing claims and invoices."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    return templates.TemplateResponse("billing.html", {
        "request": request,
        "current_user": user,
    })


# V3: Invoice view page
@app.get("/billing/invoice/{invoice_id}", response_class=HTMLResponse)
def invoice_view_page(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    """View a specific invoice."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    user_id = user.get("id") if isinstance(user, dict) else user.id
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    
    from app.services.billing_service import get_invoice_details
    
    invoice_data = get_invoice_details(db, invoice_id)
    
    if not invoice_data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Check access - user can only see their own invoices (unless admin)
    if not is_admin and invoice_data.get("user", {}).get("id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return templates.TemplateResponse("invoice_view.html", {
        "request": request,
        "current_user": user,
        "invoice": invoice_data,
    })


# V3: Payment method setup page
@app.get("/billing/payment-method", response_class=HTMLResponse)
def payment_method_page(request: Request, db: Session = Depends(get_db)):
    """Payment method setup page."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    return templates.TemplateResponse("payment_method.html", {
        "request": request,
        "current_user": user,
    })


# V3: Admin billing dashboard
@app.get("/admin/billing", response_class=HTMLResponse)
def admin_billing_dashboard(request: Request, db: Session = Depends(get_db)):
    """Admin billing dashboard page."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return templates.TemplateResponse("admin_billing.html", {
        "request": request,
        "current_user": user,
    })


# V3: Admin claims management
@app.get("/admin/claims", response_class=HTMLResponse)
def admin_claims_management(request: Request, db: Session = Depends(get_db)):
    """Admin claims management page."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return templates.TemplateResponse("admin_claims.html", {
        "request": request,
        "current_user": user,
    })


# V3: Admin reports (redirects to billing for now)
@app.get("/admin/reports", response_class=HTMLResponse)
def admin_reports_page(request: Request):
    """Admin reports page - redirects to billing dashboard."""
    return RedirectResponse(url="/admin/billing", status_code=303)










# ======================================================================
# Case document uploads
# ======================================================================

















# ======================================================================
# NEW: Outstanding Liens API
# ======================================================================




# ======================================================================
# Simple health check
# ======================================================================
@app.get("/healthz")
def healthz():
    """Basic liveness + DB connectivity check."""
    db_ok = True
    err = None
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception as exc:
        db_ok = False
        err = str(exc)
    return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "error", "error": err}


# =====================
# START: Added in v1.05+ for Archive + Export + Search
# =====================
@app.on_event("startup")
def _ensure_archived_column():
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("cases")}
        if "archived" not in cols:
            with engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE cases ADD COLUMN archived INTEGER DEFAULT 0")
    except Exception as e:
        logger.warning("Could not ensure 'archived' column: %s", e)











# =====================
# v1.07 Additions — Unarchive + AJAX endpoints
# =====================
@app.on_event("startup")
def _ensure_archived_column_v107():
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("cases")}
        if "archived" not in cols:
            with engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE cases ADD COLUMN archived INTEGER DEFAULT 0")
    except Exception as e:
        logger.warning("Could not ensure 'archived' column: %s", e)









@app.get("/debug/owners/{case_id}")
async def debug_owners(
    case_id: int,
    user: "User" = Depends(get_current_user)
):
    """
    Debug owner data structure - ADMIN ONLY
    
    Security: This endpoint exposes sensitive property owner data
    and should only be accessible to administrators for debugging.
    """
    from app.services.skiptrace_service import load_property_for_case
    from app.auth import User
    import json
    
    # Require admin access
    if not user.is_admin:
        raise HTTPException(
            status_code=403, 
            detail="Admin access required for debug endpoints"
        )
    
    property_payload = load_property_for_case(case_id)
    
    if not property_payload or not property_payload.get("results"):
        return {"error": "No property data"}
    
    props = property_payload["results"].get("properties", [])
    if not props:
        return {"error": "No properties in results"}
    
    prop = props[0]
    
    # Get raw owner data
    owner_raw = prop.get("owner")
    
    # Parse it
    parsed = parse_property_data(property_payload)
    
    return {
        "step1_raw_owner_type": str(type(owner_raw).__name__),
        "step2_raw_owner_data": owner_raw,
        "step3_is_list": isinstance(owner_raw, list),
        "step4_is_dict": isinstance(owner_raw, dict),
        "step5_if_dict_fullName": owner_raw.get("fullName") if isinstance(owner_raw, dict) else None,
        "step6_if_dict_has_semicolon": ";" in str(owner_raw.get("fullName", "")) if isinstance(owner_raw, dict) else False,
        "step7_parsed_owners_count": len(parsed.get("owners", [])) if parsed else 0,
        "step8_parsed_owners": parsed.get("owners") if parsed else None,
        "step9_full_parsed_data": parsed,
    }
@app.on_event("shutdown")
async def shutdown():
    await shutdown_progress_bus()
# =====================
# Manual Add Case (v1.08)
# =====================
# (placeholder for future additions)

