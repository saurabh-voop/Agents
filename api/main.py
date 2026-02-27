"""
FastAPI application — the web layer for the Pai Kane Agent System.
Handles: WhatsApp webhooks, GM approval dashboard API, admin endpoints, health checks.
"""

from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
import structlog

from core.config import get_settings
from api.webhooks import router as webhooks_router
from api.dashboard import router as dashboard_router
from api.admin import router as admin_router

logger = structlog.get_logger()
settings = get_settings()

app = FastAPI(
    title="Pai Kane Group — Agentic AI Sales System",
    description="Three-tier autonomous AI agent system for lead mining, technical configuration, and commercial pricing.",
    version="1.0.0",
)

# CORS for dashboard frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
app.include_router(dashboard_router, prefix="/dashboard", tags=["Dashboard"])
app.include_router(admin_router, prefix="/admin", tags=["Admin"])


@app.get("/")
async def root():
    return {
        "system": "Pai Kane Agentic AI Sales System",
        "version": "1.0.0",
        "status": "running",
        "agents": ["agent_s_r1", "agent_rm", "agent_gm"],
    }


@app.get("/health")
async def health():
    """Health check for monitoring."""
    from database.connection import get_sync_engine
    try:
        engine = get_sync_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "database": db_status,
        "timestamp": str(datetime.utcnow()),
    }
