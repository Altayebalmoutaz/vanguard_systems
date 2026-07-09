"""Resolve the application Postgres DSN from environment."""

from __future__ import annotations

from urllib.parse import quote_plus, urlparse


def resolve_database_url(
    *,
    database_url: str | None = None,
    supabase_url: str | None = None,
    supabase_db_password: str | None = None,
    supabase_pooler_host: str | None = None,
) -> str | None:
    """Return a psycopg DSN from explicit DATABASE_URL or Supabase URL + password.

    On IPv4-only networks the direct host ``db.<ref>.supabase.co`` is often IPv6-only
    (connection fails on Windows). Set ``SUPABASE_POOLER_HOST`` to the session pooler
    from Dashboard → Database → Connection string (e.g. ``aws-1-eu-west-1.pooler.supabase.com``).
    """
    direct = (database_url or "").strip()
    if direct:
        return direct

    pw = (supabase_db_password or "").strip()
    raw = (supabase_url or "").strip()
    if not pw or not raw:
        return None

    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host.endswith(".supabase.co"):
        return None

    ref = host.removesuffix(".supabase.co")
    encoded_pw = quote_plus(pw)

    pooler = (supabase_pooler_host or "").strip()
    if pooler:
        return (
            f"postgresql://postgres.{ref}:{encoded_pw}@{pooler}:5432/postgres"
            "?sslmode=require"
        )

    return (
        f"postgresql://postgres:{encoded_pw}@db.{ref}.supabase.co:5432/postgres"
        "?sslmode=require"
    )
