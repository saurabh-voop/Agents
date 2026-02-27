"""
Fuel Calculator — Pure Python, no external APIs.
Calculates HSD fuel consumption, tank sizing, and runtime for DG sets.

Based on CPCB/manufacturer data: standard DG sets consume ~0.28 L/kWh at full load.
Derates with load percentage as per IS 10002 / manufacturer test data.

Used by Agent-RM to answer common customer questions about fuel cost and autonomy.
"""

import structlog
from sqlalchemy import text

logger = structlog.get_logger()

# Specific fuel consumption (SFC) at full load in L/kWh
# Industry standard: 0.28-0.32 L/kWh depending on engine make
SFC_AT_FULL_LOAD = 0.28

# Load-efficiency curve: consumption factor relative to full load
# At partial load, diesel engines are less efficient (higher L/kWh)
LOAD_EFFICIENCY_FACTORS = {
    25: 1.45,   # 25% load — very inefficient
    50: 1.15,   # 50% load — slightly inefficient
    75: 1.03,   # 75% load — near optimal
    100: 1.00,  # 100% load — rated efficiency
}

# Standard tank sizes available (litres)
STANDARD_TANK_SIZES = [100, 200, 300, 400, 500, 660, 900, 1100, 1500, 2000, 3000]


def _get_hsd_price() -> float:
    """
    Fetch current HSD (diesel) price from commodity_prices table.
    Falls back to settings value if DB has no data yet.
    """
    try:
        from database.connection import get_sync_engine
        from core.config import get_settings
        engine = get_sync_engine()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT price FROM commodity_prices WHERE indicator = 'diesel_india' LIMIT 1"
            )).fetchone()
            if row and row[0]:
                return float(row[0])
    except Exception as e:
        logger.warning("hsd_price_db_fetch_failed", error=str(e))
    # Fallback: settings value (set via HSD_PRICE_FALLBACK_INR in .env)
    try:
        from core.config import get_settings
        return get_settings().hsd_price_fallback_inr
    except Exception:
        return 90.0


def _interpolate_efficiency(load_pct: float) -> float:
    """Interpolate load efficiency factor for any load percentage."""
    load_pct = max(25.0, min(100.0, load_pct))
    breakpoints = sorted(LOAD_EFFICIENCY_FACTORS.keys())
    for i, bp in enumerate(breakpoints):
        if load_pct <= bp:
            if i == 0:
                return LOAD_EFFICIENCY_FACTORS[bp]
            lo, hi = breakpoints[i - 1], breakpoints[i]
            t = (load_pct - lo) / (hi - lo)
            return LOAD_EFFICIENCY_FACTORS[lo] + t * (LOAD_EFFICIENCY_FACTORS[hi] - LOAD_EFFICIENCY_FACTORS[lo])
    return LOAD_EFFICIENCY_FACTORS[100]


def calculate_fuel_consumption(kva: float, load_pct: float = 75.0) -> dict:
    """
    Calculate HSD fuel consumption for a running DG set.

    Args:
        kva: DG set rating in kVA
        load_pct: Operating load as percentage of rated kVA (25-100)

    Returns:
        {
            "litres_per_hour": float,
            "litres_per_day_8hr": float,
            "litres_per_month_8hr": float,
            "cost_per_hour_inr": float,
            "cost_per_day_inr": float,
            "load_pct": float,
            "kw_output": float
        }
    """
    load_pct = max(25.0, min(100.0, load_pct))
    pf = 0.8  # standard power factor for DG sets
    kw_output = kva * pf * (load_pct / 100.0)

    efficiency_factor = _interpolate_efficiency(load_pct)
    litres_per_hour = kw_output * SFC_AT_FULL_LOAD * efficiency_factor

    hsd_price = _get_hsd_price()
    cost_per_hour = litres_per_hour * hsd_price

    logger.info(
        "fuel_consumption_calculated",
        kva=kva, load_pct=load_pct, lph=round(litres_per_hour, 2),
    )

    return {
        "litres_per_hour": round(litres_per_hour, 2),
        "litres_per_day_8hr": round(litres_per_hour * 8, 1),
        "litres_per_month_8hr_25days": round(litres_per_hour * 8 * 25, 0),
        "cost_per_hour_inr": round(cost_per_hour, 0),
        "cost_per_day_8hr_inr": round(cost_per_hour * 8, 0),
        "cost_per_month_inr": round(cost_per_hour * 8 * 25, 0),
        "load_pct": load_pct,
        "kw_output": round(kw_output, 1),
        "hsd_price_per_litre": hsd_price,
        "note": f"{kva} kVA at {load_pct}% load ({kw_output:.1f} kW output). "
                f"Fuel cost approx ₹{cost_per_hour * 8:,.0f}/day (8 hrs).",
    }


def calculate_tank_size(kva: float, runtime_hours: float, load_pct: float = 75.0) -> dict:
    """
    Recommend appropriate fuel tank size for required autonomy.

    Args:
        kva: DG set rating in kVA
        runtime_hours: Required autonomy in hours
        load_pct: Expected operating load percentage (default 75%)

    Returns:
        {
            "fuel_required_litres": float,
            "recommended_tank_litres": int,
            "actual_runtime_hours": float,    # with recommended tank
            "standard_tank_available": bool
        }
    """
    consumption = calculate_fuel_consumption(kva, load_pct)
    lph = consumption["litres_per_hour"]

    fuel_required = lph * runtime_hours
    # Add 10% safety margin + 5% unusable sump
    fuel_with_margin = fuel_required * 1.15

    # Find next standard tank size
    recommended_tank = None
    for tank in STANDARD_TANK_SIZES:
        if tank >= fuel_with_margin:
            recommended_tank = tank
            break
    if not recommended_tank:
        recommended_tank = int(fuel_with_margin * 1.1)  # custom size needed

    actual_runtime = (recommended_tank * 0.95) / lph  # 95% usable volume

    return {
        "fuel_required_litres": round(fuel_required, 0),
        "fuel_with_safety_margin_litres": round(fuel_with_margin, 0),
        "recommended_tank_litres": recommended_tank,
        "actual_runtime_hours": round(actual_runtime, 1),
        "standard_tank_available": recommended_tank in STANDARD_TANK_SIZES,
        "litres_per_hour": lph,
        "note": (
            f"For {runtime_hours}h autonomy at {load_pct}% load: need {fuel_required:.0f}L. "
            f"Recommend {recommended_tank}L tank → {actual_runtime:.1f}h actual runtime."
        ),
    }


def calculate_runtime(kva: float, tank_litres: float, load_pct: float = 75.0) -> dict:
    """
    Calculate how long a given tank will last.

    Args:
        kva: DG set rating in kVA
        tank_litres: Fuel tank capacity in litres
        load_pct: Operating load percentage

    Returns:
        {
            "runtime_hours": float,
            "runtime_days_8hr": float,
            "usable_fuel_litres": float
        }
    """
    consumption = calculate_fuel_consumption(kva, load_pct)
    lph = consumption["litres_per_hour"]
    usable = tank_litres * 0.95  # 5% unusable sump

    runtime_hours = usable / lph if lph > 0 else 0

    return {
        "runtime_hours": round(runtime_hours, 1),
        "runtime_days_8hr": round(runtime_hours / 8, 1),
        "usable_fuel_litres": round(usable, 0),
        "litres_per_hour": lph,
        "tank_litres": tank_litres,
        "load_pct": load_pct,
        "note": (
            f"{tank_litres}L tank → {runtime_hours:.1f}h runtime at {load_pct}% load "
            f"({runtime_hours / 8:.1f} working days of 8h)."
        ),
    }
