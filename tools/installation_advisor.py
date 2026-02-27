"""
Installation Advisor — Pure Python, no external APIs.
Generates site requirement checklists, plinth dimensions, ventilation specs,
and earthing requirements for DG set installations.

Based on IS 10002, IS 3043, CPWD specifications, and manufacturer guidelines.
Used by Agent-RM to advise customers on site preparation requirements.
"""

import structlog

logger = structlog.get_logger()

# Physical dimensions by kVA (L x W x H in mm) — approximate for planning purposes
# Includes acoustic enclosure + service clearance. Actual dims vary by make/model.
DG_SET_DIMENSIONS = {
    10:    {"L": 1400, "W": 700,  "H": 1200},
    15:    {"L": 1600, "W": 750,  "H": 1250},
    20:    {"L": 1700, "W": 800,  "H": 1300},
    25:    {"L": 1800, "W": 850,  "H": 1350},
    40:    {"L": 2000, "W": 900,  "H": 1400},
    62.5:  {"L": 2400, "W": 1000, "H": 1500},
    82.5:  {"L": 2600, "W": 1050, "H": 1550},
    100:   {"L": 2800, "W": 1100, "H": 1600},
    125:   {"L": 3000, "W": 1150, "H": 1650},
    160:   {"L": 3200, "W": 1200, "H": 1700},
    200:   {"L": 3500, "W": 1250, "H": 1750},
    250:   {"L": 3800, "W": 1300, "H": 1800},
    320:   {"L": 4000, "W": 1400, "H": 1900},
    400:   {"L": 4500, "W": 1500, "H": 2000},
    500:   {"L": 5000, "W": 1600, "H": 2100},
    625:   {"L": 5500, "W": 1700, "H": 2200},
    750:   {"L": 6000, "W": 1800, "H": 2300},
    1000:  {"L": 7000, "W": 2000, "H": 2500},
}

# Fuel consumption for sizing fuel room ventilation
FUEL_LPH_AT_75PCT = {
    25: 5.3, 40: 8.5, 62.5: 13.3, 82.5: 17.5, 100: 21.2,
    125: 26.5, 160: 34.0, 200: 42.4, 250: 53.0, 320: 67.8, 400: 84.8,
    500: 106.0, 625: 132.5, 750: 159.0, 1000: 212.0,
}


def _find_nearest_dims(kva: float) -> dict:
    """Find nearest dimension spec for given kVA."""
    ratings = sorted(DG_SET_DIMENSIONS.keys())
    for r in ratings:
        if kva <= r:
            return DG_SET_DIMENSIONS[r]
    return DG_SET_DIMENSIONS[ratings[-1]]


def get_plinth_dimensions(kva: float) -> dict:
    """
    Return recommended plinth/base dimensions for DG set installation.

    Plinth should extend 300mm beyond DG set on all sides.
    Minimum 150mm raised from floor to protect from flooding.
    """
    dims = _find_nearest_dims(kva)
    plinth_L = dims["L"] + 600  # +300mm each side
    plinth_W = dims["W"] + 600
    plinth_H = 150              # minimum 150mm height

    return {
        "dg_set_L_mm": dims["L"],
        "dg_set_W_mm": dims["W"],
        "dg_set_H_mm": dims["H"],
        "plinth_L_mm": plinth_L,
        "plinth_W_mm": plinth_W,
        "plinth_height_mm": plinth_H,
        "room_L_mm": dims["L"] + 2000,   # 1m clearance each side for maintenance
        "room_W_mm": dims["W"] + 2000,
        "room_H_mm": dims["H"] + 600,    # 600mm above DG for hot air exhaust
        "note": (
            f"DG set envelope: {dims['L']}×{dims['W']}×{dims['H']}mm. "
            f"Plinth: {plinth_L}×{plinth_W}×150mm (RCC M20). "
            f"Minimum room: {dims['L'] + 2000}×{dims['W'] + 2000}×{dims['H'] + 600}mm."
        ),
    }


def get_ventilation_requirements(kva: float) -> dict:
    """
    Calculate ventilation requirements for DG room.

    Based on: heat dissipation = ~10-15% of rated kW output.
    Fresh air required to keep room temperature ≤ 40°C.
    """
    pf = 0.8
    kw = kva * pf
    heat_dissipated_kw = kw * 0.12  # 12% of output as heat in room

    # Air volume required: Q = Heat / (rho × Cp × ΔT)
    # rho=1.2 kg/m3, Cp=1.005 kJ/kg.K, ΔT=10°C
    airflow_m3_per_sec = heat_dissipated_kw / (1.2 * 1.005 * 10)
    airflow_cfm = airflow_m3_per_sec * 2118.88

    dims = _find_nearest_dims(kva)
    # Inlet louver area: velocity ≤ 2 m/s
    inlet_area_m2 = airflow_m3_per_sec / 2.0
    outlet_area_m2 = inlet_area_m2 * 1.2  # outlet 20% larger

    return {
        "airflow_m3_per_hour": round(airflow_m3_per_sec * 3600, 0),
        "airflow_cfm": round(airflow_cfm, 0),
        "inlet_louver_area_m2": round(inlet_area_m2, 2),
        "outlet_louver_area_m2": round(outlet_area_m2, 2),
        "inlet_louver_size_mm": f"{int(inlet_area_m2 * 10000 / 800)}×800mm approx",
        "exhaust_duct_required": kva >= 125,
        "exhaust_duct_diameter_mm": 250 if kva < 125 else (300 if kva < 250 else 400),
        "note": (
            f"Minimum fresh air: {airflow_m3_per_sec * 3600:.0f} m³/hr. "
            f"Inlet louvre {inlet_area_m2:.2f} m², outlet louvre {outlet_area_m2:.2f} m². "
            f"{'Forced exhaust duct required.' if kva >= 125 else 'Natural ventilation sufficient.'}"
        ),
    }


def get_installation_requirements(
    kva: float,
    enclosure_type: str = "acoustic",
    indoor_outdoor: str = "indoor",
    has_ats: bool = True,
) -> dict:
    """
    Generate complete site preparation checklist for DG set installation.

    Args:
        kva: DG set rating in kVA
        enclosure_type: 'acoustic' | 'super_silent' | 'open' | 'weather_proof'
        indoor_outdoor: 'indoor' | 'outdoor'
        has_ats: Whether ATS (Automatic Transfer Switch) is included

    Returns:
        Complete checklist dict with civil, electrical, mechanical requirements.
    """
    dims = get_plinth_dimensions(kva)
    ventilation = get_ventilation_requirements(kva)
    enclosure_type = enclosure_type.lower()
    indoor_outdoor = indoor_outdoor.lower()

    # Cable sizing — rule of thumb: 1.5A per kVA at 415V
    amps = kva * 1.5 * 1000 / 415
    if amps <= 100:
        cable = "35 sq.mm 4C Aluminium armoured"
    elif amps <= 200:
        cable = "70 sq.mm 4C Aluminium armoured"
    elif amps <= 300:
        cable = "120 sq.mm 4C Aluminium armoured"
    elif amps <= 400:
        cable = "185 sq.mm 4C Aluminium armoured"
    else:
        cable = "240 sq.mm 4C Aluminium armoured (2 runs)"

    checklist = {
        "summary": {
            "kva_rating": kva,
            "installation_type": f"{indoor_outdoor} / {enclosure_type} enclosure",
            "has_ats": has_ats,
        },
        "civil_works": {
            "plinth": (
                f"RCC plinth {dims['plinth_L_mm']}×{dims['plinth_W_mm']}×{dims['plinth_height_mm']}mm. "
                "M20 concrete, 8mm reinforcement bars, smooth top surface."
            ),
            "room_size": (
                f"Min room {dims['room_L_mm']}mm (L) × {dims['room_W_mm']}mm (W) × {dims['room_H_mm']}mm (H)."
                if indoor_outdoor == "indoor" else "Outdoor canopy/shed with 1m clearance all sides."
            ),
            "floor_drain": "100mm dia floor drain inside DG room for coolant/oil spills.",
            "anti_vibration": "AVM (Anti-Vibration Mounts) pads supplied with DG set — plinth must be level to ±3mm.",
            "cable_trench": f"Cable trench 300mm wide × 450mm deep from DG set to main LT panel.",
            "fuel_tank_room": f"Separate fuel storage if tank >2000L — RCC bunded room required per petroleum rules.",
        },
        "mechanical": {
            "exhaust_pipe": (
                f"MS exhaust pipe min {'200mm' if kva < 125 else '250mm' if kva < 250 else '300mm'} dia, "
                "insulated, discharged min 3m above roof or outside building."
            ),
            "ventilation_inlet": f"Inlet louvre {ventilation['inlet_louver_area_m2']} m² on prevailing wind side.",
            "ventilation_outlet": f"Outlet louvre {ventilation['outlet_louver_area_m2']} m² opposite to inlet.",
            "fuel_line": "25mm GI pipe from day tank to engine, with inline filter and shutoff valve.",
            "day_tank": f"500L day tank recommended inside DG room (petroleum rules compliant).",
        },
        "electrical": {
            "power_cable": f"Output cable: {cable} — from DG set output to ATS/main panel.",
            "ats_panel": "ATS panel to be mounted near main distribution board, min 1.2m clearance front." if has_ats else "Manual changeover switch (MCS) with mechanical interlock required.",
            "earthing": (
                f"{'2 x' if kva >= 100 else '1 x'} GI strip 40×6mm earth conductor. "
                "Earthing electrode as per IS 3043. DG neutral earthed separately from building earth."
            ),
            "neutral_link": "Separate neutral bus in ATS panel. Do not connect DG neutral to mains neutral without isolation.",
            "battery_charger": "Built-in trickle charger included. Ensure 230V single phase supply for charger.",
            "protection": "DG set includes: overcurrent, short circuit, earth fault, high temp, low oil pressure protection.",
        },
        "safety_compliance": {
            "cpcb_iv_plus": f"{'Acoustic enclosure' if enclosure_type in ('acoustic','super_silent') else 'Weather proof enclosure'} supplied — CPCB-IV+ norm compliant.",
            "fire_extinguisher": "CO2 fire extinguisher (4.5 kg) to be placed within 5m of DG set.",
            "no_smoking": "'No Smoking' and 'Flammable' signage required in fuel storage area.",
            "oil_sump": "Drip tray / concrete sump under DG set to collect oil drips — mandatory for CPCB.",
            "operating_manual": "CPCB-IV+ compliance certificate and test report to be submitted to local PCB.",
        },
        "commissioning": {
            "steps": [
                "Verify plinth level (±3mm tolerance)",
                "Place DG set on AVM pads, align and grout",
                "Connect exhaust system — check for leaks",
                "Terminate power cables — verify insulation resistance (>100 MΩ)",
                "Fill coolant (pre-mixed) and lube oil (as specified)",
                "Charge batteries — check electrolyte level",
                "Test ATS changeover under load",
                "Run no-load for 30 minutes, then load test to 75%",
                "Record load test readings: voltage, frequency, fuel consumption",
                "Obtain CPCB-IV+ installation certificate from authorized body",
            ],
        },
    }

    logger.info("installation_requirements_generated", kva=kva, type=indoor_outdoor, enclosure=enclosure_type)
    return checklist
