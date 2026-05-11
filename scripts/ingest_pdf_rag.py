#!/usr/bin/env python3
"""
Ingest a PDF into public.rag_documents + rag_document_chunks with optional embeddings.

Prerequisites:
  1. Apply RAG migration (tables + match_rag_chunks in public; see supabase/migrations/005_coding_agent_rag.sql or a public-schema variant).
  2. .env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY
  3. For embeddings: OPENROUTER_API_KEY (and optional OPENROUTER_EMBEDDING_MODEL)

Usage (from repo root):
  python scripts/ingest_pdf_rag.py ./data/manuals/delta.pdf \\
    --slug delta-dental-manual-2024 \\
    --title "Delta Dental Provider Manual" \\
    --source-type delta_manual

Text-only (no vectors; similarity search will skip until you re-run with embeddings):
  python scripts/ingest_pdf_rag.py ./file.pdf --slug my-doc --title "T" --source-type other --no-embed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.config import get_settings
from app.integrations.supabase_client import get_supabase_client
from app.services.rag_ingest import (
    build_chunks_from_pages,
    embed_texts_openrouter,
    extract_pdf_pages,
)

SOURCE_TYPES = ("cdt", "icd10_cm", "delta_manual", "ny_manual", "other")


def _upsert_document(
    supabase,
    *,
    slug: str,
    title: str,
    source_type: str,
    file_name: str,
) -> str:
    tbl = supabase.table("rag_documents")
    existing = tbl.select("id").eq("slug", slug).limit(1).execute()
    row = {
        "slug": slug,
        "title": title,
        "source_type": source_type,
        "file_name": file_name,
    }
    if existing.data:
        doc_id = existing.data[0]["id"]
        tbl.update(
            {"title": title, "source_type": source_type, "file_name": file_name}
        ).eq("id", doc_id).execute()
        return str(doc_id)
    ins = tbl.insert(row).execute()
    if not ins.data:
        raise RuntimeError("Failed to insert rag_documents row")
    return str(ins.data[0]["id"])


def main() -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Ingest PDF into Supabase RAG tables")
    parser.add_argument("pdf", type=Path, help="Path to PDF file")
    parser.add_argument("--slug", required=True, help="Stable id, e.g. delta-manual-2024")
    parser.add_argument("--title", required=True, help="Human-readable title")
    parser.add_argument(
        "--source-type",
        required=True,
        choices=SOURCE_TYPES,
        help="Corpus type for filtering at retrieval time",
    )
    parser.add_argument("--chunk-size", type=int, default=1200, help="Characters per chunk")
    parser.add_argument("--overlap", type=int, default=200, help="Character overlap between chunks")
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Store chunks without embeddings (faster; run again without this to embed)",
    )
    parser.add_argument("--embed-batch-size", type=int, default=16, help="Texts per embedding API call")
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    supabase = get_supabase_client()
    settings = get_settings()

    # Step 1: extract pages
    pages = extract_pdf_pages(pdf_path)
    nonempty = sum(1 for _, t in pages if t.strip())
    if nonempty == 0:
        print("Warning: no text extracted (scanned PDF?). OCR pipeline not included.", file=sys.stderr)

    # Step 2: chunk
    chunks = build_chunks_from_pages(pages, args.chunk_size, args.overlap)
    if not chunks:
        print("No chunks produced; exiting.", file=sys.stderr)
        return 1

    # Step 3: register document (re-ingest replaces chunks for same slug)
    doc_id = _upsert_document(
        supabase,
        slug=args.slug,
        title=args.title,
        source_type=args.source_type,
        file_name=pdf_path.name,
    )

    ch_tbl = supabase.table("rag_document_chunks")
    ch_tbl.delete().eq("document_id", doc_id).execute()

    # Step 4: optional embeddings
    texts = [c["content"] for c in chunks]
    vectors: list[list[float] | None]
    if args.no_embed:
        vectors = [None] * len(texts)
    else:
        vectors = embed_texts_openrouter(settings, texts, batch_size=args.embed_batch_size)

    # Step 5: insert rows
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        slice_chunks = chunks[i : i + batch_size]
        slice_vecs = vectors[i : i + batch_size]
        rows = []
        for ch, vec in zip(slice_chunks, slice_vecs, strict=True):
            rec: dict = {
                "document_id": doc_id,
                "chunk_index": ch["chunk_index"],
                "page_start": ch["page_start"],
                "page_end": ch["page_end"],
                "content": ch["content"],
                "metadata": ch["metadata"],
            }
            if vec is not None:
                rec["embedding"] = vec
            rows.append(rec)
        ch_tbl.insert(rows).execute()

    print(
        f"OK: document_id={doc_id} schema=public chunks={len(chunks)} "
        f"embedded={not args.no_embed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
