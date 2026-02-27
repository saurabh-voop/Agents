"""
Web scraper tool — extracts lead signals from external websites.
Sources: Google News RSS, MahaRERA portal, company websites.
Uses BeautifulSoup for HTML parsing, feedparser for RSS.
"""

import httpx
import feedparser
import structlog
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed

logger = structlog.get_logger()


# ============================================================
# Google News RSS — construction project monitoring
# ============================================================

def fetch_google_news(
    query: str = "construction project Mumbai OR DG set OR diesel generator OR power backup Mumbai",
    days: int = 7,
) -> list[dict]:
    """
    Fetch recent news articles from Google News RSS.
    Returns list of {"title", "link", "published", "source"}.
    """
    encoded_query = query.replace(" ", "+")
    url = (
        f"https://news.google.com/rss/search?"
        f"q={encoded_query}+when:{days}d&hl=en-IN&gl=IN&ceid=IN:en"
    )

    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:30]:  # Limit to 30 articles
            articles.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": entry.get("source", {}).get("title", "Unknown"),
                "summary": entry.get("summary", ""),
            })
        logger.info("google_news_fetched", count=len(articles), query=query[:50])
        return articles
    except Exception as e:
        logger.error("google_news_failed", error=str(e))
        return []


# ============================================================
# MahaRERA — registered construction projects
# ============================================================

@retry(stop=stop_after_attempt(2), wait=wait_fixed(5))
def fetch_maharera_projects(
    district: str = "Mumbai Suburban",
    status: str = "Under Construction",
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """
    Fetch registered projects from MahaRERA.
    Tries the API first, falls back to web scraping if API doesn't exist.
    Returns list of {"project_name", "developer", "rera_number", "location", "type"}.
    """
    # Attempt 1: Try MahaRERA public API (may or may not exist)
    try:
        api_url = f"https://maharera.maharashtra.gov.in/wp-json/maha-rera/v2/registered-projects"
        params = {
            "page": page,
            "per_page": per_page,
            "district": district,
            "status": status,
        }
        response = httpx.get(api_url, params=params, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                logger.info("maharera_api_success", count=len(data))
                return _parse_maharera_api(data)
    except Exception:
        pass

    # Attempt 2: Scrape the public search page
    try:
        return _scrape_maharera_search(district, status)
    except Exception as e:
        logger.error("maharera_scrape_failed", error=str(e))
        return []


def _parse_maharera_api(data: list) -> list[dict]:
    """Parse MahaRERA API response into structured lead data."""
    projects = []
    for item in data:
        projects.append({
            "project_name": item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else str(item.get("title", "")),
            "developer": item.get("promoter_name", item.get("developer", "")),
            "rera_number": item.get("rera_number", item.get("registration_number", "")),
            "location": item.get("district", "") + ", " + item.get("taluka", ""),
            "type": item.get("project_type", "residential"),
            "status": item.get("status", "Under Construction"),
        })
    return projects


def _scrape_maharera_search(district: str, status: str) -> list[dict]:
    """
    Scrape MahaRERA public search page.
    Note: This is a fallback — the page structure may change.
    """
    url = "https://maharera.maharashtra.gov.in/projects/search"
    try:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        soup = BeautifulSoup(response.text, "lxml")

        # Extract project listings (structure depends on current page layout)
        projects = []
        rows = soup.select("table.views-table tbody tr") or soup.select(".view-content .views-row")

        for row in rows[:50]:
            cells = row.select("td") if row.select("td") else [row]
            if len(cells) >= 3:
                projects.append({
                    "project_name": cells[0].get_text(strip=True) if cells else "",
                    "developer": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    "rera_number": cells[2].get_text(strip=True) if len(cells) > 2 else "",
                    "location": district,
                    "type": "residential",
                    "status": status,
                })

        logger.info("maharera_scraped", count=len(projects))
        return projects
    except Exception as e:
        logger.error("maharera_scrape_failed", error=str(e))
        return []


# ============================================================
# Company website scraper — contact detail extraction
# ============================================================

def scrape_company_website(url: str) -> dict:
    """
    Scrape a company website for contact details.
    Fallback when Apollo.io doesn't find contacts.
    Returns: {"emails": [...], "phones": [...], "names": [...]}
    """
    import re

    try:
        response = httpx.get(url, timeout=20, follow_redirects=True)
        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text()

        # Extract emails
        emails = list(set(re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)))

        # Extract Indian phone numbers
        phones = list(set(re.findall(r'(?:\+91[\s-]?)?[789]\d{9}', text)))

        # Try to find "Contact Us" or "About Us" pages for more details
        contact_links = [
            a.get("href") for a in soup.select("a")
            if a.get_text(strip=True).lower() in ["contact", "contact us", "about us", "about"]
        ]

        logger.info("company_scraped", url=url, emails=len(emails), phones=len(phones))
        return {
            "emails": emails[:5],
            "phones": phones[:5],
            "contact_links": contact_links[:3],
        }
    except Exception as e:
        logger.error("company_scrape_failed", url=url, error=str(e))
        return {"emails": [], "phones": [], "contact_links": []}
