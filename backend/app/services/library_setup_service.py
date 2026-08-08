"""Explicit, local-only library initialization and filename index import."""
from __future__ import annotations

import sqlite3
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import settings_service
from ..core.library_root import assert_path_under_root, selected_library_root
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
    parse_confidence TEXT,
    bpm_source TEXT,
    bpm_trusted INTEGER NOT NULL DEFAULT 0,
    bpm_analyzed_at TEXT,
    key_source TEXT,
    key_trusted INTEGER NOT NULL DEFAULT 0,
    key_analyzed_at TEXT,
    cue_source TEXT,
    cue_count INTEGER NOT NULL DEFAULT 0,
    metadata_trusted INTEGER NOT NULL DEFAULT 0,
    metadata_imported_at TEXT,
    storage_zone TEXT NOT NULL DEFAULT 'LIBRARY'
        CHECK (storage_zone IN ('INBOX', 'LIBRARY', 'QUARANTINE'))
);
CREATE INDEX IF NOT EXISTS idx_tracks_filepath ON tracks(filepath);
CREATE INDEX IF NOT EXISTS idx_tracks_storage_zone ON tracks(storage_zone);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_lc ON tracks(LOWER(COALESCE(artist, '')));
CREATE INDEX IF NOT EXISTS idx_tracks_title_lc ON tracks(LOWER(COALESCE(title, '')));
CREATE INDEX IF NOT EXISTS idx_tracks_genre_lc ON tracks(LOWER(COALESCE(genre, '')));
CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm);
"""


def _target_root(library_root: str | None) -> Path:
    """
    Resolve the library root a mutating/import operation must act on.

    A pending root written by Settings (``.run/local/crateiq.env``) is only a
    staged change that takes effect on process restart (see
    ``scripts/crateiq-local-services.sh``); it must never be substituted here
    just because a caller omitted an explicit ``library_root``, or a
    same-process operation could silently scan/index a different library than
    the one actually selected for this running backend. Omitting the argument
    always means "use the canonical selected root for this process."
    """
    if library_root is not None:
        return settings_service._validated_library_root(library_root)
    return selected_library_root()


def _db_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "processed.db", root)


def ensure_storage_zone_column(root: Path) -> None:
    """
    Idempotently add the storage_zone column to an existing processed.db that
    predates the Cycle 9 managed workspace model. No-op if the DB does not
    exist yet or the column is already present. Existing rows backfill to
    'LIBRARY' via the column DEFAULT, matching current pipeline behavior
    exactly: a track imported before Cycle 9 already lived directly in the
    library, so its visibility must not change.
    """
    db_path = _db_path(root)
    if not db_path.is_file():
        return
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute(
                "ALTER TABLE tracks ADD COLUMN storage_zone TEXT NOT NULL DEFAULT 'LIBRARY'"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_storage_zone ON tracks(storage_zone)")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


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
    ensure_storage_zone_column(root)
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
    long_path_warnings: list[str] = []
    warnings: list[str] = []
    filename_counts: Counter[str] = Counter()
    folders_scanned = 1
    total_files = 0
    unsupported_file_count = 0
    skipped_file_count = 0
    try:
        paths = root.rglob("*")
        for path in paths:
            try:
                relative = path.relative_to(root)
                if any(part in _SKIP_DIRECTORIES for part in relative.parts):
                    continue
                if path.is_dir():
                    folders_scanned += 1
                    continue
                if not path.is_file():
                    continue
                total_files += 1
                if not os.access(path, os.R_OK):
                    skipped_file_count += 1
                    if len(skipped) < _SAMPLE_LIMIT:
                        skipped.append(str(relative))
                    continue
                if path.suffix.lower() in _AUDIO_EXTENSIONS:
                    tracks.append(path)
                    filename_counts[path.name.casefold()] += 1
                    if len(str(relative)) > 220 and len(long_path_warnings) < _SAMPLE_LIMIT:
                        long_path_warnings.append(str(relative))
                else:
                    unsupported_file_count += 1
                    if len(unsupported) < _SAMPLE_LIMIT:
                        unsupported.append(str(relative))
            except (OSError, PermissionError, ValueError):
                skipped_file_count += 1
                if len(skipped) < _SAMPLE_LIMIT:
                    skipped.append(path.name)
    except (OSError, PermissionError) as exc:
        warnings.append(f"Some folders could not be read: {exc.__class__.__name__}.")
    tracks.sort(key=lambda path: str(path.relative_to(root)).lower())
    duplicate_name_candidates = [
        {"filename": filename, "count": count}
        for filename, count in sorted(filename_counts.items())
        if count > 1
    ][:_SAMPLE_LIMIT]
    if long_path_warnings:
        warnings.append(f"{len(long_path_warnings)} long audio path(s) are shown for review.")
    if skipped_file_count:
        warnings.append(f"{skipped_file_count} unreadable or skipped file(s) were not included.")
    return {
        "library_root": redact_path(root),
        "track_count": len(tracks),
        "supported_audio_files": len(tracks),
        "total_files": total_files,
        "folders_scanned": folders_scanned,
        "unsupported_file_count": unsupported_file_count,
        "skipped_file_count": skipped_file_count,
        "sample_tracks": [str(path.relative_to(root)) for path in tracks[:_SAMPLE_LIMIT]],
        "track_paths": tracks,
        "skipped_files": skipped,
        "unsupported_files": unsupported,
        "duplicate_name_candidates": duplicate_name_candidates,
        "long_path_warnings": long_path_warnings,
        "warnings": warnings,
    }


def scan_preview(library_root: str | None = None) -> dict[str, Any]:
    root = _target_root(library_root)
    if not _db_path(root).is_file():
        raise ValueError("Configured library is not initialized. Initialize it before scanning.")
    result = _scan(root)
    result.pop("track_paths")
    result["importable"] = bool(result["supported_audio_files"])
    result["message"] = "Preview only. No music files or local index records were changed."
    return result


def _filename_metadata(path: Path) -> tuple[str | None, str | None, str]:
    stem = path.stem.strip()
    if " - " in stem:
        artist, title = (part.strip() or None for part in stem.split(" - ", 1))
        return artist, title, "MEDIUM"
    return None, stem or None, "LOW"


def _embedded_tags(path: Path) -> dict[str, str]:
    """Read-only embedded tag lookup. Never writes; never raises."""
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return {}
        get = lambda key: (audio.get(key) or [''])[0].strip()
        return {
            "artist": get("artist"),
            "title": get("title"),
            "album": get("album"),
            "genre": get("genre"),
        }
    except Exception:
        return {}


def _track_metadata(path: Path) -> tuple[str | None, str | None, str | None, str | None, str, bool]:
    """
    Resolve artist/title/album/genre for a discovered file.

    Embedded tags take priority (source of truth for real metadata); filename
    parsing is only a fallback for artist/title when tags are missing. Never
    writes to the file — read-only lookup.
    """
    tags = _embedded_tags(path)
    embedded_artist = tags.get("artist") or None
    embedded_title = tags.get("title") or None
    album = tags.get("album") or None
    genre = tags.get("genre") or None

    if embedded_artist and embedded_title:
        return embedded_artist, embedded_title, album, genre, "HIGH", True

    fallback_artist, fallback_title, fallback_confidence = _filename_metadata(path)
    artist = embedded_artist or fallback_artist
    title = embedded_title or fallback_title
    confidence = "MEDIUM" if (embedded_artist or embedded_title) else fallback_confidence
    tags_present = bool(embedded_artist or embedded_title or album or genre)
    return artist, title, album, genre, confidence, tags_present


def import_previewed_library(library_root: str | None = None) -> dict[str, Any]:
    root = _target_root(library_root)
    db_path = _db_path(root)
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized. Initialize it before importing.")
    scan = _scan(root)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        existing_paths = {
            row[0]
            for row in conn.execute("SELECT filepath FROM tracks")
        }
        imported_count = 0
        tags_read_count = 0
        for path in scan["track_paths"]:
            safe_path = assert_path_under_root(path, root)
            artist, title, album, genre, confidence, tags_present = _track_metadata(safe_path)
            if tags_present:
                tags_read_count += 1
            if str(safe_path) not in existing_paths:
                imported_count += 1
            conn.execute(
                """
                INSERT INTO tracks (filepath, filename, artist, title, album, genre, filesize_bytes, status, processed_at, pipeline_ver, parse_confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 'library-import-v1', ?)
                ON CONFLICT(filepath) DO UPDATE SET
                    filename = excluded.filename,
                    filesize_bytes = excluded.filesize_bytes
                """,
                (str(safe_path), safe_path.name, artist, title, album, genre, safe_path.stat().st_size, now, confidence),
            )
        total_indexed_count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    scan.pop("track_paths")
    scan["imported_count"] = imported_count
    scan["existing_count"] = scan["supported_audio_files"] - imported_count
    scan["tags_read_count"] = tags_read_count
    scan["total_indexed_count"] = total_indexed_count
    scan["importable"] = bool(scan["supported_audio_files"])
    scan["next_actions"] = [
        {"label": "Open Library", "route": "/"},
        {"label": "Review MIK coverage", "route": "/jobs#mixed-in-key-coverage"},
        {"label": "Analyze missing BPM", "route": "/jobs#bpm-analysis"},
        {"label": "Analyze missing key/Camelot", "route": "/jobs#key-analysis"},
        {"label": "Build Manual Crates", "route": "/crates"},
    ]
    scan["message"] = "Imported filenames into CrateIQ's local index only. Audio files and tags were not changed."
    return scan
