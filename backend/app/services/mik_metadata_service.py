"""Read-only Mixed In Key-compatible metadata review and DB-only import."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.analyzer import CAMELOT_TO_MUSICAL, _read_existing_analysis

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_SAMPLE_LIMIT = 20
_TRUSTED_SOURCES = {"mixed_in_key", "mik_compatible_tag"}
_MIGRATION_COLUMNS = {
    "bpm_source": "TEXT",
    "bpm_trusted": "INTEGER NOT NULL DEFAULT 0",
    "key_source": "TEXT",
    "key_trusted": "INTEGER NOT NULL DEFAULT 0",
    "cue_source": "TEXT",
    "cue_count": "INTEGER NOT NULL DEFAULT 0",
    "metadata_trusted": "INTEGER NOT NULL DEFAULT 0",
    "metadata_imported_at": "TEXT",
}


def _db_path() -> Path:
    root = selected_library_root()
    return assert_path_under_root(library_db_path(root), root)


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}


def _ensure_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add local provenance columns only during the explicit import action."""
    existing = _columns(conn)
    for name, definition in _MIGRATION_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {definition}")


def _safe_tag_values(path: Path) -> tuple[float | None, str | None, str | None]:
    """Read existing tags via mutagen-backed analyzer helper; never write tags."""
    bpm, camelot = _read_existing_analysis(path)
    if bpm is not None and bpm <= 0:
        bpm = None
    camelot = camelot.upper() if camelot else None
    musical = CAMELOT_TO_MUSICAL.get(camelot) if camelot else None
    return bpm, camelot, musical


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = len(rows)
    with_bpm = sum(row.get("bpm") is not None for row in rows)
    with_camelot = sum(bool(row.get("key_camelot")) for row in rows)
    with_key = sum(bool(row.get("key_camelot") or row.get("key_musical")) for row in rows)
    with_cues = sum(int(row.get("cue_count") or 0) > 0 for row in rows)
    trusted_bpm = sum(
        row.get("bpm") is not None and row.get("bpm_source") in _TRUSTED_SOURCES
        for row in rows
    )
    trusted_key = sum(
        bool(row.get("key_camelot") or row.get("key_musical"))
        and row.get("key_source") in _TRUSTED_SOURCES
        for row in rows
    )
    missing_bpm = total - with_bpm
    missing_key = total - with_key
    return {
        "total_tracks": total,
        "with_bpm": with_bpm,
        "with_key": with_key,
        "with_camelot": with_camelot,
        "with_cues": with_cues,
        "trusted_bpm": trusted_bpm,
        "trusted_key": trusted_key,
        "missing_bpm": missing_bpm,
        "missing_key": missing_key,
        "fallback_bpm_candidates": missing_bpm,
        "fallback_key_candidates": missing_key,
    }


def _db_rows() -> tuple[list[dict[str, Any]], set[str]]:
    db_path = _db_path()
    if not db_path.is_file():
        return [], set()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = _columns(conn)
        select_columns = ["id", "filepath", "filename", "artist", "title", "bpm", "key_musical", "key_camelot"]
        select_columns.extend(column for column in _MIGRATION_COLUMNS if column in columns)
        rows = [dict(row) for row in conn.execute(f"SELECT {', '.join(select_columns)} FROM tracks ORDER BY id")]
    return rows, columns


def coverage() -> dict[str, Any]:
    """Return current local-index coverage without reading any audio files."""
    rows, _ = _db_rows()
    return {
        "summary": _summary(rows),
        "samples": [],
        "warnings": ["Cue tag parsing is not implemented; cue coverage is unavailable."],
        "cue_support": "unavailable",
        "write_behavior": "crateiq_db_only",
    }


def preview() -> dict[str, Any]:
    """Explicitly inspect imported file tags. No database or media writes occur."""
    root = selected_library_root()
    rows, _ = _db_rows()
    warnings: list[str] = ["Cue tag parsing is not implemented; cue coverage is unavailable."]
    samples: list[dict[str, Any]] = []
    merged_rows: list[dict[str, Any]] = []
    unreadable_count = 0

    for row in rows:
        merged = dict(row)
        try:
            path = assert_path_under_root(row["filepath"], root)
        except ValueError:
            unreadable_count += 1
            merged_rows.append(merged)
            continue
        if not path.is_file():
            unreadable_count += 1
            merged_rows.append(merged)
            continue
        bpm, camelot, musical = _safe_tag_values(path)
        found_bpm = bpm is not None
        found_key = camelot is not None
        if found_bpm and merged.get("bpm") is None:
            merged["bpm"] = bpm
            merged["bpm_source"] = "mik_compatible_tag"
        if found_key and not (merged.get("key_camelot") or merged.get("key_musical")):
            merged["key_camelot"] = camelot
            merged["key_musical"] = musical
            merged["key_source"] = "mik_compatible_tag"
        if (found_bpm or found_key) and len(samples) < _SAMPLE_LIMIT:
            samples.append({
                "track_id": row["id"],
                "filename": row["filename"],
                "bpm": bpm,
                "key_camelot": camelot,
                "key_musical": musical,
                "source": "mik_compatible_tag",
                "trusted": True,
            })
        merged_rows.append(merged)

    if unreadable_count:
        warnings.append(f"{unreadable_count} imported track file(s) were missing, unreadable, or outside the selected root.")
    return {
        "summary": _summary(merged_rows),
        "samples": samples,
        "warnings": warnings,
        "cue_support": "unavailable",
        "write_behavior": "crateiq_db_only",
    }


def import_metadata() -> dict[str, Any]:
    """Persist only missing MIK-compatible BPM/key values into processed.db."""
    root = selected_library_root()
    db_path = _db_path()
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized.")

    imported = unchanged = skipped = 0
    warnings: list[str] = ["Cue tag parsing is not implemented; cue coverage is unavailable."]
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_provenance_columns(conn)
        rows = conn.execute("SELECT id, filepath, bpm, key_musical, key_camelot FROM tracks ORDER BY id").fetchall()
        for row in rows:
            try:
                path = assert_path_under_root(row["filepath"], root)
            except ValueError:
                skipped += 1
                continue
            if not path.is_file():
                skipped += 1
                continue
            bpm, camelot, musical = _safe_tag_values(path)
            update: dict[str, Any] = {}
            if bpm is not None and row["bpm"] is None:
                update["bpm"] = bpm
                update["bpm_source"] = "mik_compatible_tag"
                update["bpm_trusted"] = 1
            if camelot is not None and not (row["key_camelot"] or row["key_musical"]):
                update["key_camelot"] = camelot
                update["key_musical"] = musical
                update["key_source"] = "mik_compatible_tag"
                update["key_trusted"] = 1
            if update:
                update["metadata_trusted"] = 1
                update["metadata_imported_at"] = now
                assignments = ", ".join(f"{column} = ?" for column in update)
                conn.execute(
                    f"UPDATE tracks SET {assignments} WHERE id = ?",
                    [*update.values(), row["id"]],
                )
                imported += 1
            else:
                unchanged += 1
        rows_after = [dict(row) for row in conn.execute("SELECT bpm, key_musical, key_camelot, cue_count, bpm_source, key_source FROM tracks")]
    if skipped:
        warnings.append(f"{skipped} imported track file(s) were missing, unreadable, or outside the selected root.")
    return {
        "summary": _summary(rows_after),
        "samples": [],
        "warnings": warnings,
        "cue_support": "unavailable",
        "write_behavior": "crateiq_db_only",
        "imported_count": imported,
        "unchanged_count": unchanged,
        "skipped_count": skipped,
    }
