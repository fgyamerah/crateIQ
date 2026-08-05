"""Safe selected-field Beets enrichment review: CrateIQ DB only."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from ...schemas.beets_review import BeetsApplyRequest, BeetsApplyResult, BeetsReviewResponse, BeetsReviewTrackUpdate
from ...services import beets_review_service

router = APIRouter(tags=["enrichment"])


@router.get("/enrichment/beets/review", response_model=BeetsReviewResponse)
async def get_beets_review() -> BeetsReviewResponse:
    return BeetsReviewResponse(**beets_review_service.get_review())


@router.post("/enrichment/beets/preview-refresh", response_model=BeetsReviewResponse)
async def refresh_beets_review() -> BeetsReviewResponse:
    try:
        return BeetsReviewResponse(**beets_review_service.refresh_preview())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/enrichment/beets/tracks/{track_id}", response_model=BeetsReviewResponse)
async def update_beets_review(
    body: BeetsReviewTrackUpdate,
    track_id: int = Path(ge=1),
) -> BeetsReviewResponse:
    try:
        return BeetsReviewResponse(**beets_review_service.update_review(track_id, body.decision, body.note, body.selected_fields))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/enrichment/beets/apply", response_model=BeetsApplyResult)
async def apply_beets_review(body: BeetsApplyRequest) -> BeetsApplyResult:
    try:
        return BeetsApplyResult(**beets_review_service.apply_selected([item.model_dump() for item in body.items], confirm=body.confirm))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
