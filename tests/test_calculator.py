"""
Tests for the calculator tool — financial and engineering math.
These must be precise. LLM never does these calculations.
Run: pytest tests/test_calculator.py -v
"""

import pytest
from tools.calculator import (
    calculate_margin,
    calculate_gst,
    calculate_discount,
    calculate_deal_value,
    estimate_freight,
    calculate_derating,
)


class TestPricingCalculations:

    def test_margin_above_pep(self):
        result = calculate_margin(selling_price=1876413, pep_price=1792641)
        assert result["margin_inr"] == 83772
        assert result["margin_pct"] == pytest.approx(4.67, abs=0.1)
        assert result["is_above_pep"] is True

    def test_margin_below_pep(self):
        result = calculate_margin(selling_price=1700000, pep_price=1792641)
        assert result["is_above_pep"] is False
        assert result["margin_pct"] < 0

    def test_gst_18_percent(self):
        result = calculate_gst(subtotal=1000000)
        assert result["gst_amount"] == 180000
        assert result["total_with_gst"] == 1180000

    def test_discount_10_percent(self):
        result = calculate_discount(list_price=1000000, discount_pct=10)
        assert result["discount_amount"] == 100000
        assert result["final_price"] == 900000

    def test_deal_value_complete(self):
        result = calculate_deal_value(
            unit_price=1876413,
            quantity=1,
            accessories_total=0,
            freight=35000,
            gst_rate=18,
        )
        assert result["product_total"] == 1876413
        assert result["gst_amount"] == pytest.approx(337754.34, abs=1)
        assert result["total_deal_value"] == pytest.approx(2249167.34, abs=1)

    def test_deal_value_multiple_units(self):
        result = calculate_deal_value(
            unit_price=500000,
            quantity=3,
            freight=50000,
        )
        assert result["product_total"] == 1500000
        assert result["total_deal_value"] > 1500000  # Must include GST + freight


class TestFreightEstimate:

    def test_mumbai_freight(self):
        freight = estimate_freight(to_location="Mumbai")
        assert freight == 25000  # Standard weight to Mumbai

    def test_pune_freight(self):
        freight = estimate_freight(to_location="Pune")
        assert freight == 20000

    def test_unknown_location_default(self):
        freight = estimate_freight(to_location="Kolhapur")
        assert freight == 35000  # Default for unknown Maharashtra


class TestDerating:

    def test_no_derating_at_sea_level(self):
        result = calculate_derating(rated_kva=250)
        assert result["derating_factor"] == 1.0
        assert result["derated_kva"] == 250

    def test_altitude_derating(self):
        result = calculate_derating(rated_kva=250, altitude_m=2000)
        # 2000m: (2000-1000)/300 * 3.5% = 11.67% derating
        assert result["derating_factor"] < 1.0
        assert result["needs_upsizing"] is True
        assert result["suggested_kva"] >= 250

    def test_temperature_derating(self):
        result = calculate_derating(rated_kva=250, ambient_temp_c=50)
        # 50°C: (50-40)/5 * 2% = 4% derating
        assert result["derating_factor"] < 1.0

    def test_combined_derating(self):
        result = calculate_derating(rated_kva=250, altitude_m=1500, ambient_temp_c=45)
        assert result["derating_factor"] < 0.96  # Both factors
