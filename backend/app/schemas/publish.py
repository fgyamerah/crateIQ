"""
Pydantic schemas for the Guided Publish readiness contract.

This composes the existing crate export and SSD sync capabilities into one
truthful, read-only snapshot. It never exports or syncs anything itself.

Preview is not approval. Approval is not execution. Execution is not
verification — this contract only ever reports the first of those states.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

PublishExportTarget = Literal["csv", "json", "m3u", "m3u8", "rekordbox_xml", "serato"]
PublishSyncSource = Literal["library", "inbox"]


class PublishReadiness(BaseModel):
    crate_id: int
    crate_name: str
    track_count: int

    export_target: PublishExportTarget
    export_destination_category: str = Field(
        description="Category/relative description of where the export would be "
        "staged, e.g. '<library-root>/exports/serato'. Never a claim that a "
        "live Rekordbox/Serato database would be written."
    )
    export_ready: bool

    sync_source: PublishSyncSource
    sync_destination_category: str = Field(
        description="Category description of the configured sync destination, "
        "e.g. 'external_ssd'. Never implies the destination is user-configurable "
        "from this contract."
    )
    sync_ready: bool

    blockers: List[str] = []
    warnings: List[str] = []
    conflicts: List[str] = []

    confirmation_required: bool
    next_operation: Literal["export", "sync", "none"]
