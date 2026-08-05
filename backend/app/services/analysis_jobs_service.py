"""Safe, read-only catalog for optional analysis and enrichment jobs.

This service deliberately does not invoke external tools or create pipeline
jobs. It provides candidate previews so the Jobs page can truthfully expose
which advanced workflows are ready to configure versus implemented.
"""
from __future__ import annotations

import sqlite3
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
            "SELECT id, filename, artist, title, genre, bpm, key_musical, key_camelot FROM tracks ORDER BY id"
        )]


def _as_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "track_id": row["id"],
        "filename": row["filename"],
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
            "status": pending_or_missing("bpm_analysis"),
            "required_tools": ["aubio"],
            "candidate_count": len(missing_bpm),
            "enabled": bool(capabilities["bpm_analysis"].get("enabled")),
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "runner_pending; no writes",
            "safety": _SAFETY,
            "message": "Only tracks with no local BPM are candidates. The DB-only runner is not implemented yet.",
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
    warnings.append("This preview is read-only. The runner is not implemented and no files, tags, or local track fields are changed.")
    return {
        "job": job, "total_tracks": len(rows), "candidate_count": len(candidates),
        "samples": [_as_candidate(row) for row in candidates[:_SAMPLE_LIMIT]],
        "warnings": warnings, "expected_write_behavior": job["write_behavior"],
        "runner_implemented": False,
    }


def run(job_type: str) -> None:
    if job_type not in _JOB_TYPES:
        raise ValueError("Unknown analysis job type.")
    if job_type == "mixed_in_key_coverage":
        raise RuntimeError("MIK coverage is preview-only here. Use Settings to explicitly import missing compatible metadata into the local index.")
    raise RuntimeError("This analysis runner is not implemented yet. Preview candidates and configure the required tool; no job was started.")


def history() -> dict[str, Any]:
    return {
        "history": [],
        "message": "Analysis job history will appear when a safe DB-only runner is implemented. Candidate previews are intentionally not persisted.",
    }
