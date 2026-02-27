# Pai Kane Group — Agentic AI Sales System
## Complete System Guide

---

## Table of Contents
1. [What This System Does](#1-what-this-system-does)
2. [How Data Flows End to End](#2-how-data-flows-end-to-end)
3. [Infrastructure Files](#3-infrastructure-files)
4. [Database Layer](#4-database-layer)
5. [Core Layer — Shared Infrastructure](#5-core-layer--shared-infrastructure)
6. [Agents — The AI Brains](#6-agents--the-ai-brains)
7. [Tools — External Integrations](#7-tools--external-integrations)
8. [API Layer — HTTP Endpoints](#8-api-layer--http-endpoints)
9. [Config Files](#9-config-files)
10. [What We Have Achieved (With Proof)](#10-what-we-have-achieved-with-proof)
11. [What Is Remaining](#11-what-is-remaining)
12. [Next Steps in Priority Order](#12-next-steps-in-priority-order)

---

## 1. What This System Does

Pai Kane Group manufactures DG sets (diesel generators) at a factory in Goa and sells to
construction companies, hospitals, commercial buildings, and industrial clients across Maharashtra.

This system replaces/augments the human sales process with three AI agents that work around the clock:

```
NEW LEAD (from Google News, RERA, Zoho, WhatsApp)
    │
    ▼
[AGENT-S] Qualifies, scores, enriches, sends first WhatsApp
    │  customer asks for quote/technical details
    ▼
[AGENT-RM] Matches product catalog, builds BOM, checks compliance
    │  configuration ready
    ▼
[AGENT-GM] Prices the deal, checks margins + commodity risk
    │  sends email to GM with approve/reject link
    ▼
[YOU — GM] Review deal on dashboard, click Approve
    │
    ▼
Quote PDF generated → sent to customer via WhatsApp/Email
```

Everything runs inside Docker on your server. No manual intervention needed except
the final GM approval step.

---

## 2. How Data Flows End to End

### Step 1 — Lead Mining (Every 2 hours, 9AM–7PM, Mon–Sat)
- Celery Beat fires `mine_leads` task
- Agent-S calls Google News RSS for Mumbai construction stories
- Agent-S calls MahaRERA API for new registered projects
- OpenAI GPT-4o-mini extracts structured lead data from raw text
- LLM scores each lead 0–100 (kVA requirement = +25, timeline = +20, etc.)
- Apollo.io enriches company → finds contact name + phone + email
- Lead saved to PostgreSQL `leads` table
- Lead mirrored to Zoho CRM (so your sales team sees it there too)
- If score ≥ 40 and phone exists → WhatsApp message sent

### Step 2 — Customer Replies
- Customer replies on WhatsApp
- Meta sends webhook POST to `/webhooks/whatsapp`
- Celery task dispatched to Agent-S `handle_customer_reply`
- Agent-S checks conversation history, decides: keep qualifying OR escalate
- If customer asks for price/specs → escalation created in DB (Agent-S → Agent-RM)

### Step 3 — Technical Configuration (Every 30 seconds)
- Celery Beat fires `process_rm_escalations` every 30 seconds
- Agent-RM picks up escalation, extracts kVA from requirement text
- Queries `products` table for nearest matching DG set model
- Builds complete BOM (12-line item list)
- Validates CPCB-IV+ compliance, checks delivery timeline
- Saves `technical_configs` record
- Creates new escalation (Agent-RM → Agent-GM)

### Step 4 — Commercial Pricing (Every 30 seconds)
- Celery Beat fires `process_gm_escalations` every 30 seconds
- Agent-GM picks up escalation
- Calculates PEP price, dealer price, customer list price
- Fetches today's commodity prices (copper, steel, diesel)
- Looks up company's payment history in Zoho Books (good payer = better terms)
- Calls OpenAI to write 2–3 sentence recommendation reasoning
- Saves `deal_recommendations` record
- Sends email to GM (saurabh.salunkhe@paikane.com) via SendGrid

### Step 5 — Human GM Approval
- GM receives email with "Review & Approve" button
- Opens dashboard: `GET /dashboard/deals/pending`
- Reviews: BOM, commodity snapshot, margin, customer payment history
- Submits decision: approve / approve_with_modified_price / reject / escalate_to_cmd
- `POST /dashboard/deals/{id}/decide` records decision + triggers next action

---

## 3. Infrastructure Files

### `Dockerfile`
Builds the Docker image for both `app` and `worker` containers.
- Base: `python:3.12-slim`
- Installs system libs: `build-essential`, `libpq-dev` (PostgreSQL C driver)
- Installs Python packages from `requirements.txt`
- Sets `PYTHONPATH=/app` — **critical** — without this Celery workers can't find
  the `agents/`, `core/`, `tools/` packages
- Exposes port 8000 (FastAPI)

### `docker-compose.yml`
Defines and wires up 4 containers:

| Container | Image | Purpose |
|---|---|---|
| `paikane-postgres` | postgres:16-alpine | Primary database |
| `paikane-redis` | redis:7-alpine | Celery message broker + result backend |
| `paikane-app` | paikane-agents-app | FastAPI web server (port 8000) |
| `paikane-worker` | paikane-agents-worker | Celery worker + beat scheduler |

All containers share `paikane-net` bridge network so they can reach each other
by container name (e.g., `postgres:5432`, `redis:6379`).

The `app` and `worker` containers mount your local code folder at `/app` via
`volumes: - .:/app` — this means code changes on your machine instantly appear
inside the container without rebuilding.

### `.env`
All secrets live here. Never commit this file to git. Read by:
- `docker-compose.yml` via `env_file: .env`
- Python via `core/config.py` (Pydantic BaseSettings)

### `.env.example`
Template showing every variable the system needs. Safe to commit.
Copy to `.env` and fill in real values.

### `requirements.txt`
Python package list. Key packages:
- `fastapi` + `uvicorn` — web framework
- `celery[redis]` — task queue
- `sqlalchemy[asyncio]` + `asyncpg` — database (async for FastAPI)
- `psycopg2-binary` — database (sync for agents/Celery)
- `pydantic-settings` — config from .env
- `openai` — LLM calls
- `httpx` — HTTP client (Zoho, WhatsApp, Apollo)
- `tenacity` — retry logic for external API calls
- `structlog` — structured JSON logging
- `sendgrid` — email delivery
- `weasyprint` — PDF generation for quotes

---

## 4. Database Layer

### `database/init.sql`
Runs once when PostgreSQL container starts for the first time.
Creates 10 tables + seed data.

#### Table: `leads`
Every prospect the system tracks. Fields include:
- Identity: `customer_name`, `company_name`, `phone`, `email`
- Location: `location_city`, `location_state`, `region`
- Classification: `source`, `status`, `temperature` (HOT/WARM/COLD), `lead_score` (0-100)
- Requirement: `requirement_text`, `estimated_kva`, `purchase_type`, `segment`, `project_type`
- Tracking: `follow_up_count`, `last_contacted_at`, `zoho_lead_id`
- Metadata: `created_at`, `updated_at`, `deleted_at`

Status progression:
`new → qualified → needs_enrichment → contacted → escalated → quoted → won/lost`

#### Table: `conversations`
One row per customer. Links a phone number to all their messages.
Fields: `customer_phone`, `customer_name`, `company_name`, `current_agent`, `status`, `region`

#### Table: `messages`
Every message in every conversation — both sent and received.
Fields: `conversation_id`, `sender` (agent_s/agent_rm/agent_gm/customer),
`content`, `channel` (whatsapp/email), `delivery_status`, `channel_message_id`

#### Table: `escalations`
The inter-agent message queue. When Agent-S finishes qualifying, it inserts a row here.
Agent-RM polls this table every 30 seconds.
Fields: `from_agent`, `to_agent`, `lead_id`, `conversation_id`, `priority`,
`status` (pending → in_progress → completed), `payload` (JSONB), `reason`

#### Table: `technical_configs`
Agent-RM's output after matching a lead to a product.
Fields: `lead_id`, `kva_rating`, `phase`, `engine_make`, `engine_model`,
`alternator_make`, `alternator_model`, `controller`, `enclosure_type`, `panel_type`,
`sku`, `bom` (JSONB), `cpcb_iv_compliant`, `delivery_feasibility`, `is_standard`

#### Table: `deal_recommendations`
Agent-GM's pricing output. Awaits human GM approval.
Fields: `lead_id`, `config_id`, `price_sheet`, `price_tier`, `pep_price`, `dealer_price`,
`customer_price`, `recommended_price`, `margin_above_pep_pct`, `gst_amount`,
`freight_estimate`, `total_deal_value`, `commodity_snapshot` (JSONB),
`recommendation` (approve_at_list/approve_with_discount/escalate_to_cmd),
`reasoning`, `risk_level`, `gm_decision`, `gm_approved_price`, `gm_decided_at`

#### Table: `products`
DG set catalogue. Seeded with real Pai Kane products.
Fields: `sku`, `kva_rating`, `phase`, `engine_make`, `engine_model`, `alternator_make`,
`alternator_model`, `enclosure_type`, `panel_type`, `base_price_inr`, `pep_price`,
`dealer_price`, `customer_price`, `lead_time_weeks`, `active`

#### Table: `commodity_prices`
Daily snapshot of copper, steel, diesel prices.
Used by Agent-GM to adjust quote validity and flag cost risk.

#### Table: `payment_history`
Customer payment records pulled from Zoho Books.
Used by Agent-GM to set payment terms (good payer = 50/50, new = 100% advance).

#### Table: `audit_log`
Every agent action recorded with timestamp, agent ID, action type, lead ID, and details.
Used for debugging, compliance, and the dashboard activity feed.

### `database/connection.py`
Two database connections:
- **Async** (`get_db`): Used by FastAPI routes. Non-blocking, handles concurrent requests.
- **Sync** (`get_sync_engine`): Used by agents and Celery tasks. Simple synchronous SQLAlchemy.

---

## 5. Core Layer — Shared Infrastructure

Every file in `core/` is used by multiple agents. They provide shared services
so each agent doesn't duplicate logic.

### `core/config.py`
**Single source of truth for all settings.**

Uses Pydantic `BaseSettings` — reads values from environment variables (and `.env` file).
Cached with `@lru_cache` so `.env` is read exactly once at startup.

Key settings groups:
- Database URLs (async + sync)
- Redis URL
- OpenAI key + model names
- Zoho CRM credentials (client_id, secret, refresh_token, org_id, base URL)
- Zoho Books credentials
- WhatsApp Business API credentials
- SendGrid (email)
- Apollo.io (contact enrichment)
- SerpAPI (web search)
- Commodity API
- Agent config (region, sector, location filter, follow-up timing)
- Notification emails (gm_email, engineering_email)

**How to use:** `from core.config import get_settings; settings = get_settings()`
Never read `os.environ` directly anywhere else.

### `core/llm.py`
**The OpenAI wrapper. Three functions used by agents:**

#### `call_llm(messages, tools, model, temperature, max_tokens)`
Raw call to OpenAI Chat Completions API. Returns the full response including
any tool calls the model wants to make. Retries up to 3 times on failure.
Logs: model used, tokens consumed, latency, whether tools were called.

#### `run_agent_loop(system_prompt, user_message, tools_spec, tool_handlers, max_iterations=10)`
**The core agentic pattern.** This is what makes agents "agentic" rather than just
one-shot LLM calls.

How it works:
```
1. Send system_prompt + user_message + available tools to OpenAI
2. OpenAI responds: either plain text (done) OR tool_calls (needs to act)
3. If tool_calls:
     - Execute each tool function in Python
     - Append result to messages as role="tool"
     - Send back to OpenAI
4. Repeat until OpenAI gives text response (no more tool calls)
5. Safety: stop after max_iterations to prevent infinite loops
```

Example: Agent-RM asks LLM to configure a 125 kVA DG set.
LLM calls `search_products(kva=125)` → Python queries DB → result fed back →
LLM calls `check_compliance(sku="PK-CUM-125-3p")` → Python returns True →
LLM calls `calculate_delivery(city="Mumbai")` → Python returns 3 weeks →
LLM gives final text: "Configured: Cummins 125 kVA, 3 weeks delivery" → done.

#### `call_llm_simple(system_prompt, user_message)` → `str`
One-shot text generation. No tools. Used for: message drafting, reasoning text,
simple extractions.

#### `call_llm_json(system_prompt, user_message)` → `dict | list`
One-shot call that expects JSON output. Strips markdown backticks, parses JSON.
Used for: lead qualification (structured scoring), data extraction from scraped text.

### `core/schemas.py`
Pydantic data models defining the shape of data passed between components.
Acts as a contract — enforces required fields and types.

Key schemas:
- `LeadCreate` / `LeadResponse` — for creating and reading leads
- `EscalationCreate` — for Agent-S → Agent-RM → Agent-GM handoffs
- `TechnicalConfigResponse` — Agent-RM's output
- `DealRecommendationResponse` — Agent-GM's output
- `GMDecision` — body of dashboard approve/reject POST

### `core/escalation.py`
**The inter-agent message queue.** Three functions:

- `create_escalation(from_agent, to_agent, lead_id, payload, priority, reason)`
  Inserts a row in the `escalations` table. Called by Agent-S and Agent-RM.

- `pick_up_escalation(agent_name)`
  Atomically claims one pending escalation for an agent.
  Uses `SELECT ... FOR UPDATE SKIP LOCKED` — prevents two workers claiming the same job.
  Returns the escalation row, or `None` if nothing pending.

- `complete_escalation(id, result)`
  Marks escalation as completed, stores result payload.

### `core/conversation.py`
Manages conversation threads. Each lead has one conversation that all agents
share (they can all see the full history).

Functions:
- `create_conversation(phone, customer_name, company_name, region)` → conv_id
- `find_conversation_by_phone(phone)` → conversation row or None
- `get_conversation_history(conv_id)` → list of message rows
- `add_message(conv_id, sender, content, delivery_status, whatsapp_message_id)`
- `update_current_agent(conv_id, agent_name)` — tracks which agent "owns" this conversation now
- `format_history_for_llm(history)` → formatted string for LLM context

### `core/audit.py`
Single function: `log_activity(agent, action, lead_id, conversation_id, escalation_id, details, error_message, processing_time_ms)`
Writes to `audit_log` table. Every significant agent action calls this.
Powers the `/dashboard/activity` endpoint.

### `core/scheduler.py`
Two things in one file:

**1. Celery app configuration:**
- Broker: Redis (job queue)
- Backend: Redis (stores task results)
- Timezone: Asia/Kolkata
- Rate limit: 10 tasks/minute (protects external APIs)

**2. Beat schedule (cron jobs):**

| Job | When | What |
|---|---|---|
| `mine-leads` | Every 2h, 9AM–7PM, Mon–Sat | Agent-S mining cycle |
| `process-followups` | Daily 9AM, Mon–Sat | Send follow-up messages |
| `process-rm-escalations` | Every 30 seconds | Agent-RM picks up work |
| `process-gm-escalations` | Every 30 seconds | Agent-GM picks up work |
| `fetch-commodities` | Daily 6AM | Refresh commodity prices |
| `pipeline-review` | Monday 9AM | Weekly funnel summary |
| `check-expiring-quotes` | Daily 9:30AM, Mon–Sat | Nudge expiring quotes |
| `check-zoho-new-leads` | Every 5 minutes | Pull leads entered in Zoho manually |

**3. Task functions:**
Each `@app.task` function is what Celery actually executes.
They import the agent class and call a method. The lazy import (`from agents.agent_s import AgentS` inside the function) is intentional — avoids circular imports at module load time.

---

## 6. Agents — The AI Brains

### `agents/agent_s.py` — Lead Hunter
**10 documented functions (F1–F10)**

#### `run_mining_cycle()` — F1: FIND
Called by Celery every 2 hours. Sequence:
1. `fetch_google_news(query)` — RSS feed from Google News
2. `fetch_maharera_projects(district)` — RERA registered projects
3. For each raw signal: deduplicate → qualify → enrich → save → outreach

#### `_extract_leads_from_news(articles)` — F1 continued
Sends up to 20 news articles to GPT-4o-mini.
LLM extracts: `company_name`, `project_name`, `location`, `project_type`,
`estimated_scale`, `dg_relevance`.
Returns structured list.

#### `_qualify_lead(lead)` — F3: QUALIFY
Sends lead info to LLM with scoring rubric:
- +25 specific kVA requirement
- +20 timeline/urgency mentioned
- +15 construction sector
- +10 Mumbai Suburban location
- +10 has phone number
- +5 has email
- +15 asked for price
- -30 spam/irrelevant

Returns: `temperature`, `lead_score`, `segment`, `purchase_type`, `estimated_kva`,
`priority_action`, `reasoning`.

#### `_is_duplicate(company_name)` — F9: DEDUP
Simple SQL check: does this company already exist in `leads` table?
Case-insensitive. Skips if yes.

#### `_save_lead(lead)` — F4: WRITE CRM
Saves to PostgreSQL `leads` table.
Also calls `zoho_create_lead()` to mirror in Zoho CRM.
Wrapped in try/catch so Zoho failure doesn't block DB save.

#### `_send_outreach(lead, lead_id)` — F5: OUTREACH
Only called if: phone exists AND lead_score ≥ 40.
1. Calls GPT-4o-mini to write personalized WhatsApp message (<150 words)
2. Creates `conversations` record
3. Sends via WhatsApp Business API
4. Updates `messages` table with delivery status + WhatsApp message ID

Rules for outreach message:
- Reference their specific project
- Mention 1–2 Pai Kane advantages (CPCB-IV+, 2–3 week delivery, Goa factory)
- Ask ONE discovery question
- Never mention price in INR
- Never use emojis

#### `process_followups()` — F6: FOLLOW UP
Runs daily at 9AM. Finds leads that need follow-up:
- HOT leads: follow up every 48 hours (up to 3 times)
- WARM leads: every 7 days
- COLD leads: every 14 days

LLM generates contextual follow-up message (value-add, case study, factory visit offer,
or final check before closing).

#### `handle_customer_reply(phone, message, wa_message_id)` — F7: RESPOND
Called when a customer WhatsApps back. Sequence:
1. Find or create conversation by phone number
2. Save incoming message to DB
3. Check if message warrants escalation to Agent-RM
4. If yes → `_escalate_to_rm()` + send handoff message to customer
5. If no → generate response, send, log

#### `_should_escalate(message, history)` — judgment call
Asks LLM: should this go to Agent-RM?
Escalates if: price/quote request, technical specs question, specific kVA + wants quote,
compliance question, sizing help needed.

#### `_escalate_to_rm(...)` — F8: ESCALATE
Creates `escalations` row with `to_agent="agent_rm"`.
Sends customer a handoff message: "Our technical team will review..."
Updates conversation's `current_agent` to `agent_rm`.

#### `process_zoho_inbound()` — F10 (variant)
Polls Zoho CRM every 5 minutes for leads with `Lead_Status = New` in Maharashtra.
Qualifies and saves them to PostgreSQL. Sends WhatsApp outreach if score ≥ 40.
Updates Zoho lead status to "Qualified".

---

### `agents/agent_rm.py` — Technical Engineer

#### `process_pending_escalation()`
Called every 30 seconds by Celery. Picks up ONE escalation from queue.
If none: returns `{"processed": False, "reason": "no_pending_escalations"}`.
If found: calls `_build_configuration()`, saves, escalates to Agent-GM.

#### `_build_configuration(payload)`
1. Extracts kVA using regex first, falls back to LLM if regex fails
2. Queries `products` table for nearest matching product (kva_rating ≥ requested)
3. Runs `calculate_derating()` for altitude/temperature derating
4. Builds complete config dict: engine, alternator, enclosure, panel, SKU
5. Generates 12-item BOM
6. Sets compliance flags (all standard products are CPCB-IV+ compliant by default)

#### `_find_matching_product(kva)`
`SELECT * FROM products WHERE kva_rating >= :kva AND active = true ORDER BY kva_rating ASC LIMIT 1`
Always picks the smallest product that meets the requirement (cost optimization).

#### `_build_bom(product)`
Returns a 12-item list: DG set, alternator, acoustic enclosure, control panel,
AVM pads, silencer, lube oil (first fill), coolant (first fill), MS exhaust pipe,
earthing conductor, control cables, hot air exhaust ducting.

#### `_save_configuration(config, escalation)`
Writes to `technical_configs` table with all individual columns.
Returns the new `config_id`.

#### `_escalate_to_engineering(escalation, config)`
For non-standard requirements (kVA outside catalog range, special specs).
Sends email to `settings.engineering_email` via SendGrid.
Note: still proceeds gracefully if SendGrid key not configured.

---

### `agents/agent_gm.py` — Commercial Brain

#### `process_pending_escalation()`
Called every 30 seconds. Picks up pricing requests from Agent-RM.

#### `_build_deal_recommendation(config, payload, esc)`
The main commercial logic:

1. **Pricing** — reads PEP/dealer/customer prices from the config (which got them from the products table)
2. **Margin calc** — `calculate_margin(recommended_price, pep_price)`
3. **GST** — `calculate_gst(recommended_price)` → 18% GST
4. **Freight** — `estimate_freight(city)` → distance-based from Goa factory
5. **Commodities** — `get_commodity_snapshot()` → checks if copper/steel/diesel spiked today
6. **Payment history** — `get_customer_payment_history(company_name)` → Zoho Books lookup
   - Good existing customer → 50% advance, 50% on delivery
   - New customer → 100% advance
7. **Quote validity** — 30 days normally, reduced to 15 days if commodities volatile
8. **LLM reasoning** — 2–3 sentence explanation of the recommendation
9. **Auto-recommendation logic:**
   - Margin > 3% → `approve_at_list`
   - Margin 0–3% → `approve_with_discount`
   - Margin < 0% → `escalate_to_cmd` (below cost — needs CMD approval)

#### `_notify_gm(rec, rec_id)`
Sends approval email to `settings.gm_email` via SendGrid.
Email includes: customer, kVA, recommended price, "Review & Approve" button linking to dashboard.

#### `process_approval(rec_id, decision, approved_price, notes)`
Called when GM clicks approve/reject on dashboard.
Updates `deal_recommendations` table with decision + timestamp.
If approved → next step is quote delivery (currently returns `next_action: "deliver_quote"`).

#### `fetch_and_store_commodities()`
Calls commodity API, stores daily prices. Runs at 6AM daily.

#### `run_pipeline_review()`
Monday 9AM: counts HOT/WARM/COLD/quoted leads in active pipeline.
Logs to audit trail. (Email summary to GM — future enhancement.)

---

## 7. Tools — External Integrations

### `tools/zoho_crm.py`
OAuth2 authenticated. Token cached in memory, refreshed 60s before expiry.

Functions:
- `search_leads(criteria)` — COQL-style criteria search
- `get_lead(lead_id)` — single lead by Zoho ID
- `create_lead(lead_data)` — new lead in Zoho
- `update_lead(lead_id, update_data)` — update any field
- `search_leads_by_company(company_name)` — deduplication
- `search_leads_by_phone(phone)` — deduplication
- `create_quotation(quote_data)` — create quotation record in Zoho

### `tools/zoho_books.py`
- `get_customer_payment_history(company_name)` — searches Zoho Books for the company,
  returns: `existing_customer` (bool), `total_invoices`, `overdue_amount`, `payment_reliability`
  (good/fair/poor). Used by Agent-GM for payment terms decision.

### `tools/whatsapp.py`
Meta Business API integration.
- `send_text_message(phone, message)` — free-form text (within 24h window)
- `send_template_message(phone, template_name, params)` — pre-approved template
  (required for first contact, must be approved by Meta first)
- Gracefully returns `{"status": "skipped"}` if `WHATSAPP_ACCESS_TOKEN` is empty

### `tools/email_tool.py`
SendGrid integration.
- `send_email(to_email, subject, body_html, to_name)` — generic email
- `send_gm_approval_notification(gm_email, customer_name, company_name, kva, price, rec_id)` —
  formatted approval email with table layout and approve button
- Returns `{"status": "skipped"}` if `SENDGRID_API_KEY` is empty (no crash)

### `tools/calculator.py`
**Pure Python math. Never calls LLM. Always deterministic.**

- `calculate_pep(kva, bom_items)` — Price at Entry Point (your cost floor)
- `calculate_margin(selling_price, pep_price)` → `{"margin_pct": 18.5, "margin_inr": 45000}`
- `calculate_gst(price)` → `{"gst_amount": X, "total_with_gst": Y}` (18% GST)
- `calculate_deal_value(price, freight, accessories)` → `{"subtotal": X, "total_deal_value": Y}`
- `estimate_freight(to_location)` → freight cost based on distance from Goa factory
  (Mumbai Suburban ≈ ₹18,000 for <125 kVA)
- `calculate_derating(kva, altitude_m, ambient_temp_c)` → derated kVA for hot/high-altitude sites

### `tools/commodity.py`
- `fetch_commodity_prices()` — calls commodity API for copper, steel, HSD diesel
- `store_commodity_prices(prices)` — saves to `commodity_prices` table
- `get_commodity_snapshot()` — reads latest stored prices, compares to baseline,
  returns: `{"copper_impact": "none|moderate|significant", "overall_impact": ...}`

### `tools/search.py`
SerpAPI integration. Agent-S uses this to find construction project news.
- `search_construction_projects(query, location)` — returns list of search results

### `tools/scraper.py`
- `fetch_google_news(query)` — RSS-based Google News scraping (no API key needed)
- `fetch_maharera_projects(district)` — scrapes MahaRERA project listings

### `tools/enrichment.py`
Apollo.io integration.
- `enrich_contact(company_name)` — finds the best contact at a company:
  returns name, email, phone, LinkedIn URL, employee count, revenue.
  Used by Agent-S when lead has no contact details.

### `tools/pdf_reader.py`
- `extract_text_from_pdf(file_path)` — extracts text from uploaded PDF files.
  Used when customers send tender documents via WhatsApp.

### `tools/doc_generator.py`
- `generate_quote_pdf(deal_recommendation)` — generates branded PDF quote.
  Used after GM approves a deal. (Currently placeholder — full implementation pending.)

---

## 8. API Layer — HTTP Endpoints

### `api/main.py`
FastAPI application entry point.
- Registers routers: webhooks, dashboard, admin
- Startup: checks database connectivity
- `GET /` — system info
- `GET /health` — database + redis health check

### `api/webhooks.py` — Inbound Events

#### `GET /webhooks/whatsapp`
WhatsApp verification handshake. Meta calls this once when you register the webhook URL.
Checks `hub.verify_token` matches your `WHATSAPP_VERIFY_TOKEN` in `.env`.

#### `POST /webhooks/whatsapp`
Meta calls this for every incoming WhatsApp message.
Parses the payload, dispatches `handle_incoming_message_task` via Celery.
Returns 200 immediately (Meta requires fast response — actual processing is async).

#### `POST /webhooks/zoho/lead-created`
Zoho CRM calls this when a new lead is created there manually.
Triggers `process_zoho_new_leads_task` via Celery.

### `api/dashboard.py` — GM Approval Interface

#### `GET /dashboard/deals/pending`
Lists all deal recommendations with `gm_decision IS NULL`.
Returns: customer, company, kVA, recommended price, risk level, created timestamp.

#### `GET /dashboard/deals/{id}`
Full deal detail for one recommendation. Returns:
- Customer + company info
- Product BOM (itemized list)
- Pricing breakdown (PEP → dealer → list → recommended → GST → freight → total)
- Commodity snapshot (copper/steel/diesel impact today)
- Payment history summary
- Agent's reasoning text
- Recommendation: approve_at_list / approve_with_discount / escalate_to_cmd

#### `POST /dashboard/deals/{id}/decide`
Body: `{"decision": "approved", "approved_price": 850000, "notes": "Strategic customer"}`

Valid decisions:
- `approved` — accept recommended price
- `approved_modified` — accept with a different price (set `approved_price`)
- `rejected` — decline the deal
- `escalated_cmd` — needs CMD sign-off (below PEP price)

Triggers `process_gm_approval_task` via Celery which executes the decision.

#### `GET /dashboard/pipeline`
Live sales funnel: count of HOT/WARM/COLD leads, leads in each status,
total deal value in pipeline, conversion rates.

#### `GET /dashboard/activity`
Recent audit log entries. Shows what each agent has been doing.
Used for monitoring and debugging.

### `api/admin.py` — System Management

#### `GET /admin/status`
Health of all integrations: DB connected, Redis connected, OpenAI key set,
Zoho credentials set, WhatsApp token set, SendGrid key set.

#### `GET /admin/products`
Lists all products from the `products` table.
Shows SKU, kVA, engine/alternator specs, prices, lead time.

#### `POST /admin/trigger/{task}`
Manually fire any scheduled task without waiting for the cron timer.
Valid tasks: `mine`, `rm`, `gm`, `commodities`, `followups`
Returns: `{"status": "triggered", "task": "mine_leads"}`

---

## 9. Config Files

### `config/agent_configs/agent_s_r1.json`
Agent-S's personality and configuration. `r1` means Region 1 (Mumbai Suburban).

Contains:
- `system_prompt` — Agent-S's character, rules, tone, what to say/not say
- `lead_scoring_weights` — the numerical scores used in `_qualify_lead()`
- `follow_up_cadence` — HOT/WARM/COLD timing in days
- `region`, `sector`, `location_filter` — where to mine leads
- `outreach_rules` — message length limits, forbidden topics (price, emojis)

Agent-S loads this at startup: `_load_agent_config("config/agent_configs/agent_s_r1.json")`

To add a new region (e.g., Pune), copy this file to `agent_s_r2.json`, change the
location_filter and system_prompt, then create an `AgentS` instance pointing to it.

---

## 10. What We Have Achieved (With Proof)

### ✅ Full system starts and runs
**Proof:** Docker Compose output + curl results
```
docker compose ps → all 4 containers: healthy/running
curl http://localhost:8000/ → {"system":"Pai Kane Agentic AI Sales System","version":"1.0.0","status":"running"}
curl http://localhost:8000/health → {"status":"healthy","database":"healthy"}
```

### ✅ Database initialized with schema + seed data
**Proof:** PostgreSQL container healthy. `init.sql` runs automatically on first start.
All 10 tables created. Products seeded.
```
docker exec -it paikane-postgres psql -U paikane_admin -d paikane_agents -c "\dt"
→ 10 tables listed
```

### ✅ All 3 agents running on schedule
**Proof:** Worker logs
```
agent_rm.process_pending_escalations → succeeded in 0.005s: {'processed': False, 'reason': 'no_pending_escalations'}
agent_gm.process_pending_escalations → succeeded in 0.005s: {'processed': False}
```
Both agents poll every 30 seconds, find nothing to do (no leads yet), and return cleanly.
This is the correct behavior — they will process real work as soon as leads flow in.

### ✅ Celery Beat scheduler running on correct times
**Proof:** Worker logs show beat schedule active
```
[INFO/Beat] beat: Starting...
[INFO/Beat] Scheduler: Sending due task check-zoho-new-leads ...
[INFO/Beat] Scheduler: Sending due task process-rm-escalations ...
[INFO/Beat] Scheduler: Sending due task process-gm-escalations ...
```

### ✅ Zoho CRM network connectivity confirmed
**Proof:** `docker exec -it paikane-app curl -s https://accounts.zoho.in/oauth/v2/token -w "%{http_code}"` → `400`
(400 = bad request = server reached, just no credentials in the raw curl call)

### ✅ Python httpx can reach Zoho from inside Docker
**Proof:** `docker exec -it paikane-worker python -c "import httpx; r = httpx.post('https://accounts.zoho.in/oauth/v2/token'); print(r.status_code)"` → `500`
(500 = server error because no body sent = but TCP + SSL + DNS all work)

### ✅ Critical runtime bugs fixed (10+ SQL errors patched)
All agents had wrong SQL column names that would have caused crashes on first use.
Fixed:
- `agent_gm.py` — 10+ wrong INSERT column names in `_save_recommendation`
- `agent_rm.py` — wrong INSERT columns (compliance_check, delivery blobs → individual fields)
- `agent_s.py` — `whatsapp_message_id` → `channel_message_id`
- `init.sql` — missing `follow_up_count` and `last_contacted_at` columns in `leads`

### ✅ Docker/Python module import fixed
**Proof:** Before fix: `ModuleNotFoundError: No module named 'agents'` in every task.
After adding `ENV PYTHONPATH=/app` to Dockerfile and rebuilding → all tasks import correctly.

### ✅ Pydantic validation error fixed
Docker Compose injects `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` into the app container.
Pydantic rejected these as unknown fields and crashed at startup.
Fixed by adding `extra = "ignore"` to `Settings.Config`.

### ✅ System prompt moved to config (not hardcoded)
Agent-S's personality and instructions now live in `config/agent_configs/agent_s_r1.json`.
Editable without touching Python code.

### ✅ GM approval dashboard API built (5 endpoints)
Full REST API for the GM to review and approve deals without logging into a server.
Ready to connect a frontend (React, mobile app, or even just curl).

### ✅ Admin API built (4 endpoints)
Trigger any agent task manually, view product catalog, check integration health.

---

## 11. What Is Remaining

### 🔴 BLOCKER — OpenAI API Key not set
**Impact:** Every single agent call fails. Mining, qualification, outreach, response handling —
all require OpenAI. Nothing in the agent pipeline works without it.
**Fix:** Add real key to `.env` → `OPENAI_API_KEY=sk-...` → restart containers.

### 🟡 WhatsApp Business API not configured
**Impact:** Agents can't send or receive WhatsApp messages. This is the primary customer channel.
**Needed:** `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_BUSINESS_ACCOUNT_ID`, `WHATSAPP_ACCESS_TOKEN`
from Meta Business Manager. Also need to register webhook URL with Meta.
**Workaround:** System works via email (SendGrid) and manual Zoho entry in the meantime.

### 🟡 SendGrid not configured
**Impact:** GM approval emails not sent. Engineer escalation emails not sent.
**Needed:** `SENDGRID_API_KEY` from sendgrid.com. Also verify sender domain `paikanegroup.com`.
**Current behavior:** Email calls return `{"status": "skipped"}` — no crash, just silent.

### 🟡 Quote PDF delivery not implemented
**Impact:** After GM approves a deal, the system records the approval but doesn't
automatically send the quote to the customer.
**Location:** `agents/agent_gm.py` `process_approval()` — returns `next_action: "deliver_quote"`
but doesn't yet call `doc_generator.py` or send via WhatsApp/email.

### 🟡 Apollo.io not configured (contact enrichment)
**Impact:** Leads mined from news/RERA without contact details won't be enriched.
Score-40 threshold still works, but many leads will be `needs_enrichment` status.
**Needed:** `APOLLO_API_KEY`

### 🟡 SerpAPI not configured (web search)
**Impact:** Agent-S web search mining won't work. RERA scraping still works.
**Needed:** `SERPAPI_KEY`

### 🟡 Commodity API not configured
**Impact:** Agent-GM will use hardcoded baseline prices instead of live data.
Commodity risk flags won't fire.
**Needed:** `COMMODITY_API_KEY`

### 🟡 No frontend dashboard
**Impact:** GM approval requires curl commands or API tool. Not user-friendly.
**What exists:** Full REST API ready. Needs a simple web frontend.
**Options:** React/Next.js dashboard, or even a simple Retool/Appsmith instance.

### 🟢 WhatsApp message templates not created
**Impact:** First-contact messages require pre-approved Meta templates.
Without approved templates, can only message customers who contacted you first.
**Fix:** Submit templates in Meta Business Manager (takes 24–48 hours to approve).

### 🟢 Quote PDF template not designed
**Impact:** `tools/doc_generator.py` exists but has placeholder implementation.
Quotes are calculated correctly but can't be rendered to PDF yet.

### 🟢 `check_expiring_quotes()` not implemented
**Location:** `agent_s.py` line 601 — currently returns `{"checked": 0, "reminders_sent": 0}`.
Marked as "Phase D7" in code.

---

## 12. Next Steps in Priority Order

### Step 1 — Unblock everything (30 minutes)
Add OpenAI key to `.env`, restart containers, verify mining cycle runs end to end.
```bash
# Edit .env — add real OPENAI_API_KEY
docker compose restart app worker
curl -X POST http://localhost:8000/admin/trigger/mine
docker compose logs worker -f --tail=30
```
Expected: see `mining_cycle_started` → `llm_call` → `mining_cycle_completed` with counts.

### Step 2 — Test the full agent pipeline (1 hour)
Manually inject a test lead and trace it through all 3 agents:
```sql
-- Insert a test lead directly into DB
INSERT INTO leads (customer_name, company_name, phone, location_city, location_state,
  source, status, temperature, lead_score, estimated_kva, requirement_text, region, created_by)
VALUES ('Test Contact', 'ABC Builders Pvt Ltd', '+919876543210', 'Mumbai', 'Maharashtra',
  'manual', 'escalated', 'HOT', 80, 125,
  '125 kVA DG set needed for residential project, urgent', 'R1', 'manual');

-- Get the lead ID
SELECT id FROM leads WHERE company_name = 'ABC Builders Pvt Ltd';

-- Insert escalation to Agent-RM
INSERT INTO escalations (from_agent, to_agent, lead_id, priority, status, reason, payload)
VALUES ('agent_s', 'agent_rm', '<lead_id>', 'HOT', 'pending', 'quote_request',
  '{"customer_name": "Test Contact", "company_name": "ABC Builders Pvt Ltd",
    "phone": "+919876543210", "requirement_summary": "125 kVA DG set urgent",
    "conversation_id": null}');
```
Watch worker logs → Agent-RM should process → escalate to Agent-GM → GM notification.

### Step 3 — Configure WhatsApp (1–2 days)
1. Go to Meta Business Manager → WhatsApp → API Setup
2. Get Phone Number ID, Business Account ID, Access Token
3. Add to `.env`
4. Register webhook: `https://your-domain.com/webhooks/whatsapp`
5. Create and submit message templates (first-contact templates)
6. Test: send a WhatsApp to your business number and watch logs

### Step 4 — Configure SendGrid (1 hour)
1. Sign up at sendgrid.com
2. Verify sender domain `paikanegroup.com` (add DNS records)
3. Get API key → add to `.env`
4. Test: `curl -X POST http://localhost:8000/admin/trigger/gm`
   → Should receive approval email at saurabh.salunkhe@paikane.com

### Step 5 — Build simple GM dashboard frontend (3–5 days)
The API is ready. A minimal React page with:
- Table of pending deals
- Deal detail modal (BOM, pricing, commodity risk)
- Approve / Modify Price / Reject buttons

Or use Retool/Appsmith (no-code) pointed at your API for a faster prototype.

### Step 6 — Implement quote PDF + delivery (2 days)
- Design quote PDF template in `tools/doc_generator.py`
- Complete `process_approval()` in `agent_gm.py` to call doc generator + send to customer
- Test full cycle: mine → qualify → configure → price → approve → quote delivered

### Step 7 — Configure remaining APIs
- Apollo.io key → contact enrichment
- SerpAPI key → web mining
- Commodity API key → live price risk

### Step 8 — Production deployment
- Move from `localhost:8000` to a real server (VPS, AWS EC2, etc.)
- Set up HTTPS (needed for WhatsApp webhook)
- Configure proper secrets management (not just .env file)
- Set up log monitoring (papertrail, datadog, etc.)
- Remove `--reload` flag from uvicorn in docker-compose for production

---

*Generated: February 2026 | Pai Kane Group Agentic AI Sales System v1.0*
