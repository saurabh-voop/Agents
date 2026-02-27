"""
Exchange Rate Tool — USD/INR live rate from free public API.
No API key required. Uses open.er-api.com (free tier, 1500 req/month).

Used by Agent-GM to flag import cost impact when INR has weakened.
Cummins/Perkins engines and Leroy Somer alternators are partly USD-priced.
"""

import time
import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

logger = structlog.get_logger()

# Free exchange rate API — no key needed
ER_API_URL = "https://open.er-api.com/v6/latest/USD"

# Cache: avoid hitting API on every agent call
_rate_cache = {"rate": None, "fetched_at": 0, "ttl_seconds": 3600}


def _get_baseline_rate() -> float:
    """
    Baseline USD/INR at time of last price list revision.
    Read from settings (USD_INR_BASELINE in .env) — update when price list changes.
    Also checks commodity_prices DB for the stored inr_usd indicator.
    """
    # Try DB first (stored from last commodity fetch)
    try:
        from database.connection import get_sync_engine
        from sqlalchemy import text as sqla_text
        engine = get_sync_engine()
        with engine.connect() as conn:
            row = conn.execute(sqla_text(
                "SELECT baseline_price FROM commodity_prices WHERE indicator = 'inr_usd' LIMIT 1"
            )).fetchone()
            if row and row[0]:
                return float(row[0])
    except Exception:
        pass
    # Fallback to .env setting
    try:
        from core.config import get_settings
        return get_settings().usd_inr_baseline
    except Exception:
        return 83.5

def _get_import_component_pct() -> dict:
    """
    USD-denominated component % of PEP by engine make.
    Reads from agent_gm.json config so it can be updated without code changes.
    """
    try:
        import json, os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg_path = os.path.join(base, "config", "agent_configs", "agent_gm.json")
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg.get("import_component_pct_by_engine", {})
    except Exception:
        pass
    # Fallback defaults
    return {
        "cummins":  0.35,
        "perkins":  0.38,
        "kirloskar": 0.15,
        "mahindra": 0.12,
        "default":  0.25,
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
def _fetch_rate() -> float:
    """Fetch current USD/INR rate from public API."""
    response = httpx.get(ER_API_URL, timeout=10)
    response.raise_for_status()
    data = response.json()
    rate = data["rates"]["INR"]
    logger.info("exchange_rate_fetched", usd_inr=rate)
    return rate


def get_usd_inr_rate() -> dict:
    """
    Get current USD/INR exchange rate with caching.

    Returns:
        {
            "current_rate": float,
            "baseline_rate": float,
            "change_pct": float,        # positive = INR weakened (bad for imports)
            "direction": str,           # "weakened" | "strengthened" | "stable"
            "impact": str,              # "none" | "moderate" | "significant"
            "cached": bool
        }
    """
    now = time.time()
    if _rate_cache["rate"] and (now - _rate_cache["fetched_at"]) < _rate_cache["ttl_seconds"]:
        current_rate = _rate_cache["rate"]
        cached = True
    else:
        try:
            current_rate = _fetch_rate()
            _rate_cache["rate"] = current_rate
            _rate_cache["fetched_at"] = now
            cached = False
        except Exception as e:
            logger.warning("exchange_rate_fetch_failed", error=str(e))
            # Fall back to baseline if API fails
            current_rate = _get_baseline_rate()
            cached = True

    change_pct = round(((current_rate - _get_baseline_rate()) / _get_baseline_rate()) * 100, 2)

    if abs(change_pct) < 1.0:
        direction = "stable"
        impact = "none"
    elif change_pct > 0:
        direction = "weakened"          # INR weakened = imports cost more
        impact = "significant" if change_pct > 3 else "moderate"
    else:
        direction = "strengthened"      # INR strengthened = imports cheaper
        impact = "none"

    return {
        "current_rate": round(current_rate, 2),
        "baseline_rate": _get_baseline_rate(),
        "change_pct": change_pct,
        "direction": direction,
        "impact": impact,
        "cached": cached,
        "note": (
            f"USD/INR: {current_rate:.2f} (baseline {_get_baseline_rate()}). "
            f"INR has {direction} by {abs(change_pct):.1f}% — "
            f"{'import costs higher, consider price adjustment' if impact != 'none' else 'no material impact on PEP'}."
        ),
    }


def calculate_import_cost_impact(
    pep_price: float,
    engine_make: str = "cummins",
) -> dict:
    """
    Calculate the INR impact of exchange rate movement on PEP price.

    Args:
        pep_price: Current PEP price in INR
        engine_make: Engine manufacturer (affects import exposure %)

    Returns:
        {
            "pep_price_inr": float,
            "import_exposure_pct": float,
            "usd_denominated_inr": float,
            "rate_change_pct": float,
            "pep_adjustment_inr": float,     # how much PEP has effectively changed
            "adjusted_pep_inr": float,        # corrected PEP if rate adjusted
            "recommendation": str
        }
    """
    rate_info = get_usd_inr_rate()
    import_pcts = _get_import_component_pct()
    engine_key = engine_make.lower() if engine_make.lower() in import_pcts else "default"
    import_pct = import_pcts.get(engine_key, 0.25)

    usd_denominated = pep_price * import_pct
    rate_change_pct = rate_info["change_pct"] / 100.0
    pep_adjustment = usd_denominated * rate_change_pct
    adjusted_pep = pep_price + pep_adjustment

    if abs(pep_adjustment) < 5000:
        recommendation = "Exchange rate movement is within tolerance. No PEP adjustment needed."
    elif pep_adjustment > 0:
        recommendation = (
            f"INR weakened — import components now cost ₹{pep_adjustment:,.0f} more. "
            f"Effective PEP is ₹{adjusted_pep:,.0f}. Consider passing partial cost to customer "
            f"or flag for CMD review if margin drops below threshold."
        )
    else:
        recommendation = (
            f"INR strengthened — import savings of ₹{abs(pep_adjustment):,.0f}. "
            f"Effective PEP is ₹{adjusted_pep:,.0f}. Additional margin available."
        )

    logger.info(
        "import_cost_impact_calculated",
        engine=engine_make,
        pep=pep_price,
        adjustment=pep_adjustment,
    )

    return {
        "pep_price_inr": pep_price,
        "engine_make": engine_make,
        "import_exposure_pct": round(import_pct * 100, 0),
        "usd_denominated_component_inr": round(usd_denominated, 0),
        "current_usd_inr": rate_info["current_rate"],
        "baseline_usd_inr": _get_baseline_rate(),
        "rate_change_pct": rate_info["change_pct"],
        "pep_adjustment_inr": round(pep_adjustment, 0),
        "adjusted_pep_inr": round(adjusted_pep, 0),
        "recommendation": recommendation,
    }
