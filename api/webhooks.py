"""
Webhook endpoints — receive external events.
WhatsApp: incoming customer messages.
Zoho CRM: lead creation events (optional).
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
import structlog

from core.config import get_settings
from core.scheduler import handle_incoming_message_task
from tools.whatsapp import parse_incoming_webhook

logger = structlog.get_logger()
settings = get_settings()
router = APIRouter()


# ============================================================
# WhatsApp Webhook
# ============================================================

@router.get("/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    WhatsApp webhook verification (GET).
    Meta sends this when you register the webhook URL.
    Must respond with the challenge string.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("whatsapp_webhook_verified")
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def whatsapp_incoming(request: Request):
    """
    WhatsApp incoming message webhook (POST).
    Called by Meta every time a customer sends a message.
    Dispatches to Agent-S (or current agent) via Celery task.
    """
    body = await request.json()
    parsed = parse_incoming_webhook(body)

    if not parsed:
        # Not a user message (could be status update, read receipt, etc.)
        return {"status": "ok"}

    logger.info(
        "whatsapp_incoming",
        from_phone=parsed["from_phone"],
        type=parsed["type"],
        text_length=len(parsed.get("text", "")),
    )

    # Only process text messages for now
    if parsed["type"] != "text" or not parsed.get("text"):
        return {"status": "ok", "note": "non-text message ignored"}

    # Dispatch to Celery worker — non-blocking
    # The worker will determine which agent handles this based on conversation.current_agent
    handle_incoming_message_task.delay(
        phone=parsed["from_phone"],
        message=parsed["text"],
        wa_message_id=parsed["message_id"],
    )

    return {"status": "ok", "dispatched": True}


# ============================================================
# Zoho CRM Webhook (Optional — for real-time lead notifications)
# ============================================================

@router.post("/zoho/lead-created")
async def zoho_lead_created(request: Request):
    """
    Zoho CRM notification webhook.
    Triggered when a new lead is created in Zoho CRM.
    Alternative to polling (process_zoho_new_leads scheduled task).
    Set up in Zoho CRM > Settings > Automation > Webhooks.
    """
    try:
        body = await request.json()
        logger.info("zoho_webhook", event="lead_created", data=body)

        # Trigger Agent-S to process this specific lead
        from core.scheduler import process_zoho_new_leads_task
        process_zoho_new_leads_task.delay()

        return {"status": "ok"}
    except Exception as e:
        logger.error("zoho_webhook_failed", error=str(e))
        return {"status": "error", "message": str(e)}


# ============================================================
# Manual Trigger Endpoints (for testing)
# ============================================================

@router.post("/trigger/mine")
async def trigger_mining():
    """Manually trigger a mining cycle (for testing)."""
    from core.scheduler import mine_leads_task
    mine_leads_task.delay()
    return {"status": "mining_triggered"}


@router.post("/trigger/followups")
async def trigger_followups():
    """Manually trigger follow-up processing (for testing)."""
    from core.scheduler import process_followups_task
    process_followups_task.delay()
    return {"status": "followups_triggered"}
