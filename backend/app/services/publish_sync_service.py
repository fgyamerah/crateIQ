"""
Guarded SSD sync lifecycle: validate -> preview (dry-run) -> confirm ->
execute -> verify.

This is a thin orchestrator over the existing, already-safe rsync_runner
(preview_sync/start_sync_job unchanged, no --delete ever requested here).
It never re-implements the rsync invocation or progress parsing -- it only
adds structural destination-safety validation before preview/execute, and
a post-execution verification pass that re-runs the same safe dry-run
preview and checks it now reports zero pending file transfers.

Preview is not approval. Approval is not execution. Execution is not
verification.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..core.config import SYNC_DEST_SSD, SYNC_SOURCE_MAP
from ..schemas.publish import (
    PublishSyncConfirmResponse,
    PublishSyncPreview,
    PublishSyncStatus,
    PublishSyncSource,
)
from ..schemas.sync import SyncPreviewRequest
from . import job_service, publish_operations_service, rsync_runner
from .publish_safety import evaluate_sync_paths

_JOB_TERMINAL_TO_OPERATION_STATUS = {
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


class PublishSyncBlocked(ValueError):
    """Raised when confirm() is called but blockers exist or confirm is not true."""


def _validate(sync_source: PublishSyncSource) -> Tuple[List[str], List[str]]:
    source_path = SYNC_SOURCE_MAP.get(sync_source)
    return evaluate_sync_paths(source_path, SYNC_DEST_SSD)


async def preview(sync_source: PublishSyncSource) -> PublishSyncPreview:
    blockers, warnings = _validate(sync_source)
    source_path = SYNC_SOURCE_MAP.get(sync_source)

    if blockers:
        # Never spawn rsync when the destination/source is already unsafe.
        return PublishSyncPreview(
            sync_source=sync_source,
            source_path=str(source_path) if source_path else "",
            dest_path=str(SYNC_DEST_SSD),
            ssd_mounted=SYNC_DEST_SSD.exists() and SYNC_DEST_SSD.is_dir(),
            file_count=0,
            files=[],
            truncated=False,
            blockers=blockers,
            warnings=warnings,
            confirmation_required=False,
        )

    result = await rsync_runner.preview_sync(SyncPreviewRequest(source=sync_source))
    return PublishSyncPreview(
        sync_source=sync_source,
        source_path=result.source_path,
        dest_path=result.dest_path,
        ssd_mounted=result.ssd_mounted,
        file_count=result.file_count,
        files=result.files,
        truncated=result.truncated,
        blockers=blockers,
        warnings=warnings + list(result.warnings),
        confirmation_required=True,
    )


def confirm(sync_source: PublishSyncSource, confirm: bool) -> PublishSyncConfirmResponse:
    if not confirm:
        raise PublishSyncBlocked("Explicit confirm=true is required to execute a sync.")

    blockers, _warnings = _validate(sync_source)
    if blockers:
        raise PublishSyncBlocked("; ".join(blockers))

    try:
        job = rsync_runner.start_sync_job(source_key=sync_source, allow_delete=False)
    except ValueError as exc:
        raise PublishSyncBlocked(str(exc)) from exc

    operation = publish_operations_service.start_operation(
        "sync",
        sync_source=sync_source,
        job_id=job.id,
        scope=f"sync:{sync_source}",
    )
    return PublishSyncConfirmResponse(
        operation_id=operation["id"],
        job_id=job.id,
        message="Sync job queued (no --delete)",
    )


async def get_status(operation_id: str) -> Optional[PublishSyncStatus]:
    row = publish_operations_service.get_operation(operation_id)
    if row is None or row.get("operation_type") != "sync":
        return None

    job = job_service.get_job(row["job_id"]) if row.get("job_id") else None
    job_status = job.status if job else None

    if row["status"] == "running" and job is not None and job.status in _JOB_TERMINAL_TO_OPERATION_STATUS:
        final_status = _JOB_TERMINAL_TO_OPERATION_STATUS[job.status]
        if final_status == "completed":
            verification_status, verification_details = await _verify_sync(row["sync_source"])
            result = "synced"
            error_reason = None
        else:
            verification_status, verification_details = "skipped", [
                f"Sync job ended with status={job.status}; verification skipped."
            ]
            result = "not_synced"
            error_reason = f"rsync job {job.status} (exit_code={job.exit_code})"

        publish_operations_service.finish_operation(
            operation_id,
            status=final_status,
            destination_relative=f"external_ssd:{row['sync_source']}",
            result=result,
            verification_status=verification_status,
            verification_details=verification_details,
            warnings=[],
            error_reason=error_reason,
        )
        row = publish_operations_service.get_operation(operation_id)
        assert row is not None

    return PublishSyncStatus(
        operation_id=row["id"],
        sync_source=row.get("sync_source"),
        job_id=row.get("job_id"),
        status=row["status"],
        job_status=job_status,
        progress_current=job.progress_current if job else None,
        progress_total=job.progress_total if job else None,
        progress_percent=job.progress_percent if job else None,
        destination_relative=row.get("destination_relative"),
        result=row.get("result"),
        verification_status=row.get("verification_status"),
        verification_details=row.get("verification_details") or [],
        warnings=row.get("warnings") or [],
        error_reason=row.get("error_reason"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


async def _verify_sync(sync_source: str) -> Tuple[str, List[str]]:
    """
    Re-run the same safe dry-run preview after the sync job finished. If it
    now reports zero pending file transfers, the destination matches the
    source. Never re-runs a live sync; never mutates anything.
    """
    result = await rsync_runner.preview_sync(SyncPreviewRequest(source=sync_source))
    if result.warnings:
        return "failed", [f"Post-sync dry-run reported warnings: {'; '.join(result.warnings)}"]
    if result.file_count > 0:
        sample = ", ".join(f.path for f in result.files[:5])
        return "failed", [f"Post-sync dry-run still reports {result.file_count} pending file(s): {sample}"]
    return "verified", ["Post-sync dry-run reports zero pending file transfers; destination matches source."]
