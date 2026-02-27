"""
Zoho Books integration — read-only for agents.
Agent-GM reads: payment history, customer credit, item catalog.
"""

import httpx
import structlog
from tools.zoho_crm import _refresh_token
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


def _headers() -> dict:
    token = _refresh_token()
    return {"Authorization": f"Zoho-oauthtoken {token}"}


def get_customer_payment_history(customer_name: str) -> dict:
    """
    Look up a customer's payment track record in Zoho Books.
    Returns summary: total invoiced, total paid, outstanding, average days to pay.
    """
    if not settings.zoho_books_org_id:
        return {"error": "Zoho Books not configured"}

    try:
        url = f"{settings.zoho_books_api_base}/invoices"
        params = {
            "organization_id": settings.zoho_books_org_id,
            "customer_name": customer_name,
            "sort_column": "date",
            "sort_order": "D",
        }
        response = httpx.get(url, headers=_headers(), params=params, timeout=30)
        response.raise_for_status()
        invoices = response.json().get("invoices", [])

        if not invoices:
            return {"existing_customer": False, "total_invoices": 0}

        total_invoiced = sum(inv.get("total", 0) for inv in invoices)
        total_paid = sum(inv.get("payment_made", 0) for inv in invoices)
        outstanding = total_invoiced - total_paid
        paid_count = sum(1 for inv in invoices if inv.get("status") == "paid")

        return {
            "existing_customer": True,
            "total_invoices": len(invoices),
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "outstanding": outstanding,
            "paid_invoices": paid_count,
            "payment_reliability": "good" if paid_count > len(invoices) * 0.8 else "average" if paid_count > len(invoices) * 0.5 else "poor",
        }
    except Exception as e:
        logger.error("zoho_books_failed", customer=customer_name, error=str(e))
        return {"error": str(e)}


def get_item_catalog() -> list[dict]:
    """Fetch the item catalog from Zoho Books."""
    if not settings.zoho_books_org_id:
        return []

    try:
        url = f"{settings.zoho_books_api_base}/items"
        params = {"organization_id": settings.zoho_books_org_id, "per_page": 200}
        response = httpx.get(url, headers=_headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json().get("items", [])
    except Exception as e:
        logger.error("zoho_books_items_failed", error=str(e))
        return []
