"""
Deal Analytics — Historical pricing intelligence from PostgreSQL.
Queries past deals to help Agent-GM price competitively and understand win/loss patterns.

No external APIs — reads from existing deal_recommendations and leads tables.
Used by Agent-GM to anchor recommendations with real historical data.
"""

import structlog
from sqlalchemy import text
from database.connection import get_sync_engine

logger = structlog.get_logger()


def get_segment_pricing_history(segment: str, kva_min: float = 0, kva_max: float = 9999) -> dict:
    """
    Get average winning prices and margins for a segment/kVA range from historical deals.

    Args:
        segment: 'construction' | 'commercial' | 'industrial' | 'hospital' | 'residential'
        kva_min: Minimum kVA (inclusive)
        kva_max: Maximum kVA (inclusive)

    Returns:
        {
            "segment": str,
            "kva_range": str,
            "total_deals": int,
            "won_deals": int,
            "conversion_rate_pct": float,
            "avg_win_price_inr": float | None,
            "avg_win_margin_pct": float | None,
            "avg_discount_from_list_pct": float | None,
            "typical_payment_terms": str | None,
            "insight": str
        }
    """
    engine = get_sync_engine()
    try:
        query = text("""
            SELECT
                COUNT(*) as total_deals,
                COUNT(*) FILTER (WHERE l.status = 'won') as won_deals,
                AVG(dr.recommended_price) FILTER (WHERE l.status = 'won') as avg_win_price,
                AVG(dr.margin_above_pep_pct) FILTER (WHERE l.status = 'won') as avg_win_margin,
                AVG(dr.discount_from_list_pct) FILTER (WHERE l.status = 'won') as avg_discount,
                MODE() WITHIN GROUP (ORDER BY dr.payment_terms) FILTER (WHERE l.status = 'won') as common_payment_terms
            FROM deal_recommendations dr
            JOIN leads l ON l.id = dr.lead_id
            WHERE l.segment = :segment
              AND dr.lead_id IN (
                  SELECT id FROM leads WHERE segment = :segment
                    AND estimated_kva BETWEEN :kva_min AND :kva_max
              )
              AND dr.gm_decision IS NOT NULL
        """)
        with engine.connect() as conn:
            row = conn.execute(query, {
                "segment": segment,
                "kva_min": kva_min,
                "kva_max": kva_max,
            }).fetchone()

        if not row or row[0] == 0:
            return {
                "segment": segment,
                "kva_range": f"{kva_min}–{kva_max} kVA",
                "total_deals": 0,
                "won_deals": 0,
                "conversion_rate_pct": 0,
                "avg_win_price_inr": None,
                "avg_win_margin_pct": None,
                "avg_discount_from_list_pct": None,
                "typical_payment_terms": None,
                "insight": f"No historical data yet for {segment} segment in {kva_min}–{kva_max} kVA range. Use list pricing as baseline.",
            }

        total = row[0]
        won = row[1] or 0
        conversion = round((won / total) * 100, 1) if total > 0 else 0

        insight_parts = []
        if row[2]:
            insight_parts.append(f"Avg win price ₹{row[2]:,.0f}")
        if row[3]:
            insight_parts.append(f"{row[3]:.1f}% margin")
        if row[4] and row[4] > 0:
            insight_parts.append(f"{row[4]:.1f}% avg discount given")
        if conversion > 0:
            insight_parts.append(f"{conversion}% conversion rate")

        return {
            "segment": segment,
            "kva_range": f"{kva_min}–{kva_max} kVA",
            "total_deals": int(total),
            "won_deals": int(won),
            "conversion_rate_pct": conversion,
            "avg_win_price_inr": round(row[2], 0) if row[2] else None,
            "avg_win_margin_pct": round(row[3], 2) if row[3] else None,
            "avg_discount_from_list_pct": round(row[4], 2) if row[4] else None,
            "typical_payment_terms": row[5],
            "insight": ". ".join(insight_parts) + "." if insight_parts else "No historical wins in this range yet.",
        }
    except Exception as e:
        logger.warning("segment_pricing_history_failed", error=str(e))
        return {
            "segment": segment,
            "kva_range": f"{kva_min}–{kva_max} kVA",
            "total_deals": 0,
            "won_deals": 0,
            "conversion_rate_pct": 0,
            "avg_win_price_inr": None,
            "avg_win_margin_pct": None,
            "avg_discount_from_list_pct": None,
            "typical_payment_terms": None,
            "insight": "Historical data unavailable.",
        }


def get_similar_deals(kva: float, segment: str, location: str = "") -> list[dict]:
    """
    Find recent comparable deals (±30% kVA, same segment) for pricing reference.

    Returns up to 5 most recent comparable deals with outcome.
    """
    kva_min = kva * 0.7
    kva_max = kva * 1.3

    engine = get_sync_engine()
    try:
        query = text("""
            SELECT
                l.company_name,
                l.location_city,
                l.segment,
                l.temperature,
                l.status as outcome,
                dr.recommended_price,
                dr.margin_above_pep_pct,
                dr.discount_from_list_pct,
                dr.gm_decision,
                dr.payment_terms,
                dr.created_at
            FROM deal_recommendations dr
            JOIN leads l ON l.id = dr.lead_id
            WHERE l.estimated_kva BETWEEN :kva_min AND :kva_max
              AND l.segment = :segment
              AND dr.gm_decision IS NOT NULL
            ORDER BY dr.created_at DESC
            LIMIT 5
        """)
        with engine.connect() as conn:
            rows = conn.execute(query, {
                "kva_min": kva_min,
                "kva_max": kva_max,
                "segment": segment,
            }).fetchall()

        if not rows:
            return []

        return [
            {
                "company": row[0],
                "city": row[1],
                "segment": row[2],
                "temperature": row[3],
                "outcome": row[4],
                "recommended_price_inr": round(row[5], 0) if row[5] else None,
                "margin_pct": round(row[6], 2) if row[6] else None,
                "discount_from_list_pct": round(row[7], 2) if row[7] else None,
                "gm_decision": row[8],
                "payment_terms": row[9],
            }
            for row in rows
        ]
    except Exception as e:
        logger.warning("similar_deals_failed", error=str(e))
        return []


def get_lost_deal_reasons(segment: str = None) -> dict:
    """
    Analyse why deals were lost — price, competition, timeline, or other.
    Agent-GM uses this to avoid repeating pricing mistakes.
    """
    engine = get_sync_engine()
    try:
        query = text("""
            SELECT
                COUNT(*) as lost_count,
                AVG(dr.margin_above_pep_pct) as avg_margin_at_loss,
                AVG(dr.discount_from_list_pct) as avg_discount_at_loss
            FROM deal_recommendations dr
            JOIN leads l ON l.id = dr.lead_id
            WHERE l.status = 'lost'
              AND (:segment IS NULL OR l.segment = :segment)
        """)
        with engine.connect() as conn:
            row = conn.execute(query, {"segment": segment}).fetchone()

        if not row or row[0] == 0:
            return {"lost_count": 0, "insight": "No lost deals recorded yet."}

        return {
            "lost_count": int(row[0]),
            "avg_margin_at_loss_pct": round(row[1], 2) if row[1] else None,
            "avg_discount_at_loss_pct": round(row[2], 2) if row[2] else None,
            "insight": (
                f"{row[0]} lost deals. Average margin was {row[1]:.1f}% at time of loss. "
                "If margin is already high at loss, price was not the issue."
                if row[1] else f"{row[0]} lost deals recorded."
            ),
        }
    except Exception as e:
        logger.warning("lost_deal_reasons_failed", error=str(e))
        return {"lost_count": 0, "insight": "Data unavailable."}
