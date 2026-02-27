"""
Agent-GM: Commercial Brain — Maharashtra

Functions:
- Pick up pricing escalations from Agent-RM
- Look up PEP/Dealer/Customer pricing from catalog
- Calculate margin, GST, freight, total deal value
- Check commodity price impact
- Check customer payment history (Zoho Books)
- Build Deal Recommendation for Human GM
- Notify GM for approval
- Process GM decisions
- Weekly pipeline review
"""

import json
import structlog
from datetime import datetime
from sqlalchemy import text

from core.config import get_settings
from core.llm import call_llm_simple
from core.escalation import pick_up_escalation, complete_escalation
from core.audit import log_activity
from database.connection import get_sync_engine
from tools.calculator import calculate_margin, calculate_gst, calculate_deal_value, estimate_freight
from tools.commodity import fetch_commodity_prices, store_commodity_prices, get_commodity_snapshot
from tools.zoho_books import get_customer_payment_history
from tools.email_tool import send_gm_approval_notification

logger = structlog.get_logger()
settings = get_settings()


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
            # Build deal recommendation
            recommendation = self._build_deal_recommendation(config, payload, esc)

            # Save to database
            rec_id = self._save_recommendation(recommendation, esc)

            # Notify Human GM
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
        """Assemble complete deal recommendation."""
        kva = config.get("kva_rating", 0)
        pep_price = config.get("pep_price", 0)
        dealer_price = config.get("dealer_price", 0)
        customer_price = config.get("customer_price", 0)
        company_name = payload.get("company_name", "Unknown")
        panel_type = config.get("panel_type", "amf_logic")

        # Calculate pricing
        recommended_price = customer_price  # Default: list price
        margin = calculate_margin(recommended_price, pep_price)
        gst = calculate_gst(recommended_price)
        freight = estimate_freight(to_location=payload.get("location", "Mumbai"))
        deal = calculate_deal_value(recommended_price, freight=freight)

        # Check commodities
        commodities = get_commodity_snapshot()

        # Check payment history
        payment_history = get_customer_payment_history(company_name)
        existing_customer = payment_history.get("existing_customer", False)

        # Determine payment terms
        if existing_customer and payment_history.get("payment_reliability") == "good":
            payment_terms = "50% advance, 50% on delivery"
        else:
            payment_terms = "100% advance (new customer)"

        # Determine delivery
        delivery_min = config.get("delivery", {}).get("lead_time_weeks_min", 4)
        delivery_max = config.get("delivery", {}).get("lead_time_weeks_max", 6)
        delivery_weeks = f"{delivery_min}-{delivery_max} weeks"

        # Quote validity — reduce if commodities volatile
        validity_days = 30 if commodities.get("overall_impact") != "significant" else 15
        quote_valid_until = (datetime.utcnow().date().replace(
            day=datetime.utcnow().day) if False else None)  # calculated below
        from datetime import timedelta
        quote_valid_until = (datetime.utcnow() + timedelta(days=validity_days)).date()

        # Generate recommendation reasoning with LLM
        reasoning = call_llm_simple(
            "You are Agent-GM for Pai Kane Group. Write a 2-3 sentence reasoning for this deal recommendation.",
            f"""Customer: {company_name}
Product: {kva} kVA DG Set
Recommended Price: INR {recommended_price:,.0f}
Margin above PEP: {margin['margin_pct']}%
Existing Customer: {existing_customer}
Commodity Impact: {commodities.get('overall_impact', 'none')}
Segment: {config.get('segment', 'construction')}
Delivery: {delivery_weeks}""",
            temperature=0.3,
            max_tokens=100,
        )

        # Determine recommendation
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
            "delivery_weeks": delivery_weeks,
            "quote_valid_until": str(quote_valid_until),
            "recommendation": recommendation,
            "reasoning": reasoning,
            "risk_level": "low" if margin["margin_pct"] > 3 else "medium",
            "strategic_value": f"{config.get('segment', 'construction').title()} sector — {kva} kVA — {delivery_weeks}",
        }

    def _save_recommendation(self, rec: dict, esc: dict) -> str:
        """Save deal recommendation to database."""
        pricing = rec["pricing"]
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
                "price_sheet": rec["price_sheet"],
                "price_tier": rec["price_tier"],
                "pep": pricing["pep_price"],
                "dealer": pricing["dealer_price"],
                "cust": pricing["customer_price"],
                "rec_price": pricing["recommended_price"],
                "accessories": pricing["accessories_total"],
                "subtotal": pricing["subtotal"],
                "gst": pricing["gst_amount"],
                "freight": pricing["freight_estimate"],
                "total": pricing["total_deal_value"],
                "discount_pct": pricing["discount_from_list_pct"],
                "margin_pct": pricing["margin_above_pep_pct"],
                "quantity": rec["quantity"],
                "commodities": json.dumps(rec["commodity_snapshot"]),
                "payment": rec["payment_terms"],
                "recommendation": rec["recommendation"],
                "reasoning": rec["reasoning"],
                "risk_level": rec["risk_level"],
                "strategic_value": rec.get("strategic_value", ""),
                "quote_valid_until": rec["quote_valid_until"],
                "agent": self.agent_id,
            })
            conn.commit()
            return str(result.fetchone()[0])

    def _notify_gm(self, rec: dict, rec_id: str) -> None:
        """Notify Human GM about pending deal for approval."""
        send_gm_approval_notification(
            gm_email=settings.gm_email,
            customer_name=rec["customer_name"],
            company_name=rec["company_name"],
            kva=rec["kva_rating"],
            recommended_price=rec["pricing"]["recommended_price"],
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
            RETURNING lead_id, customer_name, company_name, kva_rating
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
            # Trigger quote delivery to customer
            return {"status": "approved", "next_action": "deliver_quote"}

        return {"status": decision}

    # ============================================================
    # Commodity Monitoring
    # ============================================================

    def fetch_and_store_commodities(self) -> dict:
        """Daily commodity price fetch and storage."""
        prices = fetch_commodity_prices()
        store_commodity_prices(prices)
        log_activity(agent=self.agent_id, action="commodities_fetched", details={"count": len(prices)})
        return {"fetched": len(prices)}

    # ============================================================
    # Pipeline Review
    # ============================================================

    def run_pipeline_review(self) -> dict:
        """Weekly pipeline health assessment."""
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
