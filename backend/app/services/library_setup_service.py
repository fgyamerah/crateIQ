"""Explicit, local-only library initialization and filename index import."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import settings_service
from ..core.library_root import assert_path_under_root
from ..core.preflight import redact_path

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg"}
_SKIP_DIRECTORIES = {".git", ".run", ".venv", "node_modules", "logs", "exports"}
_SAMPLE_LIMIT = 20

_TRACKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    artist TEXT,
    title TEXT,
    album TEXT,
    genre TEXT,
    bpm REAL,
    key_musical TEXT,
    key_camelot TEXT,
    duration_sec REAL,
    bitrate_kbps INTEGER,
    filesize_bytes INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    error_msg TEXT,
    processed_at TEXT,
    pipeline_ver TEXT,
    quality_tier TEXT,
    parse_confidence TEXT
);
CREATE INDEX IF NOT EXISTS idx_tracks_filepath ON tracks(filepath);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_lc ON tracks(LOWER(COALESCE(artist, '')));
CREATE INDEX IF NOT EXISTS idx_tracks_title_lc ON tracks(LOWER(COALESCE(title, '')));
CREATE INDEX IF NOT EXISTS idx_tracks_genre_lc ON tracks(LOWER(COALESCE(genre, '')));
CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm);
"""


def _target_root(library_root: str | None) -> Path:
    if library_root is not None:
        return settings_service._validated_library_root(library_root)
    pending = settings_service._pending_library_root()
    if pending is None:
        raise ValueError("Save and validate a pending configured library root before library setup.")
    return pending


def _db_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "processed.db", root)


def library_status(library_root: str | None = None) -> dict[str, Any]:
    root = _target_root(library_root)
    db_path = _db_path(root)
    return {
        "library_root": redact_path(root),
        "initialized": db_path.is_file(),
        "processed_db": redact_path(db_path),
    }


def initialize_library(library_root: str | None = None) -> dict[str, Any]:
    """Create only CrateIQ's local directories and empty index schema."""
    root = _target_root(library_root)
    logs_dir = assert_path_under_root(root / "logs", root)
    exports_dir = assert_path_under_root(root / "exports", root)
    logs_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    db_path = _db_path(root)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_TRACKS_SCHEMA)
    return {
        "library_root": redact_path(root),
        "initialized": True,
        "processed_db": redact_path(db_path),
        "message": "CrateIQ local index initialized. No files were scanned or changed.",
    }


def _scan(root: Path) -> dict[str, Any]:
    tracks: list[Path] = []
    unsupported: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []
    try:
        paths = root.rglob("*")
        for path in paths:
            try:
                if any(part in _SKIP_DIRECTORIES for part in path.relative_to(root).parts):
                    continue
                if not path.is_file():
                    continue
                relative = path.relative_to(root)
                if path.suffix.lower() in _AUDIO_EXTENSIONS:
                    tracks.append(path)
                elif len(unsupported) < _SAMPLE_LIMIT:
                    unsupported.append(str(relative))
            except (OSError, PermissionError, ValueError):
                if len(skipped) < _SAMPLE_LIMIT:
                    skipped.append(path.name)
    except (OSError, PermissionError) as exc:
        warnings.append(f"Some folders could not be read: {exc.__class__.__name__}.")
    tracks.sort(key=lambda path: str(path.relative_to(root)).lower())
    return {
        "library_root": redact_path(root),
        "track_count": len(tracks),
        "sample_tracks": [str(path.relative_to(root)) for path in tracks[:_SAMPLE_LIMIT]],
        "track_paths": tracks,
        "skipped_files": skipped,
        "unsupported_files": unsupported,
        "warnings": warnings,
    }


def scan_preview(library_root: str | None = None) -> dict[str, Any]:
    root = _target_root(library_root)
    if not _db_path(root).is_file():
        raise ValueError("Configured library is not initialized. Initialize it before scanning.")
    result = _scan(root)
    result.pop("track_paths")
    result["message"] = "Preview only. No music files or local index records were changed."
    return result


def _filename_metadata(path: Path) -> tuple[str | None, str | None, str]:
    stem = path.stem.strip()
    if " - " in stem:
        artist, title = (part.strip() or None for part in stem.split(" - ", 1))
        return artist, title, "MEDIUM"
    return None, stem or None, "LOW"


def import_previewed_library(library_root: str | None = None) -> dict[str, Any]:
    root = _target_root(library_root)
    db_path = _db_path(root)
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized. Initialize it before importing.")
    scan = _scan(root)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        for path in scan["track_paths"]:
            safe_path = assert_path_under_root(path, root)
            artist, title, confidence = _filename_metadata(safe_path)
            conn.execute(
                """
                INSERT INTO tracks (filepath, filename, artist, title, filesize_bytes, status, processed_at, pipeline_ver, parse_confidence)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, 'library-import-v1', ?)
                ON CONFLICT(filepath) DO UPDATE SET
                    filename = excluded.filename,
                    filesize_bytes = excluded.filesize_bytes
                """,
                (str(safe_path), safe_path.name, artist, title, safe_path.stat().st_size, now, confidence),
            )
    scan.pop("track_paths")
    scan["imported_count"] = scan.pop("track_count")
    scan["message"] = "Imported filenames into CrateIQ's local index only. Audio files and tags were not changed."
    return scan
