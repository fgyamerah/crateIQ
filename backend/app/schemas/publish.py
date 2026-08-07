"""
Pydantic schemas for the Guided Publish readiness contract.

This composes the existing crate export and SSD sync capabilities into one
truthful, read-only snapshot. It never exports or syncs anything itself.

Preview is not approval. Approval is not execution. Execution is not
verification — this contract only ever reports the first of those states.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .export import CrateLineEndings, CratePathMode

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


# ---------------------------------------------------------------------------
# Guarded export lifecycle: validate -> preview -> confirm -> execute -> verify
# ---------------------------------------------------------------------------

class PublishExportPreview(BaseModel):
    crate_id: int
    crate_name: str
    export_target: PublishExportTarget
    target_path: str = Field(
        description="Exact path the write would use if run immediately. Since "
        "the name is timestamp-based, a later confirm may compute a different "
        "(still guaranteed-unique, never-overwriting) path."
    )
    target_exists: bool
    proposed_filename: str
    track_count: int
    warnings: List[str] = []
    blockers: List[str] = []
    no_overwrite: bool = True
    confirmation_required: bool = True


class PublishExportRequest(BaseModel):
    export_target: PublishExportTarget
    path_mode: CratePathMode = "filename"
    include_metadata: bool = True
    line_endings: CrateLineEndings = "lf"
    confirm: bool = Field(
        False,
        description="Must be explicitly true to execute. False/omitted is "
        "rejected — there is no implicit-confirm default.",
    )


class PublishExportResult(BaseModel):
    operation_id: str
    crate_id: int
    crate_name: str
    export_target: PublishExportTarget
    written: bool
    output_path: str
    track_count: int
    verification_status: Literal["verified", "failed", "skipped"]
    verification_details: List[str] = []
    warnings: List[str] = []


class PublishOperationSummary(BaseModel):
    id: str
    operation_type: Literal["export", "sync"]
    export_target: Optional[str] = None
    sync_source: Optional[str] = None
    status: Literal["running", "completed", "failed", "cancelled"]
    crate_id: Optional[int] = None
    crate_name: Optional[str] = None
    scope: Optional[str] = None
    track_count: int = 0
    destination_relative: Optional[str] = None
    result: Optional[str] = None
    verification_status: Optional[Literal["verified", "failed", "skipped"]] = None
    verification_details: List[str] = []
    warnings: List[str] = []
    error_reason: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
