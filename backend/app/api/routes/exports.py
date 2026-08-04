"""
Export routes.

  POST  /api/exports/validate       — validate tracks for export (synchronous, fast)
  POST  /api/exports/run            — dispatch a rekordbox-export job
  GET   /api/exports                — list past rekordbox-export jobs
  GET   /api/exports/{export_id}    — get a single export job + log link
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from ...schemas.export import (
    CrateExportPreview,
    CrateExportRequest,
    CrateExportResult,
    ExportRunRequest,
    ExportRunResponse,
    SeratoExportPreview,
    SeratoExportRequest,
    SeratoExportResult,
    ValidateResponse,
)
from ...schemas.job import JobResponse
from ...services import job_service, toolkit_runner
from ...services.export_validation import run_validation
from ...services import crate_export_service, crate_service, serato_export_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["exports"])

@router.get("/exports/serato/preview/{crate_id}", response_model=SeratoExportPreview)
async def preview_serato_export(
    crate_id: int, path_mode: str = "filename"
) -> SeratoExportPreview:
    try:
        result = serato_export_service.preview(
            crate_id, SeratoExportRequest(path_mode=path_mode)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return result


@router.post("/exports/serato/{crate_id}", response_model=SeratoExportResult)
async def export_serato(crate_id: int, body: SeratoExportRequest) -> SeratoExportResult:
    try:
        result = serato_export_service.write(crate_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return result

@router.get("/exports/crates")
async def list_exportable_crates():
    """List local Manual Crates that can be exported safely."""
    return crate_service.list_crates()

@router.get("/exports/crates/{crate_id}/preview", response_model=CrateExportPreview)
async def preview_crate_export(crate_id: int, format: str = "m3u8", path_mode: str = "filename", include_metadata: bool = True, line_endings: str = "lf") -> CrateExportPreview:
    try: request = CrateExportRequest(format=format, path_mode=path_mode, include_metadata=include_metadata, line_endings=line_endings)
    except Exception as exc: raise HTTPException(status_code=422, detail="Unsupported crate export options") from exc
    preview = crate_export_service.preview(crate_id, request)
    if preview is None: raise HTTPException(status_code=404, detail="Crate not found")
    return preview

@router.post("/exports/crates/{crate_id}", response_model=CrateExportResult, status_code=201)
async def export_crate(crate_id: int, body: CrateExportRequest) -> CrateExportResult:
    result = crate_export_service.write(crate_id, body)
    if result is None: raise HTTPException(status_code=404, detail="Crate not found")
    return result


# ---------------------------------------------------------------------------
# POST /api/exports/validate
# ---------------------------------------------------------------------------

@router.post("/exports/validate", response_model=ValidateResponse)
async def validate_export() -> ValidateResponse:
    """
    Validate the library for export without running the actual export.

    Checks every OK track in the pipeline DB against export requirements:
      - file exists on disk (stale path detection)
      - BPM present and in the 50–220 range
      - Camelot key present and valid format
      - artist and title present (filename fallback applied)
      - genre not a junk/placeholder value

    Returns structured stats, all excluded tracks with reasons, and warnings.
    This is a fast read-only operation — no subprocesses are spawned.
    """
    return run_validation()


# ---------------------------------------------------------------------------
# POST /api/exports/run
# ---------------------------------------------------------------------------

@router.post("/exports/run", response_model=ExportRunResponse, status_code=202)
async def run_export(body: ExportRunRequest) -> ExportRunResponse:
    """
    Dispatch a rekordbox-export job.

    Returns a job_id immediately. Poll GET /api/exports/{job_id} or
    GET /api/jobs/{job_id} for status and logs.

    MIK-first note: XML is disabled by default (force_xml=False).
    Enable it only if you are not using Mixed In Key.
    """
    args: list[str] = []

    if body.dry_run:
        args.append("--dry-run")
    if body.skip_m3u:
        args.append("--no-m3u")
    if body.force_xml:
        args.append("--force-xml")
    if body.recover_missing:
        args.append("--recover-missing-analysis")

    try:
        toolkit_runner.build_command("rekordbox-export", args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    job = job_service.create_job("rekordbox-export", args)

    try:
        toolkit_runner.create_and_start_job(job.id, job.command, job.args)
    except Exception as exc:
        job_service.mark_finished(job.id, status="failed", exit_code=-1)
        log.exception("Failed to start rekordbox-export job %s: %s", job.id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start export job: {exc}")

    flags = []
    if body.dry_run:
        flags.append("dry-run")
    if body.skip_m3u:
        flags.append("no-m3u")
    if body.force_xml:
        flags.append("force-xml")
    if body.recover_missing:
        flags.append("recover-missing")

    msg = "Export job queued"
    if flags:
        msg += f" ({', '.join(flags)})"

    return ExportRunResponse(job_id=job.id, message=msg)


# ---------------------------------------------------------------------------
# GET /api/exports
# ---------------------------------------------------------------------------

@router.get("/exports", response_model=List[JobResponse])
async def list_exports(
    limit:  int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0,  ge=0),
) -> List[JobResponse]:
    """
    Return past rekordbox-export jobs, newest first.

    These are a filtered view of the jobs table — the same job_id can be
    used with GET /api/jobs/{id}/logs to stream the log.
    """
    all_jobs = job_service.list_jobs(limit=500, offset=0)
    export_jobs = [j for j in all_jobs if j.command == "rekordbox-export"]
    page = export_jobs[offset: offset + limit]
    return [JobResponse.from_job(j) for j in page]


# ---------------------------------------------------------------------------
# GET /api/exports/{export_id}
# ---------------------------------------------------------------------------

@router.get("/exports/{export_id}", response_model=JobResponse)
async def get_export(export_id: str) -> JobResponse:
    """
    Return a single export job by its ID.

    The export_id is the same as the job_id returned by POST /api/exports/run.
    Use GET /api/jobs/{export_id}/logs to read the job log.
    """
    job = job_service.get_job(export_id)
    if job is None or job.command != "rekordbox-export":
        raise HTTPException(status_code=404, detail=f"Export job {export_id!r} not found")
    return JobResponse.from_job(job)
