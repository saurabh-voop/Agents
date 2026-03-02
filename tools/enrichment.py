"""
Contact enrichment tool — finds decision-maker contacts for companies.

Priority order (free first, paid fallback):
  1. Website scrape via Google search + contact page (free)
  2. JustDial scrape (free)
  3. Apollo.io API (paid — only used if API key is configured)

Critical for news/RERA leads that have company names but no contacts.
"""

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


def enrich_contact(
    company_name: str,
    target_titles: list[str] | None = None,
    location: str = "Mumbai, India",
) -> dict:
    """
    Find the right person to contact at a company.

    Tries free methods first (website scrape, JustDial),
    falls back to Apollo.io only if API key is configured.

    Returns:
        {"name", "title", "email", "phone", "source", "confidence"}
        or {"error": "..."} if not found
    """
    # Step 1: Free — scrape developer website + JustDial
    from tools.scraper import find_developer_contact
    free_result = find_developer_contact(company_name, location.split(",")[0])

    if free_result.get("phone") or free_result.get("email"):
        return {
            "name": "",          # website scrape rarely gives a name
            "title": "",
            "email": free_result.get("email", ""),
            "phone": free_result.get("phone", ""),
            "website": free_result.get("website", ""),
            "source": free_result.get("source", "website"),
            "confidence": "high" if free_result.get("phone") else "medium",
        }

    # Step 2: Apollo.io (paid fallback — only if key is set)
    if settings.apollo_api_key:
        apollo_result = _enrich_via_apollo(company_name, target_titles, location)
        if not apollo_result.get("error"):
            apollo_result["source"] = "apollo"
            return apollo_result
        logger.info("apollo_fallback_failed", company=company_name, error=apollo_result.get("error"))

    return {"error": f"No contact found for: {company_name}"}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
def _enrich_via_apollo(
    company_name: str,
    target_titles: list[str] | None = None,
    location: str = "Mumbai, India",
) -> dict:
    """Apollo.io enrichment — paid fallback."""
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
            return {"error": f"No matching contacts found at {company_name}"}

        person = people[0]
        return {
            "name": person.get("name", ""),
            "title": person.get("title", ""),
            "email": person.get("email", ""),
            "phone": _extract_phone(person),
            "linkedin_url": person.get("linkedin_url", ""),
            "company": company_name,
            "confidence": "high" if person.get("email") else "medium",
        }

    except Exception as e:
        logger.error("apollo_enrichment_failed", company=company_name, error=str(e))
        return {"error": str(e)}


def _extract_phone(person: dict) -> str:
    """Extract phone number from Apollo person data."""
    phones = person.get("phone_numbers", [])
    if phones:
        for p in phones:
            if p.get("type") == "mobile":
                return p.get("number", "")
        return phones[0].get("number", "")
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
