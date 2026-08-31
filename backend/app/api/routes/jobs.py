"""
Job routes.

  POST   /api/jobs               — submit a new pipeline job
  GET    /api/jobs               — list jobs (newest first)
  GET    /api/jobs/{job_id}      — get a single job
  GET    /api/jobs/{job_id}/logs — return the job's stdout+stderr log
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ...schemas.job import JobCreate, JobResponse
from ...services import job_service, toolkit_runner
from ...services import process_registry

log = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


# ---------------------------------------------------------------------------
# POST /api/jobs
# ---------------------------------------------------------------------------

@router.post("/jobs", response_model=JobResponse, status_code=202)
async def submit_job(body: JobCreate) -> JobResponse:
    """
    Submit a new pipeline job.

    The job is created immediately with status=pending and the subprocess
    is started in the background.  Poll GET /api/jobs/{job_id} for status.

    The command must be one of the allowed pipeline.py subcommands.
    The args list may only contain validated flags from the allowlist.
    """
    # Validate the full command+args before writing anything to the DB.
    # build_command() raises ValueError on any disallowed token.
    try:
        toolkit_runner.build_command(body.command, body.args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    job = job_service.create_job(body.command, body.args)

    try:
        toolkit_runner.create_and_start_job(
            job.id, job.command, job.args, library_key=job.library_key
        )
    except Exception as exc:
        # The job record exists in the DB; mark it failed so it's visible.
        job_service.mark_finished(
            job.id, status="failed", exit_code=-1, library_key=job.library_key
        )
        log.exception("Failed to start job %s: %s", job.id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start job: {exc}")

    return JobResponse.from_job(job)


# ---------------------------------------------------------------------------
# GET /api/jobs
# ---------------------------------------------------------------------------

@router.get("/jobs", response_model=List[JobResponse])
async def list_jobs(
    limit: int  = Query(default=50,  ge=1, le=500),
    offset: int = Query(default=0,   ge=0),
) -> List[JobResponse]:
    """
    Return jobs ordered by creation time descending.

    Supports basic pagination via `limit` and `offset`.
    """
    jobs = job_service.list_jobs(limit=limit, offset=offset)
    return [JobResponse.from_job(j) for j in jobs]


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """Return the current state of a single job."""
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
    return JobResponse.from_job(job)


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/logs
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}/logs", response_class=PlainTextResponse)
async def get_job_logs(
    job_id: str,
    tail: Optional[int] = Query(
        default=None,
        ge=1,
        le=10_000,
        description="Return only the last N lines of the log.",
    ),
) -> str:
    """
    Return the raw stdout+stderr log for a job as plain text.

    If the job is still running the log is written live, so calling this
    endpoint multiple times shows incremental output.

    Use ?tail=N to return only the last N lines (useful for large logs).
    """
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

    if not job.log_path:
        return ""

    log_path = Path(job.log_path)
    if not log_path.exists():
        # Job may be pending (not yet started) or log was never written
        if job.status == "pending":
            return "(job is pending — not yet started)\n"
        return "(log file not found)\n"

    # Read with errors="replace" so a corrupt byte sequence doesn't 500
    text = log_path.read_text(encoding="utf-8", errors="replace")

    if tail is not None:
        lines = text.splitlines(keepends=True)
        text = "".join(lines[-tail:])

    return text


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/cancel
# ---------------------------------------------------------------------------

class CancelResponse(BaseModel):
    job_id:  str
    success: bool
    message: str


@router.post("/jobs/{job_id}/cancel", response_model=CancelResponse)
async def cancel_job(job_id: str) -> CancelResponse:
    """
    Send SIGTERM to the running process for a job.

    Returns immediately — the job status transitions to 'cancelled' once
    the process actually exits (usually within a second or two).  Poll
    GET /api/jobs/{job_id} to confirm the final status.

    Returns 404 if the job is unknown.
    Returns 409 if the job is not in a cancellable state (pending / running).
    Returns success=False (200) if the process is no longer in the registry
    (e.g. it finished between the request and the SIGTERM delivery).
    """
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Job {job_id!r} is {job.status!r} and cannot be cancelled. "
                "Only pending or running jobs can be cancelled."
            ),
        )

    sent = process_registry.request_cancel(job_id)

    if sent:
        return CancelResponse(
            job_id  = job_id,
            success = True,
            message = "SIGTERM sent — job will be marked cancelled once it exits.",
        )
    else:
        # Process not in registry: may have just finished, or was pending
        # and hadn't spawned yet.  Mark it cancelled in the DB directly.
        job_service.mark_finished(
            job_id,
            status="cancelled",
            exit_code=-15,
            library_key=job.library_key,
        )
        return CancelResponse(
            job_id  = job_id,
            success = True,
            message = "Job was not running; marked cancelled.",
        )
