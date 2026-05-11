from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/")
def root(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {"message": "Agent system is running", "app": settings.app_name}


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}
