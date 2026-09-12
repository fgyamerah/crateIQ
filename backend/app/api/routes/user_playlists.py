"""API for manually curated playlists owned by the active library."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Path, Query, Response

from ...schemas.user_playlist import (
    PlaylistAddPreview,
    PlaylistCreateRequest,
    PlaylistDetail,
    PlaylistMutationResponse,
    PlaylistReorderRequest,
    PlaylistSort,
    PlaylistSummary,
    PlaylistTrackIdsRequest,
    PlaylistUpdateRequest,
)
from ...services import user_playlist_service

router = APIRouter(tags=["user-playlists"])


@router.get("/user-playlists", response_model=list[PlaylistSummary])
async def list_user_playlists() -> list[PlaylistSummary]:
    return user_playlist_service.list_playlists()


@router.post("/user-playlists", response_model=PlaylistSummary, status_code=201)
async def create_user_playlist(body: PlaylistCreateRequest) -> PlaylistSummary:
    try:
        return user_playlist_service.create_playlist(body.name, body.description)
    except sqlite3.IntegrityError as exc:
        if "user_playlists.name" in str(exc).lower() or "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="A playlist with that name already exists in this library.") from exc
        raise


@router.get("/user-playlists/{playlist_id}", response_model=PlaylistDetail)
async def get_user_playlist(
    playlist_id: int = Path(ge=1),
    search: str | None = Query(default=None, max_length=120),
    sort: PlaylistSort = Query(default="manual"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    favorite_only: bool = Query(default=False),
) -> PlaylistDetail:
    playlist = user_playlist_service.get_playlist(
        playlist_id, search=search, sort=sort, order=order, favorite_only=favorite_only,
    )
    if playlist is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


@router.patch("/user-playlists/{playlist_id}", response_model=PlaylistSummary)
async def update_user_playlist(body: PlaylistUpdateRequest, playlist_id: int = Path(ge=1)) -> PlaylistSummary:
    if "name" not in body.model_fields_set and "description" not in body.model_fields_set:
        raise HTTPException(status_code=422, detail="At least one playlist field is required")
    try:
        playlist = user_playlist_service.update_playlist(
            playlist_id,
            name=body.name,
            description=body.description,
            update_description="description" in body.model_fields_set,
        )
    except sqlite3.IntegrityError as exc:
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="A playlist with that name already exists in this library.") from exc
        raise
    if playlist is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


@router.delete("/user-playlists/{playlist_id}", status_code=204)
async def delete_user_playlist(playlist_id: int = Path(ge=1)) -> Response:
    if not user_playlist_service.delete_playlist(playlist_id):
        raise HTTPException(status_code=404, detail="Playlist not found")
    return Response(status_code=204)


@router.post("/user-playlists/{playlist_id}/tracks/preview", response_model=PlaylistAddPreview)
async def preview_user_playlist_tracks(body: PlaylistTrackIdsRequest, playlist_id: int = Path(ge=1)) -> PlaylistAddPreview:
    preview = user_playlist_service.preview_add(playlist_id, body.track_ids)
    if preview is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return preview


@router.post("/user-playlists/{playlist_id}/tracks", response_model=PlaylistMutationResponse, status_code=201)
async def add_user_playlist_tracks(body: PlaylistTrackIdsRequest, playlist_id: int = Path(ge=1)) -> PlaylistMutationResponse:
    if len(body.track_ids) > 1 and not body.confirm:
        raise HTTPException(status_code=409, detail="Preview this bulk add before applying it.")
    preview = user_playlist_service.preview_add(playlist_id, body.track_ids)
    if preview is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    if preview.missing_count:
        raise HTTPException(status_code=404, detail="One or more selected tracks are not in the active library.")
    result = user_playlist_service.add_tracks(playlist_id, body.track_ids)
    if result == "playlist_missing":
        raise HTTPException(status_code=404, detail="Playlist not found")
    return result  # type: ignore[return-value]


@router.delete("/user-playlists/{playlist_id}/tracks/{track_id}", response_model=PlaylistDetail)
async def remove_user_playlist_track(playlist_id: int = Path(ge=1), track_id: int = Path(ge=1)) -> PlaylistDetail:
    result = user_playlist_service.remove_track(playlist_id, track_id)
    if result == "playlist_missing":
        raise HTTPException(status_code=404, detail="Playlist not found")
    if result == "track_missing":
        raise HTTPException(status_code=404, detail="Track is not in this playlist")
    return user_playlist_service.get_playlist(playlist_id)  # type: ignore[return-value]


@router.post("/user-playlists/{playlist_id}/tracks/remove", response_model=PlaylistDetail)
async def remove_user_playlist_tracks(body: PlaylistTrackIdsRequest, playlist_id: int = Path(ge=1)) -> PlaylistDetail:
    result = user_playlist_service.remove_tracks(playlist_id, body.track_ids)
    if result == "playlist_missing":
        raise HTTPException(status_code=404, detail="Playlist not found")
    return user_playlist_service.get_playlist(playlist_id)  # type: ignore[return-value]


@router.patch("/user-playlists/{playlist_id}/tracks/reorder", response_model=PlaylistDetail)
async def reorder_user_playlist_tracks(body: PlaylistReorderRequest, playlist_id: int = Path(ge=1)) -> PlaylistDetail:
    result = user_playlist_service.reorder_tracks(playlist_id, body.track_ids)
    if result == "playlist_missing":
        raise HTTPException(status_code=404, detail="Playlist not found")
    if result == "invalid_order":
        raise HTTPException(status_code=422, detail="track_ids must contain every current playlist track exactly once")
    return user_playlist_service.get_playlist(playlist_id)  # type: ignore[return-value]
