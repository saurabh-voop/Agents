"""
Agent-GM: Commercial Brain — Maharashtra

Functions:
- Pick up pricing escalations from Agent-RM
- Look up PEP/Dealer/Customer pricing from catalog
- Calculate margin, GST, freight, total deal value
- Check commodity price impact
- Check customer payment history (Zoho Books)
- Verify company registration and credit risk (MCA)
- Check USD/INR impact on import costs
- Analyse historical deal pricing for this segment
- Build Deal Recommendation for Human GM
- Notify GM for approval
- Process GM decisions
- Weekly pipeline review
"""

import json
import structlog
from datetime import datetime, timedelta
from sqlalchemy import text

from core.config import get_settings
from core.llm import run_agent_loop
from core.escalation import pick_up_escalation, complete_escalation
from core.audit import log_activity
from core.memory import save_memory, build_memory_prompt
from database.connection import get_sync_engine
from tools.calculator import calculate_margin, calculate_gst, calculate_deal_value, estimate_freight
from tools.commodity import get_commodity_snapshot
from tools.zoho_books import get_customer_payment_history
from tools.company_lookup import lookup_company_mca
from tools.deal_analytics import get_segment_pricing_history, get_similar_deals, get_lost_deal_reasons
from tools.exchange_rate import get_usd_inr_rate, calculate_import_cost_impact
from tools.email_tool import send_gm_approval_notification

logger = structlog.get_logger()
settings = get_settings()


def _load_agent_config(config_path: str) -> dict:
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base_dir, config_path)
    with open(full_path, "r") as f:
        return json.load(f)


_AGENT_GM_CONFIG = _load_agent_config("config/agent_configs/agent_gm.json")
AGENT_GM_SYSTEM_PROMPT = _AGENT_GM_CONFIG.get("system_prompt", "")


# ============================================================
# OpenAI Tool Definitions for run_agent_loop
# ============================================================

AGENT_GM_TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_payment_history",
            "description": "Look up customer's payment history in Zoho Books. Returns existing_customer flag, payment reliability, overdue amounts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string", "description": "Company name to look up"}
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_company_mca",
            "description": "Verify company registration in MCA21/ROC database. Returns registration status, company type, age, and credit risk assessment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"}
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_usd_inr_rate",
            "description": "Get current USD/INR exchange rate and assess impact on import component costs (Cummins/Perkins engines).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_import_cost_impact",
            "description": "Calculate the INR impact of USD/INR rate movement on PEP price for a given engine make.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pep_price": {"type": "number", "description": "Current PEP price in INR"},
                    "engine_make": {"type": "string", "description": "Engine manufacturer: cummins/perkins/kirloskar/mahindra"},
                },
                "required": ["pep_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_commodity_snapshot",
            "description": "Get today's commodity prices (copper, steel, diesel) and their impact on product costs.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_segment_pricing_history",
            "description": "Get historical average winning prices and margins for a segment/kVA range from past deals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "segment": {"type": "string", "description": "construction/commercial/industrial/hospital/residential"},
                    "kva_min": {"type": "number", "description": "Minimum kVA for range filter"},
                    "kva_max": {"type": "number", "description": "Maximum kVA for range filter"},
                },
                "required": ["segment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_similar_deals",
            "description": "Find recent comparable deals (similar kVA, same segment) with their outcomes and prices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kva": {"type": "number"},
                    "segment": {"type": "string"},
                    "location": {"type": "string", "description": "Optional location filter"},
                },
                "required": ["kva", "segment"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_margin",
            "description": "Calculate margin percentage and amount above PEP floor price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selling_price": {"type": "number", "description": "Proposed selling price in INR"},
                    "pep_price": {"type": "number", "description": "PEP (Price at Entry Point) floor in INR"},
                },
                "required": ["selling_price", "pep_price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_gst",
            "description": "Calculate 18% GST on a price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subtotal": {"type": "number", "description": "Pre-GST amount in INR"}
                },
                "required": ["subtotal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_freight",
            "description": "Estimate freight cost from Goa factory to customer location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_location": {"type": "string", "description": "Destination city or area"},
                },
                "required": ["to_location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_deal_value",
            "description": "Calculate complete deal value: product + accessories + freight + GST.",
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_price": {"type": "number"},
                    "quantity": {"type": "number", "description": "Number of units, default 1"},
                    "accessories": {"type": "number", "description": "Accessories total in INR, default 0"},
                    "freight": {"type": "number", "description": "Freight cost in INR"},
                    "gst_rate": {"type": "number", "description": "GST rate percentage, default 18"},
                },
                "required": ["unit_price"],
            },
        },
    },
]


def _make_tool_handlers() -> dict:
    """Build tool_handlers dict for Agent-GM."""
    return {
        "get_customer_payment_history": get_customer_payment_history,
        "lookup_company_mca": lookup_company_mca,
        "get_usd_inr_rate": get_usd_inr_rate,
        "calculate_import_cost_impact": calculate_import_cost_impact,
        "get_commodity_snapshot": get_commodity_snapshot,
        "get_segment_pricing_history": get_segment_pricing_history,
        "get_similar_deals": get_similar_deals,
        "calculate_margin": calculate_margin,
        "calculate_gst": lambda subtotal: calculate_gst(subtotal),
        "estimate_freight": lambda to_location: estimate_freight(to_location=to_location),
        "calculate_deal_value": calculate_deal_value,
    }


class AgentGM:
    """Agent-GM: Commercial decision engine for Maharashtra."""

    def __init__(self):
        self.agent_id = "agent_gm"
        self.engine = get_sync_engine()

    def process_pending_escalation(self) -> dict:
        """Pick up and process one pending pricing request from Agent-RM."""
        esc = pick_up_escalation("agent_gm")
        if not esc:
            return {"processed": False}

        logger.info("gm_processing", escalation_id=str(esc["id"]))
        payload = esc["payload"]
        config = payload.get("config", {})

        try:
            recommendation = self._build_deal_recommendation(config, payload, esc)
            rec_id = self._save_recommendation(recommendation, esc)
            self._notify_gm(recommendation, rec_id)
            complete_escalation(str(esc["id"]), {"recommendation_id": rec_id})

            log_activity(
                agent=self.agent_id, action="deal_recommendation_created",
                lead_id=str(esc.get("lead_id")) if esc.get("lead_id") else None,
                details={
                    "kva": config.get("kva_rating"),
                    "recommended_price": recommendation["pricing"]["recommended_price"],
                    "margin_pct": recommendation["pricing"]["margin_above_pep_pct"],
                    "recommendation": recommendation["recommendation"],
                },
            )
            return {"processed": True, "recommendation_id": rec_id}

        except Exception as e:
            logger.error("gm_processing_failed", error=str(e))
            return {"processed": False, "error": str(e)}

    def _build_deal_recommendation(self, config: dict, payload: dict, esc: dict) -> dict:
        """
        Use run_agent_loop to let LLM reason through all commercial tools and
        build a comprehensive deal recommendation.
        """
        kva = config.get("kva_rating", 0)
        pep_price = config.get("pep_price", 0)
        customer_price = config.get("customer_price", 0)
        company_name = payload.get("company_name", "Unknown")
        engine_make = config.get("engine_make", "")
        segment = config.get("segment", "construction")
        location = payload.get("location", "Mumbai")

        user_message = f"""Build a complete deal recommendation for Human GM approval.

Customer: {payload.get('customer_name', 'Unknown')} ({company_name})
Product: {kva} kVA DG Set ({engine_make} engine)
Segment: {segment}
Location: {location}
PEP Price: ₹{pep_price:,.0f}
List (Customer) Price: ₹{customer_price:,.0f}

Steps:
1. lookup_company_mca("{company_name}") — verify registration, get credit risk
2. get_customer_payment_history("{company_name}") — check payment track record
3. get_usd_inr_rate() — check INR movement impact on Cummins/Perkins cost
4. calculate_import_cost_impact(pep_price={pep_price}, engine_make="{engine_make}") — adjust PEP if needed
5. get_commodity_snapshot() — check copper/steel/diesel impact today
6. get_segment_pricing_history(segment="{segment}", kva_min={kva*0.7:.0f}, kva_max={kva*1.3:.0f}) — historical anchor
7. get_similar_deals(kva={kva}, segment="{segment}") — comparable deals
8. calculate_margin(selling_price={customer_price}, pep_price={pep_price}) — baseline margin
9. estimate_freight(to_location="{location}") — freight cost
10. calculate_deal_value(unit_price={customer_price}, freight=<from step 9>) — total deal
11. Based on all data: decide recommended_price, payment_terms, quote_validity_days

Return a JSON recommendation with these exact keys:
{{
  "customer_name": str,
  "company_name": str,
  "segment": str,
  "existing_customer": bool,
  "kva_rating": number,
  "price_sheet": str,           // panel type
  "price_tier": str,            // customer/dealer/pep
  "pricing": {{
    "pep_price": number,
    "dealer_price": number,
    "customer_price": number,
    "recommended_price": number,
    "discount_from_list_pct": number,
    "margin_above_pep_pct": number,
    "accessories_total": number,
    "subtotal": number,
    "gst_amount": number,
    "freight_estimate": number,
    "total_deal_value": number
  }},
  "quantity": number,
  "commodity_snapshot": dict,
  "payment_terms": str,
  "delivery_weeks": str,
  "quote_valid_until": str,     // ISO date
  "recommendation": str,        // "approve_at_list" | "approve_with_discount" | "escalate_to_cmd"
  "reasoning": str,             // 2-3 sentences for Human GM
  "risk_level": str,            // "low" | "medium" | "high"
  "strategic_value": str,
  "company_credit_risk": str,   // from MCA check
  "exchange_rate_impact": str   // brief note on USD/INR
}}"""

        tool_handlers = _make_tool_handlers()

        # Inject memory about this company into system prompt
        system_prompt = AGENT_GM_SYSTEM_PROMPT + build_memory_prompt(company_name, self.agent_id)

        result = run_agent_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            tools_spec=AGENT_GM_TOOLS_SPEC,
            tool_handlers=tool_handlers,
            model=settings.openai_model_advanced,   # Use GPT-4o for commercial decisions
            max_iterations=12,
        )

        try:
            response_text = result.get("response", "")
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            recommendation = json.loads(response_text.strip())
            recommendation["_tool_calls"] = len(result.get("tool_calls", []))

            # Save key commercial facts to memory
            pricing = recommendation.get("pricing", {})
            save_memory(company_name, self.agent_id, {
                "last_deal_date": datetime.utcnow().strftime("%Y-%m-%d"),
                "last_recommended_price": pricing.get("recommended_price"),
                "last_margin_pct": pricing.get("margin_above_pep_pct"),
                "last_payment_terms": recommendation.get("payment_terms"),
                "last_recommendation": recommendation.get("recommendation"),
                "risk_level": recommendation.get("risk_level"),
                "existing_customer": recommendation.get("existing_customer"),
            })

            return recommendation
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("gm_recommendation_parse_failed", error=str(e))
            return self._direct_recommendation(config, payload, esc)

    def _direct_recommendation(self, config: dict, payload: dict, esc: dict) -> dict:
        """Fallback: build recommendation using direct Python calls (original approach)."""
        kva = config.get("kva_rating", 0)
        pep_price = config.get("pep_price", 0)
        dealer_price = config.get("dealer_price", 0)
        customer_price = config.get("customer_price", 0)
        company_name = payload.get("company_name", "Unknown")
        panel_type = config.get("panel_type", "amf_logic")

        recommended_price = customer_price
        margin = calculate_margin(recommended_price, pep_price)
        gst = calculate_gst(recommended_price)
        freight = estimate_freight(to_location=payload.get("location", "Mumbai"))
        deal = calculate_deal_value(recommended_price, freight=freight)
        commodities = get_commodity_snapshot()
        payment_history = get_customer_payment_history(company_name)
        existing_customer = payment_history.get("existing_customer", False)

        payment_terms = (
            "50% advance, 50% on delivery"
            if existing_customer and payment_history.get("payment_reliability") == "good"
            else "100% advance (new customer)"
        )

        validity_days = 30 if commodities.get("overall_impact") != "significant" else 15
        quote_valid_until = (datetime.utcnow() + timedelta(days=validity_days)).date()

        delivery_min = config.get("delivery", {}).get("lead_time_weeks_min", 4)
        delivery_max = config.get("delivery", {}).get("lead_time_weeks_max", 6)

        if margin["margin_pct"] < 0:
            recommendation = "escalate_to_cmd"
        elif margin["margin_pct"] < 3:
            recommendation = "approve_with_discount"
        else:
            recommendation = "approve_at_list"

        return {
            "customer_name": payload.get("customer_name", "Unknown"),
            "company_name": company_name,
            "segment": config.get("segment", "construction"),
            "existing_customer": existing_customer,
            "kva_rating": kva,
            "price_sheet": panel_type,
            "price_tier": "customer",
            "pricing": {
                "pep_price": pep_price,
                "dealer_price": dealer_price,
                "customer_price": customer_price,
                "recommended_price": recommended_price,
                "discount_from_list_pct": 0,
                "margin_above_pep_pct": margin["margin_pct"],
                "accessories_total": 0,
                "subtotal": deal["subtotal"],
                "gst_amount": gst["gst_amount"],
                "freight_estimate": freight,
                "total_deal_value": deal["total_deal_value"],
            },
            "quantity": config.get("quantity", 1),
            "commodity_snapshot": commodities,
            "payment_terms": payment_terms,
            "delivery_weeks": f"{delivery_min}-{delivery_max} weeks",
            "quote_valid_until": str(quote_valid_until),
            "recommendation": recommendation,
            "reasoning": f"Standard {config.get('segment', 'construction')} sector deal. {margin['margin_pct']:.1f}% margin above PEP.",
            "risk_level": "low" if margin["margin_pct"] > 3 else "medium",
            "strategic_value": f"{config.get('segment', 'construction').title()} sector — {kva} kVA",
            "company_credit_risk": "unknown",
            "exchange_rate_impact": "not assessed",
        }

    def _save_recommendation(self, rec: dict, esc: dict) -> str:
        """Save deal recommendation to database."""
        pricing = rec.get("pricing", {})
        query = text("""
            INSERT INTO deal_recommendations
            (lead_id, config_id, price_sheet, price_tier,
             pep_price, dealer_price, customer_price, recommended_price,
             accessories_total, subtotal, gst_amount, freight_estimate, total_deal_value,
             discount_from_list_pct, margin_above_pep_pct, quantity,
             commodity_snapshot, payment_terms,
             recommendation, reasoning, risk_level, strategic_value,
             quote_valid_until, created_by)
            VALUES (:lead_id, :config_id, :price_sheet, :price_tier,
                    :pep, :dealer, :cust, :rec_price,
                    :accessories, :subtotal, :gst, :freight, :total,
                    :discount_pct, :margin_pct, :quantity,
                    :commodities, :payment,
                    :recommendation, :reasoning, :risk_level, :strategic_value,
                    :quote_valid_until, :agent)
            RETURNING id
        """)
        with self.engine.connect() as conn:
            result = conn.execute(query, {
                "lead_id": str(esc.get("lead_id")) if esc.get("lead_id") else None,
                "config_id": esc["payload"].get("config_id"),
                "price_sheet": rec.get("price_sheet", "amf_logic"),
                "price_tier": rec.get("price_tier", "customer"),
                "pep": pricing.get("pep_price", 0),
                "dealer": pricing.get("dealer_price", 0),
                "cust": pricing.get("customer_price", 0),
                "rec_price": pricing.get("recommended_price", 0),
                "accessories": pricing.get("accessories_total", 0),
                "subtotal": pricing.get("subtotal", 0),
                "gst": pricing.get("gst_amount", 0),
                "freight": pricing.get("freight_estimate", 0),
                "total": pricing.get("total_deal_value", 0),
                "discount_pct": pricing.get("discount_from_list_pct", 0),
                "margin_pct": pricing.get("margin_above_pep_pct", 0),
                "quantity": rec.get("quantity", 1),
                "commodities": json.dumps(rec.get("commodity_snapshot", {})),
                "payment": rec.get("payment_terms", "100% advance"),
                "recommendation": rec.get("recommendation", "approve_at_list"),
                "reasoning": rec.get("reasoning", ""),
                "risk_level": rec.get("risk_level", "medium"),
                "strategic_value": rec.get("strategic_value", ""),
                "quote_valid_until": rec.get("quote_valid_until"),
                "agent": self.agent_id,
            })
            conn.commit()
            return str(result.fetchone()[0])

    def _notify_gm(self, rec: dict, rec_id: str) -> None:
        """Notify Human GM about pending deal for approval."""
        send_gm_approval_notification(
            gm_email=settings.gm_email,
            customer_name=rec.get("customer_name", "Unknown"),
            company_name=rec.get("company_name", "Unknown"),
            kva=rec.get("kva_rating", 0),
            recommended_price=rec.get("pricing", {}).get("recommended_price", 0),
            recommendation_id=rec_id,
        )
        log_activity(agent=self.agent_id, action="gm_notified", details={"recommendation_id": rec_id})

    # ============================================================
    # GM Approval Processing
    # ============================================================

    def process_approval(self, recommendation_id: str, decision: str, approved_price: float | None = None, notes: str = "") -> dict:
        """Process Human GM's decision on a deal recommendation."""
        query = text("""
            UPDATE deal_recommendations
            SET gm_decision = :decision,
                gm_approved_price = :price,
                gm_notes = :notes,
                gm_decided_at = NOW(),
                updated_at = NOW()
            WHERE id = :id
            RETURNING lead_id, recommended_price
        """)
        with self.engine.connect() as conn:
            result = conn.execute(query, {
                "decision": decision,
                "price": approved_price,
                "notes": notes,
                "id": recommendation_id,
            })
            conn.commit()
            row = result.fetchone()

        log_activity(
            agent=self.agent_id, action=f"gm_{decision}",
            details={"recommendation_id": recommendation_id, "approved_price": approved_price},
        )

        if decision == "approved" and row:
            return {"status": "approved", "next_action": "deliver_quote"}

        return {"status": decision}

    # ============================================================
    # Commodity Monitoring
    # ============================================================

    def fetch_and_store_commodities(self) -> dict:
        from tools.commodity import fetch_commodity_prices, store_commodity_prices
        prices = fetch_commodity_prices()
        store_commodity_prices(prices)
        log_activity(agent=self.agent_id, action="commodities_fetched", details={"count": len(prices)})
        return {"fetched": len(prices)}

    # ============================================================
    # Pipeline Review
    # ============================================================

    def run_pipeline_review(self) -> dict:
        query = text("""
            SELECT
                COUNT(*) FILTER (WHERE temperature = 'HOT') as hot_count,
                COUNT(*) FILTER (WHERE temperature = 'WARM') as warm_count,
                COUNT(*) FILTER (WHERE temperature = 'COLD') as cold_count,
                COUNT(*) FILTER (WHERE status = 'quoted') as quoted_count,
                COUNT(*) as total
            FROM leads
            WHERE region = 'R1' AND deleted_at IS NULL
            AND status NOT IN ('won', 'lost', 'archived')
        """)
        with self.engine.connect() as conn:
            row = conn.execute(query).fetchone()
            pipeline = dict(row._mapping) if row else {}

        log_activity(agent=self.agent_id, action="pipeline_review", details=pipeline)
        logger.info("pipeline_review", **pipeline)
        return pipeline
