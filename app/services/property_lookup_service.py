import requests
from app.settings import settings

def lookup_property_by_address(address: str):
    url = f"{settings.BATCHDATA_BASE_URL}/property/lookup"
    headers = {
        "Authorization": f"Bearer {settings.BATCHDATA_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "address": address
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()
