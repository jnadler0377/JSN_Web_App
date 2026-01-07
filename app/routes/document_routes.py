# app/routes/document_routes.py
"""
Document Management Routes - V3 with Ownership Checks
- Upload/download documents
- OCR processing
- Document history and versioning
- V3: Access control based on case ownership
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, File, UploadFile, Form, Body
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Case
from app.services.auth_service import get_current_user

logger = logging.getLogger("pascowebapp.documents")

router = APIRouter(prefix="/api/v2", tags=["documents"])

# Globals - will be set by main.py
UPLOAD_ROOT = None
DOCUMENT_MANAGER_AVAILABLE = False
get_document_manager = None
DocumentType = None


def init_document_routes(upload_root, doc_manager_available, doc_manager_func, doc_type_enum):
    """Initialize document routes with dependencies"""
    global UPLOAD_ROOT, DOCUMENT_MANAGER_AVAILABLE, get_document_manager, DocumentType
    UPLOAD_ROOT = upload_root
    DOCUMENT_MANAGER_AVAILABLE = doc_manager_available
    get_document_manager = doc_manager_func
    DocumentType = doc_type_enum


def _check_document_access(case: Case, user: dict, action: str = "view") -> bool:
    """
    V3: Check if user can access documents for a case.
    
    Args:
        case: The case the document belongs to
        user: Current user dict
        action: 'view' for listing, 'download' for downloading
    
    Returns:
        True if access allowed, False otherwise
    """
    if not user:
        return False
    
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    role = user.get("role", "subscriber") if isinstance(user, dict) else getattr(user, "role", "subscriber")
    
    # Admins can access everything
    if is_admin:
        return True
    
    # Owner can access their case documents
    if case.assigned_to is not None and case.assigned_to == user_id:
        return True
    
    # For unclaimed cases, analysts and owners can view/download
    if case.assigned_to is None and role in ('admin', 'analyst', 'owner'):
        return True
    
    # Subscribers can only view document metadata for unclaimed cases (not download)
    if action == "view" and case.assigned_to is None:
        return True
    
    return False


@router.get("/cases/{case_id}/documents")
def api_get_case_documents(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get all documents for a case"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    # Verify case exists
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # V3: Check access
    can_access = _check_document_access(case, user, "view")
    can_download = _check_document_access(case, user, "download")
    
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    documents = doc_manager.get_documents_for_case(case_id)
    stats = doc_manager.get_document_stats(case_id)
    
    # V3: If user can't download, mask file paths and add lock indicator
    if not can_download:
        for doc in documents:
            doc["file_path"] = "[locked]"
            doc["download_url"] = None
            doc["locked"] = True
            doc["lock_reason"] = "Claim this case to download documents"
    else:
        for doc in documents:
            doc["locked"] = False
    
    return {
        "case_id": case_id,
        "documents": documents,
        "stats": stats,
        "can_download": can_download,
        "document_types": [
            {"value": dt.value, "label": DocumentType.display_name(dt)}
            for dt in DocumentType
        ]
    }


@router.post("/cases/{case_id}/documents")
async def api_upload_document(
    case_id: int,
    request: Request,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    description: str = Form(None),
    db: Session = Depends(get_db)
):
    """Upload a new document"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    # Verify case exists
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # V3: Check if user can upload (must be owner or admin, or case is unclaimed and user is analyst+)
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    role = user.get("role", "subscriber") if isinstance(user, dict) else getattr(user, "role", "subscriber")
    
    can_upload = False
    if is_admin:
        can_upload = True
    elif case.assigned_to == user_id:
        can_upload = True
    elif case.assigned_to is None and role in ('admin', 'analyst', 'owner'):
        can_upload = True
    
    if not can_upload:
        raise HTTPException(status_code=403, detail="You must claim this case to upload documents")
    
    # Validate document type
    try:
        doc_type = DocumentType(document_type)
    except ValueError:
        doc_type = DocumentType.OTHER
    
    # Read file content
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    
    # Max file size: 50MB
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")
    
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    
    result = doc_manager.upload_document(
        case_id=case_id,
        filename=file.filename,
        content=content,
        doc_type=doc_type,
        mime_type=file.content_type or "application/octet-stream",
        user_id=user_id,
        description=description
    )
    
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    
    return result


@router.get("/documents/{doc_id}")
def api_get_document(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get single document details"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    document = doc_manager.get_document_by_id(doc_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # V3: Check access to the case this document belongs to
    case = db.query(Case).filter(Case.id == document.get("case_id")).first()
    if case:
        can_download = _check_document_access(case, user, "download")
        if not can_download:
            document["file_path"] = "[locked]"
            document["locked"] = True
            document["lock_reason"] = "Claim this case to access document details"
        else:
            document["locked"] = False
    
    return document


@router.get("/documents/{doc_id}/download")
def api_download_document(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Download document file - V3: Requires case ownership"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    document = doc_manager.get_document_by_id(doc_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # V3: Check case ownership before allowing download
    case = db.query(Case).filter(Case.id == document.get("case_id")).first()
    if case:
        if not _check_document_access(case, user, "download"):
            raise HTTPException(
                status_code=403, 
                detail="You must claim this case to download documents. "
                       "Claim the case from the case detail page to get access."
            )
    
    file_path = Path(UPLOAD_ROOT) / document["file_path"]
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    return FileResponse(
        path=str(file_path),
        filename=document["original_filename"] or document["filename"],
        media_type=document["mime_type"] or "application/octet-stream"
    )


@router.delete("/documents/{doc_id}")
def api_delete_document(
    doc_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Soft delete a document - V3: Requires case ownership"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    document = doc_manager.get_document_by_id(doc_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # V3: Check case ownership before allowing delete
    case = db.query(Case).filter(Case.id == document.get("case_id")).first()
    if case:
        user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
        is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
        
        if not is_admin and case.assigned_to != user_id:
            raise HTTPException(
                status_code=403, 
                detail="You must own this case to delete documents"
            )
    
    success = doc_manager.soft_delete_document(doc_id)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete document")
    
    return {"status": "success", "message": "Document deleted"}


@router.get("/cases/{case_id}/documents/history/{doc_type}")
def api_get_document_history(
    case_id: int,
    doc_type: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """Get version history for a document type"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    try:
        document_type = DocumentType(doc_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document type")
    
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    history = doc_manager.get_document_history(case_id, document_type)
    
    # V3: Check access for download URLs
    case = db.query(Case).filter(Case.id == case_id).first()
    can_download = False
    if case:
        can_download = _check_document_access(case, user, "download")
    
    # Mask download info if user can't download
    if not can_download:
        for version in history:
            version["file_path"] = "[locked]"
            version["locked"] = True
    
    return {
        "case_id": case_id,
        "document_type": doc_type,
        "versions": history,
        "can_download": can_download,
    }


@router.post("/documents/{doc_id}/ocr")
def api_run_document_ocr(
    doc_id: int,
    request: Request,
    body: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """Run OCR on a document and extract structured data with field mapping"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    if not DOCUMENT_MANAGER_AVAILABLE:
        raise HTTPException(status_code=501, detail="Document manager not available")
    
    # Get document
    doc_manager = get_document_manager(db, str(UPLOAD_ROOT))
    document = doc_manager.get_document_by_id(doc_id)
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # V3: Check case ownership for OCR
    case = db.query(Case).filter(Case.id == document.get("case_id")).first()
    if case:
        if not _check_document_access(case, user, "download"):
            raise HTTPException(
                status_code=403, 
                detail="You must claim this case to run OCR on documents"
            )
    
    # Get file path
    file_path = Path(UPLOAD_ROOT) / document["file_path"]
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
    # Check if it's a PDF
    mime_type = document.get("mime_type", "")
    if mime_type != "application/pdf" and not str(file_path).lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="OCR only supports PDF files")
    
    # Get document type from request or document
    doc_type = body.get("document_type") or document.get("document_type") or "other"
    
    # Run enhanced OCR
    try:
        # Try enhanced service first
        try:
            from app.services.ocr_service_enhanced import (
                extract_document_data_enhanced, 
                get_target_field_options
            )
            extracted_data = extract_document_data_enhanced(str(file_path), doc_type)
            target_fields = get_target_field_options()
        except ImportError:
            # Fall back to original service
            from app.services.ocr_service import extract_document_data
            extracted_data = extract_document_data(str(file_path), doc_type)
            target_fields = []
        
        return {
            "status": "success",
            "document_id": doc_id,
            "case_id": document.get("case_id"),
            "document_type": doc_type,
            "extracted_data": extracted_data,
            "target_fields": target_fields,
        }
        
    except ImportError:
        raise HTTPException(status_code=501, detail="OCR service not available. Install PyPDF2.")
    except Exception as e:
        logger.error(f"OCR failed for document {doc_id}: {e}")
        raise HTTPException(status_code=500, detail=f"OCR failed: {str(e)}")


@router.post("/cases/{case_id}/ocr-mapping")
def api_save_ocr_mapping(
    case_id: int,
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Save mapped OCR fields to case"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # Get case
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    
    # V3: Check case ownership for saving OCR mappings
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    is_admin = user.get("is_admin", False) if isinstance(user, dict) else getattr(user, "is_admin", False)
    role = user.get("role", "subscriber") if isinstance(user, dict) else getattr(user, "role", "subscriber")
    
    can_edit = False
    if is_admin:
        can_edit = True
    elif case.assigned_to == user_id:
        can_edit = True
    elif case.assigned_to is None and role in ('admin', 'analyst', 'owner'):
        can_edit = True
    
    if not can_edit:
        raise HTTPException(
            status_code=403, 
            detail="You must claim this case to save OCR mappings"
        )
    
    mappings = body.get("mappings", [])
    saved_fields = []
    
    # Get current liens
    try:
        current_liens = json.loads(case.outstanding_liens or "[]")
    except:
        current_liens = []
    
    # Get current property overrides
    try:
        property_overrides = json.loads(case.property_overrides or "{}")
    except:
        property_overrides = {}
    
    # Track if we're adding a new lien
    new_lien = {}
    
    for mapping in mappings:
        field_name = mapping.get("target_field")
        value = mapping.get("value")
        raw_value = mapping.get("raw_value", value)
        
        if not field_name or value is None:
            continue
        
        try:
            # Direct case fields
            if field_name == "case_number" and not case.case_number:
                case.case_number = str(value)
                saved_fields.append({"field": "case_number", "value": value})
                
            elif field_name == "address":
                case.address_override = str(value)
                saved_fields.append({"field": "address_override", "value": value})
                
            elif field_name == "parcel_id":
                case.parcel_id = str(value)
                saved_fields.append({"field": "parcel_id", "value": value})
                
            elif field_name == "filing_date":
                case.filing_datetime = str(value)
                saved_fields.append({"field": "filing_datetime", "value": value})
            
            # Financial fields
            elif field_name == "arv":
                try:
                    case.arv = float(str(value).replace(",", "").replace("$", ""))
                    saved_fields.append({"field": "arv", "value": case.arv})
                except:
                    pass
                    
            elif field_name == "rehab":
                try:
                    case.rehab = float(str(value).replace(",", "").replace("$", ""))
                    saved_fields.append({"field": "rehab", "value": case.rehab})
                except:
                    pass
                    
            elif field_name == "closing_costs":
                try:
                    case.closing_costs = float(str(value).replace(",", "").replace("$", ""))
                    saved_fields.append({"field": "closing_costs", "value": case.closing_costs})
                except:
                    pass
            
            # Lien fields
            elif field_name == "lien_holder":
                new_lien["holder"] = str(value)
                
            elif field_name == "lien_amount":
                try:
                    new_lien["amount"] = str(float(str(value).replace(",", "").replace("$", "")))
                except:
                    new_lien["amount"] = str(value)
                    
            elif field_name == "lien_type":
                new_lien["type"] = str(value)
                
            elif field_name == "lien_position":
                new_lien["position"] = str(value)
            
            # Property override fields
            elif field_name.startswith("property_"):
                prop_field = field_name.replace("property_", "")
                property_overrides[prop_field] = value
                saved_fields.append({"field": field_name, "value": value})
            
            # Defendant (add to defendants table)
            elif field_name == "defendant":
                from app.models import Defendant
                # Check if defendant already exists
                existing = db.query(Defendant).filter(
                    Defendant.case_id == case_id,
                    Defendant.name == str(value)
                ).first()
                
                if not existing:
                    new_defendant = Defendant(case_id=case_id, name=str(value))
                    db.add(new_defendant)
                    saved_fields.append({"field": "defendant", "value": value})
        
        except Exception as e:
            logger.error(f"Error mapping field {field_name}: {e}")
    
    # Save new lien if we have holder or amount
    if new_lien.get("holder") or new_lien.get("amount"):
        if "holder" not in new_lien:
            new_lien["holder"] = "Unknown"
        if "amount" not in new_lien:
            new_lien["amount"] = "0"
        current_liens.append(new_lien)
        case.outstanding_liens = json.dumps(current_liens)
        saved_fields.append({"field": "outstanding_liens", "value": new_lien})
    
    # Save property overrides
    if property_overrides:
        case.property_overrides = json.dumps(property_overrides)
    
    db.commit()
    
    return {
        "status": "success",
        "case_id": case_id,
        "saved_fields": saved_fields,
        "total_saved": len(saved_fields)
    }
