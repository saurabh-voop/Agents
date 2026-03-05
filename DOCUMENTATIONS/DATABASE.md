# Pai Kane Agents — Database Reference

## Overview

PostgreSQL 16. 11 tables. All timestamps are `timezone`-aware.
Soft deletes via `deleted_at` — nothing is hard-deleted. All dedup queries use `WHERE deleted_at IS NULL`.

---

## Table Relationships

```
products ──────────────────────────────────────────────┐
                                                        ↓
leads ──→ conversations ──→ messages          technical_configs ──→ deal_recommendations
  ↑              ↑                ↑                    ↑                     ↑
  │              │                │                    │                     │
  └──────────────┴────────────────┴─── escalations ────┴─────────────────────┘
                                               ↑
                                       agent_activity_log
                                       agent_memory
                                       commodity_prices
                                       outreach_templates
```

---

## 1. `leads` — Every Prospect

**What it stores:** One row per company/person that enters the system, from any source.

**Written by:** Agent-S (after qualification + enrichment)
**Read by:** All 3 agents, dedup check, outreach trigger, admin API

```
id                  uuid          PRIMARY KEY
company_name        varchar(255)  "Lodha Developers Limited"
customer_name       varchar(255)  "Rahul Mehta" (contact person, if known)
designation         varchar(100)  "Project Manager"
phone               varchar(20)   "8291026005"
email               varchar(255)  "easylease@lodhagroup.com"
location_city       varchar(100)  "Mumbai Suburban"
location_district   varchar(100)  "Mumbai Suburban"
location_state      varchar(50)   "Maharashtra" (default)
source              varchar(30)   "rera" | "news" | "zoho_inbound"
source_reference    varchar(255)  RERA number or Zoho lead ID of origin record
lead_score          integer       30  (LLM qualification score, 0-100)
temperature         varchar(10)   "WARM"  (HOT / WARM / COLD)
purchase_type       varchar(15)   "PURCHASE" | "BIDDING" | "UNKNOWN"
project_type        varchar(20)   "NEW_PROJECT" | "EXPANSION" | "REPLACEMENT"
segment             varchar(30)   "construction" | "commercial" | "hospital" | "industrial"
requirement_text    text          "Need 500 kVA DG for building construction"
estimated_kva       integer       500  (LLM estimate)
estimated_quantity  integer       1   (number of units)
estimated_deal_value numeric      2500000.00
status              varchar(30)   "new" → "qualified" → "contacted" → "won" | "lost"
region              varchar(10)   "R1"  (agent region assignment)
zoho_lead_id        varchar(50)   "5060012345"  (Zoho CRM sync ID)
zoho_sync_status    varchar(20)   "pending" | "synced" | "failed"
conversation_id     uuid          FK → conversations (linked when chat starts)
created_by          varchar(30)   "agent_s"
deleted_at          timestamptz   NULL (soft delete — dedup checks WHERE deleted_at IS NULL)
```

**Indexes:** source, status, temperature, lead_score, region, zoho_lead_id, created_at

---

## 2. `conversations` — Chat Threads

**What it stores:** One row per active conversation with a customer, regardless of channel.

**Written by:** Agent-S when first contact is made
**Read by:** All agents — `current_agent` tells who should respond next

```
id               uuid          PRIMARY KEY
customer_phone   varchar(20)   "8291026005"
customer_email   varchar(255)  "easylease@lodhagroup.com"
customer_name    varchar(255)  "Rahul Mehta"
company_name     varchar(255)  "Lodha Developers Limited"
zoho_lead_id     varchar(50)   Zoho sync reference
channel          varchar(20)   "whatsapp" | "email"
status           varchar(30)   "active" | "closed" | "escalated"
current_agent    varchar(20)   "agent_s" | "agent_rm" | "agent_gm"
region           varchar(10)   "R1"
closed_at        timestamptz   NULL while active
```

**Key rule:** When Agent-S escalates to Agent-RM, `current_agent` is updated to `"agent_rm"`.
This is how agents know who "has the ball" — they only process conversations where `current_agent = their own agent_id`.

**Indexes:** customer_phone, status, current_agent, region, zoho_lead_id

---

## 3. `messages` — Individual Chat Messages

**What it stores:** Every single message in a conversation — both directions, all channels.

**Written by:** Webhooks (inbound customer message) + agents (outbound reply)
**Read by:** `get_conversation_history()` — loads all messages → fed into LLM as context

```
id                  uuid          PRIMARY KEY
conversation_id     uuid          FK → conversations
sender_type         varchar(20)   "customer" | "agent_s" | "agent_rm" | "agent_gm"
content             text          "I need a 500 kVA DG set for my building"
content_type        varchar(20)   "text" | "image" | "document"
channel             varchar(20)   "whatsapp" | "email"
channel_message_id  varchar(100)  "wamid.HBgL..." (Meta's message ID for delivery tracking)
delivery_status     varchar(20)   "sent" | "delivered" | "read" | "failed"
```

**Relationship:** Many messages → one conversation

**Indexes:** conversation_id, sender_type, created_at

---

## 4. `escalations` — Agent-to-Agent Message Queue

**What it stores:** A handoff ticket from one agent to another.
This IS the inter-agent messaging system — no HTTP, no RabbitMQ, just DB rows.
Celery beat polls every 30 seconds for `status='pending'` rows.

**Written by:** Sending agent (Agent-S → RM, Agent-RM → GM)
**Read by:** Celery beat → target agent picks up and processes

```
id               uuid          PRIMARY KEY
lead_id          uuid          FK → leads
conversation_id  uuid          FK → conversations
from_agent       varchar(20)   "agent_s"
to_agent         varchar(20)   "agent_rm"
reason           varchar(50)   "technical_config_needed"
priority         varchar(10)   "standard" | "urgent"
status           varchar(20)   "pending" → "picked_up" → "completed"
payload          jsonb         Everything the next agent needs:
                               {
                                 "lead_id": "...",
                                 "requirement": "500 kVA hospital Andheri West",
                                 "conversation_id": "...",
                                 "context": "Customer asked for detailed spec..."
                               }
picked_up_at     timestamptz   Set when target agent starts processing
completed_at     timestamptz   Set when target agent finishes
response         jsonb         What the receiving agent sends back
```

**Flow:** `pending` → `picked_up` → `completed`

**Indexes:** status, to_agent, lead_id, priority, created_at

---

## 5. `agent_activity_log` — Full Audit Trail

**What it stores:** Every action every agent takes, forever. Never deleted.
This is the system's black box recorder.

**Written by:** Every agent after every significant action via `log_activity()`
**Read by:** Admin API `/admin/status`, debugging, cost tracking

```
id                  uuid          PRIMARY KEY
agent               varchar(20)   "agent_s"
action              varchar(50)   "mining_cycle_completed" | "lead_qualified" | "outreach_sent"
lead_id             uuid          FK → leads (if relevant to a lead)
conversation_id     uuid          FK → conversations (if relevant to a chat)
escalation_id       uuid          FK → escalations (if relevant to a handoff)
details             jsonb         {"mined": 150, "qualified": 134, "enriched": 115}
processing_time_ms  integer       894059
llm_tokens_used     integer       2199
llm_model           varchar(30)   "llama-3.3-70b-versatile"
error_message       text          NULL (filled if something failed)
```

**Indexes:** agent, action, lead_id, created_at

---

## 6. `agent_memory` — Extracted Facts Per Company

**What it stores:** Compact structured facts about a company, per agent. Persists forever across conversations.

**Written by:** Each agent after a run via `save_memory()`
**Read by:** Each agent before a run via `build_memory_prompt()` — injected into LLM system prompt

```
id           uuid          PRIMARY KEY
entity_id    varchar(255)  "lodha developers limited"  (company name, lowercased)
agent        varchar(50)   "agent_rm"
facts        jsonb         Grows over time — any key-value facts:
                           {
                             "last_config_date": "2026-03-04",
                             "last_kva": 500,
                             "last_engine": "Cummins",
                             "last_alternator": "Stamford",
                             "last_outreach_date": "2026-03-04",
                             "outreach_channel": "whatsapp"
                           }
updated_at   timestamptz   Auto-updated on every save
```

**Unique key:** `(entity_id, agent)` — one memory slot per company per agent.

**Merge logic:** `facts = existing_facts || new_facts`
New keys are added. Same keys are overwritten. Old keys are preserved.
No facts are ever deleted unless explicitly overwritten.

**How it's used:**
```
Before agent run  →  build_memory_prompt(company, agent)  →  appended to system prompt
After agent run   →  save_memory(company, agent, new_facts)  →  merged into JSONB blob
```

**Difference from conversations/messages:**

| | conversations + messages | agent_memory |
|---|---|---|
| What | Full chat transcript | Extracted key facts |
| Size | Grows with every message | Always compact |
| Purpose | LLM conversation context | Cross-session knowledge |
| Lifespan | Per conversation | Permanent |

---

## 7. `products` — DG Set Catalog

**What it stores:** Pai Kane's complete product range — every model with specs and 3-tier pricing.

**Written by:** Manual seeding (database/init.sql)
**Read by:** Agent-RM (load_estimator, fuel_calculator tools), Agent-GM (pricing)

```
id                      uuid          PRIMARY KEY
sku                     varchar(50)   "PKG-500-3P-CUM-ACU"
kva_rating              numeric(6,1)  500.0
phase                   varchar(10)   "3-phase" | "1-phase"
engine_make             varchar(30)   "Cummins" | "Perkins" | "Mahindra"
engine_model            varchar(50)   "NTA855-G4"
alternator_make         varchar(30)   "Stamford" | "Leroy Somer"
alternator_model        varchar(50)   "UCI274H"
enclosure_type          varchar(20)   "open" | "acoustic" | "weatherproof"
panel_type              varchar(20)   "AMF" | "manual"
controller              varchar(30)   "DEIF SGC120" (default)
cpcb_iv_compliant       boolean       true  (mandatory from Jan 2024)
pep_price               numeric(12,2) 2100000.00  ← Pai Kane's cost (FLOOR — never go below)
dealer_price            numeric(12,2) 2300000.00  ← dealer selling price
customer_price          numeric(12,2) 2500000.00  ← standard list price to customer
lead_time_weeks_min     integer       6
lead_time_weeks_max     integer       8
price_list_version      varchar(30)   "Rel.3 dtd 01/01/2026"
price_list_valid_until  date          2026-03-31
is_active               boolean       true
```

**Pricing tiers:**
- `pep_price` = floor — Agent-GM will never recommend below this
- `dealer_price` = mid tier
- `customer_price` = standard list — ceiling for standard deals
- Agent-GM can go between `pep_price` and `customer_price` depending on deal size and competition

**Indexes:** kva_rating, phase, enclosure_type, panel_type, is_active

---

## 8. `technical_configs` — Agent-RM's Output

**What it stores:** The complete technical specification Agent-RM builds for a lead after running its tool loop.

**Written by:** Agent-RM after its tool loop completes
**Read by:** Agent-GM (to calculate pricing on top of this config)

```
id                        uuid          PRIMARY KEY
lead_id                   uuid          FK → leads
escalation_id             uuid          FK → escalations
kva_rating                integer       500
phase                     varchar(10)   "3-phase"
engine_make               varchar(30)   "Cummins"
engine_model              varchar(50)   "NTA855-G4"
alternator_make           varchar(30)   "Stamford"
alternator_model          varchar(50)   "UCI274H"
controller                varchar(30)   "DEIF SGC120"
enclosure_type            varchar(20)   "acoustic"
panel_type                varchar(20)   "AMF"
sku                       varchar(50)   matched product SKU from products table
cpcb_iv_compliant         boolean       true
noise_zone                varchar(20)   "residential"  (from noise_compliance tool)
compliance_notes          text          "Max 75 dB(A) at 1m — acoustic enclosure required"
bom                       jsonb         Full bill of materials:
                                        {
                                          "base_unit": "500 kVA Cummins acoustic",
                                          "AMF_panel": "DEIF SGC120",
                                          "fuel_tank": "990L day tank",
                                          "accessories": ["exhaust pipe", "vibration pads"]
                                        }
standard_lead_time_weeks  integer       8
customer_requested_date   date          2026-04-15
delivery_feasibility      varchar(20)   "feasible" | "tight" | "not_feasible"
is_standard               boolean       true  (false = customisation needed)
non_standard_reason       text          NULL (filled if customisation required)
created_by                varchar(30)   "agent_rm"
```

**Indexes:** lead_id, kva_rating

---

## 9. `deal_recommendations` — Agent-GM's Output

**What it stores:** The full commercial proposal Agent-GM builds, pending GM approval.

**Written by:** Agent-GM after pricing calculation
**Read by:** GM via `/dashboard/pending` → approves/rejects via `/dashboard/approve`

```
id                      uuid          PRIMARY KEY
lead_id                 uuid          FK → leads
config_id               uuid          FK → technical_configs
pep_price               numeric(12,2) 2100000.00  (Pai Kane's cost, from products)
dealer_price            numeric(12,2) 2300000.00
customer_price          numeric(12,2) 2500000.00
recommended_price       numeric(12,2) 2350000.00  ← what Agent-GM suggests
accessories_total       numeric(12,2) 75000.00
subtotal                numeric(12,2) 2425000.00
gst_amount              numeric(12,2) 436500.00   (18% GST)
freight_estimate        numeric(12,2) 15000.00
total_deal_value        numeric(12,2) 2876500.00
discount_from_list_pct  numeric(5,2)  6.00        (% off customer_price)
margin_above_pep_pct    numeric(5,2)  11.90       (% above pep_price)
quantity                integer       1
commodity_snapshot      jsonb         Prices at time of quote (for audit):
                                      {"hsd_inr": 92.5, "usd_inr": 84.2, "steel_inr": 58000}
payment_terms           text          "50% advance, 50% before dispatch"
competitor_mentioned    varchar(100)  "Kirloskar" (if customer mentioned a competitor)
competitor_price        numeric(12,2) 2200000.00  (if known)
competitive_notes       text          "Kirloskar quote was for open enclosure, not acoustic"
recommendation          varchar(30)   "APPROVE" | "REJECT" | "NEGOTIATE"  ← Agent-GM's verdict
reasoning               text          "Margin at 11.9% is above 10% threshold. Standard deal."
risk_level              varchar(10)   "LOW" | "MEDIUM" | "HIGH"
strategic_value         text          "Lodha is a repeat customer — worth discount"
gm_decision             varchar(20)   NULL → "approved" | "rejected" | "counter_offered"
gm_approved_price       numeric(12,2) NULL → GM can override recommended_price
gm_notes                text          "Approved. Send quote by tomorrow."
gm_decided_at           timestamptz   NULL → filled when GM acts
gm_decided_by           varchar(50)   "saurabh.salunkhe@paikane.com"
quote_valid_until       date          2026-04-05
created_by              varchar(30)   "agent_gm"
```

**Indexes:** lead_id, recommendation, gm_decision, created_at

---

## 10. `outreach_templates` — Message Templates

**What it stores:** Pre-written message templates for WhatsApp and email outreach.

**Written by:** Manual seeding (database/init.sql)
**Read by:** Agent-S when sending outreach — picks template by segment + channel

```
id                        uuid          PRIMARY KEY
name                      varchar(100)  "construction_cold_outreach"
segment                   varchar(30)   "construction" | "hospital" | "commercial"
channel                   varchar(20)   "whatsapp" | "email"
template_type             varchar(20)   "initial_outreach" | "followup" | "quote_delivery"
subject                   varchar(255)  Email subject (NULL for WhatsApp)
body                      text          "Namaste {name}! Pai Kane Group here..."
whatsapp_template_name    varchar(100)  "paikane_construction_v2"  ← Meta's approved name
whatsapp_template_status  varchar(20)   "approved" | "pending" | "rejected"
is_active                 boolean       true
```

**Why WhatsApp templates exist:**
Meta requires pre-approved templates for the first message to any new number.
Free-form text is only allowed after the customer replies first.
Template must be submitted to Meta and approved before use.

**Indexes:** segment, channel

---

## 11. `commodity_prices` — Raw Material Price Tracker

**What it stores:** Daily snapshots of prices that directly affect DG set costs.

**Written by:** `fetch_commodity_prices_task` (daily Celery task)
**Read by:** Agent-RM (`fuel_calculator` for running costs), Agent-GM (import cost risk assessment)

```
id                        uuid          PRIMARY KEY
indicator                 varchar(30)   "hsd_inr" | "usd_inr" | "steel_inr" | "copper_inr"
price                     numeric(12,4) 92.5000
unit                      varchar(20)   "INR/litre" | "INR/USD" | "INR/tonne"
source                    varchar(50)   "commodity_api" | "manual" | "rbi"
baseline_price            numeric(12,4) 90.0000  (price when last price list was set)
change_from_baseline_pct  numeric(6,2)  2.78     (how much it has moved since baseline)
fetched_at                timestamptz   2026-03-05 00:00:00
```

**Why this matters for pricing:**
- If `hsd_inr` rises → running cost per hour goes up → customer's backup power cost goes up
- If `usd_inr` rises → imported engine/alternator costs go up → `pep_price` effectively goes up
- Agent-GM uses `change_from_baseline_pct` to flag pricing risk on deals with high import components

**Indicators tracked:**

| Indicator | What it tracks | Used by |
|---|---|---|
| `hsd_inr` | HSD diesel per litre | fuel_calculator tool |
| `usd_inr` | USD/INR exchange rate | Agent-GM import cost risk |
| `steel_inr` | Steel per tonne | Base frame cost |
| `copper_inr` | Copper per tonne | Alternator winding cost |

**Indexes:** indicator, fetched_at

---

## Common Patterns

### Soft Delete
```sql
-- Every query filters deleted records
SELECT * FROM leads WHERE deleted_at IS NULL;

-- Soft delete instead of hard delete
UPDATE leads SET deleted_at = NOW() WHERE id = :id;
```

### Auto-updated timestamps
```sql
-- Trigger on leads, conversations, products, escalations, etc.
BEFORE UPDATE → SET updated_at = NOW()
```

### JSONB merge for memory
```sql
-- agent_memory uses || operator to merge facts without losing old ones
ON CONFLICT (entity_id, agent)
DO UPDATE SET facts = agent_memory.facts || :new_facts
```

### UUID primary keys
All tables use `uuid_generate_v4()` — no sequential integers. Safe for distributed inserts, no ID guessing.
