"""
Admin API — system management endpoints.

Endpoints:
  GET  /admin/status          — Full system status (agents, queue depths, DB)
  GET  /admin/products        — List product catalog
  POST /admin/trigger/mine    — Manually trigger a mining cycle
  POST /admin/trigger/rm      — Manually trigger RM escalation processing
  POST /admin/trigger/gm      — Manually trigger GM escalation processing
"""

from fastapi import APIRouter, HTTPException
from sqlalchemy import text
import structlog

from core.config import get_settings
from database.connection import get_sync_engine

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


# ============================================================
# GET /admin/status
# ============================================================

@router.get("/status")
def get_system_status():
    """Full system status — agent queue depths, DB counts, config."""
    engine = get_sync_engine()
    try:
        with engine.connect() as conn:
            escalations_pending = conn.execute(text("""
                SELECT to_agent, COUNT(*) as count
                FROM escalations WHERE status = 'pending'
                GROUP BY to_agent
            """)).fetchall()

            escalations_failed = conn.execute(text(
                "SELECT COUNT(*) FROM escalations WHERE status = 'failed'"
            )).scalar()

            lead_count = conn.execute(text(
                "SELECT COUNT(*) FROM leads WHERE deleted_at IS NULL"
            )).scalar()

            conversation_count = conn.execute(text(
                "SELECT COUNT(*) FROM conversations WHERE status = 'active'"
            )).scalar()

            product_count = conn.execute(text(
                "SELECT COUNT(*) FROM products WHERE is_active = true"
            )).scalar()

        return {
            "status": "running",
            "agents": {
                "agent_s_r1": {"region": settings.agent_s_region, "sector": settings.agent_s_sector},
                "agent_rm": {"region": "Maharashtra"},
                "agent_gm": {"region": "Maharashtra"},
            },
            "queue": {
                "pending_by_agent": {row[0]: row[1] for row in escalations_pending},
                "failed_escalations": escalations_failed,
            },
            "database": {
                "total_leads": lead_count,
                "active_conversations": conversation_count,
                "active_products": product_count,
            },
        }
    except Exception as e:
        logger.error("admin_status_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")


# ============================================================
# GET /admin/products
# ============================================================

@router.get("/products")
def list_products(phase: str = None, panel_type: str = None, enclosure_type: str = None):
    """List products from catalog with optional filters."""
    engine = get_sync_engine()
    conditions = ["is_active = true"]
    params = {}
    if phase:
        conditions.append("phase = :phase")
        params["phase"] = phase
    if panel_type:
        conditions.append("panel_type = :panel_type")
        params["panel_type"] = panel_type
    if enclosure_type:
        conditions.append("enclosure_type = :enclosure_type")
        params["enclosure_type"] = enclosure_type

    where = " AND ".join(conditions)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT * FROM products WHERE {where} ORDER BY kva_rating ASC"),
            params,
        ).fetchall()

    return {
        "count": len(rows),
        "products": [dict(row._mapping) for row in rows],
    }


# ============================================================
# POST /admin/trigger/*  — Manual task triggers for testing
# ============================================================

@router.post("/trigger/mine")
def trigger_mining():
    """Manually trigger Agent-S mining cycle."""
    from core.scheduler import mine_leads_task
    mine_leads_task.delay()
    return {"status": "triggered", "task": "mine_leads"}


@router.post("/trigger/rm")
def trigger_rm():
    """Manually trigger Agent-RM escalation processing."""
    from core.scheduler import process_rm_escalations_task
    process_rm_escalations_task.delay()
    return {"status": "triggered", "task": "process_rm_escalations"}


@router.post("/trigger/gm")
def trigger_gm():
    """Manually trigger Agent-GM escalation processing."""
    from core.scheduler import process_gm_escalations_task
    process_gm_escalations_task.delay()
    return {"status": "triggered", "task": "process_gm_escalations"}


@router.post("/trigger/commodities")
def trigger_commodities():
    """Manually trigger commodity price fetch."""
    from core.scheduler import fetch_commodity_prices_task
    fetch_commodity_prices_task.delay()
    return {"status": "triggered", "task": "fetch_commodity_prices"}
