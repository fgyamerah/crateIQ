"""
Health and version routes.

  GET /api/health   — public liveness/readiness without host paths
  GET /api/stats    — read-only counts and latest path-audit summary
  GET /api/version  — backend and toolkit version strings
"""
from fastapi import APIRouter, Header, HTTPException, Request
from typing import Any, Optional
from pydantic import BaseModel

from ...core.config import BACKEND_VERSION, PIPELINE_PY, TOOLKIT_ROOT
from ...services import read_only as read_only_service
from ...services.backend_instance_identity import verify_supervisor_token
from ...services.operation_admission_gate import operation_admission_gate

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
        db_exists = read_only_service.db_exists()
    except RuntimeError:
        db_exists = False
    return HealthResponse(ok=True, db_exists=db_exists)


@router.get("/internal/supervisor-identity")
async def supervisor_identity(
    request: Request,
    supervisor_token: str | None = Header(default=None, alias="X-CrateIQ-Supervisor-Token"),
) -> dict[str, object]:
    """Loopback-only verification channel; never trust forwarded headers."""
    client = request.client
    if client is None or client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=404, detail="Not found")
    identity = verify_supervisor_token(supervisor_token)
    if identity is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"identity": identity}


@router.post("/internal/supervisor-admission")
async def supervisor_admission(
    request: Request,
    action: str,
    supervisor_token: str | None = Header(default=None, alias="X-CrateIQ-Supervisor-Token"),
) -> dict[str, object]:
    """Private loopback-only runtime-gate control for the owning supervisor.

    This endpoint neither accepts a library path nor starts a handoff.  It is
    the process-local gate bridge required because the supervisor owns child
    processes while each backend owns its own admission gate.
    """
    client = request.client
    if client is None or client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=404, detail="Not found")
    if verify_supervisor_token(supervisor_token) is None:
        raise HTTPException(status_code=404, detail="Not found")
    if action == "begin_draining":
        await operation_admission_gate.begin_draining_async()
    elif action == "abort_draining":
        operation_admission_gate.abort_draining()
    elif action != "status":
        raise HTTPException(status_code=404, detail="Not found")
    return {"admission": operation_admission_gate.status()}


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
