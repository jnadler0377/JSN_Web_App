# app/services/ocr_service.py
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime

from app.config import settings

logger = logging.getLogger("pascowebapp.ocr")


# ========================================
# VALIDATION FUNCTIONS (NEW - Security Fix)
# ========================================

def validate_parcel_id(parcel_id: str) -> Optional[str]:
    """
    Validate and clean a parcel ID
    Returns cleaned parcel_id or None if invalid
    
    Valid formats:
    - Pasco: XX-XX-XX-XXXX-XXXXX-XXXX
    - Pinellas: XX-XX-XX-XXXXX-XXX-XXXX
    """
    if not parcel_id or not isinstance(parcel_id, str):
        return None
    
    parcel_id = parcel_id.strip()
    
    # Check length (reasonable bounds)
    if len(parcel_id) < 10 or len(parcel_id) > 50:
        logger.warning(f"Parcel ID length out of bounds: {len(parcel_id)}")
        return None
    
    # Must match one of the known formats
    pasco_pattern = r'^\d{2}-\d{2}-\d{2}-\d{4}-\d{5}-\d{4}$'
    pinellas_pattern = r'^\d{2}-\d{2}-\d{2}-\d{5}-\d{3}-\d{4}$'
    
    if re.match(pasco_pattern, parcel_id):
        logger.debug(f"Valid Pasco parcel ID: {parcel_id}")
        return parcel_id
    elif re.match(pinellas_pattern, parcel_id):
        logger.debug(f"Valid Pinellas parcel ID: {parcel_id}")
        return parcel_id
    else:
        logger.warning(f"Parcel ID doesn't match known formats: {parcel_id}")
        return None


def validate_address(address: str) -> Optional[str]:
    """
    Validate and clean an address
    Returns cleaned address or None if invalid
    """
    if not address or not isinstance(address, str):
        return None
    
    address = address.strip()
    
    # Length check (must be reasonable)
    if len(address) < 10:
        logger.warning(f"Address too short: {address}")
        return None
    
    if len(address) > 200:
        logger.warning(f"Address too long ({len(address)} chars), truncating")
        address = address[:200]
    
    # Must contain numbers (street number)
    if not re.search(r'\d', address):
        logger.warning(f"Address has no numbers: {address}")
        return None
    
    # Must contain letters (street name)
    if not re.search(r'[A-Za-z]', address):
        logger.warning(f"Address has no letters: {address}")
        return None
    
    # Remove/check for dangerous characters
    dangerous_chars = ['<', '>', '{', '}', '\\', '|', '^', '~', '[', ']', '`', '\x00']
    for char in dangerous_chars:
        if char in address:
            logger.warning(f"Address contains dangerous character '{char}': {address}")
            return None
    
    # Remove excessive whitespace
    address = re.sub(r'\s+', ' ', address)
    
    logger.debug(f"Valid address: {address}")
    return address


def validate_case_number(case_number: str) -> Optional[str]:
    """
    Validate case number format
    Returns cleaned case_number or None if invalid
    
    Standard format: XX-XXXX-XX-XXXXXX-XXXX-XX
    """
    if not case_number or not isinstance(case_number, str):
        return None
    
    case_number = case_number.strip().upper()
    
    # Length check
    if len(case_number) < 15 or len(case_number) > 30:
        logger.warning(f"Case number length out of bounds: {case_number}")
        return None
    
    # Standard format: XX-XXXX-XX-XXXXXX-XXXX-XX
    pattern = r'^\d{2}-\d{4}-[A-Z]{2}-\d{6}-[A-Z]{4}-[A-Z]{2}$'
    
    if re.match(pattern, case_number):
        logger.debug(f"Valid case number: {case_number}")
        return case_number
    else:
        logger.warning(f"Case number doesn't match expected format: {case_number}")
        return None


def validate_date_string(date_str: str) -> Optional[str]:
    """
    Validate date string and normalize format
    Returns YYYY-MM-DD format or None if invalid
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    
    # Try parsing various formats
    formats = [
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%B %d %Y",
        "%m/%d/%y",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # Sanity check: between 1990 and 2050
            if 1990 <= dt.year <= 2050:
                normalized = dt.strftime("%Y-%m-%d")
                logger.debug(f"Valid date: {date_str} -> {normalized}")
                return normalized
            else:
                logger.warning(f"Date year out of range: {dt.year}")
        except ValueError:
            continue
    
    logger.warning(f"Could not parse date: {date_str}")
    return None


# ========================================
# ORIGINAL EXTRACTION FUNCTIONS
# ========================================

def extract_text_from_pdf(pdf_path: str) -> str:
    """
    Extract raw text from PDF using PyPDF2 or pytesseract
    """
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    
    text = ""
    
    # Try PyPDF2 first (for text-based PDFs)
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            text += page.extract_text() + "\n"
    except Exception as exc:
        logger.warning(f"PyPDF2 extraction failed for {pdf_path}: {exc}")
    
    # If no text extracted, try OCR (for scanned PDFs)
    if not text.strip() and settings.enable_ocr:
        text = extract_text_with_tesseract(pdf_path)
    
    return text


def extract_text_with_tesseract(pdf_path: str) -> str:
    """
    Use Tesseract OCR to extract text from scanned PDF
    Requires: pip install pytesseract pdf2image
    """
    try:
        import pytesseract
        from pdf2image import convert_from_path
        
        # Convert PDF to images
        images = convert_from_path(pdf_path)
        
        # OCR each page
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img) + "\n"
        
        return text
    
    except ImportError:
        logger.error("pytesseract or pdf2image not installed. OCR unavailable.")
        return ""
    
    except Exception as exc:
        logger.error(f"Tesseract OCR failed for {pdf_path}: {exc}")
        return ""


def extract_currency_amounts(text: str) -> List[float]:
    """
    Extract all currency amounts from text
    """
    # Patterns for currency: $1,234.56 or $1234.56
    pattern = r'\$\s*([0-9]{1,3}(?:,?[0-9]{3})*(?:\.[0-9]{2})?)'
    
    amounts = []
    for match in re.finditer(pattern, text):
        try:
            amount_str = match.group(1).replace(",", "")
            amount = float(amount_str)
            amounts.append(amount)
        except ValueError:
            continue
    
    return amounts


def extract_dates(text: str) -> List[str]:
    """
    Extract dates in various formats
    """
    dates = []
    
    # MM/DD/YYYY or MM-DD-YYYY
    pattern1 = r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b'
    dates.extend(re.findall(pattern1, text))
    
    # Month DD, YYYY (e.g., January 15, 2024)
    pattern2 = r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b'
    dates.extend(re.findall(pattern2, text))
    
    return ["/".join(d) if isinstance(d, tuple) else d for d in dates]


def extract_parcel_ids(text: str) -> List[str]:
    """
    Extract parcel IDs (common Florida formats)
    """
    parcels = []
    
    # Pasco format: XX-XX-XX-XXXX-XXXXX-XXXX
    pattern1 = r'\b\d{2}-\d{2}-\d{2}-\d{4}-\d{5}-\d{4}\b'
    parcels.extend(re.findall(pattern1, text))
    
    # Pinellas format: XX-XX-XX-XXXXX-XXX-XXXX
    pattern2 = r'\b\d{2}-\d{2}-\d{2}-\d{5}-\d{3}-\d{4}\b'
    parcels.extend(re.findall(pattern2, text))
    
    return parcels


def extract_case_numbers(text: str) -> List[str]:
    """
    Extract case numbers (format: XX-XXXX-XX-XXXXXX-XXXX-XX)
    """
    pattern = r'\b\d{2}-\d{4}-[A-Z]{2}-\d{6}-[A-Z]{4}-[A-Z]{2}\b'
    return re.findall(pattern, text)


def extract_mortgage_data(text: str) -> Dict[str, Any]:
    """
    Extract structured data from mortgage document
    """
    data: Dict[str, Any] = {
        "document_type": "mortgage",
        "full_text": text,
    }
    
    # Mortgage/loan amount
    amounts = extract_currency_amounts(text)
    if amounts:
        # Usually the largest amount is the principal
        data["loan_amount"] = max(amounts)
    
    # Interest rate
    rate_pattern = r'(\d+\.?\d*)\s*%?\s*(?:percent|per\s*annum|interest\s*rate)'
    rate_matches = re.findall(rate_pattern, text, re.IGNORECASE)
    if rate_matches:
        try:
            data["interest_rate"] = float(rate_matches[0])
        except ValueError:
            pass
    
    # Lender name (often after "Lender:" or "Mortgagee:")
    lender_pattern = r'(?:Lender|Mortgagee|Bank):\s*([A-Z][A-Za-z\s&.,]+(?:Bank|Credit Union|Mortgage|LLC|Inc|Corp))'
    lender_matches = re.findall(lender_pattern, text, re.IGNORECASE)
    if lender_matches:
        data["lender_name"] = lender_matches[0].strip()
    
    # Borrower name(s)
    borrower_pattern = r'Borrower[s]?:\s*([A-Z][A-Za-z\s,&]+)'
    borrower_matches = re.findall(borrower_pattern, text, re.IGNORECASE)
    if borrower_matches:
        data["borrowers"] = [b.strip() for b in borrower_matches[0].split(",")]
    
    # Property address
    # Look for common patterns after "Property Address" or "Legal Description"
    address_pattern = r'(?:Property Address|Located at):\s*([0-9]+\s+[A-Za-z\s,]+\s+[A-Z]{2}\s+\d{5})'
    address_matches = re.findall(address_pattern, text, re.IGNORECASE)
    if address_matches:
        data["property_address"] = address_matches[0].strip()
    
    # Recording date
    dates = extract_dates(text)
    if dates:
        data["recording_date"] = dates[0]  # Usually first date mentioned
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["parcel_id"] = parcels[0]
    
    return data


def extract_deed_data(text: str) -> Dict[str, Any]:
    """
    Extract structured data from deed document
    """
    data: Dict[str, Any] = {
        "document_type": "deed",
        "full_text": text,
    }
    
    # Grantor (seller)
    grantor_pattern = r'Grantor[s]?:\s*([A-Z][A-Za-z\s,&]+)'
    grantor_matches = re.findall(grantor_pattern, text, re.IGNORECASE)
    if grantor_matches:
        data["grantors"] = [g.strip() for g in grantor_matches[0].split(",")]
    
    # Grantee (buyer)
    grantee_pattern = r'Grantee[s]?:\s*([A-Z][A-Za-z\s,&]+)'
    grantee_matches = re.findall(grantee_pattern, text, re.IGNORECASE)
    if grantee_matches:
        data["grantees"] = [g.strip() for g in grantee_matches[0].split(",")]
    
    # Sale price / consideration
    consideration_pattern = r'(?:Consideration|Purchase Price|Sale Price):\s*\$?\s*([0-9,]+\.?\d*)'
    consideration_matches = re.findall(consideration_pattern, text, re.IGNORECASE)
    if consideration_matches:
        try:
            amount_str = consideration_matches[0].replace(",", "")
            data["sale_price"] = float(amount_str)
        except ValueError:
            pass
    
    # Recording date
    dates = extract_dates(text)
    if dates:
        data["recording_date"] = dates[0]
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["parcel_id"] = parcels[0]
    
    # Property address
    address_pattern = r'(?:Property Address|Located at):\s*([0-9]+\s+[A-Za-z\s,]+\s+[A-Z]{2}\s+\d{5})'
    address_matches = re.findall(address_pattern, text, re.IGNORECASE)
    if address_matches:
        data["property_address"] = address_matches[0].strip()
    
    return data


def extract_lis_pendens_data(text: str) -> Dict[str, Any]:
    """
    Extract structured data from lis pendens / verified complaint
    """
    data: Dict[str, Any] = {
        "document_type": "lis_pendens",
        "full_text": text,
    }
    
    # Case number
    case_numbers = extract_case_numbers(text)
    if case_numbers:
        data["case_number"] = case_numbers[0]
    
    # Plaintiff
    plaintiff_pattern = r'Plaintiff[s]?:\s*([A-Z][A-Za-z\s,&.]+(?:Bank|LLC|Inc|Corp|Company))'
    plaintiff_matches = re.findall(plaintiff_pattern, text, re.IGNORECASE)
    if plaintiff_matches:
        data["plaintiff"] = plaintiff_matches[0].strip()
    
    # Defendants
    defendant_pattern = r'Defendant[s]?:\s*([A-Z][A-Za-z\s,&]+)'
    defendant_matches = re.findall(defendant_pattern, text, re.IGNORECASE)
    if defendant_matches:
        # Split by commas or "and"
        defendants_text = defendant_matches[0]
        defendants = [d.strip() for d in re.split(r',|\sand\s', defendants_text)]
        data["defendants"] = [d for d in defendants if d and len(d) > 2]
    
    # Amount claimed
    amounts = extract_currency_amounts(text)
    if amounts:
        data["amount_claimed"] = max(amounts)
    
    # Filing date
    dates = extract_dates(text)
    if dates:
        data["filing_date"] = dates[0]
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["parcel_id"] = parcels[0]
    
    return data


def extract_document_data(pdf_path: str, document_type: str) -> Dict[str, Any]:
    """
    Main entry point for OCR extraction
    
    Args:
        pdf_path: Path to PDF file
        document_type: One of: mortgage, deed, lis_pendens, other
    
    Returns:
        Dict with extracted structured data
    """
    logger.info(f"Starting OCR extraction: {pdf_path} (type: {document_type})")
    
    # Extract raw text
    text = extract_text_from_pdf(pdf_path)
    
    if not text.strip():
        logger.warning(f"No text extracted from {pdf_path}")
        return {
            "document_type": document_type,
            "full_text": "",
            "structured_data": {},
            "error": "No text could be extracted",
        }
    
    # Extract structured data based on document type
    if document_type == "mortgage":
        structured_data = extract_mortgage_data(text)
    elif document_type in ["deed", "current_deed", "previous_deed"]:
        structured_data = extract_deed_data(text)
    elif document_type in ["lis_pendens", "verified_complaint"]:
        structured_data = extract_lis_pendens_data(text)
    else:
        # Generic extraction for unknown types
        structured_data = {
            "document_type": document_type,
            "amounts": extract_currency_amounts(text),
            "dates": extract_dates(text),
            "parcel_ids": extract_parcel_ids(text),
            "case_numbers": extract_case_numbers(text),
        }
    
    logger.info(f"OCR extraction complete: {len(structured_data)} fields extracted")
    
    return {
        "full_text": text,
        "structured_data": structured_data,
        "document_type": document_type,
    }


def auto_populate_case_from_ocr(case_id: int, ocr_results: Dict[str, Any]) -> Dict[str, str]:
    """
    Automatically populate case fields from OCR results
    ✅ NOW WITH VALIDATION (Security Fix)
    
    Returns:
        Dict of field_name -> new_value that were auto-populated
    """
    from app.database import SessionLocal
    from app.models import Case
    
    db = SessionLocal()
    populated_fields = {}
    validation_warnings = []
    
    try:
        case = db.get(Case, case_id) if hasattr(db, "get") else db.query(Case).get(case_id)
        if not case:
            logger.error(f"Case {case_id} not found")
            return populated_fields
        
        structured = ocr_results.get("structured_data", {})
        
        # Parcel ID - VALIDATED ✅
        if not case.parcel_id and structured.get("parcel_id"):
            validated_parcel = validate_parcel_id(structured["parcel_id"])
            if validated_parcel:
                case.parcel_id = validated_parcel
                populated_fields["parcel_id"] = validated_parcel
                logger.info(f"Auto-populated parcel_id: {validated_parcel}")
            else:
                msg = f"Invalid parcel_id from OCR: {structured['parcel_id']}"
                logger.warning(msg)
                validation_warnings.append(msg)
        
        # Address - VALIDATED ✅
        if not case.address and structured.get("property_address"):
            validated_address = validate_address(structured["property_address"])
            if validated_address:
                case.address = validated_address
                populated_fields["address"] = validated_address
                logger.info(f"Auto-populated address: {validated_address}")
            else:
                msg = f"Invalid address from OCR: {structured['property_address']}"
                logger.warning(msg)
                validation_warnings.append(msg)
        
        # Case number - VALIDATED ✅
        if not case.case_number and structured.get("case_number"):
            validated_case_num = validate_case_number(structured["case_number"])
            if validated_case_num:
                case.case_number = validated_case_num
                populated_fields["case_number"] = validated_case_num
                logger.info(f"Auto-populated case_number: {validated_case_num}")
            else:
                msg = f"Invalid case_number from OCR: {structured['case_number']}"
                logger.warning(msg)
                validation_warnings.append(msg)
        
        # Filing date - VALIDATED ✅
        if not case.filing_datetime and structured.get("filing_date"):
            validated_date = validate_date_string(structured["filing_date"])
            if validated_date:
                case.filing_datetime = validated_date
                populated_fields["filing_datetime"] = validated_date
                logger.info(f"Auto-populated filing_datetime: {validated_date}")
            else:
                msg = f"Invalid filing_date from OCR: {structured['filing_date']}"
                logger.warning(msg)
                validation_warnings.append(msg)
        
        db.commit()
        
        if populated_fields:
            logger.info(f"Auto-populated {len(populated_fields)} fields for case {case_id}: {list(populated_fields.keys())}")
        
        if validation_warnings:
            logger.warning(f"OCR validation warnings for case {case_id}: {len(validation_warnings)} field(s) rejected")
    
    except Exception as exc:
        logger.error(f"Error auto-populating case {case_id} from OCR: {exc}", exc_info=True)
        db.rollback()
    
    finally:
        db.close()
    
    return populated_fields
