"""Shared pytest fixtures (e.g. stub ``supabase`` before imports that require it)."""

from __future__ import annotations

import sys
import types

import pytest

from app.config import Settings

# CI / minimal envs may lack a working ``supabase`` package; stub before first import.
if "supabase" not in sys.modules:
    _sb = types.ModuleType("supabase")

    class _Client:
        """Placeholder for type hints only."""

    _sb.Client = _Client
    _sb.create_client = lambda *a, **k: None
    sys.modules["supabase"] = _sb


@pytest.fixture
def neon_settings() -> Settings:
    """Settings with a placeholder Neon DSN for routing tests."""
    return Settings(
        neon_database_url="postgresql://test:test@localhost:5432/neondb?sslmode=require"
    )


@pytest.fixture
def supabase_only_settings() -> Settings:
    """Settings without Neon — Supabase fallback path."""
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
    )
