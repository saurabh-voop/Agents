"""
Calculator tool — precise financial and engineering math.
NEVER let the LLM do these calculations. LLMs hallucinate on numbers.
"""

from typing import Optional


# ============================================================
# Pricing Calculations
# ============================================================

def calculate_margin(selling_price: float, pep_price: float) -> dict:
    """Calculate margin above PEP floor."""
    margin = selling_price - pep_price
    margin_pct = (margin / pep_price * 100) if pep_price > 0 else 0
    return {
        "margin_inr": round(margin, 2),
        "margin_pct": round(margin_pct, 2),
        "is_above_pep": selling_price >= pep_price,
    }


def calculate_gst(subtotal: float, gst_rate: float = 18.0) -> dict:
    """Calculate GST on a subtotal."""
    gst_amount = subtotal * (gst_rate / 100)
    total = subtotal + gst_amount
    return {
        "subtotal": round(subtotal, 2),
        "gst_rate": gst_rate,
        "gst_amount": round(gst_amount, 2),
        "total_with_gst": round(total, 2),
    }


def calculate_discount(
    list_price: float,
    discount_pct: float,
) -> dict:
    """Calculate discounted price and validate against bands."""
    discount_amount = list_price * (discount_pct / 100)
    final_price = list_price - discount_amount
    return {
        "list_price": round(list_price, 2),
        "discount_pct": round(discount_pct, 2),
        "discount_amount": round(discount_amount, 2),
        "final_price": round(final_price, 2),
    }


def calculate_deal_value(
    unit_price: float,
    quantity: int = 1,
    accessories_total: float = 0,
    freight: float = 0,
    gst_rate: float = 18.0,
) -> dict:
    """Calculate complete deal value with GST and freight."""
    product_total = unit_price * quantity
    subtotal = product_total + accessories_total
    gst = subtotal * (gst_rate / 100)
    total = subtotal + gst + freight
    return {
        "unit_price": round(unit_price, 2),
        "quantity": quantity,
        "product_total": round(product_total, 2),
        "accessories_total": round(accessories_total, 2),
        "subtotal": round(subtotal, 2),
        "gst_amount": round(gst, 2),
        "freight": round(freight, 2),
        "total_deal_value": round(total, 2),
    }


def estimate_freight(
    from_location: str = "Goa",
    to_location: str = "Mumbai",
    weight_category: str = "standard",
) -> float:
    """
    Estimate freight cost based on route and weight.
    Rough estimates — Agent-GM can override.
    """
    # Freight matrix (INR): from Goa factory to major destinations
    freight_matrix = {
        "Mumbai": {"light": 15000, "standard": 25000, "heavy": 45000},
        "Pune": {"light": 12000, "standard": 20000, "heavy": 38000},
        "Nashik": {"light": 18000, "standard": 30000, "heavy": 50000},
        "Nagpur": {"light": 25000, "standard": 40000, "heavy": 65000},
        "Aurangabad": {"light": 20000, "standard": 35000, "heavy": 55000},
    }

    # Find closest match
    for city, rates in freight_matrix.items():
        if city.lower() in to_location.lower():
            return float(rates.get(weight_category, rates["standard"]))

    # Default for unknown locations in Maharashtra
    return 35000.0


# ============================================================
# Engineering Calculations
# ============================================================

def calculate_derating(
    rated_kva: float,
    altitude_m: Optional[int] = None,
    ambient_temp_c: Optional[int] = None,
) -> dict:
    """
    Calculate engine derating for altitude and temperature.
    Standard conditions: sea level (0m), 40°C ambient.
    Derating: 3.5% per 300m above 1000m, 2% per 5°C above 40°C.
    """
    derating_factor = 1.0
    reasons = []

    if altitude_m and altitude_m > 1000:
        altitude_derating = ((altitude_m - 1000) / 300) * 0.035
        derating_factor -= altitude_derating
        reasons.append(f"Altitude {altitude_m}m: -{altitude_derating*100:.1f}%")

    if ambient_temp_c and ambient_temp_c > 40:
        temp_derating = ((ambient_temp_c - 40) / 5) * 0.02
        derating_factor -= temp_derating
        reasons.append(f"Ambient {ambient_temp_c}°C: -{temp_derating*100:.1f}%")

    derating_factor = max(derating_factor, 0.5)  # Never derate below 50%
    derated_kva = rated_kva * derating_factor

    return {
        "rated_kva": rated_kva,
        "derating_factor": round(derating_factor, 4),
        "derated_kva": round(derated_kva, 1),
        "needs_upsizing": derating_factor < 0.9,
        "suggested_kva": _next_standard_kva(derated_kva) if derating_factor < 0.9 else rated_kva,
        "reasons": reasons,
    }


def _next_standard_kva(required_kva: float) -> float:
    """Find the next standard kVA rating above the required value."""
    standard_ratings = [
        5, 7.5, 10, 15, 20, 25, 30, 45, 62.5, 75, 82.5, 100, 125, 160,
        180, 200, 250, 320, 380, 400, 500, 625, 750, 800, 1010, 1250, 1500, 2000,
    ]
    for rating in standard_ratings:
        if rating >= required_kva:
            return float(rating)
    return 2000.0
