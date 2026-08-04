"""Preview and explicitly save local Smart Crate suggestions."""
from fastapi import APIRouter, HTTPException

from ...schemas.crate import CrateDetail
from ...schemas.smart_crate import SmartCratePreset, SmartCratePreview, SmartCrateRequest
from ...services import crate_service, smart_crate_service

router = APIRouter(tags=["smart-crates"])

@router.get("/smart-crates/presets", response_model=list[SmartCratePreset])
async def presets() -> list[SmartCratePreset]:
    return smart_crate_service.list_presets()

@router.post("/smart-crates/preview", response_model=SmartCratePreview)
async def preview(body: SmartCrateRequest) -> SmartCratePreview:
    return smart_crate_service.preview(body)

@router.post("/smart-crates/save", response_model=CrateDetail, status_code=201)
async def save(body: SmartCrateRequest) -> CrateDetail:
    suggestion = smart_crate_service.preview(body)
    if not suggestion.tracks:
        raise HTTPException(status_code=422, detail="Preview has no tracks to save")
    crate = crate_service.create_crate(suggestion.generated_name, body.notes)
    for track in suggestion.tracks:
        crate_service.add_track(crate.id, track.track_id, None)
    return crate_service.get_crate(crate.id)  # type: ignore[return-value]
