"""
Escalation manager — agent-to-agent message queue.
Uses PostgreSQL escalations table as a lightweight message queue.
Agent-S creates → Agent-RM polls and picks up → processes → creates new for Agent-GM.
"""

import json
import structlog
from datetime import datetime, timezone
from sqlalchemy import text
from database.connection import get_sync_engine
from core.audit import log_activity

logger = structlog.get_logger()


def create_escalation(
    from_agent: str,
    to_agent: str,
    lead_id: str | None,
    conversation_id: str | None,
    priority: str,
    reason: str,
    payload: dict,
) -> str:
    """
    Create an escalation between agents.
    Returns the escalation ID.
    """
    engine = get_sync_engine()
    query = text("""
        INSERT INTO escalations 
        (from_agent, to_agent, lead_id, conversation_id, priority, reason, payload, status)
        VALUES (:from_agent, :to_agent, :lead_id, :conv_id, :priority, :reason, :payload, 'pending')
        RETURNING id
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "lead_id": lead_id,
            "conv_id": conversation_id,
            "priority": priority,
            "reason": reason,
            "payload": json.dumps(payload),
        })
        conn.commit()
        esc_id = str(result.fetchone()[0])

    log_activity(
        agent=from_agent,
        action="escalation_created",
        lead_id=lead_id,
        conversation_id=conversation_id,
        escalation_id=esc_id,
        details={"to_agent": to_agent, "priority": priority, "reason": reason},
    )
    logger.info("escalation_created", id=esc_id, from_agent=from_agent, to_agent=to_agent, reason=reason)
    return esc_id


def pick_up_escalation(agent: str) -> dict | None:
    """
    Poll for pending escalations addressed to this agent.
    Returns the oldest pending escalation and marks it as 'processing'.
    """
    engine = get_sync_engine()

    # Atomic pick-up: SELECT + UPDATE in one transaction
    query = text("""
        UPDATE escalations 
        SET status = 'processing', updated_at = NOW()
        WHERE id = (
            SELECT id FROM escalations 
            WHERE to_agent = :agent AND status = 'pending'
            ORDER BY 
                CASE priority 
                    WHEN 'HOT' THEN 1 
                    WHEN 'WARM' THEN 2 
                    WHEN 'COLD' THEN 3 
                    ELSE 4 
                END,
                created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, from_agent, to_agent, lead_id, conversation_id, 
                  priority, reason, payload, created_at
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"agent": agent})
        conn.commit()
        row = result.fetchone()
        if row:
            data = dict(row._mapping)
            data["payload"] = json.loads(data["payload"]) if isinstance(data["payload"], str) else data["payload"]
            logger.info("escalation_picked_up", id=str(data["id"]), agent=agent)
            return data
    return None


def complete_escalation(
    escalation_id: str,
    response_payload: dict | None = None,
) -> None:
    """Mark an escalation as completed with optional response data."""
    engine = get_sync_engine()
    query = text("""
        UPDATE escalations 
        SET status = 'completed', 
            response = :response,
            updated_at = NOW()
        WHERE id = :esc_id
    """)
    with engine.connect() as conn:
        conn.execute(query, {
            "esc_id": escalation_id,
            "response": json.dumps(response_payload) if response_payload else None,
        })
        conn.commit()
    logger.info("escalation_completed", id=escalation_id)


def fail_escalation(escalation_id: str, error: str) -> None:
    """Mark an escalation as failed."""
    engine = get_sync_engine()
    query = text("""
        UPDATE escalations 
        SET status = 'failed', 
            response = :error,
            updated_at = NOW()
        WHERE id = :esc_id
    """)
    with engine.connect() as conn:
        conn.execute(query, {"esc_id": escalation_id, "error": json.dumps({"error": error})})
        conn.commit()
    logger.error("escalation_failed", id=escalation_id, error=error)
