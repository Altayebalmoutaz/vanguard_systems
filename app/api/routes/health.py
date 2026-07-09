import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/")
def root(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {"message": "Agent system is running", "app": settings.app_name}


@router.get("/health")
async def health() -> dict:
    """Liveness: process is up. Point restart probes here."""
    return {"status": "ok"}


@router.get("/ready")
def ready(settings: Annotated[Settings, Depends(get_settings)]) -> JSONResponse:
    """Readiness: dependencies reachable. Point load-balancer routing probes here."""
    checks: dict[str, str] = {}
    ok = True

    from app.db.connection import NeonNotConfiguredError, neon_connection

    try:
        with neon_connection(settings) as conn, conn.cursor() as cur:
            cur.execute("select 1")
            cur.fetchone()
        checks["postgres"] = "ok"
    except NeonNotConfiguredError:
        checks["postgres"] = "not_configured"
        ok = False
    except Exception as exc:
        logger.warning("readiness postgres check failed: %s: %s", type(exc).__name__, exc)
        checks["postgres"] = "error"
        ok = False

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )
