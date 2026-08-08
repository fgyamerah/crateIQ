"""
Pydantic schemas for the SSD sync API.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Preview (dry-run)
# ---------------------------------------------------------------------------

class SyncPreviewRequest(BaseModel):
    source: Literal["library"] = Field(
        "library",
        description=(
            "The only supported source: the active workspace's Library "
            "folder (or, in Legacy Direct Library compatibility mode, the "
            "legacy root itself). Never the managed Inbox or Quarantine."
        ),
    )


class SyncFileChange(BaseModel):
    """A single file or directory that rsync would transfer."""
    path:      str
    is_dir:    bool = False


class SyncPreviewResponse(BaseModel):
    source_path:  str
    dest_path:    str
    file_count:   int                    # total transferable files (not dirs)
    files:        List[SyncFileChange]   # up to MAX_PREVIEW_FILES entries
    truncated:    bool = False
    summary:      Optional[str] = None  # "sent X bytes  …  total size is Y"
    warnings:     List[str] = []
    ssd_mounted:  bool = True


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class SyncRunRequest(BaseModel):
    source: Literal["library"] = "library"
    allow_delete: bool = Field(
        False,
        description=(
            "Pass --delete to rsync — removes files from the destination that "
            "are no longer in the source.  DESTRUCTIVE: enable only intentionally."
        ),
    )


class SyncRunResponse(BaseModel):
    job_id:  str
    message: str


# ---------------------------------------------------------------------------
# Status convenience (mirrors JobResponse subset for the sync history table)
# ---------------------------------------------------------------------------

class SyncConfigResponse(BaseModel):
    """Read-only config shown on the Sync page."""
    sources:  dict[str, str]   # name → resolved path ("" if not resolvable)
    dest:     Optional[str] = None   # None if no destination is configured yet
    destination_status:   Literal["ready", "needs_setup", "not_mounted", "unsafe"]
    destination_blockers: List[str] = []
    destination_warnings: List[str] = []
    rsync_bin: str
    ssd_mounted: bool
