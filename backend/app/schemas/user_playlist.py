"""Schemas for user-created, library-local playlists."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


MAX_PLAYLIST_TRACKS = 200


def _trim_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("name must not be blank")
    return value


class PlaylistCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _trim_name(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class PlaylistUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> Optional[str]:
        return _trim_name(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class PlaylistTrackIdsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_ids: list[StrictInt] = Field(min_length=1, max_length=MAX_PLAYLIST_TRACKS)
    confirm: bool = False

    @field_validator("track_ids")
    @classmethod
    def unique_positive_track_ids(cls, value: list[int]) -> list[int]:
        if any(track_id < 1 for track_id in value):
            raise ValueError("track_ids must contain positive IDs")
        if len(set(value)) != len(value):
            raise ValueError("track_ids must not contain duplicates")
        return value


class PlaylistReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_ids: list[StrictInt] = Field(max_length=5000)

    @field_validator("track_ids")
    @classmethod
    def unique_positive_track_ids(cls, value: list[int]) -> list[int]:
        if any(track_id < 1 for track_id in value):
            raise ValueError("track_ids must contain positive IDs")
        if len(set(value)) != len(value):
            raise ValueError("track_ids must not contain duplicates")
        return value


class PlaylistSummary(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: str
    updated_at: str
    track_count: int


class PlaylistTrack(BaseModel):
    track_id: int
    position: int
    added_at: str
    artist: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    genre: Optional[str] = None
    comment: Optional[str] = None
    label: Optional[str] = None
    bpm: Optional[float] = None
    key_musical: Optional[str] = None
    key_camelot: Optional[str] = None
    duration_sec: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    status: Optional[str] = None
    quality_tier: Optional[str] = None
    parse_confidence: Optional[str] = None
    storage_zone: Optional[str] = None
    rating: Optional[int] = None
    favorite: bool = False
    missing_from_library: bool = False


class PlaylistDetail(PlaylistSummary):
    tracks: list[PlaylistTrack]


class PlaylistAddPreview(BaseModel):
    playlist_id: int
    selected_count: int
    will_add_count: int
    already_present_count: int
    missing_count: int
    track_ids: list[int]
    message: str


class PlaylistMutationResponse(BaseModel):
    playlist: PlaylistDetail
    selected_count: int
    added_count: int
    already_present_count: int
    missing_count: int
    message: str


PlaylistSort = Literal["manual", "artist", "title", "rating", "favorite"]
