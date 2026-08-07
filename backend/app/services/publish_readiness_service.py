"""
Read-only publish readiness contract.

Composes the existing crate export services and SSD sync configuration into
one truthful, side-effect-free snapshot. This module never exports or syncs
anything — it only reads already-published state (crate contents, pipeline
DB rows, sync config, and existing export artifacts on disk).

A green/ready state here is not an approval: the guided flow still requires
an explicit preview and an explicit confirmation before anything is written.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..core.config import SYNC_DEST_SSD, SYNC_SOURCE_MAP
from ..core.library_root import selected_library_root
from ..schemas.export import CrateExportRequest
from ..schemas.publish import PublishExportTarget, PublishReadiness, PublishSyncSource
from . import crate_export_service, crate_service
from .publish_safety import evaluate_sync_paths

_STAGED_TARGETS = {"rekordbox_xml", "serato"}
_STAGED_SUBDIR = {"rekordbox_xml": "rekordbox", "serato": "serato"}


def get_readiness(
    crate_id: int,
    export_target: PublishExportTarget = "m3u8",
    sync_source: PublishSyncSource = "library",
) -> Optional[PublishReadiness]:
    crate = crate_service.get_crate(crate_id)
    if crate is None:
        return None

    blockers: List[str] = []
    warnings: List[str] = []
    conflicts: List[str] = []

    track_count = len(crate.tracks)
    if track_count == 0:
        blockers.append("[export] Crate has no tracks — nothing to publish.")

    root = selected_library_root()

    if export_target in _STAGED_TARGETS:
        export_dir = root / "exports" / _STAGED_SUBDIR[export_target]
        export_destination_category = str(export_dir)
    else:
        export_dir = root / "exports"
        export_destination_category = str(export_dir)

    # Reuse the real portable-export preview to surface per-track path
    # warnings (e.g. a track with no usable library path) without writing
    # anything — preview() never touches disk.
    if track_count > 0:
        portable = crate_export_service.preview(
            crate_id, CrateExportRequest(format="m3u8", path_mode="filename")
        )
        if portable is not None:
            warnings.extend(f"[export] {w}" for w in portable.warnings)

    # Informational: prior exports already exist for this crate. No-overwrite
    # naming already prevents collisions, so this is a conflict to disclose,
    # never a blocker.
    prior = _existing_export_conflicts(export_dir, crate.name)
    if prior:
        conflicts.append(
            f"[export] {prior} prior export artifact(s) already exist for this "
            "crate at this destination; a new uniquely named file will be "
            "created (no existing file will be overwritten)."
        )

    export_ready = not blockers

    # --- Sync side -----------------------------------------------------
    sync_source_path = SYNC_SOURCE_MAP.get(sync_source)
    sync_dest_path = SYNC_DEST_SSD
    sync_destination_category = "external_ssd"

    raw_blockers, raw_warnings = evaluate_sync_paths(sync_source_path, sync_dest_path)
    sync_blockers = [f"[sync] {b}" for b in raw_blockers]
    sync_warnings = [f"[sync] {w}" for w in raw_warnings]

    sync_ready = not sync_blockers

    all_blockers = blockers + sync_blockers
    all_warnings = warnings + sync_warnings

    confirmation_required = export_ready or sync_ready
    next_operation: str = "export" if export_ready else "none"

    return PublishReadiness(
        crate_id=crate.id,
        crate_name=crate.name,
        track_count=track_count,
        export_target=export_target,
        export_destination_category=export_destination_category,
        export_ready=export_ready,
        sync_source=sync_source,
        sync_destination_category=sync_destination_category,
        sync_ready=sync_ready,
        blockers=all_blockers,
        warnings=all_warnings,
        conflicts=conflicts,
        confirmation_required=confirmation_required,
        next_operation=next_operation,  # type: ignore[arg-type]
    )


def _existing_export_conflicts(export_dir: Path, crate_name: str) -> int:
    """Count prior export artifacts for this crate already on disk (read-only)."""
    if not export_dir.exists() or not export_dir.is_dir():
        return 0
    prefix = crate_export_service._safe_name(crate_name) + "_"
    try:
        return sum(1 for entry in export_dir.iterdir() if entry.name.startswith(prefix))
    except OSError:
        return 0
