"""
Company Lookup — MCA21 / ROC verification.
Checks company registration status, type, age, and basic compliance signals.

Uses MCA21 public API (no key required) with graceful fallback.
Used by Agent-GM to assess credit risk and verify company legitimacy before offering terms.
"""

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger()

# MCA21 public search endpoint (no API key required)
MCA_SEARCH_URL = "https://efiling.mca.gov.in/NameAvailability/rest/nameAvailability/reserveName"

# Fallback: IndiaFilings / Zaubacorp public data
ZAUBACORP_URL = "https://www.zaubacorp.com/company-search"


def lookup_company_mca(company_name: str) -> dict:
    """
    Look up company registration details from MCA21 database.

    Returns risk assessment for Agent-GM to use in payment terms decisions.

    Args:
        company_name: Company name as given by customer

    Returns:
        {
            "found": bool,
            "company_name": str,
            "cin": str | None,
            "registration_status": str,   # active / strike_off / dormant / unknown
            "company_type": str,          # pvt_ltd / public / llp / proprietorship / unknown
            "incorporation_year": int | None,
            "company_age_years": int | None,
            "paid_up_capital_cr": float | None,
            "risk_level": str,            # low / medium / high
            "risk_factors": list[str],
            "credit_recommendation": str
        }
    """
    result = _try_mca_lookup(company_name)
    if not result.get("found"):
        result = _heuristic_assessment(company_name)

    # Compute risk level
    risk_factors = []
    if result.get("company_age_years") is not None and result["company_age_years"] < 2:
        risk_factors.append("Company less than 2 years old")
    if result.get("registration_status") not in ("active", "unknown"):
        risk_factors.append(f"Non-active status: {result.get('registration_status')}")
    if result.get("paid_up_capital_cr") is not None and result["paid_up_capital_cr"] < 0.1:
        risk_factors.append("Low paid-up capital (<₹10 lakh)")
    if result.get("company_type") in ("proprietorship", "unknown"):
        risk_factors.append("Unregistered / proprietorship entity")

    if len(risk_factors) == 0:
        risk_level = "low"
        credit_recommendation = "Standard terms applicable. 50% advance, 50% on delivery."
    elif len(risk_factors) == 1:
        risk_level = "medium"
        credit_recommendation = "Moderate risk. 75% advance, 25% on delivery recommended."
    else:
        risk_level = "high"
        credit_recommendation = "Higher risk profile. 100% advance payment recommended."

    result["risk_level"] = risk_level
    result["risk_factors"] = risk_factors
    result["credit_recommendation"] = credit_recommendation

    logger.info(
        "company_lookup_completed",
        company=company_name,
        found=result.get("found"),
        risk=risk_level,
    )
    return result


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=3))
def _try_mca_lookup(company_name: str) -> dict:
    """Attempt MCA21 public database lookup."""
    try:
        # MCA21 name check endpoint — public, no auth needed
        response = httpx.get(
            "https://efiling.mca.gov.in/NameAvailability/rest/nameAvailability/reserveName",
            params={"companyName": company_name, "companyType": ""},
            timeout=8,
            headers={"Accept": "application/json"},
        )
        if response.status_code == 200:
            data = response.json()
            # Parse MCA response structure
            if data.get("companyExists"):
                company_data = data.get("companyDetails", {})
                from datetime import datetime
                inc_date = company_data.get("dateOfIncorporation", "")
                inc_year = None
                age_years = None
                if inc_date:
                    try:
                        inc_dt = datetime.strptime(inc_date[:10], "%Y-%m-%d")
                        inc_year = inc_dt.year
                        age_years = (datetime.now() - inc_dt).days // 365
                    except Exception:
                        pass

                return {
                    "found": True,
                    "company_name": company_data.get("companyName", company_name),
                    "cin": company_data.get("cin"),
                    "registration_status": company_data.get("companyStatus", "unknown").lower().replace(" ", "_"),
                    "company_type": _normalize_company_type(company_data.get("companyType", "")),
                    "incorporation_year": inc_year,
                    "company_age_years": age_years,
                    "paid_up_capital_cr": None,
                    "source": "mca21",
                }
        return {"found": False}
    except Exception as e:
        logger.warning("mca_lookup_failed", error=str(e))
        return {"found": False}


def _normalize_company_type(raw: str) -> str:
    raw = raw.lower()
    if "private" in raw:
        return "pvt_ltd"
    if "public" in raw:
        return "public"
    if "llp" in raw or "limited liability" in raw:
        return "llp"
    if "one person" in raw or "opc" in raw:
        return "opc"
    return "unknown"


def _heuristic_assessment(company_name: str) -> dict:
    """
    Fallback: assess company type from name patterns when MCA lookup fails.
    Gives Agent-GM a basic risk signal without external data.
    """
    name_lower = company_name.lower()

    # Detect company type from name suffix
    if any(x in name_lower for x in ["pvt ltd", "private limited", "pvt. ltd"]):
        company_type = "pvt_ltd"
        status = "active"  # assume active if name has proper legal suffix
    elif any(x in name_lower for x in ["ltd", "limited", "public"]):
        company_type = "public"
        status = "active"
    elif "llp" in name_lower:
        company_type = "llp"
        status = "active"
    elif any(x in name_lower for x in ["builders", "developers", "infra", "construction", "projects"]):
        company_type = "unknown"
        status = "unknown"
    else:
        company_type = "unknown"
        status = "unknown"

    return {
        "found": False,
        "company_name": company_name,
        "cin": None,
        "registration_status": status,
        "company_type": company_type,
        "incorporation_year": None,
        "company_age_years": None,
        "paid_up_capital_cr": None,
        "source": "heuristic",
        "note": "MCA lookup unavailable — assessment based on company name pattern only.",
    }
