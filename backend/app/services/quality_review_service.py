"""Persist safe ffprobe findings and DB-only quality-review decisions.

This service only calls the existing bounded ffprobe preview. It stores safe
relative-path findings and human review state in the selected library index;
it never transcodes, edits media/tags, or runs remediation.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import analysis_jobs_service, quality_findings_service

_DECISIONS = {"reviewed", "ignore", "review_later", "unresolved"}
_LOW_BITRATE_KBPS = 192
_LOSSY_CODECS = {"mp2", "mp3", "aac", "vorbis", "opus", "wma"}
_FLAGS = {"unreadable", "missing_bitrate", "low_bitrate", "missing_duration", "unsupported_format", "probe_warning"}
_SAFETY = ["db_only_review", "probe_only", "no_transcode", "no_file_writes", "no_tag_writes"]


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
        CREATE TABLE IF NOT EXISTS quality_review_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            tracks_checked INTEGER NOT NULL,
            items_json TEXT NOT NULL,
            warnings_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quality_review_decisions (
            snapshot_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('reviewed', 'ignore', 'review_later', 'unresolved')),
            note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'ffprobe_preview',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (snapshot_id, track_id),
            FOREIGN KEY (snapshot_id) REFERENCES quality_review_snapshots(id)
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(quality_review_decisions)")}
    if "source" not in columns:
        conn.execute("ALTER TABLE quality_review_decisions ADD COLUMN source TEXT NOT NULL DEFAULT 'ffprobe_preview'")


def _empty_response(message: str) -> dict[str, Any]:
    return {
        "summary": {"tracks_checked": 0, "findings": 0, "unresolved": 0, "reviewed": 0, "ignored": 0, "review_later": 0},
        "items": [], "safety": _SAFETY, "warnings": [], "latest_preview_at": None, "source": None,
        "low_bitrate_threshold_kbps": _LOW_BITRATE_KBPS, "message": message,
    }


def _issue_flags(item: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    status = item.get("status")
    if status in {"unreadable", "missing_bitrate", "unsupported_format"}:
        flags.append(status)
    elif status == "probe_error":
        flags.append("probe_warning")
    if item.get("duration_sec") is None:
        flags.append("missing_duration")
    codec = str(item.get("codec") or "").casefold()
    bitrate = item.get("bitrate_kbps")
    if codec in _LOSSY_CODECS and isinstance(bitrate, int) and bitrate < _LOW_BITRATE_KBPS:
        flags.append("low_bitrate")
    return flags


def _safe_items(items: Any) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("track_id"), int):
            continue
        raw_status = item.get("status")
        status = "probe_warning" if raw_status == "probe_error" else raw_status
        if status not in {"probe_ok", "unreadable", "missing_bitrate", "unsupported_format", "probe_warning"}:
            status = "probe_warning"
        flags = [flag for flag in _issue_flags(item) if flag in _FLAGS]
        safe.append({
            "track_id": item["track_id"], "filename": str(item.get("filename") or "Track"),
            "relative_path": item.get("relative_path"), "container": item.get("container"), "codec": item.get("codec"),
            "duration_sec": item.get("duration_sec"), "bitrate_kbps": item.get("bitrate_kbps"),
            "sample_rate_hz": item.get("sample_rate_hz"), "channels": item.get("channels"),
            "file_size_bytes": item.get("file_size_bytes"), "status": status, "flags": flags,
        })
    return safe


def _track_metadata(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    by_id = {item["track_id"]: item for item in items}
    placeholders = ",".join("?" for _ in by_id)
    rows = conn.execute(f"SELECT id, title, artist FROM tracks WHERE id IN ({placeholders})", tuple(by_id)).fetchall()
    for track_id, title, artist in rows:
        by_id[track_id]["title"] = title
        by_id[track_id]["artist"] = artist


_DURABLE_ITEM_DEFAULTS = {
    "relative_path": None, "container": None, "codec": None, "duration_sec": None,
    "bitrate_kbps": None, "sample_rate_hz": None, "channels": None, "file_size_bytes": None,
    "status": None, "flags": [],
}
_FFPROBE_ITEM_DEFAULTS = {
    "reason_code": None, "source": "ffprobe_preview", "severity": None, "blocking": False,
    "message": None, "details": None, "first_seen_at": None, "last_seen_at": None,
    "occurrence_count": None,
}


def _durable_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Durable findings (e.g. BPM-analysis decode findings), orphans skipped.

    Read independently of any ffprobe snapshot, so these always survive a
    `refresh_preview()` snapshot replace -- the architectural reason this
    module exists (see `quality_findings_service`).
    """
    findings = quality_findings_service.list_findings(conn=conn)
    if not findings:
        return []
    track_ids = {finding["track_id"] for finding in findings}
    placeholders = ",".join("?" for _ in track_ids)
    tracks = {
        row["id"]: row
        for row in conn.execute(f"SELECT id, filename, title, artist FROM tracks WHERE id IN ({placeholders})", tuple(track_ids))
    }
    items: list[dict[str, Any]] = []
    for finding in findings:
        track = tracks.get(finding["track_id"])
        if track is None:
            continue  # orphan safety: the referenced track no longer exists
        items.append({
            "finding_key": finding["finding_key"], "track_id": finding["track_id"],
            "filename": track["filename"], "title": track["title"], "artist": track["artist"],
            **_DURABLE_ITEM_DEFAULTS,
            "reason_code": finding["reason_code"], "source": finding["source"],
            "severity": finding["severity"], "blocking": finding["blocking"],
            "message": finding["message"], "details": finding["details"],
            "first_seen_at": finding["first_seen_at"], "last_seen_at": finding["last_seen_at"],
            "occurrence_count": finding["occurrence_count"],
            "decision": finding["decision"], "note": finding["note"], "reviewed_at": finding["reviewed_at"],
        })
    return items


def _summarize(tracks_checked: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"tracks_checked": tracks_checked, "findings": len(items), "unresolved": 0, "reviewed": 0, "ignored": 0, "review_later": 0}
    for item in items:
        summary["ignored" if item["decision"] == "ignore" else item["decision"]] += 1
    return summary


def _response_from_snapshot(conn: sqlite3.Connection, snapshot: sqlite3.Row) -> dict[str, Any]:
    try:
        items = _safe_items(json.loads(snapshot["items_json"]))
    except (TypeError, json.JSONDecodeError):
        items = []
    try:
        warnings = [str(item) for item in json.loads(snapshot["warnings_json"])]
    except (TypeError, json.JSONDecodeError):
        warnings = ["The saved quality preview could not be read completely."]
    _track_metadata(conn, items)
    decisions = {
        row["track_id"]: row
        for row in conn.execute(
            "SELECT track_id, decision, note, updated_at FROM quality_review_decisions WHERE snapshot_id = ?",
            (snapshot["id"],),
        )
    }
    findings = [item for item in items if item["flags"]]
    for item in findings:
        item["finding_key"] = f"ffprobe:{item['track_id']}"
        decision = decisions.get(item["track_id"])
        item["decision"] = decision["decision"] if decision else "unresolved"
        item["note"] = decision["note"] if decision else ""
        item["reviewed_at"] = decision["updated_at"] if decision else None
        item.update(_FFPROBE_ITEM_DEFAULTS)
    all_items = findings + _durable_items(conn)
    return {
        "summary": _summarize(snapshot["tracks_checked"], all_items), "items": all_items,
        "safety": _SAFETY, "warnings": warnings,
        "latest_preview_at": snapshot["created_at"], "source": snapshot["source"],
        "low_bitrate_threshold_kbps": _LOW_BITRATE_KBPS,
        "message": "Review decisions are stored only in CrateIQ's local index. No transcode or remediation action is available.",
    }


def _response_without_snapshot(conn: sqlite3.Connection, message: str) -> dict[str, Any]:
    """No ffprobe snapshot exists yet, but durable findings are shown regardless."""
    durable = _durable_items(conn)
    return {
        "summary": _summarize(0, durable), "items": durable, "safety": _SAFETY, "warnings": [],
        "latest_preview_at": None, "source": None,
        "low_bitrate_threshold_kbps": _LOW_BITRATE_KBPS, "message": message,
    }


def _latest_snapshot(conn: sqlite3.Connection) -> sqlite3.Row | None:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "quality_review_snapshots" not in tables:
        return None
    return conn.execute(
        "SELECT id, created_at, source, tracks_checked, items_json, warnings_json FROM quality_review_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _current_response(conn: sqlite3.Connection, no_snapshot_message: str) -> dict[str, Any]:
    snapshot = _latest_snapshot(conn)
    if snapshot is None:
        return _response_without_snapshot(conn, no_snapshot_message)
    return _response_from_snapshot(conn, snapshot)


def get_review() -> dict[str, Any]:
    try:
        db_path = _db_path()
    except ValueError:
        return _empty_response("Initialize the local library index, then refresh a safe ffprobe preview to begin quality review.")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return _current_response(conn, "No audio-quality preview is saved yet. Refresh the safe ffprobe preview to begin review.")


def refresh_preview() -> dict[str, Any]:
    preview = analysis_jobs_service.preview("audio_quality_probe")
    items = _safe_items(preview.get("quality_probes"))
    warnings = [str(item) for item in preview.get("warnings", [])]
    tracks_checked = int((preview.get("summary") or {}).get("total_tracks_checked", len(items)))
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        cursor = conn.execute(
            "INSERT INTO quality_review_snapshots (created_at, source, tracks_checked, items_json, warnings_json) VALUES (?, ?, ?, ?, ?)",
            (_now(), "ffprobe_preview", tracks_checked, json.dumps(items), json.dumps(warnings)),
        )
        snapshot = conn.execute(
            "SELECT id, created_at, source, tracks_checked, items_json, warnings_json FROM quality_review_snapshots WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        assert snapshot is not None
        return _response_from_snapshot(conn, snapshot)


def _parse_durable_finding_id(finding_key: str) -> int:
    try:
        return int(finding_key.split(":", 1)[1])
    except (IndexError, ValueError):
        raise ValueError("finding_key is not a valid durable finding identifier.")


def update_decision(track_id: int, decision: str, note: str = "", finding_key: str | None = None) -> dict[str, Any]:
    """Update a review decision, addressed by track_id (legacy) or finding_key.

    `finding_key` disambiguates when a track has more than one open finding
    (e.g. an ffprobe `low_bitrate` flag plus a durable BPM decode finding).
    Omitting it preserves the original ffprobe-only decision contract for
    existing callers. A `finding_key` that does not belong to `track_id` is
    rejected so a decision can never be misapplied to the wrong finding.
    """
    if decision not in _DECISIONS:
        raise ValueError("Decision must be reviewed, ignore, review_later, or unresolved.")
    if finding_key and finding_key.startswith("durable:"):
        finding_id = _parse_durable_finding_id(finding_key)
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            owned = any(
                finding["finding_id"] == finding_id and finding["track_id"] == track_id
                for finding in quality_findings_service.list_findings(conn=conn)
            )
            if not owned:
                raise LookupError("Quality finding was not found for this track.")
            quality_findings_service.update_decision(finding_id, decision, note, conn=conn)
            return _current_response(conn, "No audio-quality preview is saved yet. Refresh preview before recording a decision.")

    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_tables(conn)
        snapshot = conn.execute(
            "SELECT id, created_at, source, tracks_checked, items_json, warnings_json FROM quality_review_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if snapshot is None:
            raise LookupError("No audio-quality preview is saved yet. Refresh preview before recording a decision.")
        items = _safe_items(json.loads(snapshot["items_json"]))
        if not any(item["track_id"] == track_id and item["flags"] for item in items):
            raise LookupError("Quality finding was not found in the latest preview.")
        if finding_key and finding_key != f"ffprobe:{track_id}":
            raise LookupError("Quality finding was not found for this track.")
        conn.execute(
            """
            INSERT INTO quality_review_decisions (snapshot_id, track_id, decision, note, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id, track_id) DO UPDATE SET
                decision = excluded.decision, note = excluded.note, source = excluded.source, updated_at = excluded.updated_at
            """,
            (snapshot["id"], track_id, decision, note.strip(), snapshot["source"], _now()),
        )
        return _response_from_snapshot(conn, snapshot)
