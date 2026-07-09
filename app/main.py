import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI

from app.api.auth import require_principal
from app.api.routes import agents, coding, dashboard, health, legacy, rcm, review
from app.api.routes import auth as auth_routes
from app.config import get_settings
from app.eligibility.config import get_settings as get_eligibility_settings
from app.eligibility.main import app as eligibility_agent_app
from app.eligibility.main import run_from_opendental
from app.eligibility.retry_worker import start_retry_worker
from app.eligibility.voice.worker import start_voice_worker
from app.integrations.opendental.poller import start_appointment_poller
from app.logging_config import CorrelationIdMiddleware, configure_logging, init_sentry
from app.observability.metrics import router as metrics_router
from app.pipeline.worker import start_pipeline_worker
from app.realtime.bus import start_realtime_listener
from app.startup_guards import validate_production_readiness

logger = logging.getLogger(__name__)

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    # Starlette does NOT run the lifespan of mounted sub-apps, so the OpenDental
    # appointment poller (defined on the eligibility app) must be started here.
    eligibility_settings = get_eligibility_settings()
    poller_task = None
    app_settings = get_settings()
    if app_settings.pilot_shadow_mode:
        logger.warning(
            "PILOT_SHADOW_MODE enabled — OD write-back and claim submit are blocked; "
            "eligibility/coding runs are logged to platform.pilot_shadow_events"
        )
    if eligibility_settings.pilot_shadow_mode and eligibility_settings.opendental_writeback_enabled:
        logger.warning(
            "OPENDENTAL_WRITEBACK_ENABLED is set but shadow mode suppresses all writes"
        )
    if eligibility_settings.opendental_auto_poll_enabled:
        poller_task = start_appointment_poller(eligibility_settings)
        logger.warning(
            "OpenDental auto-poll enabled (interval=%ss, window_days=%s)",
            eligibility_settings.opendental_auto_poll_interval_seconds,
            eligibility_settings.opendental_auto_poll_date_window_days,
        )
    retry_task = None
    if eligibility_settings.eligibility_retry_worker_enabled:
        retry_task = start_retry_worker(eligibility_settings)
        logger.warning(
            "Eligibility retry worker enabled (interval=%ss, batch=%s)",
            eligibility_settings.eligibility_retry_worker_interval_seconds,
            eligibility_settings.eligibility_retry_batch_size,
        )
    voice_task = None
    from app.eligibility.voice.bland import bland_configured

    voice_worker_on = eligibility_settings.voice_verification_worker_enabled or (
        eligibility_settings.voice_verification_enabled and bland_configured(eligibility_settings)
    )
    if voice_worker_on:
        voice_task = start_voice_worker(eligibility_settings)
        logger.warning(
            "Voice verification worker enabled (provider=%s, interval=%ss, batch=%s)",
            eligibility_settings.voice_call_provider or "bland",
            eligibility_settings.voice_verification_worker_interval_seconds,
            eligibility_settings.voice_verification_batch_size,
        )
    pipeline_task = None
    if app_settings.pipeline_worker_enabled:
        pipeline_task = start_pipeline_worker(app_settings)
        logger.warning(
            "Pipeline worker enabled (interval=%ss, batch=%s, hitl_threshold=%s)",
            app_settings.pipeline_worker_interval_seconds,
            app_settings.pipeline_worker_batch_size,
            app_settings.confidence_hitl_threshold,
        )
    realtime_task = None
    from app.db.connection import get_neon_dsn

    if get_neon_dsn(app_settings):
        realtime_task = start_realtime_listener(app_settings)
        logger.info("Realtime LISTEN/NOTIFY listener enabled (channel=rcm_events)")
    background_tasks = [
        t
        for t in (poller_task, retry_task, voice_task, pipeline_task, realtime_task)
        if t is not None
    ]
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
            # CancelledError is a BaseException (not Exception) in 3.8+, so suppress it explicitly.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(log_level=settings.log_level)
    init_sentry(
        dsn=settings.sentry_dsn,
        app_name=settings.app_name,
        environment=settings.environment,
    )
    validate_production_readiness(settings)
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)

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
    app.include_router(dashboard.router, dependencies=auth)
    app.include_router(agents.router, dependencies=auth)
    # Prometheus scrape endpoint; authenticate the scraper with an INTERNAL_API_KEYS
    # entry via X-API-Key.
    app.include_router(metrics_router, dependencies=auth)
    app.include_router(auth_routes.router)

    # Eligibility sub-app has its own ELIGIBILITY_AGENT_API_KEY bearer guard and
    # its own CORS middleware (see app/eligibility/main.py). Mounted under
    # /eligibility-agent so its OpenAPI surface stays independent.
    app.mount("/eligibility-agent", eligibility_agent_app)
    return app


app = create_app()
