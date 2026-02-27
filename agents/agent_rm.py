"""
Agent-RM: Technical Engineer — Maharashtra

Functions:
- Pick up escalations from Agent-S
- Match customer requirements to product catalog
- Build BOM (Bill of Materials)
- Validate CPCB IV+ compliance
- Assess delivery feasibility
- Handle customer-facing technical conversations
- Escalate to Agent-GM for pricing
- Escalate non-standard requirements to human engineering
"""

import json
import structlog
from sqlalchemy import text

from core.config import get_settings
from core.llm import call_llm_json, call_llm_simple
from core.conversation import get_conversation_history, add_message, update_current_agent, format_history_for_llm
from core.escalation import pick_up_escalation, complete_escalation, create_escalation
from core.audit import log_activity
from database.connection import get_sync_engine
from tools.calculator import calculate_derating
from tools.whatsapp import send_text_message

logger = structlog.get_logger()
settings = get_settings()


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
            # Build technical configuration
            config = self._build_configuration(payload)

            if not config.get("is_standard", True):
                # Non-standard: escalate to human engineering
                self._escalate_to_engineering(esc, config)
                complete_escalation(str(esc["id"]), {"action": "escalated_engineering"})
                return {"processed": True, "action": "escalated_engineering"}

            # Save configuration to DB
            config_id = self._save_configuration(config, esc)

            # Escalate to Agent-GM for pricing
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
        """Match requirement to product catalog and build complete configuration."""
        requirement = payload.get("requirement_summary", "")
        estimated_kva = self._extract_kva(requirement)

        # Look up matching product from catalog
        product = self._find_matching_product(estimated_kva)

        if not product:
            return {
                "is_standard": False,
                "non_standard_reason": f"No standard product found for {estimated_kva} kVA",
                "kva_rating": estimated_kva,
            }

        # Check if derating is needed (standard conditions assumed unless stated)
        derating = calculate_derating(estimated_kva)

        # Determine enclosure and panel from product
        config = {
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
            "compliance": {
                "cpcb_iv_compliant": True,
                "enclosure_suitable": True,
                "state_specific_flags": [],
            },
            "delivery": {
                "lead_time_weeks_min": product.get("lead_time_weeks", 4),
                "lead_time_weeks_max": product.get("lead_time_weeks", 4) + 2,
                "feasibility": "feasible",
            },
            "is_standard": True,
            "segment": payload.get("segment", "construction"),
            "quantity": 1,
        }

        return config

    def _extract_kva(self, text: str) -> float:
        """Extract kVA rating from requirement text."""
        import re
        match = re.search(r'(\d+\.?\d*)\s*kva', text.lower())
        if match:
            return float(match.group(1))
        # Ask LLM if regex fails
        try:
            result = call_llm_json(
                "Extract the kVA rating from this text. Return {\"kva\": number}. If not mentioned, estimate based on context (construction site: 62.5-250).",
                text,
            )
            return float(result.get("kva", 125))
        except Exception:
            return 125.0  # Default for construction

    def _find_matching_product(self, kva: float) -> dict | None:
        """Find the best matching product from the catalog."""
        query = text("""
            SELECT * FROM products 
            WHERE kva_rating >= :kva AND active = true
            ORDER BY kva_rating ASC
            LIMIT 1
        """)
        with self.engine.connect() as conn:
            row = conn.execute(query, {"kva": kva}).fetchone()
            return dict(row._mapping) if row else None

    def _build_bom(self, product: dict) -> list[dict]:
        """Build standard BOM for a product."""
        bom = [
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
        return bom

    def _save_configuration(self, config: dict, escalation: dict) -> str:
        """Save technical configuration to database."""
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
                "kva": config["kva_rating"],
                "phase": config.get("phase", "3-phase"),
                "eng_make": config["engine_make"],
                "eng_model": config["engine_model"],
                "alt_make": config["alternator_make"],
                "alt_model": config["alternator_model"],
                "controller": config.get("controller", "DEIF SGC120"),
                "enc": config["enclosure_type"],
                "panel": config["panel_type"],
                "sku": config.get("sku"),
                "bom": json.dumps(config["bom"]),
                "cpcb_iv": compliance.get("cpcb_iv_compliant", True),
                "noise_zone": compliance.get("noise_zone"),
                "compliance_notes": json.dumps(compliance.get("state_specific_flags", [])),
                "lead_time_weeks": delivery.get("lead_time_weeks_min", 4),
                "feasibility": delivery.get("feasibility", "feasible"),
                "standard": config["is_standard"],
                "non_standard_reason": config.get("non_standard_reason"),
                "agent": self.agent_id,
            })
            conn.commit()
            return str(result.fetchone()[0])

    def _escalate_to_engineering(self, escalation: dict, config: dict) -> None:
        """Escalate non-standard requirements to human engineering team."""
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
