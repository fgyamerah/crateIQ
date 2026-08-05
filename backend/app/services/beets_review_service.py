"""Safe selected-field enrichment review using CrateIQ's local index only.

Beets remains optional and is not invoked in this foundation. The existing
candidate preview identifies missing local metadata; users explicitly enter
and select allowed non-critical values before a DB-only apply.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import analysis_jobs_service

_ALLOWED_FIELDS = ("artist", "title", "genre")
_DECISIONS = {"pending", "applied", "ignored", "review_later"}
_SAFETY = ["db_only_apply", "review_before_apply", "no_tag_writes", "no_file_moves", "no_audio_changes", "no_bpm_key_camelot_cue_changes"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    root = selected_library_root()
    path = assert_path_under_root(library_db_path(root), root)
    if not path.is_file():
        raise ValueError("Configured library is not initialized.")
    return path


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beets_review_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            items_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beets_review_decisions (
            snapshot_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('pending', 'applied', 'ignored', 'review_later')),
            note TEXT NOT NULL DEFAULT '',
            selected_fields_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT 'crateiq_metadata_candidate',
            updated_at TEXT NOT NULL,
            applied_at TEXT,
            PRIMARY KEY (snapshot_id, track_id),
            FOREIGN KEY (snapshot_id) REFERENCES beets_review_snapshots(id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(beets_review_decisions)")}
    if "source" not in columns:
        conn.execute("ALTER TABLE beets_review_decisions ADD COLUMN source TEXT NOT NULL DEFAULT 'crateiq_metadata_candidate'")
    if "applied_at" not in columns:
        conn.execute("ALTER TABLE beets_review_decisions ADD COLUMN applied_at TEXT")
    track_columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    for name, definition in {"enrichment_source": "TEXT", "enrichment_updated_at": "TEXT", "enrichment_reviewed_at": "TEXT"}.items():
        if name not in track_columns:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {name} {definition}")


def _empty_response(message: str) -> dict[str, Any]:
    return {
        "summary": {"candidates": 0, "pending": 0, "applied": 0, "ignored": 0, "review_later": 0, "fields_selected": 0},
        "items": [], "safety": _SAFETY, "warnings": [], "latest_preview_at": None,
        "source": None, "message": message,
    }


def _valid_fields(fields: Any, allowed: set[str]) -> dict[str, str]:
    if not isinstance(fields, dict):
        raise ValueError("selected_fields must be an object of explicitly selected values.")
    invalid = set(fields) - set(_ALLOWED_FIELDS)
    if invalid:
        raise ValueError(f"Unsupported enrichment field(s): {', '.join(sorted(invalid))}.")
    result: dict[str, str] = {}
    for field, value in fields.items():
        if field not in allowed:
            raise ValueError(f"{field} is not missing for this candidate and cannot be overwritten in this workflow.")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must contain a non-empty selected value.")
        if len(value.strip()) > 500:
            raise ValueError(f"{field} is too long.")
        result[field] = value.strip()
    return result


def _safe_items(items: Any) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("track_id"), int):
            continue
        current = item.get("current_fields") if isinstance(item.get("current_fields"), dict) else {}
        current_fields = {field: current.get(field) if isinstance(current.get(field), str) else None for field in _ALLOWED_FIELDS}
        missing = [field for field in item.get("missing_fields", []) if field in _ALLOWED_FIELDS and not current_fields[field]]
        safe.append({
            "track_id": item["track_id"], "filename": str(item.get("filename") or "Track"),
            "relative_path": item.get("relative_path"), "current_fields": current_fields,
            "missing_fields": missing, "allowed_fields": missing,
        })
    return safe


def _response_from_snapshot(conn: sqlite3.Connection, snapshot: sqlite3.Row) -> dict[str, Any]:
    try:
        items = _safe_items(json.loads(snapshot["items_json"]))
    except (TypeError, json.JSONDecodeError):
        items = []
    try:
        warnings = [str(item) for item in json.loads(snapshot["warnings_json"])]
    except (TypeError, json.JSONDecodeError):
        warnings = ["The saved enrichment preview could not be read completely."]
    decisions = {
        row["track_id"]: row
        for row in conn.execute(
            "SELECT track_id, decision, note, selected_fields_json, updated_at FROM beets_review_decisions WHERE snapshot_id = ?",
            (snapshot["id"],),
        )
    }
    summary = {"candidates": len(items), "pending": 0, "applied": 0, "ignored": 0, "review_later": 0, "fields_selected": 0}
    for item in items:
        decision = decisions.get(item["track_id"])
        try:
            selected = _valid_fields(json.loads(decision["selected_fields_json"]), set(item["allowed_fields"])) if decision else {}
        except (TypeError, json.JSONDecodeError, ValueError):
            selected = {}
        item["decision"] = decision["decision"] if decision else "pending"
        item["note"] = decision["note"] if decision else ""
        item["selected_fields"] = selected
        item["updated_at"] = decision["updated_at"] if decision else None
        summary[item["decision"]] += 1
        summary["fields_selected"] += len(selected)
    return {
        "summary": summary, "items": items, "safety": _SAFETY, "warnings": warnings,
        "latest_preview_at": snapshot["created_at"], "source": snapshot["source"],
        "message": "Local metadata candidates only: Beets is not invoked. Apply writes selected fields to CrateIQ's local index only.",
    }


def get_review() -> dict[str, Any]:
    try:
        db_path = _db_path()
    except ValueError:
        return _empty_response("Initialize the local library index, then refresh a local metadata candidate preview to begin review.")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "beets_review_snapshots" not in tables:
            return _empty_response("No Beets enrichment preview is saved yet. Refresh the local candidate preview to begin review.")
        snapshot = conn.execute("SELECT id, created_at, source, items_json, warnings_json FROM beets_review_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if snapshot is None:
            return _empty_response("No Beets enrichment preview is saved yet. Refresh the local candidate preview to begin review.")
        return _response_from_snapshot(conn, snapshot)


def refresh_preview() -> dict[str, Any]:
    preview = analysis_jobs_service.preview("beets_enrichment")
    items = []
    for candidate in analysis_jobs_service.beets_enrichment_candidates():
        items.append({
            "track_id": candidate.get("track_id"), "filename": candidate.get("filename"),
            "relative_path": candidate.get("relative_path"),
            "current_fields": {field: candidate.get(field) for field in _ALLOWED_FIELDS},
            "missing_fields": candidate.get("missing_fields", []),
        })
    safe_items = _safe_items(items)
    warnings = [str(item) for item in preview.get("warnings", [])]
    warnings.append("Local candidate preview only: no Beets subprocess, tag write, or file operation was started.")
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        cursor = conn.execute(
            "INSERT INTO beets_review_snapshots (created_at, source, items_json, warnings_json) VALUES (?, ?, ?, ?)",
            (_now(), "crateiq_metadata_candidate", json.dumps(safe_items), json.dumps(warnings)),
        )
        snapshot = conn.execute("SELECT id, created_at, source, items_json, warnings_json FROM beets_review_snapshots WHERE id = ?", (cursor.lastrowid,)).fetchone()
        assert snapshot is not None
        return _response_from_snapshot(conn, snapshot)


def _latest_snapshot(conn: sqlite3.Connection) -> sqlite3.Row:
    snapshot = conn.execute("SELECT id, created_at, source, items_json, warnings_json FROM beets_review_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if snapshot is None:
        raise LookupError("No Beets enrichment preview is saved yet. Refresh preview before recording a decision.")
    return snapshot


def _snapshot_item(snapshot: sqlite3.Row, track_id: int) -> dict[str, Any]:
    items = _safe_items(json.loads(snapshot["items_json"]))
    item = next((item for item in items if item["track_id"] == track_id), None)
    if item is None:
        raise LookupError("Enrichment candidate was not found in the latest preview.")
    return item


def update_review(track_id: int, decision: str, note: str = "", selected_fields: dict[str, str] | None = None) -> dict[str, Any]:
    if decision not in _DECISIONS:
        raise ValueError("Decision must be pending, applied, ignored, or review_later.")
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        snapshot = _latest_snapshot(conn)
        item = _snapshot_item(snapshot, track_id)
        if decision == "applied":
            raise ValueError("Use the explicit selected-field apply action instead of setting applied directly.")
        selected = _valid_fields(selected_fields or {}, set(item["allowed_fields"]))
        conn.execute(
            """
            INSERT INTO beets_review_decisions (snapshot_id, track_id, decision, note, selected_fields_json, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id, track_id) DO UPDATE SET
                decision = excluded.decision, note = excluded.note, selected_fields_json = excluded.selected_fields_json,
                source = excluded.source, updated_at = excluded.updated_at
            """,
            (snapshot["id"], track_id, decision, note.strip(), json.dumps(selected), snapshot["source"], _now()),
        )
        return _response_from_snapshot(conn, snapshot)


def apply_selected(items: list[dict[str, Any]], *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Applying enrichment requires confirm=true after reviewing selected fields.")
    if not items:
        raise ValueError("Select at least one track and field to apply.")
    applied = skipped = failed = 0
    warnings: list[str] = []
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        snapshot = _latest_snapshot(conn)
        now = _now()
        for request in items:
            track_id = request.get("track_id")
            if not isinstance(track_id, int):
                failed += 1; warnings.append("Skipped an invalid track selection."); continue
            try:
                item = _snapshot_item(snapshot, track_id)
                fields = _valid_fields(request.get("fields"), set(item["allowed_fields"]))
            except (LookupError, ValueError) as exc:
                failed += 1; warnings.append(str(exc)); continue
            if not fields:
                failed += 1; warnings.append(f"Select at least one allowed field for {item['filename']}."); continue
            stored = conn.execute(
                "SELECT selected_fields_json FROM beets_review_decisions WHERE snapshot_id = ? AND track_id = ?",
                (snapshot["id"], track_id),
            ).fetchone()
            try:
                persisted = _valid_fields(json.loads(stored["selected_fields_json"]), set(item["allowed_fields"])) if stored else {}
            except (TypeError, json.JSONDecodeError, ValueError):
                persisted = {}
            if fields != persisted:
                failed += 1; warnings.append(f"Save the selected fields for {item['filename']} before applying."); continue
            row = conn.execute("SELECT artist, title, genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if row is None:
                failed += 1; warnings.append(f"Track {track_id} no longer exists in the local index."); continue
            current = dict(row)
            nonempty = [field for field in fields if current.get(field)]
            if nonempty:
                skipped += 1; warnings.append(f"Skipped {item['filename']}: {', '.join(nonempty)} is no longer empty and overwrite is not supported."); continue
            assignments = ", ".join(f"{field} = ?" for field in fields)
            conn.execute(
                f"UPDATE tracks SET {assignments}, enrichment_source = ?, enrichment_updated_at = ?, enrichment_reviewed_at = ? WHERE id = ?",
                (*fields.values(), snapshot["source"], now, now, track_id),
            )
            conn.execute(
                """
                INSERT INTO beets_review_decisions (snapshot_id, track_id, decision, note, selected_fields_json, source, updated_at, applied_at)
                VALUES (?, ?, 'applied', '', ?, ?, ?, ?)
                ON CONFLICT(snapshot_id, track_id) DO UPDATE SET
                    decision = 'applied', selected_fields_json = excluded.selected_fields_json, source = excluded.source,
                    updated_at = excluded.updated_at, applied_at = excluded.applied_at
                """,
                (snapshot["id"], track_id, json.dumps(fields), snapshot["source"], now, now),
            )
            applied += 1
        review = _response_from_snapshot(conn, snapshot)
    return {"applied": applied, "skipped": skipped, "failed": failed, "warnings": warnings, "review": review}
