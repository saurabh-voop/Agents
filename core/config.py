"""
Centralized configuration loaded from environment variables.
All secrets come from .env — never hardcoded.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- Database ---
    database_url: str = "postgresql+asyncpg://paikane_admin:@localhost:5432/paikane_agents"
    database_url_sync: str = "postgresql://paikane_admin:@localhost:5432/paikane_agents"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM Provider ---
    # Set LLM_PROVIDER=groq for free trial, LLM_PROVIDER=openai for production
    llm_provider: str = "groq"

    # --- OpenAI (production) ---
    openai_api_key: str = ""
    openai_model_default: str = "gpt-4o-mini"
    openai_model_advanced: str = "gpt-4o"

    # --- Groq (free trial — same OpenAI SDK format, just different URL + key) ---
    groq_api_key: str = ""
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_model_default: str = "llama-3.3-70b-versatile"
    groq_model_advanced: str = "llama-3.3-70b-versatile"

    # --- Zoho CRM ---
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_refresh_token: str = ""
    zoho_org_id: str = ""
    zoho_api_base: str = "https://www.zohoapis.in/crm/v6"
    zoho_auth_url: str = "https://accounts.zoho.in/oauth/v2/token"

    # --- Zoho Books ---
    zoho_books_org_id: str = ""
    zoho_books_api_base: str = "https://www.zohoapis.in/books/v3"

    # --- WhatsApp ---
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"

    # --- SendGrid ---
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "sales@paikanegroup.com"
    sendgrid_from_name: str = "Pai Kane Group"

    # --- Apollo.io ---
    apollo_api_key: str = ""
    apollo_api_url: str = "https://api.apollo.io/v1"

    # --- SerpAPI ---
    serpapi_key: str = ""
    serpapi_url: str = "https://serpapi.com/search"

    # --- Commodity ---
    commodity_api_key: str = ""
    commodity_api_url: str = "https://commodities-api.com/api"

    # --- Agent Config ---
    agent_s_mining_interval_hours: int = 2
    agent_s_followup_time: str = "09:00"
    agent_s_region: str = "R1"
    agent_s_sector: str = "construction"
    agent_s_location_filter: str = "Mumbai Suburban"

    # --- Commodity Fallback Defaults (used when DB has no data yet) ---
    hsd_price_fallback_inr: float = 90.0     # HSD per litre — update in .env if price changes
    usd_inr_baseline: float = 83.5           # USD/INR at time of last price list revision

    # --- Notification Emails ---
    gm_email: str = "saurabh.salunkhe@paikane.com"
    engineering_email: str = "engineering@paikane.com"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Dry Run (testing) ---
    # Set DRY_RUN=true to log all CRM/WhatsApp/Email writes without actually sending anything.
    # Flip to false when ready to go live.
    dry_run: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Docker Compose injects POSTGRES_USER etc. — ignore unknown env vars


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton — loaded once, reused everywhere."""
    return Settings()
