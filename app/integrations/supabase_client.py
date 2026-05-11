"""
Single source of truth for Supabase clients.

Two factories cover the two access patterns in the codebase:

- :func:`create_supabase` — explicit, settings-driven; returns ``Client | None`` (no exception
  when creds are missing). Use from request handlers / agents that have access to ``Settings``
  via FastAPI ``Depends``.

- :func:`get_supabase_client` — process-wide singleton, raises ``RuntimeError`` when creds
  are missing. Reads from :class:`Settings` first, falls back to direct ``os.environ`` for
  scripts that bypass pydantic-settings.

Both prefer the **service-role** key (RLS-bypassing) and fall back to anon. Pass an explicit
``key`` argument when you need to enforce a specific posture (e.g. anon for end-user contexts).
"""

from __future__ import annotations

import os
from typing import Literal

from app.config import Settings, get_settings
from supabase import Client, create_client

KeyMode = Literal["service_role", "anon"]


def _pick_key(settings: Settings, mode: KeyMode | None) -> str:
    if mode == "service_role":
        return settings.supabase_service_role_key
    if mode == "anon":
        return settings.supabase_anon_key
    return settings.supabase_service_role_key or settings.supabase_anon_key


def create_supabase(settings: Settings, *, key_mode: KeyMode | None = None) -> Client | None:
    """Return a Supabase client, or None if credentials are not configured."""
    key = _pick_key(settings, key_mode)
    if not settings.supabase_url or not key:
        return None
    return create_client(settings.supabase_url, key)


_singleton: Client | None = None


def get_supabase_client() -> Client:
    """
    Process-wide cached Supabase client.

    Resolution order:
    1. ``Settings.supabase_service_role_key`` -> ``Settings.supabase_anon_key``
    2. Env vars: ``SUPABASE_KEY`` (legacy) -> ``SUPABASE_SERVICE_ROLE_KEY`` -> ``SUPABASE_ANON_KEY``

    Raises ``RuntimeError`` if no credentials resolve.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    settings = get_settings()
    url = settings.supabase_url or os.getenv("SUPABASE_URL", "")
    key = (
        settings.supabase_service_role_key
        or settings.supabase_anon_key
        or os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    )
    if not url or not key:
        raise RuntimeError(
            "Missing Supabase credentials. Set SUPABASE_URL plus one of "
            "SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY, or (legacy) SUPABASE_KEY."
        )
    _singleton = create_client(url, key)
    return _singleton


def reset_supabase_singleton() -> None:
    """Test hook: drop the cached singleton so the next call rebuilds."""
    global _singleton
    _singleton = None
