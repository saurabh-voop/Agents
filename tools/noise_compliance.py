"""
CPCB-IV+ Noise Compliance Tool — Pure Python, no external APIs.
Based on CPCB Environment (Protection) Amendment Rules 2002 and
CPCB-IV+ DG set emission norms for India.

Used by Agent-RM to validate enclosure requirements and site suitability.
"""

import structlog

logger = structlog.get_logger()

# CPCB ambient noise limits (dB(A)) — Day 6AM-10PM / Night 10PM-6AM
AMBIENT_NOISE_LIMITS = {
    "industrial":   {"day": 75, "night": 70},
    "commercial":   {"day": 65, "night": 55},
    "residential":  {"day": 55, "night": 45},
    "silence":      {"day": 50, "night": 40},   # hospital / court / school
}

# CPCB-IV+ DG set noise emission limits at 1 metre (dB(A)) by kVA
# Source: CPCB DG set guidelines amended 2018
DGSET_NOISE_AT_1M = {
    10:   86, 15:  87, 20:  88, 25:  88,
    40:   90, 62.5: 91, 82.5: 92, 100: 93,
    125:  94, 160: 95, 200: 96, 250: 96,
    320:  97, 400: 97, 500: 98, 625: 98,
    750:  99, 1000: 100, 1500: 101, 2000: 102,
}

# Noise attenuation by enclosure type (dB(A) reduction)
ENCLOSURE_ATTENUATION = {
    "open":             0,
    "weather_proof":    5,
    "acoustic":        25,   # standard acoustic — CPCB-IV+ compliant
    "super_silent":    35,   # hospital / silence zones
}


def _find_noise_at_1m(kva: float) -> float:
    """Interpolate DG set noise emission at 1 metre for any kVA."""
    ratings = sorted(DGSET_NOISE_AT_1M.keys())
    for i, r in enumerate(ratings):
        if kva <= r:
            if i == 0:
                return DGSET_NOISE_AT_1M[r]
            lo, hi = ratings[i - 1], ratings[i]
            t = (kva - lo) / (hi - lo)
            return DGSET_NOISE_AT_1M[lo] + t * (DGSET_NOISE_AT_1M[hi] - DGSET_NOISE_AT_1M[lo])
    return DGSET_NOISE_AT_1M[ratings[-1]]


def _attenuate_at_distance(db_at_1m: float, distance_m: float) -> float:
    """Sound pressure level at a given distance using inverse square law."""
    if distance_m <= 0:
        distance_m = 1.0
    import math
    return round(db_at_1m - 20 * math.log10(distance_m), 1)


def check_noise_compliance(kva: float, zone_type: str, distance_m: float = 1.0) -> dict:
    """
    Check CPCB-IV+ noise compliance for a DG set at given distance.

    Args:
        kva: DG set rating in kVA
        zone_type: 'industrial' | 'commercial' | 'residential' | 'silence'
        distance_m: Distance from DG set boundary to nearest sensitive receptor (metres)

    Returns:
        {
            "compliant": bool,
            "noise_at_boundary_db": float,
            "limit_db": float,
            "excess_db": float,      # 0 if compliant
            "enclosure_required": str,
            "zone_type": str,
            "assessment": str
        }
    """
    zone_type = zone_type.lower().strip()
    if zone_type not in AMBIENT_NOISE_LIMITS:
        zone_type = "residential"  # safe default

    limits = AMBIENT_NOISE_LIMITS[zone_type]
    day_limit = limits["day"]

    raw_db_at_1m = _find_noise_at_1m(kva)

    # Standard acoustic enclosure reduces by 25 dB (CPCB-IV+ standard)
    with_acoustic_db = raw_db_at_1m - ENCLOSURE_ATTENUATION["acoustic"]
    db_at_boundary = _attenuate_at_distance(with_acoustic_db, distance_m)

    compliant = db_at_boundary <= day_limit
    excess = max(0.0, round(db_at_boundary - day_limit, 1))

    # Determine minimum enclosure needed
    if zone_type == "silence":
        enclosure_required = "super_silent"
    elif zone_type == "residential" and distance_m < 5:
        enclosure_required = "super_silent"
    else:
        enclosure_required = "acoustic"

    logger.info(
        "noise_compliance_checked",
        kva=kva, zone=zone_type, distance_m=distance_m,
        db_at_boundary=db_at_boundary, limit=day_limit, compliant=compliant,
    )

    return {
        "compliant": compliant,
        "noise_at_boundary_db": db_at_boundary,
        "limit_db": day_limit,
        "excess_db": excess,
        "raw_emission_db_at_1m": round(raw_db_at_1m, 1),
        "enclosure_required": enclosure_required,
        "zone_type": zone_type,
        "assessment": (
            f"Compliant — {db_at_boundary} dB(A) at {distance_m}m boundary, limit {day_limit} dB(A)."
            if compliant else
            f"Non-compliant — {db_at_boundary} dB(A) exceeds {day_limit} dB(A) limit by {excess} dB. "
            f"Recommend {enclosure_required.replace('_', ' ')} enclosure or increase setback distance."
        ),
    }


def get_enclosure_recommendation(kva: float, zone_type: str, distance_m: float = 3.0) -> dict:
    """
    Recommend the correct enclosure type for a site.

    Returns:
        {
            "recommended_enclosure": str,
            "noise_reduction_db": int,
            "cpcb_iv_compliant": bool,
            "notes": str
        }
    """
    zone_type = zone_type.lower().strip()
    if zone_type not in AMBIENT_NOISE_LIMITS:
        zone_type = "residential"

    if zone_type == "silence":
        enc = "super_silent"
        notes = "Hospital / court / school zone — super silent enclosure mandatory. 35 dB(A) attenuation."
    elif zone_type == "residential":
        if distance_m >= 10:
            enc = "acoustic"
            notes = f"Residential zone, {distance_m}m setback. Standard acoustic enclosure sufficient."
        else:
            enc = "super_silent"
            notes = f"Residential zone, only {distance_m}m setback. Super silent enclosure required."
    elif zone_type == "commercial":
        enc = "acoustic"
        notes = "Commercial zone — standard CPCB-IV+ acoustic enclosure sufficient."
    else:  # industrial
        enc = "acoustic"
        notes = "Industrial zone — standard acoustic enclosure. Verify site boundary distance."

    return {
        "recommended_enclosure": enc,
        "noise_reduction_db": ENCLOSURE_ATTENUATION[enc],
        "cpcb_iv_compliant": enc in ("acoustic", "super_silent"),
        "zone_type": zone_type,
        "notes": notes,
    }
