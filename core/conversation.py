"""
Conversation manager — shared context across all agents.
All agents read/write to the same conversation thread.
Customer sees one continuous chat with "Pai Kane Group."
"""

import json
import structlog
from datetime import datetime, timezone
from sqlalchemy import text
from database.connection import get_sync_engine

logger = structlog.get_logger()


def create_conversation(
    customer_phone: str,
    customer_name: str,
    company_name: str,
    channel: str = "whatsapp",
    region: str = "R1",
    zoho_lead_id: str | None = None,
) -> str:
    """Create a new conversation thread. Returns conversation ID."""
    engine = get_sync_engine()
    query = text("""
        INSERT INTO conversations 
        (customer_phone, customer_name, company_name, status, current_agent, channel, region, zoho_lead_id)
        VALUES (:phone, :name, :company, 'active', 'agent_s', :channel, :region, :zoho_lead_id)
        RETURNING id
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {
            "phone": customer_phone,
            "name": customer_name,
            "company": company_name,
            "channel": channel,
            "region": region,
            "zoho_lead_id": zoho_lead_id,
        })
        conn.commit()
        row = result.fetchone()
        conv_id = str(row[0])
        logger.info("conversation_created", id=conv_id, customer=customer_name)
        return conv_id


def find_conversation_by_phone(phone: str) -> dict | None:
    """Find an active conversation by customer phone number."""
    engine = get_sync_engine()
    query = text("""
        SELECT id, customer_phone, customer_name, company_name, status, 
               current_agent, channel, region, zoho_lead_id, created_at
        FROM conversations 
        WHERE customer_phone = :phone AND status = 'active'
        ORDER BY created_at DESC LIMIT 1
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"phone": phone})
        row = result.fetchone()
        if row:
            return dict(row._mapping)
    return None


def get_conversation_history(conversation_id: str, limit: int = 50) -> list[dict]:
    """
    Load full message history for a conversation.
    Called before every agent response to build context.
    """
    engine = get_sync_engine()
    query = text("""
        SELECT sender_type, content, content_type, created_at
        FROM messages
        WHERE conversation_id = :conv_id
        ORDER BY created_at ASC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"conv_id": conversation_id, "limit": limit})
        return [dict(row._mapping) for row in result.fetchall()]


def add_message(
    conversation_id: str,
    sender_type: str,
    content: str,
    content_type: str = "text",
    channel: str = "whatsapp",
    delivery_status: str = "sent",
    whatsapp_message_id: str | None = None,
) -> str:
    """Add a message to a conversation. Returns message ID."""
    engine = get_sync_engine()
    query = text("""
        INSERT INTO messages 
        (conversation_id, sender_type, content, content_type, channel, 
         delivery_status, whatsapp_message_id)
        VALUES (:conv_id, :sender, :content, :content_type, :channel, 
                :status, :wa_id)
        RETURNING id
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {
            "conv_id": conversation_id,
            "sender": sender_type,
            "content": content,
            "content_type": content_type,
            "channel": channel,
            "status": delivery_status,
            "wa_id": whatsapp_message_id,
        })
        conn.commit()
        return str(result.fetchone()[0])


def update_current_agent(conversation_id: str, new_agent: str) -> None:
    """Switch which agent controls a conversation (invisible handoff)."""
    engine = get_sync_engine()
    query = text("""
        UPDATE conversations SET current_agent = :agent, updated_at = NOW()
        WHERE id = :conv_id
    """)
    with engine.connect() as conn:
        conn.execute(query, {"agent": new_agent, "conv_id": conversation_id})
        conn.commit()
    logger.info("agent_handoff", conversation_id=conversation_id, new_agent=new_agent)


def format_history_for_llm(history: list[dict]) -> str:
    """Format conversation history as text for LLM context window."""
    lines = []
    for msg in history:
        sender = msg["sender_type"].upper()
        if sender == "CUSTOMER":
            sender = "CUSTOMER"
        elif "agent_s" in sender:
            sender = "AGENT-S (You)"
        elif "agent_rm" in sender:
            sender = "AGENT-RM"
        elif "agent_gm" in sender:
            sender = "AGENT-GM"
        else:
            sender = sender.upper()

        ts = msg["created_at"]
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%Y-%m-%d %H:%M")
        lines.append(f"[{ts}] {sender}: {msg['content']}")
    return "\n".join(lines)
