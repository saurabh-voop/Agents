"""
Web search tool — structured search for construction projects and DG set demand.
Uses SerpAPI for Google search results.
"""

import httpx
import structlog
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


def search_web(
    query: str,
    location: str = "Mumbai, Maharashtra, India",
    num_results: int = 10,
) -> list[dict]:
    """
    Search Google via SerpAPI for construction project signals.
    Returns list of {"title", "link", "snippet"}.
    """
    if not settings.serpapi_key:
        logger.warning("serpapi_key_not_set")
        return []

    params = {
        "api_key": settings.serpapi_key,
        "engine": "google",
        "q": query,
        "location": location,
        "google_domain": "google.co.in",
        "gl": "in",
        "hl": "en",
        "num": num_results,
    }

    try:
        response = httpx.get(settings.serpapi_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
            })

        logger.info("web_search", query=query[:50], results=len(results))
        return results
    except Exception as e:
        logger.error("web_search_failed", query=query[:50], error=str(e))
        return []


def search_construction_projects(location: str = "Mumbai Suburban") -> list[dict]:
    """Pre-built search for new construction projects needing DG sets."""
    queries = [
        f"new construction project {location} 2026",
        f"RERA registered project {location} under construction",
        f"infrastructure project {location} power requirement",
        f"diesel generator requirement {location}",
    ]
    all_results = []
    for q in queries:
        all_results.extend(search_web(q, num_results=5))
    return all_results
