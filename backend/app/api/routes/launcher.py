"""Installation-scoped launcher endpoints with registry-bound activation."""
from __future__ import annotations

import asyncio
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ...core.library_root import selected_library_root
from ...core.preflight import redact_path
from ...services import library_launcher_admin_service, library_registry_service, supervisor_ipc

router = APIRouter(tags=["launcher"])


class LibraryCandidateRequest(BaseModel):
    library_root: str = Field(min_length=1, max_length=4096)


class LibraryClassificationResponse(BaseModel):
    canonical_path: str
    classification: Literal[
        "managed_workspace", "legacy_direct_library", "empty_folder",
        "external_music_folder", "malformed_or_unsafe", "missing",
    ]
    available: bool
    message: str


class RecentLibraryResponse(BaseModel):
    library_id: str
    path: str
    display_name: str
    last_opened_at: str
    availability: bool
    classification: str
    active: bool


class RegistryResponse(BaseModel):
    recent_libraries: list[RecentLibraryResponse]
    registry_status: Literal["ready", "malformed"]
    message: str | None = None


class BrowseRootResponse(BaseModel):
    display_name: str
    path: str


class BrowseEntryResponse(BaseModel):
    display_name: str
    path: str
    entry_type: Literal["directory", "symlink"]
    selectable: bool
    classification: str
    reason: str


class BrowseResponse(BaseModel):
    current_path: str
    parent_path: str | None
    roots: list[BrowseRootResponse]
    entries: list[BrowseEntryResponse]
    offset: int
    limit: int
    truncated: bool


class RegisterLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class CreateLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_directory: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=120)


class RegisteredLibraryResponse(BaseModel):
    library_id: str
    library_root: str
    library_key: str
    display_name: str
    classification: Literal["managed_workspace", "legacy_direct_library"]
    availability: bool
    last_opened_at: str | None


class ActivateLibraryRequest(BaseModel):
    """Only an opaque registry ID is accepted from LAN callers."""
    model_config = ConfigDict(extra="forbid")
    library_id: str = Field(min_length=9, max_length=128, pattern=r"^library_[0-9a-f]{64}$")


class ActivationStartResponse(BaseModel):
    activation_id: str
    activation_status: Literal["activating"]


class ActivationBlockerResponse(BaseModel):
    category: Literal["active_work", "foreign_active_work", "legacy_ambiguous_work"]
    count: int = Field(ge=0)
    message: str


class ActivationStatusResponse(BaseModel):
    activation_status: Literal["idle", "activating", "succeeded", "blocked", "failed", "fail_closed"]
    activation_id: str | None = None
    library_id: str | None = None
    result: Literal["activated", "already_active"] | None = None
    error_code: str | None = None
    message: str | None = None
    blocker: ActivationBlockerResponse | None = None
    registry_recency_updated: bool | None = None
    warning_code: Literal["registry_recency_update_failed"] | None = None


class CurrentLibraryResponse(BaseModel):
    rootless: bool
    library_root: str | None = None
    library_id: str | None = None
    display_name: str | None = None
    launcher_status: Literal["ready", "supervisor_unavailable"]
    activation_status: Literal["idle", "activating", "succeeded", "blocked", "failed", "fail_closed"]


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _require_local_operator(request: Request) -> None:
    """Allow host-path administration only for an explicitly local startup."""
    if os.environ.get("CRATEIQ_LAUNCH_ACCESS_MODE") != "local":
        raise HTTPException(status_code=403, detail="Launcher filesystem administration is disabled when CrateIQ is not started in local-only mode.")
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="Launcher filesystem administration is available only to the local CrateIQ operator.")


def _active_registry_metadata() -> tuple[bool, str | None, str | None, str | None]:
    try:
        root = selected_library_root()
    except RuntimeError:
        return True, None, None, None
    try:
        registered = library_registry_service.registered_library_for_root(root)
    except (library_registry_service.MalformedRegistryError, ValueError):
        registered = None
    return False, redact_path(root), (registered or {}).get("library_id"), (registered or {}).get("display_name")


def _activation_status_from_supervisor() -> tuple[dict[str, Any], bool]:
    try:
        payload = supervisor_ipc.request("status")
    except supervisor_ipc.SupervisorIPCError:
        return {"status": "idle"}, False
    activation = payload.get("activation")
    valid = {"idle", "activating", "succeeded", "blocked", "failed", "fail_closed"}
    if not isinstance(activation, dict) or activation.get("status") not in valid:
        return {"status": "failed", "error_code": "invalid_supervisor_status", "message": "Supervisor status is unavailable."}, False
    return activation, True


def _activation_response(payload: dict[str, Any]) -> ActivationStatusResponse:
    return ActivationStatusResponse(
        activation_status=payload["status"], activation_id=payload.get("activation_id"),
        library_id=payload.get("library_id"), result=payload.get("result"),
        error_code=payload.get("error_code"), message=payload.get("message"),
        blocker=payload.get("blocker"), registry_recency_updated=payload.get("registry_recency_updated"),
        warning_code=payload.get("warning_code"),
    )


@router.get("/launcher/library-registry", response_model=RegistryResponse)
async def library_registry() -> RegistryResponse:
    _, _, active_library_id, _ = _active_registry_metadata()
    return RegistryResponse(**library_registry_service.get_launcher_registry(active_library_id=active_library_id))


@router.post("/launcher/library-classification", response_model=LibraryClassificationResponse)
async def classify_library(body: LibraryCandidateRequest, request: Request) -> LibraryClassificationResponse:
    _require_local_operator(request)
    try:
        return LibraryClassificationResponse(**library_registry_service.classify_library_candidate(body.library_root))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/launcher/browse", response_model=BrowseResponse)
async def browse_libraries(
    request: Request,
    path: str | None = Query(default=None, min_length=1, max_length=4096),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=library_launcher_admin_service.BROWSE_DEFAULT_LIMIT,
        ge=1,
        le=library_launcher_admin_service.BROWSE_MAX_LIMIT,
    ),
) -> BrowseResponse:
    _require_local_operator(request)
    try:
        result = await asyncio.to_thread(
            library_launcher_admin_service.browse_directories,
            path,
            offset=offset,
            limit=limit,
        )
        return BrowseResponse(**result)
    except library_launcher_admin_service.BrowseLocationError as exc:
        raise HTTPException(status_code=422, detail=_detail("invalid_browse_location", str(exc))) from exc


@router.post("/launcher/register-library", response_model=RegisteredLibraryResponse)
async def register_library(body: RegisterLibraryRequest, request: Request) -> RegisteredLibraryResponse:
    _require_local_operator(request)
    try:
        result = await asyncio.to_thread(
            library_launcher_admin_service.register_existing_library,
            body.path,
        )
        return RegisteredLibraryResponse(**result)
    except library_launcher_admin_service.BrowseLocationError as exc:
        raise HTTPException(status_code=422, detail=_detail("unsafe_path", str(exc))) from exc
    except library_registry_service.UnsafeRegistryLibraryError as exc:
        raise HTTPException(
            status_code=422,
            detail=_detail("not_a_valid_library", "The selected directory is not a valid activatable CrateIQ library."),
        ) from exc
    except library_registry_service.MalformedRegistryError as exc:
        raise HTTPException(status_code=503, detail=_detail("registry_unavailable", "The library registry is unavailable.")) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=_detail("registry_write_failed", "The library registry could not be updated.")) from exc


@router.post("/launcher/create-library", response_model=RegisteredLibraryResponse, status_code=status.HTTP_201_CREATED)
async def create_library(body: CreateLibraryRequest, request: Request) -> RegisteredLibraryResponse:
    _require_local_operator(request)
    try:
        result = await asyncio.to_thread(
            library_launcher_admin_service.create_managed_library,
            body.parent_directory,
            body.name,
        )
        return RegisteredLibraryResponse(**result)
    except library_launcher_admin_service.LibraryNameError as exc:
        raise HTTPException(status_code=422, detail=_detail("invalid_library_name", str(exc))) from exc
    except library_launcher_admin_service.LibraryCollisionError as exc:
        raise HTTPException(status_code=409, detail=_detail("library_name_collision", str(exc))) from exc
    except library_launcher_admin_service.BrowseLocationError as exc:
        raise HTTPException(status_code=422, detail=_detail("unsafe_parent", str(exc))) from exc
    except library_launcher_admin_service.LibraryInitializationError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                **_detail("initialization_failed", str(exc)),
                "partial_directory_left": exc.partial_left,
            },
        ) from exc
    except library_launcher_admin_service.RegistryAfterCreateError as exc:
        raise HTTPException(
            status_code=503,
            detail=_detail("registry_write_failed_after_create", str(exc)),
        ) from exc


@router.post("/launcher/activate-library", response_model=ActivationStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def activate_library(body: ActivateLibraryRequest) -> ActivationStartResponse:
    """Start a registry-ID-only supervisor handoff.

    Success replaces this backend process, so final completion is read from
    the surviving supervisor after the client reconnects to the stable port.
    """
    try:
        target = library_registry_service.resolve_registered_library(body.library_id)
    except library_registry_service.UnknownRegistryLibraryError as exc:
        raise HTTPException(status_code=404, detail=_detail("unknown_library", "The selected library is not in this installation registry.")) from exc
    except library_registry_service.UnsafeRegistryLibraryError as exc:
        raise HTTPException(status_code=422, detail=_detail("unsafe_library", "The selected library is unavailable or no longer safe to open.")) from exc
    except library_registry_service.MalformedRegistryError as exc:
        raise HTTPException(status_code=503, detail=_detail("registry_unavailable", "The library registry is unavailable.")) from exc
    try:
        result = supervisor_ipc.request("activate_registered_library", {
            "library_id": target["library_id"], "library_root": target["library_root"],
            "library_key": target["library_key"], "classification": target["classification"],
        })
    except supervisor_ipc.SupervisorIPCError as exc:
        code = str(exc)
        if code == "activation_in_progress":
            raise HTTPException(status_code=409, detail=_detail(code, "Another library activation is already in progress.")) from exc
        if code == "supervisor_fail_closed":
            raise HTTPException(status_code=409, detail=_detail(code, "Library activation requires supervisor restart or operator intervention.")) from exc
        raise HTTPException(status_code=503, detail=_detail("supervisor_unavailable", "The local supervisor is unavailable.")) from exc
    if result.get("result") != "activation_started" or not isinstance(result.get("activation_id"), str):
        raise HTTPException(status_code=503, detail=_detail("supervisor_invalid_response", "The local supervisor returned an invalid activation response."))
    return ActivationStartResponse(activation_id=result["activation_id"], activation_status="activating")


@router.get("/launcher/activation-status", response_model=ActivationStatusResponse)
async def activation_status() -> ActivationStatusResponse:
    # Keep the promoted backend's event loop available for the supervisor's
    # private identity probe even if local IPC is slow or interrupted.
    payload, available = await asyncio.to_thread(_activation_status_from_supervisor)
    if not available:
        raise HTTPException(status_code=503, detail=_detail("supervisor_unavailable", "The local supervisor is unavailable."))
    return _activation_response(payload)


@router.get("/launcher/current-library", response_model=CurrentLibraryResponse)
async def current_library() -> CurrentLibraryResponse:
    rootless, library_root, library_id, display_name = _active_registry_metadata()
    activation, available = await asyncio.to_thread(_activation_status_from_supervisor)
    return CurrentLibraryResponse(
        rootless=rootless, library_root=library_root, library_id=library_id, display_name=display_name,
        launcher_status="ready" if available else "supervisor_unavailable",
        activation_status=activation["status"] if available else "idle",
    )
