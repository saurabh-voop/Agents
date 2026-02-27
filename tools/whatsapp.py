"""
WhatsApp Business API tool.
Sends messages via Meta Cloud API. Receives messages via webhook (see api/webhooks.py).
Primary customer communication channel for India market.
"""

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def send_text_message(to_phone: str, message: str) -> dict:
    """
    Send a text message via WhatsApp Business API.
    
    Args:
        to_phone: Customer phone with country code (e.g., "919876543210")
        message: Text message content
    
    Returns:
        {"message_id": "wamid.xxx", "status": "sent"} or error
    """
    # Normalize phone: remove +, spaces, dashes
    to_phone = to_phone.replace("+", "").replace(" ", "").replace("-", "")
    if not to_phone.startswith("91"):
        to_phone = "91" + to_phone

    url = f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }

    response = httpx.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    msg_id = data.get("messages", [{}])[0].get("id", "unknown")
    logger.info("whatsapp_sent", to=to_phone, message_id=msg_id)
    return {"message_id": msg_id, "status": "sent"}


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5))
def send_template_message(
    to_phone: str,
    template_name: str,
    language_code: str = "en",
    components: list | None = None,
) -> dict:
    """
    Send a pre-approved template message via WhatsApp Business API.
    Required for first-contact outreach (Meta policy — can't send freeform to unknown numbers).
    
    Args:
        to_phone: Customer phone with country code
        template_name: Approved template name (e.g., "paikane_intro")
        language_code: Template language
        components: Template variable substitutions
    """
    to_phone = to_phone.replace("+", "").replace(" ", "").replace("-", "")
    if not to_phone.startswith("91"):
        to_phone = "91" + to_phone

    url = f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
        },
    }
    if components:
        payload["template"]["components"] = components

    response = httpx.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    msg_id = data.get("messages", [{}])[0].get("id", "unknown")
    logger.info("whatsapp_template_sent", to=to_phone, template=template_name, message_id=msg_id)
    return {"message_id": msg_id, "status": "sent"}


def parse_incoming_webhook(body: dict) -> dict | None:
    """
    Parse an incoming WhatsApp webhook payload.
    Returns structured message data or None if not a user message.
    """
    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return None

        msg = messages[0]
        contact = value.get("contacts", [{}])[0]

        return {
            "message_id": msg.get("id"),
            "from_phone": msg.get("from"),
            "customer_name": contact.get("profile", {}).get("name", "Unknown"),
            "timestamp": msg.get("timestamp"),
            "type": msg.get("type"),
            "text": msg.get("text", {}).get("body", "") if msg.get("type") == "text" else "",
        }
    except (IndexError, KeyError) as e:
        logger.error("whatsapp_webhook_parse_failed", error=str(e))
        return None
