"""
Document generator — creates formatted quotation PDFs.
Used by Agent-RM after GM approval to create formal quote documents.
Placeholder: uses simple HTML-to-string for now. Phase D7 will add proper PDF generation.
"""

import structlog
from datetime import datetime, timedelta

logger = structlog.get_logger()


def generate_quotation_text(
    customer_name: str,
    company_name: str,
    kva_rating: float,
    configuration_summary: str,
    price: float,
    gst: float,
    freight: float,
    total: float,
    payment_terms: str,
    delivery_weeks: str,
    validity_days: int = 30,
) -> str:
    """
    Generate quotation text for WhatsApp/email delivery.
    Full PDF generation will be added in Phase D7.
    """
    today = datetime.now().strftime("%d-%b-%Y")
    valid_until = (datetime.now() + timedelta(days=validity_days)).strftime("%d-%b-%Y")

    quote = f"""QUOTATION — Pai Kane Group
Power Engineering (India) Pvt. Ltd.
No. 58/A, Tuem Industrial Estate, Pernem, North Goa — 403512

Date: {today}
Valid Until: {valid_until}

To: {customer_name}
Company: {company_name}

PRODUCT: {kva_rating} kVA Diesel Generator Set
{configuration_summary}

PRICING (Ex-Works Goa):
Product Price: INR {price:,.0f}
GST (18%): INR {gst:,.0f}
Freight (Estimated): INR {freight:,.0f}
---
TOTAL: INR {total:,.0f}

PAYMENT TERMS: {payment_terms}
DELIVERY: {delivery_weeks} from confirmed order

SCOPE OF SUPPLY:
- DG Set with Engine, Alternator and Base Frame
- Acoustic Enclosure (Weather proof)
- AMF Control Panel
- AVM Pads
- Silencer with Bellows
- Lube Oil and Coolant (First Fill)
- MS Exhaust Pipe
- Earthing Conductor
- Control Cables
- Hot Air Exhaust Ducting

COMPLIANCE: CPCB IV+ Emission Norms

WARRANTY: 24 months or 3000 running hours (whichever is earlier)

Terms & Conditions apply. 
For queries contact: sales@paikanegroup.com

--- Pai Kane Group | Touching Lives ---"""

    logger.info("quotation_generated", customer=customer_name, kva=kva_rating)
    return quote
