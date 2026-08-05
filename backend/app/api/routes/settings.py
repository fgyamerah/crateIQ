"""Local Settings API: diagnostics plus one safe library-scoped preference."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core.preflight import run_preflight
from ...services import settings_service

router = APIRouter(tags=["settings"])


class SettingsLibrary(BaseModel):
    mode: Literal["demo", "configured"]
    library_root: str
    processed_db: str
    manual_crates_db: str
    exports_root: str
    restart_required: bool
    readiness_status: Literal["ready", "degraded", "not_ready"]


class SettingsTool(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    message: str
    source: str
    resolved: Optional[str] = None


class SettingsSafety(BaseModel):
    mixed_in_key_authoritative: bool
    missing_data_only_analysis: bool
    no_automatic_file_or_tag_modification: bool
    no_live_serato_writes: bool
    no_live_rekordbox_database_writes: bool
    preview_before_export_or_apply: bool


class SettingsPreferences(BaseModel):
    default_export_path_mode: Literal["filename", "relative", "absolute"]


class SettingsResponse(BaseModel):
    library: SettingsLibrary
    tools: list[SettingsTool]
    safety: SettingsSafety
    preferences: SettingsPreferences


class SettingsUpdateRequest(BaseModel):
    default_export_path_mode: Optional[Literal["filename", "relative", "absolute"]] = Field(default=None)


@router.get("/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    return SettingsResponse(**settings_service.get_settings())


@router.patch("/settings", response_model=SettingsResponse)
async def patch_settings(body: SettingsUpdateRequest) -> SettingsResponse:
    try:
        return SettingsResponse(**settings_service.update_preferences(body.default_export_path_mode))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/settings/runtime")
async def get_settings_runtime():
    """Refreshable read-only readiness report used by Settings diagnostics."""
    return run_preflight()
