"""
Health and version routes.

  GET /api/health   — liveness + selected root details
  GET /api/stats    — read-only counts and latest path-audit summary
  GET /api/version  — backend and toolkit version strings
"""
from fastapi import APIRouter
from typing import Any, Optional
from pydantic import BaseModel

from ...core.config import BACKEND_VERSION, PIPELINE_PY, TOOLKIT_ROOT
from ...services import read_only as read_only_service

# Import toolkit version without running the full pipeline module
import importlib.util, sys

router = APIRouter(tags=["health"])


def _toolkit_version() -> str:
    """Read PIPELINE_VERSION from config.py without importing pipeline.py."""
    try:
        spec = importlib.util.spec_from_file_location(
            "_tk_config", str(TOOLKIT_ROOT / "config.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "PIPELINE_VERSION", "unknown")
    except Exception:
        return "unknown"


class HealthResponse(BaseModel):
    ok: bool
    library_root: Optional[str] = None
    db_path: Optional[str] = None
    db_exists: bool = False


class StatsResponse(BaseModel):
    tracks_count: int
    disk_audio_files: int
    missing_files: int
    untracked_files: int
    stale_processed_state_total: int
    canonical_source: str
    last_audit_report: Optional[dict[str, Any]] = None


class VersionResponse(BaseModel):
    backend_version: str
    toolkit_version: str
    pipeline_py: str


@router.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
async def health() -> HealthResponse:
    try:
        root = read_only_service.get_library_root()
        db_path = read_only_service.get_db_path()
    except RuntimeError:
        return HealthResponse(ok=True)
    return HealthResponse(
        ok=True,
        library_root=str(root),
        db_path=str(db_path),
        db_exists=read_only_service.db_exists(),
    )


@router.get("/stats", response_model=StatsResponse)
async def stats() -> StatsResponse:
    payload = read_only_service.build_stats_payload()
    return StatsResponse(**payload)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(
        backend_version=BACKEND_VERSION,
        toolkit_version=_toolkit_version(),
        pipeline_py=str(PIPELINE_PY),
    )
