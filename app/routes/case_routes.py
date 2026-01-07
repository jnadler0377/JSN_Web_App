# app/routes/case_routes.py
"""
Case Routes - All case CRUD and related operations

Migrated from main.py to reduce file size and improve maintainability.
"""

import json
import logging
import tempfile
import os
import io
import csv as _csv
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Request, Depends, Form, Query, File, UploadFile, HTTPException, Body, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text, func, bindparam, inspect, case as sql_case

from app.database import get_db, engine, SessionLocal
from app.models import Case, Defendant, Docket, Note
from app.services.auth_service import get_current_user
from app.utils import ensure_case_folder, compute_offer_70, compute_offer_80
from app.schemas import OutstandingLien, OutstandingLiensUpdate
from app.services.permission_service import can_view_sensitive, get_case_visibility
from app.services.claim_service import get_claim_for_case, get_user_claim_count
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
from app.services.comparables_service import (
    fetch_and_save_comparables,
    load_comparables_from_db,
    calculate_suggested_arv,
)
from app.services.ocr_service import (
    extract_document_data,
    auto_populate_case_from_ocr,
)
from app.services.report_service import (
    generate_case_report,
    build_case_report_bytes,
    _is_short_sale,
    _report_filename
)

try:
    from app.config import settings
except ImportError:
    class settings:
        pass

logger = logging.getLogger("pascowebapp.cases")

router = APIRouter(prefix="/cases", tags=["cases"])

# Will be set by main.py
templates = None
UPLOAD_ROOT = None


def init_case_routes(t, upload_root):
    """Initialize templates and upload root from main app"""
    global templates, UPLOAD_ROOT
    templates = t
    UPLOAD_ROOT = upload_root


# ============================================================
# HELPER FUNCTIONS
# ============================================================

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


def _as_float(val: object) -> float:
    """Convert value to float safely"""
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
    """Sum all lien amounts from JSON string or list"""
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


# ============================================================
# CASE ROUTES
# ============================================================

@router.post("/{case_id}/fetch-comparables")
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


@router.get("/{case_id}/comparables", response_class=HTMLResponse)
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


@router.post("/{case_id}/documents/{doc_type}/ocr")
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


@router.post("/bulk/skip-trace")
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


@router.post("/bulk/property-lookup")
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


@router.get("/new", response_class=HTMLResponse)
def new_case_form(request: Request):
    return templates.TemplateResponse("cases_new.html", {"request": request, "error": None, "current_user": get_current_user(request)})


@router.post("/create")
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
        return templates.TemplateResponse("cases_new.html", {"request": request, "error": "Case # is required.", "current_user": get_current_user(request)})

    # Duplicate check
    exists = db.query(Case).filter(Case.case_number == cn).one_or_none()
    if exists:
        return templates.TemplateResponse("cases_new.html", {"request": request, "error": f"Case {cn} already exists (ID {exists.id}).", "current_user": get_current_user(request)})

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


@router.post("/{case_id}/update", response_class=HTMLResponse)
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


@router.get("/{case_id}", response_class=HTMLResponse)
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
            listing = p.get("listing", {})
            general = p.get("general", {})
            rehab_year_built = (
                listing.get("yearBuilt")
                or building.get("yearBuilt")
                or general.get("yearBuilt")
                or p.get("yearBuilt")
            )
            rehab_sqft = (
                listing.get("totalBuildingAreaSquareFeet")
                or building.get("livingAreaSqft")
                or general.get("buildingAreaSqft")
            )
            
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

    # V3: Get claim information
    claim_info = None
    try:
        active_claim = get_claim_for_case(db, case_id)
        if active_claim:
            claim_info = {
                "id": active_claim.id,
                "user_id": active_claim.user_id,
                "claimed_at": active_claim.claimed_at.isoformat() if active_claim.claimed_at else None,
                "score_at_claim": active_claim.score_at_claim,
                "price_cents": active_claim.price_cents,
                "price_display": active_claim.price_display,
            }
    except Exception as e:
        logger.warning(f"Could not load claim info for case {case_id}: {e}")

    # V3 Phase 3: Calculate visibility permissions
    visibility = None
    try:
        from app.models import User as UserModel
        current_user = get_current_user(request)
        logger.info(f"DEBUG: current_user type={type(current_user)}, value={current_user}")
        
        user_id = None
        if isinstance(current_user, dict):
            user_id = current_user.get("id")
        elif current_user:
            user_id = getattr(current_user, "id", None)
        
        logger.info(f"DEBUG: user_id={user_id}, case.assigned_to={case.assigned_to}")
        
        user_obj = None
        if user_id:
            user_obj = db.query(UserModel).filter(UserModel.id == user_id).first()
            if user_obj:
                logger.info(f"DEBUG: user_obj found - id={user_obj.id}, is_admin={getattr(user_obj, 'is_admin', None)}, role={getattr(user_obj, 'role', None)}")
            else:
                logger.warning(f"DEBUG: No user found for id={user_id}")
        
        visibility = get_case_visibility(case, user_obj)
        logger.info(f"DEBUG: Visibility result for case {case_id}: {visibility}")
    except Exception as e:
        import traceback
        logger.error(f"Visibility calculation failed for case {case_id}: {e}")
        logger.error(traceback.format_exc())
        # Default to RESTRICTED access on error (safe default)
        visibility = {
            'can_view_sensitive': False,
            'can_view_address': False,
            'can_view_skip_trace': False,
            'can_view_documents': False,
            'can_claim': True,
            'can_release': False,
            'is_owner': False,
            'is_claimed': case.assigned_to is not None,
        }

    return templates.TemplateResponse(
        "case_detail.html",
        {
            "request": request,
            "case": case,
            "current_user": get_current_user(request),
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
            "claim_info": claim_info,  # V3
            "visibility": visibility,  # V3 Phase 3
        },
    )

# NEW: Skip trace endpoint using BatchData


@router.post("/{case_id}/skip-trace", response_class=HTMLResponse)
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

    property_payload = load_property_for_case(case_id)
    property_data = parse_property_data(property_payload) if property_payload else None
    has_property_data = bool(property_payload and property_payload.get("results"))

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
            "offer_70": offer,
            "offer_80": flip_offer,
            "active_parcel_id": case.parcel_id,
            "notes": notes,
            "defendants": defendants,
            "skip_trace": skip_trace,
            "skip_trace_error": skip_trace_error,
            "property_payload": property_payload,
            "property_data": property_data,
            "property_error": None,
            "has_property_data": has_property_data,
            "rehab_condition": rehab_condition,
            "rehab_suggested": rehab_suggested,
            "property_overrides": property_overrides,
        },
    )


@router.get("/{case_id}/property-lookup", response_class=HTMLResponse)
def property_lookup_case_get(request: Request, case_id: int, db: Session = Depends(get_db)):
    return property_lookup_case(request, case_id, db)


@router.post("/{case_id}/property-lookup", response_class=HTMLResponse)
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
    property_payload: Optional[dict] = None
    property_data: Optional[dict] = None
    property_error: Optional[str] = None

    try:
        street, city, state, postal_code = get_case_address_components(case)
        raw_property_data = batchdata_property_lookup_all_attributes(
            street, city, state, postal_code
        )
        save_property_for_case(case.id, raw_property_data)
        property_payload = normalize_property_payload(raw_property_data)
        property_data = parse_property_data(property_payload)
    except HTTPException as exc:
        detail = exc.detail
        property_error = detail if isinstance(detail, str) else str(detail)
        # fall back to any previously saved data
        property_payload = load_property_for_case(case_id)
        if property_payload:
            property_data = parse_property_data(property_payload)
    except Exception as exc:
        property_error = f"Unexpected error during property lookup: {exc}"
        property_payload = load_property_for_case(case_id)
        if property_payload:
            property_data = parse_property_data(property_payload)

    offer = compute_offer_70(case.arv or 0, case.rehab or 0, case.closing_costs or 0)
    rehab_condition = getattr(case, "rehab_condition", "") or "Good"
    property_overrides = _parse_property_overrides(case)
    rehab_suggested = _estimate_rehab_from_property(
        property_data, rehab_condition, property_overrides
    )
    flip_offer = compute_offer_80(case.arv or 0, case.rehab or 0, case.closing_costs or 0)

    arv = float(case.arv) if case.arv else 0.0
    rehab = float(case.rehab) if case.rehab else 0.0
    closing_input = float(case.closing_costs) if case.closing_costs else None
    if closing_input:
        closing = closing_input
    else:
        closing = arv * 0.045 if arv > 0 else 0.0

    rehab_year_built = None
    rehab_sqft = None
    if property_payload and property_payload.get("results"):
        props = property_payload["results"].get("properties", [])
        if props:
            p = props[0]
            listing = p.get("listing", {})
            building = p.get("building", {})
            general = p.get("general", {})
            rehab_year_built = (
                listing.get("yearBuilt")
                or building.get("yearBuilt")
                or general.get("yearBuilt")
                or p.get("yearBuilt")
            )
            rehab_sqft = (
                listing.get("totalBuildingAreaSquareFeet")
                or building.get("livingAreaSqft")
                or general.get("buildingAreaSqft")
            )

    if property_overrides.get("year_built"):
        rehab_year_built = property_overrides.get("year_built")
    if property_overrides.get("sqft"):
        rehab_sqft = property_overrides.get("sqft")

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
            "offer_70": offer,
            "offer_80": flip_offer,
            "active_parcel_id": case.parcel_id,
            "notes": notes,
            "defendants": defendants,
            "skip_trace": skip_trace,
            "skip_trace_error": skip_trace_error,
            "property_payload": property_payload,
            "property_data": property_data,
            "property_error": property_error,
            "has_property_data": bool(property_payload and property_payload.get("results")),
            "arv": arv,
            "rehab": rehab,
            "rehab_condition": rehab_condition,
            "rehab_year_built": rehab_year_built,
            "rehab_sqft": rehab_sqft,
            "rehab_suggested": rehab_suggested,
            "closing": closing,
            "closing_input": closing_input,
            "property_overrides": property_overrides,
        },
    )


@router.post("/{case_id}/upload/verified")
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


@router.post("/{case_id}/upload/value_calc")
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


@router.post("/{case_id}/upload/mortgage")
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


@router.post("/{case_id}/upload/current-deed")
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


@router.post("/{case_id}/upload/previous-deed")
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


@router.post("/{case_id}/documents/upload")
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


@router.post("/{case_id}/notes/add")
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


@router.post("/{case_id}/property-overrides")
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


@router.get("/{case_id}/notes/{note_id}/delete")
def delete_note(case_id: int, note_id: int, db: Session = Depends(get_db)):
    note = db.query(Note).filter(Note.id == note_id, Note.case_id == case_id).first()
    if note:
        db.delete(note)
        db.commit()
    return RedirectResponse(url=f"/cases/{case_id}", status_code=303)


@router.get("/{case_id}/liens", response_model=list[OutstandingLien])
def get_outstanding_liens(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).get(case_id)  # type: ignore[call-arg]
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case.get_outstanding_liens()


@router.post("/{case_id}/liens")
def save_outstanding_liens(case_id: int, payload: OutstandingLiensUpdate, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # Convert liens to JSON string and save to outstanding_liens column
    import json
    case.outstanding_liens = json.dumps([l.dict() for l in payload.outstanding_liens])
    db.commit()
    
    # Return the saved liens
    return json.loads(case.outstanding_liens)


@router.get("", response_class=HTMLResponse)
def cases_list(
    request: Request,
    page: int = Query(1),
    page_size: Optional[int] = Query(None),
    show_archived: Optional[str] = Query("0"),
    show_new: Optional[str] = Query("0"),
    case: str = Query("", alias="case"),
    tag: str = Query(""),
    sort_by: str = Query("case_number"),
    sort_order: str = Query("desc"),
    claim_filter: str = Query(""),  # V3: Filter by claim status
    db: Session = Depends(get_db),
):
    # Handle page_size - default to 10, cap at 100
    if page_size is None or page_size < 5:
        page_size = 10
    if page_size > 100:
        page_size = 100
    
    # Handle show_archived - convert empty string to 0
    try:
        show_archived_int = int(show_archived) if show_archived else 0
    except (ValueError, TypeError):
        show_archived_int = 0
    
    # Handle show_new - convert empty string to 0
    try:
        show_new_int = int(show_new) if show_new else 0
    except (ValueError, TypeError):
        show_new_int = 0
    
    qry = db.query(Case)

    if not show_archived_int:
        qry = qry.filter(text("(archived IS NULL OR archived = 0)"))
    
    # Filter by user permissions
    user = get_current_user(request)
    if user and user.get("role") != "admin" and not getattr(user, "is_admin", False):
        user_id = user.get("id")
        role = user.get("role", "")
        
        if role == "subscriber":
            # Subscribers ONLY see cases assigned to them
            qry = qry.filter(text("assigned_to = :uid")).params(uid=user_id)
        else:
            # Analysts/Owners see assigned cases + unassigned cases
            qry = qry.filter(text("(assigned_to = :uid OR assigned_to IS NULL)")).params(uid=user_id)

    # V3: Apply claim filter
    user_id_for_claims = user.get("id") if user else None
    if claim_filter == "mine" and user_id_for_claims:
        qry = qry.filter(Case.assigned_to == user_id_for_claims)
    elif claim_filter == "available":
        qry = qry.filter(Case.assigned_to.is_(None))
    elif claim_filter == "claimed":
        qry = qry.filter(Case.assigned_to.isnot(None))

    # Show new: filter to cases without an address
    if show_new_int:
        qry = qry.filter(text("(address IS NULL OR address = '' OR TRIM(address) = '')"))
        qry = qry.filter(text("(address_override IS NULL OR address_override = '' OR TRIM(address_override) = '')"))

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
    # Dynamic ordering based on sort_by and sort_order
    sort_column = Case.case_number  # Default
    if sort_by == "case_number":
        sort_column = Case.case_number
    elif sort_by == "filing_datetime":
        sort_column = Case.filing_datetime
    elif sort_by == "style":
        sort_column = Case.style
    elif sort_by == "address":
        sort_column = Case.address
    else:
        sort_column = Case.case_number
    
    # Always sort claimed cases first (assigned_to IS NOT NULL)
    claimed_first = sql_case(
        (Case.assigned_to.isnot(None), 0),
        else_=1
    )
    
    if sort_order == "asc":
        qry = qry.order_by(claimed_first, sort_column.asc())
    else:
        qry = qry.order_by(claimed_first, sort_column.desc())

    total = qry.count()
    pages = (total + page_size - 1) // page_size
    offset = (page - 1) * page_size

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
                    quick = (
                        props[0].get("quickLists")
                        or props[0].get("quickList")
                        or {}
                    )
                except Exception:
                    continue

                tags: list[str] = []
                if isinstance(quick, dict):
                    for key, value in quick.items():
                        if not value:
                            continue
                        label = (
                            str(key)
                            .replace("_", " ")
                            .replace("  ", " ")
                            .strip()
                        )
                        if not label:
                            continue
                        # Title-case without mangling acronyms like HOA
                        parts = []
                        for part in label.split(" "):
                            if part.lower() == "hoa":
                                parts.append("HOA")
                            else:
                                parts.append(part[:1].upper() + part[1:])
                        tags.append(" ".join(parts))
                elif isinstance(quick, list):
                    for item in quick:
                        if isinstance(item, str):
                            label = item.strip()
                        elif isinstance(item, dict):
                            label = (
                                item.get("name")
                                or item.get("tag")
                                or item.get("label")
                                or ""
                            ).strip()
                        else:
                            label = ""
                        if label:
                            tags.append(label)
                elif isinstance(quick, str):
                    label = quick.strip()
                    if label:
                        tags.append(label)
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
            "show_archived": bool(show_archived_int),
            "show_new": bool(show_new_int),
            "search_query": case,
            "tag_filter": tag,
            "tag_options": list(tag_map.keys()),
            "page_size": page_size,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "defendants_count": defendants_count,
            "notes_count": notes_count,
            "docs_present": docs_present,
            "skiptrace_present": skiptrace_present,
            "quick_flags": quick_flags,
            "short_sale": short_sale,
            "current_user": get_current_user(request),
            "claim_filter": claim_filter,  # V3
        },
    )


@router.post("/archive")
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


@router.post("/export")
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


@router.post("/export_crm")
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


@router.post("/unarchive")
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


@router.post("/archive_async")
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


@router.post("/unarchive_async")
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


@router.post("/{case_id}/property/update-owner")
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


@router.post("/{case_id}/property/update-valuation")
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


@router.post("/{case_id}/property/update-demographics")
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


