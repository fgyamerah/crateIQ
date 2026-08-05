"""Safe, read-only catalog for optional analysis and enrichment jobs.

This service deliberately does not invoke external tools or create pipeline
jobs. It provides candidate previews so the Jobs page can truthfully expose
which advanced workflows are ready to configure versus implemented.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import mik_metadata_service, settings_service

_JOB_TYPES = (
    "mixed_in_key_coverage",
    "bpm_analysis",
    "key_analysis",
    "beets_enrichment",
    "duplicate_detection",
    "audio_quality_probe",
)
_SAMPLE_LIMIT = 12
_SAFETY = ["missing_data_only", "no_tag_writes", "preserve_trusted_values"]
_BPM_MIN = 40.0
_BPM_MAX = 250.0
_BPM_TIMEOUT_SECONDS = 20
_BPM_MIGRATION_COLUMNS = {
    "bpm_source": "TEXT",
    "bpm_trusted": "INTEGER NOT NULL DEFAULT 0",
    "bpm_analyzed_at": "TEXT",
}


def _track_rows() -> list[dict[str, Any]]:
    root = selected_library_root()
    db_path = assert_path_under_root(library_db_path(root), root)
    if not db_path.is_file():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        required = {"id", "filename", "artist", "title", "genre", "bpm", "key_musical", "key_camelot"}
        if not required.issubset(columns):
            return []
        return [dict(row) for row in conn.execute(
            "SELECT id, filepath, filename, artist, title, genre, bpm, key_musical, key_camelot FROM tracks ORDER BY id"
        )]


def _resolve_aubio_binary() -> str | None:
    """Resolve only the modern aubio CLI; no librosa/aubiotrack fallback."""
    override = os.environ.get("AUBIO_BIN", "").strip()
    if override:
        resolved = shutil.which(override)
        if resolved and os.access(resolved, os.X_OK):
            return resolved
        return None
    resolved = shutil.which("aubio")
    return resolved if resolved and os.access(resolved, os.X_OK) else None


def _parse_aubio_bpm(output: str) -> float | None:
    """Return a conservative median BPM from aubio tempo output.

    Accepts a single BPM (``128.0``), a timestamp/BPM pair
    (``0.371 128.0``), and summary output (``123.26 bpm``). For pair-style
    lines the last numeric value is used so elapsed seconds cannot be mistaken
    for BPM on longer tracks.
    """
    values: list[float] = []
    for raw_line in output.splitlines():
        numbers = [float(token) for token in re.findall(r"[-+]?\d+(?:\.\d+)?", raw_line)]
        if not numbers:
            continue
        candidate = numbers[-1]
        if _BPM_MIN <= candidate <= _BPM_MAX:
            values.append(candidate)
    if not values:
        return None
    value = float(statistics.median(values))
    return round(value, 2) if _BPM_MIN <= value <= _BPM_MAX else None


def _ensure_bpm_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    for name, definition in _BPM_MIGRATION_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {definition}")


def _bpm_candidates(conn: sqlite3.Connection, root: Path, limit: int | None = None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
               bpm_source, bpm_trusted
        FROM tracks
        WHERE bpm IS NULL
        ORDER BY id
    """
    params: list[int] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def _as_candidate(row: dict[str, Any]) -> dict[str, Any]:
    root = selected_library_root()
    try:
        relative_path = str(assert_path_under_root(row["filepath"], root).relative_to(root))
    except (KeyError, TypeError, ValueError):
        relative_path = None
    return {
        "track_id": row["id"],
        "filename": row["filename"],
        "relative_path": relative_path,
        "artist": row.get("artist"),
        "title": row.get("title"),
        "genre": row.get("genre"),
        "bpm": row.get("bpm"),
        "key_camelot": row.get("key_camelot"),
        "key_musical": row.get("key_musical"),
    }


def _tool_ready(capability: dict[str, Any]) -> bool:
    return bool(capability.get("available"))


def _definitions() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _track_rows()
    capabilities = settings_service.get_capabilities()["analysis"]
    total = len(rows)
    missing_bpm = [row for row in rows if row.get("bpm") is None]
    missing_key = [row for row in rows if not (row.get("key_camelot") or row.get("key_musical"))]

    def pending_or_missing(capability_name: str) -> str:
        return "coming_soon" if _tool_ready(capabilities[capability_name]) else "missing_tool"

    aubio_binary = _resolve_aubio_binary()
    bpm_status = "ready" if aubio_binary and missing_bpm else "disabled" if aubio_binary else "missing_tool"
    definitions = [
        {
            "type": "mixed_in_key_coverage",
            "label": "Review Mixed In Key coverage",
            "status": "ready" if total else "disabled",
            "required_tools": [],
            "required_source": "Existing Mixed In Key or compatible metadata",
            "candidate_count": total,
            "enabled": True,
            "default_enabled": True,
            "runner_implemented": False,
            "write_behavior": "preview_read_only; explicit local-index import in Settings",
            "safety": _SAFETY,
            "message": "Preview compatible tags first. Importing missing values remains a separate explicit DB-only step.",
        },
        {
            "type": "bpm_analysis",
            "label": "Analyze missing BPM",
            "status": bpm_status,
            "required_tools": ["aubio"],
            "candidate_count": len(missing_bpm),
            "enabled": bool(capabilities["bpm_analysis"].get("enabled")),
            "default_enabled": False,
            "runner_implemented": bool(aubio_binary),
            "write_behavior": "crateiq_db_only",
            "safety": _SAFETY,
            "message": (
                "Preview then explicitly confirm a small aubio-only DB update. Existing and MIK-compatible BPM are never overwritten."
                if aubio_binary else "aubio is required for this optional runner. Import remains available without it."
            ),
        },
        {
            "type": "key_analysis",
            "label": "Analyze missing key/Camelot",
            "status": pending_or_missing("key_analysis"),
            "required_tools": ["keyfinder-cli"],
            "candidate_count": len(missing_key),
            "enabled": bool(capabilities["key_analysis"].get("enabled")),
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "runner_pending; no writes",
            "safety": _SAFETY,
            "message": "Only tracks with no local key or Camelot value are candidates. The DB-only runner is not implemented yet.",
        },
        {
            "type": "beets_enrichment",
            "label": "Beets enrichment",
            "status": pending_or_missing("beets_enrichment"),
            "required_tools": ["beet"],
            "candidate_count": total,
            "enabled": False,
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "review_only; no tag writes",
            "safety": _SAFETY,
            "message": "A review-first, DB-only enrichment runner is still pending.",
        },
        {
            "type": "duplicate_detection",
            "label": "Duplicate detection",
            "status": pending_or_missing("duplicate_detection"),
            "required_tools": ["rmlint"],
            "candidate_count": total,
            "enabled": False,
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "preview_only; no delete or move actions",
            "safety": _SAFETY,
            "message": "Candidate preview is safe; rmlint execution and any file action are not exposed here.",
        },
        {
            "type": "audio_quality_probe",
            "label": "Audio quality probe",
            "status": pending_or_missing("audio_quality_probe"),
            "required_tools": ["ffprobe", "ffmpeg"],
            "candidate_count": total,
            "enabled": bool(capabilities["audio_quality_probe"].get("enabled")),
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "runner_pending; no transcode or writes",
            "safety": _SAFETY,
            "message": "Future probes will inspect files only. No transcode or media modification is available.",
        },
    ]
    return definitions, rows


def list_jobs() -> dict[str, Any]:
    definitions, _ = _definitions()
    return {"jobs": definitions}


def preview(job_type: str) -> dict[str, Any]:
    definitions, rows = _definitions()
    job = next((item for item in definitions if item["type"] == job_type), None)
    if job is None:
        raise ValueError("Unknown analysis job type.")

    if job_type == "mixed_in_key_coverage":
        result = mik_metadata_service.preview()
        samples = [
            {
                "track_id": item["track_id"], "filename": item["filename"],
                "bpm": item.get("bpm"), "key_camelot": item.get("key_camelot"),
                "key_musical": item.get("key_musical"),
            }
            for item in result["samples"]
        ]
        return {
            "job": job, "total_tracks": result["summary"]["total_tracks"],
            "candidate_count": result["summary"]["total_tracks"], "samples": samples,
            "warnings": result["warnings"], "expected_write_behavior": job["write_behavior"],
            "runner_implemented": False,
        }

    if job_type == "bpm_analysis":
        candidates = [row for row in rows if row.get("bpm") is None]
    elif job_type == "key_analysis":
        candidates = [row for row in rows if not (row.get("key_camelot") or row.get("key_musical"))]
    else:
        candidates = rows
    warnings = []
    if job["status"] == "missing_tool":
        warnings.append(f"Required tool unavailable: {', '.join(job['required_tools'])}.")
    if job_type == "bpm_analysis" and job["runner_implemented"]:
        warnings.append("Preview is read-only. An explicit confirmed run invokes aubio only and writes BPM/provenance to CrateIQ's local index.")
    else:
        warnings.append("This preview is read-only. The runner is not implemented and no files, tags, or local track fields are changed.")
    return {
        "job": job, "total_tracks": len(rows), "candidate_count": len(candidates),
        "samples": [_as_candidate(row) for row in candidates[:_SAMPLE_LIMIT]],
        "warnings": warnings, "expected_write_behavior": job["write_behavior"],
        "runner_implemented": job["runner_implemented"],
    }


def run(job_type: str, *, confirm: bool = False, limit: int = 10) -> dict[str, Any]:
    if job_type not in _JOB_TYPES:
        raise ValueError("Unknown analysis job type.")
    if job_type == "mixed_in_key_coverage":
        raise RuntimeError("MIK coverage is preview-only here. Use Settings to explicitly import missing compatible metadata into the local index.")
    if job_type == "bpm_analysis":
        if not confirm:
            raise ValueError("BPM analysis requires confirm=true after previewing candidates.")
        return _run_bpm_analysis(limit)
    raise RuntimeError("This analysis runner is not implemented yet. Preview candidates and configure the required tool; no job was started.")


def _run_bpm_analysis(limit: int) -> dict[str, Any]:
    binary = _resolve_aubio_binary()
    if not binary:
        raise RuntimeError("aubio is not available. Configure AUBIO_BIN or install aubio before running BPM analysis.")
    root = selected_library_root()
    db_path = assert_path_under_root(library_db_path(root), root)
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized.")

    analyzed = updated = skipped = failed = 0
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as conn:
        _ensure_bpm_columns(conn)
        candidates = _bpm_candidates(conn, root, limit)
        for row in candidates:
            try:
                path = assert_path_under_root(row["filepath"], root)
            except ValueError:
                skipped += 1
                warnings.append(f"Skipped {row['filename']}: path is outside the selected library root.")
                continue
            if not path.is_file():
                skipped += 1
                warnings.append(f"Skipped {row['filename']}: file is missing or unreadable.")
                continue
            try:
                completed = subprocess.run(
                    [binary, "tempo", str(path)], capture_output=True, text=True,
                    timeout=_BPM_TIMEOUT_SECONDS, check=False,
                )
            except subprocess.TimeoutExpired:
                failed += 1
                warnings.append(f"aubio timed out for {row['filename']}.")
                continue
            except OSError:
                failed += 1
                warnings.append(f"aubio could not read {row['filename']}.")
                continue
            analyzed += 1
            bpm = _parse_aubio_bpm(completed.stdout)
            if completed.returncode != 0 or bpm is None:
                failed += 1
                warnings.append(f"aubio returned no usable BPM for {row['filename']}.")
                continue
            # The WHERE condition makes the write race-safe and reinforces the
            # missing-data-only rule if another explicit workflow wrote first.
            changed = conn.execute(
                """
                UPDATE tracks
                SET bpm = ?, bpm_source = 'aubio', bpm_trusted = 0, bpm_analyzed_at = ?
                WHERE id = ? AND bpm IS NULL
                """,
                (bpm, datetime.now(timezone.utc).isoformat(), row["id"]),
            ).rowcount
            if not changed:
                skipped += 1
                continue
            updated += 1
            result = _as_candidate(dict(row))
            result["bpm"] = bpm
            results.append(result)
        remaining = conn.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NULL").fetchone()[0]
    return {
        "job_type": "bpm_analysis", "analyzed": analyzed, "updated": updated,
        "skipped": skipped, "failed": failed, "remaining_missing_bpm": remaining,
        "warnings": warnings, "results": results,
    }


def history() -> dict[str, Any]:
    return {
        "history": [],
        "message": "Analysis job history will appear when a safe DB-only runner is implemented. Candidate previews are intentionally not persisted.",
    }
