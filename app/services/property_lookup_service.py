# app/services/property_lookup_service.py
"""
Property Lookup Service - BatchData API Integration
✅ WITH COMPREHENSIVE ERROR HANDLING (Fixed Version)
"""

import requests
import logging
import time
from typing import Optional, Dict, Any
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config import settings

logger = logging.getLogger("pascowebapp.property_lookup")


# ========================================
# Retry Session with Exponential Backoff
# ========================================

def create_retry_session(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (500, 502, 503, 504)
) -> requests.Session:
    """
    Create a requests session with automatic retry logic
    
    Args:
        retries: Number of retry attempts
        backoff_factor: Multiplier for exponential backoff (0.5 = 0.5s, 1s, 2s)
        status_forcelist: HTTP status codes to retry on
    
    Returns:
        Configured requests.Session
    
    Example:
        session = create_retry_session(retries=3, backoff_factor=1.0)
        # Will retry up to 3 times with 1s, 2s, 4s backoff
    """
    session = requests.Session()
    
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],  # Retry on these methods
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session


# ========================================
# Property Lookup Functions
# ========================================

def lookup_property_by_address(address: str) -> Optional[Dict[str, Any]]:
    """
    Look up property information by address using BatchData API
    
    Args:
        address: Property address to look up (e.g., "123 Main St, Tampa, FL 33602")
    
    Returns:
        Property data dict if successful, None if error
        
    Example:
        result = lookup_property_by_address("123 Main St, Tampa, FL 33602")
        if result:
            print(f"Owner: {result.get('owner_name')}")
            print(f"Value: ${result.get('assessed_value'):,}")
        else:
            print("Lookup failed (check logs)")
    
    Error Handling:
        - Returns None on any error (network, timeout, HTTP, JSON)
        - Logs all errors with appropriate level (error/warning)
        - Does not raise exceptions (graceful degradation)
    """
    # Input validation
    if not address or not address.strip():
        logger.warning("lookup_property_by_address called with empty address")
        return None
    
    address = address.strip()
    
    # Check if API is configured
    if not settings.batchdata_base_url or not settings.batchdata_api_key:
        logger.error(
            "BatchData API not configured. Set BATCHDATA_BASE_URL and "
            "BATCHDATA_API_KEY in environment variables."
        )
        return None
    
    # Build request
    url = f"{settings.batchdata_base_url}/property/lookup"
    headers = {
        "Authorization": f"Bearer {settings.batchdata_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "address": address
    }
    
    logger.info(f"Looking up property: {address[:50]}...")  # Truncate for logging
    
    # Create retry session (will retry 3 times with exponential backoff)
    session = create_retry_session(retries=3, backoff_factor=0.5)
    
    try:
        # Make request with timeout and retry logic
        resp = session.post(
            url,
            json=payload,
            headers=headers,
            timeout=30  # 30 second timeout
        )
        
        # Check for HTTP errors (4xx, 5xx)
        resp.raise_for_status()
        
        # Parse JSON response
        try:
            data = resp.json()
        except ValueError as json_err:
            logger.error(f"Invalid JSON response from BatchData API: {json_err}")
            logger.debug(f"Response text: {resp.text[:500]}")
            return None
        
        # Validate response structure
        if not isinstance(data, dict):
            logger.error(f"Unexpected response type from API: {type(data)}, expected dict")
            return None
        
        logger.info(f"✅ Successfully looked up property: {address[:50]}")
        logger.debug(f"Response keys: {list(data.keys())}")
        
        return data
    
    except requests.exceptions.Timeout:
        logger.error(
            f"⏱️ Timeout looking up property: {address[:50]} "
            f"(API did not respond within 30 seconds)"
        )
        return None
    
    except requests.exceptions.ConnectionError as conn_err:
        logger.error(
            f"🔌 Connection error looking up property: {address[:50]} "
            f"(Check network/internet connection)"
        )
        logger.debug(f"Connection error details: {conn_err}")
        return None
    
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response else "unknown"
        
        # Log with appropriate severity based on status code
        if status_code == 404:
            logger.warning(f"🔍 Property not found in API: {address[:50]}")
        elif status_code == 401:
            logger.error("🔐 API authentication failed. Check BATCHDATA_API_KEY.")
        elif status_code == 429:
            logger.error("🚫 API rate limit exceeded. Try again later.")
        elif status_code >= 500:
            logger.error(f"🔥 API server error ({status_code}). The API service may be down.")
        else:
            logger.error(f"❌ HTTP error {status_code} looking up property: {address[:50]}")
        
        # Log response body for debugging (truncated)
        if http_err.response:
            logger.debug(f"Error response: {http_err.response.text[:500]}")
        
        return None
    
    except requests.exceptions.RequestException as req_err:
        # Catch-all for other requests errors
        logger.error(f"❌ Request error looking up property: {address[:50]}")
        logger.debug(f"Request error details: {req_err}")
        return None
    
    except Exception as exc:
        # Catch any unexpected errors
        logger.exception(f"💥 Unexpected error looking up property: {address[:50]}")
        return None
    
    finally:
        # Always close the session to free resources
        session.close()


def lookup_property_by_parcel_id(parcel_id: str) -> Optional[Dict[str, Any]]:
    """
    Look up property information by parcel ID
    
    Args:
        parcel_id: Property parcel/folio ID (e.g., "12-34-56-7890-12345-6789")
    
    Returns:
        Property data dict if successful, None if error
        
    Example:
        result = lookup_property_by_parcel_id("12-34-56-7890-12345-6789")
        if result:
            print(f"Address: {result.get('address')}")
        else:
            print("Lookup failed")
    """
    # Input validation
    if not parcel_id or not parcel_id.strip():
        logger.warning("lookup_property_by_parcel_id called with empty parcel_id")
        return None
    
    parcel_id = parcel_id.strip()
    
    # Check if API is configured
    if not settings.batchdata_base_url or not settings.batchdata_api_key:
        logger.error("BatchData API not configured.")
        return None
    
    # Build request
    url = f"{settings.batchdata_base_url}/property/lookup"
    headers = {
        "Authorization": f"Bearer {settings.batchdata_api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "parcel_id": parcel_id
    }
    
    logger.info(f"Looking up property by parcel ID: {parcel_id}")
    
    # Create retry session
    session = create_retry_session(retries=3, backoff_factor=0.5)
    
    try:
        resp = session.post(
            url,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        resp.raise_for_status()
        
        # Parse JSON
        try:
            data = resp.json()
        except ValueError as json_err:
            logger.error(f"Invalid JSON response: {json_err}")
            return None
        
        # Validate structure
        if not isinstance(data, dict):
            logger.error(f"Unexpected response type: {type(data)}")
            return None
        
        logger.info(f"✅ Successfully looked up property by parcel ID: {parcel_id}")
        return data
    
    except requests.exceptions.Timeout:
        logger.error(f"⏱️ Timeout looking up parcel ID: {parcel_id}")
        return None
    
    except requests.exceptions.ConnectionError:
        logger.error(f"🔌 Connection error looking up parcel ID: {parcel_id}")
        return None
    
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response else "unknown"
        
        if status_code == 404:
            logger.warning(f"🔍 Parcel ID not found: {parcel_id}")
        elif status_code == 401:
            logger.error("🔐 API authentication failed.")
        else:
            logger.error(f"❌ HTTP error {status_code} looking up parcel ID: {parcel_id}")
        
        return None
    
    except requests.exceptions.RequestException:
        logger.error(f"❌ Request error looking up parcel ID: {parcel_id}")
        return None
    
    except Exception:
        logger.exception(f"💥 Unexpected error looking up parcel ID: {parcel_id}")
        return None
    
    finally:
        session.close()


def test_api_connection() -> bool:
    """
    Test if the BatchData API is accessible and configured correctly
    
    Returns:
        True if API is accessible and authenticated, False otherwise
        
    Example:
        if test_api_connection():
            print("✅ API is working")
        else:
            print("❌ API is not configured or unreachable")
    """
    # Check configuration
    if not settings.batchdata_base_url:
        logger.error("BATCHDATA_BASE_URL not set in environment")
        return False
    
    if not settings.batchdata_api_key:
        logger.error("BATCHDATA_API_KEY not set in environment")
        return False
    
    # Try a health check or simple request
    # Note: Adjust endpoint based on actual API
    url = f"{settings.batchdata_base_url}/health"
    headers = {
        "Authorization": f"Bearer {settings.batchdata_api_key}"
    }
    
    logger.info("Testing BatchData API connection...")
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        logger.info("✅ BatchData API connection successful")
        return True
    
    except requests.exceptions.Timeout:
        logger.error("⏱️ API connection test timed out")
        return False
    
    except requests.exceptions.ConnectionError:
        logger.error("🔌 Could not connect to API (network error)")
        return False
    
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response else "unknown"
        
        if status_code == 401:
            logger.error("🔐 API authentication failed (invalid API key)")
        elif status_code == 404:
            # /health endpoint might not exist - try a different approach
            logger.warning("Health endpoint not found, but API may still work")
            # Consider this a pass if we got a 404 (server is reachable)
            return True
        else:
            logger.error(f"❌ API test failed with HTTP {status_code}")
        
        return False
    
    except Exception as exc:
        logger.error(f"💥 Unexpected error testing API: {exc}")
        return False


def batch_lookup_properties(
    addresses: list[str],
    max_concurrent: int = 5,
    delay_between_requests: float = 0.5
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Look up multiple properties with rate limiting
    
    Args:
        addresses: List of addresses to look up
        max_concurrent: Maximum concurrent requests (for future async implementation)
        delay_between_requests: Seconds to wait between requests (rate limiting)
    
    Returns:
        Dict mapping address to property data (or None if failed)
        
    Example:
        addresses = ["123 Main St, Tampa, FL", "456 Oak Ave, Tampa, FL"]
        results = batch_lookup_properties(addresses, delay_between_requests=1.0)
        
        for addr, data in results.items():
            if data:
                print(f"{addr}: {data.get('owner_name')}")
            else:
                print(f"{addr}: Lookup failed")
    """
    results = {}
    
    logger.info(f"Batch lookup: {len(addresses)} addresses")
    
    for i, address in enumerate(addresses):
        if not address or not address.strip():
            results[address] = None
            continue
        
        logger.info(f"Batch lookup {i+1}/{len(addresses)}: {address[:50]}")
        
        # Look up property
        data = lookup_property_by_address(address)
        results[address] = data
        
        # Rate limiting (except for last request)
        if i < len(addresses) - 1 and delay_between_requests > 0:
            time.sleep(delay_between_requests)
    
    successful = sum(1 for d in results.values() if d is not None)
    logger.info(
        f"Batch lookup complete: {successful}/{len(addresses)} successful"
    )
    
    return results
