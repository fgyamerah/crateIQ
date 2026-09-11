"""DB-only user signals for tracks.

Ratings and favorites live beside the existing review decision/history data in
``track_reviews``.  They are deliberately not part of the audio tag writer.
Read paths never create or migrate the table; writes use an additive migration
and preserve review status, notes, and play history.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import library_db_path

MAX_BULK_TRACKS = 200
_UNSET = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_reviews'"
    ).fetchone()
    if not row:
        return set()
    return {str(item[1]) for item in conn.execute("PRAGMA table_info(track_reviews)")}


def ensure_review_table(conn: sqlite3.Connection) -> None:
    """Create the review table if needed and add Favorites additively."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS track_reviews(
            track_id INTEGER PRIMARY KEY,
            review_status TEXT NOT NULL DEFAULT 'unreviewed',
            rating INTEGER,
            favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
            notes TEXT NOT NULL DEFAULT '',
            play_count INTEGER NOT NULL DEFAULT 0,
            last_played_at TEXT,
            reviewed_at TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    columns = _table_columns(conn)
    if "favorite" not in columns:
        conn.execute(
            "ALTER TABLE track_reviews ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
        # Older CrateIQ versions represented Favorites as a review status.
        # Carry that user choice forward once, without changing the status.
        conn.execute(
            "UPDATE track_reviews SET favorite=1 WHERE review_status='favorite' AND favorite=0"
        )


def _default_summary(track_id: int) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "review_status": "unreviewed",
        "rating": None,
        "favorite": False,
    }


def summaries(root: Path, track_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Batch-read review signals for valid tracks in the selected DB."""
    if not track_ids:
        return {}
    db_path = library_db_path(root)
    if not db_path.is_file():
        return {}
    unique = list(dict.fromkeys(track_ids))
    placeholders = ",".join("?" for _ in unique)
    result: dict[int, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        review_columns = _table_columns(conn)
        if not review_columns:
            rows = conn.execute(
                f"SELECT id FROM tracks WHERE id IN ({placeholders})", unique
            ).fetchall()
            return {int(row["id"]): _default_summary(int(row["id"])) for row in rows}
        favorite_expr = (
            "COALESCE(r.favorite, CASE WHEN r.review_status='favorite' THEN 1 ELSE 0 END)"
            if "favorite" in review_columns else
            "CASE WHEN r.review_status='favorite' THEN 1 ELSE 0 END"
        )
        rows = conn.execute(
            f"""SELECT t.id AS track_id,
                       COALESCE(r.review_status, 'unreviewed') AS review_status,
                       r.rating AS rating,
                       {favorite_expr} AS favorite
                FROM tracks t
                LEFT JOIN track_reviews r ON r.track_id=t.id
                WHERE t.id IN ({placeholders})""",
            unique,
        ).fetchall()
        for row in rows:
            result[int(row["track_id"])] = {
                "track_id": int(row["track_id"]),
                "review_status": row["review_status"],
                "rating": row["rating"],
                "favorite": bool(row["favorite"]),
            }
    return result


def item(root: Path, track_id: int, *, create: bool = False) -> dict[str, Any] | None:
    db_path = library_db_path(root)
    if not db_path.is_file():
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if create:
            ensure_review_table(conn)
        row = conn.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone()
        if not row:
            return None
        review_columns = _table_columns(conn)
        if review_columns:
            favorite_expr = (
                "COALESCE(favorite, CASE WHEN review_status='favorite' THEN 1 ELSE 0 END)"
                if "favorite" in review_columns else
                "CASE WHEN review_status='favorite' THEN 1 ELSE 0 END"
            )
            summary_row = conn.execute(
                f"SELECT review_status, rating, {favorite_expr} AS favorite FROM track_reviews WHERE track_id=?",
                (track_id,),
            ).fetchone()
            result = _default_summary(track_id)
            if summary_row:
                result.update(dict(summary_row))
                result["track_id"] = track_id
                result["favorite"] = bool(result["favorite"])
        else:
            result = _default_summary(track_id)
        if create:
            row = conn.execute(
                """SELECT review_status, rating, notes, play_count,
                          last_played_at, reviewed_at, updated_at, favorite
                   FROM track_reviews WHERE track_id=?""",
                (track_id,),
            ).fetchone()
            if row:
                result.update(dict(row))
                result["favorite"] = bool(row["favorite"])
        return result


def update(
    root: Path,
    track_id: int,
    *,
    review_status: Any = _UNSET,
    rating: Any = _UNSET,
    favorite: Any = _UNSET,
    notes: Any = _UNSET,
) -> dict[str, Any] | None:
    db_path = library_db_path(root)
    if not db_path.is_file():
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_review_table(conn)
        conn.commit()
        if not conn.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
            return None
        old = item(root, track_id, create=True) or _default_summary(track_id)
        next_status = old.get("review_status", "unreviewed") if review_status is _UNSET else review_status
        next_rating = old.get("rating") if rating is _UNSET else (None if rating == 0 else rating)
        next_favorite = old.get("favorite", False) if favorite is _UNSET else favorite
        if review_status is not _UNSET and review_status == "favorite" and favorite is _UNSET:
            next_favorite = True
        next_notes = old.get("notes", "") if notes is _UNSET else notes
        next_reviewed_at = old.get("reviewed_at")
        if review_status is not _UNSET and review_status != old.get("review_status"):
            next_reviewed_at = _now() if next_status != "unreviewed" else None
        conn.execute(
            """INSERT INTO track_reviews(
                       track_id, review_status, rating, favorite, notes,
                       play_count, last_played_at, reviewed_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id) DO UPDATE SET
                       review_status=excluded.review_status,
                       rating=excluded.rating,
                       favorite=excluded.favorite,
                       notes=excluded.notes,
                       play_count=excluded.play_count,
                       last_played_at=excluded.last_played_at,
                       reviewed_at=excluded.reviewed_at,
                       updated_at=excluded.updated_at""",
            (
                track_id, next_status, next_rating, int(bool(next_favorite)),
                next_notes, old.get("play_count", 0), old.get("last_played_at"),
                next_reviewed_at, _now(),
            ),
        )
        conn.commit()
    return item(root, track_id, create=True)


def _validate_track_ids(track_ids: list[int]) -> list[int]:
    if not track_ids:
        raise ValueError("Select at least one track.")
    if len(track_ids) > MAX_BULK_TRACKS:
        raise ValueError(f"Bulk rating and Favorites are limited to {MAX_BULK_TRACKS} tracks at a time.")
    if len(set(track_ids)) != len(track_ids):
        raise ValueError("Selected track IDs must be unique.")
    return track_ids


def _validate_operations(operations: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not operations:
        raise ValueError("Choose a rating or Favorites action before previewing.")
    unknown = set(operations) - {"rating", "favorite"}
    if unknown:
        raise ValueError(f"Unsupported review signal: {sorted(unknown)[0]}.")
    normalized: dict[str, dict[str, Any]] = {}
    for field, raw in operations.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{field.capitalize()} operation must be an object.")
        keys = set(raw)
        if field == "rating":
            if keys - {"operation", "value"}:
                raise ValueError("Rating operation contains unsupported fields.")
            operation = raw.get("operation")
            value = raw.get("value")
            if operation == "set":
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                    raise ValueError("Rating must be an integer from 1 to 5.")
            elif operation == "clear":
                if value not in (None, ""):
                    raise ValueError("Clear rating does not accept a value.")
            else:
                raise ValueError("Rating operation must be set or clear.")
        else:
            if keys - {"operation", "value"}:
                raise ValueError("Favorite operation contains unsupported fields.")
            operation = raw.get("operation")
            value = raw.get("value")
            if operation != "set" or not isinstance(value, bool):
                raise ValueError("Favorite operation must explicitly set true or false.")
        normalized[field] = {"operation": operation, "value": value}
    return normalized


def _next_value(current: Any, operation: dict[str, Any]) -> Any:
    if operation["operation"] == "clear":
        return None
    return operation["value"]


def bulk_preview(root: Path, track_ids: list[int], *, operations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = _validate_track_ids(track_ids)
    normalized = _validate_operations(operations)
    current = summaries(root, ids)
    missing = len(ids) - len(current)
    fields: dict[str, dict[str, Any]] = {}
    changed_ids: set[int] = set()
    for field, operation in normalized.items():
        present = [track_id for track_id in ids if track_id in current]
        matching = sum(_next_value(current[track_id].get(field), operation) == current[track_id].get(field) for track_id in present)
        affected = len(present) - matching
        changed_ids.update(
            track_id for track_id in present
            if _next_value(current[track_id].get(field), operation) != current[track_id].get(field)
        )
        values = {current[track_id].get(field) for track_id in present}
        fields[field] = {
            "operation": operation["operation"],
            "value": operation.get("value"),
            "mixed": len(values) > 1,
            "affected_count": affected,
            "already_matching_count": matching,
            "skipped_count": missing,
        }
    return {
        "selected_count": len(ids),
        "eligible_count": len(current),
        "changeable_count": len(changed_ids),
        "missing_count": missing,
        "fields": fields,
        "items": [
            {
                "track_id": track_id,
                "status": "not_found" if track_id not in current else (
                    "change" if track_id in changed_ids else "unchanged"
                ),
            }
            for track_id in ids
        ],
        "message": "Preview only. No review history, ratings, Favorites, metadata, tags, or files were changed.",
    }


def bulk_apply(root: Path, track_ids: list[int], *, operations: dict[str, dict[str, Any]], confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Bulk rating and Favorites require confirm=true after reviewing the preview.")
    preview = bulk_preview(root, track_ids, operations=operations)
    if preview["missing_count"]:
        raise ValueError("Every selected track must belong to the active library.")
    normalized = _validate_operations(operations)
    db_path = library_db_path(root)
    with sqlite3.connect(db_path) as conn:
        ensure_review_table(conn)
        conn.commit()
        current = summaries(root, track_ids)
        results: list[dict[str, Any]] = []
        succeeded = unchanged = 0
        for track_id in track_ids:
            updates = {
                field: _next_value(current[track_id].get(field), operation)
                for field, operation in normalized.items()
                if _next_value(current[track_id].get(field), operation) != current[track_id].get(field)
            }
            if not updates:
                unchanged += 1
                results.append({"track_id": track_id, "status": "unchanged"})
                continue
            columns = ["track_id", *updates, "updated_at"]
            values = [track_id, *[int(value) if field == "favorite" else value for field, value in updates.items()], _now()]
            assignments = ", ".join(f"{field}=excluded.{field}" for field in updates)
            placeholders = ",".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO track_reviews({','.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT(track_id) DO UPDATE SET {assignments}, updated_at=excluded.updated_at",
                values,
            )
            succeeded += 1
            results.append({"track_id": track_id, "status": "succeeded", "fields": list(updates)})
        conn.commit()
    return {
        "selected_count": len(track_ids),
        "changed_count": succeeded,
        "succeeded_count": succeeded,
        "unchanged_count": unchanged,
        "results": results,
        "message": "Review signals saved to the active library database. Audio files and tags were not changed.",
    }
