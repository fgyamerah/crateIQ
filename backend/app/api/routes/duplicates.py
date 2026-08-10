"""Safe duplicate review API: database-only decisions, never file actions.

Also exposes a read-only, plan-first duplicate resolution surface
(``/duplicates/resolution-plan``) that is separate from review state and has
no apply/execute endpoint. See
``docs/architecture/DUPLICATE_RESOLUTION_SPEC.md``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from ...schemas.duplicate_resolution_plan import DuplicateResolutionPlanResponse
from ...schemas.duplicate_review import DuplicateReviewDecisionUpdate, DuplicateReviewResponse
from ...services import duplicate_resolution_plan_service, duplicate_review_service

router = APIRouter(tags=["duplicates"])


@router.get("/duplicates/review", response_model=DuplicateReviewResponse)
async def get_duplicate_review() -> DuplicateReviewResponse:
    try:
        return DuplicateReviewResponse(**duplicate_review_service.get_review())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/duplicates/review/preview-refresh", response_model=DuplicateReviewResponse)
async def refresh_duplicate_review_preview() -> DuplicateReviewResponse:
    """Refresh the constrained rmlint preview and save only its safe snapshot."""
    try:
        return DuplicateReviewResponse(**duplicate_review_service.refresh_preview())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/duplicates/review/groups/{group_id}/items/{track_id}", response_model=DuplicateReviewResponse)
async def update_duplicate_review_decision(
    body: DuplicateReviewDecisionUpdate,
    group_id: str = Path(min_length=1, max_length=128),
    track_id: int = Path(ge=1),
) -> DuplicateReviewResponse:
    try:
        return DuplicateReviewResponse(**duplicate_review_service.update_decision(group_id, track_id, body.decision, body.note))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/duplicates/resolution-plan", response_model=DuplicateResolutionPlanResponse)
async def get_duplicate_resolution_plan() -> DuplicateResolutionPlanResponse:
    """Plan-first only: propose per-track resolution status from the latest reviewed
    snapshot and its human decisions. No apply/execute endpoint exists."""
    try:
        return DuplicateResolutionPlanResponse(**duplicate_resolution_plan_service.get_plan())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
