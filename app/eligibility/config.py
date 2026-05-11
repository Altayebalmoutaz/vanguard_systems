"""Environment configuration for the Eligibility Agent service."""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EligibilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    stedi_api_key: str = Field(default="", validation_alias="STEDI_API_KEY")
    stedi_base_url: str = Field(
        default="https://healthcare.us.stedi.com/2024-04-01",
        validation_alias="STEDI_BASE_URL",
    )
    stedi_eligibility_path: str = Field(
        default="/change/medicalnetwork/eligibility/v3",
        validation_alias="STEDI_ELIGIBILITY_PATH",
    )
    stedi_manager_base_url: str = Field(
        default="https://manager.us.stedi.com/2024-04-01",
        validation_alias="STEDI_MANAGER_BASE_URL",
    )
    stedi_batch_eligibility_path: str = Field(
        default="/eligibility-manager/batch-eligibility",
        validation_alias="STEDI_BATCH_ELIGIBILITY_PATH",
    )
    stedi_timeout_seconds: float = Field(default=60.0, validation_alias="STEDI_TIMEOUT_SECONDS")
    stedi_batch_timeout_seconds: float = Field(default=120.0, validation_alias="STEDI_BATCH_TIMEOUT_SECONDS")
    stedi_max_retries: int = Field(default=3, validation_alias="STEDI_MAX_RETRIES")
    stedi_retry_base_seconds: float = Field(default=0.5, validation_alias="STEDI_RETRY_BASE_SECONDS")
    stedi_retry_max_seconds: float = Field(default=4.0, validation_alias="STEDI_RETRY_MAX_SECONDS")
    stedi_retry_jitter_seconds: float = Field(default=0.1, validation_alias="STEDI_RETRY_JITTER_SECONDS")
    # Dental mock requests with a test API key need `stedi-test: true` (see Stedi mock eligibility docs).
    stedi_test_header: bool = Field(default=False, validation_alias="STEDI_TEST_HEADER")

    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_key: str = Field(
        default="",
        validation_alias=AliasChoices("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )

    # Mock defaults for local/Stedi sandbox; set PROVIDER_* in .env for production.
    provider_npi: str = Field(default="1999999984", validation_alias="PROVIDER_NPI")
    provider_name: str = Field(default="Mock Dental Practice", validation_alias="PROVIDER_NAME")
    provider_tax_id: str = Field(default="123456789", validation_alias="PROVIDER_TAX_ID")

    # Optional Layer 3 LLM (OpenRouter-compatible chat completions). Off unless enabled + key.
    eligibility_layer3_llm_enrich_enabled: bool = Field(
        default=False,
        validation_alias="ELIGIBILITY_LAYER3_LLM_ENRICH_ENABLED",
    )
    eligibility_layer3_llm_openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ELIGIBILITY_LAYER3_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    )
    eligibility_layer3_llm_model: str = Field(
        default="openai/gpt-4o-mini",
        validation_alias="ELIGIBILITY_LAYER3_LLM_MODEL",
    )
    eligibility_layer3_llm_timeout_seconds: float = Field(
        default=45.0,
        validation_alias="ELIGIBILITY_LAYER3_LLM_TIMEOUT_SECONDS",
    )

    cache_ttl_days: int = Field(default=7, validation_alias="ELIGIBILITY_CACHE_TTL_DAYS")

    # Layer 5: when payer_fee_schedules has no row (or zero fee) for a requested CDT, fill from
    # built-in illustrative UCR + optional ELIGIBILITY_UCR_FALLBACK_JSON. Off by default for production.
    eligibility_ucr_fallback_enabled: bool = Field(
        default=False,
        validation_alias="ELIGIBILITY_UCR_FALLBACK_ENABLED",
    )
    eligibility_ucr_fallback_json: str = Field(
        default="",
        validation_alias="ELIGIBILITY_UCR_FALLBACK_JSON",
    )

    # When set (e.g. for Supabase `process-eligibility-request` → FastAPI via ngrok), every route
    # except GET /health requires `Authorization: Bearer <key>`.
    eligibility_agent_api_key: str = Field(
        default="",
        validation_alias="ELIGIBILITY_AGENT_API_KEY",
    )

    # OpenDental connector (local demo defaults to Open Dental Local API mode).
    opendental_base_url: str = Field(
        default="http://localhost:30222/api/v1",
        validation_alias="OPENDENTAL_BASE_URL",
    )
    opendental_developer_key: str = Field(default="", validation_alias="OPENDENTAL_DEVELOPER_KEY")
    opendental_customer_key: str = Field(default="", validation_alias="OPENDENTAL_CUSTOMER_KEY")
    opendental_timeout_seconds: float = Field(default=15.0, validation_alias="OPENDENTAL_TIMEOUT_SECONDS")
    opendental_writeback_enabled: bool = Field(
        default=False,
        validation_alias="OPENDENTAL_WRITEBACK_ENABLED",
    )
    # When set, OpenDental client reads fixtures from disk instead of issuing HTTP calls.
    opendental_replay_dir: str = Field(default="", validation_alias="OPENDENTAL_REPLAY_DIR")


@lru_cache
def get_settings() -> EligibilitySettings:
    return EligibilitySettings()
