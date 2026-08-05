"""
Analysis routes — BPM anomaly detection and re-analysis job dispatch.

  POST  /api/analysis/bpm-check              — scan pipeline DB, return anomalies
  GET   /api/analysis/bpm-anomalies          — list stored anomaly records
  GET   /api/analysis/bpm-anomalies/summary  — counts by status / reason
  PATCH /api/analysis/bpm-anomalies/{id}     — update review status
  POST  /api/analysis/reanalyze              — queue an analyze-missing job

Design notes
------------
  • bpm-check is idempotent: safe to call repeatedly.  It upserts anomaly
    records and marks previously-anomalous tracks as 'resolved' if they
    now look fine.
  • reanalyze delegates entirely to the existing job system — no new
    analysis code lives here.  The pipeline's analyze-missing + --reanalyze
    flag handles all detection and tag writes.
  • The bpm_anomalies table lives in the backend's jobs.db — it is NOT
    written to the pipeline's processed.db.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from ...schemas.bpm_analysis import (
    BpmAnomalyResponse,
    BpmCheckResult,
    BpmSummary,
    ReanalyzeRequest,
    UpdateAnomalyRequest,
)
from ...schemas.job import JobResponse
from ...schemas.analysis_jobs import (
    BpmAnalysisRunRequest,
    BpmAnalysisRunResult,
    AnalysisJobHistoryResponse,
    AnalysisJobListResponse,
    AnalysisJobPreview,
)
from ...schemas.mik_metadata import MikCoverageResponse, MikImportResponse
from ...services import (
    analysis_jobs_service,
    bpm_analysis,
    job_service,
    mik_metadata_service,
    toolkit_runner,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["analysis"])


@router.get("/analysis/jobs", response_model=AnalysisJobListResponse)
async def list_analysis_jobs() -> AnalysisJobListResponse:
    """List optional workflows and their safe, local candidate counts."""
    return AnalysisJobListResponse(**analysis_jobs_service.list_jobs())


@router.get("/analysis/jobs/history", response_model=AnalysisJobHistoryResponse)
async def get_analysis_job_history() -> AnalysisJobHistoryResponse:
    """Return persisted analysis history once a safe runner exists."""
    return AnalysisJobHistoryResponse(**analysis_jobs_service.history())


@router.get("/analysis/jobs/{job_type}/preview", response_model=AnalysisJobPreview)
async def preview_analysis_job(job_type: str) -> AnalysisJobPreview:
    """Read candidate records only; this never starts an external tool."""
    try:
        return AnalysisJobPreview(**analysis_jobs_service.preview(job_type))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/analysis/jobs/{job_type}/run", response_model=BpmAnalysisRunResult)
async def run_analysis_job(job_type: str, body: BpmAnalysisRunRequest = BpmAnalysisRunRequest()):
    """Run only the explicitly confirmed safe BPM DB-only workflow."""
    try:
        result = analysis_jobs_service.run(job_type, confirm=body.confirm, limit=body.limit)
        return BpmAnalysisRunResult(**result)
    except ValueError as exc:
        status_code = 404 if "Unknown" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/analysis/mik/coverage", response_model=MikCoverageResponse)
async def get_mik_coverage() -> MikCoverageResponse:
    """Read current local-index MIK-compatible coverage without scanning files."""
    return MikCoverageResponse(**mik_metadata_service.coverage())


@router.post("/analysis/mik/preview", response_model=MikCoverageResponse)
async def preview_mik_metadata() -> MikCoverageResponse:
    """Explicitly read imported file tags; never writes media or the database."""
    return MikCoverageResponse(**mik_metadata_service.preview())


@router.post("/analysis/mik/import", response_model=MikImportResponse)
async def import_mik_metadata() -> MikImportResponse:
    """Persist only missing trusted tag values into CrateIQ's local index."""
    try:
        return MikImportResponse(**mik_metadata_service.import_metadata())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /api/analysis/bpm-check
# ---------------------------------------------------------------------------

@router.post("/analysis/bpm-check", response_model=BpmCheckResult)
async def run_bpm_check() -> BpmCheckResult:
    """
    Scan every track in the pipeline library database, classify its BPM value
    using heuristic rules, and upsert the results into the anomaly review table.

    - Tracks with no BPM anomaly that were previously flagged are marked
      'resolved' automatically.
    - Human review decisions (reviewed / ignored / requeued) are preserved
      even if the same track is re-scanned.

    Safe to call multiple times — fully idempotent.
    """
    scanned, new_count, resolved_count, items = bpm_analysis.run_bpm_check()
    return BpmCheckResult(
        tracks_scanned=scanned,
        new_anomalies=new_count,
        resolved=resolved_count,
        total_active=len(items),
        items=items,
    )


# ---------------------------------------------------------------------------
# GET /api/analysis/bpm-anomalies/summary  (must precede /{id})
# ---------------------------------------------------------------------------

@router.get("/analysis/bpm-anomalies/summary", response_model=BpmSummary)
async def get_bpm_summary() -> BpmSummary:
    """Return aggregate counts of anomaly records grouped by status and reason."""
    raw = bpm_analysis.get_summary_by_reason()
    return BpmSummary(
        by_status=raw["by_status"],
        by_reason=raw["by_reason"],
    )


# ---------------------------------------------------------------------------
# GET /api/analysis/bpm-anomalies
# ---------------------------------------------------------------------------

@router.get("/analysis/bpm-anomalies", response_model=List[BpmAnomalyResponse])
async def list_bpm_anomalies(
    status: Optional[str] = Query(
        default=None,
        description=(
            "Filter by review_status. "
            "One of: pending | reviewed | ignored | requeued | resolved | all. "
            "Defaults to all non-resolved records."
        ),
    ),
    reason: Optional[str] = Query(
        default=None,
        description=(
            "Filter by reason: missing_bpm | too_low_10x | "
            "likely_halved | likely_doubled | too_high"
        ),
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> List[BpmAnomalyResponse]:
    """Return stored BPM anomaly records with optional status/reason filters."""
    return bpm_analysis.list_anomalies(
        status=status, reason=reason, limit=limit, offset=offset
    )


# ---------------------------------------------------------------------------
# PATCH /api/analysis/bpm-anomalies/{anomaly_id}
# ---------------------------------------------------------------------------

@router.patch(
    "/analysis/bpm-anomalies/{anomaly_id}",
    response_model=BpmAnomalyResponse,
)
async def update_bpm_anomaly(
    anomaly_id: int,
    body: UpdateAnomalyRequest,
) -> BpmAnomalyResponse:
    """
    Update the review status of a BPM anomaly record.

    Actions:
      reviewed  — operator has verified the BPM (accepted as-is)
      ignored   — dismiss the flag (won't appear in the default list)
      requeued  — mark that a re-analysis job has been requested
      pending   — reset to unreviewed
    """
    try:
        updated = bpm_analysis.update_anomaly(
            anomaly_id,
            review_status=body.review_status,
            review_note=body.review_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=404, detail=f"Anomaly {anomaly_id} not found.")
    return updated


# ---------------------------------------------------------------------------
# POST /api/analysis/reanalyze
# ---------------------------------------------------------------------------

@router.post("/analysis/reanalyze", response_model=JobResponse, status_code=202)
async def reanalyze(body: ReanalyzeRequest) -> JobResponse:
    """
    Submit an analyze-missing job through the existing job tracking system.

    With force=True (default), passes --reanalyze so the pipeline re-detects
    BPM/key even for tracks that already have a value stored — this is the
    only way to fix incorrect (but present) BPM values.

    With force=False, only tracks with missing BPM/key are processed.

    The returned job can be polled via GET /api/jobs/{id} and logs viewed
    via GET /api/jobs/{id}/logs.
    """
    args: List[str] = []
    if body.force:
        args.append("--reanalyze")
    if body.dry_run:
        args.append("--dry-run")

    try:
        toolkit_runner.build_command("analyze-missing", args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    job = job_service.create_job("analyze-missing", args)

    try:
        toolkit_runner.create_and_start_job(job.id, "analyze-missing", args)
    except Exception as exc:
        job_service.mark_finished(job.id, status="failed", exit_code=-1)
        log.exception("Failed to start reanalyze job %s: %s", job.id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start job: {exc}")

    return JobResponse.from_job(job)
