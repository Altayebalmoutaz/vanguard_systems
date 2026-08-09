"""FastAPI sub-app — Vanguard MD Coding Agent (scribe-facing)."""

from __future__ import annotations

import logging
import secrets

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.errors import sanitized_http_exception
from app.coding.config import get_coding_settings
from app.coding.decisions import run_record_decision
from app.coding.schemas import (
    CodingDecisionRequest,
    CodingDecisionResponse,
    CodingSuggestRequest,
    CodingSuggestResponse,
)
from app.coding.service import run_coding_suggest
from app.logging_config import CorrelationIdMiddleware
from app.security.phi import scrub_for_log

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vanguard MD Coding Agent",
    version="1.0.0",
    description=(
        "Solo coding agent for structured scribe payloads. "
        "POST /v1/suggest returns line-level CDT recommendations for real-time dentist review."
    ),
)


class CodingAgentApiKeyMiddleware(BaseHTTPMiddleware):
    """Bearer API key guard (mirrors eligibility sub-app). Empty key = open local mode."""

    async def dispatch(self, request: Request, call_next):
        cfg = get_coding_settings()
        key = (cfg.coding_agent_api_key or "").strip()
        if not key:
            return await call_next(request)
        path = request.url.path or ""
        if request.method == "GET" and path.rstrip("/").endswith("/health"):
            return await call_next(request)
        auth = request.headers.get("authorization") or ""
        if not auth.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "missing_or_invalid_bearer"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth[7:]
        if not secrets.compare_digest(token, key):
            return JSONResponse(
                status_code=401,
                content={"detail": "invalid_api_key"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


app.add_middleware(CodingAgentApiKeyMiddleware)
app.add_middleware(CorrelationIdMiddleware)

_cors = get_coding_settings().cors_origins_list
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "coding-agent"}


@app.post("/v1/suggest", response_model=CodingSuggestResponse)
def suggest_codes(body: CodingSuggestRequest) -> CodingSuggestResponse:
    """
    Accept a structured scribe payload and return line-level coding recommendations
    synchronously for dentist approval in the scribe UI.
    """
    try:
        return run_coding_suggest(body)
    except Exception as exc:
        raise sanitized_http_exception(
            500,
            public_message="Failed to run coding suggest",
            log_message=f"coding suggest failure: {scrub_for_log(str(exc))}",
            exc=exc,
        ) from exc


@app.post("/v1/decision", response_model=CodingDecisionResponse)
def record_decision(body: CodingDecisionRequest) -> CodingDecisionResponse:
    """
    Record what the dentist did with a prior suggest run's lines
    (approved / edited / rejected / added). This is the coding agent's
    ground truth for CDT top-1 accuracy and the live scorecard.
    """
    try:
        return run_record_decision(body)
    except Exception as exc:
        raise sanitized_http_exception(
            500,
            public_message="Failed to record coding decision",
            log_message=f"coding decision failure: {scrub_for_log(str(exc))}",
            exc=exc,
        ) from exc
