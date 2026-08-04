"""Schemas for local, manual DJ crates. These never write music files or tags."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class CrateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class CrateUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class CrateTrackAddRequest(BaseModel):
    track_id: int = Field(ge=1)
    note: Optional[str] = Field(default=None, max_length=500)


class CrateTrackReorderRequest(BaseModel):
    track_ids: list[int] = Field(min_length=0, max_length=5000)

    @field_validator("track_ids")
    @classmethod
    def unique_positive_track_ids(cls, value: list[int]) -> list[int]:
        if any(track_id < 1 for track_id in value):
            raise ValueError("track_ids must contain positive IDs")
        if len(set(value)) != len(value):
            raise ValueError("track_ids must not contain duplicates")
        return value


class CrateSummary(BaseModel):
    id: int
    name: str
    notes: Optional[str] = None
    created_at: str
    updated_at: str
    track_count: int


class CrateTrack(BaseModel):
    track_id: int
    position: int
    added_at: str
    note: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None
    genre: Optional[str] = None
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    duration_sec: Optional[float] = None
    missing_from_library: bool = False


class CrateDetail(CrateSummary):
    tracks: list[CrateTrack]
