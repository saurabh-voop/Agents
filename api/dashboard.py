"""
Dashboard API — Human GM approval interface.

Endpoints:
  GET  /dashboard/deals/pending       — All deals awaiting GM decision
  GET  /dashboard/deals/{id}          — Full deal detail
  POST /dashboard/deals/{id}/decide   — Approve / reject / modify / escalate
  GET  /dashboard/pipeline            — Live pipeline summary
  GET  /dashboard/activity            — Recent agent activity log
"""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
import structlog

from core.config import get_settings
from database.connection import get_sync_engine

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


# ============================================================
# Request / Response Models
# ============================================================

class GMDecision(BaseModel):
    decision: str           # approved | modified | rejected | escalated_cmd
    approved_price: float | None = None
    modified_payment_terms: str | None = None
    notes: str = ""
    decided_by: str = "human_gm"


# ============================================================
# GET /dashboard/deals/pending
# ============================================================

@router.get("/deals/pending")
def get_pending_deals():
    """
    Return all deal recommendations waiting for a GM decision.
    Ordered by creation time (oldest first — longest waiting).
    """
    engine = get_sync_engine()
    query = text("""
        SELECT
            dr.id,
            dr.lead_id,
            dr.config_id,
            dr.price_sheet,
            dr.price_tier,
            dr.pep_price,
            dr.recommended_price,
            dr.total_deal_value,
            dr.margin_above_pep_pct,
            dr.quantity,
            dr.payment_terms,
            dr.recommendation,
            dr.risk_level,
            dr.reasoning,
            dr.quote_valid_until,
            dr.created_at,
            -- Config details
            tc.kva_rating,
            tc.engine_make,
            tc.engine_model,
            tc.enclosure_type,
            tc.panel_type,
            tc.sku,
            -- Lead details
            l.customer_name,
            l.company_name,
            l.phone,
            l.email,
            l.location_city,
            l.segment,
            l.temperature
        FROM deal_recommendations dr
        LEFT JOIN technical_configs tc ON tc.id = dr.config_id
        LEFT JOIN leads l ON l.id = dr.lead_id
        WHERE dr.gm_decision IS NULL
        ORDER BY dr.created_at ASC
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()

    return {
        "count": len(rows),
        "deals": [_row_to_dict(row) for row in rows],
    }


# ============================================================
# GET /dashboard/deals/{recommendation_id}
# ============================================================

@router.get("/deals/{recommendation_id}")
def get_deal_detail(recommendation_id: str):
    """
    Return full detail for a single deal recommendation,
    including commodity snapshot and full BOM.
    """
    engine = get_sync_engine()
    query = text("""
        SELECT
            dr.*,
            tc.kva_rating,
            tc.engine_make,
            tc.engine_model,
            tc.alternator_make,
            tc.alternator_model,
            tc.controller,
            tc.enclosure_type,
            tc.panel_type,
            tc.sku,
            tc.bom,
            tc.cpcb_iv_compliant,
            tc.standard_lead_time_weeks,
            tc.delivery_feasibility,
            l.customer_name,
            l.company_name,
            l.phone,
            l.email,
            l.location_city,
            l.location_state,
            l.segment,
            l.temperature,
            l.source,
            l.requirement_text
        FROM deal_recommendations dr
        LEFT JOIN technical_configs tc ON tc.id = dr.config_id
        LEFT JOIN leads l ON l.id = dr.lead_id
        WHERE dr.id = :id
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"id": recommendation_id}).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Deal recommendation not found")

    result = _row_to_dict(row)

    # Parse JSONB fields
    for field in ("commodity_snapshot", "bom"):
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(result[field])
            except Exception:
                pass

    return result


# ============================================================
# POST /dashboard/deals/{recommendation_id}/decide
# ============================================================

@router.post("/deals/{recommendation_id}/decide")
def process_gm_decision(recommendation_id: str, body: GMDecision):
    """
    Record the GM's decision on a deal recommendation.

    Decisions:
    - approved        → use recommended_price (or approved_price if provided)
    - modified        → use approved_price with optional payment_terms change
    - rejected        → deal closed, no quote sent
    - escalated_cmd   → passed to CMD for final decision
    """
    valid_decisions = {"approved", "modified", "rejected", "escalated_cmd"}
    if body.decision not in valid_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision. Must be one of: {', '.join(valid_decisions)}",
        )

    if body.decision in ("approved", "modified") and body.approved_price is None:
        # For approved/modified, fetch recommended price as default if not provided
        engine = get_sync_engine()
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT recommended_price FROM deal_recommendations WHERE id = :id"),
                {"id": recommendation_id},
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Deal recommendation not found")
            body.approved_price = float(row[0])

    engine = get_sync_engine()
    query = text("""
        UPDATE deal_recommendations
        SET gm_decision       = :decision,
            gm_approved_price = :price,
            gm_notes          = :notes,
            gm_decided_at     = NOW(),
            gm_decided_by     = :decided_by,
            updated_at        = NOW()
        WHERE id = :id
        RETURNING id, lead_id, recommended_price, gm_approved_price
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {
            "decision": body.decision,
            "price": body.approved_price,
            "notes": body.notes,
            "decided_by": body.decided_by,
            "id": recommendation_id,
        })
        conn.commit()
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Deal recommendation not found")

    # Trigger post-decision actions via Agent-GM
    _trigger_post_decision(
        recommendation_id=recommendation_id,
        decision=body.decision,
        approved_price=body.approved_price,
        notes=body.notes,
    )

    logger.info(
        "gm_decision_recorded",
        recommendation_id=recommendation_id,
        decision=body.decision,
        decided_by=body.decided_by,
    )
    return {
        "status": "recorded",
        "recommendation_id": recommendation_id,
        "decision": body.decision,
        "approved_price": body.approved_price,
        "next_action": _next_action(body.decision),
    }


# ============================================================
# GET /dashboard/pipeline
# ============================================================

@router.get("/pipeline")
def get_pipeline_summary():
    """
    Live pipeline summary — lead counts by stage, temperature, and region.
    Used for the weekly Agent-GM pipeline review.
    """
    engine = get_sync_engine()
    with engine.connect() as conn:
        # Leads by temperature
        temp_counts = conn.execute(text("""
            SELECT temperature, COUNT(*) as count
            FROM leads
            WHERE deleted_at IS NULL
            AND status NOT IN ('won', 'lost', 'archived')
            GROUP BY temperature
        """)).fetchall()

        # Leads by status
        status_counts = conn.execute(text("""
            SELECT status, COUNT(*) as count
            FROM leads
            WHERE deleted_at IS NULL
            GROUP BY status
            ORDER BY count DESC
        """)).fetchall()

        # Pending GM decisions
        pending_decisions = conn.execute(text("""
            SELECT COUNT(*) FROM deal_recommendations WHERE gm_decision IS NULL
        """)).scalar()

        # Deals decided this month
        monthly_decisions = conn.execute(text("""
            SELECT gm_decision, COUNT(*) as count
            FROM deal_recommendations
            WHERE gm_decided_at >= date_trunc('month', NOW())
            GROUP BY gm_decision
        """)).fetchall()

        # Escalations in flight
        active_escalations = conn.execute(text("""
            SELECT to_agent, COUNT(*) as count
            FROM escalations
            WHERE status IN ('pending', 'processing')
            GROUP BY to_agent
        """)).fetchall()

    return {
        "timestamp": str(datetime.utcnow()),
        "leads_by_temperature": {row[0]: row[1] for row in temp_counts if row[0]},
        "leads_by_status": {row[0]: row[1] for row in status_counts},
        "pending_gm_decisions": pending_decisions,
        "decisions_this_month": {row[0]: row[1] for row in monthly_decisions if row[0]},
        "active_escalations": {row[0]: row[1] for row in active_escalations},
    }


# ============================================================
# GET /dashboard/activity
# ============================================================

@router.get("/activity")
def get_recent_activity(limit: int = 50):
    """
    Recent agent activity log — last N actions across all agents.
    Useful for debugging and monitoring.
    """
    engine = get_sync_engine()
    query = text("""
        SELECT agent, action, lead_id, conversation_id, escalation_id,
               details, processing_time_ms, llm_tokens_used, llm_model,
               error_message, created_at
        FROM agent_activity_log
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"limit": limit}).fetchall()

    return {
        "count": len(rows),
        "activity": [_row_to_dict(row) for row in rows],
    }


# ============================================================
# Helpers
# ============================================================

def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy row to a JSON-serialisable dict."""
    result = {}
    for key, value in row._mapping.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif hasattr(value, "__str__") and not isinstance(value, (str, int, float, bool, type(None))):
            result[key] = str(value)
        else:
            result[key] = value
    return result


def _next_action(decision: str) -> str:
    mapping = {
        "approved": "quote_will_be_delivered_to_customer",
        "modified": "quote_will_be_delivered_at_modified_price",
        "rejected": "deal_closed_no_quote",
        "escalated_cmd": "awaiting_cmd_review",
    }
    return mapping.get(decision, "unknown")


def _trigger_post_decision(
    recommendation_id: str,
    decision: str,
    approved_price: float | None,
    notes: str,
) -> None:
    """Trigger Agent-GM post-decision processing asynchronously via Celery."""
    try:
        from core.scheduler import process_gm_approval_task
        process_gm_approval_task.delay(recommendation_id, decision, approved_price, notes)
    except Exception as e:
        logger.warning("post_decision_trigger_failed", error=str(e))
