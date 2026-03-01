# Pai Kane Agents — Commands Reference

> All commands run from your project root: `d:\paikane-agents\`
> Replace `http://localhost:8000` with your server IP when deployed.

---

## Docker Management

```bash
# Start all containers (postgres, redis, app, worker)
docker compose up -d

# Start and rebuild images (run this after any code change)
docker compose up -d --build

# Stop all containers
docker compose down

# Stop and delete all data (full reset — caution!)
docker compose down -v

# Check status of all containers
docker compose ps

# Restart a single container
docker compose restart app
docker compose restart worker
```

---

## Logs

```bash
# Live logs — all containers
docker compose logs -f

# Live logs — app only (FastAPI)
docker logs paikane-app -f

# Live logs — worker only (Celery — agents run here)
docker logs paikane-worker -f

# Last 100 lines of worker logs
docker logs paikane-worker --tail=100

# Filter logs for errors only
docker logs paikane-worker 2>&1 | grep -i "error\|failed\|exception"

# Filter logs for a specific agent
docker logs paikane-worker 2>&1 | grep "agent_s\|agent_rm\|agent_gm"

# Filter logs for Zoho activity
docker logs paikane-worker 2>&1 | grep "zoho"

# Filter logs for WhatsApp activity
docker logs paikane-worker 2>&1 | grep "whatsapp"

# Filter logs for tool calls (agent reasoning)
docker logs paikane-worker 2>&1 | grep "tool_call\|tool_result"
```

---

## API — Health & Status

```bash
# Check if app is running
curl http://localhost:8000/health

# Full system status (DB, Redis, pending escalations)
curl http://localhost:8000/admin/system-status

# View product catalog
curl http://localhost:8000/admin/products

# View recent leads
curl http://localhost:8000/admin/leads
```

---

## Agent Triggers (Manual)

```bash
# Trigger Agent-S mining cycle (mines News + RERA + Zoho CRM)
curl -X POST http://localhost:8000/webhooks/trigger/mine

# Trigger follow-up processing (sends follow-ups to HOT/WARM leads)
curl -X POST http://localhost:8000/webhooks/trigger/followups

# Simulate a Zoho CRM lead-created webhook (as if a new lead was added in CRM)
curl -X POST http://localhost:8000/webhooks/zoho/lead-created \
  -H "Content-Type: application/json" \
  -d '{"lead_id": "TEST001"}'
```

---

## GM Approval Dashboard

```bash
# View all deals waiting for GM approval
curl http://localhost:8000/dashboard/pending-approvals

# View a specific deal
curl http://localhost:8000/dashboard/deal/{deal_id}

# Approve a deal (with optional custom price and terms)
curl -X POST http://localhost:8000/dashboard/approve/{deal_id} \
  -H "Content-Type: application/json" \
  -d '{"approved_price": 850000, "payment_terms": "50% advance, 50% on delivery", "notes": "Good customer"}'

# Reject a deal
curl -X POST http://localhost:8000/dashboard/reject/{deal_id} \
  -H "Content-Type: application/json" \
  -d '{"reason": "Margin too low — competitor pricing"}'

# View all deals (approved, rejected, pending)
curl http://localhost:8000/dashboard/all-deals
```

---

## Database — View Tables

```bash
# List all tables
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c "\dt"

# ── LEADS ──────────────────────────────────────────────────────────────────
# All leads
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, company_name, contact_name, phone, temperature, lead_score, status, created_at FROM leads ORDER BY created_at DESC LIMIT 20;"

# Hot leads only
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT company_name, contact_name, phone, lead_score, status FROM leads WHERE temperature = 'HOT' ORDER BY lead_score DESC;"

# Leads needing follow-up
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT company_name, phone, temperature, follow_up_count, last_contacted_at FROM leads WHERE status IN ('contacted', 'qualified') AND follow_up_count < 3;"

# Lead count by status
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY COUNT(*) DESC;"

# Lead count by source
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT source, COUNT(*) FROM leads GROUP BY source ORDER BY COUNT(*) DESC;"

# ── PRODUCTS ───────────────────────────────────────────────────────────────
# Full product catalog
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT kva_rating, engine_make, engine_model, alternator_make, enclosure_type, pep_price, customer_price, is_active FROM products ORDER BY kva_rating;"

# Active products only
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT kva_rating, engine_make, customer_price, lead_time_weeks_min FROM products WHERE is_active = true ORDER BY kva_rating;"

# ── ESCALATIONS ────────────────────────────────────────────────────────────
# All escalations (agent handoffs)
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, from_agent, to_agent, reason, status, priority, created_at FROM escalations ORDER BY created_at DESC LIMIT 20;"

# Pending escalations (waiting to be picked up)
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, from_agent, to_agent, reason, priority, created_at FROM escalations WHERE status = 'pending' ORDER BY created_at;"

# Failed or stuck escalations
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, from_agent, to_agent, reason, status, created_at FROM escalations WHERE status NOT IN ('completed') AND created_at < NOW() - INTERVAL '1 hour';"

# ── AGENT ACTIVITY LOG ─────────────────────────────────────────────────────
# Recent agent actions
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT agent, action, created_at FROM agent_activity_log ORDER BY created_at DESC LIMIT 20;"

# Agent activity today
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT agent, action, details, created_at FROM agent_activity_log WHERE created_at > NOW() - INTERVAL '24 hours' ORDER BY created_at DESC;"

# Count actions by agent
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT agent, action, COUNT(*) FROM agent_activity_log GROUP BY agent, action ORDER BY agent, COUNT(*) DESC;"

# Errors only
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT agent, action, error_message, created_at FROM agent_activity_log WHERE error_message IS NOT NULL ORDER BY created_at DESC LIMIT 20;"

# ── DEAL RECOMMENDATIONS ───────────────────────────────────────────────────
# All GM deals
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, company_name, kva_rating, recommended_price, margin_pct, approval_status, created_at FROM deal_recommendations ORDER BY created_at DESC LIMIT 10;"

# Pending GM approvals
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, company_name, kva_rating, recommended_price, margin_pct, quote_validity_days FROM deal_recommendations WHERE approval_status = 'pending_gm' ORDER BY created_at;"

# Approved deals
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT company_name, kva_rating, recommended_price, approved_price, margin_pct, approved_at FROM deal_recommendations WHERE approval_status = 'approved' ORDER BY approved_at DESC;"

# ── CONVERSATIONS & MESSAGES ───────────────────────────────────────────────
# Active conversations
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, customer_name, company_name, customer_phone, current_agent, updated_at FROM conversations ORDER BY updated_at DESC LIMIT 10;"

# Messages in a conversation (replace CONV_ID)
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT sender, LEFT(content, 100), delivery_status, created_at FROM messages WHERE conversation_id = 'CONV_ID' ORDER BY created_at;"

# ── TECHNICAL CONFIGS ──────────────────────────────────────────────────────
# Configs built by Agent-RM
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT id, kva_rating, engine_make, enclosure_type, cpcb_iv_compliant, is_standard, created_at FROM technical_configs ORDER BY created_at DESC LIMIT 10;"

# ── COMMODITY PRICES ───────────────────────────────────────────────────────
# Current commodity baseline prices
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT indicator, price, unit, updated_at FROM commodity_prices ORDER BY updated_at DESC;"
```

---

## Test Individual Tools (Inside Docker)

```bash
# Test Zoho CRM connection
docker exec paikane-app python -c "
from tools.zoho_crm import search_leads
leads = search_leads('(State:equals:Maharashtra)', max_results=3)
print(f'Zoho connected. Found {len(leads)} leads.')
for l in leads:
    print(f'  {l.get(\"Full_Name\")} | {l.get(\"Lead_Status\")} | {l.get(\"Phone\")}')
"

# Test fuel calculator
docker exec paikane-app python -c "
from tools.fuel_calculator import calculate_fuel_consumption
r = calculate_fuel_consumption(125, 75)
print(r['note'])
print(f'HSD price used: Rs {r[\"hsd_price_per_litre\"]}/litre')
"

# Test noise compliance
docker exec paikane-app python -c "
from tools.noise_compliance import check_noise_compliance
r = check_noise_compliance(125, 'residential', 5)
print(f'Status: {r[\"compliance_status\"]}')
print(r['recommendation'])
"

# Test load estimator
docker exec paikane-app python -c "
from tools.load_estimator import estimate_load_from_equipment
r = estimate_load_from_equipment([
    {'type': 'ac', 'quantity': 10, 'kw_each': 2.5},
    {'type': 'light', 'quantity': 50, 'kw_each': 0.06},
    {'type': 'lift', 'quantity': 2, 'kva_each': 30},
])
print(r['notes'])
"

# Test USD/INR exchange rate
docker exec paikane-app python -c "
from tools.exchange_rate import get_usd_inr_rate
r = get_usd_inr_rate()
print(r['note'])
"

# Test import cost impact
docker exec paikane-app python -c "
from tools.exchange_rate import calculate_import_cost_impact
r = calculate_import_cost_impact(750000, 'cummins')
print(r['recommendation'])
"

# Test installation advisor
docker exec paikane-app python -c "
from tools.installation_advisor import get_plinth_dimensions
r = get_plinth_dimensions(125)
print(r)
"

# Test product catalog query
docker exec paikane-app python -c "
from database.connection import get_sync_engine
from sqlalchemy import text
engine = get_sync_engine()
with engine.connect() as conn:
    rows = conn.execute(text('SELECT kva_rating, engine_make, customer_price FROM products WHERE is_active = true ORDER BY kva_rating')).fetchall()
    for r in rows:
        print(f'  {r[0]} kVA | {r[1]} | Rs {r[2]:,.0f}')
"
```

---

## Test Full Pipeline (End-to-End)

```bash
# Step 1: Insert a test lead into the DB
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c "
INSERT INTO leads (zoho_lead_id, company_name, contact_name, phone, segment, requirement_text, temperature, lead_score, status, region)
VALUES ('TEST001', 'ABC Builders Pvt Ltd', 'Ravi Sharma', '9876543210', 'construction',
        'Need 125 kVA DG set for residential project in Thane. Site is near residential area.',
        'HOT', 80, 'qualified', 'R1');
"

# Step 2: Manually create an escalation to Agent-RM
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c "
INSERT INTO escalations (from_agent, to_agent, reason, priority, status, payload)
VALUES ('agent_s', 'agent_rm', 'quote_request', 'HOT', 'pending',
        '{\"customer_name\": \"Ravi Sharma\", \"company_name\": \"ABC Builders Pvt Ltd\",
          \"phone\": \"9876543210\", \"requirement_summary\": \"Need 125 kVA DG set for residential project in Thane\",
          \"segment\": \"construction\", \"location\": \"Thane\"}');
"

# Step 3: Watch worker pick it up
docker logs paikane-worker -f

# Step 4: Check what Agent-RM built
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT kva_rating, engine_make, enclosure_type, cpcb_iv_compliant, is_standard FROM technical_configs ORDER BY created_at DESC LIMIT 1;"

# Step 5: Check what Agent-GM recommended
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "SELECT company_name, kva_rating, recommended_price, margin_pct, payment_terms, approval_status FROM deal_recommendations ORDER BY created_at DESC LIMIT 1;"

# Step 6: Check pending GM approvals
curl http://localhost:8000/dashboard/pending-approvals
```

---

## Simulate WhatsApp Incoming Message

```bash
# Simulate customer sending a WhatsApp message
curl -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "changes": [{
        "value": {
          "messages": [{
            "from": "919876543210",
            "id": "wamid.test001",
            "type": "text",
            "text": {"body": "Hi, I need a 125 kVA DG set for my construction site in Thane"}
          }]
        }
      }]
    }]
  }'
```

---

## Debugging Common Issues

```bash
# Check if all containers are healthy
docker compose ps

# Check why worker is failing (last 50 lines)
docker logs paikane-worker --tail=50

# Check if DB is reachable from app
docker exec paikane-app python -c "
from database.connection import get_sync_engine
from sqlalchemy import text
engine = get_sync_engine()
with engine.connect() as conn:
    print('DB OK:', conn.execute(text('SELECT COUNT(*) FROM leads')).scalar(), 'leads')
"

# Check if Redis is reachable
docker exec paikane-app python -c "
import redis
r = redis.from_url('redis://redis:6379/0')
r.ping()
print('Redis OK')
"

# Check if OpenAI key works
docker exec paikane-app python -c "
from core.config import get_settings
s = get_settings()
key = s.openai_api_key
print('Key set:', bool(key and key != 'sk-PASTE_YOUR_KEY_HERE'))
print('Key prefix:', key[:10] if key else 'MISSING')
"

# Check if Zoho token refreshes
docker exec paikane-app python -c "
from tools.zoho_crm import _refresh_token
token = _refresh_token()
print('Zoho token OK:', token[:20] + '...')
"

# Check Celery workers are registered
docker exec paikane-worker celery -A core.scheduler inspect registered

# Check Celery active tasks
docker exec paikane-worker celery -A core.scheduler inspect active

# Reset a stuck escalation (replace ESC_ID)
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "UPDATE escalations SET status = 'pending', picked_up_at = NULL WHERE id = 'ESC_ID';"

# Clear all test data (leads + escalations + configs + deals)
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c \
  "DELETE FROM deal_recommendations; DELETE FROM technical_configs; DELETE FROM escalations; DELETE FROM messages; DELETE FROM conversations; DELETE FROM leads WHERE zoho_lead_id LIKE 'TEST%';"
```

---

## Quick Reference — Container Names

| Container | Name | Purpose |
|---|---|---|
| FastAPI app | `paikane-app` | REST API, webhooks |
| Celery worker | `paikane-worker` | Agents run here |
| PostgreSQL | `paikane-postgres` | Database |
| Redis | `paikane-redis` | Task queue |

## Quick Reference — Key URLs

| URL | Purpose |
|---|---|
| `http://localhost:8000/health` | Health check |
| `http://localhost:8000/admin/system-status` | System status |
| `http://localhost:8000/admin/products` | Product catalog |
| `http://localhost:8000/dashboard/pending-approvals` | GM approval queue |
| `http://localhost:8000/docs` | Auto-generated API docs (Swagger) |
| `http://localhost:8000/webhooks/trigger/mine` | Manually trigger mining |
