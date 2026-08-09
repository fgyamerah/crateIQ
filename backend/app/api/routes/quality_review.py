"""Safe ffprobe quality review API: local review state, never remediation."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from ...schemas.quality_review import QualityReviewDecisionUpdate, QualityReviewResponse
from ...services import quality_review_service

router = APIRouter(tags=["quality"])


@router.get("/quality/review", response_model=QualityReviewResponse)
async def get_quality_review() -> QualityReviewResponse:
    return QualityReviewResponse(**quality_review_service.get_review())


@router.post("/quality/review/preview-refresh", response_model=QualityReviewResponse)
async def refresh_quality_review_preview() -> QualityReviewResponse:
    try:
        return QualityReviewResponse(**quality_review_service.refresh_preview())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/quality/review/tracks/{track_id}", response_model=QualityReviewResponse)
async def update_quality_review_decision(
    body: QualityReviewDecisionUpdate,
    track_id: int = Path(ge=1),
) -> QualityReviewResponse:
    try:
        return QualityReviewResponse(**quality_review_service.update_decision(track_id, body.decision, body.note, body.finding_key))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
