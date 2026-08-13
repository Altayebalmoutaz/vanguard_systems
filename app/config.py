from functools import lru_cache
from typing import Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.database_url import resolve_database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "dental-rcm-agents"
    environment: str = "development"
    log_level: str = "INFO"
    sentry_dsn: str | None = None
    supabase_url: str | None = None
    # Prefer service role on the server (bypasses RLS). If unset, anon key is used (must match your RLS policies).
    supabase_service_role_key: str | None = None
    supabase_anon_key: str | None = None
    # Database password from Supabase Dashboard → Settings → Database (not your Google login).
    supabase_db_password: str | None = Field(default=None, validation_alias="SUPABASE_DB_PASSWORD")
    # IPv4 session pooler host, e.g. aws-1-eu-west-1.pooler.supabase.com (copy from Dashboard).
    supabase_pooler_host: str | None = Field(default=None, validation_alias="SUPABASE_POOLER_HOST")

    # OpenRouter (OpenAI-compatible chat completions)
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    # Some providers require a referer; OpenRouter recommends setting site URL
    openrouter_http_referer: str | None = None
    openrouter_timeout_seconds: float = 120.0
    openrouter_max_retries: int = 3

    # Optional: Jina + Supabase RPC `match_cdt_codes` injects vector-retrieved CDT hints into the coding LLM.
    jina_api_key: str | None = None
    cdt_vector_match_count: int = 8
    cdt_vector_match_threshold: float = 0.3

    # --- Stedi 837 (claim) submission ---
    # When `stedi_claims_api_key` is set, `app.tools.claim_tools.submit_claim_tool`
    # delegates to the real Stedi Healthcare Claims API. Without a key, submission
    # fails unless `allow_claim_mock_submission` is enabled (local dev / tests only).
    stedi_claims_api_key: str | None = None
    allow_claim_mock_submission: bool = False
    stedi_claims_base_url: str = "https://healthcare.us.stedi.com"
    stedi_claims_dental_path: str = "/2024-04-01/change/medical/claims"
    # Stedi sandbox accepts a `stedi-test: true` header to bypass real payer routing.
    stedi_claims_test_header: bool = True
    stedi_claims_timeout_seconds: float = 30.0

    # --- Authentication ---
    # When `1`/`true`, every non-public route requires either:
    #   1. Authorization: Bearer <Supabase JWT> verified against `supabase_jwt_secret`, or
    #   2. X-API-Key matching one of the comma-separated entries in `internal_api_keys`.
    # Tests / local dev can leave this off; production deployments must set REQUIRE_AUTH=1.
    require_auth: bool = False
    supabase_jwt_secret: str | None = None
    # Comma-separated list of allowed static API keys (server-to-server).
    internal_api_keys: str = ""
    # Canonical psycopg DSN for the application Postgres (Supabase Postgres for the
    # Supabase-only pilot). `NEON_DATABASE_URL` is kept as a back-compat alias.
    neon_database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "NEON_DATABASE_URL"),
    )
    # When true, JWT callers must resolve to at least one platform.user_practice_roles row.
    require_rbac: bool = False

    # --- Durable pipeline worker (Phase 3) ---
    pipeline_worker_enabled: bool = False
    pipeline_worker_interval_seconds: float = 5.0
    pipeline_worker_batch_size: int = 5
    pipeline_retry_delay_seconds: float = 30.0
    pipeline_dlq_alert_threshold: int = 3
    confidence_hitl_threshold: float = 0.85

    # Wave 9: shadow pilot — eligibility runs, no OD write-back or claim submit.
    pilot_shadow_mode: bool = Field(default=False, validation_alias="PILOT_SHADOW_MODE")
    # Default tenant for background workers (OD poller) when request has no practice_id.
    pilot_default_practice_id: str = Field(
        default="",
        validation_alias="PILOT_DEFAULT_PRACTICE_ID",
    )

    # Standalone OD write-back service (default unset = in-process writeback).
    odwb_service_url: str = Field(default="", validation_alias="ODWB_SERVICE_URL")
    odwb_api_key: str = Field(default="", validation_alias="ODWB_API_KEY")
    odwb_timeout_seconds: float = Field(default=60.0, validation_alias="ODWB_TIMEOUT_SECONDS")

    @model_validator(mode="after")
    def _resolve_database_dsn(self) -> Self:
        resolved = resolve_database_url(
            database_url=self.neon_database_url,
            supabase_url=self.supabase_url,
            supabase_db_password=self.supabase_db_password,
            supabase_pooler_host=self.supabase_pooler_host,
        )
        if resolved:
            object.__setattr__(self, "neon_database_url", resolved)
        return self

    @property
    def internal_api_keys_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.internal_api_keys.split(",") if k.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
