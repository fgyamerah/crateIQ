"""Bulk waveform generation routes (Waveform Jobs Stage 2).

  GET  /api/waveform-bulk/preview                    — read-only counts
  POST /api/waveform-bulk/generate-missing            — start a bulk run
  GET  /api/waveform-bulk/operations                  — persisted run history
  GET  /api/waveform-bulk/operations/{operation_id}   — one run's detail/progress
  POST /api/waveform-bulk/operations/{operation_id}/cancel — request cancellation

This is a parent/batch orchestration layer only. Every actual generation
still goes through the existing single-track path
(``waveform_job_service`` + ``WaveformScheduler``) exactly as
``POST /api/tracks/{id}/waveform/generate`` uses it — no second generation
system is introduced here, and the preview endpoint never enqueues work.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ...schemas.waveform import (
    WaveformBulkHistoryResponse,
    WaveformBulkOperation,
    WaveformBulkPreviewResponse,
    WaveformBulkStartResponse,
)
from ...services import waveform_bulk_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["waveforms"])


@router.get("/waveform-bulk/preview", response_model=WaveformBulkPreviewResponse)
async def preview_bulk_waveform_generation() -> WaveformBulkPreviewResponse:
    """Read-only counts of ready/missing/generating/failed waveforms.

    Never enqueues a job, runs FFmpeg, or writes any state.
    """
    return WaveformBulkPreviewResponse(**waveform_bulk_service.preview_missing())


@router.post(
    "/waveform-bulk/generate-missing",
    response_model=WaveformBulkStartResponse,
    status_code=202,
)
async def start_bulk_waveform_generation() -> WaveformBulkStartResponse:
    """Start an explicit bulk run over every track that currently lacks a
    valid waveform. Returns immediately; poll the operation id for progress.
    """
    return WaveformBulkStartResponse(**waveform_bulk_service.start_generate_missing())


@router.get("/waveform-bulk/operations", response_model=WaveformBulkHistoryResponse)
async def list_bulk_waveform_operations() -> WaveformBulkHistoryResponse:
    """Persisted history of explicit, confirmed bulk runs. Previews are never recorded."""
    return WaveformBulkHistoryResponse(**waveform_bulk_service.history())


@router.get("/waveform-bulk/operations/{operation_id}", response_model=WaveformBulkOperation)
async def get_bulk_waveform_operation(operation_id: str) -> WaveformBulkOperation:
    try:
        return WaveformBulkOperation(**waveform_bulk_service.operation_detail(operation_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/waveform-bulk/operations/{operation_id}/cancel",
    response_model=WaveformBulkOperation,
)
async def cancel_bulk_waveform_operation(operation_id: str) -> WaveformBulkOperation:
    """Idempotently request cancellation.

    Stops scheduling new tracks; the track currently in flight (if any) is
    left to finish so its recorded outcome stays truthful. Cancelling an
    already-cancelled or already-terminal operation simply returns its
    current record.
    """
    try:
        return WaveformBulkOperation(**waveform_bulk_service.cancel_operation(operation_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
