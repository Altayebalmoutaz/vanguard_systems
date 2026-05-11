from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI

from app.api.auth import require_principal
from app.api.routes import agents, coding, health, legacy, rcm, review
from app.config import get_settings
from app.eligibility.main import app as eligibility_agent_app

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    yield


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
