"""Safe, read-only catalog for optional analysis and enrichment jobs.

This service deliberately does not invoke external tools or create pipeline
jobs. It provides candidate previews so the Jobs page can truthfully expose
which advanced workflows are ready to configure versus implemented.
"""
from __future__ import annotations

import logging
import os
import json
import re
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.analyzer import CAMELOT_MAP, CAMELOT_TO_MUSICAL, _RE_CAMELOT

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import analysis_operations_service, mik_metadata_service, settings_service

log = logging.getLogger(__name__)

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
_FFMPEG_DECODE_TIMEOUT_SECONDS = 60
_DIAGNOSTIC_MAX_LEN = 500
_BPM_MIGRATION_COLUMNS = {
    "bpm_source": "TEXT",
    "bpm_trusted": "INTEGER NOT NULL DEFAULT 0",
    "bpm_analyzed_at": "TEXT",
}
_KEY_TIMEOUT_SECONDS = 20
_KEY_MIGRATION_COLUMNS = {
    "key_source": "TEXT",
    "key_trusted": "INTEGER NOT NULL DEFAULT 0",
    "key_analyzed_at": "TEXT",
}
_RMLINT_TIMEOUT_SECONDS = 30
_RMLINT_MAX_FILES = 100
_FFPROBE_TIMEOUT_SECONDS = 15
_FFPROBE_MAX_FILES = 10


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
        filesize_select = "filesize_bytes" if "filesize_bytes" in columns else "NULL AS filesize_bytes"
        duration_select = "duration_sec" if "duration_sec" in columns else "NULL AS duration_sec"
        return [dict(row) for row in conn.execute(
            "SELECT id, filepath, filename, artist, title, genre, bpm, key_musical, key_camelot, "
            f"{filesize_select}, {duration_select} FROM tracks ORDER BY id"
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


def _resolve_keyfinder_binary() -> str | None:
    override = os.environ.get("KEYFINDER_BIN", "").strip()
    if override:
        resolved = shutil.which(override)
        return resolved if resolved and os.access(resolved, os.X_OK) else None
    resolved = shutil.which("keyfinder-cli")
    return resolved if resolved and os.access(resolved, os.X_OK) else None


def _resolve_rmlint_binary() -> str | None:
    """Resolve only the configured rmlint CLI for the preview-only detector."""
    override = os.environ.get("RMLINT_BIN", "").strip()
    if override:
        resolved = shutil.which(override)
        return resolved if resolved and os.access(resolved, os.X_OK) else None
    resolved = shutil.which("rmlint")
    return resolved if resolved and os.access(resolved, os.X_OK) else None


def _resolve_ffmpeg_binary() -> str | None:
    """Resolve only ffmpeg, used solely as a BPM-analysis decode fallback."""
    override = os.environ.get("FFMPEG_BIN", "").strip()
    if override:
        resolved = shutil.which(override)
        return resolved if resolved and os.access(resolved, os.X_OK) else None
    resolved = shutil.which("ffmpeg")
    return resolved if resolved and os.access(resolved, os.X_OK) else None


def _resolve_ffprobe_binary() -> str | None:
    """Resolve only ffprobe for the bounded, read-only quality preview."""
    override = os.environ.get("FFPROBE_BIN", "").strip()
    if override:
        resolved = shutil.which(override)
        return resolved if resolved and os.access(resolved, os.X_OK) else None
    resolved = shutil.which("ffprobe")
    return resolved if resolved and os.access(resolved, os.X_OK) else None


def _parse_keyfinder_output(output: str) -> tuple[str | None, str | None]:
    """Accept only an exact known musical key or Camelot keyfinder result."""
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        value = line.rsplit(":", 1)[-1].strip()
        if _RE_CAMELOT.match(value):
            camelot = value.upper()
            return CAMELOT_TO_MUSICAL.get(camelot), camelot
        camelot = CAMELOT_MAP.get(value)
        if camelot:
            return value, camelot
    return None, None


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


def _sanitize_diagnostic(text: str) -> str:
    """Condense tool stderr for internal logs only -- never returned via the API.

    Collapses consecutive duplicate lines (aubio/ffmpeg often repeat the same
    decode warning per frame) and caps total length so a pathological file
    cannot flood the log.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return " | ".join(deduped)[:_DIAGNOSTIC_MAX_LEN]


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _parse_ffprobe_payload(payload: dict[str, Any], fallback_size: Any = None) -> dict[str, Any]:
    """Extract a deliberately small, neutral subset of ffprobe's JSON output."""
    format_info = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    audio_stream = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"),
        None,
    )
    if audio_stream is None:
        return {
            "status": "unsupported_format", "container": format_info.get("format_name"),
            "codec": None, "duration_sec": _optional_float(format_info.get("duration")),
            "bitrate_kbps": None, "sample_rate_hz": None, "channels": None,
            "file_size_bytes": _optional_int(format_info.get("size")) or _optional_int(fallback_size),
        }
    bitrate = _optional_int(format_info.get("bit_rate")) or _optional_int(audio_stream.get("bit_rate"))
    return {
        "status": "probe_ok" if bitrate is not None else "missing_bitrate",
        "container": format_info.get("format_name"),
        "codec": audio_stream.get("codec_name"),
        "duration_sec": _optional_float(format_info.get("duration")) or _optional_float(audio_stream.get("duration")),
        "bitrate_kbps": round(bitrate / 1000) if bitrate is not None else None,
        "sample_rate_hz": _optional_int(audio_stream.get("sample_rate")),
        "channels": _optional_int(audio_stream.get("channels")),
        "file_size_bytes": _optional_int(format_info.get("size")) or _optional_int(fallback_size),
    }


def _ensure_bpm_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    for name, definition in _BPM_MIGRATION_COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {definition}")


def _ensure_key_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    for name, definition in _KEY_MIGRATION_COLUMNS.items():
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


def _key_candidates(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT id, filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
               key_source, key_trusted
        FROM tracks
        WHERE key_musical IS NULL AND key_camelot IS NULL
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
        "missing_fields": row.get("missing_fields", []),
    }


_COPY_MARKER_RE = re.compile(r"(?:\(\d+\)|(?:^|[ _-])copy(?:[ _-]|$)|duplicate)", re.IGNORECASE)


def _infer_format(filename: str) -> str | None:
    """Return a lowercase extension label derived only from the known filename."""
    suffix = Path(filename).suffix.lstrip(".").lower()
    return suffix or None


def _missing_metadata_fields(row: dict[str, Any]) -> list[str]:
    """List which safe, already-indexed metadata fields this track is missing."""
    fields: list[str] = []
    if not row.get("artist"):
        fields.append("artist")
    if not row.get("title"):
        fields.append("title")
    if not row.get("genre"):
        fields.append("genre")
    if row.get("bpm") is None:
        fields.append("bpm")
    if not (row.get("key_camelot") or row.get("key_musical")):
        fields.append("key")
    return fields


def _looks_like_copy_filename(filename: str) -> bool:
    """Detect common OS/file-manager copy suffixes such as "(1)" or "copy"."""
    return bool(_COPY_MARKER_RE.search(Path(filename).stem))


def _recommend_keeper(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Suggest a keeper only when filename evidence unambiguously picks one item.

    This never authorizes removing the other items; it is advisory only and
    is kept separate from any human review decision.
    """
    non_copy = [item for item in items if not item["copy_marker"]]
    if len(non_copy) == 1:
        chosen = non_copy[0]
        return {
            "track_id": chosen["track_id"],
            "reason_code": "canonical_filename_no_copy_marker",
            "evidence": [f"\"{chosen['filename']}\" has no copy-style filename marker; the other item(s) in this group do."],
        }
    return {
        "track_id": None,
        "reason_code": "insufficient_evidence",
        "evidence": ["Filename copy markers do not unambiguously identify a single canonical file in this group."],
    }


def _tool_ready(capability: dict[str, Any]) -> bool:
    return bool(capability.get("available"))


def _duplicate_size_estimate(rows: list[dict[str, Any]]) -> int:
    """Return a cheap, conservative same-size estimate before rmlint hashes files."""
    counts: dict[int, int] = {}
    for row in rows:
        size = row.get("filesize_bytes")
        if isinstance(size, int) and size > 0:
            counts[size] = counts.get(size, 0) + 1
    return sum(count for count in counts.values() if count > 1)


def _definitions() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _track_rows()
    capabilities = settings_service.get_capabilities()["analysis"]
    total = len(rows)
    missing_bpm = [row for row in rows if row.get("bpm") is None]
    missing_key = [row for row in rows if not (row.get("key_camelot") or row.get("key_musical"))]
    enrichment_candidates = []
    for row in rows:
        missing_fields = [field for field in ("artist", "title", "genre") if not row.get(field)]
        if missing_fields:
            enrichment_candidates.append({**row, "missing_fields": missing_fields})

    aubio_binary = _resolve_aubio_binary()
    keyfinder_binary = _resolve_keyfinder_binary()
    rmlint_binary = _resolve_rmlint_binary()
    ffprobe_binary = _resolve_ffprobe_binary()
    rmlint_ready = bool(rmlint_binary and _tool_ready(capabilities["duplicate_detection"]))
    ffprobe_ready = bool(ffprobe_binary and _tool_ready(capabilities["audio_quality_probe"]))
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
            "status": "ready" if keyfinder_binary and missing_key else "disabled" if keyfinder_binary else "missing_tool",
            "required_tools": ["keyfinder-cli"],
            "candidate_count": len(missing_key),
            "enabled": bool(capabilities["key_analysis"].get("enabled")),
            "default_enabled": False,
            "runner_implemented": bool(keyfinder_binary),
            "write_behavior": "crateiq_db_only",
            "safety": _SAFETY,
            "message": "Preview then explicitly confirm a small keyfinder-cli-only DB update. Existing and MIK-compatible keys are never overwritten." if keyfinder_binary else "keyfinder-cli is required for this optional runner. Import remains available without it.",
        },
        {
            "type": "beets_enrichment",
            "label": "Beets enrichment",
            "status": "ready" if _tool_ready(capabilities["beets_enrichment"]) and enrichment_candidates else "disabled" if _tool_ready(capabilities["beets_enrichment"]) else "missing_tool",
            "required_tools": ["beet"],
            "candidate_count": len(enrichment_candidates),
            "enabled": False,
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "review_required; DB-only accepted enrichment",
            "safety": ["preview_first", "no_tag_writes", "no_file_moves", "preserve_trusted_values"],
            "message": "Preview incomplete non-critical metadata from the local index. Beets execution and automatic apply are intentionally deferred.",
        },
        {
            "type": "duplicate_detection",
            "label": "Duplicate detection",
            "status": "ready" if rmlint_ready and total else "disabled" if rmlint_ready else "missing_tool",
            "required_tools": ["rmlint"],
            "candidate_count": _duplicate_size_estimate(rows),
            "enabled": False,
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "preview_only; no file or database writes",
            "safety": ["preview_only", "no_delete", "no_move", "no_rename", "no_quarantine", "no_tag_writes", "no_file_writes"],
            "message": (
                "Preview up to 100 validated imported paths with rmlint. The count is a same-size estimate until a read-only scan runs."
                if rmlint_ready else "rmlint is required for this optional, preview-only duplicate detector. Import remains available without it."
            ),
        },
        {
            "type": "audio_quality_probe",
            "label": "Audio quality probe",
            "status": "ready" if ffprobe_ready and total else "disabled" if ffprobe_ready else "missing_tool",
            "required_tools": ["ffprobe"],
            "candidate_count": min(total, _FFPROBE_MAX_FILES),
            "enabled": bool(capabilities["audio_quality_probe"].get("enabled")),
            "default_enabled": False,
            "runner_implemented": False,
            "write_behavior": "preview_only; no transcode, file, tag, or database writes",
            "safety": ["probe_only", "no_transcode", "no_tag_writes", "no_file_writes"],
            "message": (
                f"Preview up to {_FFPROBE_MAX_FILES} validated imported tracks with ffprobe JSON. No files are changed."
                if ffprobe_ready else "ffprobe is required for this optional, preview-only audio metadata check. Import remains available without it."
            ),
        },
    ]
    return definitions, rows


def list_jobs() -> dict[str, Any]:
    definitions, _ = _definitions()
    return {"jobs": definitions}


def beets_enrichment_candidates() -> list[dict[str, Any]]:
    """Return every local-index metadata candidate without invoking beet."""
    candidates: list[dict[str, Any]] = []
    for row in _track_rows():
        missing_fields = [field for field in ("artist", "title", "genre") if not row.get(field)]
        if missing_fields:
            candidates.append({
                **row, "track_id": row["id"], "relative_path": _as_candidate(row)["relative_path"],
                "missing_fields": missing_fields,
            })
    return candidates


def _preview_duplicate_detection(job: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Run rmlint's JSON formatter only; never create/execute an action script."""
    warnings: list[str] = []
    root = selected_library_root()
    binary = _resolve_rmlint_binary() if job["status"] != "missing_tool" else None
    track_by_path: dict[str, dict[str, Any]] = {}
    paths: list[Path] = []
    for row in rows:
        try:
            path = assert_path_under_root(row["filepath"], root)
        except (KeyError, TypeError, ValueError):
            warnings.append(f"Skipped {row.get('filename', 'a track')}: its path is outside the selected library root.")
            continue
        if not path.is_file():
            warnings.append(f"Skipped {row.get('filename', 'a track')}: its file is missing or unreadable.")
            continue
        track_by_path[str(path)] = row
        paths.append(path)
    paths = paths[:_RMLINT_MAX_FILES]
    if len(track_by_path) > len(paths):
        warnings.append(f"Preview is limited to {_RMLINT_MAX_FILES} readable imported tracks; rescope the library before scanning more.")
    if not binary:
        warnings.append("Required tool unavailable: rmlint. No duplicate scan was started.")
        return {
            "job": job, "total_tracks": len(rows), "candidate_count": 0,
            "samples": [], "warnings": warnings,
            "expected_write_behavior": job["write_behavior"], "runner_implemented": False,
            "preview_only": True,
            "summary": {"total_tracks_checked": 0, "duplicate_groups": 0, "duplicate_candidates": 0},
            "groups": [],
            "next_step": "Open Duplicate Review to record DB-only keep, ignore, or review-later decisions. No file action exists.",
        }
    if not paths:
        warnings.append("No readable imported tracks are available for a duplicate preview.")
        return {
            "job": job, "total_tracks": len(rows), "candidate_count": 0,
            "samples": [], "warnings": warnings,
            "expected_write_behavior": job["write_behavior"], "runner_implemented": False,
            "preview_only": True,
            "summary": {"total_tracks_checked": 0, "duplicate_groups": 0, "duplicate_candidates": 0},
            "groups": [],
            "next_step": "Open Duplicate Review to record DB-only keep, ignore, or review-later decisions. No file action exists.",
        }
    try:
        completed = subprocess.run(
            [binary, "-T", "df", "-o", "json", "--no-followlinks", "--no-with-color", "--no-crossdev", *map(str, paths)],
            capture_output=True,
            text=True,
            timeout=_RMLINT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        warnings.append("rmlint preview timed out. No files, tags, or database decisions were changed.")
        completed = None
    except OSError:
        warnings.append("rmlint could not start. Check its local setup; no files, tags, or database decisions were changed.")
        completed = None
    groups: list[dict[str, Any]] = []
    if completed is not None:
        if completed.returncode != 0:
            warnings.append("rmlint did not complete the preview. No files, tags, or database decisions were changed.")
        else:
            try:
                reports = json.loads(completed.stdout)
            except json.JSONDecodeError:
                warnings.append("rmlint returned an unreadable preview. No files, tags, or database decisions were changed.")
            else:
                by_checksum: dict[str, list[dict[str, Any]]] = {}
                for report in reports if isinstance(reports, list) else []:
                    if not isinstance(report, dict) or report.get("type") != "duplicate_file":
                        continue
                    row = track_by_path.get(str(report.get("path", "")))
                    checksum = report.get("checksum")
                    if row is not None and isinstance(checksum, str) and checksum:
                        by_checksum.setdefault(checksum, []).append(row)
                for index, (checksum, group_rows) in enumerate(by_checksum.items(), start=1):
                    if len(group_rows) < 2:
                        continue
                    items = []
                    for row in sorted(group_rows, key=lambda item: (str(item.get("filepath", "")).casefold(), item["id"])):
                        item = _as_candidate(row)
                        filename = item["filename"]
                        items.append({
                            "track_id": item["track_id"], "filename": filename,
                            "title": item["title"], "artist": item["artist"],
                            "relative_path": item["relative_path"], "size_bytes": row.get("filesize_bytes"),
                            "genre": item["genre"], "bpm": item["bpm"],
                            "key_camelot": item["key_camelot"], "key_musical": item["key_musical"],
                            "duration_sec": row.get("duration_sec"),
                            "format": _infer_format(filename),
                            "missing_metadata": _missing_metadata_fields(row),
                            "copy_marker": _looks_like_copy_filename(filename),
                        })
                    groups.append({
                        "group_id": f"dup-{index}", "reason": "rmlint duplicate", "confidence": "high", "items": items,
                        "match_basis": "content_checksum",
                        "checksum_prefix": checksum[:12] if isinstance(checksum, str) else None,
                        "recommendation": _recommend_keeper(items),
                    })
    duplicate_candidates = sum(len(group["items"]) for group in groups)
    samples = [
        {
            "track_id": item["track_id"], "filename": item["filename"], "relative_path": item["relative_path"],
            "artist": item["artist"], "title": item["title"], "genre": None,
            "bpm": None, "key_camelot": None, "key_musical": None,
        }
        for group in groups for item in group["items"]
    ][:_SAMPLE_LIMIT]
    warnings.append("Preview only: rmlint JSON output is read and discarded. No delete, move, rename, quarantine, tag, file, or database action exists.")
    return {
        "job": job, "total_tracks": len(rows), "candidate_count": duplicate_candidates,
        "samples": samples, "warnings": warnings,
        "expected_write_behavior": job["write_behavior"], "runner_implemented": False,
        "preview_only": True,
        "summary": {
            "total_tracks_checked": len(paths), "duplicate_groups": len(groups),
            "duplicate_candidates": duplicate_candidates,
        },
        "groups": groups,
        "next_step": "Open Duplicate Review to record DB-only keep, ignore, or review-later decisions. No file action exists.",
    }


def _preview_audio_quality(job: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Probe a small, validated track sample with ffprobe JSON and discard output."""
    warnings: list[str] = []
    root = selected_library_root()
    binary = _resolve_ffprobe_binary() if job["status"] != "missing_tool" else None
    candidates = rows[:_FFPROBE_MAX_FILES]
    if len(rows) > len(candidates):
        warnings.append(f"Preview is limited to {_FFPROBE_MAX_FILES} indexed tracks; no broader scan was started.")
    if not binary:
        warnings.append("Required tool unavailable: ffprobe. No audio metadata probe was started.")
        return {
            "job": job, "total_tracks": len(rows), "candidate_count": 0, "samples": [], "warnings": warnings,
            "expected_write_behavior": job["write_behavior"], "runner_implemented": False, "preview_only": True,
            "summary": {"total_tracks_checked": 0, "probe_ok": 0, "needs_review": 0}, "quality_probes": [],
            "next_step": "Open Audio Quality Review to record DB-only findings. No transcode or remediation action exists.",
        }

    probes: list[dict[str, Any]] = []
    for row in candidates:
        base = _as_candidate(row)
        result = {
            "track_id": row["id"], "filename": row["filename"], "relative_path": base["relative_path"],
            "status": "unreadable", "container": None, "codec": None, "duration_sec": None,
            "bitrate_kbps": None, "sample_rate_hz": None, "channels": None,
            "file_size_bytes": _optional_int(row.get("filesize_bytes")),
        }
        try:
            path = assert_path_under_root(row["filepath"], root)
        except (KeyError, TypeError, ValueError):
            warnings.append(f"Skipped {row.get('filename', 'a track')}: its path is outside the selected library root.")
            probes.append(result)
            continue
        if not path.is_file():
            warnings.append(f"Skipped {row['filename']}: its file is missing or unreadable.")
            probes.append(result)
            continue
        try:
            completed = subprocess.run(
                [binary, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT_SECONDS, check=False, shell=False,
            )
        except subprocess.TimeoutExpired:
            result["status"] = "probe_error"
            warnings.append(f"ffprobe timed out for {row['filename']}; no files were changed.")
            probes.append(result)
            continue
        except OSError:
            result["status"] = "probe_error"
            warnings.append(f"ffprobe could not read {row['filename']}; no files were changed.")
            probes.append(result)
            continue
        if completed.returncode != 0:
            result["status"] = "probe_error"
            warnings.append(f"ffprobe returned no usable metadata for {row['filename']}; no files were changed.")
            probes.append(result)
            continue
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            result["status"] = "probe_error"
            warnings.append(f"ffprobe returned unreadable metadata for {row['filename']}; no files were changed.")
            probes.append(result)
            continue
        result.update(_parse_ffprobe_payload(payload, row.get("filesize_bytes")))
        probes.append(result)

    probe_ok = sum(probe["status"] == "probe_ok" for probe in probes)
    warnings.append("Preview only: ffprobe metadata is read and discarded. No transcode, tag, file, or database action exists.")
    return {
        "job": job, "total_tracks": len(rows), "candidate_count": len(candidates),
        "samples": [_as_candidate(row) for row in candidates], "warnings": warnings,
        "expected_write_behavior": job["write_behavior"], "runner_implemented": False, "preview_only": True,
        "summary": {"total_tracks_checked": len(candidates), "probe_ok": probe_ok, "needs_review": len(candidates) - probe_ok},
        "quality_probes": probes,
        "next_step": "Open Audio Quality Review to record DB-only findings. No transcode or remediation action exists.",
    }


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

    if job_type == "duplicate_detection":
        return _preview_duplicate_detection(job, rows)

    if job_type == "audio_quality_probe":
        return _preview_audio_quality(job, rows)

    if job_type == "bpm_analysis":
        candidates = [row for row in rows if row.get("bpm") is None]
    elif job_type == "key_analysis":
        candidates = [row for row in rows if not (row.get("key_camelot") or row.get("key_musical"))]
    elif job_type == "beets_enrichment":
        candidates = beets_enrichment_candidates()
    else:
        candidates = rows
    warnings = []
    if job["status"] == "missing_tool":
        warnings.append(f"Required tool unavailable: {', '.join(job['required_tools'])}.")
    if job_type == "bpm_analysis" and job["runner_implemented"]:
        warnings.append("Preview is read-only. An explicit confirmed run invokes aubio only and writes BPM/provenance to CrateIQ's local index.")
    elif job_type == "key_analysis" and job["runner_implemented"]:
        warnings.append("Preview is read-only. An explicit confirmed run invokes keyfinder-cli only and writes key/Camelot provenance to CrateIQ's local index.")
    elif job_type == "beets_enrichment":
        warnings.append("Preview is DB-only and does not invoke beet. Suggestions/apply require a future selected-field review flow.")
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
    if job_type == "key_analysis":
        if not confirm:
            raise ValueError("Key/Camelot analysis requires confirm=true after previewing candidates.")
        return _run_key_analysis(limit)
    if job_type == "duplicate_detection":
        raise RuntimeError("Duplicate detection is preview-only. Resolution actions are not implemented; no files, tags, or database decisions were changed.")
    if job_type == "audio_quality_probe":
        raise RuntimeError("Audio quality probing is preview-only. No files, tags, or database records were changed.")
    raise RuntimeError("This analysis runner is not implemented yet. Preview candidates and configure the required tool; no job was started.")


def _attempt_ffmpeg_fallback(aubio_binary: str, source_path: Path, filename: str) -> tuple[float | None, str]:
    """Recover a BPM from malformed-but-decodable audio via an FFmpeg decode.

    Direct aubio decoding already failed for `source_path` when this is
    called. Decodes to a secure temporary WAV outside the managed workspace
    (never derived from the source's own directory), retries aubio against
    that WAV, and guarantees the temporary file/directory is removed via the
    context manager regardless of outcome. The source file itself is never
    opened for writing.

    Returns (bpm, message): bpm is None on any failure; message is always a
    concise, user-facing sentence -- never raw stderr or a stack trace.
    """
    ffmpeg_binary = _resolve_ffmpeg_binary()
    if not ffmpeg_binary:
        return None, (
            f"{filename}: direct aubio decode failed and FFmpeg is not available "
            "for recovery. BPM was not changed."
        )

    with tempfile.TemporaryDirectory(prefix="crateiq-bpm-") as tmp_dir:
        wav_path = Path(tmp_dir) / "audio.wav"
        try:
            ffmpeg_completed = subprocess.run(
                [
                    ffmpeg_binary, "-y", "-v", "warning", "-i", str(source_path),
                    "-vn", "-ac", "2", "-ar", "44100", str(wav_path),
                ],
                capture_output=True, text=True, timeout=_FFMPEG_DECODE_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg BPM-recovery decode timed out for %s", filename)
            return None, (
                f"{filename}: direct aubio decode failed and FFmpeg timed out while "
                "attempting recovery. BPM was not changed."
            )
        except OSError:
            log.warning("ffmpeg BPM-recovery decode could not start for %s", filename)
            return None, (
                f"{filename}: direct aubio decode failed and FFmpeg could not start "
                "for recovery. BPM was not changed."
            )

        if ffmpeg_completed.stderr:
            log.info(
                "ffmpeg BPM-recovery diagnostics for %s: %s",
                filename, _sanitize_diagnostic(ffmpeg_completed.stderr),
            )

        if ffmpeg_completed.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size == 0:
            return None, (
                f"{filename}: direct aubio decode failed and FFmpeg could not recover "
                "a decodable audio stream. BPM was not changed."
            )

        try:
            fallback_completed = subprocess.run(
                [aubio_binary, "tempo", str(wav_path)],
                capture_output=True, text=True, timeout=_BPM_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            return None, (
                f"{filename}: FFmpeg recovered malformed audio, but aubio timed out "
                "analyzing it. BPM was not changed."
            )
        except OSError:
            return None, (
                f"{filename}: FFmpeg recovered malformed audio, but aubio could not "
                "start to analyze it. BPM was not changed."
            )

        bpm = _parse_aubio_bpm(fallback_completed.stdout)
        if fallback_completed.returncode != 0 or bpm is None:
            return None, (
                f"{filename}: FFmpeg recovered malformed audio, but aubio could not "
                "determine a usable BPM. BPM was not changed."
            )

        return bpm, (
            f"{filename}: direct aubio decode failed; FFmpeg recovered enough audio "
            f"and BPM was measured at {bpm:.2f}. Original file was not modified."
        )


def _run_bpm_analysis(limit: int) -> dict[str, Any]:
    binary = _resolve_aubio_binary()
    if not binary:
        raise RuntimeError("aubio is not available. Configure AUBIO_BIN or install aubio before running BPM analysis.")
    root = selected_library_root()
    db_path = assert_path_under_root(library_db_path(root), root)
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized.")

    analyzed = updated = skipped = failed = recovered = 0
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    operation_id: str | None = None
    cancelled = False
    remaining: int | None = None
    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_bpm_columns(conn)
            eligible_total = conn.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NULL").fetchone()[0]
            candidates = _bpm_candidates(conn, root, limit)
            operation_id = analysis_operations_service.start_operation(
                "bpm_analysis", scope_limit=limit, eligible_total=eligible_total,
                considered=len(candidates),
            )["id"]
            for row in candidates:
                if analysis_operations_service.is_cancel_requested(operation_id):
                    cancelled = True
                    break
                analysis_operations_service.update_progress(
                    operation_id, processed=analyzed, succeeded=updated, skipped=skipped,
                    failed=failed, recovered=recovered,
                )
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

                # "Analyzed" counts tracks attempted, not subprocess invocations --
                # a fallback-recovered track still ran up to three tool calls
                # (direct aubio, ffmpeg decode, fallback aubio) but is one attempt.
                analyzed += 1
                try:
                    completed = subprocess.run(
                        [binary, "tempo", str(path)], capture_output=True, text=True,
                        timeout=_BPM_TIMEOUT_SECONDS, check=False,
                    )
                    direct_bpm = _parse_aubio_bpm(completed.stdout) if completed.returncode == 0 else None
                    direct_ok = completed.returncode == 0 and direct_bpm is not None
                except subprocess.TimeoutExpired:
                    direct_ok = False
                except OSError:
                    direct_ok = False

                if direct_ok:
                    bpm = direct_bpm
                    bpm_source = "aubio"
                    fallback_warning = None
                else:
                    bpm, fallback_warning = _attempt_ffmpeg_fallback(binary, path, row["filename"])
                    if bpm is None:
                        failed += 1
                        warnings.append(fallback_warning)
                        continue
                    bpm_source = "aubio_ffmpeg_decode"

                # The WHERE condition makes the write race-safe and reinforces the
                # missing-data-only rule if another explicit workflow wrote first.
                changed = conn.execute(
                    """
                    UPDATE tracks
                    SET bpm = ?, bpm_source = ?, bpm_trusted = 0, bpm_analyzed_at = ?
                    WHERE id = ? AND bpm IS NULL
                    """,
                    (bpm, bpm_source, datetime.now(timezone.utc).isoformat(), row["id"]),
                ).rowcount
                if not changed:
                    skipped += 1
                    continue
                updated += 1
                if bpm_source == "aubio_ffmpeg_decode":
                    recovered += 1
                    warnings.append(fallback_warning)
                result = _as_candidate(dict(row))
                result["bpm"] = bpm
                results.append(result)
            remaining = conn.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NULL").fetchone()[0]
    except Exception as exc:
        if operation_id:
            analysis_operations_service.finish_operation(
                operation_id, status="failed", processed=analyzed, succeeded=updated,
                skipped=skipped, failed=failed, remaining_missing=None, warnings=warnings,
                error_reason=str(exc), recovered=recovered,
            )
        raise
    if operation_id:
        analysis_operations_service.finish_operation(
            operation_id, status="cancelled" if cancelled else "completed",
            processed=analyzed, succeeded=updated, skipped=skipped, failed=failed,
            remaining_missing=remaining, warnings=warnings,
            error_reason="user_cancelled" if cancelled else None, recovered=recovered,
        )
    return {
        "job_type": "bpm_analysis", "analyzed": analyzed, "updated": updated,
        "skipped": skipped, "failed": failed, "recovered": recovered,
        "remaining_missing_bpm": remaining,
        "warnings": warnings, "results": results, "operation_id": operation_id,
        "cancelled": cancelled,
        "outcome": analysis_operations_service.derive_outcome(
            "cancelled" if cancelled else "completed", failed, recovered,
        ),
    }


def _run_key_analysis(limit: int) -> dict[str, Any]:
    binary = _resolve_keyfinder_binary()
    if not binary:
        raise RuntimeError("keyfinder-cli is not available. Configure KEYFINDER_BIN or install keyfinder-cli before running key analysis.")
    root = selected_library_root()
    db_path = assert_path_under_root(library_db_path(root), root)
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized.")
    analyzed = updated = skipped = failed = 0
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    operation_id: str | None = None
    cancelled = False
    remaining: int | None = None
    try:
        with sqlite3.connect(db_path) as conn:
            _ensure_key_columns(conn)
            eligible_total = conn.execute(
                "SELECT COUNT(*) FROM tracks WHERE key_musical IS NULL AND key_camelot IS NULL"
            ).fetchone()[0]
            candidates = _key_candidates(conn, limit)
            operation_id = analysis_operations_service.start_operation(
                "key_analysis", scope_limit=limit, eligible_total=eligible_total,
                considered=len(candidates),
            )["id"]
            for row in candidates:
                if analysis_operations_service.is_cancel_requested(operation_id):
                    cancelled = True
                    break
                analysis_operations_service.update_progress(
                    operation_id, processed=analyzed, succeeded=updated, skipped=skipped, failed=failed,
                )
                try:
                    path = assert_path_under_root(row["filepath"], root)
                except ValueError:
                    skipped += 1; warnings.append(f"Skipped {row['filename']}: path is outside the selected library root."); continue
                if not path.is_file():
                    skipped += 1; warnings.append(f"Skipped {row['filename']}: file is missing or unreadable."); continue
                try:
                    completed = subprocess.run([binary, str(path)], capture_output=True, text=True, timeout=_KEY_TIMEOUT_SECONDS, check=False)
                except subprocess.TimeoutExpired:
                    failed += 1; warnings.append(f"keyfinder-cli timed out for {row['filename']}."); continue
                except OSError:
                    failed += 1; warnings.append(f"keyfinder-cli could not read {row['filename']}."); continue
                analyzed += 1
                musical, camelot = _parse_keyfinder_output(completed.stdout)
                if completed.returncode != 0 or not (musical or camelot):
                    failed += 1; warnings.append(f"keyfinder-cli returned no recognized key for {row['filename']}."); continue
                changed = conn.execute(
                    """UPDATE tracks SET key_musical = ?, key_camelot = ?, key_source = 'keyfinder-cli', key_trusted = 0, key_analyzed_at = ?
                       WHERE id = ? AND key_musical IS NULL AND key_camelot IS NULL""",
                    (musical, camelot, datetime.now(timezone.utc).isoformat(), row["id"]),
                ).rowcount
                if not changed:
                    skipped += 1; continue
                updated += 1
                result = _as_candidate(dict(row)); result["key_musical"] = musical; result["key_camelot"] = camelot; results.append(result)
            remaining = conn.execute("SELECT COUNT(*) FROM tracks WHERE key_musical IS NULL AND key_camelot IS NULL").fetchone()[0]
    except Exception as exc:
        if operation_id:
            analysis_operations_service.finish_operation(
                operation_id, status="failed", processed=analyzed, succeeded=updated,
                skipped=skipped, failed=failed, remaining_missing=None, warnings=warnings,
                error_reason=str(exc),
            )
        raise
    if operation_id:
        analysis_operations_service.finish_operation(
            operation_id, status="cancelled" if cancelled else "completed",
            processed=analyzed, succeeded=updated, skipped=skipped, failed=failed,
            remaining_missing=remaining, warnings=warnings,
            error_reason="user_cancelled" if cancelled else None,
        )
    return {
        "job_type": "key_analysis", "analyzed": analyzed, "updated": updated, "skipped": skipped,
        "failed": failed, "remaining_missing_key": remaining, "warnings": warnings,
        "results": results, "operation_id": operation_id, "cancelled": cancelled,
    }


def history() -> dict[str, Any]:
    records = analysis_operations_service.list_recent()
    return {
        "history": records,
        "message": (
            "Recent explicit, confirmed BPM/key analysis runs. Candidate previews are intentionally not persisted."
            if records else
            "No confirmed analysis runs yet. Candidate previews are intentionally not persisted."
        ),
    }


def operation_detail(operation_id: str) -> dict[str, Any]:
    record = analysis_operations_service.get_operation(operation_id)
    if record is None:
        raise ValueError(f"Analysis operation {operation_id} not found.")
    return record


def cancel_operation(operation_id: str) -> dict[str, Any]:
    record = analysis_operations_service.request_cancel(operation_id)
    if record is None:
        raise ValueError(f"Analysis operation {operation_id} not found.")
    return record
