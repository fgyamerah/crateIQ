"""
Controlled metadata write-back (Cycle 7).

Exact write plan -> mandatory byte-for-byte backup outside the scanned
music tree -> explicit confirm -> write -> re-read verify -> restore.

Write surface is deliberately conservative: only the six fields the local
index models through its explicit review/edit flows (artist, title, album,
genre, comment, label). Never written: BPM, key, Camelot, cue points,
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
from typing import Any, Iterable

from ..core import db as backend_db
from ..core.config import TAG_WRITE_BACKUP_DIR
from ..core.db import get_conn
from ..core.library_key import current_library_key
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_WRITABLE_FIELDS = ("artist", "title", "album", "genre", "comment", "label")
_EASY_TAG_KEYS = {"label": "organization"}
_SUPPORTED_EXTENSIONS = {".mp3", ".flac"}
_MAX_TRACKS_PER_REQUEST = 50


def _scoped_writable_fields(allowed_fields: Iterable[str] | None) -> tuple[str, ...]:
    """Return a canonical safe field scope for an existing writer operation."""
    if allowed_fields is None:
        return _WRITABLE_FIELDS
    if isinstance(allowed_fields, str):
        raise ValueError("Tag-write field scope must be a collection of field names.")
    requested = set(allowed_fields)
    unsupported = requested - set(_WRITABLE_FIELDS)
    if unsupported:
        raise ValueError(f"Unsupported tag-write field: {sorted(unsupported)[0]}.")
    if not requested:
        raise ValueError("Select at least one tag-write field.")
    return tuple(field for field in _WRITABLE_FIELDS if field in requested)


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
        get = lambda key: str((audio.get(key) or [""])[0])
        result = {
            field: get(_EASY_TAG_KEYS.get(field, field))
            for field in _WRITABLE_FIELDS
        }
        if path.suffix.lower() == ".mp3" and not result["comment"]:
            full = MFile(str(path))
            if full is not None and full.tags is not None:
                for key in full.tags.keys():
                    if key.startswith("COMM"):
                        frame = full.tags[key]
                        if getattr(frame, "text", None):
                            result["comment"] = str(frame.text[0])
                            break
        return result
    except Exception:
        return {}


def _write_easy_tags(path: Path, fields: dict[str, str]) -> None:
    """Write only the given easy-tag keys. Every other tag/frame is preserved."""
    from mutagen import File as MFile
    audio = MFile(str(path), easy=True)
    if audio is None:
        raise RuntimeError("File could not be opened for tag writing.")
    mp3_comment = fields.get("comment") if path.suffix.lower() == ".mp3" else None
    easy_fields = {field: value for field, value in fields.items() if not (field == "comment" and mp3_comment is not None)}
    for field, value in easy_fields.items():
        key = _EASY_TAG_KEYS.get(field, field)
        if value:
            audio[key] = [value]
        elif key in audio:
            del audio[key]
    if easy_fields:
        audio.save()

    if mp3_comment is not None:
        from mutagen.id3 import COMM, ID3, ID3NoHeaderError
        try:
            tags = ID3(str(path))
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("COMM")
        if mp3_comment:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[mp3_comment]))
        tags.save(str(path))


def _clear_intents(conn: sqlite3.Connection, track_ids: list[int]) -> dict[int, set[str]]:
    """Return explicit current user clears; absent metadata is never inferred as a clear."""
    if not track_ids:
        return {}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "field_provenance" not in tables:
        return {}
    placeholders = ",".join("?" * len(track_ids))
    rows = conn.execute(
        f"SELECT track_id, field_name FROM field_provenance "
        f"WHERE track_id IN ({placeholders}) AND is_current = 1 AND origin = 'user' "
        "AND (value IS NULL OR TRIM(value) = '')",
        track_ids,
    ).fetchall()
    result: dict[int, set[str]] = {}
    for row in rows:
        result.setdefault(int(row["track_id"]), set()).add(str(row["field_name"]))
    return result


def _plan_row(
    track: sqlite3.Row,
    root: Path,
    clear_fields: set[str] | None = None,
    writable_fields: Iterable[str] = _WRITABLE_FIELDS,
) -> dict[str, Any]:
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
    clear_fields = clear_fields or set()
    row_keys = set(track.keys())
    for field in writable_fields:
        approved = (track[field] or "").strip() if field in row_keys else ""
        current_file_value = (file_tags.get(field) or "").strip()
        if approved == current_file_value:
            continue
        if not approved and field not in clear_fields:
            continue
        fields.append({
            "field": field,
            "current_file_value": current_file_value or None,
            "approved_value": approved,
            "action": "CLEAR" if not approved else ("ADD" if not current_file_value else "REPLACE"),
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


def build_plan_items_for_rows(rows: Iterable[sqlite3.Row], root: Path) -> list[dict[str, Any]]:
    """Build exact plan items for already-fetched rows in one filesystem pass.

    This is the read-only batch seam used by Inbox preparation-state and
    promotion preview. It avoids reopening the pipeline database once per
    track and ensures each managed file's tags are read at most once by a
    single projection request.
    """
    materialized = list(rows)
    if not materialized:
        return []
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        clear_by_track = _clear_intents(conn, [int(row["id"]) for row in materialized])
    return [_plan_row(row, root, clear_by_track.get(int(row["id"]))) for row in materialized]


def latest_track_outcomes(track_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Return each requested track's latest persisted tag-write outcome.

    Historical failures are deliberately not returned when a newer operation
    has a successful/non-failed result for the same track. Callers must still
    compare the current live plan before treating a latest failure as active.
    """
    wanted = set(track_ids)
    if not wanted:
        return {}
    outcomes: dict[int, dict[str, Any]] = {}
    jobs_db_path = Path(backend_db.JOBS_DB_PATH)
    if not jobs_db_path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{jobs_db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT status, plan_json, result_json, error_reason, created_at "
                "FROM tag_write_operations WHERE library_key = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 500",
                (current_library_key(),),
            ).fetchall()
    except sqlite3.Error:
        return {}

    for row in rows:
        try:
            results = json.loads(row["result_json"] or "[]")
        except (TypeError, ValueError):
            results = []
        result_ids: set[int] = set()
        for result in results:
            track_id = result.get("track_id")
            if track_id not in wanted or track_id in outcomes:
                continue
            result_ids.add(track_id)
            outcomes[track_id] = {
                "status": result.get("status"),
                "failed": result.get("status") == "failed",
                "created_at": row["created_at"],
            }

        # An interrupted/operation-level failure can have no per-track result.
        # Its saved plan is the bounded source of affected track identities.
        if row["status"] == "failed" and row["error_reason"]:
            try:
                plan_items = json.loads(row["plan_json"] or "[]")
            except (TypeError, ValueError):
                plan_items = []
            for item in plan_items:
                track_id = item.get("track_id")
                if track_id in wanted and track_id not in outcomes and track_id not in result_ids:
                    outcomes[track_id] = {
                        "status": "failed",
                        "failed": True,
                        "created_at": row["created_at"],
                    }
        if len(outcomes) == len(wanted):
            break
    return outcomes


def build_plan(track_ids: list[int], *, allowed_fields: Iterable[str] | None = None) -> dict[str, Any]:
    """Read-only, side-effect-free exact write plan for the given tracks."""
    if not track_ids:
        raise ValueError("Select at least one track.")
    if len(track_ids) > _MAX_TRACKS_PER_REQUEST:
        raise ValueError(f"Select at most {_MAX_TRACKS_PER_REQUEST} tracks per write-back plan.")
    writable_fields = _scoped_writable_fields(allowed_fields)
    root = selected_library_root()
    placeholders = ",".join("?" * len(track_ids))
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
        field_select = [field if field in columns else f"NULL AS {field}" for field in _WRITABLE_FIELDS]
        rows = {
            row["id"]: row
            for row in conn.execute(
                f"SELECT id, filepath, filename, {', '.join(field_select)} FROM tracks WHERE id IN ({placeholders})",
                track_ids,
            )
        }
        clear_by_track = _clear_intents(conn, track_ids)
    items: list[dict[str, Any]] = []
    for track_id in track_ids:
        row = rows.get(track_id)
        if row is None:
            items.append({"track_id": track_id, "filename": None, "relative_path": None,
                          "blocked": True, "blocker": "Track no longer exists in the local index.", "fields": []})
            continue
        items.append(_plan_row(row, root, clear_by_track.get(track_id), writable_fields))

    changeable = [item for item in items if not item["blocked"] and item["fields"]]
    return {
        "items": items,
        "track_count": len(items),
        "changeable_count": len(changeable),
        "no_op_count": sum(1 for item in items if not item["blocked"] and not item["fields"]),
        "blocked_count": sum(1 for item in items if item["blocked"]),
        "additions": sum(1 for item in items for f in item["fields"] if f["action"] == "ADD"),
        "replacements": sum(1 for item in items for f in item["fields"] if f["action"] == "REPLACE"),
        "clears": sum(1 for item in items for f in item["fields"] if f["action"] == "CLEAR"),
        "backup_space_estimate_bytes": sum(item.get("expected_size") or 0 for item in changeable),
        "writable_fields": list(writable_fields),
        "supported_formats": sorted(_SUPPORTED_EXTENSIONS),
        "message": "Preview only. No file, backup, or local index changes were made.",
    }


def _backup_dir(operation_id: str, library_key: str) -> Path:
    """Globally stored backups are namespaced by the immutable library key."""
    backup_dir = TAG_WRITE_BACKUP_DIR / library_key / operation_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def apply_plan(
    track_ids: list[int],
    expected: dict[int, dict[str, int]],
    *,
    confirm: bool,
    allowed_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """
    expected: {track_id: {"expected_size": int, "expected_mtime_ns": int}}, echoed
    back by the client from its own most recent build_plan() call. A file whose
    current stat no longer matches is blocked as stale -- the plan is never
    silently rebased onto a file that changed after the user reviewed it.
    """
    if not confirm:
        raise ValueError("Applying tag write-back requires confirm=true after reviewing the exact plan.")
    plan = build_plan(track_ids, allowed_fields=allowed_fields)
    root = selected_library_root()
    operation_id = uuid.uuid4().hex
    now = _now()
    library_key = current_library_key()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tag_write_operations (id, status, track_count, plan_json, created_at, started_at, library_key) "
            "VALUES (?, 'running', ?, ?, ?, ?, ?)",
            (operation_id, len(track_ids), json.dumps(plan["items"]), now, now, library_key),
        )

    backup_dir = _backup_dir(operation_id, library_key)
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

            if not item["fields"]:
                skipped += 1
                results.append({"track_id": track_id, "status": "skipped", "reason": "No approved fields differ from the file."})
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
            "backup_manifest_json = ?, result_json = ?, finished_at = ? WHERE id = ? AND library_key = ?",
            (status, applied, skipped, failed, json.dumps(manifest), json.dumps(results), _now(), operation_id, library_key),
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
            "SELECT * FROM tag_write_operations WHERE library_key = ? ORDER BY created_at DESC, id DESC LIMIT ?", (current_library_key(), limit)
        ).fetchall()
    return [_row_to_operation(row) for row in rows]


def get_operation(operation_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tag_write_operations WHERE id = ? AND library_key = ?", (operation_id, current_library_key())).fetchone()
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

    backup_path = TAG_WRITE_BACKUP_DIR / current_library_key() / operation_id / manifest_entry["backup_filename"]
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
        key = current_library_key()
        row = conn.execute("SELECT result_json, status FROM tag_write_operations WHERE id = ? AND library_key = ?", (operation_id, key)).fetchone()
        results = json.loads(row["result_json"]) if row and row["result_json"] else []
        for result in results:
            if result.get("track_id") == track_id:
                result["restored"] = True
                result["restore_verified"] = verified
        applied_ids = {r["track_id"] for r in results if r.get("status") == "applied"}
        restored_ids = {r["track_id"] for r in results if r.get("restored")}
        new_status = "restored" if applied_ids and applied_ids.issubset(restored_ids) else row["status"]
        conn.execute(
            "UPDATE tag_write_operations SET result_json = ?, status = ?, restored_at = ? WHERE id = ? AND library_key = ?",
            (json.dumps(results), new_status, _now(), operation_id, key),
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
        key = current_library_key()
        rows = conn.execute("SELECT id FROM tag_write_operations WHERE status = 'running' AND library_key = ?", (key,)).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE tag_write_operations SET status = 'failed', error_reason = ?, finished_at = ? WHERE id = ? AND library_key = ?",
                ("backend_restarted", now, row["id"], key),
            )
    return len(rows)
