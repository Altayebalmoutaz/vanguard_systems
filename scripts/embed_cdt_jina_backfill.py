#!/usr/bin/env python3
"""
Backfill Jina embeddings on public.cdt_codes.embedding (pgvector).

Requires in Supabase: extension vector, column cdt_codes.embedding (e.g. vector(1024)).

Env (repo-root .env):
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY  (or SUPABASE_KEY)
  JINA_API_KEY  (aliases: JINA_KEY, JINAAI_API_KEY — surrounding quotes stripped)

Optional:
  JINA_EMBEDDING_MODEL=jina-embeddings-v5-text-small
  JINA_EMBEDDING_DIMENSIONS=1024   # Jina v5 text-small max; must match cdt_codes.embedding vector(N)
  JINA_BATCH_SIZE=10
  CDT_EMBED_STATUS=active     # if set, only rows with this status (otherwise all rows with null embedding)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JINA_URL = "https://api.jina.ai/v1/embeddings"


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v or not str(v).strip():
        raise SystemExit(f"Missing required environment variable: {name}")
    return str(v).strip()


def _supabase_key() -> str:
    k = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not k or not str(k).strip():
        raise SystemExit("Set SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) for server-side updates.")
    return _strip_env_value(str(k))


def _strip_env_value(raw: str) -> str:
    s = raw.strip().strip("\ufeff")
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        return s[1:-1].strip()
    return s


def _jina_api_key() -> str:
    for name in ("JINA_API_KEY", "JINA_KEY", "JINAAI_API_KEY"):
        v = os.getenv(name)
        if v and str(v).strip():
            return _strip_env_value(str(v))
    raise SystemExit(
        "Missing Jina API key. Set JINA_API_KEY in .env (get a key from https://jina.ai/)."
    )


def build_embed_text(row: dict) -> str:
    """Stable, rich text for retrieval.passage embedding."""
    lines: list[str] = []
    code = (row.get("code") or "").strip()
    if code:
        lines.append(f"CDT Code: {code}")
    desc = (row.get("description") or "").strip()
    if desc:
        lines.append(f"Description: {desc}")
    cat = (row.get("category") or "").strip()
    if cat:
        lines.append(f"Category: {cat}")
    sub = (row.get("subcategory") or "").strip()
    if sub:
        lines.append(f"Subcategory: {sub}")
    kw = (row.get("keyword") or "").strip()
    if kw:
        lines.append(f"Keywords: {kw}")
    notes = (row.get("notes") or "").strip()
    if notes:
        lines.append(f"Notes: {notes}")
    if not lines:
        return f"CDT Code: {code or 'UNKNOWN'}"
    return "\n".join(lines)


def embed_batch(
    client: httpx.Client,
    *,
    api_key: str,
    model: str,
    dimensions: int,
    texts: list[str],
) -> list[list[float]]:
    r = client.post(
        JINA_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "input": texts,
            "task": "retrieval.passage",
            "dimensions": dimensions,
            "normalized": True,
        },
        timeout=120.0,
    )
    if r.status_code == 401:
        body = (r.text or "")[:300]
        raise SystemExit(
            "Jina API returned 401 Unauthorized. Fix JINA_API_KEY (no extra spaces/quotes; "
            "create a new key at jina.ai if this one was rotated). "
            f"Response snippet: {body!r}"
        )
    r.raise_for_status()
    data = r.json()
    out = [item["embedding"] for item in data["data"]]
    if len(out) != len(texts):
        raise RuntimeError("Jina response length mismatch")
    return out


def fetch_page(supabase, *, offset: int, limit: int, status_filter: str | None):
    q = (
        supabase.table("cdt_codes")
        .select("code, description, category, subcategory, notes, status")
        .is_("embedding", None)
        .order("code")
        .range(offset, offset + limit - 1)
    )
    if status_filter:
        q = q.eq("status", status_filter)
    return q.execute()


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Backfill Jina v5 embeddings on cdt_codes")
    parser.add_argument("--dry-run", action="store_true", help="Only print how many rows need embeddings")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0 = no cap)")
    parser.add_argument(
        "--test-jina",
        action="store_true",
        help="Call Jina once with a dummy string and exit (validates JINA_API_KEY only)",
    )
    args = parser.parse_args()

    url = _strip_env_value(_require_env("SUPABASE_URL"))
    key = _supabase_key()
    model = os.getenv("JINA_EMBEDDING_MODEL", "jina-embeddings-v5-text-small").strip()
    dimensions = int(os.getenv("JINA_EMBEDDING_DIMENSIONS", "1024"))
    batch_size = max(1, int(os.getenv("JINA_BATCH_SIZE", "10")))
    status_filter = os.getenv("CDT_EMBED_STATUS", "").strip() or None

    supabase = create_client(url, key)

    if args.test_jina:
        jina_key = _jina_api_key()
        with httpx.Client() as client:
            embed_batch(
                client,
                api_key=jina_key,
                model=model,
                dimensions=dimensions,
                texts=["CDT Code: D0120\nDescription: test ping"],
            )
        print("Jina API key OK (single test embedding returned).")
        return 0

    page_size = 200
    offset = 0
    total_seen = 0
    embedded = 0
    failed: list[str] = []

    # Probe first page
    try:
        fetch_page(supabase, offset=0, limit=1, status_filter=status_filter)
    except Exception as e:
        print(
            "Query failed. Ensure cdt_codes has an `embedding` column and optional columns match.\n"
            "If `keyword` does not exist, add it or we can narrow the select — error:\n",
            e,
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        # count approximate: scan pages
        n = 0
        off = 0
        while True:
            res = fetch_page(supabase, offset=off, limit=page_size, status_filter=status_filter)
            rows = res.data or []
            if not rows:
                break
            n += len(rows)
            off += page_size
        print(f"Dry run: ~{n} rows with null embedding" + (f" and status={status_filter!r}" if status_filter else ""))
        return 0

    jina_key = _jina_api_key()

    with httpx.Client() as client:
        while True:
            res = fetch_page(supabase, offset=offset, limit=page_size, status_filter=status_filter)
            rows = res.data or []
            if not rows:
                break

            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                if args.limit and embedded >= args.limit:
                    print(f"Stopped at --limit {args.limit}")
                    print(f"Embedded: {embedded}  Failed: {len(failed)}")
                    if failed:
                        print("Failed codes:", failed[:50], "..." if len(failed) > 50 else "")
                    return 0

                texts = [build_embed_text(r) for r in batch]
                try:
                    vectors = embed_batch(
                        client,
                        api_key=jina_key,
                        model=model,
                        dimensions=dimensions,
                        texts=texts,
                    )
                except SystemExit:
                    raise
                except Exception as e:
                    print(f"[ERROR] Jina embed batch at offset {offset + i}: {e}", file=sys.stderr)
                    failed.extend(str(r.get("code") or "") for r in batch)
                    continue

                for r, vec in zip(batch, vectors, strict=True):
                    if args.limit and embedded >= args.limit:
                        break
                    code = r.get("code")
                    if not code:
                        continue
                    try:
                        supabase.table("cdt_codes").update({"embedding": vec}).eq("code", code).execute()
                        embedded += 1
                    except Exception as e:
                        print(f"[ERROR] Supabase update {code!r}: {e}", file=sys.stderr)
                        failed.append(str(code))
                        continue

                total_seen += len(batch)
                print(f"Progress: embedded={embedded} (batch ok, page_offset={offset})")
                time.sleep(0.15)

            offset += page_size

    print("---")
    print(f"Complete: embedded={embedded}  failed={len(failed)}")
    if failed:
        print("Failed codes (retry later):", failed[:80], "..." if len(failed) > 80 else "")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
