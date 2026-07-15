"""Deprecated alias — use scripts/apply_schema_migrations.py."""

from __future__ import annotations

import warnings

from scripts.apply_schema_migrations import main

if __name__ == "__main__":
    warnings.warn(
        "apply_neon_migrations.py is deprecated; use apply_schema_migrations.py "
        "(schema lives under schema/migrations/, applied to Supabase via DATABASE_URL).",
        DeprecationWarning,
        stacklevel=1,
    )
    main()
