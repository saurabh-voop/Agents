"""
Pydantic schemas for all inter-agent data contracts.
These enforce the exact data structures passed between agents.
If you change a schema, both the sending and receiving agent must agree.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date
from enum import Enum
from uuid import UUID, uuid4


# ============================================================
# Enums — strict values for classification fields
# ============================================================

class PurchaseType(str, Enum):
    PURCHASE = "PURCHASE"
    BIDDING = "BIDDING"
    UNKNOWN = "UNKNOWN"

class Temperature(str, Enum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"

class ProjectType(str, Enum):
    NEW_PROJECT = "NEW_PROJECT"
    EXPANSION = "EXPANSION"
    REPLACEMENT = "REPLACEMENT"
    UNKNOWN = "UNKNOWN"

class Segment(str, Enum):
    CONSTRUCTION = "construction"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    HOSPITAL = "hospital"
    DATA_CENTRE = "data_centre"
    GOVERNMENT = "government"
    RESIDENTIAL = "residential"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"

class LeadSource(str, Enum):
    NEWS = "news"
    RERA = "rera"
    INDIAMART = "indiamart"
    GEM = "gem"
    ZOHO_INBOUND = "zoho_inbound"
    WEBSITE = "website"
    REFERRAL = "referral"
    EXPO = "expo"
    MANUAL = "manual"

class LeadStatus(str, Enum):
    NEW = "new"
    NEEDS_ENRICHMENT = "needs_enrichment"
    ENRICHED = "enriched"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    RESPONDED = "responded"
    ESCALATED_RM = "escalated_rm"
    CONFIG_DONE = "config_done"
    PRICING_DONE = "pricing_done"
    QUOTED = "quoted"
    WON = "won"
    LOST = "lost"
    ARCHIVED = "archived"

class PriorityAction(str, Enum):
    IMMEDIATE_OUTREACH = "immediate_outreach"
    STANDARD_OUTREACH = "standard_outreach"
    NEEDS_ENRICHMENT = "needs_enrichment"
    LOW_PRIORITY = "low_priority"

class EscalationReason(str, Enum):
    PRICING_REQUEST = "pricing_request"
    TECHNICAL_QUESTION = "technical_question"
    QUOTE_REQUEST = "quote_request"
    SIZING_HELP = "sizing_help"
    TENDER_COMPLIANCE = "tender_compliance"
    NON_STANDARD = "non_standard"
    PARALLEL_OPERATION = "parallel_operation"
    BELOW_PEP = "below_pep"
    PIPELINE_ALERT = "pipeline_alert"
    CUSTOMER_COMPLAINT = "customer_complaint"

class EnclosureType(str, Enum):
    SHEET_METAL = "sheet_metal"
    GRP = "grp"

class PanelType(str, Enum):
    AMF_LOGIC = "amf_logic"
    AMF_ATS = "amf_ats"

class GMDecision(str, Enum):
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    ESCALATED_CMD = "escalated_cmd"

class GMRecommendation(str, Enum):
    APPROVE_AT_LIST = "approve_at_list"
    APPROVE_WITH_DISCOUNT = "approve_with_discount"
    ESCALATE_TO_CMD = "escalate_to_cmd"
    REJECT = "reject"


# ============================================================
# Lead Schemas — Agent-S output
# ============================================================

class RawMinedLead(BaseModel):
    """Raw lead extracted from a mining source before qualification."""
    source: LeadSource
    company_name: str
    project_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    location: Optional[str] = None
    requirement_text: Optional[str] = None
    estimated_kva: Optional[int] = None
    rera_number: Optional[str] = None
    news_url: Optional[str] = None
    news_title: Optional[str] = None
    tender_id: Optional[str] = None
    tender_deadline: Optional[date] = None


class QualifiedLead(BaseModel):
    """Fully qualified lead — Agent-S output after scoring."""
    source: LeadSource
    company_name: str
    project_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    location: str = "Mumbai Suburban"
    requirement_text: str = ""
    estimated_kva: int = 0
    quantity: int = 1
    purchase_type: PurchaseType = PurchaseType.UNKNOWN
    temperature: Temperature = Temperature.COLD
    project_type: ProjectType = ProjectType.UNKNOWN
    segment: Segment = Segment.CONSTRUCTION
    lead_score: int = 0
    priority_action: PriorityAction = PriorityAction.LOW_PRIORITY
    needs_contact_enrichment: bool = False
    reasoning: str = ""
    source_reference: Optional[str] = None


# ============================================================
# Escalation Package — Agent-S → Agent-RM
# ============================================================

class ConversationMessage(BaseModel):
    sender: str
    message: str
    timestamp: datetime

class DiscoveryData(BaseModel):
    """What Agent-S has discovered about the customer's needs."""
    load_type: Optional[str] = "unknown"        # motors, lighting, mixed
    phase: Optional[str] = "unknown"             # 3-phase, 1-phase
    installation: Optional[str] = "unknown"      # outdoor, rooftop, basement
    zone: Optional[str] = "unknown"              # residential, commercial, industrial
    existing_dg: Optional[str] = "unknown"       # none, same_brand, different_brand
    ats_needed: Optional[str] = "unknown"        # yes, no
    altitude_m: Optional[int] = None
    ambient_temp_c: Optional[int] = None

class EscalationPackage(BaseModel):
    """Data contract: Agent-S → Agent-RM."""
    escalation_id: UUID = Field(default_factory=uuid4)
    from_agent: str = "agent_s_r1"
    to_agent: str = "agent_rm"
    priority: Temperature = Temperature.WARM
    reason: EscalationReason

    # Lead info
    lead_id: Optional[UUID] = None
    zoho_lead_id: Optional[str] = None
    customer_name: str
    company_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    location: str = ""
    segment: Segment = Segment.CONSTRUCTION
    purchase_type: PurchaseType = PurchaseType.UNKNOWN
    temperature: Temperature = Temperature.WARM
    lead_score: int = 0

    # Requirement
    requirement_text: str
    estimated_kva: Optional[int] = None
    quantity: int = 1
    application: Optional[str] = None            # construction_site, permanent_backup, both
    timeline: Optional[str] = None
    special_requirements: list[str] = []

    # Context
    conversation_history: list[ConversationMessage] = []
    discovery_data: Optional[DiscoveryData] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# Technical Configuration — Agent-RM → Agent-GM
# ============================================================

class BOMItem(BaseModel):
    item: str
    sku: Optional[str] = None
    code: Optional[str] = None
    qty: int = 1
    notes: Optional[str] = None

class ComplianceCheck(BaseModel):
    cpcb_iv_compliant: bool = True
    noise_zone: Optional[str] = None
    enclosure_suitable: bool = True
    state_specific_flags: list[str] = []

class DeliveryAssessment(BaseModel):
    standard_lead_time_weeks_min: int
    standard_lead_time_weeks_max: int
    customer_requested_date: Optional[date] = None
    feasibility: str = "feasible"                # feasible, tight, not_feasible

class TechnicalConfigPackage(BaseModel):
    """Data contract: Agent-RM → Agent-GM."""
    config_id: UUID = Field(default_factory=uuid4)
    lead_id: Optional[UUID] = None
    escalation_id: Optional[UUID] = None

    # Configuration
    kva_rating: float
    phase: str = "3-phase"
    engine_make: str
    engine_model: str
    alternator_make: str
    alternator_model: str
    controller: str = "DEIF SGC120"
    enclosure_type: EnclosureType
    panel_type: PanelType
    sku: Optional[str] = None

    # BOM
    bom: list[BOMItem]

    # Compliance & Delivery
    compliance: ComplianceCheck
    delivery: DeliveryAssessment

    # Metadata
    is_standard: bool = True
    non_standard_reason: Optional[str] = None
    price_tier_recommendation: str = "customer"  # customer, dealer
    segment: Segment = Segment.CONSTRUCTION
    quantity: int = 1
    notes: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# Deal Recommendation — Agent-GM → Human GM
# ============================================================

class PricingBreakdown(BaseModel):
    price_sheet: PanelType
    pep_price: float
    dealer_price: float
    customer_price: float
    recommended_price: float
    discount_pct: float = 0.0
    margin_above_pep_pct: float
    accessories_total: float = 0.0
    subtotal: float
    gst_18_pct: float
    freight_estimate: float = 0.0
    total_deal_value: float

class CommoditySnapshot(BaseModel):
    copper_mcx_per_kg: Optional[float] = None
    copper_trend: str = "stable"
    steel_per_tonne: Optional[float] = None
    steel_trend: str = "stable"
    inr_usd: Optional[float] = None
    forex_trend: str = "stable"
    impact: str = "none"                         # none, minor, significant

class DealRecommendation(BaseModel):
    """Data contract: Agent-GM → Human GM Dashboard."""
    recommendation_id: UUID = Field(default_factory=uuid4)
    lead_id: Optional[UUID] = None
    config_id: Optional[UUID] = None

    # Customer
    customer_name: str
    company_name: str
    segment: Segment
    purchase_type: PurchaseType
    existing_customer: bool = False

    # Configuration summary
    kva_rating: float
    quantity: int = 1
    configuration_summary: str = ""

    # Pricing
    pricing: PricingBreakdown

    # Context
    commodity_snapshot: CommoditySnapshot = CommoditySnapshot()
    payment_terms: str = "100% advance (new customer)"
    delivery_weeks: str = ""
    quote_validity_days: int = 30

    # Competitive
    competitor_mentioned: Optional[str] = None
    competitor_price: Optional[float] = None
    competitive_notes: Optional[str] = None

    # Recommendation
    recommendation: GMRecommendation
    reasoning: str
    risk_level: str = "low"                      # low, medium, high
    strategic_value: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


class ApprovalResponse(BaseModel):
    """Data contract: Human GM → System."""
    recommendation_id: UUID
    decision: GMDecision
    approved_price: Optional[float] = None
    modified_payment_terms: Optional[str] = None
    notes: Optional[str] = None
    decided_by: str = ""
    decided_at: datetime = Field(default_factory=datetime.utcnow)
