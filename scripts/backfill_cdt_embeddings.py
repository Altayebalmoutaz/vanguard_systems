#!/usr/bin/env python3
"""
Vanguard MD — CDT codes embedding backfill via Jina AI (passage) → Supabase public.cdt_codes.embedding.

Requires: vector(1024) on cdt_codes.embedding, Jina API key, Supabase service key.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

import httpx

from supabase import create_client

JINA_URL = "https://api.jina.ai/v1/embeddings"
EMBEDDING_MODEL = "jina-embeddings-v5-text-small"
EMBEDDING_DIMENSIONS = 1024
BATCH_SIZE = 10


def _strip_env_value(raw: str) -> str:
    s = raw.strip().strip("\ufeff")
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1].strip()
    return s


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v or not str(v).strip():
        raise SystemExit(f"Missing required environment variable: {name}")
    return _strip_env_value(str(v))


def _supabase_service_key() -> str:
    k = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not k or not str(k).strip():
        raise SystemExit("Set SUPABASE_SERVICE_KEY (or SUPABASE_SERVICE_ROLE_KEY).")
    return _strip_env_value(str(k))


def _jina_api_key() -> str:
    for name in ("JINA_API_KEY", "JINA_KEY"):
        v = os.getenv(name)
        if v and str(v).strip():
            return _strip_env_value(str(v))
    raise SystemExit("Missing JINA_API_KEY.")


def build_cdt_text(code: dict) -> str:
    parts = [
        f"CDT Code: {code.get('code', '')}",
        f"Description: {code.get('description', '')}",
        f"Category: {code.get('category', '')}",
        f"Subcategory: {code.get('subcategory', '')}",
        f"Keywords: {code.get('keyword', '')}",
        f"Notes: {code.get('notes', '')}",
    ]
    out: list[str] = []
    for p in parts:
        if ": " not in p:
            continue
        _, rest = p.split(": ", 1)
        if rest.strip():
            out.append(p)
    return "\n".join(out) if out else f"CDT Code: {code.get('code', 'UNKNOWN')}"


def embed_batch(
    client: httpx.Client,
    *,
    api_key: str,
    texts: list[str],
) -> list[list[float]]:
    r = client.post(
        JINA_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": EMBEDDING_MODEL,
            "input": texts,
            "task": "retrieval.passage",
            "dimensions": EMBEDDING_DIMENSIONS,
            "normalized": True,
        },
        timeout=120.0,
    )
    if r.status_code == 401:
        raise SystemExit(
            "Jina 401 Unauthorized — check JINA_API_KEY. "
            + (r.text or "")[:400]
        )
    r.raise_for_status()
    data = r.json()
    return [item["embedding"] for item in data["data"]]


def fetch_page(supabase, *, offset: int, limit: int):
    return (
        supabase.table("cdt_codes")
        .select("code, description, category, subcategory, keyword, notes, status")
        .is_("embedding", None)
        .order("code")
        .range(offset, offset + limit - 1)
        .execute()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    url = _require_env("SUPABASE_URL")
    key = _supabase_service_key()
    jina_key = _jina_api_key()

    supabase = create_client(url, key)
    page_size = 200
    embedded = 0
    failed: list[str] = []

    if args.dry_run:
        n = 0
        off = 0
        while True:
            res = fetch_page(supabase, offset=off, limit=page_size)
            rows = res.data or []
            if not rows:
                break
            n += len(rows)
            off += page_size
        print(f"Dry run: {n} active CDT rows with null embedding")
        return 0

    with httpx.Client() as client:
        offset = 0
        while True:
            res = fetch_page(supabase, offset=offset, limit=page_size)
            rows = res.data or []
            if not rows:
                break

            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i : i + BATCH_SIZE]
                if args.limit and embedded >= args.limit:
                    print(f"Stopped at --limit {args.limit}; embedded={embedded}")
                    return 0 if not failed else 1

                texts = [build_cdt_text(c) for c in batch]
                try:
                    vectors = embed_batch(client, api_key=jina_key, texts=texts)
                except Exception as e:
                    print(f"[ERROR] Jina batch offset {offset + i}: {e}", file=sys.stderr)
                    failed.extend(str(c.get("code") or "") for c in batch)
                    continue

                for c, vec in zip(batch, vectors, strict=True):
                    code = c.get("code")
                    if not code:
                        continue
                    try:
                        supabase.table("cdt_codes").update({"embedding": vec}).eq("code", code).execute()
                        embedded += 1
                    except Exception as e:
                        print(f"[ERROR] Supabase update {code!r}: {e}", file=sys.stderr)
                        failed.append(str(code))

                print(f"Progress: embedded={embedded}")
                time.sleep(0.15)

            offset += page_size

    print(f"Complete: embedded={embedded} failed={len(failed)}")
    if failed:
        print("Failed codes:", failed[:60], "..." if len(failed) > 60 else "")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
