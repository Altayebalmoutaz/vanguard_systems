"""
Extract text from PDFs, chunk for RAG, and optionally embed via OpenRouter.

Used by scripts/ingest_pdf_rag.py; keeps heavy logic out of the CLI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"
EXPECTED_EMBEDDING_DIM = 1536


def extract_pdf_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return 1-based page numbers and raw text per page."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("Install pypdf: pip install pypdf") from e

    reader = PdfReader(str(pdf_path))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        text = re.sub(r"\s+", " ", raw).strip()
        pages.append((i, text))
    return pages


def chunk_page_text(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """Sliding character windows within one page."""
    text = text.strip()
    if not text:
        return []
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = end - overlap
    return chunks


def build_chunks_from_pages(
    pages: list[tuple[int, str]],
    chunk_size: int,
    overlap: int,
) -> list[dict[str, Any]]:
    """Produce chunk records with stable chunk_index and page metadata."""
    out: list[dict[str, Any]] = []
    idx = 0
    for page_num, text in pages:
        for piece in chunk_page_text(text, chunk_size, overlap):
            out.append(
                {
                    "chunk_index": idx,
                    "page_start": page_num,
                    "page_end": page_num,
                    "content": piece,
                    "metadata": {"page": page_num},
                }
            )
            idx += 1
    return out


def embed_texts_openrouter(
    settings: Settings,
    texts: list[str],
    *,
    model: str | None = None,
    batch_size: int = 16,
) -> list[list[float]]:
    """
    Batch embeddings via OpenRouter (OpenAI-compatible /v1/embeddings).

    Set OPENROUTER_EMBEDDING_MODEL in env (default: openai/text-embedding-3-small).
    """
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (required for embeddings)")

    model = model or settings.openrouter_embedding_model
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_http_referer or "https://localhost",
        "X-Title": settings.app_name,
    }

    all_vectors: list[list[float]] = []
    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            payload = {"model": model, "input": batch}
            r = client.post(OPENROUTER_EMBEDDINGS_URL, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            items = data.get("data") or []
            items_sorted = sorted(items, key=lambda x: x.get("index", 0))
            batch_vectors: list[list[float]] = []
            for item in items_sorted:
                vec = item.get("embedding")
                if not isinstance(vec, list):
                    raise RuntimeError("Embedding response missing embedding array")
                if len(vec) != EXPECTED_EMBEDDING_DIM:
                    raise RuntimeError(
                        f"Embedding dim {len(vec)} != {EXPECTED_EMBEDDING_DIM} "
                        f"(adjust migration vector size or embedding model)"
                    )
                batch_vectors.append([float(x) for x in vec])
            if len(batch_vectors) != len(batch):
                raise RuntimeError("Embedding batch size mismatch from provider")
            all_vectors.extend(batch_vectors)
    return all_vectors
