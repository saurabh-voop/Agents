"""
Celery task scheduler — handles all async and scheduled agent work.
Mining cycles, follow-ups, commodity monitoring, pipeline reviews.
"""

from celery import Celery
from celery.schedules import crontab
from core.config import get_settings

settings = get_settings()

# Initialize Celery with Redis broker
app = Celery(
    "paikane",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

app.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=False,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Rate limiting for external APIs
    task_default_rate_limit="10/m",
)

# ============================================================
# Scheduled Tasks (Celery Beat)
# ============================================================
app.conf.beat_schedule = {
    # Agent-S: Mine leads every 2 hours during business hours (Mon-Sat, 9AM-7PM IST)
    "mine-leads": {
        "task": "agents.agent_s.mine_leads",
        "schedule": crontab(minute=0, hour="9,11,13,15,17,19", day_of_week="1-6"),
    },
    # Agent-S: Process follow-ups daily at 9AM IST
    "process-followups": {
        "task": "agents.agent_s.process_followups",
        "schedule": crontab(minute=0, hour=9, day_of_week="1-6"),
    },
    # Agent-RM: Poll for pending escalations every 30 seconds
    "process-rm-escalations": {
        "task": "agents.agent_rm.process_pending_escalations",
        "schedule": 30.0,
    },
    # Agent-GM: Poll for pending pricing requests every 30 seconds
    "process-gm-escalations": {
        "task": "agents.agent_gm.process_pending_escalations",
        "schedule": 30.0,
    },
    # Agent-GM: Fetch commodity prices daily at 6AM IST
    "fetch-commodities": {
        "task": "agents.agent_gm.fetch_commodity_prices",
        "schedule": crontab(minute=0, hour=6),
    },
    # Agent-GM: Weekly pipeline review — Monday 9AM IST
    "pipeline-review": {
        "task": "agents.agent_gm.pipeline_review",
        "schedule": crontab(minute=0, hour=9, day_of_week=1),
    },
    # Agent-S: Check for expiring quotes daily
    "check-expiring-quotes": {
        "task": "agents.agent_s.check_expiring_quotes",
        "schedule": crontab(minute=30, hour=9, day_of_week="1-6"),
    },
    # Agent-S: Process new Zoho CRM leads every 5 minutes
    "check-zoho-new-leads": {
        "task": "agents.agent_s.process_zoho_new_leads",
        "schedule": 300.0,
    },
}


# ============================================================
# Task Definitions (imported by workers)
# ============================================================

@app.task(name="agents.agent_s.mine_leads", bind=True, max_retries=2)
def mine_leads_task(self):
    """Run the full Agent-S mining cycle."""
    try:
        from agents.agent_s import AgentS
        agent = AgentS()
        result = agent.run_mining_cycle()
        return result
    except Exception as exc:
        self.retry(exc=exc, countdown=60)


@app.task(name="agents.agent_s.process_followups", bind=True, max_retries=1)
def process_followups_task(self):
    """Process daily follow-ups for all leads."""
    try:
        from agents.agent_s import AgentS
        agent = AgentS()
        result = agent.process_followups()
        return result
    except Exception as exc:
        self.retry(exc=exc, countdown=120)


@app.task(name="agents.agent_s.process_zoho_new_leads", bind=True, max_retries=2)
def process_zoho_new_leads_task(self):
    """Check for and process new leads from Zoho CRM."""
    try:
        from agents.agent_s import AgentS
        agent = AgentS()
        result = agent.process_zoho_inbound()
        return result
    except Exception as exc:
        self.retry(exc=exc, countdown=60)


@app.task(name="agents.agent_s.check_expiring_quotes")
def check_expiring_quotes_task():
    """Check for quotes about to expire and send follow-ups."""
    from agents.agent_s import AgentS
    agent = AgentS()
    return agent.check_expiring_quotes()


@app.task(name="agents.agent_rm.process_pending_escalations")
def process_rm_escalations_task():
    """Agent-RM picks up and processes pending escalations."""
    from agents.agent_rm import AgentRM
    agent = AgentRM()
    return agent.process_pending_escalation()


@app.task(name="agents.agent_gm.process_pending_escalations")
def process_gm_escalations_task():
    """Agent-GM picks up and processes pending pricing requests."""
    from agents.agent_gm import AgentGM
    agent = AgentGM()
    return agent.process_pending_escalation()


@app.task(name="agents.agent_gm.fetch_commodity_prices")
def fetch_commodity_prices_task():
    """Daily commodity price fetch and baseline comparison."""
    from agents.agent_gm import AgentGM
    agent = AgentGM()
    return agent.fetch_and_store_commodities()


@app.task(name="agents.agent_gm.pipeline_review")
def pipeline_review_task():
    """Weekly pipeline health assessment."""
    from agents.agent_gm import AgentGM
    agent = AgentGM()
    return agent.run_pipeline_review()


# One-off tasks triggered by webhooks or API calls
@app.task(name="agents.agent_s.handle_incoming_message")
def handle_incoming_message_task(phone: str, message: str, wa_message_id: str):
    """Process an incoming WhatsApp message from a customer."""
    from agents.agent_s import AgentS
    agent = AgentS()
    return agent.handle_customer_reply(phone, message, wa_message_id)


@app.task(name="agents.agent_gm.process_approval")
def process_gm_approval_task(recommendation_id: str, decision: str, approved_price: float | None, notes: str):
    """Process the GM's decision on a deal recommendation (post-dashboard action)."""
    from agents.agent_gm import AgentGM
    agent = AgentGM()
    return agent.process_approval(recommendation_id, decision, approved_price, notes)
