"""Human review endpoints for agent decisions."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.errors import sanitized_http_exception
from app.api.tenancy import PracticeContextDep
from app.config import get_settings
from app.services.decision_service import review_decision

_REVIEW_DECISION_ROLES = frozenset({"admin", "billing_lead"})


def _require_review_role(tenant: PracticeContextDep) -> None:
    if tenant.role not in _REVIEW_DECISION_ROLES:
        raise HTTPException(status_code=403, detail="role_forbidden")


router = APIRouter(tags=["review"])


class ReviewDecisionRequest(BaseModel):
    decision_id: str
    status: Literal["approved", "rejected"]
    override: dict[str, Any] | None = None


@router.post("/review-decision")
def review_agent_decision(
    body: ReviewDecisionRequest,
    tenant: PracticeContextDep,
) -> dict[str, str]:
    """
    Update decision status and optionally store manual override feedback.
    """
    _require_review_role(tenant)
    try:
        settings = get_settings()
        return review_decision(
            settings,
            body.decision_id,
            body.status,
            body.override,
            practice_id=tenant.practice_id,
        )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise sanitized_http_exception(
            503,
            public_message="Database is unavailable",
            log_message="review_decision Supabase failure",
            exc=e,
        ) from e
    except Exception as e:
        raise sanitized_http_exception(
            500,
            public_message="Failed to review decision",
            log_message=f"review_decision failed for decision_id={body.decision_id!r}",
            exc=e,
        ) from e
