-- ============================================================
-- Pai Kane Group — Agentic AI Sales System
-- PostgreSQL Database Schema v1.0
-- ============================================================
-- This script runs on first container startup via Docker entrypoint.
-- All tables use UUID primary keys, soft deletes, and audit columns.
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- SCHEMA: Core Tables
-- ============================================================

-- ------------------------------------------------------------
-- Table: conversations
-- The shared conversation thread between customer and agents.
-- All agents read/write to the same conversation.
-- ------------------------------------------------------------
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Customer identification
    customer_phone VARCHAR(20),           -- E.164 format: +919876543210
    customer_email VARCHAR(255),
    customer_name VARCHAR(255),
    company_name VARCHAR(255),
    
    -- Zoho CRM reference
    zoho_lead_id VARCHAR(50),
    zoho_contact_id VARCHAR(50),
    
    -- Conversation state
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    -- Values: active, paused, escalated, closed, archived
    
    current_agent VARCHAR(20) NOT NULL DEFAULT 'agent_s',
    -- Values: agent_s, agent_rm, agent_gm, human
    -- Tracks which agent is currently handling this conversation
    
    channel VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    -- Values: whatsapp, email, web_form, phone
    
    region VARCHAR(10) NOT NULL DEFAULT 'R1',
    -- Values: R1 (MMR), R2 (Pune), R3 (Nashik)
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    created_by VARCHAR(30) NOT NULL DEFAULT 'system',
    deleted_at TIMESTAMPTZ  -- Soft delete
);

CREATE INDEX idx_conversations_phone ON conversations(customer_phone);
CREATE INDEX idx_conversations_zoho ON conversations(zoho_lead_id);
CREATE INDEX idx_conversations_status ON conversations(status);
CREATE INDEX idx_conversations_region ON conversations(region);
CREATE INDEX idx_conversations_current_agent ON conversations(current_agent);

-- ------------------------------------------------------------
-- Table: messages
-- Individual messages within a conversation. Both customer
-- messages and agent messages stored here.
-- ------------------------------------------------------------
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    
    -- Who sent this message
    sender_type VARCHAR(20) NOT NULL,
    -- Values: customer, agent_s, agent_rm, agent_gm, human, system
    
    -- Message content
    content TEXT NOT NULL,
    content_type VARCHAR(20) NOT NULL DEFAULT 'text',
    -- Values: text, template, document, image
    
    -- Channel details
    channel VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    channel_message_id VARCHAR(100),      -- WhatsApp/email message ID for tracking
    
    -- Delivery status
    delivery_status VARCHAR(20) DEFAULT 'sent',
    -- Values: queued, sent, delivered, read, failed
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id);
CREATE INDEX idx_messages_created ON messages(created_at);
CREATE INDEX idx_messages_sender ON messages(sender_type);

-- ------------------------------------------------------------
-- Table: leads
-- Mirror/supplement of Zoho CRM lead data. Agent-S writes here
-- AND to Zoho CRM. This is the agent's working copy.
-- ------------------------------------------------------------
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id),
    
    -- Zoho CRM sync
    zoho_lead_id VARCHAR(50),
    zoho_sync_status VARCHAR(20) DEFAULT 'pending',
    -- Values: pending, synced, failed, conflict
    zoho_last_sync TIMESTAMPTZ,
    
    -- Customer details
    customer_name VARCHAR(255),
    company_name VARCHAR(255),
    designation VARCHAR(100),
    phone VARCHAR(20),
    email VARCHAR(255),
    location_city VARCHAR(100),
    location_district VARCHAR(100),
    location_state VARCHAR(50) DEFAULT 'Maharashtra',
    
    -- Lead source
    source VARCHAR(30) NOT NULL,
    -- Values: indiamart, gem, cppp, rera, news, zoho_inbound, expo, referral, website
    source_reference VARCHAR(255),        -- Tender ID, IndiaMART enquiry ID, etc.
    
    -- Qualification (Agent-S)
    purchase_type VARCHAR(15),            -- PURCHASE, BIDDING
    temperature VARCHAR(10),              -- HOT, WARM, COLD
    project_type VARCHAR(20),             -- NEW_PROJECT, EXPANSION, REPLACEMENT
    segment VARCHAR(30),                  -- construction, hospital, data_centre, commercial, industrial, government, infrastructure
    lead_score INTEGER DEFAULT 0,         -- 0-100
    
    -- Requirement (as stated by customer)
    requirement_text TEXT,
    estimated_kva INTEGER,
    estimated_quantity INTEGER DEFAULT 1,
    estimated_deal_value NUMERIC(12,2),   -- INR
    
    -- Status
    status VARCHAR(30) NOT NULL DEFAULT 'new',
    -- Values: new, contacted, responded, qualified, escalated_rm, config_done, pricing_done, quoted, won, lost, archived

    -- Follow-up tracking (used by Agent-S follow-up cadence)
    follow_up_count INTEGER NOT NULL DEFAULT 0,
    last_contacted_at TIMESTAMPTZ,

    -- Region assignment
    region VARCHAR(10) NOT NULL DEFAULT 'R1',
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(30) NOT NULL DEFAULT 'agent_s',
    deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_leads_zoho ON leads(zoho_lead_id);
CREATE INDEX idx_leads_status ON leads(status);
CREATE INDEX idx_leads_temperature ON leads(temperature);
CREATE INDEX idx_leads_region ON leads(region);
CREATE INDEX idx_leads_segment ON leads(segment);
CREATE INDEX idx_leads_score ON leads(lead_score);
CREATE INDEX idx_leads_source ON leads(source);
CREATE INDEX idx_leads_created ON leads(created_at);

-- ------------------------------------------------------------
-- Table: escalations
-- Tracks every handoff between agents with full context.
-- This is the "message queue" between agents.
-- ------------------------------------------------------------
CREATE TABLE escalations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES leads(id),
    conversation_id UUID REFERENCES conversations(id),
    
    -- Routing
    from_agent VARCHAR(20) NOT NULL,      -- agent_s, agent_rm, agent_gm
    to_agent VARCHAR(20) NOT NULL,        -- agent_rm, agent_gm, human_gm, human_engineering, cmd
    
    -- Escalation details
    reason VARCHAR(50) NOT NULL,
    -- Values: pricing_request, technical_question, quote_request, sizing_help,
    --         tender_compliance, non_standard, parallel_operation, below_pep,
    --         pipeline_alert, customer_complaint, factory_visit
    
    priority VARCHAR(10) NOT NULL DEFAULT 'standard',
    -- Values: hot, standard, nurture
    
    -- Payload (JSON - the escalation package)
    payload JSONB NOT NULL,
    -- Contains: full escalation package as defined in agent docs
    
    -- Processing status
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- Values: pending, processing, completed, failed, expired
    
    picked_up_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    
    -- Response (JSON - what the receiving agent produced)
    response JSONB,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(30) NOT NULL
);

CREATE INDEX idx_escalations_lead ON escalations(lead_id);
CREATE INDEX idx_escalations_status ON escalations(status);
CREATE INDEX idx_escalations_to_agent ON escalations(to_agent);
CREATE INDEX idx_escalations_priority ON escalations(priority);
CREATE INDEX idx_escalations_created ON escalations(created_at);

-- ------------------------------------------------------------
-- Table: technical_configs
-- Agent-RM's output: the Technical Configuration Package
-- ------------------------------------------------------------
CREATE TABLE technical_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES leads(id),
    escalation_id UUID REFERENCES escalations(id),
    
    -- Configuration
    kva_rating INTEGER NOT NULL,
    phase VARCHAR(10) NOT NULL DEFAULT '3-phase',  -- 1-phase, 3-phase
    engine_make VARCHAR(30) NOT NULL,
    engine_model VARCHAR(50) NOT NULL,
    alternator_make VARCHAR(30) NOT NULL,
    alternator_model VARCHAR(50) NOT NULL,
    controller VARCHAR(30) DEFAULT 'DEIF SGC120',
    
    -- Enclosure & Panel
    enclosure_type VARCHAR(20) NOT NULL,  -- sheet_metal, grp
    panel_type VARCHAR(20) NOT NULL,      -- amf_logic, amf_ats
    
    -- SKU from price list
    sku VARCHAR(50),
    
    -- BOM (JSON array of line items)
    bom JSONB NOT NULL,
    -- Example: [{"item": "DG Set", "sku": "...", "qty": 1}, {"item": "Exhaust Pipe", "code": "MS-0", "qty": 1}, ...]
    
    -- Compliance
    cpcb_iv_compliant BOOLEAN NOT NULL DEFAULT true,
    noise_zone VARCHAR(20),               -- residential, commercial, industrial
    compliance_notes TEXT,
    
    -- Delivery
    standard_lead_time_weeks INTEGER,
    customer_requested_date DATE,
    delivery_feasibility VARCHAR(20),     -- feasible, tight, not_feasible
    
    -- Status
    is_standard BOOLEAN NOT NULL DEFAULT true,
    non_standard_reason TEXT,             -- If not standard, why
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(30) NOT NULL DEFAULT 'agent_rm'
);

CREATE INDEX idx_configs_lead ON technical_configs(lead_id);
CREATE INDEX idx_configs_kva ON technical_configs(kva_rating);

-- ------------------------------------------------------------
-- Table: deal_recommendations
-- Agent-GM's output: the Deal Recommendation Package
-- Presented to human GM for approval.
-- ------------------------------------------------------------
CREATE TABLE deal_recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES leads(id),
    config_id UUID NOT NULL REFERENCES technical_configs(id),
    
    -- Pricing
    price_sheet VARCHAR(20) NOT NULL,     -- amf_logic, amf_ats
    price_tier VARCHAR(20) NOT NULL,      -- customer, dealer, pep
    
    -- Amounts (INR)
    pep_price NUMERIC(12,2) NOT NULL,
    dealer_price NUMERIC(12,2) NOT NULL,
    customer_price NUMERIC(12,2) NOT NULL,
    recommended_price NUMERIC(12,2) NOT NULL,
    accessories_total NUMERIC(12,2) DEFAULT 0,
    subtotal NUMERIC(12,2) NOT NULL,
    gst_amount NUMERIC(12,2) NOT NULL,
    freight_estimate NUMERIC(12,2) DEFAULT 0,
    total_deal_value NUMERIC(12,2) NOT NULL,
    
    -- Margin analysis
    discount_from_list_pct NUMERIC(5,2) DEFAULT 0,
    margin_above_pep_pct NUMERIC(5,2) NOT NULL,
    
    -- Quantity (for multi-unit)
    quantity INTEGER NOT NULL DEFAULT 1,
    
    -- Commodity context (JSON)
    commodity_snapshot JSONB,
    -- Example: {"copper_mcx": 850, "copper_trend": "+2.1%", "steel": 55000, ...}
    
    -- Payment terms recommendation
    payment_terms TEXT,
    
    -- Competitive context
    competitor_mentioned VARCHAR(100),
    competitor_price NUMERIC(12,2),
    competitive_notes TEXT,
    
    -- Agent-GM recommendation
    recommendation VARCHAR(30) NOT NULL,
    -- Values: approve_at_list, approve_with_discount, escalate_to_cmd, reject
    reasoning TEXT NOT NULL,
    risk_level VARCHAR(10) NOT NULL,       -- low, medium, high
    strategic_value TEXT,
    
    -- Human GM decision
    gm_decision VARCHAR(20),
    -- Values: approved, modified, rejected, escalated_cmd
    gm_approved_price NUMERIC(12,2),
    gm_notes TEXT,
    gm_decided_at TIMESTAMPTZ,
    gm_decided_by VARCHAR(50),
    
    -- Quote validity
    quote_valid_until DATE,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(30) NOT NULL DEFAULT 'agent_gm'
);

CREATE INDEX idx_deals_lead ON deal_recommendations(lead_id);
CREATE INDEX idx_deals_decision ON deal_recommendations(gm_decision);
CREATE INDEX idx_deals_recommendation ON deal_recommendations(recommendation);
CREATE INDEX idx_deals_created ON deal_recommendations(created_at);

-- ------------------------------------------------------------
-- Table: agent_activity_log
-- Audit trail: every action by every agent is logged here.
-- This is APPEND-ONLY — never update or delete.
-- ------------------------------------------------------------
CREATE TABLE agent_activity_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Who did what
    agent VARCHAR(20) NOT NULL,
    -- Values: agent_s_r1, agent_s_r2, agent_s_r3, agent_rm, agent_gm, human_gm, human_eng, system
    
    action VARCHAR(50) NOT NULL,
    -- Values: lead_discovered, lead_enriched, lead_qualified, lead_scored,
    --         outreach_sent, follow_up_sent, customer_responded, escalation_created,
    --         config_created, compliance_checked, bom_assembled, pricing_calculated,
    --         recommendation_created, gm_approved, gm_rejected, gm_modified,
    --         quote_created, quote_delivered, conversation_takeover, error
    
    -- Context
    lead_id UUID REFERENCES leads(id),
    conversation_id UUID REFERENCES conversations(id),
    escalation_id UUID REFERENCES escalations(id),
    
    -- Details (JSON - flexible payload)
    details JSONB,
    -- Example: {"score": 78, "temperature": "HOT", "source": "indiamart"}
    
    -- Performance tracking
    processing_time_ms INTEGER,           -- How long the action took
    llm_tokens_used INTEGER,              -- Token consumption for this action
    llm_model VARCHAR(30),                -- gpt-4o-mini, gpt-4o, etc.
    
    -- Error tracking
    error_message TEXT,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_activity_agent ON agent_activity_log(agent);
CREATE INDEX idx_activity_action ON agent_activity_log(action);
CREATE INDEX idx_activity_lead ON agent_activity_log(lead_id);
CREATE INDEX idx_activity_created ON agent_activity_log(created_at);

-- ============================================================
-- SCHEMA: Knowledge Base Tables
-- ============================================================

-- ------------------------------------------------------------
-- Table: products
-- The complete product catalog from the price list.
-- Agent-RM queries this for configuration matching.
-- Agent-GM queries this for pricing.
-- ------------------------------------------------------------
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Product identification
    sku VARCHAR(50),
    kva_rating NUMERIC(6,1) NOT NULL,     -- e.g., 82.5
    phase VARCHAR(10) NOT NULL,           -- 1-phase, 3-phase
    
    -- Configuration
    engine_make VARCHAR(30) NOT NULL,
    engine_model VARCHAR(50) NOT NULL,
    alternator_make VARCHAR(30) NOT NULL,
    alternator_model VARCHAR(50) NOT NULL,
    enclosure_type VARCHAR(20) NOT NULL,  -- sheet_metal, grp
    panel_type VARCHAR(20) NOT NULL,      -- amf_logic, amf_ats
    controller VARCHAR(30) DEFAULT 'DEIF SGC120',
    
    -- Category
    category VARCHAR(20) NOT NULL,        -- lhp, hhp
    -- LHP: 10-160 kVA, HHP: 250-2000 kVA
    
    -- Pricing (INR) — STRICTLY CONFIDENTIAL
    pep_price NUMERIC(12,2) NOT NULL,
    dealer_price NUMERIC(12,2) NOT NULL,
    customer_price NUMERIC(12,2) NOT NULL,
    
    -- Delivery
    lead_time_weeks_min INTEGER NOT NULL,
    lead_time_weeks_max INTEGER NOT NULL,
    
    -- Compliance
    cpcb_iv_compliant BOOLEAN NOT NULL DEFAULT true,
    
    -- Price list reference
    price_list_version VARCHAR(30) DEFAULT 'Rel.3 dtd 01/01/2026',
    price_list_valid_until DATE DEFAULT '2026-03-31',
    
    -- Status
    is_active BOOLEAN NOT NULL DEFAULT true,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_kva ON products(kva_rating);
CREATE INDEX idx_products_phase ON products(phase);
CREATE INDEX idx_products_enclosure ON products(enclosure_type);
CREATE INDEX idx_products_panel ON products(panel_type);
CREATE INDEX idx_products_active ON products(is_active);

-- ------------------------------------------------------------
-- Table: commodity_prices
-- Cached commodity/forex data for Agent-GM monitoring.
-- ------------------------------------------------------------
CREATE TABLE commodity_prices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    indicator VARCHAR(30) NOT NULL,
    -- Values: copper_mcx, copper_lme, steel_india, inr_usd, inr_eur, diesel_india
    
    price NUMERIC(12,4) NOT NULL,
    unit VARCHAR(20) NOT NULL,            -- Rs/kg, Rs/tonne, rate, Rs/litre
    source VARCHAR(50),
    
    -- Baseline for comparison
    baseline_price NUMERIC(12,4),         -- Price on price list date (01/01/2026)
    change_from_baseline_pct NUMERIC(6,2),
    
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_commodity_indicator ON commodity_prices(indicator);
CREATE INDEX idx_commodity_fetched ON commodity_prices(fetched_at);

-- ------------------------------------------------------------
-- Table: outreach_templates
-- Message templates for Agent-S outreach.
-- Editable via admin — no code change needed.
-- ------------------------------------------------------------
CREATE TABLE outreach_templates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    name VARCHAR(100) NOT NULL,
    segment VARCHAR(30) NOT NULL,         -- construction, hospital, data_centre, etc.
    channel VARCHAR(20) NOT NULL,         -- whatsapp, email
    template_type VARCHAR(20) NOT NULL,   -- first_contact, follow_up_1, follow_up_2, follow_up_3
    
    -- Template content (with {placeholders})
    subject VARCHAR(255),                 -- For email only
    body TEXT NOT NULL,
    -- Placeholders: {customer_name}, {company_name}, {project_location}, {kva_range}, {agent_name}
    
    -- WhatsApp template approval
    whatsapp_template_name VARCHAR(100),  -- Meta-approved template name
    whatsapp_template_status VARCHAR(20), -- approved, pending, rejected
    
    is_active BOOLEAN NOT NULL DEFAULT true,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_templates_segment ON outreach_templates(segment);
CREATE INDEX idx_templates_channel ON outreach_templates(channel);

-- ============================================================
-- SEED DATA: Product Catalog from Price List
-- ============================================================

-- AMF Logic — 1 Phase — Sheet Metal
INSERT INTO products (sku, kva_rating, phase, engine_make, engine_model, alternator_make, alternator_model, enclosure_type, panel_type, category, pep_price, dealer_price, customer_price, lead_time_weeks_min, lead_time_weeks_max) VALUES
('PKESD4-10-L1-AMFWA', 10, '1-phase', 'ESCORTS', 'G15-IV', 'LEROY SOMER', 'LSAP 40E-1PH', 'sheet_metal', 'amf_logic', 'lhp', 285856, 296444, 304384, 4, 6),
('PKESD4-15-L1-AMFWA', 15, '1-phase', 'ESCORTS', 'G20-IV', 'LEROY SOMER', 'LSAP 42.3 A-1PH', 'sheet_metal', 'amf_logic', 'lhp', 295240, 306175, 314376, 4, 6),
('PKESD4-20-L1-AMFWA', 20, '1-phase', 'ESCORTS', 'G20-IV', 'LEROY SOMER', 'LSAP 42.3 C-1PH', 'sheet_metal', 'amf_logic', 'lhp', 330681, 342929, 352114, 4, 6),
('PKVED4-25-L1-AMFWA', 25, '1-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 F-1PH', 'sheet_metal', 'amf_logic', 'lhp', 434528, 450622, 462692, 2, 3),
('PKVED4-30-L1-AMFWA', 30, '1-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 G-1PH', 'sheet_metal', 'amf_logic', 'lhp', 447765, 464349, 476787, 2, 3),
('PKVED4-45-L1-AMFWA', 45, '1-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 B-1PH', 'sheet_metal', 'amf_logic', 'lhp', 555512, 576087, 591517, 2, 3);

-- AMF Logic — 3 Phase — Sheet Metal
INSERT INTO products (sku, kva_rating, phase, engine_make, engine_model, alternator_make, alternator_model, enclosure_type, panel_type, category, pep_price, dealer_price, customer_price, lead_time_weeks_min, lead_time_weeks_max) VALUES
('PKESD4-10-L3-AMFWA', 10, '3-phase', 'ESCORTS', 'G15-IV', 'LEROY SOMER', 'LSAP 40 C2', 'sheet_metal', 'amf_logic', 'lhp', 273199, 283317, 290906, 4, 6),
('PKESD4-15-L3-AMFWA', 15, '3-phase', 'ESCORTS', 'G20-IV', 'LEROY SOMER', 'LSAP 40 E', 'sheet_metal', 'amf_logic', 'lhp', 277603, 287885, 295596, 4, 6),
('PKESD4-20-L3-AMFWA', 20, '3-phase', 'ESCORTS', 'G20-IV', 'LEROY SOMER', 'LSAP 40 H', 'sheet_metal', 'amf_logic', 'lhp', 306671, 318029, 326547, 4, 6),
('PKVED4-25-L3-AMFWA', 25, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 B', 'sheet_metal', 'amf_logic', 'lhp', 403951, 418913, 430133, 2, 3),
('PKVED4-30-L3-AMFWA', 30, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 C', 'sheet_metal', 'amf_logic', 'lhp', 408941, 424087, 435446, 2, 3),
('PKVED4-45-L3-AMFWA', 45, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 G', 'sheet_metal', 'amf_logic', 'lhp', 485682, 503671, 517162, 2, 3),
('PKVED4-60-L3-AMFWA', 60, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 K', 'sheet_metal', 'amf_logic', 'lhp', 546206, 566436, 581608, 2, 3),
('PKVED4-82.5-L3-AMFWA', 82.5, '3-phase', 'VECV EICHER', 'E494 CPCBIV', 'LEROY SOMER', 'LSAP 44.3B2', 'sheet_metal', 'amf_logic', 'lhp', 775134, 803842, 825374, 2, 3),
('PKVED4-100-L3-AMFWA', 100, '3-phase', 'VECV EICHER', 'E494 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 C', 'sheet_metal', 'amf_logic', 'lhp', 831165, 861949, 885037, 2, 3),
('PKVED4-125-L3-AMFWA', 125, '3-phase', 'VECV EICHER', 'E694 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 D', 'sheet_metal', 'amf_logic', 'lhp', 911768, 945537, 970864, 2, 3),
('PKVED4-160-L3-AMFWA', 160, '3-phase', 'VECV EICHER', 'E694 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 H', 'sheet_metal', 'amf_logic', 'lhp', 1114508, 1155786, 1186745, 2, 3),
('PKBUD4-250-L3-AMFWA', 250, '3-phase', 'BAUDOUIN', '6M12G6D4/5', 'LEROY SOMER', 'LSAP 45 L1', 'sheet_metal', 'amf_logic', 'hhp', 1792641, 1859035, 1876413, 8, 10),
('PKBUD4-350-L3-AMFWA', 350, '3-phase', 'BAUDOUIN', '6M21G2D4/5', 'LEROY SOMER', 'LSAP 47 A1', 'sheet_metal', 'amf_logic', 'hhp', 2796061, 2899618, 2906202, 8, 10),
('PKBUD4-400-L3-AMFWA', 400, '3-phase', 'BAUDOUIN', '6M21G2D4/5', 'LEROY SOMER', 'LSAP 47 A2', 'sheet_metal', 'amf_logic', 'hhp', 2809734, 2913799, 2920762, 8, 10),
('PKBUD4-450-L3-AMFWA', 450, '3-phase', 'BAUDOUIN', '6M21G4D4/5', 'LEROY SOMER', 'LSAP 47 B1', 'sheet_metal', 'amf_logic', 'hhp', 2941928, 3050888, 3061523, 8, 10),
('PKBUD4-500-L3-AMFWA', 500, '3-phase', 'BAUDOUIN', '6M21G6D4/5', 'LEROY SOMER', 'LSAP 47 C', 'sheet_metal', 'amf_logic', 'hhp', 3055731, 3168907, 3182703, 8, 10),
('PKBUD4-650-L3-AMFWA', 650, '3-phase', 'BAUDOUIN', '6M33G2D4/5', 'LEROY SOMER', 'LSAP 47 F', 'sheet_metal', 'amf_logic', 'hhp', 4762315, 4938697, 4985810, 8, 10),
('PKBUD4-750-L3-AMFWA', 750, '3-phase', 'BAUDOUIN', '6M33G4D4/5', 'LEROY SOMER', 'LSA 49.3 M6', 'sheet_metal', 'amf_logic', 'hhp', 5213385, 5406473, 5466116, 8, 10),
('PKBUD4-1010-L3-AMFWA', 1010, '3-phase', 'BAUDOUIN', '12M26D968E200', 'STAMFORD', 'HCI634Y', 'sheet_metal', 'amf_logic', 'hhp', 5507767, 5711758, 5864752, 13, 15),
('PKBUD4-1250-L3-AMFWA', 1250, '3-phase', 'BAUDOUIN', '12M33D1210E200', 'STAMFORD', 'HCKI634Z', 'sheet_metal', 'amf_logic', 'hhp', 7173634, 7439324, 7638592, 13, 15),
('PKBUD4-1500-L3-AMFWA', 1500, '3-phase', 'BAUDOUIN', '12M33G1650/5', 'STAMFORD', 'S7L1D-C', 'sheet_metal', 'amf_logic', 'hhp', 8922656, 9253125, 9500976, 13, 15),
('PKBUD4-1750-L3-AMFWA', 1750, '3-phase', 'BAUDOUIN', '16M33G1900/5', 'STAMFORD', 'S7L1D-E', 'sheet_metal', 'amf_logic', 'hhp', 12464885, 12926547, 13272794, 13, 15),
('PKBUD4-2000-L3-AMFWA', 2000, '3-phase', 'BAUDOUIN', '16M33G2250/5', 'STAMFORD', 'S7L1D-G', 'sheet_metal', 'amf_logic', 'hhp', 14328219, 14858893, 15256899, 13, 15);

-- AMF Logic — 3 Phase — GRP Canopy
INSERT INTO products (sku, kva_rating, phase, engine_make, engine_model, alternator_make, alternator_model, enclosure_type, panel_type, category, pep_price, dealer_price, customer_price, lead_time_weeks_min, lead_time_weeks_max) VALUES
('PKESD4-GRP-10-L3-AMFWA', 10, '3-phase', 'ESCORTS', 'G15-IV', 'LEROY SOMER', 'LSAP 40 C2', 'grp', 'amf_logic', 'lhp', 335821, 348258, 357587, 4, 6),
('PKESD4-GRP-15-L3-AMFWA', 15, '3-phase', 'ESCORTS', 'G20-IV', 'LEROY SOMER', 'LSAP 40 E', 'grp', 'amf_logic', 'lhp', 340225, 352826, 362276, 4, 6),
('PKESD4-GRP-20-L3-AMFWA', 20, '3-phase', 'ESCORTS', 'G20-IV', 'LEROY SOMER', 'LSAP 40 H', 'grp', 'amf_logic', 'lhp', 367840, 381464, 391682, 4, 6),
('PKVED4-GRP-25-L3-AMFWA', 25, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 B', 'grp', 'amf_logic', 'lhp', 447639, 464219, 476653, 4, 6),
('PKVED4-GRP-30-L3-AMFWA', 30, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 C', 'grp', 'amf_logic', 'lhp', 453219, 470005, 482594, 4, 6),
('PKVED4-GRP-45-L3-AMFWA', 45, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 G', 'grp', 'amf_logic', 'lhp', 561064, 581844, 597430, 4, 6),
('PKVED4-GRP-60-L3-AMFWA', 60, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 K', 'grp', 'amf_logic', 'lhp', 621565, 644586, 661851, 4, 6),
('PKVED4-GRP-82.5-L3-AMFWA', 82.5, '3-phase', 'VECV EICHER', 'E494 CPCBIV', 'LEROY SOMER', 'LSAP 44.3B2', 'grp', 'amf_logic', 'lhp', 940882, 975729, 1001865, 4, 6),
('PKVED4-GRP-100-L3-AMFWA', 100, '3-phase', 'VECV EICHER', 'E494 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 C', 'grp', 'amf_logic', 'lhp', 996912, 1033835, 1061527, 4, 6),
('PKVED4-GRP-125-L3-AMFWA', 125, '3-phase', 'VECV EICHER', 'E694 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 D', 'grp', 'amf_logic', 'lhp', 1091033, 1131442, 1161748, 4, 6),
('PKVED4-GRP-160-L3-AMFWA', 160, '3-phase', 'VECV EICHER', 'E694 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 H', 'grp', 'amf_logic', 'lhp', 1264086, 1310904, 1346018, 4, 6);

-- AMF with ATS — 3 Phase — Sheet Metal (key entries)
INSERT INTO products (sku, kva_rating, phase, engine_make, engine_model, alternator_make, alternator_model, enclosure_type, panel_type, category, pep_price, dealer_price, customer_price, lead_time_weeks_min, lead_time_weeks_max) VALUES
('PKVED4-25-L3-AMF', 25, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 B', 'sheet_metal', 'amf_ats', 'lhp', 410215, 425409, 436803, 2, 3),
('PKVED4-30-L3-AMF', 30, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 C', 'sheet_metal', 'amf_ats', 'lhp', 420585, 436162, 447845, 2, 3),
('PKVED4-60-L3-AMF', 60, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 K', 'sheet_metal', 'amf_ats', 'lhp', 565350, 586289, 601993, 2, 3),
('PKVED4-100-L3-AMF', 100, '3-phase', 'VECV EICHER', 'E494 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 C', 'sheet_metal', 'amf_ats', 'lhp', 890865, 923860, 948607, 2, 3),
('PKVED4-160-L3-AMF', 160, '3-phase', 'VECV EICHER', 'E694 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 H', 'sheet_metal', 'amf_ats', 'lhp', 1195670, 1239954, 1273168, 2, 3),
('PKBUD4-250-L3-AMF', 250, '3-phase', 'BAUDOUIN', '6M12G6D4/5', 'LEROY SOMER', 'LSAP 45 L1', 'sheet_metal', 'amf_ats', 'hhp', 1958220, 2030747, 2085142, 8, 10),
('PKBUD4-500-L3-AMF', 500, '3-phase', 'BAUDOUIN', '6M21G6D4/5', 'LEROY SOMER', 'LSAP 47 C', 'sheet_metal', 'amf_ats', 'hhp', 3352189, 3476345, 3569461, 8, 10),
('PKBUD4-750-L3-AMF', 750, '3-phase', 'BAUDOUIN', '6M33G4D4/5', 'LEROY SOMER', 'LSA 49.3 M6', 'sheet_metal', 'amf_ats', 'hhp', 5909784, 6128665, 6292826, 8, 10);

-- AMF with ATS — 3 Phase — GRP Canopy (selected)
INSERT INTO products (sku, kva_rating, phase, engine_make, engine_model, alternator_make, alternator_model, enclosure_type, panel_type, category, pep_price, dealer_price, customer_price, lead_time_weeks_min, lead_time_weeks_max) VALUES
('PKVED4-GRP-25-L3-AMF', 25, '3-phase', 'VECV EICHER', 'E483 CPCBIV', 'LEROY SOMER', 'LSAP 42.3 B', 'grp', 'amf_ats', 'lhp', 453903, 470715, 483323, 4, 6),
('PKVED4-GRP-100-L3-AMF', 100, '3-phase', 'VECV EICHER', 'E494 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 C', 'grp', 'amf_ats', 'lhp', 1056613, 1095746, 1125097, 4, 6),
('PKVED4-GRP-160-L3-AMF', 160, '3-phase', 'VECV EICHER', 'E694 CPCBIV', 'LEROY SOMER', 'LSAP 44.3 H', 'grp', 'amf_ats', 'lhp', 1345248, 1395072, 1432440, 4, 6);

-- ============================================================
-- SEED DATA: Initial Outreach Template (Construction Sector - Mumbai Suburban)
-- ============================================================

INSERT INTO outreach_templates (name, segment, channel, template_type, body) VALUES
('Construction First Contact - WhatsApp', 'construction', 'whatsapp', 'first_contact',
'Hi {customer_name}, this is {agent_name} from Pai Kane Group.

Noticed {company_name}''s construction project in {project_location}. We manufacture CPCB IV+ DG sets (10-2000 kVA) at our Goa facility with 15,000 sets/year capacity.

For construction projects, our key advantages:
- 2-3 week delivery for 25-160 kVA range
- Ex-works Goa pricing, competitive for western India logistics
- Fleet order capability for multi-site projects

Can I send you our range sheet? Happy to get our technical team to size the requirement if needed.

Best regards,
{agent_name}
Pai Kane Group | Touching Lives'),

('Construction Follow-Up 1 - WhatsApp', 'construction', 'whatsapp', 'follow_up_1',
'Hi {customer_name}, following up on my earlier message about DG sets for your project in {project_location}. Would you like me to share our technical brochure for the construction range? Happy to help with any questions.'),

('Construction Follow-Up 2 - WhatsApp', 'construction', 'whatsapp', 'follow_up_2',
'Hi {customer_name}, we recently supplied fleet units for a similar construction project in Mumbai. Happy to share the reference if useful. Our 2-3 week delivery on the 25-160 kVA range means we can support tight project timelines.'),

('Construction Follow-Up 3 - WhatsApp', 'construction', 'whatsapp', 'follow_up_3',
'Hi {customer_name}, wanted to check if the DG set requirement for your project is still active. If timing has changed, happy to reconnect whenever suits. We are here when you need us.

{agent_name} | Pai Kane Group');

-- ============================================================
-- HELPER: Updated_at trigger function
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to all tables with updated_at
CREATE TRIGGER tr_conversations_updated BEFORE UPDATE ON conversations FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER tr_leads_updated BEFORE UPDATE ON leads FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER tr_escalations_updated BEFORE UPDATE ON escalations FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER tr_configs_updated BEFORE UPDATE ON technical_configs FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER tr_deals_updated BEFORE UPDATE ON deal_recommendations FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER tr_products_updated BEFORE UPDATE ON products FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER tr_templates_updated BEFORE UPDATE ON outreach_templates FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ------------------------------------------------------------
-- Table: agent_memory
-- Persistent per-company facts across agent sessions.
-- Injected into system prompt before each run_agent_loop call.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id   VARCHAR(255) NOT NULL,   -- company_name or lead_id (lowercased)
    agent       VARCHAR(50)  NOT NULL,   -- agent_s, agent_rm, agent_gm
    facts       JSONB        NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_memory_unique ON agent_memory(entity_id, agent);
CREATE INDEX idx_agent_memory_agent ON agent_memory(agent);
CREATE TRIGGER tr_agent_memory_updated BEFORE UPDATE ON agent_memory FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- DONE
-- ============================================================
-- Total tables: 11
-- Total product records seeded: ~53 (covering all AMF Logic + key AMF ATS configs)
-- Total template records seeded: 4 (construction segment for pilot)
-- ============================================================
