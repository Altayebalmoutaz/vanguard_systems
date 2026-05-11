from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "dental-rcm-agents"
    supabase_url: str | None = None
    # Prefer service role on the server (bypasses RLS). If unset, anon key is used (must match your RLS policies).
    supabase_service_role_key: str | None = None
    supabase_anon_key: str | None = None

    # OpenRouter (OpenAI-compatible chat completions)
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    # Some providers require a referer; OpenRouter recommends setting site URL
    openrouter_http_referer: str | None = None

    # Optional: Jina + Supabase RPC `match_cdt_codes` injects vector-retrieved CDT hints into the coding LLM.
    jina_api_key: str | None = None
    cdt_vector_match_count: int = 8
    cdt_vector_match_threshold: float = 0.3

    # --- Stedi 837 (claim) submission ---
    # When `stedi_claims_api_key` is set, `app.tools.claim_tools.submit_claim_tool`
    # delegates to the real Stedi Healthcare Claims API; otherwise it falls back to
    # the mock adapter (`stedi_mock`) for local development and offline tests.
    stedi_claims_api_key: str | None = None
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

    @property
    def internal_api_keys_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.internal_api_keys.split(",") if k.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
