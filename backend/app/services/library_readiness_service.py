"""
Conservative "ready for crates" readiness contract (Cycle 8).

Composes existing, already-computed signals -- library overview, sanitation/
repair review queues, waveform coverage, and tag write-back history -- into
explainable BLOCKER / WARNING / OPTIONAL reason codes. Never recomputes or
duplicates those underlying counts; this is read-only aggregation only.

A library is "ready" when there are zero blockers. Warnings and optional
items never block the next step -- they are surfaced so the reason is
never hidden, not to gate progress on cosmetic completeness.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.library_root import selected_library_root
from . import read_only as read_only_service
from . import tag_write_service, track_service, waveform_bulk_service
from modules import metadata_repair, metadata_sanitation


def _reason(code: str, severity: str, message: str, route: str | None = None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "route": route}


def build_readiness() -> dict[str, Any]:
    overview = read_only_service.build_overview_payload()
    total_tracks = int(overview.get("total_tracks", 0) or 0)

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []

    if total_tracks == 0:
        blockers.append(_reason(
            "no_tracks_imported", "blocker",
            "No tracks are imported into CrateIQ's local index yet.", "/library-prep",
        ))
        return {
            "total_tracks": 0, "ready": False,
            "blockers": blockers, "warnings": warnings, "optional": optional,
            "coverage": {}, "message": "Import a library before reviewing readiness.",
        }

    issue_counts = track_service.get_issue_counts()
    missing_title = int(issue_counts.get("missing_title", 0) or 0)
    missing_artist = int(issue_counts.get("missing_artist", 0) or 0)
    if missing_title:
        blockers.append(_reason(
            "missing_required_title", "blocker",
            f"{missing_title} track(s) are missing a title.", "/issues",
        ))
    if missing_artist:
        blockers.append(_reason(
            "missing_required_artist", "blocker",
            f"{missing_artist} track(s) are missing an artist.", "/issues",
        ))

    try:
        failed_write_ops = [
            op for op in tag_write_service.list_operations(limit=20)
            if op["status"] in ("failed", "partially_failed")
        ]
    except Exception:
        failed_write_ops = []
    if failed_write_ops:
        blockers.append(_reason(
            "failed_tag_write_verification", "blocker",
            f"{len(failed_write_ops)} write-back operation(s) failed or partially failed verification.",
            "/apply-to-files",
        ))

    root = selected_library_root()
    try:
        sanitation_pending = int(metadata_sanitation.summary(root).get("pending_count", 0) or 0)
        repair_pending = int(metadata_repair.summary(root).get("pending_count", 0) or 0)
    except Exception:
        sanitation_pending = repair_pending = 0
    if sanitation_pending:
        warnings.append(_reason(
            "unresolved_sanitation_review", "warning",
            f"{sanitation_pending} metadata sanitation suggestion(s) are awaiting review.", "/metadata-sanitation",
        ))
    if repair_pending:
        warnings.append(_reason(
            "unresolved_repair_review", "warning",
            f"{repair_pending} metadata repair suggestion(s) are awaiting review.", "/metadata-repair",
        ))

    with_bpm = int(overview.get("tracks_with_bpm", 0) or 0)
    with_key = int(overview.get("tracks_with_camelot_key", 0) or 0)
    missing_bpm = total_tracks - with_bpm
    missing_key = total_tracks - with_key
    if missing_bpm:
        warnings.append(_reason(
            "missing_bpm_coverage", "warning",
            f"{missing_bpm} track(s) have no BPM.", "/library-prep",
        ))
    if missing_key:
        warnings.append(_reason(
            "missing_key_coverage", "warning",
            f"{missing_key} track(s) have no musical key.", "/library-prep",
        ))

    try:
        waveform_preview = waveform_bulk_service.preview_missing()
    except Exception:
        waveform_preview = {"missing": 0, "unsupported": 0, "total_tracks": total_tracks, "ready": 0}
    missing_waveforms = int(waveform_preview.get("missing", 0) or 0)
    if missing_waveforms:
        warnings.append(_reason(
            "missing_waveform_coverage", "warning",
            f"{missing_waveforms} track(s) have no generated waveform.", "/library-prep",
        ))

    missing_genre = total_tracks - int(
        _count_with_genre(root) if total_tracks else 0
    )
    if missing_genre > 0:
        optional.append(_reason(
            "missing_genre", "optional",
            f"{missing_genre} track(s) have no genre tag.", "/genres",
        ))

    return {
        "total_tracks": total_tracks,
        "ready": len(blockers) == 0,
        "blockers": blockers,
        "warnings": warnings,
        "optional": optional,
        "coverage": {
            "with_bpm": with_bpm, "with_key": with_key,
            "waveforms_ready": int(waveform_preview.get("ready", 0) or 0),
            "waveforms_missing": missing_waveforms,
        },
        "message": (
            "No blockers -- continue to crates when you're ready."
            if not blockers else
            f"{len(blockers)} blocker(s) to resolve before this library is ready for crates."
        ),
    }


def _count_with_genre(root: Path) -> int:
    import sqlite3
    from ..core.library_root import library_db_path
    db_path = library_db_path(root)
    if not db_path.is_file():
        return 0
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM tracks WHERE TRIM(COALESCE(genre, '')) != ''").fetchone()
    return int(row[0] or 0) if row else 0
