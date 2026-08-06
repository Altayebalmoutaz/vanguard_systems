"""Environment configuration for the Coding Agent sub-app."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CodingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Mount + partner auth (Bearer). Empty key = open in local/dev (mirrors eligibility).
    coding_agent_enabled: bool = Field(default=True, validation_alias="CODING_AGENT_ENABLED")
    coding_agent_api_key: str = Field(default="", validation_alias="CODING_AGENT_API_KEY")
    coding_agent_cors_origins: str = Field(
        default="",
        validation_alias="CODING_AGENT_CORS_ORIGINS",
        description="Comma-separated origins; empty disables CORS middleware.",
    )

    # Latency / quality knobs for the scribe sync path.
    coding_llm_timeout_seconds: float = Field(
        default=25.0, validation_alias="CODING_LLM_TIMEOUT_SECONDS"
    )
    coding_llm_max_retries: int = Field(default=1, validation_alias="CODING_LLM_MAX_RETRIES")
    coding_reference_cache_ttl_seconds: float = Field(
        default=300.0, validation_alias="CODING_REFERENCE_CACHE_TTL_SECONDS"
    )
    coding_confidence_review_threshold: float = Field(
        default=0.75, validation_alias="CODING_CONFIDENCE_REVIEW_THRESHOLD"
    )
    coding_default_fast_mode: bool = Field(
        default=False, validation_alias="CODING_DEFAULT_FAST_MODE"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.coding_agent_cors_origins.split(",") if o.strip()]


@lru_cache
def get_coding_settings() -> CodingSettings:
    return CodingSettings()
