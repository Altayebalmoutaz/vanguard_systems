import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI

from app.api.auth import require_principal
from app.api.routes import agents, coding, health, legacy, rcm, review
from app.config import get_settings
from app.eligibility.config import get_settings as get_eligibility_settings
from app.eligibility.main import app as eligibility_agent_app
from app.eligibility.main import run_from_opendental
from app.integrations.opendental.poller import start_appointment_poller

logger = logging.getLogger(__name__)

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    # Starlette does NOT run the lifespan of mounted sub-apps, so the OpenDental
    # appointment poller (defined on the eligibility app) must be started here.
    eligibility_settings = get_eligibility_settings()
    poller_task = None
    if eligibility_settings.opendental_auto_poll_enabled:
        poller_task = start_appointment_poller(run_from_opendental, eligibility_settings)
        logger.warning(
            "OpenDental auto-poll enabled (interval=%ss, window_days=%s)",
            eligibility_settings.opendental_auto_poll_interval_seconds,
            eligibility_settings.opendental_auto_poll_date_window_days,
        )
    try:
        yield
    finally:
        if poller_task is not None:
            poller_task.cancel()
            # CancelledError is a BaseException (not Exception) in 3.8+, so suppress it explicitly.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await poller_task


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # Health is intentionally public so load balancers / probes can reach it.
    app.include_router(health.router)

    # Every other router is gated by the central auth dependency. When
    # `Settings.require_auth` is false (default in tests / local dev) the dependency
    # yields an anonymous principal and behaviour matches the legacy open mode.
    auth = [Depends(require_principal)]
    app.include_router(legacy.router, dependencies=auth)
    app.include_router(coding.router, dependencies=auth)
    app.include_router(review.router, dependencies=auth)
    app.include_router(rcm.router, dependencies=auth)
    app.include_router(agents.router, dependencies=auth)

    # Eligibility sub-app has its own ELIGIBILITY_AGENT_API_KEY bearer guard and
    # its own CORS middleware (see app/eligibility/main.py). Mounted under
    # /eligibility-agent so its OpenAPI surface stays independent.
    app.mount("/eligibility-agent", eligibility_agent_app)
    return app


app = create_app()
