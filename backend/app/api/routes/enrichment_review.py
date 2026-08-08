from fastapi import APIRouter, HTTPException, Path
from ...schemas.enrichment_review import ApplyRequest, ApplyResult, OnlineLookupRequest, ReviewResponse, SuggestionUpdate
from ...services import enrichment_review_service as service
router=APIRouter(tags=['enrichment'])
@router.get('/enrichment/review',response_model=ReviewResponse)
async def get_review(): return ReviewResponse(**service.get_review())
@router.post('/enrichment/review/preview-refresh',response_model=ReviewResponse)
async def refresh():
    try:return ReviewResponse(**service.refresh_preview())
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
@router.patch('/enrichment/review/tracks/{track_id}/suggestions/{suggestion_id}',response_model=ReviewResponse)
async def update(body:SuggestionUpdate,track_id:int=Path(ge=1),suggestion_id:str=Path(min_length=1,max_length=128)):
    try:return ReviewResponse(**service.update_suggestion(track_id,suggestion_id,body.decision,body.note,body.selected_fields))
    except LookupError as exc: raise HTTPException(status_code=404,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
@router.post('/enrichment/review/apply',response_model=ApplyResult)
async def apply(body:ApplyRequest):
    try:return ApplyResult(**service.apply_selected([item.model_dump() for item in body.items],body.confirm))
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
@router.post('/enrichment/review/tracks/{track_id}/online-lookup',response_model=ReviewResponse)
async def online_lookup(body:OnlineLookupRequest,track_id:int=Path(ge=1)):
    """Explicit, single-track, bounded lookup against Beets or MusicBrainz. Never triggered automatically."""
    try:return ReviewResponse(**service.online_lookup(track_id,body.source))
    except LookupError as exc: raise HTTPException(status_code=404,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=422,detail=str(exc))
