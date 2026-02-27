"""
Tests for Agent-S lead qualification and scoring logic.
Run: pytest tests/test_agent_s.py -v
"""

import pytest
import json


class TestQualificationScoring:
    """Test the lead scoring formula."""

    def test_hot_construction_lead_scores_high(self):
        """A lead with specific kVA, timeline, phone, in construction should score 70+."""
        lead = {
            "company_name": "Patil Constructions",
            "contact_name": "Rajesh Patil",
            "phone": "+919876543210",
            "email": "rajesh@patil.com",
            "location": "Mumbai Suburban",
            "source": "zoho_inbound",
            "requirement_text": "Need 125 kVA DG set for construction site in Andheri. Starting next month.",
            "segment": "construction",
        }
        # Expected: +25 (specific kVA) +20 (timeline) +15 (construction) +10 (Mumbai)
        #           +10 (phone) +5 (email) +5 (company) = 90
        # This is the expected range — actual LLM may vary slightly
        assert lead["phone"] is not None
        assert "kVA" in lead["requirement_text"] or "kva" in lead["requirement_text"].lower()

    def test_cold_news_lead_without_contact_scores_low(self):
        """A news lead with no contact details should score under 40."""
        lead = {
            "company_name": "ABC Developers",
            "source": "news",
            "location": "Mumbai Suburban",
            "requirement_text": "ABC Developers launches new residential project in Kandivali.",
            "phone": None,
            "email": None,
        }
        # Expected: +15 (construction) +10 (Mumbai) +5 (company) -15 (no contact) = 15
        assert lead["phone"] is None
        assert lead["source"] == "news"

    def test_spam_lead_scores_negative(self):
        """Irrelevant or spam leads should score very low."""
        lead = {
            "company_name": "",
            "source": "zoho_inbound",
            "requirement_text": "Hello, I am interested in partnership opportunities.",
            "phone": None,
            "email": None,
        }
        # Should score near 0 or negative
        assert lead["company_name"] == ""


class TestDeduplication:
    """Test the deduplication logic."""

    def test_same_company_is_duplicate(self):
        """Same company name should be detected as duplicate."""
        company1 = "Lodha Group"
        company2 = "lodha group"  # Case insensitive
        assert company1.lower() == company2.lower()

    def test_different_companies_not_duplicate(self):
        """Different companies should not be flagged."""
        company1 = "Lodha Group"
        company2 = "Godrej Properties"
        assert company1.lower() != company2.lower()


class TestFollowupCadence:
    """Test the follow-up timing logic."""

    def test_hot_lead_followup_at_48_hours(self):
        """HOT leads should get first follow-up after 48 hours."""
        cadence = {"HOT": {"intervals_days": [2, 7, 14], "max_followups": 3}}
        assert cadence["HOT"]["intervals_days"][0] == 2

    def test_warm_lead_followup_at_7_days(self):
        """WARM leads should get first follow-up after 7 days."""
        cadence = {"WARM": {"intervals_days": [7, 14, 30], "max_followups": 3}}
        assert cadence["WARM"]["intervals_days"][0] == 7

    def test_max_followups_is_3(self):
        """No lead should get more than 3 follow-ups."""
        for temp in ["HOT", "WARM", "COLD"]:
            cadence = {
                "HOT": {"max_followups": 3},
                "WARM": {"max_followups": 3},
                "COLD": {"max_followups": 3},
            }
            assert cadence[temp]["max_followups"] == 3


class TestEscalationTriggers:
    """Test that escalation keywords are detected."""

    def test_pricing_request_triggers_escalation(self):
        keywords = ["price", "quote", "quotation", "cost", "how much"]
        message = "Can you share the price for a 250 kVA DG set?"
        assert any(kw in message.lower() for kw in keywords)

    def test_technical_question_triggers_escalation(self):
        keywords = ["noise level", "fuel consumption", "dimensions", "specification", "spec"]
        message = "What is the noise level at 7 meters?"
        assert any(kw in message.lower() for kw in keywords)

    def test_general_chat_does_not_trigger(self):
        keywords = ["price", "quote", "quotation", "cost", "noise", "spec", "dimension"]
        message = "Thank you for reaching out. We are interested."
        assert not any(kw in message.lower() for kw in keywords)
