"""Shared pytest fixtures (e.g. stub ``supabase`` before imports that require it)."""

from __future__ import annotations

import sys
import types

# CI / minimal envs may lack a working ``supabase`` package; stub before first import.
if "supabase" not in sys.modules:
    _sb = types.ModuleType("supabase")

    class _Client:
        """Placeholder for type hints only."""

    _sb.Client = _Client
    _sb.create_client = lambda *a, **k: None
    sys.modules["supabase"] = _sb
