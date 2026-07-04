from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.db.connection import get_neon_dsn
from app.integrations.supabase_client import create_supabase
from app.runtime.context import AgentContext


def get_supabase(settings: Annotated[Settings, Depends(get_settings)]):
    return create_supabase(settings)


def phi_plane_configured(settings: Annotated[Settings, Depends(get_settings)]) -> bool:
    """True when Neon PHI-plane Postgres is configured."""
    return get_neon_dsn(settings) is not None


def get_agent_context(
    settings: Annotated[Settings, Depends(get_settings)],
    supabase=Depends(get_supabase),
) -> AgentContext:
    return AgentContext(settings=settings, supabase=supabase)
