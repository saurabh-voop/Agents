"""
Zoho CRM integration tool.
Handles authentication (OAuth2 refresh), lead CRUD, contact lookup, quotation creation.
Used by: Agent-S (leads), Agent-RM (quotations), Agent-GM (analytics).
"""

import time
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Token cache — avoids refreshing on every call
_token_cache = {"access_token": None, "expires_at": 0}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
def _refresh_token() -> str:
    """Get a fresh access token using the refresh token."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    response = httpx.post(
        settings.zoho_auth_url,
        data={
            "grant_type": "refresh_token",
            "client_id": settings.zoho_client_id,
            "client_secret": settings.zoho_client_secret,
            "refresh_token": settings.zoho_refresh_token,
        },
    )
    response.raise_for_status()
    data = response.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60

    logger.info("zoho_token_refreshed")
    return data["access_token"]


def _headers() -> dict:
    """Auth headers for Zoho API calls."""
    token = _refresh_token()
    return {"Authorization": f"Zoho-oauthtoken {token}"}


# ============================================================
# Lead Operations
# ============================================================

def search_leads(criteria: str, max_results: int = 20) -> list[dict]:
    """
    Search Zoho CRM leads by criteria.
    Example: search_leads("((Lead_Status:equals:New)and(State:equals:Maharashtra))")
    """
    url = f"{settings.zoho_api_base}/Leads/search"
    params = {"criteria": criteria, "per_page": max_results}

    response = httpx.get(url, headers=_headers(), params=params, timeout=30)
    if response.status_code == 204:
        return []  # No results
    response.raise_for_status()
    return response.json().get("data", [])


def get_lead(lead_id: str) -> dict | None:
    """Get a single lead by Zoho ID."""
    url = f"{settings.zoho_api_base}/Leads/{lead_id}"
    response = httpx.get(url, headers=_headers(), timeout=30)
    if response.status_code == 204:
        return None
    response.raise_for_status()
    data = response.json().get("data", [])
    return data[0] if data else None


def create_lead(lead_data: dict) -> dict:
    """
    Create a new lead in Zoho CRM.
    lead_data should match Zoho CRM Lead fields.
    Returns: {"id": "zoho_lead_id", ...}
    """
    url = f"{settings.zoho_api_base}/Leads"
    payload = {"data": [lead_data]}

    response = httpx.post(url, headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    
    if result.get("data") and result["data"][0].get("status") == "success":
        zoho_id = result["data"][0]["details"]["id"]
        logger.info("zoho_lead_created", zoho_id=zoho_id)
        return {"id": zoho_id, "status": "success"}
    
    logger.error("zoho_lead_create_failed", result=result)
    return {"id": None, "status": "failed", "error": str(result)}


def update_lead(lead_id: str, update_data: dict) -> dict:
    """Update an existing Zoho CRM lead."""
    url = f"{settings.zoho_api_base}/Leads/{lead_id}"
    payload = {"data": [update_data]}

    response = httpx.put(url, headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def search_leads_by_company(company_name: str) -> list[dict]:
    """Search for existing leads by company name — for deduplication."""
    criteria = f"(Company:equals:{company_name})"
    return search_leads(criteria)


def search_leads_by_phone(phone: str) -> list[dict]:
    """Search for existing leads by phone — for deduplication."""
    criteria = f"((Phone:equals:{phone})or(Mobile:equals:{phone}))"
    return search_leads(criteria)


# ============================================================
# Contact Operations
# ============================================================

def search_contacts(criteria: str) -> list[dict]:
    """Search Zoho CRM contacts."""
    url = f"{settings.zoho_api_base}/Contacts/search"
    params = {"criteria": criteria}

    response = httpx.get(url, headers=_headers(), params=params, timeout=30)
    if response.status_code == 204:
        return []
    response.raise_for_status()
    return response.json().get("data", [])


# ============================================================
# Quotation Operations (Agent-RM)
# ============================================================

def create_quotation(quote_data: dict) -> dict:
    """
    Create a draft quotation in Zoho CRM.
    Called by Agent-RM after GM approval.
    """
    url = f"{settings.zoho_api_base}/Quotes"
    payload = {"data": [quote_data]}

    response = httpx.post(url, headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()

    if result.get("data") and result["data"][0].get("status") == "success":
        zoho_id = result["data"][0]["details"]["id"]
        logger.info("zoho_quotation_created", zoho_id=zoho_id)
        return {"id": zoho_id, "status": "success"}

    return {"id": None, "status": "failed", "error": str(result)}
