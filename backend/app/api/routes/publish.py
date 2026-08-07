"""
Publish routes.

  GET /api/publish/readiness/{crate_id} — read-only readiness contract
      composing existing crate export + SSD sync capabilities. Never
      exports or syncs anything; deterministic and side-effect-free.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...schemas.publish import PublishExportTarget, PublishReadiness, PublishSyncSource
from ...services import publish_readiness_service

router = APIRouter(tags=["publish"])


@router.get("/publish/readiness/{crate_id}", response_model=PublishReadiness)
async def get_publish_readiness(
    crate_id: int,
    export_target: PublishExportTarget = "m3u8",
    sync_source: PublishSyncSource = "library",
) -> PublishReadiness:
    """
    Return a truthful readiness snapshot for publishing one Manual Crate.

    A ready state is not an approval — the guided export/sync flow still
    requires an explicit preview and explicit confirmation before anything
    is written.
    """
    result = publish_readiness_service.get_readiness(crate_id, export_target, sync_source)
    if result is None:
        raise HTTPException(status_code=404, detail="Crate not found")
    return result
