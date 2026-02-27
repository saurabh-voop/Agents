"""
Agent-RM: Technical Engineer — Maharashtra

Functions:
- Pick up escalations from Agent-S
- Match customer requirements to product catalog
- Build BOM (Bill of Materials)
- Validate CPCB IV+ compliance and noise levels
- Assess delivery feasibility
- Advise on fuel consumption, tank sizing, installation requirements
- Estimate load from equipment list
- Handle customer-facing technical conversations
- Escalate to Agent-GM for pricing
- Escalate non-standard requirements to human engineering
"""

import json
import structlog
from sqlalchemy import text

from core.config import get_settings
from core.llm import call_llm_json, run_agent_loop
from core.conversation import get_conversation_history, add_message, update_current_agent, format_history_for_llm
from core.escalation import pick_up_escalation, complete_escalation, create_escalation
from core.audit import log_activity
from database.connection import get_sync_engine
from tools.calculator import calculate_derating
from tools.whatsapp import send_text_message
from tools.noise_compliance import check_noise_compliance, get_enclosure_recommendation
from tools.load_estimator import estimate_load_from_equipment, suggest_kva_rating
from tools.fuel_calculator import calculate_fuel_consumption, calculate_tank_size, calculate_runtime
from tools.installation_advisor import get_installation_requirements, get_plinth_dimensions, get_ventilation_requirements

logger = structlog.get_logger()
settings = get_settings()


def _load_agent_config(config_path: str) -> dict:
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base_dir, config_path)
    with open(full_path, "r") as f:
        return json.load(f)


_AGENT_RM_CONFIG = _load_agent_config("config/agent_configs/agent_rm.json")
AGENT_RM_SYSTEM_PROMPT = _AGENT_RM_CONFIG.get("system_prompt", "")


# ============================================================
# OpenAI Tool Definitions for run_agent_loop
# ============================================================

AGENT_RM_TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search Pai Kane product catalog for DG sets matching a kVA requirement. Returns the nearest matching product with specs and prices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number", "description": "Required kVA rating (will find nearest product at or above this)"}
                },
                "required": ["kva"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_noise_compliance",
            "description": "Check CPCB-IV+ noise compliance for a DG set at given distance from boundary. Returns pass/fail and excess dB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number", "description": "DG set rating in kVA"},
                    "zone_type": {"type": "string", "enum": ["industrial", "commercial", "residential", "silence"], "description": "Site zone type"},
                    "distance_m": {"type": "number", "description": "Distance in metres from DG set to nearest sensitive receptor"},
                },
                "required": ["kva", "zone_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_enclosure_recommendation",
            "description": "Recommend the correct enclosure type (acoustic / super_silent) based on zone and setback distance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number"},
                    "zone_type": {"type": "string", "enum": ["industrial", "commercial", "residential", "silence"]},
                    "distance_m": {"type": "number", "description": "Setback distance in metres"},
                },
                "required": ["kva", "zone_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_load_from_equipment",
            "description": "Estimate total kVA requirement from customer's equipment list with diversity factors applied.",
            "parameters": {
                "type": "object",
                "properties": {
                    "equipment_list": {
                        "type": "array",
                        "description": "List of equipment objects",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "description": "Equipment type: ac/lift/motor/pump/computer/light/ups/server/heater/fan/crane/welding/generic"},
                                "quantity": {"type": "number"},
                                "kw_each": {"type": "number", "description": "Rated power in kW per unit"},
                                "kva_each": {"type": "number", "description": "Apparent power in kVA per unit (use if kW unknown)"},
                            },
                            "required": ["type", "quantity"],
                        },
                    }
                },
                "required": ["equipment_list"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_fuel_consumption",
            "description": "Calculate HSD fuel consumption in litres/hour for a DG set at given load percentage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number", "description": "DG set rating in kVA"},
                    "load_pct": {"type": "number", "description": "Operating load percentage (25-100), default 75"},
                },
                "required": ["kva"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_tank_size",
            "description": "Calculate recommended fuel tank size for required autonomy hours.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number"},
                    "runtime_hours": {"type": "number", "description": "Required autonomy in hours"},
                    "load_pct": {"type": "number", "description": "Expected load percentage, default 75"},
                },
                "required": ["kva", "runtime_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_runtime",
            "description": "Calculate how many hours a given fuel tank will last.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number"},
                    "tank_litres": {"type": "number", "description": "Fuel tank capacity in litres"},
                    "load_pct": {"type": "number", "description": "Operating load percentage, default 75"},
                },
                "required": ["kva", "tank_litres"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_installation_requirements",
            "description": "Generate complete site preparation checklist: civil, electrical, mechanical, safety requirements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number"},
                    "enclosure_type": {"type": "string", "enum": ["acoustic", "super_silent", "open", "weather_proof"], "description": "Default: acoustic"},
                    "indoor_outdoor": {"type": "string", "enum": ["indoor", "outdoor"], "description": "Installation environment"},
                    "has_ats": {"type": "boolean", "description": "Whether ATS panel is included"},
                },
                "required": ["kva"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_plinth_dimensions",
            "description": "Get recommended plinth and room dimensions for a DG set installation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number"}
                },
                "required": ["kva"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_derating",
            "description": "Calculate kVA derating for high altitude or high ambient temperature sites.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rated_kva": {"type": "number", "description": "Rated kVA at standard conditions"},
                    "altitude_m": {"type": "number", "description": "Site altitude in metres above sea level, default 0"},
                    "ambient_temp_c": {"type": "number", "description": "Ambient temperature in Celsius, default 25"},
                },
                "required": ["rated_kva"],
            },
        },
    },
]


def _make_tool_handlers(engine) -> dict:
    """Build tool_handlers dict linking tool names to Python functions."""

    def search_products(kva: float) -> dict:
        query = text("""
            SELECT * FROM products
            WHERE kva_rating >= :kva AND is_active = true
            ORDER BY kva_rating ASC LIMIT 1
        """)
        with engine.connect() as conn:
            row = conn.execute(query, {"kva": kva}).fetchone()
            if row:
                return dict(row._mapping)
            return {"error": f"No standard product found for {kva} kVA — may need custom engineering."}

    return {
        "search_products": search_products,
        "check_noise_compliance": check_noise_compliance,
        "get_enclosure_recommendation": get_enclosure_recommendation,
        "estimate_load_from_equipment": estimate_load_from_equipment,
        "calculate_fuel_consumption": calculate_fuel_consumption,
        "calculate_tank_size": calculate_tank_size,
        "calculate_runtime": calculate_runtime,
        "get_installation_requirements": get_installation_requirements,
        "get_plinth_dimensions": get_plinth_dimensions,
        "calculate_derating": calculate_derating,
    }


class AgentRM:
    """Agent-RM: Technical configuration engine for Maharashtra."""

    def __init__(self):
        self.agent_id = "agent_rm"
        self.engine = get_sync_engine()

    def process_pending_escalation(self) -> dict:
        """Pick up and process one pending escalation from Agent-S."""
        esc = pick_up_escalation("agent_rm")
        if not esc:
            return {"processed": False, "reason": "no_pending_escalations"}

        logger.info("rm_processing", escalation_id=str(esc["id"]))
        payload = esc["payload"]

        try:
            config = self._build_configuration(payload)

            if not config.get("is_standard", True):
                self._escalate_to_engineering(esc, config)
                complete_escalation(str(esc["id"]), {"action": "escalated_engineering"})
                return {"processed": True, "action": "escalated_engineering"}

            config_id = self._save_configuration(config, esc)

            create_escalation(
                from_agent=self.agent_id,
                to_agent="agent_gm",
                lead_id=str(esc.get("lead_id")) if esc.get("lead_id") else None,
                conversation_id=str(esc.get("conversation_id")) if esc.get("conversation_id") else None,
                priority=esc.get("priority", "WARM"),
                reason="pricing_request",
                payload={
                    "config_id": config_id,
                    "config": config,
                    "customer_name": payload.get("customer_name", ""),
                    "company_name": payload.get("company_name", ""),
                    "phone": payload.get("phone", ""),
                    "location": payload.get("location", "Mumbai"),
                    "conversation_id": str(esc.get("conversation_id")) if esc.get("conversation_id") else None,
                },
            )

            complete_escalation(str(esc["id"]), {"config_id": config_id})
            log_activity(
                agent=self.agent_id, action="configuration_completed",
                lead_id=str(esc.get("lead_id")) if esc.get("lead_id") else None,
                escalation_id=str(esc["id"]),
                details={"kva": config.get("kva_rating"), "is_standard": True},
            )
            return {"processed": True, "config_id": config_id}

        except Exception as e:
            logger.error("rm_processing_failed", escalation_id=str(esc["id"]), error=str(e))
            log_activity(agent=self.agent_id, action="configuration_failed", error_message=str(e))
            return {"processed": False, "error": str(e)}

    def _build_configuration(self, payload: dict) -> dict:
        """
        Use run_agent_loop to let LLM reason through tools and build the configuration.
        LLM decides which tools to call based on what information is available.
        """
        requirement = payload.get("requirement_summary", "")
        company = payload.get("company_name", "Unknown")
        customer = payload.get("customer_name", "Unknown")

        user_message = f"""Build a complete technical configuration for this customer requirement.

Customer: {customer} ({company})
Requirement: {requirement}
Segment: {payload.get("segment", "construction")}
Location: {payload.get("location", "Mumbai Suburban")}

Steps:
1. If requirement mentions equipment list → use estimate_load_from_equipment first
2. If kVA is known/estimated → search_products for the right model
3. Check noise compliance for the site (assume residential if not specified)
4. Verify enclosure type required
5. Build a complete configuration dict with: kva_rating, engine_make, engine_model,
   alternator_make, alternator_model, enclosure_type, panel_type, sku, bom (12 items),
   compliance (cpcb_iv_compliant, noise_zone, compliance_notes),
   delivery (lead_time_weeks_min, lead_time_weeks_max, feasibility),
   is_standard (true/false), segment, quantity.

Return a JSON configuration object as your final response."""

        tool_handlers = _make_tool_handlers(self.engine)

        result = run_agent_loop(
            system_prompt=AGENT_RM_SYSTEM_PROMPT,
            user_message=user_message,
            tools_spec=AGENT_RM_TOOLS_SPEC,
            tool_handlers=tool_handlers,
            model=settings.openai_model_default,
            max_iterations=8,
        )

        # Parse the JSON config from LLM response
        try:
            response_text = result.get("response", "")
            # Strip markdown if present
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            config = json.loads(response_text.strip())
            config["_tool_calls"] = len(result.get("tool_calls", []))
            config["_iterations"] = result.get("iterations", 0)
            return config
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("rm_config_parse_failed", error=str(e))
            # Fallback: extract kVA and do direct product lookup
            return self._direct_configuration(payload, requirement)

    def _direct_configuration(self, payload: dict, requirement: str) -> dict:
        """Fallback: build config directly from products table without LLM loop."""
        estimated_kva = self._extract_kva(requirement)
        product = self._find_matching_product(estimated_kva)

        if not product:
            return {
                "is_standard": False,
                "non_standard_reason": f"No standard product found for {estimated_kva} kVA",
                "kva_rating": estimated_kva,
            }

        return {
            "kva_rating": product["kva_rating"],
            "phase": product["phase"],
            "engine_make": product["engine_make"],
            "engine_model": product["engine_model"],
            "alternator_make": product["alternator_make"],
            "alternator_model": product["alternator_model"],
            "enclosure_type": product["enclosure_type"],
            "panel_type": product["panel_type"],
            "sku": f"PK-{product['engine_make'][:3]}-{int(product['kva_rating'])}-{product['phase'][:2]}",
            "pep_price": product.get("pep_price", 0),
            "dealer_price": product.get("dealer_price", 0),
            "customer_price": product.get("customer_price", 0),
            "bom": self._build_bom(product),
            "compliance": {"cpcb_iv_compliant": True, "noise_zone": "residential", "compliance_notes": []},
            "delivery": {
                "lead_time_weeks_min": product.get("lead_time_weeks_min", 4),
                "lead_time_weeks_max": product.get("lead_time_weeks_max", 6),
                "feasibility": "feasible",
            },
            "is_standard": True,
            "segment": payload.get("segment", "construction"),
            "quantity": 1,
        }

    def _extract_kva(self, requirement_text: str) -> float:
        import re
        match = re.search(r'(\d+\.?\d*)\s*kva', requirement_text.lower())
        if match:
            return float(match.group(1))
        try:
            result = call_llm_json(
                "Extract the kVA rating from this text. Return {\"kva\": number}. If not mentioned, estimate based on context (construction site default: 125).",
                requirement_text,
            )
            return float(result.get("kva", 125))
        except Exception:
            return 125.0

    def _find_matching_product(self, kva: float) -> dict | None:
        query = text("""
            SELECT * FROM products
            WHERE kva_rating >= :kva AND is_active = true
            ORDER BY kva_rating ASC LIMIT 1
        """)
        with self.engine.connect() as conn:
            row = conn.execute(query, {"kva": kva}).fetchone()
            return dict(row._mapping) if row else None

    def _build_bom(self, product: dict) -> list[dict]:
        return [
            {"item": f"DG Set {product['kva_rating']} kVA ({product['engine_make']} {product['engine_model']})", "qty": 1},
            {"item": f"Alternator ({product['alternator_make']} {product['alternator_model']})", "qty": 1},
            {"item": f"Acoustic Enclosure ({product['enclosure_type'].replace('_', ' ').title()})", "qty": 1},
            {"item": f"Control Panel ({product['panel_type'].upper().replace('_', ' ')})", "qty": 1},
            {"item": "AVM Pads", "qty": 1},
            {"item": "Silencer with Bellows", "qty": 1},
            {"item": "Lube Oil (First Fill)", "qty": 1},
            {"item": "Coolant (First Fill)", "qty": 1},
            {"item": "MS Exhaust Pipe", "qty": 1},
            {"item": "Earthing Conductor", "qty": 1},
            {"item": "Control Cables", "qty": 1},
            {"item": "Hot Air Exhaust Ducting", "qty": 1},
        ]

    def _save_configuration(self, config: dict, escalation: dict) -> str:
        compliance = config.get("compliance", {})
        delivery = config.get("delivery", {})
        query = text("""
            INSERT INTO technical_configs
            (lead_id, escalation_id, kva_rating, phase, engine_make, engine_model,
             alternator_make, alternator_model, controller, enclosure_type, panel_type,
             sku, bom, cpcb_iv_compliant, noise_zone, compliance_notes,
             standard_lead_time_weeks, delivery_feasibility,
             is_standard, non_standard_reason, created_by)
            VALUES (:lead_id, :esc_id, :kva, :phase, :eng_make, :eng_model,
                    :alt_make, :alt_model, :controller, :enc, :panel,
                    :sku, :bom, :cpcb_iv, :noise_zone, :compliance_notes,
                    :lead_time_weeks, :feasibility,
                    :standard, :non_standard_reason, :agent)
            RETURNING id
        """)
        with self.engine.connect() as conn:
            result = conn.execute(query, {
                "lead_id": str(escalation.get("lead_id")) if escalation.get("lead_id") else None,
                "esc_id": str(escalation["id"]),
                "kva": config.get("kva_rating", 0),
                "phase": config.get("phase", "3-phase"),
                "eng_make": config.get("engine_make", ""),
                "eng_model": config.get("engine_model", ""),
                "alt_make": config.get("alternator_make", ""),
                "alt_model": config.get("alternator_model", ""),
                "controller": config.get("controller", "DEIF SGC120"),
                "enc": config.get("enclosure_type", "acoustic"),
                "panel": config.get("panel_type", "amf_logic"),
                "sku": config.get("sku"),
                "bom": json.dumps(config.get("bom", [])),
                "cpcb_iv": compliance.get("cpcb_iv_compliant", True),
                "noise_zone": compliance.get("noise_zone"),
                "compliance_notes": json.dumps(compliance.get("compliance_notes", [])),
                "lead_time_weeks": delivery.get("lead_time_weeks_min", 4),
                "feasibility": delivery.get("feasibility", "feasible"),
                "standard": config.get("is_standard", True),
                "non_standard_reason": config.get("non_standard_reason"),
                "agent": self.agent_id,
            })
            conn.commit()
            return str(result.fetchone()[0])

    def _escalate_to_engineering(self, escalation: dict, config: dict) -> None:
        from tools.email_tool import send_email
        send_email(
            to_email=settings.engineering_email,
            subject=f"Non-Standard DG Requirement — {config.get('kva_rating', '?')} kVA",
            body_html=f"""<h2>Non-Standard Technical Requirement</h2>
            <p><strong>Reason:</strong> {config.get('non_standard_reason', 'Unknown')}</p>
            <p><strong>Customer:</strong> {escalation['payload'].get('company_name', 'Unknown')}</p>
            <p><strong>kVA:</strong> {config.get('kva_rating', '?')}</p>
            <p>Please review and provide configuration guidance.</p>""",
        )
        log_activity(agent=self.agent_id, action="escalated_engineering",
                     details={"reason": config.get("non_standard_reason")})
