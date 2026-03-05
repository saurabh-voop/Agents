# Pai Kane Agents — System Architecture

## High-Level Overview

```
Internet / Zoho CRM / WhatsApp
        ↓
   FastAPI (port 8000)          ← HTTP layer: webhooks, admin triggers, GM dashboard
        ↓
   Celery Worker + Beat          ← Background task engine + cron scheduler
        ↓
   3 Agents (S → RM → GM)        ← Agentic AI logic
        ↓
   PostgreSQL (port 5432)        ← Shared state: leads, conversations, escalations
        ↓
   Redis (port 6379)             ← Celery task queue + broker
```

---

## Infrastructure — Docker Compose (5 containers)

| Container | Image | Role |
|---|---|---|
| `paikane-app` | Python 3.12 | FastAPI HTTP server |
| `paikane-worker` | Python 3.12 | Celery worker + beat scheduler |
| `paikane-postgres` | PostgreSQL 16 | Persistent data store |
| `paikane-redis` | Redis 7 | Task queue broker |
| `paikane-adminer` | Adminer | DB browser UI (port 8080) |

App and worker share the same Docker image — same codebase, different entrypoint commands.

---

## No Agent Framework Used

**LangChain, LangGraph, CrewAI, AutoGen, LlamaIndex — none of these are used.**

The entire agentic engine is ~60 lines of custom code in `core/llm.py`:

```python
def run_agent_loop(system_prompt, user_message, tools_spec, tool_handlers):
    messages = [system, user]

    while iteration < max_iterations:
        response = client.chat.completions.create(
            model=model, messages=messages, tools=tools_spec
        )

        if no tool_calls:
            return response.content        # LLM is done

        # Execute all tool calls in PARALLEL
        with ThreadPoolExecutor() as executor:
            results = executor.map(run_tool, response.tool_calls)

        # Feed results back to LLM and repeat
        messages.append(tool_results)
```

### Why not LangChain?

| | LangChain | Our approach |
|---|---|---|
| Dependency weight | 50+ packages, 100MB+ | Just `openai` SDK |
| Debugging | Hidden layers, hard to trace | Every step is visible Python |
| Tool calling | Abstracted via decorators | Native OpenAI `tools` JSON spec |
| Control | Framework decides flow | We control every step |
| Parallel tools | Not built-in | `ThreadPoolExecutor` natively |
| Upgrades | Breaking changes every version | No framework to upgrade |

---

## Full Stack — Libraries

| Library | Version | Purpose |
|---|---|---|
| `fastapi` | 0.115 | HTTP server, webhooks, REST API |
| `celery` | 5.4 | Background task queue + cron scheduler |
| `redis` | 5.2 | Celery broker + result backend |
| `sqlalchemy` | 2.0 | ORM — async (FastAPI) + sync (Celery) |
| `pydantic-settings` | 2.7 | Config from `.env` with type validation |
| `openai` | 1.58 | LLM calls — works with Groq too |
| `httpx` | 0.28 | All outbound HTTP (Zoho, SerpAPI, WhatsApp, MahaRERA) |
| `beautifulsoup4` | 4.12 | HTML parsing for website scraping |
| `feedparser` | 6.0 | Google News RSS parsing |
| `tenacity` | 9.0 | Retry logic on API failures (3 attempts, exponential backoff) |
| `structlog` | 24.4 | Structured JSON logging |
| `sendgrid` | 6.11 | Outbound emails (outreach + GM approvals) |
| `playwright` | 1.49 | Headless browser (available, used for JS sites) |

---

## Layer 1 — Configuration (`core/config.py`)

- **Pydantic Settings** reads from `.env` + environment variables
- Single `Settings` class, `@lru_cache` singleton — loaded once, reused everywhere
- Never hardcoded secrets — everything from `.env`
- `DRY_RUN=true` flag — skips all CRM/WhatsApp/Email writes without changing code

Key setting groups: Database, Redis, LLM provider, Zoho CRM/Books, WhatsApp, SendGrid, Apollo, SerpAPI, Celery schedules.

---

## Layer 2 — Database (`PostgreSQL 16`, 11 tables)

### Tables

| Table | Purpose |
|---|---|
| `leads` | Every prospect: source, score, phone, email, status |
| `conversations` | All chat threads (shared by all 3 agents) |
| `messages` | Individual messages in a conversation |
| `escalations` | DB-based message queue between agents (S → RM → GM) |
| `agent_activity_log` | Audit trail of every agent action |
| `agent_memory` | Per-agent persistent key-value memory |
| `products` | DG set catalog (kVA, model, price, lead time) |
| `technical_configs` | Saved RM technical configurations per lead |
| `deal_recommendations` | GM pricing decisions |
| `outreach_templates` | WhatsApp/email message templates |
| `commodity_prices` | HSD, steel, copper prices (for cost calculations) |

### Inter-Agent Communication

No HTTP between agents. Agent-S writes a row to `escalations` table. Celery beat polls every 30 seconds — if a pending escalation exists, the target agent picks it up.

All 3 agents share the same `conversations` table — context is never lost on handoff between agents.

### Soft Deletes

All records use `deleted_at` timestamp. Nothing is hard-deleted. Dedup checks `WHERE deleted_at IS NULL`.

---

## Layer 3 — LLM Layer (`core/llm.py`)

### Provider Switching

Uses **OpenAI Python SDK v1.58** for both providers — Groq is 100% OpenAI-API compatible.

```
LLM_PROVIDER=groq    → api.groq.com/openai/v1  + Llama 3.3 70B  (currently active)
LLM_PROVIDER=openai  → api.openai.com          + GPT-4o          (production option)
```

### 3 Call Patterns

```python
call_llm()         # Raw call with optional tool calling — returns full message object
call_llm_simple()  # Text response only — used for message generation
call_llm_json()    # Expects JSON back — strips markdown fences, parses, returns dict
```

### Retry Logic

`@retry(stop_after_attempt(3), wait=wait_exponential(min=2, max=10))` on every LLM call via `tenacity`. Auto-retries on transient API errors.

---

## Layer 4 — Task Scheduling (`core/scheduler.py`)

**Celery** with **Redis** as broker.

| Task | Schedule | Purpose |
|---|---|---|
| `mine_leads` | Every 2 hours | Agent-S full mining cycle |
| `process_zoho_new_leads` | Every 5 min | Pull new Zoho CRM leads |
| `process_followups` | Daily 9am | Follow up on stale leads |
| `check_expiring_quotes` | Daily 10am | Alert GM on expiring quotes |
| `process_rm_escalations` | Every 30 sec | Agent-RM picks up escalations |
| `process_gm_escalations` | Every 30 sec | Agent-GM picks up escalations |
| `fetch_commodity_prices` | Daily | Update HSD/commodity prices |

---

## Layer 5 — The 3 Agents

### Agent-S (`agents/agent_s.py`) — Lead Mining + Qualification + Outreach

#### Mining Cycle (`run_mining_cycle`)

```
Source 1: Google News RSS (feedparser)
  → query: "Mumbai real estate construction project builder developer"
  → LLM extracts company names from 10 articles (call_llm_json)
  → source = "news"

Source 2: MahaRERA portal (httpx + BeautifulSoup)
  → All 4 Greater Mumbai districts: Mumbai Suburban, Mumbai City, Thane, Raigad
  → Scrapes last 15 pages per district (most recent RERA registrations)
  → Parses: developer name, RERA number, pincode, registration date
  → source = "rera"

Source 3: Zoho CRM inbound (separate task, every 5 min)
  → search_leads(State=Maharashtra) → Python-side status filtering
  → Skip @paikane.com / @paikanegroup.com emails (internal staff)
  → Skip leads where City is explicitly non-Mumbai
  → Clean "None None" name strings (Zoho sends "None" for empty fields)
  → Dedup by phone when company_name is empty
  → source = "zoho_inbound"
```

#### Per-Lead Processing Pipeline

```
1. DEDUP    → exact company_name match in leads table → skip if exists
2. QUALIFY  → call_llm_json() → score 0-100, temperature, segment, kVA estimate
             → skip if score < 15
3. ENRICH   → if no phone/email → find_developer_contact()
             → SerpAPI → official website → tel: links + visible text
             → SerpAPI snippet search for phone in search results
             → JustDial fallback (filters 8888888888 masked numbers)
4. SAVE     → insert into leads table + sync to Zoho CRM
5. OUTREACH → if phone AND score ≥ 40 → send WhatsApp message
             → update Zoho lead status to "Contacted"
```

#### Scoring Rubric (0–100)

```
+25  specific kVA requirement mentioned
+20  timeline / urgency signal
+15  construction sector
+10  Mumbai Suburban location
+10  has phone number
+5   has email
+15  asked for price
+5   company name present
-10  vague requirement
-30  spam / irrelevant
```

#### Incoming WhatsApp Replies

- Looks up lead by phone number
- Runs `run_agent_loop` with full conversation history
- 10+ tools available: calculator, load estimator, fuel calc, noise compliance, etc.
- If technical config needed → escalates to Agent-RM via `escalations` table

---

### Agent-RM (`agents/agent_rm.py`) — Technical Configuration

Picks up escalations from Agent-S. Runs `run_agent_loop` with 10 tools:

| Tool | File | Purpose |
|---|---|---|
| `noise_compliance` | `tools/noise_compliance.py` | CPCB-IV+ noise limits by zone type |
| `load_estimator` | `tools/load_estimator.py` | kVA from equipment list, reads product DB |
| `fuel_calculator` | `tools/fuel_calculator.py` | HSD consumption, tank size, runtime |
| `installation_advisor` | `tools/installation_advisor.py` | Plinth, ventilation, clearances |
| `company_lookup` | `tools/company_lookup.py` | MCA21 registration verification |
| `deal_analytics` | `tools/deal_analytics.py` | Historical pricing from past deals |
| `exchange_rate` | `tools/exchange_rate.py` | Live USD/INR from open.er-api.com |
| `search_construction_projects` | `tools/search.py` | SerpAPI web search |
| `pdf_reader` | `tools/pdf_reader.py` | Parse uploaded spec sheets |
| `calculator` | `tools/calculator.py` | Arithmetic |

Produces a `technical_configs` record → escalates to Agent-GM.

---

### Agent-GM (`agents/agent_gm.py`) — Pricing + Deal Approval

Receives escalation from Agent-RM. Runs `run_agent_loop`:
- Checks margin thresholds from `config/agent_gm.json`
- Calculates deal value, import component risk
- Sends approval email to GM (Saurabh) via SendGrid
- GM approves/rejects via REST API endpoints
- On approval → sends quote to customer via WhatsApp

---

## Layer 6 — Contact Enrichment Pipeline (`tools/scraper.py`)

3-step chain per developer:

```
Step 1: SerpAPI → find official website URL
         → httpx GET website → BeautifulSoup parse
         → Priority 1: <a href="tel:+91..."> links
                        digits = re.sub(r'[^\d]', '', href)
                        clean  = digits[-10:]          ← last 10 digits handles all prefix formats
                        match  = re.match(r'[6-9]\d{9}$', clean)
         → Priority 2: regex scan visible text for Indian mobile pattern
         → Extract email from page text

Step 2 (if no phone): SerpAPI snippet search
         → query: "{company}" Mumbai phone contact
         → scan title + snippet for [6-9]\d{9}
         → DuckDuckGo HTML fallback if no SerpAPI key

Step 3 (last resort): JustDial listing
         → filter out 8888888888 (JustDial's masked number)
```

### Phone Cleaning — Key Fix

`lstrip('+91')` strips individual characters from the set, not a prefix string.
`+919979974841`.lstrip('+91') → strips +, 9, 1 → `7974841` (only 7 digits — wrong).

Correct approach: `digits[-10:]` — take last 10 digits regardless of prefix format.
Handles: `+91xxxxxxxxxx`, `91xxxxxxxxxx`, `0xxxxxxxxxx`, `xxxxxxxxxx`.

---

## Layer 7 — External Integrations

| Service | Method | Usage |
|---|---|---|
| Zoho CRM | httpx REST + OAuth2 | Token refresh → search/create/update leads |
| WhatsApp | httpx Meta Graph API v21 | Send text + template messages |
| SerpAPI | httpx REST | Google search results (website + phone snippets) |
| MahaRERA | httpx + BeautifulSoup | Scrape RERA project registrations |
| Google News | feedparser RSS | Construction project news monitoring |
| SendGrid | sendgrid SDK | Outbound emails (outreach + GM approvals) |
| Apollo.io | httpx REST | Contact enrichment (optional, not active) |
| Groq | openai SDK | LLM inference (Llama 3.3 70B, currently active) |

---

## Layer 8 — API (`api/`, FastAPI)

```
GET  /health                       liveness probe
POST /webhooks/whatsapp            Meta webhook — verify token + inbound messages
POST /webhooks/zoho                Zoho CRM event hooks
GET  /admin/status                 system status, queue depths, agent info
POST /admin/trigger/mine           manually trigger Agent-S mining cycle
POST /admin/trigger/rm             manually trigger RM escalation processing
POST /admin/trigger/gm             manually trigger GM escalation processing
GET  /dashboard/pending            GM: list pending deal approvals
POST /dashboard/approve/{id}       GM: approve a deal → triggers quote to customer
POST /dashboard/reject/{id}        GM: reject a deal
GET  /admin/products               list DG set product catalog
```

---

## Key Design Decisions

| Decision | Reason |
|---|---|
| OpenAI SDK for Groq | No vendor lock-in — swap with 1 env var change |
| DB message queue (`escalations`) | No extra message broker needed, Celery polls |
| Shared `conversations` table | All agents share context, no handoff data loss |
| `digits[-10:]` phone cleaning | Handles all Indian phone prefix formats uniformly |
| Soft deletes (`deleted_at`) | Nothing lost, dedup checks `WHERE deleted_at IS NULL` |
| `pages_per_district=15` on RERA | Only newest projects, avoids scraping 590 pages |
| `DRY_RUN` flag | Full pipeline testable without side effects |
| Groq free tier | Zero LLM cost during development (200K tokens/day) |

---

## Data Flow — End to End

```
MahaRERA / Google News / Zoho CRM
         ↓
    Agent-S mines leads
         ↓
    LLM qualifies (score 0-100)
         ↓
    SerpAPI enriches contacts
         ↓
    Saved to leads table + Zoho CRM
         ↓
    score ≥ 40? → WhatsApp outreach sent
         ↓
    Customer replies on WhatsApp
         ↓
    Webhook → Agent-S handles conversation
         ↓
    Needs DG config? → escalation → Agent-RM
         ↓
    Agent-RM runs 10 tools, builds config
         ↓
    Escalation → Agent-GM
         ↓
    Agent-GM calculates price, emails GM
         ↓
    GM approves via /dashboard/approve
         ↓
    Quote sent to customer via WhatsApp
```
