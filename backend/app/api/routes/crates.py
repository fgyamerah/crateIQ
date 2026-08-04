"""Manual crate API. Crates are library-local state, not exported playlists."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Response
from ...schemas.crate import CrateCreateRequest, CrateDetail, CrateSummary, CrateTrackAddRequest, CrateTrackReorderRequest, CrateUpdateRequest
from ...services import crate_service

router = APIRouter(tags=["crates"])


@router.get("/crates", response_model=list[CrateSummary])
async def list_crates() -> list[CrateSummary]:
    return crate_service.list_crates()


@router.post("/crates", response_model=CrateSummary, status_code=201)
async def create_crate(body: CrateCreateRequest) -> CrateSummary:
    return crate_service.create_crate(body.name, body.notes)


@router.get("/crates/{crate_id}", response_model=CrateDetail)
async def get_crate(crate_id: int = Path(ge=1)) -> CrateDetail:
    crate = crate_service.get_crate(crate_id)
    if crate is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return crate


@router.patch("/crates/{crate_id}", response_model=CrateSummary)
async def update_crate(body: CrateUpdateRequest, crate_id: int = Path(ge=1)) -> CrateSummary:
    crate = crate_service.update_crate(crate_id, name=body.name, notes=body.notes, update_notes="notes" in body.model_fields_set)
    if crate is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return crate


@router.delete("/crates/{crate_id}", status_code=204)
async def delete_crate(crate_id: int = Path(ge=1)) -> Response:
    if not crate_service.delete_crate(crate_id):
        raise HTTPException(status_code=404, detail="Crate not found")
    return Response(status_code=204)


@router.post("/crates/{crate_id}/tracks", response_model=CrateDetail, status_code=201)
async def add_track(body: CrateTrackAddRequest, crate_id: int = Path(ge=1)) -> CrateDetail:
    result = crate_service.add_track(crate_id, body.track_id, body.note)
    if result == "crate_missing": raise HTTPException(status_code=404, detail="Crate not found")
    if result == "track_missing": raise HTTPException(status_code=404, detail="Track not found in the selected library")
    if result == "duplicate": raise HTTPException(status_code=409, detail="Track is already in this crate")
    return crate_service.get_crate(crate_id)  # type: ignore[return-value]


@router.delete("/crates/{crate_id}/tracks/{track_id}", response_model=CrateDetail)
async def remove_track(crate_id: int = Path(ge=1), track_id: int = Path(ge=1)) -> CrateDetail:
    result = crate_service.remove_track(crate_id, track_id)
    if result == "crate_missing": raise HTTPException(status_code=404, detail="Crate not found")
    if result == "track_missing": raise HTTPException(status_code=404, detail="Track is not in this crate")
    return crate_service.get_crate(crate_id)  # type: ignore[return-value]


@router.patch("/crates/{crate_id}/tracks/reorder", response_model=CrateDetail)
async def reorder_tracks(body: CrateTrackReorderRequest, crate_id: int = Path(ge=1)) -> CrateDetail:
    result = crate_service.reorder_tracks(crate_id, body.track_ids)
    if result == "crate_missing": raise HTTPException(status_code=404, detail="Crate not found")
    if result == "invalid_order": raise HTTPException(status_code=422, detail="track_ids must contain every current crate track exactly once")
    return crate_service.get_crate(crate_id)  # type: ignore[return-value]
