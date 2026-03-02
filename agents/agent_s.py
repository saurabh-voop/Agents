"""
Agent-S: Lead Hunter — Mumbai Suburban, Construction Sector (Pilot)

10 Functions:
F1: FIND — Mine leads from News, RERA, Zoho, IndiaMART
F2: ENRICH — Find contact details via Apollo.io
F3: QUALIFY — Score and classify leads (0-100)
F4: WRITE CRM — Create/update in PostgreSQL + Zoho CRM
F5: OUTREACH — Generate and send personalized WhatsApp messages
F6: FOLLOW UP — Automated cadence (HOT: 48hrs/1wk/2wk, WARM: 1wk/2wk/1mo)
F7: RESPOND — Handle customer replies in real-time
F8: ESCALATE — Hand off to Agent-RM when quote/technical needed
F9: DEDUP — Check for duplicates before creating leads
F10: LOG — Audit trail of every action
"""

import json
import structlog
from datetime import datetime, timedelta
from sqlalchemy import text

from core.config import get_settings
from core.llm import call_llm_json, call_llm_simple, run_agent_loop
from core.conversation import (
    create_conversation, find_conversation_by_phone, get_conversation_history,
    add_message, update_current_agent, format_history_for_llm,
)
from core.escalation import create_escalation
from core.audit import log_activity
from core.memory import save_memory, build_memory_prompt
from database.connection import get_sync_engine
from tools.scraper import fetch_google_news, fetch_maharera_projects
from tools.search import search_construction_projects
from tools.enrichment import enrich_contact
from tools.zoho_crm import search_leads, create_lead as zoho_create_lead, update_lead as zoho_update_lead, search_leads_by_company
from tools.whatsapp import send_text_message
from tools.email_tool import send_email

logger = structlog.get_logger()
settings = get_settings()


def _load_agent_config(config_path: str) -> dict:
    """Load agent configuration from JSON file."""
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base_dir, config_path)
    with open(full_path, "r") as f:
        return json.load(f)


_AGENT_S_CONFIG = _load_agent_config("config/agent_configs/agent_s_r1.json")
AGENT_S_SYSTEM_PROMPT = _AGENT_S_CONFIG.get("system_prompt", "")


class AgentS:
    """Agent-S: Lead Hunter for Mumbai Suburban, Construction sector."""

    def __init__(self):
        self.agent_id = "agent_s_r1"
        self.region = settings.agent_s_region
        self.sector = settings.agent_s_sector
        self.location_filter = settings.agent_s_location_filter
        self.engine = get_sync_engine()

    # ============================================================
    # F1: FIND — Mine leads from multiple sources
    # ============================================================

    def run_mining_cycle(self) -> dict:
        """Full mining cycle: News + RERA + web search → qualify → enrich → store → outreach."""
        logger.info("mining_cycle_started", agent=self.agent_id)
        start = datetime.utcnow()

        # Collect raw signals from all sources in parallel concept (sequential here)
        raw_leads = []

        # Source 1: Google News RSS
        news_articles = fetch_google_news(
            query=f"construction project {self.location_filter} OR DG set OR diesel generator OR power backup {self.location_filter}"
        )
        if news_articles:
            news_leads = self._extract_leads_from_news(news_articles)
            raw_leads.extend(news_leads)

        # Source 2: MahaRERA
        rera_projects = fetch_maharera_projects(district=self.location_filter)
        if rera_projects:
            rera_leads = self._extract_leads_from_rera(rera_projects)
            raw_leads.extend(rera_leads)

        # Deduplicate, qualify, and process
        results = {"mined": len(raw_leads), "qualified": 0, "enriched": 0, "outreach_sent": 0}

        for lead in raw_leads:
            # F9: DEDUP
            if self._is_duplicate(lead.get("company_name", "")):
                continue

            # F3: QUALIFY
            qualified = self._qualify_lead(lead)
            if not qualified or qualified.get("lead_score", 0) < 15:
                continue

            # F2: ENRICH (if no contact details)
            if not lead.get("phone") and not lead.get("email"):
                contact = enrich_contact(lead.get("company_name", ""))
                if contact and not contact.get("error"):
                    lead["contact_name"] = contact.get("name", "")
                    lead["phone"] = contact.get("phone", "")
                    lead["email"] = contact.get("email", "")
                    qualified["needs_contact_enrichment"] = False
                    results["enriched"] += 1

            # Merge qualification into lead
            lead.update(qualified)

            # F4: WRITE CRM
            lead_id = self._save_lead(lead)
            results["qualified"] += 1

            # F5: OUTREACH (if has contact and score >= 40)
            if lead.get("phone") and lead.get("lead_score", 0) >= 40:
                self._send_outreach(lead, lead_id)
                results["outreach_sent"] += 1

        elapsed_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
        log_activity(
            agent=self.agent_id, action="mining_cycle_completed",
            details=results, processing_time_ms=elapsed_ms,
        )
        logger.info("mining_cycle_completed", **results, elapsed_ms=elapsed_ms)
        return results

    def _extract_leads_from_news(self, articles: list[dict]) -> list[dict]:
        """Use LLM to extract structured lead data from news articles."""
        articles_text = "\n\n".join([
            f"Title: {a['title']}\nSource: {a['source']}\nSummary: {a.get('summary', '')}\nLink: {a['link']}"
            for a in articles[:20]
        ])

        prompt = """Extract potential DG set leads from these news articles about Mumbai construction.
For each relevant article, return: company_name, project_name, location, project_type, 
estimated_scale, news_source, news_title, news_url, dg_relevance.
Every construction project needs DG sets (temporary power during construction, permanent backup after).
Return a JSON array. If no relevant leads, return []."""

        try:
            result = call_llm_json(prompt, f"Articles:\n\n{articles_text}")
            if isinstance(result, list):
                for lead in result:
                    lead["source"] = "news"
                return result
        except Exception as e:
            logger.error("news_extraction_failed", error=str(e))
        return []

    def _extract_leads_from_rera(self, projects: list[dict]) -> list[dict]:
        """Convert RERA project data into lead format."""
        leads = []
        for p in projects:
            leads.append({
                "source": "rera",
                "company_name": p.get("developer", ""),
                "project_name": p.get("project_name", ""),
                "rera_number": p.get("rera_number", ""),
                "location": p.get("location", self.location_filter),
                "project_type": p.get("type", "residential"),
                "requirement_text": f"RERA registered project: {p.get('project_name', '')} by {p.get('developer', '')}. Under construction in {p.get('location', '')}.",
            })
        return leads

    # ============================================================
    # F3: QUALIFY — Score and classify leads
    # ============================================================

    def _qualify_lead(self, lead: dict) -> dict | None:
        """Use LLM to qualify and score a lead."""
        prompt = f"""Qualify this lead for Pai Kane Group (DG set manufacturer).
Region: Mumbai Suburban. Sector: Construction.

Scoring (0-100):
+25 specific kVA requirement, +20 timeline/urgency, +15 construction sector,
+10 Mumbai Suburban, +10 has phone, +5 has email, +15 asked for price, +5 company name,
-10 vague, -30 spam/irrelevant.

Return JSON: {{
"purchase_type": "PURCHASE|BIDDING|UNKNOWN",
"temperature": "HOT|WARM|COLD",
"project_type": "NEW_PROJECT|EXPANSION|REPLACEMENT|UNKNOWN",
"segment": "construction|commercial|industrial|hospital|residential|other",
"lead_score": 0-100,
"estimated_kva": number or 0,
"priority_action": "immediate_outreach|standard_outreach|needs_enrichment|low_priority",
"needs_contact_enrichment": true|false,
"reasoning": "1-2 sentences"
}}"""

        lead_info = f"""Company: {lead.get('company_name', 'Unknown')}
Project: {lead.get('project_name', 'Unknown')}
Location: {lead.get('location', 'Unknown')}
Source: {lead.get('source', 'Unknown')}
Phone: {lead.get('phone', 'None')}
Email: {lead.get('email', 'None')}
Requirement: {lead.get('requirement_text', 'None')}"""

        try:
            return call_llm_json(prompt, lead_info)
        except Exception as e:
            logger.error("qualification_failed", error=str(e), company=lead.get("company_name"))
            return None

    # ============================================================
    # F9: DEDUP — Check for duplicates
    # ============================================================

    def _is_duplicate(self, company_name: str) -> bool:
        """Check if company already exists in our database."""
        if not company_name:
            return False

        query = text("""
            SELECT COUNT(*) FROM leads 
            WHERE LOWER(company_name) = LOWER(:company) 
            AND deleted_at IS NULL
        """)
        with self.engine.connect() as conn:
            count = conn.execute(query, {"company": company_name}).scalar()
        return count > 0

    # ============================================================
    # F4: WRITE CRM — Save to PostgreSQL + Zoho
    # ============================================================

    def _save_lead(self, lead: dict) -> str | None:
        """Save qualified lead to PostgreSQL and sync to Zoho CRM."""
        query = text("""
            INSERT INTO leads 
            (customer_name, company_name, phone, email, location_city, location_state,
             source, source_reference, requirement_text, purchase_type, temperature,
             project_type, segment, lead_score, estimated_kva, status, region, created_by)
            VALUES 
            (:name, :company, :phone, :email, :city, 'Maharashtra',
             :source, :ref, :req, :purchase, :temp, :project, :segment, 
             :score, :kva, :status, :region, :agent)
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {
                    "name": lead.get("contact_name", "Unknown"),
                    "company": lead.get("company_name", "Unknown"),
                    "phone": lead.get("phone", ""),
                    "email": lead.get("email", ""),
                    "city": lead.get("location", self.location_filter),
                    "source": lead.get("source", "unknown"),
                    "ref": lead.get("project_name", lead.get("rera_number", "")),
                    "req": lead.get("requirement_text", ""),
                    "purchase": lead.get("purchase_type", "UNKNOWN"),
                    "temp": lead.get("temperature", "COLD"),
                    "project": lead.get("project_type", "UNKNOWN"),
                    "segment": lead.get("segment", "construction"),
                    "score": lead.get("lead_score", 0),
                    "kva": lead.get("estimated_kva", 0),
                    "status": "needs_enrichment" if lead.get("needs_contact_enrichment") else "qualified",
                    "region": self.region,
                    "agent": self.agent_id,
                })
                conn.commit()
                row = result.fetchone()
                lead_id = str(row[0]) if row else None

                # Also create in Zoho CRM (async — don't block on failure)
                try:
                    zoho_create_lead({
                        "Company": lead.get("company_name", ""),
                        "Last_Name": lead.get("contact_name", "Unknown"),
                        "Phone": lead.get("phone", ""),
                        "Email": lead.get("email", ""),
                        "City": lead.get("location", ""),
                        "State": "Maharashtra",
                        "Lead_Source": lead.get("source", ""),
                        "Description": lead.get("requirement_text", ""),
                        "Lead_Status": "Qualified",
                        "Pai_Kane_Score": lead.get("lead_score", 0),
                        "Lead_Temperature": lead.get("temperature", "Cold").capitalize(),
                        "DG_kVA_Requirement": lead.get("estimated_kva") or None,
                    })
                except Exception as ze:
                    logger.warning("zoho_sync_failed", error=str(ze))

                log_activity(
                    agent=self.agent_id, action="lead_created",
                    lead_id=lead_id,
                    details={
                        "company": lead.get("company_name"),
                        "score": lead.get("lead_score"),
                        "source": lead.get("source"),
                        "temperature": lead.get("temperature"),
                    },
                )
                return lead_id
        except Exception as e:
            logger.error("save_lead_failed", error=str(e))
            return None

    # ============================================================
    # F5: OUTREACH — Generate and send personalized messages
    # ============================================================

    def _send_outreach(self, lead: dict, lead_id: str | None) -> None:
        """Generate personalized outreach and send via WhatsApp."""
        prompt = """Generate a personalized WhatsApp outreach message for this construction lead.
Rules: Under 150 words. Professional but warm. Reference their project. 
Highlight 1-2 advantages (CPCB IV+ compliant, 2-3 week delivery for 25-160 kVA, 
15000 sets/year capacity, ex-works Goa pricing).
Ask ONE discovery question. Sign off as Pai Kane Group.
NEVER mention price in INR. NEVER use emojis. Format for WhatsApp."""

        lead_context = f"""Company: {lead.get('company_name')}
Contact: {lead.get('contact_name', 'Sir/Madam')}
Project: {lead.get('project_name', 'Not specified')}
Location: {lead.get('location')}
Source: {lead.get('source')}
Segment: {lead.get('segment')}
Temperature: {lead.get('temperature')}
Estimated kVA: {lead.get('estimated_kva', 'Unknown')}"""

        message = call_llm_simple(prompt, lead_context, temperature=0.7, max_tokens=300)

        if not message:
            return

        # Create conversation + send WhatsApp
        phone = lead.get("phone", "")
        if phone:
            conv_id = create_conversation(
                customer_phone=phone,
                customer_name=lead.get("contact_name", "Unknown"),
                company_name=lead.get("company_name", "Unknown"),
                region=self.region,
            )
            add_message(conv_id, self.agent_id, message, delivery_status="queued")

            try:
                wa_result = send_text_message(phone, message)
                # Update delivery status
                with self.engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE messages SET delivery_status = 'sent',
                        channel_message_id = :wa_id
                        WHERE conversation_id = :conv_id AND delivery_status = 'queued'
                        ORDER BY created_at DESC LIMIT 1
                    """), {"wa_id": wa_result.get("message_id"), "conv_id": conv_id})
                    conn.commit()
            except Exception as e:
                logger.error("whatsapp_send_failed", phone=phone, error=str(e))

            # Mark lead as Contacted in Zoho so agent doesn't re-contact next cycle
            zoho_id = lead.get("zoho_lead_id")
            if zoho_id:
                try:
                    zoho_update_lead(zoho_id, {
                        "Lead_Status": "Contacted",
                        "Last_Outreach_Date": datetime.utcnow().strftime("%Y-%m-%d"),
                    })
                except Exception:
                    pass

            # Save outreach fact to memory
            save_memory(lead.get("company_name", ""), self.agent_id, {
                "last_outreach_date": datetime.utcnow().strftime("%Y-%m-%d"),
                "outreach_channel": "whatsapp",
                "lead_score": lead.get("lead_score"),
                "temperature": lead.get("temperature"),
                "estimated_kva": lead.get("estimated_kva"),
                "segment": lead.get("segment"),
            })

            log_activity(
                agent=self.agent_id, action="outreach_sent",
                lead_id=lead_id, conversation_id=conv_id,
                details={"channel": "whatsapp", "message_length": len(message)},
            )

    # ============================================================
    # F7: RESPOND — Handle incoming customer replies
    # ============================================================

    def handle_customer_reply(self, phone: str, message: str, wa_message_id: str) -> dict:
        """Process an incoming WhatsApp message from a customer."""
        # Find existing conversation
        conv = find_conversation_by_phone(phone)
        if not conv:
            # New conversation from unknown number — create a minimal lead
            conv_id = create_conversation(phone, "Unknown", "Unknown", region=self.region)
            conv = {"id": conv_id, "current_agent": "agent_s"}
        else:
            conv_id = str(conv["id"])

        # Save incoming message
        add_message(conv_id, "customer", message, whatsapp_message_id=wa_message_id)

        # Load conversation history for context
        history = get_conversation_history(conv_id)
        history_text = format_history_for_llm(history)

        # Check if this should be escalated to RM
        escalation_check = self._should_escalate(message, history_text)
        if escalation_check.get("should_escalate"):
            return self._escalate_to_rm(conv_id, conv, message, escalation_check)

        # Generate response
        response_prompt = f"""You are Agent-S for Pai Kane Group, continuing a WhatsApp conversation.

Conversation so far:
{history_text}

Customer just said: {message}

Respond naturally. If they answered a discovery question, acknowledge and ask the next one.
Discovery sequence: kVA needed → timeline → number of sites → any special requirements.
If they ask for pricing or technical details, say "Let me connect you with our technical team" 
and I'll handle the escalation separately.
Keep response under 100 words. No emojis."""

        response = call_llm_simple(AGENT_S_SYSTEM_PROMPT, response_prompt, temperature=0.6, max_tokens=200)

        # Send response
        add_message(conv_id, self.agent_id, response, delivery_status="queued")
        try:
            send_text_message(phone, response)
        except Exception as e:
            logger.error("reply_send_failed", phone=phone, error=str(e))

        log_activity(
            agent=self.agent_id, action="customer_reply_processed",
            conversation_id=conv_id,
            details={"customer_message_length": len(message)},
        )
        return {"conversation_id": conv_id, "response_sent": True}

    def _should_escalate(self, message: str, history: str) -> dict:
        """Check if a customer message requires escalation to Agent-RM."""
        check = call_llm_json(
            """Analyze this customer message in the context of the conversation.
Should this be escalated to the technical team? 

Escalate if: customer asks for price/quote, asks technical specs (noise, fuel, dimensions),
mentions specific kVA + wants quotation, asks about compliance, or needs sizing help.

Return JSON: {"should_escalate": true/false, "reason": "pricing_request|technical_question|quote_request|sizing_help", "summary": "brief context"}""",
            f"Conversation:\n{history}\n\nLatest message: {message}",
        )
        return check if isinstance(check, dict) else {"should_escalate": False}

    # ============================================================
    # F8: ESCALATE — Hand off to Agent-RM
    # ============================================================

    def _escalate_to_rm(self, conv_id: str, conv: dict, message: str, check: dict) -> dict:
        """Create escalation to Agent-RM and notify customer."""
        # Get lead data from DB
        lead_data = self._get_lead_for_conversation(conv_id)

        esc_id = create_escalation(
            from_agent=self.agent_id,
            to_agent="agent_rm",
            lead_id=lead_data.get("id") if lead_data else None,
            conversation_id=conv_id,
            priority=lead_data.get("temperature", "WARM") if lead_data else "WARM",
            reason=check.get("reason", "quote_request"),
            payload={
                "customer_name": conv.get("customer_name", "Unknown"),
                "company_name": conv.get("company_name", "Unknown"),
                "phone": conv.get("customer_phone", ""),
                "requirement_summary": check.get("summary", message),
                "conversation_id": conv_id,
            },
        )

        # Inform customer (seamless handoff)
        handoff_msg = "Our technical team will review your requirements and share a detailed configuration and quotation shortly. Thank you for your patience."
        add_message(conv_id, self.agent_id, handoff_msg)
        try:
            send_text_message(conv.get("customer_phone", ""), handoff_msg)
        except Exception:
            pass

        update_current_agent(conv_id, "agent_rm")
        return {"conversation_id": conv_id, "escalated": True, "escalation_id": esc_id}

    def _get_lead_for_conversation(self, conv_id: str) -> dict | None:
        """Get lead data associated with a conversation."""
        query = text("""
            SELECT l.* FROM leads l 
            JOIN conversations c ON c.zoho_lead_id = l.zoho_lead_id 
            WHERE c.id = :conv_id LIMIT 1
        """)
        with self.engine.connect() as conn:
            row = conn.execute(query, {"conv_id": conv_id}).fetchone()
            return dict(row._mapping) if row else None

    # ============================================================
    # F6: FOLLOW UP — Automated cadence
    # ============================================================

    def process_followups(self) -> dict:
        """Daily follow-up processing for all leads that need follow-up."""
        now = datetime.utcnow()
        results = {"checked": 0, "sent": 0}

        # Find leads needing follow-up
        query = text("""
            SELECT l.id, l.customer_name, l.company_name, l.phone, l.temperature,
                   l.follow_up_count, l.last_contacted_at, l.requirement_text
            FROM leads l
            WHERE l.status IN ('contacted', 'qualified')
            AND l.phone IS NOT NULL AND l.phone != ''
            AND l.region = :region
            AND l.deleted_at IS NULL
            AND (
                (l.temperature = 'HOT' AND l.last_contacted_at < :hot_cutoff)
                OR (l.temperature = 'WARM' AND l.last_contacted_at < :warm_cutoff)
                OR (l.temperature = 'COLD' AND l.last_contacted_at < :cold_cutoff)
            )
            AND l.follow_up_count < 3
        """)

        hot_cutoff = now - timedelta(days=2)
        warm_cutoff = now - timedelta(days=7)
        cold_cutoff = now - timedelta(days=14)

        with self.engine.connect() as conn:
            rows = conn.execute(query, {
                "region": self.region,
                "hot_cutoff": hot_cutoff,
                "warm_cutoff": warm_cutoff,
                "cold_cutoff": cold_cutoff,
            }).fetchall()

        for row in rows:
            lead = dict(row._mapping)
            results["checked"] += 1
            self._send_followup(lead)
            results["sent"] += 1

        log_activity(agent=self.agent_id, action="followups_processed", details=results)
        return results

    def _send_followup(self, lead: dict) -> None:
        """Generate and send a follow-up message."""
        followup_num = (lead.get("follow_up_count") or 0) + 1
        prompt = f"""Generate follow-up #{followup_num} WhatsApp message for this lead.
Follow-up 1: Add value — mention case study or delivery speed.
Follow-up 2: Offer factory visit or reference a similar project.
Follow-up 3: Final gentle check before closing.
Under 80 words. No emojis. Professional."""

        context = f"Company: {lead['company_name']}, Contact: {lead['customer_name']}, Temperature: {lead['temperature']}"
        message = call_llm_simple(prompt, context, temperature=0.7, max_tokens=150)

        if message and lead.get("phone"):
            try:
                send_text_message(lead["phone"], message)
                with self.engine.connect() as conn:
                    conn.execute(text("""
                        UPDATE leads SET follow_up_count = :count, 
                        last_contacted_at = NOW(), updated_at = NOW()
                        WHERE id = :id
                    """), {"count": followup_num, "id": lead["id"]})
                    conn.commit()
            except Exception as e:
                logger.error("followup_failed", lead_id=str(lead["id"]), error=str(e))

    # ============================================================
    # Zoho CRM inbound lead processing
    # ============================================================

    # Statuses that mean the lead has already been handled — agent must not re-contact
    _SKIP_STATUSES = {"Contacted", "Qualified", "Escalated", "Quote Sent", "Won", "Lost", "Do Not Contact", "Converted"}

    def process_zoho_inbound(self) -> dict:
        """Check for new leads in Zoho CRM and process them."""
        results = {"found": 0, "skipped_status": 0, "processed": 0}
        try:
            new_leads = search_leads("(State:equals:Maharashtra)")
            results["found"] = len(new_leads)

            for lead in new_leads:
                # Skip leads already handled (Contacted, Qualified, Won, Lost, etc.)
                zoho_status = lead.get("Lead_Status")
                if zoho_status and zoho_status in self._SKIP_STATUSES:
                    results["skipped_status"] += 1
                    continue

                lead_data = {
                    "source": "zoho_inbound",
                    "zoho_lead_id": lead.get("id", ""),
                    "company_name": lead.get("Company", ""),
                    "contact_name": f"{lead.get('First_Name', '')} {lead.get('Last_Name', '')}".strip(),
                    "phone": lead.get("Phone") or lead.get("Mobile") or "",
                    "email": lead.get("Email", ""),
                    "location": lead.get("City", self.location_filter),
                    "requirement_text": lead.get("Description", ""),
                }

                if self._is_duplicate(lead_data["company_name"]):
                    continue

                qualified = self._qualify_lead(lead_data)
                if qualified:
                    lead_data.update(qualified)
                    lead_id = self._save_lead(lead_data)

                    outreach_sent = False
                    if lead_data.get("phone") and lead_data.get("lead_score", 0) >= 40:
                        self._send_outreach(lead_data, lead_id)
                        outreach_sent = True

                    # Write score/temperature back to Zoho and mark as Contacted
                    try:
                        zoho_update_lead(lead["id"], {
                            "Lead_Status": "Contacted" if outreach_sent else "Qualified",
                            "Pai_Kane_Score": lead_data.get("lead_score", 0),
                            "Lead_Temperature": lead_data.get("temperature", "Cold").capitalize(),
                            "DG_kVA_Requirement": lead_data.get("estimated_kva") or None,
                        })
                    except Exception:
                        pass

                    results["processed"] += 1

        except Exception as e:
            logger.error("zoho_inbound_failed", error=str(e))

        return results

    def check_expiring_quotes(self) -> dict:
        """Check for quotes expiring within 5 days and send reminders."""
        # Placeholder — implemented in Phase D7
        return {"checked": 0, "reminders_sent": 0}
