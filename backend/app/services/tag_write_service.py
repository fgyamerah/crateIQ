"""
Controlled metadata write-back (Cycle 7).

Exact write plan -> mandatory byte-for-byte backup outside the scanned
music tree -> explicit confirm -> write -> re-read verify -> restore.

Write surface is deliberately conservative: only the four fields the local
index already models reliably through Cycles 5-6's review flow (artist,
title, album, genre). Never written: BPM, key, Camelot, cue points,
artwork, ReplayGain, or any other tag -- writing uses mutagen's easy-tag
interface (mutagen.File(path, easy=True)) and only ever touches the keys
explicitly listed below, so every other frame/field in the file is left
byte-for-byte alone by mutagen's incremental save. Only MP3 and FLAC are
supported; every other format is reported as an explicit blocker in the
preview, never silently skipped.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.config import TAG_WRITE_BACKUP_DIR
from ..core.db import get_conn
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_WRITABLE_FIELDS = ("artist", "title", "album", "genre")
_SUPPORTED_EXTENSIONS = {".mp3", ".flac"}
_MAX_TRACKS_PER_REQUEST = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    root = selected_library_root()
    path = assert_path_under_root(library_db_path(root), root)
    if not path.is_file():
        raise ValueError("Configured library is not initialized.")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_file_tags(path: Path) -> dict[str, str]:
    """Read-only lookup of the writable fields' current file values. Never writes."""
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return {}
        get = lambda key: (audio.get(key) or [""])[0]
        return {field: get(field) for field in _WRITABLE_FIELDS}
    except Exception:
        return {}


def _write_easy_tags(path: Path, fields: dict[str, str]) -> None:
    """Write only the given easy-tag keys. Every other tag/frame is preserved."""
    from mutagen import File as MFile
    audio = MFile(str(path), easy=True)
    if audio is None:
        raise RuntimeError("File could not be opened for tag writing.")
    for field, value in fields.items():
        audio[field] = [value]
    audio.save()


def _plan_row(track: sqlite3.Row, root: Path) -> dict[str, Any]:
    """Build one track's write plan row. Side-effect free."""
    filename = track["filename"]
    try:
        path = assert_path_under_root(track["filepath"], root)
    except (ValueError, TypeError):
        return {"track_id": track["id"], "filename": filename, "relative_path": None,
                "blocked": True, "blocker": "File path is outside the configured library root.", "fields": []}
    if not path.is_file():
        return {"track_id": track["id"], "filename": filename, "relative_path": None,
                "blocked": True, "blocker": "Source file no longer exists.", "fields": []}
    relative_path = str(path.relative_to(root))
    if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
        return {"track_id": track["id"], "filename": filename, "relative_path": relative_path,
                "blocked": True,
                "blocker": f"{(path.suffix.lstrip('.').upper() or 'This format')} is not a supported write-back format (MP3/FLAC only).",
                "fields": []}

    file_tags = _read_file_tags(path)
    fields: list[dict[str, Any]] = []
    for field in _WRITABLE_FIELDS:
        approved = (track[field] or "").strip()
        current_file_value = (file_tags.get(field) or "").strip()
        if not approved or approved == current_file_value:
            continue
        fields.append({
            "field": field,
            "current_file_value": current_file_value or None,
            "approved_value": approved,
            "action": "ADD" if not current_file_value else "REPLACE",
        })
    stat = path.stat()
    return {
        "track_id": track["id"], "filename": filename, "relative_path": relative_path,
        "blocked": False, "blocker": None, "fields": fields,
        # expected_mtime_ns is a JSON string: nanosecond epoch timestamps
        # exceed Number.MAX_SAFE_INTEGER, so a JS client round-tripping it as
        # a JSON number silently corrupts it, permanently failing the
        # apply-time staleness check below.
        "expected_size": stat.st_size, "expected_mtime_ns": str(stat.st_mtime_ns),
    }


def build_plan(track_ids: list[int]) -> dict[str, Any]:
    """Read-only, side-effect-free exact write plan for the given tracks."""
    if not track_ids:
        raise ValueError("Select at least one track.")
    if len(track_ids) > _MAX_TRACKS_PER_REQUEST:
        raise ValueError(f"Select at most {_MAX_TRACKS_PER_REQUEST} tracks per write-back plan.")
    root = selected_library_root()
    placeholders = ",".join("?" * len(track_ids))
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            row["id"]: row
            for row in conn.execute(
                f"SELECT id, filepath, filename, {', '.join(_WRITABLE_FIELDS)} FROM tracks WHERE id IN ({placeholders})",
                track_ids,
            )
        }
    items: list[dict[str, Any]] = []
    for track_id in track_ids:
        row = rows.get(track_id)
        if row is None:
            items.append({"track_id": track_id, "filename": None, "relative_path": None,
                          "blocked": True, "blocker": "Track no longer exists in the local index.", "fields": []})
            continue
        items.append(_plan_row(row, root))

    changeable = [item for item in items if not item["blocked"] and item["fields"]]
    return {
        "items": items,
        "track_count": len(items),
        "changeable_count": len(changeable),
        "no_op_count": sum(1 for item in items if not item["blocked"] and not item["fields"]),
        "blocked_count": sum(1 for item in items if item["blocked"]),
        "additions": sum(1 for item in items for f in item["fields"] if f["action"] == "ADD"),
        "replacements": sum(1 for item in items for f in item["fields"] if f["action"] == "REPLACE"),
        "backup_space_estimate_bytes": sum(item.get("expected_size") or 0 for item in changeable),
        "writable_fields": list(_WRITABLE_FIELDS),
        "supported_formats": sorted(_SUPPORTED_EXTENSIONS),
        "message": "Preview only. No file, backup, or local index changes were made.",
    }


def _backup_dir(operation_id: str) -> Path:
    backup_dir = TAG_WRITE_BACKUP_DIR / operation_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def apply_plan(track_ids: list[int], expected: dict[int, dict[str, int]], *, confirm: bool) -> dict[str, Any]:
    """
    expected: {track_id: {"expected_size": int, "expected_mtime_ns": int}}, echoed
    back by the client from its own most recent build_plan() call. A file whose
    current stat no longer matches is blocked as stale -- the plan is never
    silently rebased onto a file that changed after the user reviewed it.
    """
    if not confirm:
        raise ValueError("Applying tag write-back requires confirm=true after reviewing the exact plan.")
    plan = build_plan(track_ids)
    root = selected_library_root()
    operation_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tag_write_operations (id, status, track_count, plan_json, created_at, started_at) "
            "VALUES (?, 'running', ?, ?, ?, ?)",
            (operation_id, len(track_ids), json.dumps(plan["items"]), now, now),
        )

    backup_dir = _backup_dir(operation_id)
    manifest: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    applied = skipped = failed = 0

    with sqlite3.connect(_db_path()) as write_conn:
        write_conn.row_factory = sqlite3.Row
        for item in plan["items"]:
            track_id = item["track_id"]
            if item["blocked"]:
                skipped += 1
                results.append({"track_id": track_id, "status": "skipped", "reason": item["blocker"]})
                continue
            if not item["fields"]:
                skipped += 1
                results.append({"track_id": track_id, "status": "skipped", "reason": "No approved fields differ from the file."})
                continue

            path = assert_path_under_root(root / item["relative_path"], root)
            exp = expected.get(track_id) or {}
            try:
                stat = path.stat()
            except OSError:
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": "Source file no longer exists."})
                continue
            try:
                expected_mtime_ns = int(exp.get("expected_mtime_ns"))
            except (TypeError, ValueError):
                expected_mtime_ns = None
            if stat.st_size != exp.get("expected_size") or stat.st_mtime_ns != expected_mtime_ns:
                failed += 1
                results.append({"track_id": track_id, "status": "failed",
                                "reason": "File changed since preview -- stale plan blocked. Re-run preview and try again."})
                continue

            # 1. Backup first -- byte-for-byte, hash-verified, before any mutation.
            backup_name = f"{track_id}_{path.name}"
            backup_path = backup_dir / backup_name
            try:
                original_hash = _sha256(path)
                shutil.copy2(path, backup_path)
                if _sha256(backup_path) != original_hash:
                    raise RuntimeError("backup hash mismatch after copy")
            except Exception as exc:
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": f"Backup failed, no write attempted: {exc}"})
                continue
            manifest.append({
                "track_id": track_id, "relative_path": item["relative_path"], "backup_filename": backup_name,
                "original_sha256": original_hash, "original_size": stat.st_size,
                "original_mtime_ns": stat.st_mtime_ns, "backed_up_at": _now(),
            })

            # 2. Apply only the diffed, approved fields.
            approved_fields = {f["field"]: f["approved_value"] for f in item["fields"]}
            try:
                _write_easy_tags(path, approved_fields)
            except Exception as exc:
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": f"Write failed: {exc}. Backup preserved for restore."})
                continue

            # 3. Re-read and verify every field that was supposed to change.
            reread = _read_file_tags(path)
            mismatches = [field for field, value in approved_fields.items() if (reread.get(field) or "").strip() != value]
            if mismatches:
                failed += 1
                results.append({"track_id": track_id, "status": "failed",
                                "reason": f"Verification failed for: {', '.join(mismatches)}. Backup preserved for restore."})
                continue

            # 4. Local index already held the approved value (that's what made it
            # the plan's source of truth); nothing to update there.
            applied += 1
            results.append({"track_id": track_id, "status": "applied", "fields": list(approved_fields), "verified": True})
        write_conn.commit()

    status = "completed" if failed == 0 else ("partially_failed" if applied or skipped else "failed")
    with get_conn() as conn:
        conn.execute(
            "UPDATE tag_write_operations SET status = ?, applied_count = ?, skipped_count = ?, failed_count = ?, "
            "backup_manifest_json = ?, result_json = ?, finished_at = ? WHERE id = ?",
            (status, applied, skipped, failed, json.dumps(manifest), json.dumps(results), _now(), operation_id),
        )
    return {"operation_id": operation_id, "status": status, "applied": applied, "skipped": skipped,
            "failed": failed, "results": results}


def _row_to_operation(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for json_key, target_key in (
        ("plan_json", "plan"), ("backup_manifest_json", "backup_manifest"),
        ("result_json", "results"), ("warnings_json", "warnings"),
    ):
        raw = data.pop(json_key, None)
        try:
            data[target_key] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            data[target_key] = []
    return data


def list_operations(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tag_write_operations ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_operation(row) for row in rows]


def get_operation(operation_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tag_write_operations WHERE id = ?", (operation_id,)).fetchone()
    return _row_to_operation(row) if row else None


def restore_file(operation_id: str, track_id: int, *, confirm: bool) -> dict[str, Any]:
    """Restore one file from its recorded backup within a completed operation."""
    if not confirm:
        raise ValueError("Restoring a file requires confirm=true.")
    operation = get_operation(operation_id)
    if operation is None:
        raise LookupError("Write-back operation was not found.")
    manifest_entry = next((m for m in operation["backup_manifest"] if m["track_id"] == track_id), None)
    if manifest_entry is None:
        raise LookupError("No backup was recorded for this track in this operation.")

    backup_path = TAG_WRITE_BACKUP_DIR / operation_id / manifest_entry["backup_filename"]
    if not backup_path.is_file():
        raise LookupError("Backup file is missing on disk.")
    if _sha256(backup_path) != manifest_entry["original_sha256"]:
        raise RuntimeError("Backup integrity check failed -- refusing to restore from a corrupted backup.")

    root = selected_library_root()
    target_path = assert_path_under_root(root / manifest_entry["relative_path"], root)
    if not target_path.is_file():
        raise LookupError("Original file location no longer exists.")

    # Atomic replace: copy to a same-directory temp file, verify, then rename over the target.
    tmp_path = target_path.parent / f"{target_path.name}.restoring-{uuid.uuid4().hex[:8]}"
    shutil.copy2(backup_path, tmp_path)
    if _sha256(tmp_path) != manifest_entry["original_sha256"]:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("Restore verification failed before replace -- original file left untouched.")
    tmp_path.replace(target_path)
    verified = _sha256(target_path) == manifest_entry["original_sha256"]

    with get_conn() as conn:
        row = conn.execute("SELECT result_json, status FROM tag_write_operations WHERE id = ?", (operation_id,)).fetchone()
        results = json.loads(row["result_json"]) if row and row["result_json"] else []
        for result in results:
            if result.get("track_id") == track_id:
                result["restored"] = True
                result["restore_verified"] = verified
        applied_ids = {r["track_id"] for r in results if r.get("status") == "applied"}
        restored_ids = {r["track_id"] for r in results if r.get("restored")}
        new_status = "restored" if applied_ids and applied_ids.issubset(restored_ids) else row["status"]
        conn.execute(
            "UPDATE tag_write_operations SET result_json = ?, status = ?, restored_at = ? WHERE id = ?",
            (json.dumps(results), new_status, _now(), operation_id),
        )
    return {"track_id": track_id, "restored": True, "verified": verified, "relative_path": manifest_entry["relative_path"]}


def recover_interrupted_operations() -> int:
    """Close out operations left 'running' by a previous backend process.

    A restart must never silently resume a tag write-back or leave a row
    permanently claiming 'running'. Any file already backed up before the
    interruption keeps its backup on disk regardless -- this only rewrites
    the operational history row.
    """
    now = _now()
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM tag_write_operations WHERE status = 'running'").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE tag_write_operations SET status = 'failed', error_reason = ?, finished_at = ? WHERE id = ?",
                ("backend_restarted", now, row["id"]),
            )
    return len(rows)
