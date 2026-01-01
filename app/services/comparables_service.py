# app/services/comparables_service.py
from __future__ import annotations

import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

import requests
from sqlalchemy import text
from app.config import settings
from app.database import engine

logger = logging.getLogger("pascowebapp.comparables")


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two points using Haversine formula
    Returns distance in miles
    """
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
    
    return R * c


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
    """
    if not settings.batchdata_api_key:
        raise ValueError("BatchData API key not configured")
    
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
    
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.request_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json().get("comparables", [])
    
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code == 404:
            logger.warning(f"No comparables found for {street}, {city}")
            return []
        logger.error(f"BatchData comparables API error: {exc}")
        raise
    
    except Exception as exc:
        logger.error(f"Failed to fetch comparables: {exc}")
        raise


def save_comparables_to_db(case_id: int, comparables: List[Dict[str, Any]]) -> int:
    """
    Save comparable sales to database
    Returns count of saved records
    """
    if not comparables:
        return 0
    
    try:
        with engine.begin() as conn:
            # Clear existing comparables for this case
            conn.execute(
                text("DELETE FROM property_comparables WHERE case_id = :case_id"),
                {"case_id": case_id}
            )
            
            # Insert new comparables
            for comp in comparables:
                addr = comp.get("address", {})
                sale = comp.get("sale", {})
                building = comp.get("building", {})
                
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
                        "address": addr.get("street") or addr.get("full"),
                        "city": addr.get("city"),
                        "state": addr.get("state"),
                        "zip": addr.get("zip"),
                        "sale_date": sale.get("date") or sale.get("lastSaleDate"),
                        "sale_price": sale.get("price") or sale.get("lastSalePrice"),
                        "beds": building.get("bedrooms"),
                        "baths": building.get("bathrooms") or building.get("totalBathrooms"),
                        "sqft": building.get("livingAreaSqft") or building.get("sqft"),
                        "year": building.get("yearBuilt"),
                        "distance": comp.get("distance_miles"),
                        "price_sqft": comp.get("price_per_sqft"),
                        "source": comp.get("source", "batchdata"),
                    }
                )
        
        logger.info(f"Saved {len(comparables)} comparables for case {case_id}")
        return len(comparables)
    
    except Exception as exc:
        logger.error(f"Failed to save comparables for case {case_id}: {exc}")
        raise


def load_comparables_from_db(case_id: int) -> List[Dict[str, Any]]:
    """
    Load saved comparables from database
    """
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
    
    Returns:
        (suggested_arv, low_estimate, high_estimate)
    """
    if not comparables:
        return 0.0, 0.0, 0.0
    
    # Filter to valid sales with prices
    valid_comps = [
        c for c in comparables
        if c.get("sale_price") and float(c["sale_price"]) > 0
    ]
    
    if not valid_comps:
        return 0.0, 0.0, 0.0
    
    # Calculate price per sqft for each comp
    price_per_sqft_values = []
    for comp in valid_comps:
        sqft = comp.get("sqft")
        price = comp.get("sale_price")
        
        if sqft and price and float(sqft) > 0:
            ppsf = float(price) / float(sqft)
            price_per_sqft_values.append(ppsf)
    
    if not price_per_sqft_values:
        # Fall back to raw average of sale prices
        prices = [float(c["sale_price"]) for c in valid_comps]
        avg_price = sum(prices) / len(prices)
        return avg_price, min(prices), max(prices)
    
    # Calculate average price per sqft
    avg_ppsf = sum(price_per_sqft_values) / len(price_per_sqft_values)
    
    # Use subject property sqft if available, otherwise estimate
    if subject_sqft and subject_sqft > 0:
        suggested_arv = avg_ppsf * subject_sqft
    else:
        # Estimate based on average comp sqft
        avg_comp_sqft = sum(
            float(c["sqft"]) for c in valid_comps if c.get("sqft")
        ) / len(valid_comps)
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
    """
    street = case_data.get("street")
    city = case_data.get("city")
    state = case_data.get("state")
    postal_code = case_data.get("postal_code")
    
    if not all([street, city, state]):
        raise ValueError("Incomplete address for comparables search")
    
    # Fetch comparables from API
    comparables = fetch_comparables_from_batchdata(
        street=street,
        city=city,
        state=state,
        postal_code=postal_code,
        radius_miles=1.0,
        max_results=10,
    )
    
    # Calculate distances if subject coordinates available
    subject_lat = case_data.get("lat")
    subject_lon = case_data.get("lon")
    
    if subject_lat and subject_lon:
        for comp in comparables:
            comp_lat = comp.get("latitude")
            comp_lon = comp.get("longitude")
            if comp_lat and comp_lon:
                comp["distance_miles"] = round(
                    calculate_distance(subject_lat, subject_lon, comp_lat, comp_lon),
                    2
                )
    
    # Save to database
    save_comparables_to_db(case_id, comparables)
    
    # Calculate suggested ARV
    suggested_arv, low_est, high_est = calculate_suggested_arv(
        comparables,
        subject_sqft=case_data.get("sqft"),
        subject_beds=case_data.get("beds"),
        subject_baths=case_data.get("baths"),
    )
    
    return {
        "comparables": comparables,
        "count": len(comparables),
        "suggested_arv": suggested_arv,
        "low_estimate": low_est,
        "high_estimate": high_est,
        "avg_price_per_sqft": (
            suggested_arv / case_data["sqft"] if case_data.get("sqft") else None
        ),
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
    """
    if not settings.google_maps_api_key:
        return "<p>Google Maps API key not configured</p>"
    
    comparables = load_comparables_from_db(case_id)
    
    # Build markers for map
    markers = []
    
    # Subject property (red marker)
    markers.append({
        "lat": subject_lat,
        "lon": subject_lon,
        "label": "Subject",
        "color": "red",
        "title": subject_address,
    })
    
    # Comparables (blue markers)
    for idx, comp in enumerate(comparables[:10]):  # Limit to 10 for clarity
        if comp.get("comp_address"):
            # Note: You'd need to geocode these addresses or store lat/lon in DB
            markers.append({
                "label": str(idx + 1),
                "color": "blue",
                "title": f"{comp['comp_address']} - ${comp['sale_price']:,.0f}",
            })
    
    # Generate basic HTML with Google Maps embed
    map_html = f"""
    <div id="comps-map" style="width: 100%; height: 400px; border-radius: 8px; border: 1px solid #ddd;">
        <iframe
            width="100%"
            height="400"
            frameborder="0"
            style="border:0; border-radius: 8px;"
            referrerpolicy="no-referrer-when-downgrade"
            src="https://www.google.com/maps/embed/v1/place?key={settings.google_maps_api_key}&q={subject_address}"
            allowfullscreen>
        </iframe>
    </div>
    """
    
    return map_html
