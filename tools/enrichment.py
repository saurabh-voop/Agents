"""
Contact enrichment tool — finds decision-maker contacts for companies.
Uses Apollo.io API. Critical for news/RERA leads that have company names but no contacts.
"""

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
def enrich_contact(
    company_name: str,
    target_titles: list[str] | None = None,
    location: str = "Mumbai, India",
) -> dict:
    """
    Find the right person to contact at a company.
    
    Args:
        company_name: The company to search
        target_titles: Job titles to prioritize (default: procurement/projects roles)
        location: Company location for filtering
    
    Returns:
        {"name", "title", "email", "phone", "linkedin_url", "confidence"}
        or {"error": "..."} if not found
    """
    if not settings.apollo_api_key:
        logger.warning("apollo_api_key_not_set")
        return {"error": "Apollo.io API key not configured"}

    if target_titles is None:
        # DG set buyer personas in construction companies
        target_titles = [
            "Procurement", "Purchase", "Projects", "Project Manager",
            "MEP", "Electrical", "Director", "Managing Director",
            "VP Operations", "General Manager", "Chief Engineer",
        ]

    # Step 1: Search for the organization
    org_url = f"{settings.apollo_api_url}/organizations/search"
    org_payload = {
        "api_key": settings.apollo_api_key,
        "q_organization_name": company_name,
        "organization_locations": [location],
        "per_page": 1,
    }

    try:
        response = httpx.post(org_url, json=org_payload, timeout=30)
        response.raise_for_status()
        orgs = response.json().get("organizations", [])

        if not orgs:
            logger.info("apollo_org_not_found", company=company_name)
            return {"error": f"Organization not found: {company_name}"}

        org_id = orgs[0].get("id")

        # Step 2: Search for people at this org with target titles
        people_url = f"{settings.apollo_api_url}/people/search"
        people_payload = {
            "api_key": settings.apollo_api_key,
            "organization_ids": [org_id],
            "person_titles": target_titles,
            "per_page": 5,
        }

        response = httpx.post(people_url, json=people_payload, timeout=30)
        response.raise_for_status()
        people = response.json().get("people", [])

        if not people:
            logger.info("apollo_no_contacts", company=company_name)
            return {"error": f"No matching contacts found at {company_name}"}

        # Return the best match (first result, highest relevance)
        person = people[0]
        result = {
            "name": person.get("name", ""),
            "title": person.get("title", ""),
            "email": person.get("email", ""),
            "phone": _extract_phone(person),
            "linkedin_url": person.get("linkedin_url", ""),
            "company": company_name,
            "confidence": "high" if person.get("email") else "medium",
        }

        logger.info("apollo_contact_found", company=company_name, name=result["name"])
        return result

    except Exception as e:
        logger.error("apollo_enrichment_failed", company=company_name, error=str(e))
        return {"error": str(e)}


def _extract_phone(person: dict) -> str:
    """Extract phone number from Apollo person data."""
    phones = person.get("phone_numbers", [])
    if phones:
        # Prefer mobile numbers
        for p in phones:
            if p.get("type") == "mobile":
                return p.get("number", "")
        return phones[0].get("number", "")

    # Fallback to organization phone
    org = person.get("organization", {})
    return org.get("phone", "")


def enrich_contact_batch(companies: list[dict]) -> list[dict]:
    """
    Enrich multiple companies in batch.
    Input: [{"company_name": "...", "location": "..."}]
    Output: [{"company_name": "...", "contact": {...}}]
    """
    results = []
    for company in companies:
        contact = enrich_contact(
            company_name=company["company_name"],
            location=company.get("location", "Mumbai, India"),
        )
        results.append({
            "company_name": company["company_name"],
            "contact": contact,
        })
    return results
