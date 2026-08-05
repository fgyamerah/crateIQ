"""Persist non-destructive duplicate-review decisions in the local index.

Snapshots retain only the safe, relative-path output produced by the existing
rmlint preview. Decisions are scoped to their snapshot, so a later scan never
silently applies an old decision to a changed duplicate group.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import analysis_jobs_service

_DECISIONS = {"keep", "ignore", "review_later", "unresolved"}
_SAFETY = ["db_only_review", "no_delete", "no_move", "no_rename", "no_quarantine", "no_tag_writes"]


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
        CREATE TABLE IF NOT EXISTS duplicate_review_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            groups_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS duplicate_review_decisions (
            snapshot_id INTEGER NOT NULL,
            group_id TEXT NOT NULL,
            track_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('keep', 'ignore', 'review_later', 'unresolved')),
            note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'rmlint_preview',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, group_id, track_id),
            FOREIGN KEY (snapshot_id) REFERENCES duplicate_review_snapshots(id)
        )
        """
    )
    decision_columns = {row[1] for row in conn.execute("PRAGMA table_info(duplicate_review_decisions)")}
    if "source" not in decision_columns:
        conn.execute("ALTER TABLE duplicate_review_decisions ADD COLUMN source TEXT NOT NULL DEFAULT 'rmlint_preview'")


def _empty_response(message: str) -> dict[str, Any]:
    return {
        "summary": {"groups": 0, "candidates": 0, "unresolved": 0, "keep": 0, "ignore": 0, "review_later": 0},
        "groups": [], "safety": _SAFETY, "warnings": [], "latest_preview_at": None,
        "source": None, "message": message,
    }


def _safe_groups(groups: Any) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not isinstance(group.get("group_id"), str):
            continue
        items = []
        for item in group.get("items", []):
            if not isinstance(item, dict) or not isinstance(item.get("track_id"), int):
                continue
            items.append({
                "track_id": item["track_id"], "filename": str(item.get("filename") or "Track"),
                "title": item.get("title"), "artist": item.get("artist"),
                "relative_path": item.get("relative_path"), "size_bytes": item.get("size_bytes"),
            })
        safe.append({
            "group_id": group["group_id"], "reason": str(group.get("reason") or "duplicate candidate"),
            "confidence": group.get("confidence") if group.get("confidence") in {"high", "medium", "low"} else "low",
            "items": items,
        })
    return safe


def _response_from_snapshot(conn: sqlite3.Connection, snapshot: sqlite3.Row) -> dict[str, Any]:
    try:
        groups = _safe_groups(json.loads(snapshot["groups_json"]))
    except (TypeError, json.JSONDecodeError):
        groups = []
    try:
        warnings = [str(item) for item in json.loads(snapshot["warnings_json"])]
    except (TypeError, json.JSONDecodeError):
        warnings = ["The saved duplicate preview could not be read completely."]
    decisions = {
        (row["group_id"], row["track_id"]): row
        for row in conn.execute(
            "SELECT group_id, track_id, decision, note, updated_at FROM duplicate_review_decisions WHERE snapshot_id = ?",
            (snapshot["id"],),
        )
    }
    summary = {"groups": len(groups), "candidates": 0, "unresolved": 0, "keep": 0, "ignore": 0, "review_later": 0}
    for group in groups:
        for item in group["items"]:
            decision = decisions.get((group["group_id"], item["track_id"]))
            item["decision"] = decision["decision"] if decision else "unresolved"
            item["note"] = decision["note"] if decision else ""
            item["reviewed_at"] = decision["updated_at"] if decision else None
            summary["candidates"] += 1
            summary[item["decision"]] += 1
    return {
        "summary": summary, "groups": groups, "safety": _SAFETY, "warnings": warnings,
        "latest_preview_at": snapshot["created_at"], "source": snapshot["source"],
        "message": "Review decisions are stored only in CrateIQ's local index. No file action is available.",
    }


def get_review() -> dict[str, Any]:
    try:
        db_path = _db_path()
    except ValueError:
        return _empty_response("Initialize the local library index, then refresh a safe duplicate preview to begin review.")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "duplicate_review_snapshots" not in tables:
            return _empty_response("No duplicate preview is saved yet. Refresh the safe preview to begin DB-only review.")
        snapshot = conn.execute(
            "SELECT id, created_at, source, groups_json, warnings_json FROM duplicate_review_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if snapshot is None:
            return _empty_response("No duplicate preview is saved yet. Refresh the safe preview to begin DB-only review.")
        return _response_from_snapshot(conn, snapshot)


def refresh_preview() -> dict[str, Any]:
    preview = analysis_jobs_service.preview("duplicate_detection")
    groups = _safe_groups(preview.get("groups"))
    warnings = [str(item) for item in preview.get("warnings", [])]
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        cursor = conn.execute(
            "INSERT INTO duplicate_review_snapshots (created_at, source, groups_json, warnings_json) VALUES (?, ?, ?, ?)",
            (_now(), "rmlint_preview", json.dumps(groups), json.dumps(warnings)),
        )
        snapshot = conn.execute(
            "SELECT id, created_at, source, groups_json, warnings_json FROM duplicate_review_snapshots WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        assert snapshot is not None
        return _response_from_snapshot(conn, snapshot)


def update_decision(group_id: str, track_id: int, decision: str, note: str = "") -> dict[str, Any]:
    if decision not in _DECISIONS:
        raise ValueError("Decision must be keep, ignore, review_later, or unresolved.")
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        snapshot = conn.execute(
            "SELECT id, created_at, source, groups_json, warnings_json FROM duplicate_review_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if snapshot is None:
            raise LookupError("No duplicate preview is saved yet. Refresh preview before recording a decision.")
        groups = _safe_groups(json.loads(snapshot["groups_json"]))
        if not any(group["group_id"] == group_id and any(item["track_id"] == track_id for item in group["items"]) for group in groups):
            raise LookupError("Duplicate group item was not found in the latest preview.")
        conn.execute(
            """
            INSERT INTO duplicate_review_decisions (snapshot_id, group_id, track_id, decision, note, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id, group_id, track_id) DO UPDATE SET
                decision = excluded.decision, note = excluded.note, source = excluded.source, updated_at = excluded.updated_at
            """,
            (snapshot["id"], group_id, track_id, decision, note.strip(), snapshot["source"], _now()),
        )
        return _response_from_snapshot(conn, snapshot)
