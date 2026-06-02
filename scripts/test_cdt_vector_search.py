#!/usr/bin/env python3
"""
Smoke-test Jina query embeddings + Supabase RPC match_cdt_codes.

Requires in Supabase: function public.match_cdt_codes(query_embedding, match_threshold,
match_count, payer_filter) returning rows with code, description, similarity, deny_rules,
bundling_rules, frequency_limits (shape must match your RPC).

Env: JINA_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _strip(raw: str) -> str:
    s = raw.strip().strip("\ufeff")
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1].strip()
    return s


def _env(name: str) -> str:
    v = os.getenv(name)
    if not v or not str(v).strip():
        raise SystemExit(f"Missing env: {name}")
    return _strip(str(v))


JINA_API_KEY = _env("JINA_API_KEY")
SUPABASE_URL = _env("SUPABASE_URL")
SUPABASE_KEY = _strip(
    str(os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "")
)
if not SUPABASE_KEY:
    raise SystemExit("Set SUPABASE_SERVICE_KEY or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


async def embed_query(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={
                "Authorization": f"Bearer {JINA_API_KEY}",
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
        response.raise_for_status()
        data = response.json()
        return data["data"][0]["embedding"]


async def test_search() -> None:
    test_queries = [
        "patient has 5mm pocket depths bleeding on probing bone loss periodontal scaling",
        "full mouth xrays bitewing radiographs",
        "tooth extraction simple lower molar",
        "crown preparation porcelain fused to metal upper molar",
        "comprehensive new patient oral examination",
    ]

    for query in test_queries:
        print(f"\nQuery: {query}")
        print("-" * 60)

        embedding = await embed_query(query)

        results = supabase.rpc(
            "match_cdt_codes",
            {
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": 3,
                "payer_filter": "Delta Dental",
            },
        ).execute()

        if not results.data:
            print("No results - try lowering match_threshold")
        else:
            for r in results.data:
                sim = float(r.get("similarity") or 0.0)
                print(f"  {r.get('code')} | {r.get('description')} | similarity: {sim:.3f}")
                if r.get("deny_rules"):
                    print("  [!] Deny rules exist")
                if r.get("bundling_rules"):
                    print("  [+] Bundling rules exist")
                if r.get("frequency_limits"):
                    print("  [~] Frequency limits exist")


if __name__ == "__main__":
    asyncio.run(test_search())
