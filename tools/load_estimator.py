"""
Load Estimator — Pure Python, no external APIs.
Estimates kVA requirement from customer's equipment list.
Applies standard diversity factors used in Indian electrical design practice.

Used by Agent-RM when customer says "I have X ACs and Y motors" instead of quoting kVA.
"""

import structlog
from sqlalchemy import text

logger = structlog.get_logger()

# Fallback kVA ratings — used only if DB is unreachable
_FALLBACK_KVA_RATINGS = [10, 15, 20, 25, 40, 62.5, 82.5, 100, 125, 160, 200, 250, 320, 400, 500, 625, 750, 1000]


def _get_standard_kva_ratings() -> list[float]:
    """Fetch available kVA ratings from products table. Falls back to hardcoded list."""
    try:
        from database.connection import get_sync_engine
        engine = get_sync_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT kva_rating FROM products WHERE is_active = true ORDER BY kva_rating ASC"
            )).fetchall()
            if rows:
                return [float(r[0]) for r in rows]
    except Exception as e:
        logger.warning("kva_ratings_db_fetch_failed", error=str(e))
    return _FALLBACK_KVA_RATINGS

# Power factor assumptions by equipment type
EQUIPMENT_POWER_FACTORS = {
    "ac":           0.85,   # air conditioner (compressor motor)
    "lift":         0.80,   # elevator (induction motor, starting surge)
    "motor":        0.80,   # generic induction motor
    "pump":         0.82,   # water pump
    "computer":     0.95,   # UPS-fed IT load
    "light":        0.95,   # LED / fluorescent
    "welding":      0.60,   # arc welding
    "ups":          0.90,   # UPS system (fed load)
    "ats":          1.00,   # automatic transfer switch (no load itself)
    "server":       0.95,   # server room
    "heater":       1.00,   # resistive heater
    "fan":          0.85,   # industrial fan
    "crane":        0.75,   # overhead crane (intermittent)
    "generic":      0.85,   # unknown equipment
}

# Demand / diversity factors by equipment type (simultaneous use assumption)
DEMAND_FACTORS = {
    "ac":       0.75,   # not all ACs at full load simultaneously
    "lift":     0.60,   # lifts rarely all running together
    "motor":    0.70,
    "pump":     0.80,
    "computer": 0.90,
    "light":    0.90,
    "welding":  0.50,   # intermittent use
    "ups":      0.85,
    "ats":      1.00,
    "server":   0.90,
    "heater":   0.80,
    "fan":      0.85,
    "crane":    0.40,   # very intermittent
    "generic":  0.75,
}

# Starting current multiplier for motor loads (kVA demand during startup)
STARTING_MULTIPLIERS = {
    "ac":     2.5,
    "lift":   3.0,
    "motor":  3.0,
    "pump":   2.5,
    "fan":    2.0,
    "crane":  3.5,
}


def estimate_load_from_equipment(equipment_list: list[dict]) -> dict:
    """
    Estimate total DG set kVA requirement from an equipment list.

    Args:
        equipment_list: List of dicts, each with:
            - "type": equipment type (ac/lift/motor/pump/computer/light/etc.)
            - "quantity": number of units
            - "kw_each": rated power per unit in kW (or "kva_each" for apparent power)
            - "kva_each": (optional) apparent power if kW not known

    Example:
        [
            {"type": "ac", "quantity": 10, "kw_each": 2.5},
            {"type": "lift", "quantity": 2, "kva_each": 30},
            {"type": "light", "quantity": 50, "kw_each": 0.06},
        ]

    Returns:
        {
            "total_connected_kva": float,
            "total_demand_kva": float,      # after diversity factor
            "recommended_kva": float,       # suggested DG set rating
            "starting_kva": float,          # peak during motor start
            "breakdown": list[dict],        # per-equipment breakdown
            "design_margin_pct": int,       # headroom left
            "notes": str
        }
    """
    breakdown = []
    total_connected_kva = 0.0
    total_demand_kva = 0.0
    max_starting_kva = 0.0

    for item in equipment_list:
        eq_type = item.get("type", "generic").lower().strip()
        if eq_type not in EQUIPMENT_POWER_FACTORS:
            eq_type = "generic"

        qty = float(item.get("quantity", 1))
        pf = EQUIPMENT_POWER_FACTORS[eq_type]
        demand_factor = DEMAND_FACTORS[eq_type]

        # Determine kVA per unit
        if "kw_each" in item:
            kva_each = float(item["kw_each"]) / pf
        elif "kva_each" in item:
            kva_each = float(item["kva_each"])
        else:
            kva_each = 1.0 / pf  # assume 1 kW if not specified

        connected_kva = qty * kva_each
        demand_kva = connected_kva * demand_factor
        total_connected_kva += connected_kva
        total_demand_kva += demand_kva

        # Starting kVA (motors only)
        if eq_type in STARTING_MULTIPLIERS:
            # Largest single motor start added on top of running load
            largest_motor_kva = kva_each * STARTING_MULTIPLIERS[eq_type]
            max_starting_kva = max(max_starting_kva, largest_motor_kva)

        breakdown.append({
            "type": eq_type,
            "quantity": int(qty),
            "kva_each": round(kva_each, 2),
            "connected_kva": round(connected_kva, 2),
            "demand_kva": round(demand_kva, 2),
            "demand_factor": demand_factor,
            "power_factor": pf,
        })

    # Design with 20% headroom + starting current consideration
    design_kva = total_demand_kva * 1.20
    peak_starting_kva = total_demand_kva + max_starting_kva

    recommended_kva = suggest_kva_rating(max(design_kva, peak_starting_kva))
    margin_pct = int(((recommended_kva - total_demand_kva) / recommended_kva) * 100) if recommended_kva > 0 else 0

    logger.info(
        "load_estimated",
        connected_kva=round(total_connected_kva, 1),
        demand_kva=round(total_demand_kva, 1),
        recommended_kva=recommended_kva,
    )

    return {
        "total_connected_kva": round(total_connected_kva, 1),
        "total_demand_kva": round(total_demand_kva, 1),
        "design_kva_with_headroom": round(design_kva, 1),
        "peak_starting_kva": round(peak_starting_kva, 1),
        "recommended_kva": recommended_kva,
        "design_margin_pct": margin_pct,
        "breakdown": breakdown,
        "notes": (
            f"Connected load {total_connected_kva:.1f} kVA, demand load {total_demand_kva:.1f} kVA "
            f"(diversity applied). Recommended {recommended_kva} kVA DG set includes 20% headroom "
            f"and motor starting allowance."
        ),
    }


def suggest_kva_rating(calculated_kva: float) -> float:
    """Return next standard Pai Kane catalog kVA rating at or above calculated load."""
    ratings = _get_standard_kva_ratings()
    for rating in ratings:
        if rating >= calculated_kva:
            return float(rating)
    return ratings[-1] if ratings else calculated_kva
