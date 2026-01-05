# app/routes/report_routes.py
"""
Report Routes
- Reports dashboard page
- Portfolio/ROI/Deal summary PDF generation
- Report management API
"""

import io
import re
import zipfile
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Case
from app.services.auth_service import get_current_user

# Try to import report service
try:
    from app.services.report_service import (
        generate_case_report,
        build_case_report_bytes,
        _is_short_sale,
        _report_filename
    )
    REPORT_SERVICE_AVAILABLE = True
except ImportError:
    REPORT_SERVICE_AVAILABLE = False
    generate_case_report = None
    build_case_report_bytes = None
    _is_short_sale = lambda x: False
    _report_filename = lambda *args: "report.pdf"

try:
    from app.config import settings
except ImportError:
    class settings:
        upload_root = "uploads"

logger = logging.getLogger("pascowebapp.reports")

router = APIRouter()

# Templates - will be set by main.py
templates = None

# Upload root for saving reports
UPLOAD_ROOT = Path(settings.upload_root if hasattr(settings, 'upload_root') else "uploads")

# Try to import advanced reporting service
try:
    from app.services.advanced_reporting_service import (
        AdvancedReporting, get_reporting_service, REPORTLAB_AVAILABLE
    )
    REPORTING_AVAILABLE = REPORTLAB_AVAILABLE
except ImportError:
    REPORTING_AVAILABLE = False
    logger.warning("Advanced reporting service not available")


def init_templates(t):
    """Initialize templates from main app"""
    global templates
    templates = t


def init_upload_root(path: Path):
    """Initialize upload root path"""
    global UPLOAD_ROOT
    UPLOAD_ROOT = path


# ============================================================
# CASE REPORT ROUTES
# ============================================================

@router.get("/cases/{case_id}/report")
def case_report(case_id: int, db: Session = Depends(get_db)):
    """Generate and download single case report PDF"""
    if not REPORT_SERVICE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Report service not available")
    return generate_case_report(case_id, db)


@router.post("/cases/reports/download")
def download_case_reports(
    ids: List[int] = Form(default=[]),
    include_attachments: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Download multiple case reports as ZIP"""
    if not REPORT_SERVICE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Report service not available")
    
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
            file_name = _report_filename(
                cid,
                _safe_name(getattr(case, "case_number", "") or ""),
                _is_short_sale(case),
            )
            report_buf = build_case_report_bytes(cid, db, include_attachments=include_docs)
            report_buf.seek(0)
            zf.writestr(file_name, report_buf.read())

    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=case_reports.zip"},
    )


# ============================================================
# REPORTS DASHBOARD
# ============================================================

@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, db: Session = Depends(get_db)):
    """Reports dashboard page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    
    # Get case counts for the UI
    total_cases = db.query(Case).filter(
        (Case.archived == None) | (Case.archived == 0)
    ).count()
    
    cases_with_arv = db.query(Case).filter(
        (Case.archived == None) | (Case.archived == 0),
        Case.arv > 0
    ).count()
    
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "user": user,
        "current_user": user,
        "total_cases": total_cases,
        "cases_with_arv": cases_with_arv,
        "reporting_available": REPORTING_AVAILABLE,
    })


# ============================================================
# ADVANCED REPORTING API
# ============================================================

@router.post("/api/v2/reports/portfolio")
def api_generate_portfolio_report(
    request: Request,
    body: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """Generate portfolio analysis PDF report"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not REPORTING_AVAILABLE:
        raise HTTPException(
            status_code=501, 
            detail="Reporting not available. Install: pip install reportlab matplotlib"
        )
    
    try:
        reporting = get_reporting_service(db)
        
        pdf_bytes, filename = reporting.generate_portfolio_report(
            start_date=body.get("start_date"),
            end_date=body.get("end_date"),
            include_charts=body.get("include_charts", True),
            include_case_details=body.get("include_case_details", True)
        )
        
        # Save to temp file and return download link
        output_path = UPLOAD_ROOT / "reports"
        output_path.mkdir(exist_ok=True)
        
        file_path = output_path / filename
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)
        
        return {
            "status": "success",
            "filename": filename,
            "download_url": f"/uploads/reports/{filename}",
            "size": len(pdf_bytes)
        }
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {str(e)}")


@router.post("/api/v2/reports/roi-projection")
def api_generate_roi_report(
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Generate ROI projection report for selected cases"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not REPORTING_AVAILABLE:
        raise HTTPException(status_code=501, detail="Reporting not available")
    
    case_ids = body.get("case_ids", [])
    if not case_ids:
        raise HTTPException(status_code=400, detail="No cases selected")
    
    try:
        reporting = get_reporting_service(db)
        pdf_bytes, filename = reporting.generate_roi_projection_report(case_ids)
        
        output_path = UPLOAD_ROOT / "reports"
        output_path.mkdir(exist_ok=True)
        
        file_path = output_path / filename
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)
        
        return {
            "status": "success",
            "filename": filename,
            "download_url": f"/uploads/reports/{filename}",
            "size": len(pdf_bytes),
            "cases_included": len(case_ids)
        }
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"ROI report generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v2/reports/deal-summary/{case_id}")
def api_generate_deal_summary(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Generate single deal summary PDF"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not REPORTING_AVAILABLE:
        raise HTTPException(status_code=501, detail="Reporting not available")
    
    try:
        reporting = get_reporting_service(db)
        pdf_bytes, filename = reporting.generate_deal_summary_report(case_id)
        
        output_path = UPLOAD_ROOT / "reports"
        output_path.mkdir(exist_ok=True)
        
        file_path = output_path / filename
        with open(file_path, "wb") as f:
            f.write(pdf_bytes)
        
        return {
            "status": "success",
            "filename": filename,
            "download_url": f"/uploads/reports/{filename}",
            "size": len(pdf_bytes)
        }
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Deal summary generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v2/reports/list")
def api_list_reports(
    request: Request,
    db: Session = Depends(get_db)
):
    """List previously generated reports"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    reports_dir = UPLOAD_ROOT / "reports"
    if not reports_dir.exists():
        return {"reports": []}
    
    reports = []
    for file_path in sorted(reports_dir.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = file_path.stat()
        reports.append({
            "filename": file_path.name,
            "download_url": f"/uploads/reports/{file_path.name}",
            "size": stat.st_size,
            "size_formatted": f"{stat.st_size / 1024:.1f} KB" if stat.st_size < 1024*1024 else f"{stat.st_size / 1024 / 1024:.1f} MB",
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        })
    
    return {"reports": reports[:20]}  # Last 20 reports


@router.delete("/api/v2/reports/{filename}")
def api_delete_report(
    filename: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Delete a generated report"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Security: prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    file_path = UPLOAD_ROOT / "reports" / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    
    file_path.unlink()
    
    return {"status": "success", "deleted": filename}
