"""
Email tool — SendGrid integration for formal outreach.
Used for: tender responses, formal quotes, follow-ups where WhatsApp is not appropriate.
"""

import structlog
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content
from core.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    to_name: str | None = None,
) -> dict:
    """Send an email via SendGrid."""
    if not settings.sendgrid_api_key:
        logger.warning("sendgrid_key_not_set")
        return {"status": "skipped", "reason": "SendGrid not configured"}

    try:
        message = Mail(
            from_email=Email(settings.sendgrid_from_email, settings.sendgrid_from_name),
            to_emails=To(to_email, to_name),
            subject=subject,
            html_content=Content("text/html", body_html),
        )

        sg = SendGridAPIClient(settings.sendgrid_api_key)
        response = sg.send(message)

        logger.info("email_sent", to=to_email, subject=subject, status=response.status_code)
        return {"status": "sent", "status_code": response.status_code}
    except Exception as e:
        logger.error("email_failed", to=to_email, error=str(e))
        return {"status": "failed", "error": str(e)}


def send_gm_approval_notification(
    gm_email: str,
    customer_name: str,
    company_name: str,
    kva: float,
    recommended_price: float,
    recommendation_id: str,
    dashboard_url: str = "http://localhost:8000",
) -> dict:
    """Send a deal approval notification to the GM."""
    subject = f"Deal Approval Required: {company_name} — {kva} kVA"
    body = f"""
    <h2>Deal Recommendation Awaiting Approval</h2>
    <table style="border-collapse: collapse; width: 100%;">
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Customer</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{customer_name} ({company_name})</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Requirement</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">{kva} kVA DG Set</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Recommended Price</strong></td>
            <td style="padding: 8px; border: 1px solid #ddd;">₹{recommended_price:,.0f}</td></tr>
    </table>
    <br>
    <a href="{dashboard_url}/dashboard/deals/{recommendation_id}" 
       style="background: #1B3A5C; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px;">
       Review & Approve
    </a>
    <br><br>
    <p style="color: #888;">— Pai Kane AI Sales System</p>
    """
    return send_email(gm_email, subject, body)
