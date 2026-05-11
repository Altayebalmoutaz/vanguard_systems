from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.integrations.supabase_client import create_supabase
from app.runtime.context import AgentContext


def get_supabase(settings: Annotated[Settings, Depends(get_settings)]):
    return create_supabase(settings)


def get_agent_context(
    settings: Annotated[Settings, Depends(get_settings)],
    supabase=Depends(get_supabase),
) -> AgentContext:
    return AgentContext(settings=settings, supabase=supabase)
