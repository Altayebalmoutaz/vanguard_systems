"""
Optional pgvector-backed CDT retrieval for the coding LLM (RAG-style memory).

Uses Jina `retrieval.query` + `match_cdt_codes` (same contract as the ingest scripts).
If Jina key is missing or RPC fails, returns an empty string so the agent still runs.
"""

from __future__ import annotations

from typing import Any

import httpx

from supabase import Client


def _jina_embed_query(api_key: str, text: str) -> list[float]:
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "jina-embeddings-v5-text-small",
                "input": [text],
                "task": "retrieval.query",
                "dimensions": 1024,
                "normalized": True,
            },
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


def format_cdt_vector_memory(rows: list[dict[str, Any]], *, max_lines: int = 12) -> str:
    lines: list[str] = []
    for row in rows[:max_lines]:
        code = str(row.get("code") or "").strip()
        desc = str(row.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 220:
            desc = desc[:217] + "..."
        sim = row.get("similarity")
        sim_s = f"{float(sim):.3f}" if sim is not None else "?"
        if code:
            lines.append(f"- {code} (similarity {sim_s}): {desc}")
    return "\n".join(lines)


def fetch_cdt_vector_memory(
    supabase: Client,
    clinical_note: str,
    payer: str,
    *,
    jina_api_key: str,
    match_count: int,
    match_threshold: float,
) -> str:
    """
    Returns plain text for injection into the coding LLM user prompt, or "" on skip/failure.
    """
    note = (clinical_note or "").strip()
    if not note:
        return ""

    try:
        embedding = _jina_embed_query(jina_api_key, note)
        rpc = supabase.rpc(
            "match_cdt_codes",
            {
                "query_embedding": embedding,
                "match_threshold": float(match_threshold),
                "match_count": int(match_count),
                "payer_filter": payer or "Delta Dental",
            },
        ).execute()
    except Exception:
        return ""

    rows = rpc.data if isinstance(rpc.data, list) else []
    if not rows:
        return ""

    body = format_cdt_vector_memory(rows)
    return (
        "Retrieved CDT candidates from the practice vector index (Supabase pgvector; verify clinically):\n"
        f"{body}\n"
        "Use these as hints only; final codes must match the clinical note and payer policy."
    )
