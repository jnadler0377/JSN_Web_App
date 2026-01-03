# app/services/ocr_service_enhanced.py
"""
Enhanced OCR Service with Field Mapping
Extracts structured data from legal documents and allows mapping to case fields
"""

from __future__ import annotations

import logging
import re
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger("pascowebapp.ocr_enhanced")


# ========================================
# EXTRACTION FUNCTIONS
# ========================================

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract raw text from PDF"""
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    
    text = ""
    
    # Try PyPDF2 first (this was working before)
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        if text.strip():
            logger.info(f"PyPDF2 extracted {len(text)} chars from {pdf_path}")
            return text
    except ImportError:
        logger.warning("PyPDF2 not installed")
    except Exception as exc:
        logger.warning(f"PyPDF2 extraction failed for {pdf_path}: {exc}")
    
    # Try PyMuPDF if PyPDF2 didn't work
    if not text.strip():
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
            for page in doc:
                page_text = page.get_text()
                if page_text:
                    text += page_text + "\n"
            doc.close()
            if text.strip():
                logger.info(f"PyMuPDF extracted {len(text)} chars")
                return text
        except ImportError:
            logger.info("PyMuPDF not installed")
        except Exception as exc:
            logger.warning(f"PyMuPDF extraction failed: {exc}")
    
    # Try pdfplumber as another fallback
    if not text.strip():
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            if text.strip():
                logger.info(f"pdfplumber extracted {len(text)} chars")
                return text
        except ImportError:
            pass
        except Exception as exc:
            logger.warning(f"pdfplumber extraction failed: {exc}")
    
    # Last resort: Tesseract OCR for scanned PDFs
    if not text.strip():
        logger.info("No text extracted with standard methods, attempting Tesseract OCR...")
        text = extract_text_with_tesseract(pdf_path)
    
    return text


def extract_text_with_tesseract(pdf_path: str) -> str:
    """Use Tesseract OCR to extract text from scanned PDF"""
    try:
        import pytesseract
        from pdf2image import convert_from_path
        
        # Convert PDF to images
        logger.info(f"Converting PDF to images for OCR: {pdf_path}")
        images = convert_from_path(pdf_path)
        
        # OCR each page
        text = ""
        for i, img in enumerate(images):
            logger.info(f"OCR processing page {i+1}/{len(images)}")
            page_text = pytesseract.image_to_string(img)
            text += page_text + "\n"
        
        logger.info(f"Tesseract OCR extracted {len(text)} chars")
        return text
    
    except ImportError as e:
        logger.error(f"OCR dependencies not installed: {e}")
        logger.error("Install with: pip install pytesseract pdf2image")
        logger.error("Also install Tesseract: https://github.com/UB-Mannheim/tesseract/wiki")
        return ""
    
    except Exception as exc:
        logger.error(f"Tesseract OCR failed: {exc}")
        return ""


def extract_currency_amounts(text: str) -> List[Dict[str, Any]]:
    """Extract all currency amounts with context"""
    pattern = r'(?:^|[^\d])(\$\s*([0-9]{1,3}(?:,?[0-9]{3})*(?:\.[0-9]{2})?))'
    
    amounts = []
    for match in re.finditer(pattern, text):
        try:
            full_match = match.group(1)
            amount_str = match.group(2).replace(",", "")
            amount = float(amount_str)
            
            # Get surrounding context (50 chars before and after)
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            context = text[start:end].replace('\n', ' ').strip()
            
            amounts.append({
                "value": amount,
                "formatted": f"${amount:,.2f}",
                "raw": full_match,
                "context": context
            })
        except ValueError:
            continue
    
    return amounts


def extract_addresses(text: str) -> List[Dict[str, Any]]:
    """Extract property addresses"""
    addresses = []
    
    # Pattern for street addresses
    # Matches: 123 Main Street, City, FL 34567
    patterns = [
        r'(\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Way|Boulevard|Blvd|Circle|Cir|Court|Ct|Place|Pl)\.?(?:\s*,?\s*[A-Za-z\s]+,?\s*(?:FL|Florida)\s*\d{5})?)',
        r'(?:Property Address|Located at|Property located at|Real property at)[:\s]+([^\n]+)',
        r'(?:EXHIBIT\s*["\']?A["\']?[:\s]+)([^\n]+\d{5})',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            address = match.strip() if isinstance(match, str) else match[0].strip()
            if len(address) > 10 and re.search(r'\d', address):
                addresses.append({
                    "value": address,
                    "type": "property_address"
                })
    
    return addresses


def extract_names(text: str) -> List[Dict[str, Any]]:
    """Extract person and company names"""
    names = []
    
    # Plaintiff pattern
    plaintiff_patterns = [
        r'(?:Plaintiff|Petitioner)[s]?[:\s]+([A-Z][A-Za-z\s,&.]+(?:Bank|LLC|Inc|Corp|Company|Association|Trust|N\.?A\.?))',
        r'([A-Z][A-Za-z\s,&.]+(?:Bank|LLC|Inc|Corp|Company|Association|Trust|N\.?A\.?))\s*,?\s*Plaintiff',
    ]
    
    for pattern in plaintiff_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            name = match.strip().rstrip(',')
            if len(name) > 3:
                names.append({"value": name, "type": "plaintiff", "role": "lender"})
    
    # Defendant pattern
    defendant_patterns = [
        r'(?:Defendant|Respondent)[s]?[:\s]+([A-Z][A-Za-z\s,]+)',
        r'(?:vs\.?|v\.)\s+([A-Z][A-Za-z\s,]+?)(?:,\s*et\.?\s*al\.?)?(?:\n|$)',
    ]
    
    for pattern in defendant_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            # Split by commas and "and"
            parts = re.split(r',|\sand\s', match)
            for part in parts:
                name = part.strip().rstrip(',')
                if len(name) > 3 and name.upper() != "ET AL":
                    names.append({"value": name, "type": "defendant", "role": "borrower"})
    
    # Creditor/Lienholder pattern
    creditor_patterns = [
        r'(?:Creditor|Lienholder|Lien Holder|Claimant)[:\s]+([A-Z][A-Za-z\s,&.]+)',
        r'(?:in favor of|payable to)[:\s]+([A-Z][A-Za-z\s,&.]+)',
    ]
    
    for pattern in creditor_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            name = match.strip().rstrip(',')
            if len(name) > 3:
                names.append({"value": name, "type": "creditor", "role": "lienholder"})
    
    return names


def extract_dates(text: str) -> List[Dict[str, Any]]:
    """Extract dates with context"""
    dates = []
    
    # Various date patterns
    patterns = [
        (r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', 'MM/DD/YYYY'),
        (r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', 'YYYY-MM-DD'),
        (r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})', 'Month DD, YYYY'),
    ]
    
    for pattern, fmt in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            try:
                # Get context
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                context = text[start:end].replace('\n', ' ').strip()
                
                dates.append({
                    "value": match.group(0),
                    "format": fmt,
                    "context": context
                })
            except:
                continue
    
    return dates


def extract_parcel_ids(text: str) -> List[Dict[str, Any]]:
    """Extract parcel/folio IDs"""
    parcels = []
    
    patterns = [
        r'(?:Parcel|Folio|Tax ID|Property ID)[:\s#]*([0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9A-Z-]+)',
        r'(\d{2}-\d{2}-\d{2}-\d{4}-\d{5}-\d{4})',  # Pasco format
        r'(\d{2}-\d{2}-\d{2}-\d{5}-\d{3}-\d{4})',  # Pinellas format
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            parcels.append({"value": match.strip(), "type": "parcel_id"})
    
    return parcels


def extract_case_numbers(text: str) -> List[Dict[str, Any]]:
    """Extract case numbers"""
    cases = []
    
    patterns = [
        r'(?:Case\s*(?:No\.?|Number|#)[:\s]*)([0-9]{2}-[0-9]{4}-[A-Z]{2}-[0-9]{6}-[A-Z]{4}-[A-Z]{2})',
        r'(?:Case\s*(?:No\.?|Number|#)[:\s]*)([0-9-]+[A-Z-]+[0-9-]+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            cases.append({"value": match.strip().upper(), "type": "case_number"})
    
    return cases


# ========================================
# DOCUMENT-SPECIFIC EXTRACTORS
# ========================================

def extract_verified_complaint_data(text: str) -> Dict[str, Any]:
    """
    Extract data from Verified Complaint / Lis Pendens
    Includes Exhibit A (Note) information
    """
    data = {
        "document_type": "verified_complaint",
        "fields": []
    }
    
    # Case number
    case_numbers = extract_case_numbers(text)
    if case_numbers:
        data["fields"].append({
            "name": "Case Number",
            "value": case_numbers[0]["value"],
            "target_field": "case_number",
            "confidence": "high"
        })
    
    # Property address (often in Exhibit A)
    addresses = extract_addresses(text)
    if addresses:
        data["fields"].append({
            "name": "Property Address",
            "value": addresses[0]["value"],
            "target_field": "address",
            "confidence": "medium"
        })
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["fields"].append({
            "name": "Parcel ID",
            "value": parcels[0]["value"],
            "target_field": "parcel_id",
            "confidence": "high"
        })
    
    # Names (plaintiff = lender, defendant = borrower)
    names = extract_names(text)
    for name_info in names:
        if name_info["type"] == "plaintiff":
            data["fields"].append({
                "name": "Plaintiff/Lender",
                "value": name_info["value"],
                "target_field": "lien_holder",
                "confidence": "high"
            })
        elif name_info["type"] == "defendant":
            data["fields"].append({
                "name": "Defendant/Borrower",
                "value": name_info["value"],
                "target_field": "defendant",
                "confidence": "high"
            })
    
    # Amount claimed (principal balance from Note)
    amounts = extract_currency_amounts(text)
    # Look for the largest amount (usually principal)
    if amounts:
        # Sort by value descending
        sorted_amounts = sorted(amounts, key=lambda x: x["value"], reverse=True)
        
        # Check context for specific amount types
        for amt in sorted_amounts:
            context_lower = amt.get("context", "").lower()
            if any(word in context_lower for word in ["principal", "note", "original", "loan amount"]):
                data["fields"].append({
                    "name": "Principal/Note Amount",
                    "value": amt["formatted"],
                    "raw_value": amt["value"],
                    "target_field": "lien_amount",
                    "confidence": "high",
                    "context": amt["context"]
                })
                break
            elif any(word in context_lower for word in ["total", "owed", "due", "claim"]):
                data["fields"].append({
                    "name": "Total Amount Owed",
                    "value": amt["formatted"],
                    "raw_value": amt["value"],
                    "target_field": "lien_amount",
                    "confidence": "medium",
                    "context": amt["context"]
                })
                break
        
        # If no specific match, use largest amount
        if not any(f.get("target_field") == "lien_amount" for f in data["fields"]):
            data["fields"].append({
                "name": "Amount (Largest Found)",
                "value": sorted_amounts[0]["formatted"],
                "raw_value": sorted_amounts[0]["value"],
                "target_field": "lien_amount",
                "confidence": "low",
                "context": sorted_amounts[0].get("context", "")
            })
    
    # Filing date
    dates = extract_dates(text)
    for date_info in dates:
        context_lower = date_info.get("context", "").lower()
        if any(word in context_lower for word in ["filed", "filing", "recorded"]):
            data["fields"].append({
                "name": "Filing Date",
                "value": date_info["value"],
                "target_field": "filing_datetime",
                "confidence": "high"
            })
            break
    
    return data


def extract_value_calc_data(text: str) -> Dict[str, Any]:
    """
    Extract data from Value Calculation documents
    Contains total amount owed and lender information
    """
    data = {
        "document_type": "value_calc",
        "fields": []
    }
    
    # Lender name
    names = extract_names(text)
    for name_info in names:
        if name_info["type"] in ["plaintiff", "creditor"]:
            data["fields"].append({
                "name": "Lender/Servicer",
                "value": name_info["value"],
                "target_field": "lien_holder",
                "confidence": "high"
            })
            break
    
    # Amounts - look for specific types
    amounts = extract_currency_amounts(text)
    
    for amt in amounts:
        context_lower = amt.get("context", "").lower()
        
        if any(word in context_lower for word in ["total", "payoff", "pay-off", "amount due"]):
            data["fields"].append({
                "name": "Total Payoff Amount",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": "lien_amount",
                "confidence": "high",
                "context": amt["context"]
            })
        elif any(word in context_lower for word in ["principal", "unpaid"]):
            data["fields"].append({
                "name": "Principal Balance",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": "lien_amount",
                "confidence": "medium",
                "context": amt["context"]
            })
        elif any(word in context_lower for word in ["interest", "accrued"]):
            data["fields"].append({
                "name": "Interest Owed",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": None,  # Informational only
                "confidence": "medium",
                "context": amt["context"]
            })
        elif any(word in context_lower for word in ["escrow", "tax", "insurance"]):
            data["fields"].append({
                "name": "Escrow/Tax/Insurance",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": None,
                "confidence": "medium",
                "context": amt["context"]
            })
    
    # Property address
    addresses = extract_addresses(text)
    if addresses:
        data["fields"].append({
            "name": "Property Address",
            "value": addresses[0]["value"],
            "target_field": "address",
            "confidence": "medium"
        })
    
    return data


def extract_lien_data(text: str) -> Dict[str, Any]:
    """
    Extract data from Lien documents
    Contains creditor and amount owed
    """
    data = {
        "document_type": "lien",
        "fields": []
    }
    
    # Creditor/Lienholder
    names = extract_names(text)
    for name_info in names:
        if name_info["type"] == "creditor":
            data["fields"].append({
                "name": "Creditor/Lienholder",
                "value": name_info["value"],
                "target_field": "lien_holder",
                "confidence": "high"
            })
        elif name_info["type"] == "plaintiff":
            data["fields"].append({
                "name": "Lienholder",
                "value": name_info["value"],
                "target_field": "lien_holder",
                "confidence": "medium"
            })
    
    # Lien type
    lien_types = {
        "hoa": ["hoa", "homeowner", "association", "community"],
        "tax": ["tax", "irs", "revenue", "property tax"],
        "mechanics": ["mechanic", "contractor", "construction"],
        "judgment": ["judgment", "judgement", "court"],
        "mortgage": ["mortgage", "deed of trust", "security instrument"],
    }
    
    text_lower = text.lower()
    for lien_type, keywords in lien_types.items():
        if any(kw in text_lower for kw in keywords):
            data["fields"].append({
                "name": "Lien Type",
                "value": lien_type.upper(),
                "target_field": "lien_type",
                "confidence": "medium"
            })
            break
    
    # Amount
    amounts = extract_currency_amounts(text)
    if amounts:
        sorted_amounts = sorted(amounts, key=lambda x: x["value"], reverse=True)
        
        for amt in sorted_amounts:
            context_lower = amt.get("context", "").lower()
            if any(word in context_lower for word in ["claim", "lien", "owed", "due", "amount"]):
                data["fields"].append({
                    "name": "Lien Amount",
                    "value": amt["formatted"],
                    "raw_value": amt["value"],
                    "target_field": "lien_amount",
                    "confidence": "high",
                    "context": amt["context"]
                })
                break
        
        # Fallback to largest
        if not any(f.get("target_field") == "lien_amount" for f in data["fields"]):
            data["fields"].append({
                "name": "Lien Amount",
                "value": sorted_amounts[0]["formatted"],
                "raw_value": sorted_amounts[0]["value"],
                "target_field": "lien_amount",
                "confidence": "low"
            })
    
    # Recording date
    dates = extract_dates(text)
    for date_info in dates:
        context_lower = date_info.get("context", "").lower()
        if any(word in context_lower for word in ["recorded", "filed", "date"]):
            data["fields"].append({
                "name": "Recording Date",
                "value": date_info["value"],
                "target_field": "lien_date",
                "confidence": "medium"
            })
            break
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["fields"].append({
            "name": "Parcel ID",
            "value": parcels[0]["value"],
            "target_field": "parcel_id",
            "confidence": "high"
        })
    
    return data


def extract_mortgage_data(text: str) -> Dict[str, Any]:
    """
    Extract data from Mortgage documents
    Maps to mortgage history section
    """
    data = {
        "document_type": "mortgage",
        "fields": []
    }
    
    # Lender
    names = extract_names(text)
    for name_info in names:
        if name_info["type"] == "plaintiff" or "bank" in name_info["value"].lower():
            data["fields"].append({
                "name": "Lender",
                "value": name_info["value"],
                "target_field": "mortgage_lender",
                "confidence": "high"
            })
            break
    
    # Borrower
    for name_info in names:
        if name_info["type"] == "defendant":
            data["fields"].append({
                "name": "Borrower",
                "value": name_info["value"],
                "target_field": "mortgage_borrower",
                "confidence": "high"
            })
    
    # Original loan amount
    amounts = extract_currency_amounts(text)
    for amt in amounts:
        context_lower = amt.get("context", "").lower()
        if any(word in context_lower for word in ["original", "principal", "loan amount", "sum of"]):
            data["fields"].append({
                "name": "Original Loan Amount",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": "mortgage_amount",
                "confidence": "high",
                "context": amt["context"]
            })
            break
    
    # Interest rate
    rate_pattern = r'(\d+\.?\d*)\s*%?\s*(?:percent|%|per annum|annual|interest)'
    rate_matches = re.findall(rate_pattern, text, re.IGNORECASE)
    if rate_matches:
        data["fields"].append({
            "name": "Interest Rate",
            "value": f"{rate_matches[0]}%",
            "raw_value": float(rate_matches[0]),
            "target_field": "mortgage_rate",
            "confidence": "medium"
        })
    
    # Recording date / Origination date
    dates = extract_dates(text)
    for date_info in dates:
        context_lower = date_info.get("context", "").lower()
        if any(word in context_lower for word in ["recorded", "origination", "dated", "executed"]):
            data["fields"].append({
                "name": "Mortgage Date",
                "value": date_info["value"],
                "target_field": "mortgage_date",
                "confidence": "medium"
            })
            break
    
    # Property address
    addresses = extract_addresses(text)
    if addresses:
        data["fields"].append({
            "name": "Property Address",
            "value": addresses[0]["value"],
            "target_field": "address",
            "confidence": "medium"
        })
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["fields"].append({
            "name": "Parcel ID",
            "value": parcels[0]["value"],
            "target_field": "parcel_id",
            "confidence": "high"
        })
    
    return data


def extract_deed_data(text: str) -> Dict[str, Any]:
    """Extract data from Deed documents"""
    data = {
        "document_type": "deed",
        "fields": []
    }
    
    # Grantor (seller)
    grantor_pattern = r'Grantor[s]?[:\s]+([A-Z][A-Za-z\s,&]+)'
    grantor_matches = re.findall(grantor_pattern, text, re.IGNORECASE)
    if grantor_matches:
        data["fields"].append({
            "name": "Grantor (Seller)",
            "value": grantor_matches[0].strip(),
            "target_field": None,
            "confidence": "high"
        })
    
    # Grantee (buyer)
    grantee_pattern = r'Grantee[s]?[:\s]+([A-Z][A-Za-z\s,&]+)'
    grantee_matches = re.findall(grantee_pattern, text, re.IGNORECASE)
    if grantee_matches:
        data["fields"].append({
            "name": "Grantee (Buyer)",
            "value": grantee_matches[0].strip(),
            "target_field": None,
            "confidence": "high"
        })
    
    # Sale price
    amounts = extract_currency_amounts(text)
    for amt in amounts:
        context_lower = amt.get("context", "").lower()
        if any(word in context_lower for word in ["consideration", "sale", "purchase", "price"]):
            data["fields"].append({
                "name": "Sale Price",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": "last_sale_price",
                "confidence": "high",
                "context": amt["context"]
            })
            break
    
    # Property address
    addresses = extract_addresses(text)
    if addresses:
        data["fields"].append({
            "name": "Property Address",
            "value": addresses[0]["value"],
            "target_field": "address",
            "confidence": "medium"
        })
    
    # Parcel ID
    parcels = extract_parcel_ids(text)
    if parcels:
        data["fields"].append({
            "name": "Parcel ID",
            "value": parcels[0]["value"],
            "target_field": "parcel_id",
            "confidence": "high"
        })
    
    return data


# ========================================
# MAIN EXTRACTION FUNCTION
# ========================================

def extract_document_data_enhanced(pdf_path: str, document_type: str) -> Dict[str, Any]:
    """
    Main entry point for enhanced OCR extraction
    Returns structured data with field mapping suggestions
    """
    logger.info(f"Starting enhanced OCR: {pdf_path} (type: {document_type})")
    
    # Extract raw text
    try:
        text = extract_text_from_pdf(pdf_path)
    except Exception as e:
        logger.error(f"Text extraction error: {e}")
        return {
            "document_type": document_type,
            "full_text": "",
            "fields": [],
            "error": f"Text extraction failed: {str(e)}"
        }
    
    if not text or not text.strip():
        logger.warning(f"No text extracted from {pdf_path}")
        return {
            "document_type": document_type,
            "full_text": "",
            "fields": [],
            "error": "No text could be extracted. This may be a scanned PDF.",
            "suggestion": "Install OCR support: pip install pytesseract pdf2image (and Tesseract OCR)"
        }
    
    logger.info(f"Extracted {len(text)} characters, now parsing for {document_type}...")
    
    # Route to appropriate extractor
    if document_type in ["verified_complaint", "lis_pendens"]:
        data = extract_verified_complaint_data(text)
    elif document_type == "value_calc":
        data = extract_value_calc_data(text)
    elif document_type == "lien":
        data = extract_lien_data(text)
    elif document_type == "mortgage":
        data = extract_mortgage_data(text)
    elif document_type in ["deed", "current_deed", "previous_deed"]:
        data = extract_deed_data(text)
    else:
        # Generic extraction
        data = {
            "document_type": document_type,
            "fields": []
        }
        
        # Add generic extractions
        addresses = extract_addresses(text)
        if addresses:
            data["fields"].append({
                "name": "Address Found",
                "value": addresses[0]["value"],
                "target_field": "address",
                "confidence": "low"
            })
        
        parcels = extract_parcel_ids(text)
        if parcels:
            data["fields"].append({
                "name": "Parcel ID Found",
                "value": parcels[0]["value"],
                "target_field": "parcel_id",
                "confidence": "medium"
            })
        
        amounts = extract_currency_amounts(text)
        for i, amt in enumerate(amounts[:5]):  # Top 5 amounts
            data["fields"].append({
                "name": f"Amount #{i+1}",
                "value": amt["formatted"],
                "raw_value": amt["value"],
                "target_field": None,
                "confidence": "low",
                "context": amt.get("context", "")
            })
    
    # Add full text for reference
    data["full_text"] = text
    data["text_length"] = len(text)
    
    logger.info(f"Extracted {len(data.get('fields', []))} fields from {document_type}")
    
    return data


# ========================================
# FIELD MAPPING DEFINITIONS
# ========================================

# Available target fields for mapping
TARGET_FIELDS = {
    "case_number": {"label": "Case Number", "type": "string"},
    "address": {"label": "Property Address", "type": "string"},
    "parcel_id": {"label": "Parcel ID", "type": "string"},
    "filing_datetime": {"label": "Filing Date", "type": "date"},
    "arv": {"label": "ARV (After Repair Value)", "type": "currency"},
    "rehab": {"label": "Rehab Estimate", "type": "currency"},
    "closing_costs": {"label": "Closing Costs", "type": "currency"},
    
    # Lien fields (stored in outstanding_liens JSON)
    "lien_holder": {"label": "Lien Holder", "type": "string", "array": "outstanding_liens"},
    "lien_amount": {"label": "Lien Amount", "type": "currency", "array": "outstanding_liens"},
    "lien_type": {"label": "Lien Type", "type": "string", "array": "outstanding_liens"},
    "lien_date": {"label": "Lien Date", "type": "date", "array": "outstanding_liens"},
    
    # Mortgage fields (stored in property_overrides JSON)
    "mortgage_lender": {"label": "Mortgage Lender", "type": "string", "array": "property_overrides"},
    "mortgage_borrower": {"label": "Mortgage Borrower", "type": "string", "array": "property_overrides"},
    "mortgage_amount": {"label": "Mortgage Amount", "type": "currency", "array": "property_overrides"},
    "mortgage_rate": {"label": "Interest Rate", "type": "percentage", "array": "property_overrides"},
    "mortgage_date": {"label": "Mortgage Date", "type": "date", "array": "property_overrides"},
    
    # Defendant (separate table)
    "defendant": {"label": "Defendant Name", "type": "string", "table": "defendants"},
}

def get_target_field_options():
    """Return list of available target fields for UI dropdown"""
    return [
        {"value": key, "label": info["label"], "type": info["type"]}
        for key, info in TARGET_FIELDS.items()
    ]
