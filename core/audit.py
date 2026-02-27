"""
Audit logging — every agent action is recorded here.
Append-only: never update or delete activity logs.
"""

import json
import structlog
from datetime import datetime, timezone
from sqlalchemy import text
from database.connection import get_sync_engine

logger = structlog.get_logger()


def log_activity(
    agent: str,
    action: str,
    lead_id: str | None = None,
    conversation_id: str | None = None,
    escalation_id: str | None = None,
    details: dict | None = None,
    processing_time_ms: int | None = None,
    llm_tokens_used: int | None = None,
    llm_model: str | None = None,
    error_message: str | None = None,
) -> None:
    """
    Log an agent action to the agent_activity_log table.
    This is called after every tool use and every agent decision.
    """
    engine = get_sync_engine()
    
    details_json = json.dumps(details) if details else None

    query = text("""
        INSERT INTO agent_activity_log 
        (agent, action, lead_id, conversation_id, escalation_id, 
         details, processing_time_ms, llm_tokens_used, llm_model, error_message)
        VALUES 
        (:agent, :action, :lead_id, :conversation_id, :escalation_id,
         :details, :processing_time_ms, :llm_tokens_used, :llm_model, :error_message)
    """)

    try:
        with engine.connect() as conn:
            conn.execute(query, {
                "agent": agent,
                "action": action,
                "lead_id": lead_id,
                "conversation_id": conversation_id,
                "escalation_id": escalation_id,
                "details": details_json,
                "processing_time_ms": processing_time_ms,
                "llm_tokens_used": llm_tokens_used,
                "llm_model": llm_model,
                "error_message": error_message,
            })
            conn.commit()
    except Exception as e:
        # Logging should never crash the agent — fail silently but log to stdout
        logger.error("audit_log_failed", agent=agent, action=action, error=str(e))
