"""Local Settings API: diagnostics plus one safe library-scoped preference."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core.preflight import run_preflight
from ...services import settings_service
from ...services import library_setup_service
from ...schemas.metadata_sources import MetadataSourceClearResponse, MetadataSourceTestResponse, MetadataSourcesResponse, MetadataSourcesUpdateRequest

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
    analysis: "AnalysisPreferences"


class AnalysisPreferences(BaseModel):
    analyze_bpm: bool = False
    analyze_key: bool = False
    use_mik_when_present: bool = True
    preserve_existing_bpm_key_cues: bool = True
    missing_data_only: bool = True
    use_external_tools: bool = True


class AnalysisPreferencesUpdate(BaseModel):
    analyze_bpm: Optional[bool] = None
    analyze_key: Optional[bool] = None
    use_mik_when_present: Optional[bool] = None
    preserve_existing_bpm_key_cues: Optional[bool] = None
    missing_data_only: Optional[bool] = None
    use_external_tools: Optional[bool] = None


class WorkflowCapability(BaseModel):
    available: bool
    status: Literal["available", "missing", "unknown"]
    purpose: str
    message: str
    action_state: Literal["ready", "setup_required", "coming_soon"]
    required_tool: Optional[str] = None
    required_tools: Optional[list[str]] = None
    required_source: Optional[str] = None
    enabled: Optional[bool] = None
    locked: Optional[bool] = None


class SettingsCapabilities(BaseModel):
    core: dict[str, WorkflowCapability]
    analysis: dict[str, WorkflowCapability]
    policies: dict[str, bool]


class SettingsResponse(BaseModel):
    library: SettingsLibrary
    tools: list[SettingsTool]
    safety: SettingsSafety
    preferences: SettingsPreferences
    capabilities: SettingsCapabilities


class SettingsUpdateRequest(BaseModel):
    default_export_path_mode: Optional[Literal["filename", "relative", "absolute"]] = Field(default=None)
    analysis: Optional[AnalysisPreferencesUpdate] = None


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
        analysis_updates: dict[str, Any] | None = None
        if body.analysis is not None:
            analysis_updates = body.analysis.model_dump(exclude_none=True)
        return SettingsResponse(**settings_service.update_preferences(body.default_export_path_mode, analysis_updates))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/settings/capabilities", response_model=SettingsCapabilities)
async def get_settings_capabilities() -> SettingsCapabilities:
    """Read-only, action-level availability for optional local workflows."""
    return SettingsCapabilities(**settings_service.get_capabilities())


@router.get("/settings/metadata-sources", response_model=MetadataSourcesResponse)
async def get_metadata_sources() -> MetadataSourcesResponse:
    return MetadataSourcesResponse(**settings_service.get_metadata_sources())


@router.patch("/settings/metadata-sources", response_model=MetadataSourcesResponse)
async def patch_metadata_sources(body: MetadataSourcesUpdateRequest) -> MetadataSourcesResponse:
    try:
        return MetadataSourcesResponse(**settings_service.update_metadata_sources([item.model_dump(exclude_none=True) for item in body.sources]))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/settings/metadata-sources/{source_id}/test", response_model=MetadataSourceTestResponse)
async def test_metadata_source(source_id: str) -> MetadataSourceTestResponse:
    try:
        return MetadataSourceTestResponse(**settings_service.test_metadata_source(source_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/settings/metadata-sources/{source_id}/clear-credentials", response_model=MetadataSourceClearResponse)
async def clear_metadata_source_credentials(source_id: str) -> MetadataSourceClearResponse:
    try:
        return MetadataSourceClearResponse(**settings_service.clear_metadata_source_credentials(source_id))
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
