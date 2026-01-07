# app/services/masking_service.py
"""
Data Masking Service - V3
Masks sensitive data for non-owners
"""

import re
from typing import Any, Dict, Optional


def mask_address(address: str) -> str:
    """
    Mask street address, keeping city/state visible.
    Example: "123 Main St, Tampa, FL 33601" -> "*** Main St, Tampa, FL 33601"
    """
    if not address:
        return address
    
    # Match street number at the beginning
    masked = re.sub(r'^\d+', '***', str(address))
    return masked


def mask_phone(phone: str) -> str:
    """
    Mask phone number, showing last 4 digits.
    Example: "(813) 555-1234" -> "(***) ***-1234"
    """
    if not phone:
        return phone
    
    # Extract digits
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) >= 4:
        return f"(***) ***-{digits[-4:]}"
    return "***-****"


def mask_email(email: str) -> str:
    """
    Mask email address.
    Example: "john.doe@email.com" -> "j***@***.com"
    """
    if not email or '@' not in str(email):
        return "***@***.***"
    
    email = str(email)
    local, domain = email.rsplit('@', 1)
    domain_parts = domain.rsplit('.', 1)
    
    masked_local = local[0] + '***' if local else '***'
    masked_domain = '***.' + domain_parts[-1] if len(domain_parts) > 1 else '***'
    
    return f"{masked_local}@{masked_domain}"


def mask_name(name: str) -> str:
    """
    Mask person's name.
    Example: "John A. Smith" -> "J*** S***"
    """
    if not name:
        return "***"
    
    parts = str(name).split()
    masked_parts = [p[0] + '***' if p else '***' for p in parts]
    return ' '.join(masked_parts)


def mask_skip_trace_data(skip_trace: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mask all sensitive fields in skip trace data.
    Returns a deep copy with masked values.
    """
    if not skip_trace:
        return skip_trace
    
    import copy
    masked = copy.deepcopy(skip_trace)
    
    if 'results' in masked:
        for result in masked.get('results', []):
            if 'persons' in result:
                for person in result.get('persons', []):
                    # Mask name
                    if 'full_name' in person:
                        person['full_name'] = mask_name(person['full_name'])
                    if 'first_name' in person:
                        person['first_name'] = mask_name(person['first_name'])
                    if 'last_name' in person:
                        person['last_name'] = mask_name(person['last_name'])
                    
                    # Mask phones
                    if 'phones' in person:
                        for phone in person.get('phones', []):
                            if 'number' in phone:
                                phone['number'] = mask_phone(phone['number'])
                    
                    # Mask emails
                    if 'emails' in person:
                        for email in person.get('emails', []):
                            if 'email' in email:
                                email['email'] = mask_email(email['email'])
                    
                    # Mask addresses
                    if 'addresses' in person:
                        for addr in person.get('addresses', []):
                            if 'street' in addr:
                                addr['street'] = mask_address(addr['street'])
    
    return masked


def mask_property_data(property_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mask sensitive property data (owner info, mailing address).
    Returns a deep copy with masked values.
    """
    if not property_data:
        return property_data
    
    import copy
    masked = copy.deepcopy(property_data)
    
    # Mask owners
    if 'owners' in masked:
        for owner in masked.get('owners', []):
            if 'fullName' in owner:
                owner['fullName'] = mask_name(owner['fullName'])
            if 'mailingAddress' in owner:
                addr = owner['mailingAddress']
                if 'street' in addr:
                    addr['street'] = mask_address(addr['street'])
    
    # Mask current owner if present
    if 'currentOwner' in masked:
        masked['currentOwner'] = mask_name(masked['currentOwner'])
    
    return masked


def mask_case_for_display(
    case: Any,
    property_data: Optional[Dict],
    skip_trace: Optional[Dict],
    can_view_sensitive: bool
) -> tuple:
    """
    Apply masking to case data based on permissions.
    
    Args:
        case: The Case object
        property_data: Parsed property data dict
        skip_trace: Skip trace results dict
        can_view_sensitive: Whether user can view unmasked data
    
    Returns:
        Tuple of (display_address, masked_property, masked_skip_trace)
    """
    if can_view_sensitive:
        # User has permission - return unmasked data
        address = case.address_override or case.address or ""
        return address, property_data, skip_trace
    
    # User doesn't have permission - mask everything
    address = case.address_override or case.address or ""
    masked_address = mask_address(address)
    
    masked_property = mask_property_data(property_data) if property_data else None
    masked_skip_trace = mask_skip_trace_data(skip_trace) if skip_trace else None
    
    return masked_address, masked_property, masked_skip_trace


def get_locked_message(field_type: str) -> str:
    """
    Get a user-friendly message for locked content.
    
    Args:
        field_type: Type of field (address, phone, email, document, etc.)
    
    Returns:
        Message to display to user
    """
    messages = {
        "address": "🔒 Claim this case to view the full address",
        "phone": "🔒 Claim to view phone numbers",
        "email": "🔒 Claim to view email addresses",
        "document": "🔒 Claim this case to access documents",
        "skip_trace": "🔒 Claim to view skip trace data",
        "property": "🔒 Claim to view property details",
        "default": "🔒 Claim this case to unlock",
    }
    return messages.get(field_type, messages["default"])
