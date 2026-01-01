from __future__ import annotations
from datetime import datetime
from app.config import settings
from app.services.auth_service import get_current_user
# ---- Windows event loop fix for asyncio subprocess ----
import sys as _sys
import asyncio as _asyncio
if _sys.platform == "win32":
    try:
        _asyncio.set_event_loop_policy(_asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# ---------------- Stdlib ----------------
import logging
import asyncio
import csv as _csv
import datetime as _dt
import os
import sys
import tempfile
import uuid
import io, json
import zipfile
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus
import re
from tools.import_pasco_csv import main as import_pasco_csv_main
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
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------- DB / ORM ----------------
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text, bindparam, func
from sqlalchemy.exc import OperationalError

# ---------------- App imports ----------------
from app.services.progress_bus import progress_bus
#from app.settings import settings
from .database import Base, engine, SessionLocal
from .models import Case, Defendant, Docket, Note
from .utils import ensure_case_folder, compute_offer_70, compute_offer_80
from .schemas import OutstandingLien, OutstandingLiensUpdate
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

from app.services.report_service import generate_case_report, build_case_report_bytes
from app.services.update_cases_service import run_update_cases_job
from app.services.update_cases_service import LAST_UPDATE_STATUS

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
    key = settings.GOOGLE_MAPS_API_KEY
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
# ANALYTICS DASHBOARD (Feature 10)
# ========================================

@app.get("/dashboard", response_class=HTMLResponse)
def analytics_dashboard(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Analytics dashboard page"""
    if not settings.enable_analytics:
        return RedirectResponse(url="/cases", status_code=303)
    
    metrics = get_dashboard_metrics()
    monthly_data = get_cases_by_month(months=12)
    funnel = get_conversion_funnel()
    roi = get_roi_analysis()
    opportunities = get_top_opportunities(limit=10)
    
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "metrics": metrics,
            "monthly_data": monthly_data,
            "funnel": funnel,
            "roi": roi,
            "opportunities": opportunities,
        }
    )


@app.get("/api/dashboard/metrics")
def api_dashboard_metrics(user: dict = Depends(get_current_user)):
    """API endpoint for dashboard metrics (for AJAX refresh)"""
    return get_dashboard_metrics()


# ========================================
# COMPARABLES ANALYSIS (Feature 7)
# ========================================

@app.post("/cases/{case_id}/fetch-comparables")
async def fetch_comparables(
    case_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Fetch comparable sales for a case"""
    if not settings.enable_comparables:
        raise HTTPException(status_code=400, detail="Comparables feature not enabled")
    
    # Load case
    case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # Get property data for address and details
    from app.services.skiptrace_service import (
        get_case_address_components,
        load_property_for_case,
    )
    
    street, city, state, postal = get_case_address_components(case)
    property_payload = load_property_for_case(case_id)
    
    # Extract lat/lon and sqft if available
    lat = lon = sqft = beds = baths = None
    if property_payload:
        props = (property_payload.get("results") or {}).get("properties") or []
        if props:
            prop = props[0]
            addr = prop.get("address") or {}
            building = prop.get("building") or {}
            
            lat = addr.get("latitude")
            lon = addr.get("longitude")
            sqft = building.get("livingAreaSqft")
            beds = building.get("bedrooms")
            baths = building.get("totalBathrooms")
    
    # Fetch comparables
    try:
        result = fetch_and_save_comparables(
            case_id=case_id,
            case_data={
                "street": street,
                "city": city,
                "state": state,
                "postal_code": postal,
                "lat": lat,
                "lon": lon,
                "sqft": sqft,
                "beds": beds,
                "baths": baths,
            }
        )
        
        return {"success": True, "data": result}
    
    except Exception as exc:
        logger.error(f"Failed to fetch comparables for case {case_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/cases/{case_id}/comparables", response_class=HTMLResponse)
def view_comparables(
    request: Request,
    case_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """View comparables page for a case"""
    case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    comparables = load_comparables_from_db(case_id)
    
    # Calculate suggested ARV if comparables exist
    suggested_arv = low_est = high_est = None
    if comparables:
        # Get subject property details
        from app.services.skiptrace_service import load_property_for_case
        property_payload = load_property_for_case(case_id)
        
        sqft = beds = baths = None
        if property_payload:
            props = (property_payload.get("results") or {}).get("properties") or []
            if props:
                building = props[0].get("building") or {}
                sqft = building.get("livingAreaSqft")
                beds = building.get("bedrooms")
                baths = building.get("totalBathrooms")
        
        suggested_arv, low_est, high_est = calculate_suggested_arv(
            comparables, sqft, beds, baths
        )
    
    return templates.TemplateResponse(
        "cases/comparables.html",
        {
            "request": request,
            "user": user,
            "case": case,
            "comparables": comparables,
            "suggested_arv": suggested_arv,
            "low_estimate": low_est,
            "high_estimate": high_est,
        }
    )


# ========================================
# DOCUMENT OCR (Feature 11)
# ========================================

@app.post("/cases/{case_id}/documents/{doc_type}/ocr")
async def process_document_ocr_endpoint(
    case_id: int,
    doc_type: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Trigger OCR processing for a document"""
    if not settings.enable_ocr:
        raise HTTPException(status_code=400, detail="OCR feature not enabled")
    
    case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # Get document path
    doc_map = {
        "verified": "verified_complaint_path",
        "mortgage": "mortgage_path",
        "current_deed": "current_deed_path",
        "previous_deed": "previous_deed_path",
    }
    
    attr_name = doc_map.get(doc_type)
    if not attr_name:
        raise HTTPException(status_code=400, detail="Invalid document type")
    
    doc_path = getattr(case, attr_name, None)
    if not doc_path:
        raise HTTPException(status_code=404, detail="Document not found")
    
    full_path = UPLOAD_ROOT / doc_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Document file not found")
    
    # Check if Celery is available for background processing
    if settings.is_celery_enabled:
        from app.celery_app import process_document_ocr
        task = process_document_ocr.delay(case_id, str(full_path), doc_type)
        return {"success": True, "task_id": task.id, "status": "processing"}
    else:
        # Process synchronously
        result = extract_document_data(str(full_path), doc_type)
        populated = auto_populate_case_from_ocr(case_id, result)
        
        return {
            "success": True,
            "extracted_fields": len(result.get("structured_data", {})),
            "auto_populated": populated,
            "data": result["structured_data"],
        }


# ========================================
# BULK OPERATIONS
# ========================================

@app.post("/cases/bulk/skip-trace")
async def bulk_skip_trace_endpoint(
    background_tasks: BackgroundTasks,
    ids: List[int] = Form(default=[]),
    user: dict = Depends(get_current_user),
):
    """Bulk skip trace multiple cases"""
    if not ids:
        return RedirectResponse(url="/cases", status_code=303)
    
    # Check if Celery is available
    if settings.is_celery_enabled:
        from app.celery_app import bulk_skip_trace
        task = bulk_skip_trace.delay(ids)
        return RedirectResponse(
            url=f"/tasks/{task.id}",
            status_code=303
        )
    else:
        # Fallback: process in background task (limited)
        job_id = uuid.uuid4().hex
        
        async def run_bulk_skip():
            from app.services.skiptrace_service import (
                get_case_address_components,
                batchdata_skip_trace,
                save_skiptrace_row,
            )
            db = SessionLocal()
            try:
                for case_id in ids:
                    try:
                        case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
                        if not case:
                            continue
                        
                        street, city, state, postal = get_case_address_components(case)
                        skip_data = batchdata_skip_trace(street, city, state, postal)
                        save_skiptrace_row(case.id, skip_data)
                        
                    except Exception as exc:
                        logger.error(f"Bulk skip trace failed for case {case_id}: {exc}")
            finally:
                db.close()
        
        background_tasks.add_task(run_bulk_skip)
        return RedirectResponse(url="/cases", status_code=303)


@app.post("/cases/bulk/property-lookup")
async def bulk_property_lookup_endpoint(
    background_tasks: BackgroundTasks,
    ids: List[int] = Form(default=[]),
    user: dict = Depends(get_current_user),
):
    """Bulk property lookup for multiple cases"""
    if not ids:
        return RedirectResponse(url="/cases", status_code=303)
    
    if settings.is_celery_enabled:
        from app.celery_app import bulk_property_lookup
        task = bulk_property_lookup.delay(ids)
        return RedirectResponse(url=f"/tasks/{task.id}", status_code=303)
    else:
        # Process in background
        job_id = uuid.uuid4().hex
        
        async def run_bulk_lookup():
            from app.services.skiptrace_service import (
                get_case_address_components,
                batchdata_property_lookup_all_attributes,
                save_property_for_case,
            )
            db = SessionLocal()
            try:
                for case_id in ids:
                    try:
                        case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
                        if not case:
                            continue
                        
                        street, city, state, postal = get_case_address_components(case)
                        prop_data = batchdata_property_lookup_all_attributes(street, city, state, postal)
                        save_property_for_case(case.id, prop_data)
                        
                    except Exception as exc:
                        logger.error(f"Bulk property lookup failed for case {case_id}: {exc}")
            finally:
                db.close()
        
        background_tasks.add_task(run_bulk_lookup)
        return RedirectResponse(url="/cases", status_code=303)


# ========================================
# TASK STATUS (for Celery)
# ========================================

@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_status(request: Request, task_id: str):
    """Check status of a background task"""
    if not settings.is_celery_enabled:
        return templates.TemplateResponse(
            "task_status.html",
            {"request": request, "error": "Background tasks not enabled"}
        )
    
    from app.celery_app import celery_app
    from celery.result import AsyncResult
    
    task = AsyncResult(task_id, app=celery_app)
    
    return templates.TemplateResponse(
        "task_status.html",
        {
            "request": request,
            "task_id": task_id,
            "status": task.state,
            "result": task.result if task.ready() else None,
        }
    )


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


@app.get("/cases/new", response_class=HTMLResponse)
def new_case_form(request: Request):
    return templates.TemplateResponse("cases_new.html", {"request": request, "error": None})

@app.get("/update_cases/status")
async def get_update_cases_status():
    """
    Returns the last UpdateCases job status:
      {
        "last_run": "2025-12-10T03:00:00",
        "success": true/false/null,
        "since_days": 1,
        "message": "Import complete. added=..., updated=..., skipped=..."
      }
    """
    return LAST_UPDATE_STATUS

@app.post("/cases/create")
def create_case(
    request: Request,
    case_number: str = Form(...),
    filing_date: Optional[str] = Form(None),   # "YYYY-MM-DD" or blank
    style: Optional[str] = Form(None),
    parcel_id: Optional[str] = Form(None),
    address_override: Optional[str] = Form(None),
    arv: Optional[str] = Form(None),
    rehab: Optional[str] = Form(None),
    rehab_condition: Optional[str] = Form(None),
    closing_costs: Optional[str] = Form(None),
    defendants_csv: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    # helpers
    def _num(x: Optional[str]) -> Optional[float]:
        if x is None:
            return None
        s = x.strip()
        if not s:
            return None
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None

    cn = (case_number or "").strip()
    if not cn:
        return templates.TemplateResponse("cases_new.html", {"request": request, "error": "Case # is required."})

    # Duplicate check
    exists = db.query(Case).filter(Case.case_number == cn).one_or_none()
    if exists:
        return templates.TemplateResponse("cases_new.html", {"request": request, "error": f"Case {cn} already exists (ID {exists.id})."})

    # Create case
    case = Case(case_number=cn)
    if filing_date:
        case.filing_datetime = filing_date.strip()
    if style:
        case.style = style.strip()
    if parcel_id:
        case.parcel_id = parcel_id.strip()
    if address_override:
        case.address_override = address_override.strip()

    # only set if provided
    v_arv = _num(arv)
    v_rehab = _num(rehab)
    v_cc = _num(closing_costs)
    if v_arv is not None:
        case.arv = v_arv
    if v_rehab is not None:
        case.rehab = v_rehab
    if rehab_condition:
        case.rehab_condition = rehab_condition.strip()
    if v_cc is not None:
        case.closing_costs = v_cc

    db.add(case)
    db.flush()

    if defendants_csv:
        raw = defendants_csv.replace("\r", "\n")
        parts = [p.strip() for chunk in raw.split("\n") for p in chunk.split(",")]
        seen = set()
        for name in parts:
            if name and name not in seen:
                seen.add(name)
                db.add(Defendant(case_id=case.id, name=name))

    db.commit()
    return RedirectResponse(url=f"/cases/{case.id}", status_code=303)

@app.post("/cases/{case_id}/update", response_class=HTMLResponse)
def update_case_fields(
    request: Request,
    case_id: int,
    parcel_id: Optional[str] = Form(None),
    address_override: Optional[str] = Form(None),
    arv: Optional[str] = Form(None),
    rehab: Optional[str] = Form(None),
    rehab_condition: Optional[str] = Form(None),
    closing_costs: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    # Load case
    getter = getattr(db, "get", None)
    if callable(getter):
        case = db.get(Case, case_id)
    else:
        case = db.query(Case).get(case_id)  # type: ignore[call-arg]

    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Helper to parse numbers (same logic as in create_case)
    def _num(x: Optional[str]) -> Optional[float]:
        if x is None:
            return None
        s = x.strip()
        if not s:
            return None
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return None

    # Text fields
    if parcel_id:
        case.parcel_id = parcel_id.strip()
    if address_override:
        case.address_override = address_override.strip()

    # Numbers (only set if provided)
    v_arv = _num(arv)
    v_rehab = _num(rehab)
    v_cc = _num(closing_costs)

    if v_arv is not None:
        case.arv = v_arv
    if v_rehab is not None:
        case.rehab = v_rehab
    if rehab_condition:
        case.rehab_condition = rehab_condition.strip()
    if v_cc is not None:
        case.closing_costs = v_cc

    db.add(case)
    db.commit()

    # Send user back to the case detail page
    return RedirectResponse(
        url=request.url_for("case_detail", case_id=case.id),
        status_code=303,
    )

@app.get("/cases/{case_id}", response_class=HTMLResponse)
def case_detail(request: Request, case_id: int, db: Session = Depends(get_db)):
    """Case detail page with all tabs"""
    
    # Get case
    getter = getattr(db, "get", None)
    if callable(getter):
        case = db.get(Case, case_id)
    else:
        case = db.query(Case).get(case_id)

    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Get notes
    notes = (
        db.query(Note)
        .filter(Note.case_id == case_id)
        .order_by(Note.id.desc())
        .all()
    )
    
    try:
        setattr(case, "notes", notes)
    except Exception:
        pass

    # Load skip trace data
    skip_trace = None
    skip_trace_error = None
    try:
        from app.services.skiptrace_service import load_skiptrace_for_case
        skip_trace = load_skiptrace_for_case(case_id)
    except Exception as e:
        logger.error(f"Error loading skip trace for case {case_id}: {e}")
        skip_trace_error = str(e)

    # Load property data
    property_payload = None
    property_data = None
    has_property_data = False
    
    try:
        from app.services.skiptrace_service import load_property_for_case
        property_payload = load_property_for_case(case_id)
        
        if property_payload:
            has_property_data = True
            property_data = parse_property_data(property_payload)
            
    except Exception as e:
        logger.error(f"Error loading property for case {case_id}: {e}")

    # === Financial Calculations ===
    arv = float(case.arv) if case.arv else 0.0
    rehab = float(case.rehab) if case.rehab else 0.0
    rehab_condition = case.rehab_condition or "Good"
    
    rehab_year_built = None
    rehab_sqft = None
    rehab_suggested = None
    
    if property_payload and property_payload.get("results"):
        props = property_payload["results"].get("properties", [])
        if props:
            p = props[0]
            building = p.get("building", {})
            rehab_year_built = building.get("yearBuilt")
            rehab_sqft = building.get("livingAreaSqft")
            
            if rehab_sqft and rehab_year_built:
                import datetime
                current_year = datetime.datetime.now().year
                age = current_year - rehab_year_built
                
                condition_multipliers = {
                    "Poor": 50,
                    "Fair": 30,
                    "Good": 15,
                    "Excellent": 5,
                }
                base_cost = condition_multipliers.get(rehab_condition, 15)
                
                if age > 50:
                    base_cost *= 1.5
                elif age > 30:
                    base_cost *= 1.2
                
                rehab_suggested = rehab_sqft * base_cost

    closing_input = float(case.closing_costs) if case.closing_costs else None
    if closing_input:
        closing = closing_input
    else:
        closing = arv * 0.045 if arv > 0 else 0.0

    wholesale_offer = 0.0
    flip_offer = 0.0
    
    if arv > 0:
        wholesale_offer = (arv * 0.65) - rehab - closing
        
        if arv < 350000:
            flip_multiplier = 0.80
        else:
            flip_multiplier = 0.85
        
        flip_offer = (arv * flip_multiplier) - rehab - closing

    import json
    liens_list = []
    try:
        if case.outstanding_liens:
            liens_data = json.loads(case.outstanding_liens)
            if isinstance(liens_data, list):
                liens_list = liens_data
    except Exception as e:
        logger.error(f"Error parsing liens for case {case_id}: {e}")

    defendants = []
    try:
        defendants = (
            db.query(Defendant)
            .filter(Defendant.case_id == case_id)
            .all()
        )
    except Exception as e:
        logger.error(f"Error loading defendants for case {case_id}: {e}")

    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "notes": notes,
            "defendants": defendants,
            "skip_trace": skip_trace,
            "skip_trace_error": skip_trace_error,
            "property_payload": property_payload,
            "property_data": property_data,
            "has_property_data": has_property_data,
            "liens_list": liens_list,
            "arv": arv,
            "rehab": rehab,
            "rehab_condition": rehab_condition,
            "rehab_year_built": rehab_year_built,
            "rehab_sqft": rehab_sqft,
            "rehab_suggested": rehab_suggested,
            "closing": closing,
            "closing_input": closing_input,
            "wholesale_offer": wholesale_offer,
            "flip_offer": flip_offer,
            "format_phone": lambda x: format_phone(x) if x else "N/A",
            "yn_icon": lambda x: "✓" if x else "✗",
        },
    )

# NEW: Skip trace endpoint using BatchData
@app.post("/cases/{case_id}/skip-trace", response_class=HTMLResponse)
def skip_trace_case(request: Request, case_id: int, db: Session = Depends(get_db)):
    # Load case
    getter = getattr(db, "get", None)
    if callable(getter):
        case = db.get(Case, case_id)
    else:
        case = db.query(Case).get(case_id)  # type: ignore[call-arg]

    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Notes
    notes = (
        db.query(Note)
        .filter(Note.case_id == case_id)
        .order_by(Note.id.desc())
        .all()
    )

    skip_trace: Optional[dict] = None
    skip_trace_error: Optional[str] = None

    # 1) Try table-based cache first
    skip_trace = load_skiptrace_for_case(case_id)

    # 2) If no stored data, call BatchData and persist normalized row
    if skip_trace is None:
        street, city, state, postal_code = get_case_address_components(case)

        try:
            skip_trace = batchdata_skip_trace(street, city, state, postal_code)
            # Save normalized into case_skiptrace
            save_skiptrace_row(case.id, skip_trace)
            # (optional) also keep JSON cache if you still want it:
            # set_cached_skip_trace(case_id, skip_trace)
        except HTTPException as exc:
            detail = exc.detail
            skip_trace_error = detail if isinstance(detail, str) else str(detail)
        except Exception as exc:
            skip_trace_error = f"Unexpected error during skip trace: {exc}"

    offer = compute_offer_70(case.arv or 0, case.rehab or 0, case.closing_costs or 0)
    rehab_condition = getattr(case, "rehab_condition", "") or "Good"
    property_overrides = _parse_property_overrides(case)
    rehab_suggested = _estimate_rehab_from_property(
        load_property_for_case(case_id), rehab_condition, property_overrides
    )
    flip_offer = compute_offer_80(case.arv or 0, case.rehab or 0, case.closing_costs or 0)

    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "offer_70": offer,
            "offer_80": flip_offer,
            "active_parcel_id": case.parcel_id,
            "notes": notes,
            "skip_trace": skip_trace,
            "skip_trace_error": skip_trace_error,
            "property_data": load_property_for_case(case_id),
            "property_error": None,
            "rehab_condition": rehab_condition,
            "rehab_suggested": rehab_suggested,
            "property_overrides": property_overrides,
        },
    )

@app.post("/cases/{case_id}/property-lookup", response_class=HTMLResponse)
def property_lookup_case(request: Request, case_id: int, db: Session = Depends(get_db)):
    # Load case
    getter = getattr(db, "get", None)
    if callable(getter):
        case = db.get(Case, case_id)
    else:
        case = db.query(Case).get(case_id)  # type: ignore[call-arg]

    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Notes
    notes = (
        db.query(Note)
        .filter(Note.case_id == case_id)
        .order_by(Note.id.desc())
        .all()
    )
    try:
        setattr(case, "notes", notes)
    except Exception:
        pass

    # Existing skip trace (unchanged)
    skip_trace = load_skiptrace_for_case(case_id)
    skip_trace_error: Optional[str] = None

    # Property lookup
    property_data: Optional[dict] = None
    property_error: Optional[str] = None

    try:
        street, city, state, postal_code = get_case_address_components(case)
        raw_property_data = batchdata_property_lookup_all_attributes(
            street, city, state, postal_code
        )
        save_property_for_case(case.id, raw_property_data)
        property_data = normalize_property_payload(raw_property_data)
    except HTTPException as exc:
        detail = exc.detail
        property_error = detail if isinstance(detail, str) else str(detail)
        # fall back to any previously saved data
        property_data = load_property_for_case(case_id)
    except Exception as exc:
        property_error = f"Unexpected error during property lookup: {exc}"
        property_data = load_property_for_case(case_id)

    offer = compute_offer_70(case.arv or 0, case.rehab or 0, case.closing_costs or 0)
    rehab_condition = getattr(case, "rehab_condition", "") or "Good"
    property_overrides = _parse_property_overrides(case)
    rehab_suggested = _estimate_rehab_from_property(
        property_data, rehab_condition, property_overrides
    )
    flip_offer = compute_offer_80(case.arv or 0, case.rehab or 0, case.closing_costs or 0)

    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "offer_70": offer,
            "offer_80": flip_offer,
            "active_parcel_id": case.parcel_id,
            "notes": notes,
            "skip_trace": skip_trace,
            "skip_trace_error": skip_trace_error,
            "property_data": property_data,
            "property_error": property_error,
            "rehab_condition": rehab_condition,
            "rehab_suggested": rehab_suggested,
            "property_overrides": property_overrides,
        },
    )


# ======================================================================
# SSE progress endpoints + update job orchestration
# ======================================================================
@app.get("/update_progress/{job_id}", response_class=HTMLResponse)
async def update_progress_page(request: Request, job_id: str):
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Updating cases…</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; }}
    .wrap {{ max-width: 900px; margin: 24px auto; padding: 0 16px; }}
    .spinner {{
      position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
      background: rgba(0,0,0,0.5); color: #fff; z-index: 9999; font-size: 18px;
    }}
    .log {{
      background: #0b0b0b; color: #c9f4ff; padding: 16px; border-radius: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      white-space: pre-wrap; line-height: 1.35; max-height: 60vh; overflow: auto;
      box-shadow: 0 10px 30px rgba(0,0,0,0.2);
    }}
    .muted {{ color: #9aa7b1; font-size: 12px; margin-top: 8px; }}
    .hide {{ display:none; }}
    .pill {{ display:inline-block; padding:4px 10px; border-radius: 999px; background:#eef2ff; color:#3730a3; font-size:12px; }}
  </style>
</head>
<body>
  <div id="spinner" class="spinner">Updating cases… Please don’t navigate away.</div>
  <div class="wrap">
    <h1>Update in progress <span class="pill">live log</span></h1>
    <div id="log" class="log"></div>
    <div id="hint" class="muted">This log will auto-scroll. You’ll be redirected when finished.</div>
  </div>

<script>
  const logEl = document.getElementById('log');
  const spinner = document.getElementById('spinner');
  const es = new EventSource('/events/{job_id}');
  function appendLine(s) {{
    logEl.textContent += s + '\\n';
    logEl.scrollTop = logEl.scrollHeight;
  }}
  es.onmessage = (e) => {{
    const t = e.data || '';
    if (t.startsWith('[done]')) {{
      spinner.classList.add('hide');
      es.close();
      setTimeout(() => window.location.href = '/cases', 10000);
    }} else {{
      if (t.trim().length) {{
        appendLine(t);
        spinner.classList.add('hide');
      }}
    }}
  }};
  es.onerror = () => {{
    appendLine('[connection error] retrying…');
  }};
</script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get("/events/{job_id}")
async def events(job_id: str):
    async def event_generator():
        # initial hello to open the stream promptly
        yield ": connected\n\n"
        while True:
            try:
                async for line in progress_bus.stream(job_id):
                    yield f"data: {line}\n\n"
            except Exception:
                # brief heartbeat to keep connection alive
                yield ": heartbeat\n\n"
                await asyncio.sleep(5)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/import", response_class=HTMLResponse)
def update_case_list_page(request: Request):
    # Renders the form with the "Days to scrape" selector that posts to /update_cases
    return templates.TemplateResponse("import.html", {"request": request})


@app.post("/update_cases")
async def update_cases(
    request: Request,
    since_days: int = Form(7),
    run_pasco: Optional[int] = Form(None),
    run_pinellas: Optional[int] = Form(None),
):
    """
    Starts an async job that:
      1) Runs the foreclosure scraper with --since-days
      2) Imports the resulting CSV with upsert-by-case_number (no dupes)
    Immediately redirects to a live log page.
    """
    job_id = uuid.uuid4().hex

    # prime the log so the progress page shows something immediately
    await progress_bus.publish(job_id, f"Queued job {job_id}…")

    # delegate the heavy lifting to the service
    asyncio.create_task(
        run_update_cases_job(
            job_id,
            since_days,
            run_pasco=bool(run_pasco),
            run_pinellas=bool(run_pinellas),
        )
    )

    return RedirectResponse(
        url=request.url_for("update_progress_page", job_id=job_id),
        status_code=303,
    )



async def _update_cases_job(job_id: str, since_days: int):
    try:
        await progress_bus.publish(job_id, f"Starting update job {job_id} (since_days={since_days})")

        # 1) Run the scraper to produce CSV
        scraper_script = _find_scraper_script()
        tmpdir = tempfile.mkdtemp(prefix="pasco_update_")
        csv_out = os.path.join(tmpdir, "pasco_foreclosures.csv")

        cmd = [
            sys.executable,
            str(scraper_script),
            "--since-days", str(max(0, int(since_days))),
            "--out", csv_out,  # your integrated scraper should accept --out
        ]
        await progress_bus.publish(job_id, "Launching scraper: " + " ".join(cmd))
        rc = await run_command_with_logs(cmd, job_id)
        if rc != 0 or not os.path.exists(csv_out):
            await progress_bus.publish(job_id, "[error] Scraper failed or CSV not found.]")
            await progress_bus.publish(job_id, "[done] exit_code=1")
            return

        await progress_bus.publish(job_id, "Scraper finished. Importing CSV via tools/import_pasco_csv.py…")

        # 2) Import CSV using the same logic as the CLI tool
        def _run_import():
            import_pasco_csv_main(csv_out)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_import)

        await progress_bus.publish(job_id, "Import complete via tools/import_pasco_csv.py")
        await progress_bus.publish(job_id, "[done] exit_code=0")

    except Exception as e:
        # Surface the exception in the log and signal completion
        await progress_bus.publish(job_id, f"[exception] {e}")
        await progress_bus.publish(job_id, "[done] exit_code=1")


# ======================================================================
# PDF Report for a Case (summary + attached documents)
# ======================================================================
@app.get("/cases/{case_id}/report")
def case_report(case_id: int, db: Session = Depends(get_db)):
    """
    Lightweight wrapper that delegates to app.services.report_service.
    """
    return generate_case_report(case_id, db)


@app.post("/cases/reports/download")
def download_case_reports(
    ids: List[int] = Form(default=[]),
    include_attachments: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if not ids:
        return RedirectResponse(url="/cases", status_code=303)

    cases = db.query(Case).filter(Case.id.in_(ids)).all()
    case_map = {c.id: c for c in cases}
    include_docs = include_attachments == "1"

    def _safe_name(raw: str) -> str:
        s = (raw or "").strip()
        if not s:
            return ""
        s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
        return s.strip("_")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for cid in ids:
            case = case_map.get(cid)
            if not case:
                continue
            case_num = _safe_name(getattr(case, "case_number", "") or "")
            file_name = f"case_{cid}.pdf" if not case_num else f"case_{cid}_{case_num}.pdf"
            report_buf = build_case_report_bytes(cid, db, include_attachments=include_docs)
            report_buf.seek(0)
            zf.writestr(file_name, report_buf.read())

    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=case_reports.zip"},
    )


# ======================================================================
# Case document uploads
# ======================================================================
@app.post("/cases/{case_id}/upload/verified")
async def upload_verified(
    case_id: int, verified_complaint: UploadFile = File(...), db: Session = Depends(get_db)
):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        return RedirectResponse("/cases", status_code=303)

    folder = ensure_case_folder(str(UPLOAD_ROOT), case.case_number)
    dest = Path(folder) / "Verified_Complaint.pdf"
    with open(dest, "wb") as f:
        f.write(await verified_complaint.read())

    case.verified_complaint_path = dest.relative_to(UPLOAD_ROOT).as_posix()
    db.commit()
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/upload/value_calc")
async def upload_value_calc(
    case_id: int, value_calc: UploadFile = File(...), db: Session = Depends(get_db)
):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        return RedirectResponse("/cases", status_code=303)

    folder = ensure_case_folder(str(UPLOAD_ROOT), case.case_number)
    dest = Path(folder) / "Value_Calculation.pdf"
    with open(dest, "wb") as f:
        f.write(await value_calc.read())

    case.value_calc_path = dest.relative_to(UPLOAD_ROOT).as_posix()
    db.commit()
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/upload/mortgage")
async def upload_mortgage(
    case_id: int, mortgage: UploadFile = File(...), db: Session = Depends(get_db)
):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        return RedirectResponse("/cases", status_code=303)

    folder = ensure_case_folder(str(UPLOAD_ROOT), case.case_number)
    dest = Path(folder) / "Mortgage.pdf"
    with open(dest, "wb") as f:
        f.write(await mortgage.read())

    case.mortgage_path = dest.relative_to(UPLOAD_ROOT).as_posix()
    db.commit()
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/upload/current-deed")
async def upload_current_deed(
    case_id: int, current_deed: UploadFile = File(...), db: Session = Depends(get_db)
):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        return RedirectResponse("/cases", status_code=303)

    folder = ensure_case_folder(str(UPLOAD_ROOT), case.case_number)
    dest = Path(folder) / "Current_Deed.pdf"
    with open(dest, "wb") as f:
        f.write(await current_deed.read())

    case.current_deed_path = dest.relative_to(UPLOAD_ROOT).as_posix()
    db.commit()
    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/upload/previous-deed")
async def upload_previous_deed(
    case_id: int, previous_deed: UploadFile = File(...), db: Session = Depends(get_db)
):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        return RedirectResponse("/cases", status_code=303)

    folder = ensure_case_folder(str(UPLOAD_ROOT), case.case_number)
    dest = Path(folder) / "Previous_Deed.pdf"
    with open(dest, "wb") as f:
        f.write(await previous_deed.read())

    case.previous_deed_path = dest.relative_to(UPLOAD_ROOT).as_posix()
    db.commit()
    return RedirectResponse(f"/cases/{case_id}", status_code=303)

@app.post("/cases/{case_id}/documents/upload")
async def upload_case_document(
    case_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Single upload endpoint. Uses doc_type to decide where to store the file:
    - verified          -> case.verified_complaint_path
    - mortgage          -> case.mortgage_path
    - current_deed      -> case.current_deed_path
    - previous_deed     -> case.previous_deed_path
    - value_calc        -> case.value_calc_path
    - other             -> generic Docket record
    """
    # Load case
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Normalize doc_type
    dt = (doc_type or "").strip().lower()

    # Make sure we have a filename
    original_name = file.filename or "document.pdf"
    safe_name = original_name.replace("/", "_").replace("\\", "_")

    # Folder per case
    folder = ensure_case_folder(str(UPLOAD_ROOT), case.case_number)

    # Map the dropdown choice to a fixed filename + case field
    mapping = {
        "verified":      ("Verified_Complaint.pdf", "verified_complaint_path", "Verified Complaint"),
        "mortgage":      ("Mortgage.pdf", "mortgage_path", "Mortgage"),
        "current_deed":  ("Current_Deed.pdf", "current_deed_path", "Current Deed"),
        "previous_deed": ("Previous_Deed.pdf", "previous_deed_path", "Previous Deed"),
        "value_calc":    ("Value_Calculation.pdf", "value_calc_path", "Value Calculation"),
    }

    if dt in mapping:
        target_name, attr_name, _label = mapping[dt]
        dest = Path(folder) / target_name
    else:
        # "other" or anything unknown: keep the user’s filename
        dest = Path(folder) / safe_name
        attr_name = None  # will create a Docket row instead

    # Save file to disk
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    rel_path = dest.relative_to(UPLOAD_ROOT).as_posix()

    # If it’s a known type, store on the Case model
    if attr_name:
        setattr(case, attr_name, rel_path)
        db.add(case)
        db.commit()
    else:
        # Generic "Other" document -> create a Docket record
        docket = Docket(
            case_id=case.id,
            file_name=safe_name,
            file_url=f"/uploads/{rel_path}",
            description=original_name,
        )
        db.add(docket)
        db.commit()

    return RedirectResponse(f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/notes/add")
def add_note(case_id: int, content: str = Form(...), db: Session = Depends(get_db)):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    content = (content or "").strip()
    if not content:
        return RedirectResponse(url=f"/cases/{case_id}", status_code=303)
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    note = Note(case_id=case_id, content=content, created_at=ts)
    db.add(note)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/property-overrides")
def update_property_overrides(
    case_id: int,
    property_type: Optional[str] = Form(None),
    year_built: Optional[str] = Form(None),
    sqft: Optional[str] = Form(None),
    lot_size: Optional[str] = Form(None),
    beds: Optional[str] = Form(None),
    baths: Optional[str] = Form(None),
    low_range: Optional[str] = Form(None),
    high_range: Optional[str] = Form(None),
    estimated_value: Optional[str] = Form(None),
    assessed_value: Optional[str] = Form(None),
    annual_taxes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    overrides = _parse_property_overrides(case)

    def _set(key: str, val: Optional[str]) -> None:
        if val is None:
            return
        v = val.strip()
        if v:
            overrides[key] = v
        else:
            overrides.pop(key, None)

    _set("property_type", property_type)
    _set("year_built", year_built)
    _set("sqft", sqft)
    _set("lot_size", lot_size)
    _set("beds", beds)
    _set("baths", baths)
    _set("low_range", low_range)
    _set("high_range", high_range)
    _set("estimated_value", estimated_value)
    _set("assessed_value", assessed_value)
    _set("annual_taxes", annual_taxes)

    case.property_overrides = json.dumps(overrides)
    db.add(case)
    db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.get("/cases/{case_id}/notes/{note_id}/delete")
def delete_note(case_id: int, note_id: int, db: Session = Depends(get_db)):
    note = db.query(Note).filter(Note.id == note_id, Note.case_id == case_id).first()
    if note:
        db.delete(note)
        db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


# ======================================================================
# NEW: Outstanding Liens API
# ======================================================================
@app.get("/cases/{case_id}/liens", response_model=list[OutstandingLien])
def get_outstanding_liens(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.get_outstanding_liens()


@app.post("/cases/{case_id}/liens", response_model=list[OutstandingLien])
def save_outstanding_liens(case_id: int, payload: OutstandingLiensUpdate, db: Session = Depends(get_db)):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    case.set_outstanding_liens([l.dict() for l in payload.outstanding_liens])
    db.add(case)
    db.commit()
    db.refresh(case)
    return case.get_outstanding_liens()


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


@app.get("/cases", response_class=HTMLResponse)
def cases_list(
    request: Request,
    page: int = Query(1),
    page_size: int = Query(10, ge=5, le=100),
    show_archived: int = Query(0),
    case: str = Query("", alias="case"),
    tag: str = Query(""),
    db: Session = Depends(get_db),
):
    qry = db.query(Case)

    if not show_archived:
        qry = qry.filter(text("(archived IS NULL OR archived = 0)"))

    if case:
        qry = qry.filter(Case.address.contains(case))

    def _as_float(val: object) -> float:
        try:
            if val is None:
                return 0.0
            if isinstance(val, (int, float)):
                return float(val)
            s = str(val).strip()
            if not s:
                return 0.0
            cleaned = s.replace("$", "").replace(",", "")
            return float(cleaned)
        except Exception:
            return 0.0

    def _sum_liens(raw: object) -> float:
        try:
            liens = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            liens = []
        total = 0.0
        if isinstance(liens, list):
            for item in liens:
                if not isinstance(item, dict):
                    continue
                amt = item.get("amount") or item.get("balance") or item.get("lien_amount")
                total += _as_float(amt)
        return round(total, 2)

    tag_map = {
        "Owner Occupied": "ownerOccupied",
        "High Equity": "highEquity",
        "Free & Clear": "freeAndClear",
        "Absentee Owner": "absenteeOwner",
        "Pre-foreclosure": "preforeclosure",
        "Tax Default": "taxDefault",
        "Vacant": "vacant",
        "Has HOA": "hasHoa",
        "Short Sale": "__short_sale__",
    }
    if tag == "Short Sale":
        base_rows = (
            qry.with_entities(
                Case.id,
                Case.arv,
                Case.rehab,
                Case.closing_costs,
                Case.outstanding_liens,
            )
            .all()
        )
        short_sale_ids = []
        for cid, arv_v, rehab_v, closing_v, liens_raw in base_rows:
            liens_total = _sum_liens(liens_raw)
            wholesale_offer = max(
                0.0, (_as_float(arv_v) * 0.65) - _as_float(rehab_v) - _as_float(closing_v)
            )
            flip_rate = 0.85 if _as_float(arv_v) > 350000 else 0.80
            flip_offer = max(
                0.0, (_as_float(arv_v) * flip_rate) - _as_float(rehab_v) - _as_float(closing_v)
            )
            if liens_total and (wholesale_offer < liens_total or flip_offer < liens_total):
                short_sale_ids.append(int(cid))
        if short_sale_ids:
            qry = qry.filter(Case.id.in_(short_sale_ids))
        else:
            qry = qry.filter(text("1=0"))
    elif tag and tag in tag_map:
        key = tag_map[tag]
        pattern1 = f'%"{key}":true%'
        pattern2 = f'%"{key}": true%'
        rows = db.execute(
            text(
                "SELECT case_id FROM case_property "
                "WHERE raw_json LIKE :p1 OR raw_json LIKE :p2"
            ),
            {"p1": pattern1, "p2": pattern2},
        ).fetchall()
        tag_case_ids = [int(r[0]) for r in rows]
        if tag_case_ids:
            qry = qry.filter(Case.id.in_(tag_case_ids))
        else:
            qry = qry.filter(text("1=0"))
    # page_size comes from query param (default 10)
    # Deterministic ordering: newest first
    qry = qry.order_by(Case.id.desc())

    total = qry.count()
    pages = (total + page_size - 1) // page_size
    offset = (page - 1) * page_size

    # ✅ NEWEST → OLDEST by case_id (Case.id)
    qry = qry.order_by(Case.id.desc())
    cases = qry.offset(offset).limit(page_size).all()
    pagination = {"page": page, "pages": pages, "total": total}

    # Badge counts (safe, no schema changes)
    case_ids = [c.id for c in cases]
    defendants_count = {}
    notes_count = {}
    docs_present = {}

    if case_ids:
        # Defendants count
        for cid, cnt in (
            db.query(Defendant.case_id, func.count(Defendant.id))
              .filter(Defendant.case_id.in_(case_ids))
              .group_by(Defendant.case_id)
              .all()
        ):
            defendants_count[int(cid)] = int(cnt)

        # Notes count
        for cid, cnt in (
            db.query(Note.case_id, func.count(Note.id))
              .filter(Note.case_id.in_(case_ids))
              .group_by(Note.case_id)
              .all()
        ):
            notes_count[int(cid)] = int(cnt)

    # Docs present based on stored paths on Case (Verified Complaint, Mortgage, etc.)
    for c in cases:
        docs_present[c.id] = any(
            bool(getattr(c, attr, "") or "")
            for attr in [
                "verified_complaint_path",
                "value_calc_path",
                "mortgage_path",
                "current_deed_path",
                "previous_deed_path",
                "appraiser_doc1_path",
                "appraiser_doc2_path",
            ]
        )

    # Quick flags + short sale tags
    quick_flags: dict[int, list[str]] = {}
    short_sale: dict[int, bool] = {}

    if case_ids:
        # Quick flags from BatchData property payload
        try:
            placeholders = ",".join([":id" + str(i) for i in range(len(case_ids))])
            params = {"id" + str(i): case_ids[i] for i in range(len(case_ids))}
            rows = db.execute(
                text(
                    f"SELECT case_id, raw_json FROM case_property WHERE case_id IN ({placeholders})"
                ),
                params,
            ).fetchall()
            for cid, raw_json in rows:
                if not raw_json:
                    continue
                try:
                    payload = json.loads(raw_json)
                    payload = normalize_property_payload(payload)
                    props = (payload.get("results") or {}).get("properties") or []
                    if not props:
                        continue
                    quick = props[0].get("quickLists") or {}
                except Exception:
                    continue

                tags: list[str] = []
                if quick.get("ownerOccupied"):
                    tags.append("Owner Occupied")
                if quick.get("highEquity"):
                    tags.append("High Equity")
                if quick.get("freeAndClear"):
                    tags.append("Free & Clear")
                if quick.get("absenteeOwner"):
                    tags.append("Absentee Owner")
                if quick.get("preforeclosure"):
                    tags.append("Pre-foreclosure")
                if quick.get("taxDefault"):
                    tags.append("Tax Default")
                if quick.get("vacant"):
                    tags.append("Vacant")
                if quick.get("hasHoa"):
                    tags.append("Has HOA")
                if tags:
                    quick_flags[int(cid)] = tags
        except Exception as e:
            logger.warning("cases_list: could not compute quick_flags: %s", e)

        # Short sale: liens exceed wholesale or flip offer
        for c in cases:
            liens_total = _sum_liens(getattr(c, "outstanding_liens", "[]"))
            wholesale_offer = max(0.0, (_as_float(c.arv) * 0.65) - _as_float(c.rehab) - _as_float(c.closing_costs))
            flip_rate = 0.85 if _as_float(c.arv) > 350000 else 0.80
            flip_offer = max(0.0, (_as_float(c.arv) * flip_rate) - _as_float(c.rehab) - _as_float(c.closing_costs))
            short_sale[c.id] = bool(liens_total and (wholesale_offer < liens_total or flip_offer < liens_total))

    
    # Skip Trace present (exists row in case_skiptrace)
    skiptrace_present = {}
    try:
        if cases:
            case_ids = [c.id for c in cases]
            # SQLite-friendly IN clause
            placeholders = ",".join([":id" + str(i) for i in range(len(case_ids))])
            params = {"id"+str(i): case_ids[i] for i in range(len(case_ids))}
            rows = db.execute(text(f"SELECT case_id FROM case_skiptrace WHERE case_id IN ({placeholders})"), params).fetchall()
            for (cid,) in rows:
                skiptrace_present[int(cid)] = True
    except Exception as e:
        logger.warning("cases_list: could not compute skiptrace_present: %s", e)

    return templates.TemplateResponse(
        "cases_list.html",
        {
            "request": request,
            "cases": cases,   # ✅ now defined
            "pagination": pagination,
            "show_archived": bool(show_archived),
            "search_query": case,
            "tag_filter": tag,
            "tag_options": list(tag_map.keys()),
            "page_size": page_size,
            "defendants_count": defendants_count,
            "notes_count": notes_count,
            "docs_present": docs_present,
            "skiptrace_present": skiptrace_present,
            "quick_flags": quick_flags,
            "short_sale": short_sale,
        },
    )



@app.post("/cases/archive")
def archive_cases(
    request: Request,
    ids: List[int] = Form(default=[]),
    show_archived: int = Form(0),
    db: Session = Depends(get_db),
):
    if ids:
        db.execute(
            text("UPDATE cases SET archived = 1 WHERE id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
        db.commit()
    return RedirectResponse(url="/cases?show_archived=0&page=1", status_code=303)


@app.post("/cases/export")
def export_cases(
    request: Request,
    ids: List[int] = Form(default=[]),
    show_archived: int = Form(0),
    case: str = Form("", alias="case"),
    db: Session = Depends(get_db),
):
    qry = db.query(Case)

    if not show_archived:
        qry = qry.filter(text("(archived IS NULL OR archived = 0)"))

    if case:
        qry = qry.filter(Case.case_number.contains(case))
    if ids:
        qry = qry.filter(Case.id.in_(ids))

    header = [
        "id",
        "case_number",
        "filing_datetime",
        "style",
        "address",
        "arv",
        "closing_costs",
        "current_deed_path",
        "defendants",
        "mortgage_path",
        "notes_count",
        "outstanding_liens",
        "parcel_id",
        "previous_deed_path",
        "rehab",
        "value_calc_path",
        "verified_complaint_path",
        "skiptrace_owner_name",
        "skiptrace_property_address",
        "skiptrace_phones",
        "skiptrace_emails",
    ]

    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(header)

    rows = qry.order_by(Case.filing_datetime.desc()).all()
    for c in rows:
        try:
            defendants = [d.name for d in c.defendants] if getattr(c, "defendants", None) else []
        except Exception:
            defendants = []
        try:
            notes_count = len(c.notes) if getattr(c, "notes", None) else 0
        except Exception:
            notes_count = 0

        address = (getattr(c, "address_override", None) or getattr(c, "address", "") or "").strip()
        outstanding = getattr(c, "outstanding_liens", None) or "[]"

        skip = load_skiptrace_for_case(c.id)
        skip_owner = ""
        skip_addr = ""
        skip_phones = []
        skip_emails = []
        try:
            if skip and skip.get("results"):
                result = skip["results"][0] or {}
                person = (result.get("persons") or [{}])[0] or {}
                prop_addr = result.get("propertyAddress") or {}
                skip_owner = person.get("full_name") or ""
                skip_addr = " ".join(
                    p for p in [
                        prop_addr.get("street"),
                        prop_addr.get("city"),
                        prop_addr.get("state"),
                        prop_addr.get("postalCode"),
                    ] if p
                ).strip()
                for ph in person.get("phones") or []:
                    num = ph.get("number")
                    if num:
                        skip_phones.append(num)
                for em in person.get("emails") or []:
                    addr = em.get("email")
                    if addr:
                        skip_emails.append(addr)
        except Exception:
            pass

        writer.writerow([
            c.id,
            c.case_number or "",
            c.filing_datetime or "",
            c.style or "",
            address,
            getattr(c, "arv", "") or "",
            getattr(c, "closing_costs", "") or "",
            getattr(c, "current_deed_path", "") or "",
            json.dumps(defendants),
            getattr(c, "mortgage_path", "") or "",
            notes_count,
            outstanding,
            c.parcel_id or "",
            getattr(c, "previous_deed_path", "") or "",
            getattr(c, "rehab", "") or "",
            getattr(c, "value_calc_path", "") or "",
            getattr(c, "verified_complaint_path", "") or "",
            skip_owner,
            skip_addr,
            ";".join(skip_phones),
            ";".join(skip_emails),
        ])

    buf.seek(0)
    filename = f"cases_export_{_dt.datetime.now().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


@app.post("/cases/export_crm")
def export_cases_crm(
    request: Request,
    ids: List[int] = Form(default=[]),
    show_archived: int = Form(0),
    case: str = Form("", alias="case"),
    db: Session = Depends(get_db),
):
    qry = db.query(Case)

    if not show_archived:
        qry = qry.filter(text("(archived IS NULL OR archived = 0)"))

    if case:
        qry = qry.filter(Case.case_number.contains(case))
    if ids:
        qry = qry.filter(Case.id.in_(ids))

    header = [
        "STATUS",
        "TAGS",
        "CONTACT_TYPES",
        "MOTIVATION_LEVEL",
        "NAME",
        "FIRST_NAME",
        "LAST_NAME",
        "EMAIL",
        "PHONE_1_NUMBER",
        "PHONE_1_PHONE_TYPE",
        "PHONE_1_DESCRIPTION",
        "PHONE_1_DO_NOT_CALL",
        "PHONE_1_CONSENT_GIVEN",
        "PHONE_2_NUMBER",
        "PHONE_2_PHONE_TYPE",
        "PHONE_2_DESCRIPTION",
        "PHONE_2_DO_NOT_CALL",
        "PHONE_2_CONSENT_GIVEN",
        "PHONE_3_NUMBER",
        "PHONE_3_PHONE_TYPE",
        "PHONE_3_DESCRIPTION",
        "PHONE_3_DO_NOT_CALL",
        "PHONE_3_CONSENT_GIVEN",
        "MAILING_ADDRESS",
        "MAILING_STREET_ADDRESS",
        "MAILING_CITY",
        "MAILING_STATE",
        "MAILING_ZIP",
        "COMPANY_NAME",
        "COMPANY_ADDRESS",
        "PROPERTY_FULL_ADDRESS",
        "PROPERTY_STREET_ADDRESS",
        "PROPERTY_CITY",
        "PROPERTY_STATE",
        "PROPERTY_ZIP",
        "PROPERTY_COUNTY",
        "PROPERTY_APN",
        "PROPERTY_LEGAL_DESCRIPTION",
        "PROPERTY_FULL_ADDRESS_MAP_URL",
        "PROPERTY_OCCUPANCY",
        "PROPERTY_BEDROOMS",
        "PROPERTY_BATHROOMS",
        "PROPERTY_SQFT",
        "PROPERTY_LOT_SIZE_SQFT",
        "PROPERTY_YEAR",
        "PROPERTY_NOTES",
        "OWNER_IS_ABSENTEE",
        "OWNER_2_FULL_NAME",
        "OWNER_2_LAST_NAME",
        "OWNER_2_MIDDLE_NAME",
        "OWNER_2_FIRST_NAME",
        "OWNER_2_ADDRESS",
        "OWNER_2_PHONE",
        "OWNER_2_EMAIL",
        "OWNER_2_NOTES",
        "OWNER_2_IS_SPOUSE",
        "SPOUSE_NAME",
        "SPOUSE_MAILING_ADDRESS",
        "SPOUSE_EMAIL",
        "SPOUSE_PHONE",
        "SPOUSE_NOTES",
        "PROPERTY_VALUE",
        "PROPERTY_EQUITY",
        "PROPERTY_REPAIR_ESTIMATE",
        "PROPERTY_LAST_SALE_AMOUNT",
        "PROPERTY_LAST_SALE_DT",
        "PROPERTY_LAST_SALE_IS_CASH",
        "OFFER_AMOUNT",
        "OFFER_CONTRACT_DT",
        "OFFER_ACCEPTED_DT",
        "OFFER_REJECTED_DT",
        "OFFER_NOTES",
        "OFFER_UPLOAD_ID",
        "PROPERTY_IS_LISTED",
        "PROPERTY_LISTED_DT",
        "PROPERTY_LISTING_URL",
        "PROPERTY_LISTING_AGT_NAME",
        "PROPERTY_LISTING_AGT_PHONE",
        "PROPERTY_LISTING_AGT_EMAIL",
        "PROPERTY_LISTING_AMOUNT",
        "PROPERTY_LISTING_NOTES",
        "NEEDS_SALE_BY_DT",
        "REASON_FOR_SELLING",
        "PROPERTY_SELLER_LOWEST_AMOUNT",
        "PROPERTY_SELLER_ESTIMATED_VALUE",
        "PROPERTY_SELLER_VALUE_RATIONALE",
        "MOTIVATION_NOTES",
        "MORTGAGE_BANK_NAME",
        "MORTGAGE_BANK_CONTACT_NAME",
        "MORTGAGE_BANK_CONTACT_EMAIL",
        "MORTGAGE_BANK_CONTACT_PHONE",
        "MORTGAGE_PAYMENT_ADDRESS",
        "MORTGAGE_AMOUNT_MONTHLY",
        "MORTGAGE_AMOUNT_REMAINING",
        "MORTGAGE_BEHIND_MONTHS",
        "MORTGAGE_BEHIND_AMOUNT",
        "MORTGAGE_BANK_NOTES",
        "FORECLOSURE_DEFAULT_AMOUNT",
        "FORECLOSURE_ATTORNEY_NAME",
        "FORECLOSURE_CASE_NUMBER",
        "FORECLOSURE_DOCUMENT_NUMBER",
        "FORECLOSURE_DOCUMENT_TYPE",
        "FORECLOSURE_EFFECTIVE_DT",
        "FORECLOSURE_LENDER_ADDRESS",
        "FORECLOSURE_LENDER_NAME",
        "FORECLOSURE_LIEN_POSITION",
        "FORECLOSURE_ORIGINAL_DOCUMENT_NUM",
        "FORECLOSURE_ORIGINAL_LENDER",
        "FORECLOSURE_ORIGINAL_MORTGAGE_AMT",
        "FORECLOSURE_ORIGINAL_RECORDING_DT",
        "FORECLOSURE_PLAINTIFF",
        "FORECLOSURE_RECENT_ADDED_DT",
        "FORECLOSURE_RECORDING_DT",
        "FORECLOSURE_TRUSTEE_NAME",
        "FORECLOSURE_TRUSTEE_ADDRESS",
        "FORECLOSURE_TRUSTEE_SALE_NUMBER",
        "FORECLOSURE_UNPAID_BALANCE",
        "FORECLOSURE_NOTES",
        "REPRESENTATIVE_NAME",
        "REPRESENTATIVE_ADDRESS",
        "REPRESENTATIVE_PHONE",
        "REPRESENTATIVE_EMAIL",
        "REPRESENTATIVE_NOTES",
        "ATTORNEY_NAME",
        "ATTORNEY_ADDRESS",
        "ATTORNEY_PHONE",
        "ATTORNEY_EMAIL",
        "ATTORNEY_NOTES",
        "NOTES",
    ]

    def _split_name(full: str) -> tuple[str, str]:
        parts = [p for p in (full or "").split() if p]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[-1]

    def _stringify(val: object) -> str:
        if val is None:
            return ""
        return str(val)

    rows = qry.order_by(Case.filing_datetime.desc()).all()
    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    writer.writerow(header)

    for c in rows:
        skip = load_skiptrace_for_case(c.id)
        owner_name = ""
        email = ""
        phones = []
        mailing = {}

        if skip and skip.get("results"):
            result = skip["results"][0] or {}
            person = (result.get("persons") or [{}])[0] or {}
            owner_name = person.get("full_name") or ""
            for ph in person.get("phones") or []:
                num = ph.get("number")
                if num:
                    phones.append({"number": num, "type": ph.get("type") or ""})
            for em in person.get("emails") or []:
                addr = em.get("email")
                if addr:
                    email = addr
                    break
            mailing = result.get("propertyAddress") or {}

        first_name, last_name = _split_name(owner_name)

        prop = load_property_for_case(c.id) or {}
        props = (prop.get("results") or {}).get("properties") or []
        p = props[0] if props else {}
        addr = p.get("address") or {}
        listing = p.get("listing") or {}
        general = p.get("general") or {}
        building = p.get("building") or {}
        lot = p.get("lot") or {}
        quick = p.get("quickLists") or {}

        ov_raw = getattr(c, "property_overrides", "") or "{}"
        try:
            ov = json.loads(ov_raw) if isinstance(ov_raw, str) else {}
        except Exception:
            ov = {}

        prop_street = (c.address_override or c.address or addr.get("street") or addr.get("streetNoUnit") or "").strip()
        prop_city = (addr.get("city") or "").strip()
        prop_state = (addr.get("state") or "").strip()
        prop_zip = (addr.get("zip") or "").strip()
        prop_county = (addr.get("county") or "").strip()

        sqft = ov.get("sqft") or building.get("livingAreaSqft") or general.get("buildingAreaSqft") or listing.get("totalBuildingAreaSquareFeet")
        lot_sqft = lot.get("lotSizeSqft") or listing.get("lotSizeSquareFeet") or ""
        year_built = ov.get("year_built") or general.get("yearBuilt") or p.get("yearBuilt") or listing.get("yearBuilt")
        beds = ov.get("beds") or building.get("bedrooms") or listing.get("bedroomCount") or ""
        baths = ov.get("baths") or building.get("totalBathrooms") or listing.get("bathroomCount") or ""

        tags = []
        if quick.get("ownerOccupied"):
            tags.append("Owner Occupied")
        if quick.get("highEquity"):
            tags.append("High Equity")
        if quick.get("freeAndClear"):
            tags.append("Free & Clear")
        if quick.get("absenteeOwner"):
            tags.append("Absentee Owner")
        if quick.get("preforeclosure"):
            tags.append("Pre-foreclosure")
        if quick.get("taxDefault"):
            tags.append("Tax Default")
        if quick.get("vacant"):
            tags.append("Vacant")
        if quick.get("hasHoa"):
            tags.append("Has HOA")

        phones += [{"number": "", "type": ""}] * (3 - len(phones))

        writer.writerow([
            "New",
            ";".join(tags),
            "",
            "",
            owner_name,
            first_name,
            last_name,
            email,
            _stringify(phones[0]["number"]),
            _stringify(phones[0]["type"]),
            "",
            "",
            "",
            _stringify(phones[1]["number"]),
            _stringify(phones[1]["type"]),
            "",
            "",
            "",
            _stringify(phones[2]["number"]),
            _stringify(phones[2]["type"]),
            "",
            "",
            "",
            " ".join(p for p in [mailing.get("street"), mailing.get("city"), mailing.get("state"), mailing.get("postalCode")] if p),
            _stringify(mailing.get("street")),
            _stringify(mailing.get("city")),
            _stringify(mailing.get("state")),
            _stringify(mailing.get("postalCode")),
            "",
            "",
            " ".join(p for p in [prop_street, prop_city, prop_state, prop_zip] if p),
            prop_street,
            prop_city,
            prop_state,
            prop_zip,
            prop_county,
            getattr(c, "parcel_id", "") or "",
            "",
            "",
            "",
            _stringify(beds),
            _stringify(baths),
            _stringify(sqft),
            _stringify(lot_sqft),
            _stringify(year_built),
            "",
            "true" if quick.get("absenteeOwner") else "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            _stringify(ov.get("estimated_value") or ""),
            "",
            _stringify(getattr(c, "rehab", "") or ""),
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        ])

    buf.seek(0)
    filename = f"crm_export_{_dt.datetime.now().strftime('%Y-%m-%d')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@app.post("/cases/unarchive")
def unarchive_cases(
    request: Request,
    ids: List[int] = Form(default=[]),
    show_archived: int = Form(0),
    db: Session = Depends(get_db),
):
    if ids:
        db.execute(
            text("UPDATE cases SET archived = 0 WHERE id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
        db.commit()
    return RedirectResponse(url=f"/cases?show_archived={show_archived}&page=1", status_code=303)


@app.post("/cases/archive_async")
def archive_cases_async(
    ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if not ids:
        return {"ok": True, "updated": 0}
    db.execute(
        text("UPDATE cases SET archived = 1 WHERE id IN :ids")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    db.commit()
    return {"ok": True, "updated": len(ids)}


@app.post("/cases/unarchive_async")
def unarchive_cases_async(
    ids: List[int] = Form(default=[]),
    db: Session = Depends(get_db),
):
    if not ids:
        return {"ok": True, "updated": 0}
    db.execute(
        text("UPDATE cases SET archived = 0 WHERE id IN :ids")
        .bindparams(bindparam("ids", expanding=True)),
        {"ids": ids},
    )
    db.commit()
    return {"ok": True, "updated": len(ids)}
@app.post("/cases/{case_id}/property/update-owner")
async def update_property_owner(
    request: Request,
    case_id: int,
):
    """Update owner information - multiple names, shared address"""
    try:
        from app.services.skiptrace_service import load_property_for_case
        import json
        
        # Get form data
        form_data = await request.form()
        owner_count = int(form_data.get("owner_count", 1))
        
        property_payload = load_property_for_case(case_id)
        
        if property_payload and property_payload.get("results"):
            if "properties" in property_payload["results"] and len(property_payload["results"]["properties"]) > 0:
                prop = property_payload["results"]["properties"][0]
                
                # Shared mailing address (same for all owners)
                shared_address = {
                    "street": form_data.get("mailing_street", ""),
                    "city": form_data.get("mailing_city", ""),
                    "state": form_data.get("mailing_state", ""),
                    "zipCode": form_data.get("mailing_zip", ""),
                    "county": form_data.get("mailing_county", ""),
                }
                
                if owner_count > 1:
                    # Multiple owners - create array with shared address
                    owners_array = []
                    
                    for i in range(1, owner_count + 1):
                        owner_name = form_data.get(f"owner_name_{i}", "")
                        if owner_name:  # Only add if name is not empty
                            owners_array.append({
                                "fullName": owner_name,
                                "name": owner_name,
                                "mailingAddress": shared_address.copy()
                            })
                    
                    # Store as array
                    prop["owner"] = owners_array
                    
                else:
                    # Single owner
                    owner_name = form_data.get("owner_name_1", "")
                    
                    prop["owner"] = {
                        "fullName": owner_name,
                        "name": owner_name,
                        "mailingAddress": shared_address
                    }
                
                # Save back to database
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE case_property 
                            SET raw_json = :json
                            WHERE case_id = :case_id
                        """),
                        {"case_id": case_id, "json": json.dumps(property_payload)}
                    )
                
                logger.info(f"Updated {owner_count} owner(s) for case {case_id}")
    
    except Exception as exc:
        logger.error(f"Failed to update owner info: {exc}")
    
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)

@app.post("/cases/{case_id}/property/update-valuation")
async def update_property_valuation(
    request: Request,
    case_id: int,
    estimated_value: Optional[float] = Form(None),
    as_of_date: str = Form(""),
    confidence_score: Optional[float] = Form(None),
    equity_percent: Optional[float] = Form(None),
    ltv: Optional[float] = Form(None),
):
    """Update valuation information"""
    try:
        from app.services.skiptrace_service import load_property_for_case
        import json
        
        property_payload = load_property_for_case(case_id)
        
        if property_payload and property_payload.get("results"):
            if "properties" in property_payload["results"] and len(property_payload["results"]["properties"]) > 0:
                prop = property_payload["results"]["properties"][0]
                
                if "valuation" not in prop:
                    prop["valuation"] = {}
                
                prop["valuation"].update({
                    "estimatedValue": estimated_value,
                    "asOfDate": as_of_date,
                    "confidenceScore": confidence_score,
                    "equityPercent": equity_percent,
                    "ltv": ltv,
                })
                
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE case_property 
                            SET raw_json = :json
                            WHERE case_id = :case_id
                        """),
                        {"case_id": case_id, "json": json.dumps(property_payload)}
                    )
                
                logger.info(f"Updated valuation for case {case_id}")
    
    except Exception as exc:
        logger.error(f"Failed to update valuation: {exc}")
    
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@app.post("/cases/{case_id}/property/update-demographics")
async def update_property_demographics(
    request: Request,
    case_id: int,
    age: Optional[int] = Form(None),
    gender: str = Form(""),
    marital_status: str = Form(""),
    child_count: Optional[int] = Form(None),
    income: Optional[float] = Form(None),
    net_worth: Optional[float] = Form(None),
    occupation: str = Form(""),
):
    """Update demographics information"""
    try:
        from app.services.skiptrace_service import load_property_for_case
        import json
        
        property_payload = load_property_for_case(case_id)
        
        if property_payload and property_payload.get("results"):
            if "properties" in property_payload["results"] and len(property_payload["results"]["properties"]) > 0:
                prop = property_payload["results"]["properties"][0]
                
                if "demographics" not in prop:
                    prop["demographics"] = {}
                
                prop["demographics"].update({
                    "age": age,
                    "gender": gender,
                    "maritalStatus": marital_status,
                    "childCount": child_count,
                    "income": income,
                    "netWorth": net_worth,
                    "individualOccupation": occupation,
                })
                
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            UPDATE case_property 
                            SET raw_json = :json
                            WHERE case_id = :case_id
                        """),
                        {"case_id": case_id, "json": json.dumps(property_payload)}
                    )
                
                logger.info(f"Updated demographics for case {case_id}")
    
    except Exception as exc:
        logger.error(f"Failed to update demographics: {exc}")
    
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)
@app.get("/debug/owners/{case_id}")
def debug_owners(case_id: int):
    """Debug owner data structure"""
    from app.services.skiptrace_service import load_property_for_case
    import json
    
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
# =====================
# Manual Add Case (v1.08)
# =====================
# (placeholder for future additions)
