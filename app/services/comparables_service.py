# app/services/comparables_service.py
"""
Comparables Service - Property comparable sales analysis
✅ WITH COMPREHENSIVE INPUT VALIDATION (Fixed Version)
"""

from __future__ import annotations

import html
import logging
import math
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

import requests
from sqlalchemy import text
from app.config import settings
from app.database import engine

logger = logging.getLogger("pascowebapp.comparables")


# ========================================
# VALIDATION FUNCTIONS (NEW)
# ========================================

def validate_coordinate(value: float, coord_type: str) -> float:
    """
    Validate latitude or longitude
    
    Args:
        value: Coordinate value
        coord_type: "latitude" or "longitude"
    
    Returns:
        Validated coordinate
    
    Raises:
        ValueError: If coordinate is invalid
    """
    if value is None or math.isnan(value) or math.isinf(value):
        raise ValueError(f"Invalid {coord_type}: {value}")
    
    if coord_type == "latitude":
        if not -90 <= value <= 90:
            raise ValueError(f"Latitude must be between -90 and 90, got: {value}")
    elif coord_type == "longitude":
        if not -180 <= value <= 180:
            raise ValueError(f"Longitude must be between -180 and 180, got: {value}")
    else:
        raise ValueError(f"Unknown coordinate type: {coord_type}")
    
    return float(value)


def validate_positive_number(value: Any, name: str, max_value: Optional[float] = None) -> float:
    """
    Validate that a value is a positive number
    
    Args:
        value: Value to validate
        name: Parameter name (for error messages)
        max_value: Optional maximum allowed value
    
    Returns:
        Validated float value
    
    Raises:
        ValueError: If value is invalid
    """
    try:
        num = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a number, got: {value}") from e
    
    if math.isnan(num) or math.isinf(num):
        raise ValueError(f"{name} cannot be NaN or Inf")
    
    if num <= 0:
        raise ValueError(f"{name} must be positive, got: {num}")
    
    if max_value and num > max_value:
        raise ValueError(f"{name} must not exceed {max_value}, got: {num}")
    
    return num


def validate_non_negative_number(value: Any, name: str) -> float:
    """Validate non-negative number (can be zero)"""
    try:
        num = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a number, got: {value}") from e
    
    if math.isnan(num) or math.isinf(num):
        raise ValueError(f"{name} cannot be NaN or Inf")
    
    if num < 0:
        raise ValueError(f"{name} cannot be negative, got: {num}")
    
    return num


def validate_string_length(value: str, name: str, max_length: int = 255, min_length: int = 1) -> str:
    """
    Validate string length
    
    Args:
        value: String to validate
        name: Parameter name
        max_length: Maximum allowed length
        min_length: Minimum allowed length
    
    Returns:
        Validated string
    
    Raises:
        ValueError: If string is invalid
    """
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string, got: {type(value)}")
    
    value = value.strip()
    
    if len(value) < min_length:
        raise ValueError(f"{name} must be at least {min_length} character(s), got: {len(value)}")
    
    if len(value) > max_length:
        raise ValueError(f"{name} exceeds max length of {max_length}, got: {len(value)}")
    
    return value


def validate_state_code(state: str) -> str:
    """
    Validate US state code (2-letter)
    
    Returns:
        Uppercase 2-letter state code
    
    Raises:
        ValueError: If state code is invalid
    """
    if not isinstance(state, str):
        raise ValueError(f"State must be a string, got: {type(state)}")
    
    state = state.strip().upper()
    
    if len(state) != 2:
        raise ValueError(f"State must be 2-letter code, got: {state}")
    
    if not state.isalpha():
        raise ValueError(f"State must contain only letters, got: {state}")
    
    return state


def validate_postal_code(postal_code: Optional[str]) -> Optional[str]:
    """
    Validate US postal code (ZIP)
    
    Formats: 12345 or 12345-6789
    
    Returns:
        Validated postal code or None
    
    Raises:
        ValueError: If postal code format is invalid
    """
    if not postal_code:
        return None
    
    if not isinstance(postal_code, str):
        raise ValueError(f"Postal code must be a string, got: {type(postal_code)}")
    
    postal_code = postal_code.strip()
    
    # Valid formats: 12345 or 12345-6789
    pattern = r'^\d{5}(-\d{4})?$'
    if not re.match(pattern, postal_code):
        raise ValueError(f"Invalid postal code format: {postal_code}")
    
    return postal_code


def safe_float_convert(value: Any, default: float = 0.0) -> float:
    """
    Safely convert value to float
    
    Args:
        value: Value to convert
        default: Default value if conversion fails
    
    Returns:
        Float value or default
    """
    if value is None:
        return default
    
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def safe_int_convert(value: Any, default: int = 0) -> int:
    """Safely convert value to int"""
    if value is None:
        return default
    
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sanitize_html(text: str) -> str:
    """
    Sanitize text for HTML output (prevent XSS)
    
    Args:
        text: Text to sanitize
    
    Returns:
        HTML-escaped text
    """
    if not text:
        return ""
    
    return html.escape(str(text), quote=True)


# ========================================
# MAIN FUNCTIONS (WITH VALIDATION)
# ========================================

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two points using Haversine formula
    
    Args:
        lat1, lon1: First point coordinates
        lat2, lon2: Second point coordinates
    
    Returns:
        Distance in miles
    
    Raises:
        ValueError: If coordinates are invalid
    """
    # Validate all coordinates
    try:
        lat1 = validate_coordinate(lat1, "latitude")
        lat2 = validate_coordinate(lat2, "latitude")
        lon1 = validate_coordinate(lon1, "longitude")
        lon2 = validate_coordinate(lon2, "longitude")
    except ValueError as e:
        logger.error(f"Invalid coordinates for distance calculation: {e}")
        raise
    
    R = 3959.87433  # Earth radius in miles
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (
        math.sin(delta_lat / 2) ** 2 +
        math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    distance = R * c
    
    return round(distance, 2)


def fetch_comparables_from_batchdata(
    street: str,
    city: str,
    state: str,
    postal_code: Optional[str],
    radius_miles: float = 1.0,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    Fetch comparable sales from BatchData API
    
    Args:
        street: Street address
        city: City name
        state: 2-letter state code
        postal_code: ZIP code (optional)
        radius_miles: Search radius in miles (0.1 to 50)
        max_results: Maximum results to return (1 to 100)
    
    Returns:
        List of comparable properties
    
    Raises:
        ValueError: If inputs are invalid
        requests.HTTPError: If API request fails
    """
    # Validate API configuration
    if not settings.batchdata_base_url:
        raise ValueError("BatchData base URL not configured")
    
    if not settings.batchdata_api_key:
        raise ValueError("BatchData API key not configured")
    
    # Validate inputs
    try:
        street = validate_string_length(street, "street", max_length=200, min_length=3)
        city = validate_string_length(city, "city", max_length=100, min_length=2)
        state = validate_state_code(state)
        postal_code = validate_postal_code(postal_code)
        
        # Validate numeric ranges
        radius_miles = validate_positive_number(radius_miles, "radius_miles", max_value=50.0)
        
        # Ensure max_results is reasonable
        max_results = safe_int_convert(max_results, default=10)
        if max_results < 1:
            max_results = 1
        if max_results > 100:
            max_results = 100
            logger.warning(f"max_results capped at 100")
        
    except ValueError as e:
        logger.error(f"Invalid input for comparables search: {e}")
        raise
    
    # Build API request
    url = f"{settings.batchdata_base_url}/property/comparables"
    
    payload = {
        "address": {
            "street": street,
            "city": city,
            "state": state,
        },
        "radius_miles": radius_miles,
        "max_results": max_results,
        "sold_within_months": 12,  # Last 12 months
    }
    
    if postal_code:
        payload["address"]["postalCode"] = postal_code
    
    headers = {
        "Authorization": f"Bearer {settings.batchdata_api_key}",
        "Content-Type": "application/json",
    }
    
    # Get timeout from settings with fallback
    timeout = getattr(settings, 'request_timeout_seconds', 30)
    if not timeout or timeout <= 0:
        timeout = 30
    
    try:
        logger.info(f"Fetching comparables for {street}, {city}, {state}")
        
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        
        # Validate response structure
        data = resp.json()
        if not isinstance(data, dict):
            logger.error(f"Invalid API response type: {type(data)}")
            return []
        
        comparables = data.get("comparables", [])
        if not isinstance(comparables, list):
            logger.error(f"Invalid comparables type: {type(comparables)}")
            return []
        
        logger.info(f"Fetched {len(comparables)} comparables")
        return comparables
    
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching comparables (>{timeout}s)")
        return []
    
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching comparables: {e}")
        return []
    
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code == 404:
            logger.warning(f"No comparables found for {street}, {city}")
            return []
        
        logger.error(f"BatchData comparables API error ({exc.response.status_code}): {exc}")
        return []
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error fetching comparables: {e}")
        return []
    
    except Exception as exc:
        logger.exception(f"Unexpected error fetching comparables: {exc}")
        return []


def save_comparables_to_db(case_id: int, comparables: List[Dict[str, Any]]) -> int:
    """
    Save comparable sales to database
    
    Args:
        case_id: Case ID
        comparables: List of comparable property dicts
    
    Returns:
        Count of saved records
    
    Raises:
        ValueError: If case_id is invalid
    """
    # Validate case_id
    if not isinstance(case_id, int) or case_id <= 0:
        raise ValueError(f"Invalid case_id: {case_id}")
    
    if not comparables:
        logger.info(f"No comparables to save for case {case_id}")
        return 0
    
    if not isinstance(comparables, list):
        raise ValueError(f"comparables must be a list, got: {type(comparables)}")
    
    try:
        with engine.begin() as conn:
            # Clear existing comparables for this case
            conn.execute(
                text("DELETE FROM property_comparables WHERE case_id = :case_id"),
                {"case_id": case_id}
            )
            
            saved_count = 0
            
            # Insert new comparables
            for idx, comp in enumerate(comparables):
                if not isinstance(comp, dict):
                    logger.warning(f"Skipping invalid comparable #{idx} (not a dict)")
                    continue
                
                # Extract and validate data
                addr = comp.get("address", {}) if isinstance(comp.get("address"), dict) else {}
                sale = comp.get("sale", {}) if isinstance(comp.get("sale"), dict) else {}
                building = comp.get("building", {}) if isinstance(comp.get("building"), dict) else {}
                
                # Get address fields with validation
                address_str = addr.get("street") or addr.get("full")
                if address_str:
                    address_str = str(address_str)[:200]  # Limit length
                
                city_str = addr.get("city")
                if city_str:
                    city_str = str(city_str)[:100]
                
                state_str = addr.get("state")
                if state_str:
                    state_str = str(state_str)[:2]
                
                zip_str = addr.get("zip")
                if zip_str:
                    zip_str = str(zip_str)[:10]
                
                # Get numeric fields with validation
                sale_price = safe_float_convert(sale.get("price") or sale.get("lastSalePrice"))
                if sale_price < 0:
                    sale_price = None  # Don't save negative prices
                
                sqft = safe_float_convert(building.get("livingAreaSqft") or building.get("sqft"))
                if sqft <= 0:
                    sqft = None  # Don't save invalid sqft
                
                bedrooms = safe_int_convert(building.get("bedrooms"))
                bathrooms = safe_float_convert(building.get("bathrooms") or building.get("totalBathrooms"))
                year_built = safe_int_convert(building.get("yearBuilt"))
                
                # Validate year
                current_year = datetime.now().year
                if year_built and not (1800 <= year_built <= current_year + 2):
                    year_built = None
                
                distance_miles = safe_float_convert(comp.get("distance_miles"))
                price_per_sqft = safe_float_convert(comp.get("price_per_sqft"))
                
                # Get sale date
                sale_date = sale.get("date") or sale.get("lastSaleDate")
                if sale_date:
                    sale_date = str(sale_date)[:50]
                
                source = str(comp.get("source", "batchdata"))[:50]
                
                conn.execute(
                    text("""
                        INSERT INTO property_comparables (
                            case_id, comp_address, comp_city, comp_state, comp_zip,
                            sale_date, sale_price, bedrooms, bathrooms, sqft,
                            year_built, distance_miles, price_per_sqft, source, fetched_at
                        ) VALUES (
                            :case_id, :address, :city, :state, :zip,
                            :sale_date, :sale_price, :beds, :baths, :sqft,
                            :year, :distance, :price_sqft, :source, datetime('now')
                        )
                    """),
                    {
                        "case_id": case_id,
                        "address": address_str,
                        "city": city_str,
                        "state": state_str,
                        "zip": zip_str,
                        "sale_date": sale_date,
                        "sale_price": sale_price,
                        "beds": bedrooms if bedrooms > 0 else None,
                        "baths": bathrooms if bathrooms > 0 else None,
                        "sqft": sqft,
                        "year": year_built,
                        "distance": distance_miles,
                        "price_sqft": price_per_sqft,
                        "source": source,
                    }
                )
                saved_count += 1
        
        logger.info(f"Saved {saved_count}/{len(comparables)} comparables for case {case_id}")
        return saved_count
    
    except Exception as exc:
        logger.error(f"Failed to save comparables for case {case_id}: {exc}", exc_info=True)
        raise


def load_comparables_from_db(case_id: int) -> List[Dict[str, Any]]:
    """
    Load saved comparables from database
    
    Args:
        case_id: Case ID
    
    Returns:
        List of comparable property dicts
    """
    # Validate case_id
    if not isinstance(case_id, int) or case_id <= 0:
        logger.error(f"Invalid case_id: {case_id}")
        return []
    
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT 
                        comp_address, comp_city, comp_state, comp_zip,
                        sale_date, sale_price, bedrooms, bathrooms, sqft,
                        year_built, distance_miles, price_per_sqft, source
                    FROM property_comparables
                    WHERE case_id = :case_id
                    ORDER BY distance_miles ASC, sale_date DESC
                """),
                {"case_id": case_id}
            ).mappings().fetchall()
        
        return [dict(row) for row in rows]
    
    except Exception as exc:
        logger.error(f"Failed to load comparables for case {case_id}: {exc}")
        return []


def calculate_suggested_arv(
    comparables: List[Dict[str, Any]],
    subject_sqft: Optional[int] = None,
    subject_beds: Optional[int] = None,
    subject_baths: Optional[float] = None,
) -> Tuple[float, float, float]:
    """
    Calculate suggested ARV based on comparables
    
    Args:
        comparables: List of comparable property dicts
        subject_sqft: Subject property square footage
        subject_beds: Subject property bedrooms
        subject_baths: Subject property bathrooms
    
    Returns:
        Tuple of (suggested_arv, low_estimate, high_estimate)
    """
    if not comparables or not isinstance(comparables, list):
        return 0.0, 0.0, 0.0
    
    # Filter to valid sales with prices
    valid_comps = []
    for c in comparables:
        if not isinstance(c, dict):
            continue
        
        sale_price = safe_float_convert(c.get("sale_price"))
        if sale_price > 0:
            valid_comps.append(c)
    
    if not valid_comps:
        logger.warning("No valid comparables with positive sale prices")
        return 0.0, 0.0, 0.0
    
    # Calculate price per sqft for each comp
    price_per_sqft_values = []
    for comp in valid_comps:
        sqft = safe_float_convert(comp.get("sqft"))
        price = safe_float_convert(comp.get("sale_price"))
        
        # Only include if both values are valid and positive
        if sqft > 0 and price > 0:
            ppsf = price / sqft
            price_per_sqft_values.append(ppsf)
    
    if not price_per_sqft_values:
        # Fall back to raw average of sale prices
        logger.info("No valid sqft data, using raw price average")
        prices = [safe_float_convert(c.get("sale_price")) for c in valid_comps if safe_float_convert(c.get("sale_price")) > 0]
        
        if not prices:
            return 0.0, 0.0, 0.0
        
        avg_price = sum(prices) / len(prices)
        return (
            round(avg_price, 2),
            round(min(prices), 2),
            round(max(prices), 2)
        )
    
    # Calculate average price per sqft (with safety check)
    if len(price_per_sqft_values) == 0:
        return 0.0, 0.0, 0.0
    
    avg_ppsf = sum(price_per_sqft_values) / len(price_per_sqft_values)
    
    # Use subject property sqft if available and valid
    subject_sqft_val = safe_float_convert(subject_sqft)
    
    if subject_sqft_val > 0:
        suggested_arv = avg_ppsf * subject_sqft_val
    else:
        # Estimate based on average comp sqft
        valid_sqfts = [safe_float_convert(c.get("sqft")) for c in valid_comps if safe_float_convert(c.get("sqft")) > 0]
        
        if not valid_sqfts:
            # Use median sale price if no sqft data
            prices = sorted([safe_float_convert(c.get("sale_price")) for c in valid_comps if safe_float_convert(c.get("sale_price")) > 0])
            if not prices:
                return 0.0, 0.0, 0.0
            
            suggested_arv = prices[len(prices) // 2]  # Median
        else:
            avg_comp_sqft = sum(valid_sqfts) / len(valid_sqfts)
            suggested_arv = avg_ppsf * avg_comp_sqft
    
    # Calculate range (±15%)
    low_estimate = suggested_arv * 0.85
    high_estimate = suggested_arv * 1.15
    
    return (
        round(suggested_arv, 2),
        round(low_estimate, 2),
        round(high_estimate, 2),
    )


def fetch_and_save_comparables(case_id: int, case_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main function to fetch comparables and save to database
    
    Args:
        case_id: Case ID
        case_data: Dict with keys: street, city, state, postal_code, lat, lon, sqft, beds, baths
    
    Returns:
        Dict with comparables data and suggested ARV
    
    Raises:
        ValueError: If inputs are invalid
    """
    # Validate inputs
    if not isinstance(case_id, int) or case_id <= 0:
        raise ValueError(f"Invalid case_id: {case_id}")
    
    if not isinstance(case_data, dict):
        raise ValueError(f"case_data must be a dict, got: {type(case_data)}")
    
    street = case_data.get("street")
    city = case_data.get("city")
    state = case_data.get("state")
    postal_code = case_data.get("postal_code")
    
    if not all([street, city, state]):
        raise ValueError("Incomplete address for comparables search (need street, city, state)")
    
    # Fetch comparables from API (with validation built-in)
    try:
        comparables = fetch_comparables_from_batchdata(
            street=street,
            city=city,
            state=state,
            postal_code=postal_code,
            radius_miles=1.0,
            max_results=10,
        )
    except ValueError as e:
        logger.error(f"Validation error fetching comparables: {e}")
        raise
    
    # Calculate distances if subject coordinates available
    subject_lat = safe_float_convert(case_data.get("lat"))
    subject_lon = safe_float_convert(case_data.get("lon"))
    
    if subject_lat != 0 and subject_lon != 0:
        for comp in comparables:
            comp_lat = safe_float_convert(comp.get("latitude"))
            comp_lon = safe_float_convert(comp.get("longitude"))
            
            if comp_lat != 0 and comp_lon != 0:
                try:
                    distance = calculate_distance(subject_lat, subject_lon, comp_lat, comp_lon)
                    comp["distance_miles"] = distance
                except ValueError as e:
                    logger.warning(f"Could not calculate distance: {e}")
                    comp["distance_miles"] = None
    
    # Save to database
    try:
        saved_count = save_comparables_to_db(case_id, comparables)
    except Exception as e:
        logger.error(f"Error saving comparables: {e}")
        saved_count = 0
    
    # Calculate suggested ARV
    suggested_arv, low_est, high_est = calculate_suggested_arv(
        comparables,
        subject_sqft=case_data.get("sqft"),
        subject_beds=case_data.get("beds"),
        subject_baths=case_data.get("baths"),
    )
    
    # Calculate price per sqft safely
    subject_sqft_val = safe_float_convert(case_data.get("sqft"))
    avg_price_per_sqft = None
    if subject_sqft_val > 0 and suggested_arv > 0:
        avg_price_per_sqft = round(suggested_arv / subject_sqft_val, 2)
    
    return {
        "comparables": comparables,
        "count": len(comparables),
        "saved_count": saved_count,
        "suggested_arv": suggested_arv,
        "low_estimate": low_est,
        "high_estimate": high_est,
        "avg_price_per_sqft": avg_price_per_sqft,
    }


def generate_comparables_map_html(
    case_id: int,
    subject_address: str,
    subject_lat: float,
    subject_lon: float,
) -> str:
    """
    Generate an HTML map showing subject property and comparables
    Uses Google Maps API
    
    Args:
        case_id: Case ID
        subject_address: Subject property address
        subject_lat: Subject latitude
        subject_lon: Subject longitude
    
    Returns:
        HTML string with embedded map
    """
    # Validate Google Maps API key
    if not settings.google_maps_api_key:
        return "<p>Google Maps API key not configured</p>"
    
    # Validate inputs
    try:
        case_id = int(case_id)
        if case_id <= 0:
            raise ValueError(f"Invalid case_id: {case_id}")
        
        subject_lat = validate_coordinate(subject_lat, "latitude")
        subject_lon = validate_coordinate(subject_lon, "longitude")
        
        # Sanitize address for HTML
        subject_address = sanitize_html(subject_address)
        
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid map parameters: {e}")
        return f"<p>Error: Invalid map parameters</p>"
    
    # Load comparables
    comparables = load_comparables_from_db(case_id)
    
    # URL-encode the address for the map
    import urllib.parse
    encoded_address = urllib.parse.quote(subject_address)
    
    # Generate basic HTML with Google Maps embed
    map_html = f"""
    <div id="comps-map" style="width: 100%; height: 400px; border-radius: 8px; border: 1px solid #ddd;">
        <iframe
            width="100%"
            height="400"
            frameborder="0"
            style="border:0; border-radius: 8px;"
            referrerpolicy="no-referrer-when-downgrade"
            src="https://www.google.com/maps/embed/v1/place?key={settings.google_maps_api_key}&q={encoded_address}"
            allowfullscreen>
        </iframe>
    </div>
    <p style="margin-top: 10px; font-size: 0.9em; color: #666;">
        Showing subject property at {subject_address}. Found {len(comparables)} comparable(s).
    </p>
    """
    
    return map_html
