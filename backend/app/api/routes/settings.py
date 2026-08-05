"""Local Settings API: diagnostics plus one safe library-scoped preference."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core.preflight import run_preflight
from ...services import settings_service
from ...services import library_setup_service

router = APIRouter(tags=["settings"])


class SettingsLibrary(BaseModel):
    mode: Literal["demo", "configured"]
    library_root: str
    processed_db: str
    manual_crates_db: str
    exports_root: str
    library_initialized: bool
    pending_library_root: Optional[str] = None
    pending_library_initialized: bool = False
    restart_required: bool
    restart_command: str
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


class LibraryRootRequest(BaseModel):
    library_root: str = Field(min_length=1, max_length=4096)


class LibraryRootValidationResponse(BaseModel):
    library_root: str
    valid: bool
    message: str


class LibrarySetupRequest(BaseModel):
    library_root: Optional[str] = Field(default=None, max_length=4096)


class LibraryImportRequest(LibrarySetupRequest):
    confirm: bool = False


@router.get("/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    return SettingsResponse(**settings_service.get_settings())


@router.patch("/settings", response_model=SettingsResponse)
async def patch_settings(body: SettingsUpdateRequest) -> SettingsResponse:
    try:
        return SettingsResponse(**settings_service.update_preferences(body.default_export_path_mode))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/settings/library/validate", response_model=LibraryRootValidationResponse)
async def validate_library_root(body: LibraryRootRequest) -> LibraryRootValidationResponse:
    try:
        return LibraryRootValidationResponse(**settings_service.validate_library_root(body.library_root))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.patch("/settings/library", response_model=SettingsResponse)
async def patch_library_root(body: LibraryRootRequest) -> SettingsResponse:
    try:
        return SettingsResponse(**settings_service.update_library_root(body.library_root))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/settings/library/initialize")
async def initialize_library(body: LibrarySetupRequest):
    try:
        return library_setup_service.initialize_library(body.library_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/library/scan-preview")
async def scan_library_preview(body: LibrarySetupRequest):
    try:
        return library_setup_service.scan_preview(body.library_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/library/import")
async def import_library(body: LibraryImportRequest):
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Import requires confirm=true after reviewing a scan preview.")
    try:
        return library_setup_service.import_previewed_library(body.library_root)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/settings/runtime")
async def get_settings_runtime():
    """Refreshable read-only readiness report used by Settings diagnostics."""
    return run_preflight()
