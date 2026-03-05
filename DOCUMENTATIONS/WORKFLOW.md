# Pai Kane Agents — Detailed Workflow

## Overview

The system runs three AI agents in a linear escalation chain: **Agent-S → Agent-RM → Agent-GM**.
No agent framework is used. The entire agentic engine is custom Python (~60 lines in `core/llm.py`).

```
Lead Sources (RERA / News / Zoho)
          ↓
      Agent-S          ← mines, qualifies, enriches, outreaches, converses
          ↓ (escalation)
      Agent-RM         ← technical configuration, BOM, compliance checks
          ↓ (escalation)
      Agent-GM         ← commercial pricing, risk assessment, deal approval
          ↓
    Human GM           ← final approve / reject via dashboard
          ↓
  Customer gets quote  ← WhatsApp
```

All communication between agents is via the `escalations` database table.
Celery beat polls this table every 30 seconds — no HTTP calls between agents.

---

## Celery Beat Schedule

All tasks run inside the `paikane-worker` Docker container (file: `core/scheduler.py`).

| Task | Schedule | What it does |
|---|---|---|
| `mine_leads` | Every 2h, Mon–Sat, 9AM–7PM IST | Agent-S full mining + outreach cycle |
| `process_zoho_new_leads` | Every 5 min | Pull new Maharashtra leads from Zoho CRM |
| `process_followups` | Daily 9AM IST, Mon–Sat | Follow-up cadence for stale leads |
| `check_expiring_quotes` | Daily 9:30AM IST, Mon–Sat | Alert on quotes expiring soon |
| `process_rm_escalations` | Every 30 sec | Agent-RM picks up pending escalations |
| `process_gm_escalations` | Every 30 sec | Agent-GM picks up pending escalations |
| `fetch_commodity_prices` | Daily 6AM IST | Update HSD, steel, copper prices |
| `pipeline_review` | Monday 9AM IST | Weekly pipeline health summary email to GM |

---

## Stage 1 — Lead Mining (Agent-S)

**File:** `agents/agent_s.py` → `run_mining_cycle()`
**Trigger:** Celery beat every 2 hours

### Source 1: Google News RSS

```
feedparser → query: "Mumbai real estate construction project builder developer"
→ Fetches up to 30 articles (truncated to 10, summaries capped at 200 chars)
→ call_llm_json() extracts: company_name, project_name, location, segment, dg_relevance
→ source = "news"
```

### Source 2: MahaRERA Portal

```
httpx + BeautifulSoup → scrapes 4 districts × 15 pages each
Districts: Mumbai Suburban, Mumbai City, Thane, Raigad
Per project extracts: developer_name, rera_number, pincode, registration_date
→ source = "rera"
```

### Source 3: Zoho CRM Inbound

```
Celery task: every 5 min → process_zoho_inbound()
search_leads(State=Maharashtra) from Zoho API
Python-side filters:
  - Skip if Lead_Status in (Contacted, Qualified, Won, Lost, Do Not Contact, Escalated)
  - Skip if email ends in @paikane.com or @paikanegroup.com  ← internal staff
  - Skip if City is set AND doesn't contain "mumbai"          ← wrong region
  - Dedup by company_name (exact match, case-insensitive)
  - Fallback dedup by phone when company_name is empty
→ source = "zoho_inbound"
```

---

## Stage 2 — Per-Lead Processing Pipeline

For every raw lead from all 3 sources, these steps run in order:

### Step 1: Dedup

```python
_is_duplicate(company_name)
  → SELECT COUNT(*) FROM leads WHERE LOWER(company_name) = LOWER(:name) AND deleted_at IS NULL
  → skip if exists

_is_duplicate_by_phone(phone)  ← fallback when company_name is empty
  → SELECT COUNT(*) FROM leads WHERE phone = :phone AND deleted_at IS NULL
  → filters out 8888888888 (JustDial masked numbers)
```

### Step 2: Qualify (LLM)

```
call_llm_json() with lead data → returns structured JSON:
  - purchase_type:  PURCHASE | BIDDING | UNKNOWN
  - temperature:    HOT | WARM | COLD
  - project_type:   NEW_PROJECT | EXPANSION | REPLACEMENT | UNKNOWN
  - segment:        construction | commercial | industrial | hospital | residential | other
  - lead_score:     0–100
  - estimated_kva:  number
  - priority_action: immediate_outreach | standard_outreach | needs_enrichment | low_priority
  - reasoning:      1–2 sentences

→ skip if lead_score < 15
```

**Scoring Rubric (0–100):**

| Signal | Points |
|---|---|
| Specific kVA requirement mentioned | +25 |
| Timeline / urgency signal | +20 |
| Asked for price | +15 |
| Construction sector | +15 |
| Mumbai Suburban location | +10 |
| Has phone number | +10 |
| Has email | +5 |
| Company name present | +5 |
| Vague requirement | −10 |
| Spam / irrelevant | −30 |

### Step 3: Enrich (if no contact info)

```
enrich_contact(company_name, location)
  Step 1: SerpAPI → find official website URL
          → httpx GET → BeautifulSoup parse
          → Priority 1: <a href="tel:+91..."> links
                        digits = re.sub(r'[^\d]', '', href)
                        clean  = digits[-10:]   ← handles all Indian prefix formats
          → Priority 2: regex scan visible text for [6-9]\d{9}
          → Extract email from page text

  Step 2 (if no phone): SerpAPI snippet search
          → query: "{company}" Mumbai phone contact
          → scan title + snippet for Indian mobile pattern

  Step 3 (last resort): JustDial listing
          → filter out 8888888888
```

**Phone Cleaning — Why `digits[-10:]`:**
`lstrip('+91')` strips characters from the set `{'+','9','1'}` — so `+919979974841`
becomes `7974841` (only 7 digits — wrong). Using `digits[-10:]` always takes the last 10
digits regardless of prefix format (`+91`, `91`, `0`, raw).

### Step 4: Save to DB + Zoho

```
INSERT INTO leads (company_name, phone, email, lead_score, temperature, segment, ...)
  ON CONFLICT DO NOTHING

→ Sync to Zoho CRM: create_lead() or update_lead()
  Writes custom fields: Pai_Kane_Score, Lead_Temperature, DG_kVA_Requirement
```

### Step 5: Outreach Decision

```
score >= 25 AND (phone OR email)?
  YES → _send_outreach(lead)
  NO  → done (lead saved, no contact yet)
```

---

## Stage 3 — Outreach (`_send_outreach`)

**File:** `agents/agent_s.py` → `_send_outreach()` (lines 355–476)

### WhatsApp Channel (if phone exists)

```
1. call_llm_simple() → generate personalized WhatsApp message
   Prompt rules: under 150 words, no INR price, no emojis, one discovery question,
                 mention CPCB IV+, 2-3 week delivery, 15000 sets/year

2. create_conversation() → creates row in conversations table
   add_message() → queues message in messages table

3. send_text_message(phone, message) → Meta Graph API v21
   → 3 retries with exponential backoff (2–10 sec)
   → Updates messages.delivery_status = 'sent'

4. zoho_update_lead(zoho_id, {Lead_Status: "Contacted", Last_Outreach_Date: today})

5. save_memory(company_name, "agent_s", {last_outreach_date, outreach_channel, lead_score, ...})

6. log_activity(action="outreach_sent", channel="whatsapp")
```

### Email Channel (if email exists)

```
1. call_llm_simple() → generate formal email
   Format: "Subject: ..." + blank line + body (under 200 words)
   Rules: professional tone, reference project by name, CTA to schedule call

2. send_email(to_email, subject, body_html=body)
   → SendGrid API → from sales@paikanegroup.com

3. log_activity(action="outreach_sent", channel="email")
4. save_memory(company_name, "agent_s", {email_outreach_sent: today})
```

**Channel selection summary:**

| Score | Phone | Email | Action |
|---|---|---|---|
| ≥ 25 | ✓ | ✓ | WhatsApp + Email |
| ≥ 25 | ✓ | — | WhatsApp only |
| ≥ 25 | — | ✓ | Email only |
| < 25 | any | any | No outreach |

---

## Stage 4 — Customer Replies (Webhook → Agent-S)

**File:** `api/webhooks.py` → POST `/webhooks/whatsapp`
**File:** `agents/agent_s.py` → `handle_customer_reply()`

```
Customer replies on WhatsApp
    ↓
Meta sends POST to /webhooks/whatsapp
    ↓
parse_incoming_webhook(body) → extracts phone, message, wa_message_id
    ↓
Celery task: handle_incoming_message_task(phone, message, wa_message_id)
    ↓
find_conversation_by_phone(phone)
  → Found: load existing thread
  → Not found: create new conversation (unknown number)
    ↓
add_message(conv_id, "customer", message)
    ↓
get_conversation_history(conv_id) → format for LLM
    ↓
_should_escalate(message, history)?
  Escalate if:
    - customer asks for price/quote
    - asks technical specs (noise, fuel, dimensions)
    - mentions specific kVA + wants quotation
    - asks about compliance
    - needs sizing help
```

### If NOT escalating — Agent-S responds directly:

```
Discovery sequence: kVA needed → timeline → number of sites → special requirements
call_llm_simple() → under 100 words, no emojis
send_text_message(phone, response)
add_message(conv_id, "agent_s", response)
```

### If escalating to Agent-RM:

```
create_escalation(from="agent_s", to="agent_rm", priority=temperature, payload={...})
→ Writes row to escalations table: status='pending'

Sends customer handoff message:
"Our technical team will review your requirements and share a detailed
 configuration and quotation shortly. Thank you for your patience."

update_current_agent(conv_id, "agent_rm")
```

---

## Stage 5 — Technical Configuration (Agent-RM)

**File:** `agents/agent_rm.py` → `process_pending_escalation()`
**Trigger:** Celery beat every 30 seconds

```
pick_up_escalation("agent_rm")
  → SELECT * FROM escalations WHERE target_agent='agent_rm' AND status='pending' LIMIT 1
  → UPDATE status='processing'
    ↓
_build_configuration(payload)
  → run_agent_loop(system_prompt, user_message, tools_spec, tool_handlers)
```

### Agent-RM Tools (10 total)

| Tool | Purpose |
|---|---|
| `search_products(kva)` | Find matching DG set from products table |
| `check_noise_compliance(kva, zone_type)` | CPCB-IV+ noise limits by zone |
| `get_enclosure_recommendation(zone_type)` | Acoustic enclosure type needed |
| `estimate_load_from_equipment(equipment_list)` | kVA from equipment list |
| `calculate_fuel_consumption(kva, load_factor)` | Litres/hour at load |
| `calculate_tank_size(runtime_hours, consumption)` | Tank size for required runtime |
| `calculate_runtime(tank_litres, consumption)` | Hours of operation |
| `get_installation_requirements(kva)` | Site requirements, clearances |
| `get_plinth_dimensions(kva)` | Plinth size + reinforcement specs |
| `calculate_derating(rated_kva, altitude, temp)` | kVA derating for site conditions |

### Output — `technical_configs` record:

```json
{
  "kva_rating": 125,
  "model": "PKG-125-CPIII",
  "engine_make": "Cummins",
  "noise_zone": "residential",
  "enclosure": "acoustic",
  "fuel_consumption_lph": 25.0,
  "tank_size_litres": 300,
  "runtime_hours": 12,
  "plinth": "3500mm × 1800mm × 300mm",
  "delivery_weeks": "2-3",
  "pep_price": 875000,
  "dealer_price": 960000,
  "customer_price": 1050000,
  "is_standard": true
}
```

### Standard vs Non-Standard:

```
is_standard = true  → escalate to Agent-GM for pricing
is_standard = false → escalate_to_engineering()
                      sends email to engineering@paikane.com
                      human engineer takes over
```

---

## Stage 6 — Pricing + Deal Approval (Agent-GM)

**File:** `agents/agent_gm.py` → `process_pending_escalation()`
**Trigger:** Celery beat every 30 seconds

```
pick_up_escalation("agent_gm")
    ↓
_build_deal_recommendation(config, payload, esc)
  → run_agent_loop() — LLM runs through 11 tools in sequence:
```

### Agent-GM Tools (11 total)

| Tool | Purpose |
|---|---|
| `lookup_company_mca(name)` | Verify MCA21 registration, credit risk |
| `get_customer_payment_history(name)` | Check Zoho Books payment track record |
| `get_usd_inr_rate()` | Live USD/INR from open.er-api.com |
| `calculate_import_cost_impact(pep, engine_make)` | Adjust PEP for INR movement |
| `get_commodity_snapshot()` | HSD, steel, copper price impact today |
| `get_segment_pricing_history(segment, kva_min, kva_max)` | Historical price anchors |
| `get_similar_deals(kva, segment)` | Comparable won/lost deals |
| `calculate_margin(price, pep)` | Margin above PEP % |
| `calculate_gst(subtotal)` | GST @ 18% |
| `estimate_freight(to_location)` | Freight from Goa to customer site |
| `calculate_deal_value(price, freight)` | Total deal value incl. GST + freight |

### Deal Recommendation Output:

```json
{
  "recommended_price": 1050000,
  "margin_above_pep_pct": 20.0,
  "payment_terms": "50% advance, 50% on delivery",
  "quote_valid_until": "2026-04-05",
  "delivery_weeks": "2-3",
  "risk_level": "low",
  "recommendation": "approve_at_list",
  "reasoning": "..."
}
```

**Recommendation codes:**
- `approve_at_list` — margin healthy, standard approval
- `approve_with_discount` — thin margin, GM may want to adjust price
- `escalate_to_cmd` — below PEP, needs CMD sign-off

### GM Notification:

```
_save_recommendation() → INSERT INTO deal_recommendations
_notify_gm()          → send_gm_approval_notification()
                         → SendGrid email to saurabh.salunkhe@paikane.com
                         → HTML table: customer, kVA, price, margin, risk
                         → "Review & Approve" button links to /dashboard/deals/{id}
```

---

## Stage 7 — GM Decision (Dashboard)

**File:** `api/dashboard.py`

```
GET  /dashboard/pending          → List all pending deal recommendations
POST /dashboard/approve/{id}     → Approve deal (optionally override price)
POST /dashboard/reject/{id}      → Reject deal (notes required)
```

### On Approval:

```
process_approval(rec_id, "approved", approved_price, notes)
    ↓
UPDATE deal_recommendations SET status='approved', approved_price=...
    ↓
Generate quote message via LLM
    ↓
send_text_message(customer_phone, quote_message)  ← WhatsApp quote to customer
    ↓
zoho_update_lead(zoho_id, {Lead_Status: "Quote Sent"})
    ↓
save_memory(company_name, "agent_gm", {last_deal_date, recommended_price, margin_pct, ...})
```

### On Rejection:

```
UPDATE deal_recommendations SET status='rejected'
→ Notes saved for Agent-RM context
→ No customer message sent
```

---

## Stage 8 — Follow-Up Cadence

**File:** `agents/agent_s.py` → `process_followups()`
**Trigger:** Daily 9AM IST, Mon–Sat

Picks up leads in status `contacted` or `qualified` where `follow_up_count < 3`:

| Temperature | Follow-up interval | Max follow-ups |
|---|---|---|
| HOT | Every 2 days | 3 |
| WARM | Every 7 days | 3 |
| COLD | Every 14 days | 3 |

**Follow-up message tone by number:**
- #1 — Add value: mention case study or 2–3 week delivery speed
- #2 — Offer factory visit or reference a similar project
- #3 — Final gentle check before closing the lead

After each follow-up: `follow_up_count += 1`, `last_contacted_at = NOW()`

---

## Memory System

**File:** `core/memory.py` — table: `agent_memory`

Each agent saves structured facts per company that persist across sessions.
On the next interaction, memory is injected into the system prompt.

| Agent | What it saves |
|---|---|
| Agent-S | last_outreach_date, outreach_channel, lead_score, temperature, kVA estimate |
| Agent-RM | technical configs discussed, noise zone, fuel tank decisions |
| Agent-GM | last_deal_date, recommended_price, margin_pct, payment_terms, risk_level |

**Storage:** JSONB `facts` column, merged with `||` operator on upsert.
Existing facts are preserved — new facts overwrite same keys only.

```sql
INSERT INTO agent_memory (entity_id, agent, facts, updated_at)
VALUES (:eid, :agent, :facts, NOW())
ON CONFLICT (entity_id, agent)
DO UPDATE SET
    facts = agent_memory.facts || :facts,   -- merge, not replace
    updated_at = NOW()
```

---

## Conversation Continuity

All 3 agents share the **same `conversations` + `messages` tables**.

```
conversations: id, customer_phone, company_name, current_agent, region
messages:      conversation_id, sender, content, delivery_status, created_at
```

When Agent-S escalates to Agent-RM:
- `update_current_agent(conv_id, "agent_rm")` — ownership changes in DB
- Agent-RM loads full history: `get_conversation_history(conv_id)`
- Full context preserved — RM sees everything the customer said to Agent-S

When Agent-RM escalates to Agent-GM:
- Same pattern — GM gets the full thread

When GM approves and sends quote:
- Quote message added to same conversation thread
- Customer reply comes back into the same thread

---

## DRY_RUN Mode

Set `DRY_RUN=true` in `.env` to test the full pipeline without any real sends.

| Action | DRY_RUN=true | DRY_RUN=false |
|---|---|---|
| WhatsApp send | Logs `DRY_RUN whatsapp_send skipped` | Sends via Meta API |
| Email send | Logs `DRY_RUN email_send skipped` | Sends via SendGrid |
| Zoho CRM write | Logs `DRY_RUN zoho_update skipped` | Writes to Zoho |
| DB writes | Normal | Normal |
| LLM calls | Normal | Normal |

---

## Agentic Loop (`core/llm.py`)

All 3 agents use the same `run_agent_loop()` function.

```
1. Send: [system_prompt, user_message, tools_spec] to LLM
2. LLM responds with tool_calls (or final text)
3. Execute all tool_calls IN PARALLEL (ThreadPoolExecutor)
4. Sort results back to original order (OpenAI requires matching IDs)
5. Append tool results to messages → send back to LLM
6. Repeat until: no more tool_calls → return final text
7. Safety: max_iterations limit (Agent-S: 10, Agent-RM: 10, Agent-GM: 12)
```

Retry: `@retry(stop_after_attempt(3), wait=wait_exponential(min=2, max=10))` on every LLM call.

---

## Provider Switching

```
LLM_PROVIDER=groq    → Groq (Llama 3.3 70B) — free dev tier, 200K tokens/day
LLM_PROVIDER=openai  → OpenAI (GPT-4o-mini / GPT-4o) — production
```

Same OpenAI Python SDK for both — only `base_url` and `api_key` change.
Swap with one `.env` line change, zero code changes.
