# app/services/report_service.py
"""
Modernized Case Report Service for JSN Holdings
Fresh, professional PDF report styling while keeping all original information
"""

from __future__ import annotations

import io
import json
import datetime as _dt
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch

from app.models import Case, Note
from app.database import engine
from app.services.skiptrace_service import (
    load_property_for_case,
    load_skiptrace_for_case,
)

logger = logging.getLogger("pascowebapp")

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
UPLOAD_ROOT = BASE_DIR / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)


# ================================================================
# MODERN DESIGN CONSTANTS
# ================================================================

class DesignColors:
    """Modern color palette matching V3 UI"""
    PRIMARY = colors.HexColor('#667eea')
    PRIMARY_DARK = colors.HexColor('#764ba2')
    SUCCESS = colors.HexColor('#10b981')
    WARNING = colors.HexColor('#f59e0b')
    DANGER = colors.HexColor('#ef4444')
    TEXT_DARK = colors.HexColor('#1e293b')
    TEXT_SECONDARY = colors.HexColor('#475569')
    TEXT_MUTED = colors.HexColor('#94a3b8')
    BORDER = colors.HexColor('#e2e8f0')
    BG_LIGHT = colors.HexColor('#f8fafc')
    WHITE = colors.white


# ================================================================
# BUSINESS LOGIC CONSTANTS
# ================================================================

class OfferCalculationConstants:
    HIGH_VALUE_THRESHOLD = 350000
    WHOLESALE_OFFER_RATE = 0.65
    HIGH_VALUE_FLIP_RATE = 0.85
    STANDARD_FLIP_RATE = 0.80
    DEFAULT_CLOSING_COST_RATE = 0.045
    
    @classmethod
    def get_flip_rate(cls, arv: float) -> float:
        return cls.HIGH_VALUE_FLIP_RATE if arv > cls.HIGH_VALUE_THRESHOLD else cls.STANDARD_FLIP_RATE


class ReportFormattingConstants:
    MAX_NOTES_IN_REPORT = 15
    NOTE_MAX_LENGTH = 220
    NOTE_PREVIEW_LENGTH = 217
    NOTE_FONT_SIZE = 9
    ELLIPSIS = "..."


# ================================================================
# UTILITY FUNCTIONS
# ================================================================

def _fmt_money(raw: Any) -> str:
    if raw is None or raw == "":
        return "-"
    try:
        if isinstance(raw, (int, float)):
            return f"${float(raw):,.2f}"
        s = str(raw).strip()
        if not s:
            return "-"
        cleaned = s.replace("$", "").replace(",", "")
        return f"${float(cleaned):,.2f}"
    except Exception:
        return str(raw)


def _parse_float(raw: Any, default: float = 0.0) -> float:
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        s = str(raw).strip()
        if not s:
            return default
        return float(s.replace("$", "").replace(",", ""))
    except Exception:
        return default


def _safe_filename_piece(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def _report_filename(case_id: int, case_number: Optional[str], short_sale: bool) -> str:
    base = f"case_{case_id}"
    safe_case = _safe_filename_piece(case_number or "")
    if safe_case:
        base = f"{base}_{safe_case}"
    if short_sale:
        base = f"{base}_SHORT-SALE"
    return f"{base}_report.pdf"


def _is_short_sale(case: Case) -> bool:
    arv_num = _parse_float(getattr(case, "arv", None), 0.0)
    rehab_num = _parse_float(getattr(case, "rehab", None), 0.0)
    closing_raw = getattr(case, "closing_costs", None)
    try:
        user_closing = float(closing_raw) if closing_raw not in (None, "") else None
    except Exception:
        user_closing = None
    closing_num = user_closing if user_closing is not None else round(arv_num * OfferCalculationConstants.DEFAULT_CLOSING_COST_RATE, 2)
    flip_rate = OfferCalculationConstants.get_flip_rate(arv_num)
    flip_offer = round(max(0.0, (arv_num * flip_rate) - rehab_num - closing_num), 2)
    return _sum_liens_for_calc(case) > flip_offer


def _yn_icon(val: Any) -> str:
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (int, float)):
        return "Yes" if float(val) != 0 else "No"
    if isinstance(val, str):
        v = val.strip().lower()
        return "Yes" if v in {"y", "yes", "true", "1"} else "No"
    return "No"


def _fmt_phone(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return s


def _resolve_address(case: Case, primary_prop: Optional[dict]) -> str:
    override = (getattr(case, "address_override", "") or "").strip()
    if override:
        return override.upper()
    addr = (primary_prop or {}).get("address") or {}
    if addr:
        street = (addr.get("street") or addr.get("streetNoUnit") or addr.get("line1") or "").strip()
        city = (addr.get("city") or "").strip()
        state = (addr.get("state") or "").strip()
        zip_code = (addr.get("zip") or "").strip()
        parts = [p for p in [street, city, state] if p]
        full = ", ".join(parts)
        if zip_code:
            full = f"{full}, {zip_code}"
        if full:
            return full.upper()
    addr_attr = (getattr(case, "address", "") or "").strip()
    if addr_attr:
        return addr_attr.upper()
    parcel = (getattr(case, "parcel_id", "") or "").strip()
    return f"(Parcel {parcel})" if parcel else "(ADDRESS NOT SET)"


def _sum_liens_for_calc(case: Case) -> float:
    raw = getattr(case, "outstanding_liens", "[]") or "[]"
    try:
        liens = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        liens = []
    total = 0.0
    if isinstance(liens, list):
        for item in liens:
            if isinstance(item, dict):
                amt = item.get("amount") or item.get("balance") or item.get("lien_amount")
                total += _parse_float(amt, 0.0)
    return round(total, 2)


def _iter_liens_for_display(case: Case) -> List[Dict[str, Any]]:
    raw = getattr(case, "outstanding_liens", "[]") or "[]"
    try:
        liens = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        liens = []
    rows = []
    if isinstance(liens, list):
        for item in liens:
            if isinstance(item, dict):
                desc = str(item.get("description") or item.get("holder") or item.get("lien_type") or item.get("type") or item.get("creditor") or "").strip()
                amount = item.get("amount") or item.get("balance") or item.get("lien_amount")
                if desc or amount not in (None, ""):
                    rows.append({"description": desc, "amount": amount})
    return rows


def _append_pdf_if_exists(writer: PdfWriter, rel_path: Optional[str]):
    if not rel_path:
        return
    try:
        full = UPLOAD_ROOT / rel_path
        if full.exists():
            for page in PdfReader(str(full)).pages:
                writer.add_page(page)
    except Exception as exc:
        logger.warning("Failed to append PDF %s: %s", rel_path, exc)


# ================================================================
# TEXT WRAPPING
# ================================================================

def _wrap_text(c: canvas.Canvas, text: str, max_width: float, font_name: str, font_size: int) -> List[str]:
    if not text:
        return [""]
    c.setFont(font_name, font_size)
    words = text.split()
    lines, current = [], ""
    for w in words:
        test = (current + " " + w).strip()
        if not current:
            current = w
        elif c.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


# ================================================================
# MODERN LAYOUT CLASS
# ================================================================

class ModernLayout:
    def __init__(self, c: canvas.Canvas, page_size=letter):
        self.c = c
        self.width, self.height = page_size
        self.margin_x = 45
        self.margin_bottom = 50
        self.content_width = self.width - (self.margin_x * 2)
        self.y = self.height - 50
        self.page_num = 1

    def _ensure_space(self, needed: float = 20):
        if self.y - needed < self.margin_bottom:
            self._add_footer()
            self.c.showPage()
            self.page_num += 1
            self.y = self.height - 50

    def _add_footer(self):
        self.c.setFont("Helvetica", 8)
        self.c.setFillColor(DesignColors.TEXT_MUTED)
        self.c.drawString(self.margin_x, 25, "JSN Holdings Case Report")
        self.c.drawRightString(self.width - self.margin_x, 25, f"Page {self.page_num}")
        self.c.setStrokeColor(DesignColors.BORDER)
        self.c.line(self.margin_x, 37, self.width - self.margin_x, 37)

    def draw_header(self, case_number: str, address: str, is_short_sale: bool):
        header_height = 95
        # Gradient header
        self.c.setFillColor(DesignColors.PRIMARY)
        self.c.rect(0, self.height - header_height, self.width, header_height, fill=True, stroke=False)
        self.c.setFillColor(DesignColors.PRIMARY_DARK)
        self.c.rect(self.width * 0.6, self.height - header_height, self.width * 0.4, header_height, fill=True, stroke=False)
        
        # Company name
        self.c.setFillColor(DesignColors.WHITE)
        self.c.setFont("Helvetica-Bold", 16)
        self.c.drawString(self.margin_x, self.height - 32, "JSN HOLDINGS")
        self.c.setFont("Helvetica", 10)
        self.c.drawString(self.margin_x, self.height - 46, "Case Analysis Report")
        
        # Case info (right side)
        self.c.setFont("Helvetica-Bold", 11)
        self.c.drawRightString(self.width - self.margin_x, self.height - 32, case_number or "")
        self.c.setFont("Helvetica", 9)
        self.c.drawRightString(self.width - self.margin_x, self.height - 46, f"Generated: {_dt.datetime.now().strftime('%B %d, %Y')}")
        
        # Address bar
        self.c.setFillColor(colors.HexColor('#5a67d8'))
        self.c.rect(0, self.height - header_height, self.width, 32, fill=True, stroke=False)
        self.c.setFillColor(DesignColors.WHITE)
        self.c.setFont("Helvetica-Bold", 11)
        display_addr = address[:75] + "..." if len(address) > 75 else address
        self.c.drawString(self.margin_x, self.height - header_height + 10, display_addr)
        
        # Short sale badge
        if is_short_sale:
            self.c.setFillColor(DesignColors.DANGER)
            self.c.roundRect(self.width - self.margin_x - 85, self.height - header_height + 6, 80, 20, 3, fill=True)
            self.c.setFillColor(DesignColors.WHITE)
            self.c.setFont("Helvetica-Bold", 9)
            self.c.drawCentredString(self.width - self.margin_x - 45, self.height - header_height + 12, "SHORT SALE")
        
        self.y = self.height - header_height - 18

    def draw_metrics_row(self, metrics: List[tuple]):
        self._ensure_space(65)
        num = len(metrics)
        if num == 0:
            return
        card_w = (self.content_width - (10 * (num - 1))) / num
        
        for i, (label, value, hl) in enumerate(metrics):
            x = self.margin_x + i * (card_w + 10)
            self.c.setFillColor(DesignColors.BG_LIGHT)
            self.c.roundRect(x, self.y - 50, card_w, 50, 4, fill=True, stroke=False)
            self.c.setStrokeColor(DesignColors.BORDER)
            self.c.roundRect(x, self.y - 50, card_w, 50, 4, fill=False, stroke=True)
            
            color = {"success": DesignColors.SUCCESS, "warning": DesignColors.WARNING, "danger": DesignColors.DANGER}.get(hl, DesignColors.PRIMARY)
            self.c.setFillColor(color)
            self.c.setFont("Helvetica-Bold", 14)
            self.c.drawCentredString(x + card_w/2, self.y - 25, str(value))
            self.c.setFillColor(DesignColors.TEXT_SECONDARY)
            self.c.setFont("Helvetica", 8)
            self.c.drawCentredString(x + card_w/2, self.y - 40, label)
        self.y -= 62

    def section_title(self, title: str):
        self._ensure_space(32)
        self.y -= 8
        self.c.setFillColor(DesignColors.PRIMARY)
        self.c.rect(self.margin_x, self.y - 1, 4, 14, fill=True, stroke=False)
        self.c.setFillColor(DesignColors.TEXT_DARK)
        self.c.setFont("Helvetica-Bold", 11)
        self.c.drawString(self.margin_x + 10, self.y, title)
        self.y -= 20

    def key_value_line(self, key: str, value: str, key_width: int = 140):
        self._ensure_space(15)
        self.c.setFillColor(DesignColors.TEXT_MUTED)
        self.c.setFont("Helvetica", 9)
        self.c.drawString(self.margin_x, self.y, key)
        self.c.setFillColor(DesignColors.TEXT_DARK)
        self.c.setFont("Helvetica", 9)
        for i, ln in enumerate(_wrap_text(self.c, value or "-", self.content_width - key_width, "Helvetica", 9)):
            if i > 0:
                self.y -= 12
            self.c.drawString(self.margin_x + key_width, self.y, ln)
        self.y -= 14

    def bullet_item(self, text: str, size: int = 9):
        self._ensure_space(14)
        self.c.setFillColor(DesignColors.PRIMARY)
        self.c.circle(self.margin_x + 4, self.y + 2, 2, fill=True, stroke=False)
        self.c.setFillColor(DesignColors.TEXT_SECONDARY)
        self.c.setFont("Helvetica", size)
        for i, ln in enumerate(_wrap_text(self.c, text, self.content_width - 18, "Helvetica", size)):
            if i > 0:
                self.y -= 12
            self.c.drawString(self.margin_x + 14, self.y, ln)
        self.y -= 14

    def line(self, text: str = "", size: int = 9, bold: bool = False):
        self._ensure_space(14)
        font = "Helvetica-Bold" if bold else "Helvetica"
        self.c.setFillColor(DesignColors.TEXT_DARK if bold else DesignColors.TEXT_SECONDARY)
        self.c.setFont(font, size)
        for ln in _wrap_text(self.c, text, self.content_width, font, size):
            self.c.drawString(self.margin_x, self.y, ln)
            self.y -= 13

    def spacer(self, h: int = 8):
        self.y -= h


# ================================================================
# SKIP TRACE LOADING
# ================================================================

def _load_skiptrace_for_report(case_id: int) -> Optional[Any]:
    try:
        data = load_skiptrace_for_case(case_id)
        if data:
            return data
    except Exception as exc:
        logger.warning("load_skiptrace_for_case failed: %s", exc)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT skip_trace_json FROM cases WHERE id = :id"), {"id": case_id}).mappings().first()
        if row and row.get("skip_trace_json"):
            return json.loads(row["skip_trace_json"])
    except Exception:
        pass
    return None


def _extract_skiptrace_summary(data: Any) -> tuple:
    owner_name, phones, emails = "", [], []
    
    def add_phone(p):
        if isinstance(p, dict) and p.get("number"):
            phones.append(p)
        elif p:
            phones.append({"number": p})
    
    def add_email(e):
        if isinstance(e, dict) and e.get("email"):
            emails.append(e)
        elif e:
            emails.append({"email": e})
    
    def process_contact(c):
        nonlocal owner_name
        if not isinstance(c, dict):
            return
        name = (c.get("full_name") or c.get("name") or c.get("ownerName") or "").strip()
        if name and not owner_name:
            owner_name = name
        for p in c.get("phones") or []:
            add_phone(p)
        for e in c.get("emails") or []:
            add_email(e)
    
    if isinstance(data, list):
        for c in data:
            process_contact(c)
    elif isinstance(data, dict):
        for key in ["contacts", "owners"]:
            for c in data.get(key) or []:
                process_contact(c)
        if data.get("results"):
            for res in data["results"]:
                for key in ("persons", "people", "owners"):
                    for c in res.get(key) or []:
                        process_contact(c)
        if data.get("primary_owner"):
            process_contact(data["primary_owner"])
        for p in data.get("phones") or []:
            add_phone(p)
        for e in data.get("emails") or []:
            add_email(e)
        if not owner_name:
            owner_name = (data.get("owner_name") or data.get("ownerName") or data.get("full_name") or data.get("name") or "").strip()
    
    return owner_name, phones, emails


# ================================================================
# MAIN REPORT BUILDER
# ================================================================

def build_case_report_bytes(case_id: int, db: Session, include_attachments: bool = True) -> io.BytesIO:
    getter = getattr(db, "get", None)
    case = db.get(Case, case_id) if callable(getter) else db.query(Case).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    primary_prop = load_property_for_case(case_id)
    skip_data = _load_skiptrace_for_report(case_id)
    notes = db.query(Note).filter(Note.case_id == case_id).order_by(Note.id.desc()).all()
    defendants = [d.name for d in (case.defendants or []) if d.name]
    lien_rows = _iter_liens_for_display(case)
    owner_name, phones, emails = _extract_skiptrace_summary(skip_data)

    case_number = getattr(case, "case_number", "") or ""
    filing_dt = getattr(case, "filing_datetime", "") or ""
    style = getattr(case, "style", "") or ""
    parcel = getattr(case, "parcel_id", "") or ""
    address = _resolve_address(case, primary_prop)

    arv = _parse_float(getattr(case, "arv", None))
    rehab = _parse_float(getattr(case, "rehab", None))
    closing_raw = getattr(case, "closing_costs", None)
    try:
        closing = float(closing_raw) if closing_raw not in (None, "") else None
    except:
        closing = None
    closing = closing if closing is not None else round(arv * OfferCalculationConstants.DEFAULT_CLOSING_COST_RATE, 2)
    
    total_liens = _sum_liens_for_calc(case)
    flip_rate = OfferCalculationConstants.get_flip_rate(arv)
    flip_offer = round(max(0, (arv * flip_rate) - rehab - closing), 2)
    wholesale = round(max(0, (arv * OfferCalculationConstants.WHOLESALE_OFFER_RATE) - rehab - closing), 2)
    short_sale = total_liens > flip_offer

    prop = primary_prop or {}
    
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    layout = ModernLayout(c)
    layout.draw_header(case_number, address, short_sale)

    # Metrics
    metrics = []
    if arv > 0:
        metrics.append(("ARV", _fmt_money(arv), "primary"))
    if flip_offer > 0:
        metrics.append(("Flip Offer", _fmt_money(flip_offer), "success"))
    if wholesale > 0:
        metrics.append(("Wholesale", _fmt_money(wholesale), "primary"))
    if total_liens > 0:
        metrics.append(("Total Liens", _fmt_money(total_liens), "danger" if short_sale else "warning"))
    if metrics:
        layout.draw_metrics_row(metrics[:4])

    # Deal Summary
    layout.section_title("Deal Summary")
    if arv > 0:
        layout.key_value_line("After Repair Value (ARV)", _fmt_money(arv))
    if rehab > 0:
        layout.key_value_line("Rehab Estimate", _fmt_money(rehab))
    if closing > 0:
        layout.key_value_line("Closing Costs", _fmt_money(closing))
    if flip_offer > 0:
        layout.key_value_line(f"Flip Offer ({int(flip_rate*100)}%)", _fmt_money(flip_offer))
    if wholesale > 0:
        layout.key_value_line("Wholesale Offer (65%)", _fmt_money(wholesale))
    if total_liens > 0:
        layout.key_value_line("Total Outstanding Liens", _fmt_money(total_liens))
    if short_sale:
        layout.spacer(4)
        layout.line("SHORT SALE - Liens exceed flip offer", bold=True)

    # Case Details
    layout.section_title("Case Details")
    layout.key_value_line("Case Number", case_number)
    if filing_dt:
        layout.key_value_line("Filing Date", filing_dt)
    if parcel:
        layout.key_value_line("Parcel ID", parcel)
    if style:
        layout.key_value_line("Style", style)

    # Property
    prop_type = prop.get("propertyType") or prop.get("property_type")
    beds = prop.get("bedrooms") or prop.get("beds")
    baths = prop.get("bathrooms") or prop.get("baths")
    sqft = prop.get("squareFeet") or prop.get("sqft") or prop.get("livingArea")
    lot = prop.get("lotSquareFeet") or prop.get("lotSize")
    year = prop.get("yearBuilt") or prop.get("year_built")
    
    if any([prop_type, beds, baths, sqft, year]):
        layout.section_title("Property Snapshot")
        if prop_type:
            layout.key_value_line("Property Type", str(prop_type))
        if beds:
            layout.key_value_line("Bedrooms", str(beds))
        if baths:
            layout.key_value_line("Bathrooms", str(baths))
        if sqft:
            layout.key_value_line("Living Area", f"{sqft:,} sq ft" if isinstance(sqft, (int, float)) else str(sqft))
        if lot:
            layout.key_value_line("Lot Size", f"{lot:,} sq ft" if isinstance(lot, (int, float)) else str(lot))
        if year:
            layout.key_value_line("Year Built", str(year))

    # Valuation
    assessed = prop.get("assessedValue") or prop.get("assessed_value")
    market = prop.get("marketValue") or prop.get("market_value")
    taxes = prop.get("annualTaxes") or prop.get("taxes")
    tax_year = prop.get("taxYear") or prop.get("tax_year")
    
    if any([assessed, market, taxes]):
        layout.section_title("Valuation & Taxes")
        if _parse_float(assessed) > 0:
            layout.key_value_line("Assessed Value", _fmt_money(assessed))
        if _parse_float(market) > 0:
            layout.key_value_line("Market Value", _fmt_money(market))
        if _parse_float(taxes) > 0:
            t = _fmt_money(taxes)
            if tax_year:
                t += f" ({tax_year})"
            layout.key_value_line("Annual Taxes", t)

    # Ownership
    o_name = prop.get("ownerName") or prop.get("owner_name")
    o_type = prop.get("ownerType") or prop.get("owner_type")
    o_occ = prop.get("ownerOccupied") or prop.get("owner_occupied")
    o_mail = prop.get("mailingAddress") or prop.get("mailing_address") or {}
    sale_date = prop.get("lastSaleDate") or prop.get("last_sale_date")
    sale_price = prop.get("lastSalePrice") or prop.get("last_sale_price")
    sale_type = prop.get("lastSaleType") or prop.get("sale_type")
    
    if any([o_name, sale_date, sale_price]):
        layout.section_title("Ownership & Transfer")
        if o_name:
            layout.key_value_line("Owner", str(o_name))
        if o_type:
            layout.key_value_line("Owner Type", str(o_type))
        if o_occ is not None:
            layout.key_value_line("Owner Occupied", _yn_icon(o_occ))
        if isinstance(o_mail, dict):
            parts = [str(p).strip() for p in [o_mail.get("street"), o_mail.get("city"), o_mail.get("state")] if p]
            if o_mail.get("zip"):
                parts.append(str(o_mail["zip"]))
            if parts:
                layout.key_value_line("Mailing Address", ", ".join(parts))
        if sale_date:
            layout.key_value_line("Last Sale Date", str(sale_date))
        if _parse_float(sale_price) > 0:
            layout.key_value_line("Last Sale Price", _fmt_money(sale_price))
        if sale_type:
            layout.key_value_line("Sale Type", str(sale_type))

    # Demographics
    demo = prop.get("demographics") or {}
    if demo:
        items = []
        for k, l in [("age", "Age"), ("gender", "Gender"), ("maritalStatus", "Marital Status"), ("income", "Income"), ("netWorth", "Net Worth"), ("individualOccupation", "Occupation")]:
            v = demo.get(k)
            if v:
                if k in ("income", "netWorth") and _parse_float(v) > 0:
                    items.append((l, _fmt_money(v)))
                else:
                    items.append((l, str(v)))
        if items:
            layout.section_title("Demographics")
            for l, v in items:
                layout.key_value_line(l, v)

    # Liens
    if lien_rows:
        layout.section_title("Outstanding Liens")
        for r in lien_rows:
            layout.bullet_item(f"{r['description'] or 'Unknown'} — {_fmt_money(r['amount'])}")

    # Defendants
    if defendants:
        layout.section_title("Defendants")
        for d in defendants:
            layout.bullet_item(d)

    # Skip Trace
    if owner_name or phones or emails:
        layout.section_title("Skip Trace Results")
        if owner_name:
            layout.key_value_line("Primary Owner", owner_name)
        if phones:
            layout.spacer(4)
            layout.line("Phone Numbers:", bold=True)
            for ph in phones[:8]:
                num = _fmt_phone(ph.get("number"))
                if num:
                    parts = [num]
                    if ph.get("type"):
                        parts.append(f"({ph['type']})")
                    if ph.get("score") is not None:
                        parts.append(f"Score: {ph['score']}")
                    if ph.get("dnc") is not None:
                        parts.append(f"DNC: {_yn_icon(ph['dnc'])}")
                    layout.bullet_item(" | ".join(parts))
        if emails:
            layout.spacer(4)
            layout.line("Email Addresses:", bold=True)
            for em in emails[:5]:
                addr = (em.get("email") or "").strip()
                if addr:
                    layout.bullet_item(addr)

    # Notes
    if notes:
        layout.section_title("Notes")
        for n in notes[:ReportFormattingConstants.MAX_NOTES_IN_REPORT]:
            created = getattr(n, "created_at", "") or ""
            content = (getattr(n, "content", "") or "").replace("\r", " ").replace("\n", " ")
            if len(content) > ReportFormattingConstants.NOTE_MAX_LENGTH:
                content = content[:ReportFormattingConstants.NOTE_PREVIEW_LENGTH] + "..."
            stamp = f"[{created}] " if created else ""
            layout.bullet_item(f"{stamp}{content}", size=8)

    # Attached Docs
    attached = []
    for attr, label in [("verified_complaint_path", "Verified Complaint"), ("mortgage_path", "Mortgage"), ("current_deed_path", "Current Deed"), ("previous_deed_path", "Previous Deed"), ("value_calc_path", "Value Calculation")]:
        if getattr(case, attr, ""):
            attached.append(label)
    if attached:
        layout.section_title("Attached Documents")
        for a in attached:
            layout.bullet_item(a)

    layout._add_footer()
    c.showPage()
    c.save()
    buf.seek(0)

    # Combine PDFs
    writer = PdfWriter()
    try:
        for p in PdfReader(buf).pages:
            writer.add_page(p)
    except Exception:
        buf.seek(0)
        return buf

    if include_attachments:
        for attr in ["verified_complaint_path", "mortgage_path", "current_deed_path", "previous_deed_path", "value_calc_path"]:
            _append_pdf_if_exists(writer, getattr(case, attr, ""))

    out = io.BytesIO()
    writer.write(out) if writer.pages else out.write(buf.getvalue())
    out.seek(0)
    return out


def generate_case_report(case_id: int, db: Session) -> StreamingResponse:
    getter = getattr(db, "get", None)
    case = db.get(Case, case_id) if callable(getter) else db.query(Case).get(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    filename = _report_filename(case_id, getattr(case, "case_number", None), _is_short_sale(case))
    return StreamingResponse(build_case_report_bytes(case_id, db), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})
