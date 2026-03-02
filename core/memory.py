"""
Agent Memory — Persistent per-company facts across sessions.

Before each agent run: fetch memory and inject into system prompt.
After each run: save new facts so the next run knows what happened.

Storage: agent_memory table (entity_id, agent, facts JSONB)
"""

import json
import structlog
from datetime import datetime
from sqlalchemy import text

logger = structlog.get_logger()


def get_memory(entity_id: str, agent: str) -> str:
    """
    Fetch stored memory for a company + agent combination.
    Returns a formatted string ready to append to a system prompt.
    Returns "" if no memory exists yet.
    """
    if not entity_id or not entity_id.strip():
        return ""

    try:
        from database.connection import get_sync_engine
        engine = get_sync_engine()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT facts FROM agent_memory WHERE entity_id = :eid AND agent = :agent"
            ), {"eid": entity_id.lower().strip(), "agent": agent}).fetchone()

        if not row or not row[0]:
            return ""

        facts: dict = row[0]
        if not facts:
            return ""

        lines = []
        for key, value in facts.items():
            if value:
                lines.append(f"- {key.replace('_', ' ').title()}: {value}")

        return "\n".join(lines) if lines else ""

    except Exception as e:
        logger.warning("memory_fetch_failed", entity_id=entity_id, agent=agent, error=str(e))
        return ""


def save_memory(entity_id: str, agent: str, new_facts: dict) -> None:
    """
    Save or merge new facts for a company + agent.
    Existing facts are preserved — new facts overwrite same keys.
    """
    if not entity_id or not entity_id.strip() or not new_facts:
        return

    # Strip None values
    clean_facts = {k: v for k, v in new_facts.items() if v is not None and v != ""}

    if not clean_facts:
        return

    try:
        from database.connection import get_sync_engine
        engine = get_sync_engine()
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO agent_memory (entity_id, agent, facts, updated_at)
                VALUES (:eid, :agent, :facts, NOW())
                ON CONFLICT (entity_id, agent)
                DO UPDATE SET
                    facts = agent_memory.facts || :facts,
                    updated_at = NOW()
            """), {
                "eid": entity_id.lower().strip(),
                "agent": agent,
                "facts": json.dumps(clean_facts),
            })
            conn.commit()

        logger.info("memory_saved", entity_id=entity_id, agent=agent, keys=list(clean_facts.keys()))

    except Exception as e:
        logger.warning("memory_save_failed", entity_id=entity_id, agent=agent, error=str(e))


def build_memory_prompt(entity_id: str, agent: str) -> str:
    """
    Returns a formatted memory block to append to a system prompt.
    Returns "" if no memory exists.
    """
    memory = get_memory(entity_id, agent)
    if not memory:
        return ""
    return f"\n\n## Previous Interactions — {entity_id}\n{memory}"
