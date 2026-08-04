"""Schemas for deterministic, preview-only Smart Crate suggestions."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

HarmonicMode = Literal["exact", "compatible", "energy_up", "energy_down"]


class SmartCrateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=1000)
    bpm_min: Optional[float] = Field(default=None, ge=0, le=300)
    bpm_max: Optional[float] = Field(default=None, ge=0, le=300)
    camelot_key: Optional[str] = Field(default=None, max_length=3)
    harmonic_mode: Optional[HarmonicMode] = None
    genres: list[str] = Field(default_factory=list, max_length=12)
    issue_free_only: bool = True
    limit: int = Field(default=25, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value and value.strip() else None

    @field_validator("camelot_key")
    @classmethod
    def valid_camelot_key(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        key = value.strip().upper()
        if not re.fullmatch(r"(1[0-2]|[1-9])[AB]", key):
            raise ValueError("camelot_key must be a key such as 8A or 12B")
        return key

    @field_validator("genres")
    @classmethod
    def clean_genres(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def validate_range_and_harmony(self):
        if self.bpm_min is not None and self.bpm_max is not None and self.bpm_min > self.bpm_max:
            raise ValueError("bpm_min must be less than or equal to bpm_max")
        if self.harmonic_mode and not self.camelot_key:
            raise ValueError("camelot_key is required when harmonic_mode is selected")
        return self


class SmartCrateTrack(BaseModel):
    track_id: int
    position: int
    artist: Optional[str] = None
    title: Optional[str] = None
    filename: Optional[str] = None
    genre: Optional[str] = None
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    duration_sec: Optional[float] = None
    reasons: list[str]


class SmartCratePreview(BaseModel):
    criteria: SmartCrateRequest
    generated_name: str
    explanation: str
    warnings: list[str] = []
    track_count: int
    tracks: list[SmartCrateTrack]


class SmartCratePreset(BaseModel):
    id: str
    label: str
    description: str
    criteria: SmartCrateRequest
