"""
Web scraper tool — extracts lead signals from external websites.
Sources: Google News RSS, MahaRERA portal (Playwright), company websites.

MahaRERA scraper uses Playwright (headless Chromium) to handle the JS-rendered
portal. Filters by district and registration date to get active 2024-2026 projects.
"""

import re
import json
import httpx
import feedparser
import structlog
from datetime import datetime
from urllib.parse import unquote
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed
from core.config import get_settings

logger = structlog.get_logger()

# Districts covering Greater Mumbai + surrounding areas with heavy construction
# Keys = display names, values = MahaRERA form IDs (from Konkan division, state=27)
MUMBAI_DISTRICTS = ["Mumbai Suburban", "Mumbai City", "Thane", "Raigad"]
_DISTRICT_IDS = {
    "Mumbai Suburban": "518",
    "Mumbai City":     "519",
    "Thane":           "517",
    "Raigad":          "520",
}
_STATE_ID    = "27"   # Maharashtra
_DIVISION_ID = "6"    # Konkan

# Only scrape projects registered from Jan 2024 onwards — active construction phase
RERA_FROM_DATE = "01/01/2024"
RERA_TO_DATE   = datetime.utcnow().strftime("%d/%m/%Y")  # today


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
        for entry in feed.entries[:30]:
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
# MahaRERA — Playwright-based scraper for JS-rendered portal
# ============================================================

def fetch_maharera_projects(
    districts: list[str] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    pages_per_district: int = 15,
) -> list[dict]:
    """
    Fetch recently registered projects from MahaRERA.

    Strategy: The MahaRERA search page supports GET params for district filter
    and ?page=N for pagination. Recent projects (2024-2026) are on the LAST pages
    (sorted by RERA number ascending, newest = highest number = last pages).

    Scrapes the last `pages_per_district` pages for each district, keeping only
    projects with Last Modified >= Jan 2024.

    Returns list of {"project_name", "developer", "rera_number", "location",
                      "district", "pincode", "registered_on", "type"}.
    """
    districts  = districts  or MUMBAI_DISTRICTS
    from_date = from_date or RERA_FROM_DATE  # "01/01/2024"

    # Parse cutoff year from from_date for result filtering (to_date not used — we scrape last pages)
    cutoff_year = int(from_date.split("/")[2])

    all_projects = []
    for district in districts:
        try:
            projects = _scrape_district_pages(district, cutoff_year, pages_per_district)
            all_projects.extend(projects)
            logger.info("maharera_district_done", district=district, count=len(projects))
        except Exception as e:
            logger.error("maharera_district_failed", district=district, error=str(e))

    logger.info("maharera_total", count=len(all_projects))
    return all_projects


def _scrape_district_pages(district: str, cutoff_year: int, pages_per_district: int) -> list[dict]:
    """
    Scrape the last N pages for one district via direct GET requests.
    Discovers total page count first, then fetches the most recent pages.
    """
    district_id = _DISTRICT_IDS.get(district)
    if not district_id:
        logger.warning("unknown_district", district=district)
        return []

    base_url = (
        "https://maharera.maharashtra.gov.in/projects-search-result"
        f"?project_state={_STATE_ID}&project_division={_DIVISION_ID}&project_district={district_id}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # Step 1: Fetch page 0 to get total result count
    try:
        resp = httpx.get(base_url, headers=headers, timeout=30, follow_redirects=True)
        html = resp.text
        count_match = re.search(r"Showing[^<]*<span[^>]*>(\d+)</span>", html)
        if not count_match:
            logger.warning("maharera_count_not_found", district=district)
            return []
        total_results = int(count_match.group(1))
        results_per_page = 10
        total_pages = (total_results + results_per_page - 1) // results_per_page
        logger.info("maharera_district_total", district=district, total=total_results, pages=total_pages)
    except Exception as e:
        logger.error("maharera_count_failed", district=district, error=str(e))
        return []

    # Step 2: Fetch the last N pages (most recent registrations)
    start_page = max(0, total_pages - pages_per_district)
    projects = []

    for page_num in range(start_page, total_pages):
        try:
            url = f"{base_url}&page={page_num}"
            resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
            batch = _parse_project_cards(resp.text, district, cutoff_year)
            projects.extend(batch)
            logger.info("maharera_page_scraped", district=district, page=page_num, batch=len(batch))
        except Exception as e:
            logger.warning("maharera_page_failed", district=district, page=page_num, error=str(e))

    return projects


def _parse_project_cards(html: str, district: str, cutoff_year: int = 2024) -> list[dict]:
    """Parse project cards from MahaRERA search results HTML."""
    projects = []

    # Primary pattern: shadow div cards (confirmed structure from earlier investigation)
    cards = re.findall(
        r'class="row shadow p-3 mb-5 bg-body rounded">(.*?)(?=class="row shadow p-3 mb-5|$)',
        html, re.DOTALL
    )

    for card in cards:
        clean = re.sub(r'<[^>]+>', ' ', card)
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Extract RERA number — supports both old (P51800002451) and new (PM1180002502407) formats
        rera_match = re.search(r'#\s*([A-Z]{1,2}\d{9,15})', clean)
        if not rera_match:
            continue
        rera_number = rera_match.group(1)

        # Extract last modified date and apply cutoff filter
        date_match = re.search(r'Last Modified\s+(\d{4})-(\d{2})-(\d{2})', clean)
        if not date_match:
            registered_on = ""
        else:
            registered_on = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
            # Skip projects not modified since cutoff year — they are stale/completed
            if int(date_match.group(1)) < cutoff_year:
                continue

        # Extract pincode
        pincode_match = re.search(r'Pincode\s+(\d{6})', clean)
        pincode = pincode_match.group(1) if pincode_match else ""

        # Extract district from card
        dist_match = re.search(r'District\s+([A-Za-z\s\(\)]+?)(?:Last Modified|$)', clean)
        card_district = dist_match.group(1).strip() if dist_match else district

        # Extract project name from <h4> or <strong> tag (before stripping HTML)
        title_match = re.search(r'<(?:h[1-6]|strong)[^>]*>(.*?)</(?:h[1-6]|strong)>', card, re.DOTALL | re.IGNORECASE)
        project_name = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""

        # Developer name: text between RERA number and location city (before "Find Route")
        # Format: "# RERA_NUM  PROJECT_NAME  DEVELOPER_NAME  CITY  Find Route"
        before_route = clean[:clean.find("Find Route")].strip() if "Find Route" in clean else clean
        # Remove RERA number and project name from the text to isolate developer
        remainder = re.sub(r'#\s*[A-Z]{1,2}\d{9,15}', '', before_route).strip()
        if project_name:
            remainder = remainder.replace(project_name, '', 1).strip()
        # Strip trailing city name (last word or two before "Find Route")
        dev_tokens = remainder.split()
        # Heuristic: developer name ends before a single capitalised city word (Kurla, Andheri, Borivali…)
        # Remove the last 1 token (city) to get developer
        developer = " ".join(dev_tokens[:-1]).strip() if len(dev_tokens) > 1 else remainder.strip()

        if not project_name and not developer:
            continue

        projects.append({
            "project_name": project_name,
            "developer": developer,
            "rera_number": rera_number,
            "location": card_district,
            "district": card_district,
            "pincode": pincode,
            "registered_on": registered_on,
            "type": "residential",
            "status": "Under Construction",
            "source": "maharera",
        })

    return projects


# ============================================================
# Developer contact finder — free, no Apollo needed
# ============================================================

def find_developer_contact(
    developer_name: str,
    location: str = "Mumbai",
) -> dict:
    """
    Find phone/email for a developer using Google search + website scraping.
    Tries 3 sources in order:
      1. DuckDuckGo → developer's own website → scrapes tel: links + visible text
      2. DuckDuckGo phone snippet search (finds numbers in search result snippets)
      3. JustDial listing (last resort — often masked as 8888888888, filtered out)

    Returns {"phone": "...", "email": "...", "website": "...", "source": "..."}
    or {"phone": "", "email": "", "source": "not_found"}
    """
    result = {"phone": "", "email": "", "website": "", "source": "not_found"}

    # Step 1: DuckDuckGo → official website → scrape tel: links + visible text
    # Works well for large developers with their own website.
    # Natural delay: time spent scraping the website separates the two DDG calls below.
    website = _google_find_website(developer_name, location)
    if website:
        result["website"] = website
        contact = _scrape_contact_page(website)
        if contact.get("phone"):
            result["phone"] = contact["phone"]
            result["source"] = "website"
            logger.info("contact_found_website", developer=developer_name, phone=result["phone"])
        if contact.get("email"):
            result["email"] = contact["email"]

    # Step 2: DuckDuckGo phone snippet search — only if step 1 had no phone.
    # Works well for small developers listed in directories.
    # The time spent on step 1 (website scraping) provides natural spacing between DDG requests.
    if not result["phone"]:
        ddg_contact = _scrape_indiamart(developer_name, location)
        if ddg_contact.get("phone"):
            result["phone"] = ddg_contact["phone"]
            result["source"] = "ddg_snippet"
            logger.info("contact_found_ddg_snippet", developer=developer_name, phone=result["phone"])

    # Step 3: JustDial — last resort (often returns masked 8888888888, filtered out)
    if not result["phone"]:
        jd_contact = _scrape_justdial(developer_name, location)
        if jd_contact.get("phone"):
            result["phone"] = jd_contact["phone"]
            result["source"] = "justdial"
            logger.info("contact_found_justdial", developer=developer_name, phone=result["phone"])

    if not result["phone"]:
        logger.info("contact_not_found", developer=developer_name)

    return result


def _google_find_website(company_name: str, location: str) -> str:
    """
    Find the company's official website.
    Uses SerpAPI if SERPAPI_KEY is set (reliable Google results, no throttling).
    Falls back to DuckDuckGo HTML scraping (free but throttles under load).
    Returns URL or ''.
    """
    settings = get_settings()
    query = f"{company_name} {location} real estate developer official website"

    SKIP_DOMAINS = {"google", "youtube", "wikipedia", "magicbricks", "99acres",
                    "housing", "makaan", "sulekha", "facebook", "linkedin",
                    "justdial", "indiamart", "tradeindia", "commonfloor", "proptiger"}

    # --- SerpAPI (preferred — real Google results, no throttling) ---
    if settings.serpapi_key:
        try:
            resp = httpx.get(
                settings.serpapi_url,
                params={
                    "q": query,
                    "api_key": settings.serpapi_key,
                    "engine": "google",
                    "num": 5,
                    "gl": "in",
                    "hl": "en",
                },
                timeout=15,
            )
            for r in resp.json().get("organic_results", []):
                site_url = r.get("link", "")
                if not site_url.startswith("http"):
                    continue
                domain = re.sub(r'https?://(www\.)?', '', site_url).split('/')[0].lower()
                if not any(skip in domain for skip in SKIP_DOMAINS):
                    return site_url
        except Exception as e:
            logger.warning("serpapi_website_search_failed", company=company_name, error=str(e))

    # --- DuckDuckGo HTML fallback (no key needed, may throttle under load) ---
    try:
        encoded = query.replace(" ", "+")
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        resp = httpx.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=15,
            follow_redirects=True,
        )
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            m = re.search(r'uddg=([^&]+)', href)
            if not m:
                continue
            site_url = unquote(m.group(1))
            if not site_url.startswith("http"):
                continue
            domain = re.sub(r'https?://(www\.)?', '', site_url).split('/')[0].lower()
            if not any(skip in domain for skip in SKIP_DOMAINS):
                return site_url
    except Exception as e:
        logger.warning("duckduckgo_website_search_failed", company=company_name, error=str(e))

    return ""


def _scrape_contact_page(base_url: str) -> dict:
    """
    Scrape a company website for phone and email.
    Tries homepage first, then /contact and /contact-us pages.
    """
    result = {"phone": "", "email": ""}
    pages_to_try = [base_url, f"{base_url.rstrip('/')}/contact", f"{base_url.rstrip('/')}/contact-us"]

    INDIAN_PHONE = re.compile(r'(?:\+91[\s\-]?)?[6-9]\d{9}')
    EMAIL_RE     = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

    for url in pages_to_try:
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=15,
                follow_redirects=True,
            )
            soup = BeautifulSoup(resp.text, "lxml")

            # Priority 1: extract from tel: href links (most reliable — explicitly marked as phone)
            for a in soup.select("a[href^='tel:']"):
                digits = re.sub(r'[^\d]', '', a["href"])
                # Take last 10 digits — handles +91xxxxxxxxxx, 91xxxxxxxxxx, 0xxxxxxxxxx
                clean = digits[-10:] if len(digits) >= 10 else digits
                if re.match(r'[6-9]\d{9}$', clean):
                    result["phone"] = clean
                    break

            # Priority 2: scan visible text for Indian phone patterns
            if not result["phone"]:
                text = soup.get_text()
                for p in INDIAN_PHONE.findall(text):
                    digits = re.sub(r'[^\d]', '', p)
                    clean = digits[-10:] if len(digits) >= 10 else digits
                    if re.match(r'[6-9]\d{9}$', clean):
                        result["phone"] = clean
                        break

            # Extract email from visible text
            text = soup.get_text()
            emails = EMAIL_RE.findall(text)
            emails = [e for e in emails if not any(x in e.lower() for x in ["example", "test", "noreply", "support@"])]
            if emails:
                result["email"] = emails[0]

            if result["phone"]:
                return result  # found — stop trying more pages

        except Exception:
            continue

    return result


def _scrape_indiamart(company_name: str, location: str = "Mumbai") -> dict:
    """
    Search for a phone number in search result snippets.
    Uses SerpAPI if SERPAPI_KEY is set (reliable Google results, no throttling).
    Falls back to DuckDuckGo HTML snippet search.
    Returns {"phone": "", "email": ""}.
    """
    settings = get_settings()
    city = location.split()[0]
    query = f'"{company_name}" {city} phone contact'
    INDIAN_PHONE = re.compile(r'[6-9]\d{9}')

    # --- SerpAPI (preferred — real Google results, no throttling) ---
    if settings.serpapi_key:
        try:
            resp = httpx.get(
                settings.serpapi_url,
                params={
                    "q": query,
                    "api_key": settings.serpapi_key,
                    "engine": "google",
                    "num": 5,
                    "gl": "in",
                    "hl": "en",
                },
                timeout=15,
            )
            for r in resp.json().get("organic_results", []):
                # Check title and snippet for phone numbers
                text = f"{r.get('title', '')} {r.get('snippet', '')}"
                for p in INDIAN_PHONE.findall(text):
                    if p != "8888888888":
                        return {"phone": p, "email": ""}
        except Exception as e:
            logger.warning("serpapi_phone_search_failed", company=company_name, error=str(e))

    # --- DuckDuckGo HTML fallback ---
    try:
        encoded = query.replace(" ", "+")
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        resp = httpx.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=15,
            follow_redirects=True,
        )
        text = BeautifulSoup(resp.text, "lxml").get_text()
        for p in INDIAN_PHONE.findall(text):
            if p != "8888888888":
                return {"phone": p, "email": ""}
    except Exception as e:
        logger.warning("ddg_phone_search_failed", company=company_name, error=str(e))

    return {"phone": "", "email": ""}


def _scrape_justdial(company_name: str, location: str) -> dict:
    """Search JustDial for company contact. Returns {"phone": "", "email": ""}."""
    try:
        query = f"{company_name} {location}"
        encoded = query.replace(" ", "+")
        url = f"https://www.justdial.com/search?q={encoded}&city=Mumbai"

        resp = httpx.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-IN,en;q=0.9",
            },
            timeout=15,
            follow_redirects=True,
        )
        text = BeautifulSoup(resp.text, "lxml").get_text()

        INDIAN_PHONE = re.compile(r'(?:\+91[\s\-]?)?[6-9]\d{9}')
        raw_phones = INDIAN_PHONE.findall(text)

        # Clean and filter — drop masked numbers (8888888888) returned by JustDial
        for p in raw_phones:
            digits = re.sub(r'[^\d]', '', p)
            clean = digits[-10:] if len(digits) >= 10 else digits
            if re.match(r'[6-9]\d{9}$', clean) and clean != "8888888888":
                return {"phone": clean, "email": ""}

    except Exception as e:
        logger.warning("justdial_scrape_failed", company=company_name, error=str(e))

    return {"phone": "", "email": ""}


# ============================================================
# Company website scraper — generic contact extraction
# ============================================================

def scrape_company_website(url: str) -> dict:
    """
    Scrape a company website for contact details.
    Returns: {"emails": [...], "phones": [...], "contact_links": [...]}
    """
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text()

        emails = list(set(re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text)))
        phones = list(set(re.findall(r'(?:\+91[\s\-]?)?[6-9]\d{9}', text)))
        contact_links = [
            a.get("href") for a in soup.select("a")
            if a.get_text(strip=True).lower() in ["contact", "contact us", "about us", "about"]
        ]

        logger.info("company_scraped", url=url, emails=len(emails), phones=len(phones))
        return {"emails": emails[:5], "phones": phones[:5], "contact_links": contact_links[:3]}
    except Exception as e:
        logger.error("company_scrape_failed", url=url, error=str(e))
        return {"emails": [], "phones": [], "contact_links": []}
