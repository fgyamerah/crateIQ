"""Installation-scoped, read-only launcher bootstrap endpoints."""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...core.library_root import selected_library_root
from ...core.preflight import redact_path
from ...services import library_registry_service

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
    path: str
    display_name: str
    last_opened_at: str
    availability: bool
    classification: str


class RegistryResponse(BaseModel):
    recent_libraries: list[RecentLibraryResponse]
    registry_status: Literal["ready", "malformed"]
    message: str | None = None


class CurrentLibraryResponse(BaseModel):
    rootless: bool
    library_root: str | None = None


def _require_local_operator(request: Request) -> None:
    """Allow host-path inspection only for an explicitly local server startup."""
    # Vite proxies /api traffic over loopback, so request.client alone cannot
    # distinguish a local browser from a LAN browser using the supported
    # frontend. The service helper sets this process-scoped value from its
    # selected bind mode; it is deliberately not derived from request headers.
    if os.environ.get("CRATEIQ_LAUNCH_ACCESS_MODE") != "local":
        raise HTTPException(
            status_code=403,
            detail="Candidate library path inspection is disabled when CrateIQ is not started in local-only mode.",
        )
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(
            status_code=403,
            detail="Candidate library path inspection is available only to the local CrateIQ operator.",
        )


@router.get("/launcher/library-registry", response_model=RegistryResponse)
async def library_registry() -> RegistryResponse:
    return RegistryResponse(**library_registry_service.get_launcher_registry())


@router.post("/launcher/library-classification", response_model=LibraryClassificationResponse)
async def classify_library(body: LibraryCandidateRequest, request: Request) -> LibraryClassificationResponse:
    _require_local_operator(request)
    try:
        return LibraryClassificationResponse(**library_registry_service.classify_library_candidate(body.library_root))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/launcher/current-library", response_model=CurrentLibraryResponse)
async def current_library() -> CurrentLibraryResponse:
    try:
        root = selected_library_root()
    except RuntimeError:
        return CurrentLibraryResponse(rootless=True)
    return CurrentLibraryResponse(rootless=False, library_root=redact_path(root))
