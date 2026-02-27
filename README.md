# Pai Kane Group — Agentic AI Sales System

Three-tier autonomous AI agent system for lead mining, technical configuration, and commercial pricing.

**Company:** Power Engineering (India) Pvt. Ltd. — Flagship of Pai Kane Group  
**Product:** Diesel & Gas Generator Sets, 5 kVA to 2000 kVA  
**Pilot:** Mumbai Suburban, Construction Sector

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    LEAD SOURCES                          │
│  Google News RSS │ MahaRERA │ Zoho CRM │ IndiaMART      │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  AGENT-S (Lead Hunter)                                   │
│  Mine → Enrich → Qualify → CRM → Outreach → Follow-up   │
│  Tools: Scraper, SerpAPI, Apollo.io, WhatsApp, Zoho     │
└────────────────────────┬────────────────────────────────┘
                         │ Escalation
┌────────────────────────▼────────────────────────────────┐
│  AGENT-RM (Technical Engineer)                           │
│  Product Match → BOM → Compliance → Delivery Check       │
│  Tools: Product DB, Calculator, Zoho CRM                 │
└────────────────────────┬────────────────────────────────┘
                         │ Config Package
┌────────────────────────▼────────────────────────────────┐
│  AGENT-GM (Commercial Brain)                             │
│  Pricing → Margin → Commodities → Deal Recommendation    │
│  Tools: Pricing DB, Commodity API, Zoho Books            │
└────────────────────────┬────────────────────────────────┘
                         │ Approval Request
┌────────────────────────▼────────────────────────────────┐
│  HUMAN GM (Approval Dashboard)                           │
│  Review → Approve / Modify / Reject → Quote Delivered    │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Start (Local Windows Development)

### Prerequisites
- Docker Desktop for Windows (with WSL 2 backend)
- Git

### Setup

1. **Clone and configure:**
```bash
cd C:\
git clone <repo-url> paikane-agents
cd paikane-agents
copy .env.example .env
# Edit .env — add your OpenAI API key and Zoho credentials
```

2. **Start the stack:**
```bash
docker compose up -d
```

This starts 4 services:
- **PostgreSQL** (port 5432) — database with product catalog
- **Redis** (port 6379) — task queue
- **FastAPI** (port 8000) — web layer + webhooks
- **Celery Worker** — background agent tasks

3. **Verify:**
```bash
# Check all services are running
docker compose ps

# Check API is live
curl http://localhost:8000/

# Check database has products
docker exec paikane-postgres psql -U paikane_admin -d paikane_agents -c "SELECT COUNT(*) FROM products;"

# Check system status
curl http://localhost:8000/admin/status
```

4. **Trigger a test mining cycle:**
```bash
curl -X POST http://localhost:8000/webhooks/trigger/mine
```

---

## API Endpoints

### Webhooks (External Events)
| Endpoint | Method | Purpose |
|---|---|---|
| `/webhooks/whatsapp` | GET | WhatsApp webhook verification |
| `/webhooks/whatsapp` | POST | Incoming WhatsApp messages |
| `/webhooks/zoho/lead-created` | POST | Zoho CRM new lead notification |
| `/webhooks/trigger/mine` | POST | Manually trigger mining cycle |
| `/webhooks/trigger/followups` | POST | Manually trigger follow-ups |

### Dashboard (GM Approval)
| Endpoint | Method | Purpose |
|---|---|---|
| `/dashboard/deals/pending` | GET | Deals awaiting GM approval |
| `/dashboard/deals/{id}` | GET | Full deal detail |
| `/dashboard/deals/{id}/approve` | POST | Approve/reject a deal |
| `/dashboard/pipeline` | GET | Pipeline summary |
| `/dashboard/pipeline/leads` | GET | Lead list with filters |
| `/dashboard/activity` | GET | Recent agent activity |
| `/dashboard/conversations` | GET | Active conversations |

### Admin (Knowledge Base)
| Endpoint | Method | Purpose |
|---|---|---|
| `/admin/products` | GET | List all products |
| `/admin/products/{id}` | PUT | Update product pricing |
| `/admin/templates` | GET/POST | Manage outreach templates |
| `/admin/status` | GET | Full system status |

---

## Project Structure

```
paikane-agents/
├── agents/
│   ├── agent_s.py          # Lead Hunter (mining, qualification, outreach)
│   ├── agent_rm.py         # Technical Engineer (config, BOM, compliance)
│   └── agent_gm.py         # Commercial Brain (pricing, margin, deals)
├── tools/
│   ├── scraper.py          # Web scraping (News RSS, MahaRERA)
│   ├── search.py           # SerpAPI web search
│   ├── enrichment.py       # Apollo.io contact enrichment
│   ├── zoho_crm.py         # Zoho CRM read/write
│   ├── zoho_books.py       # Zoho Books (payment history)
│   ├── whatsapp.py         # WhatsApp Business API
│   ├── email_tool.py       # SendGrid email
│   ├── commodity.py        # Commodity price monitoring
│   ├── calculator.py       # Precise financial math
│   ├── pdf_reader.py       # Tender document parsing
│   └── doc_generator.py    # Quotation generation
├── core/
│   ├── config.py           # Environment variables
│   ├── schemas.py          # Pydantic data contracts
│   ├── llm.py              # OpenAI API with tool calling
│   ├── conversation.py     # Shared conversation context
│   ├── escalation.py       # Agent-to-agent handoff
│   ├── scheduler.py        # Celery task definitions
│   └── audit.py            # Activity logging
├── database/
│   ├── connection.py       # DB connection management
│   └── init.sql            # Schema + product seed data
├── api/
│   ├── main.py             # FastAPI application
│   ├── webhooks.py         # WhatsApp + Zoho webhooks
│   ├── dashboard.py        # GM approval + pipeline API
│   └── admin.py            # Product + template management
├── config/
│   └── agent_configs/
│       └── agent_s_r1.json # Agent-S pilot configuration
├── tests/
│   ├── test_agent_s.py     # Agent-S tests
│   └── test_calculator.py  # Calculator precision tests
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

---

## Configuration

All configuration is via `.env` file. Key settings:

| Variable | Description | Required |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `ZOHO_CLIENT_ID` | Zoho OAuth client ID | Yes |
| `ZOHO_CLIENT_SECRET` | Zoho OAuth client secret | Yes |
| `ZOHO_REFRESH_TOKEN` | Zoho OAuth refresh token | Yes |
| `WHATSAPP_ACCESS_TOKEN` | Meta WhatsApp Cloud API token | For WhatsApp |
| `APOLLO_API_KEY` | Apollo.io enrichment key | For enrichment |
| `SERPAPI_KEY` | SerpAPI search key | For web search |

---

## Testing

```bash
# Run all tests
docker exec paikane-app pytest tests/ -v

# Run specific test file
docker exec paikane-app pytest tests/test_calculator.py -v

# Run with coverage
docker exec paikane-app pytest tests/ --cov=. --cov-report=term-missing
```

---

## Scheduled Tasks

| Task | Schedule | Agent |
|---|---|---|
| Lead mining | Every 2 hours (9AM-7PM, Mon-Sat) | Agent-S |
| Zoho CRM check | Every 5 minutes | Agent-S |
| Follow-ups | Daily 9AM IST | Agent-S |
| Expiring quotes | Daily 9:30AM IST | Agent-S |
| RM escalation poll | Every 30 seconds | Agent-RM |
| GM escalation poll | Every 30 seconds | Agent-GM |
| Commodity fetch | Daily 6AM IST | Agent-GM |
| Pipeline review | Monday 9AM IST | Agent-GM |

---

## Security Notes

- All secrets in `.env` — never hardcoded
- `.env` is in `.gitignore` — never committed
- All external API calls use HTTPS
- WhatsApp webhook has verify token validation
- Agent activity log is append-only (audit trail)
- GM approval required for all deals (human-in-the-loop)
- CMD approval required for below-PEP pricing
