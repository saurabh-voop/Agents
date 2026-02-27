"""
Commodity price monitoring tool.
Tracks copper, steel, forex — key inputs affecting DG set pricing.
Agent-GM uses this for deal recommendations and price list validity.
"""

import httpx
import structlog
from datetime import datetime
from sqlalchemy import text
from database.connection import get_sync_engine
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# Baseline prices as of price list date (Rel.3 01/01/2026)
BASELINES = {
    "copper_mcx": 850.0,     # INR per kg
    "copper_lme": 8500.0,    # USD per tonne
    "steel_india": 55000.0,  # INR per tonne
    "inr_usd": 83.50,
    "inr_eur": 92.00,
    "diesel_india": 89.96,   # INR per litre (Mumbai)
}


def fetch_commodity_prices() -> dict:
    """
    Fetch latest commodity prices from API.
    Returns dict with current prices and change from baseline.
    """
    if not settings.commodity_api_key:
        logger.warning("commodity_api_key_not_set, using baselines")
        return {k: {"current": v, "baseline": v, "change_pct": 0.0} for k, v in BASELINES.items()}

    try:
        url = f"{settings.commodity_api_url}/latest"
        params = {
            "access_key": settings.commodity_api_key,
            "symbols": "XCU,XFE",  # Copper, Iron/Steel
            "base": "USD",
        }
        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        rates = data.get("data", {}).get("rates", {})
        # Convert API response to our format
        prices = {}
        for indicator, baseline in BASELINES.items():
            current = baseline  # Default to baseline if API doesn't return this
            if indicator == "copper_lme" and "XCU" in rates:
                current = 1 / rates["XCU"]  # API returns inverse
            
            change_pct = round(((current - baseline) / baseline) * 100, 2)
            prices[indicator] = {
                "current": current,
                "baseline": baseline,
                "change_pct": change_pct,
            }

        logger.info("commodities_fetched", prices=len(prices))
        return prices
    except Exception as e:
        logger.error("commodity_fetch_failed", error=str(e))
        return {k: {"current": v, "baseline": v, "change_pct": 0.0} for k, v in BASELINES.items()}


def store_commodity_prices(prices: dict) -> None:
    """Store fetched prices in PostgreSQL for Agent-GM to read."""
    engine = get_sync_engine()
    now = datetime.utcnow()

    for indicator, data in prices.items():
        query = text("""
            INSERT INTO commodity_prices 
            (indicator, price, baseline_price, change_from_baseline_pct, fetched_at)
            VALUES (:indicator, :price, :baseline, :change_pct, :fetched_at)
            ON CONFLICT (indicator) DO UPDATE SET 
                price = :price, 
                change_from_baseline_pct = :change_pct,
                fetched_at = :fetched_at,
                updated_at = NOW()
        """)
        with engine.connect() as conn:
            conn.execute(query, {
                "indicator": indicator,
                "price": data["current"],
                "baseline": data["baseline"],
                "change_pct": data["change_pct"],
                "fetched_at": now,
            })
            conn.commit()


def get_commodity_snapshot() -> dict:
    """Read latest commodity snapshot from DB for deal recommendations."""
    engine = get_sync_engine()
    query = text("SELECT indicator, price, baseline_price, change_from_baseline_pct FROM commodity_prices")
    
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
    
    snapshot = {}
    max_change = 0
    for row in rows:
        r = dict(row._mapping)
        snapshot[r["indicator"]] = r
        max_change = max(max_change, abs(r["change_from_baseline_pct"] or 0))

    snapshot["overall_impact"] = "significant" if max_change > 5 else "minor" if max_change > 2 else "none"
    return snapshot
