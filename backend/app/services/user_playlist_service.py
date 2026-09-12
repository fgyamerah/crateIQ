"""Safe, library-scoped CRUD for manually curated user playlists.

Playlist state is stored beside the selected library in the app-owned crate
state database. Track rows are read from the selected library's pipeline DB;
audio files, tags, and review ownership are never changed here.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..core.crate_db import get_crate_conn
from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..core.library_root import selected_library_root
from ..schemas.user_playlist import (
    PlaylistAddPreview,
    PlaylistDetail,
    PlaylistMutationResponse,
    PlaylistSort,
    PlaylistSummary,
    PlaylistTrack,
)
from . import track_review_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary_row(conn: sqlite3.Connection, playlist_id: int):
    return conn.execute(
        """SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                  COUNT(pt.track_id) AS track_count
             FROM user_playlists p
             LEFT JOIN user_playlist_tracks pt ON pt.playlist_id = p.id
             WHERE p.id = ?
             GROUP BY p.id""",
        (playlist_id,),
    ).fetchone()


def _summary(row) -> PlaylistSummary:
    return PlaylistSummary(
        id=int(row["id"]),
        name=row["name"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        track_count=int(row["track_count"] or 0),
    )


def list_playlists() -> list[PlaylistSummary]:
    with get_crate_conn() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.description, p.created_at, p.updated_at,
                      COUNT(pt.track_id) AS track_count
                 FROM user_playlists p
                 LEFT JOIN user_playlist_tracks pt ON pt.playlist_id = p.id
                 GROUP BY p.id
                 ORDER BY p.updated_at DESC, p.id DESC"""
        ).fetchall()
    return [_summary(row) for row in rows]


def create_playlist(name: str, description: Optional[str]) -> PlaylistSummary:
    now = _now()
    with get_crate_conn() as conn:
        result = conn.execute(
            "INSERT INTO user_playlists (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, description, now, now),
        )
        row = _summary_row(conn, int(result.lastrowid))
    return _summary(row)


def update_playlist(
    playlist_id: int,
    *,
    name: Optional[str],
    description: Optional[str],
    update_description: bool,
) -> Optional[PlaylistSummary]:
    with get_crate_conn() as conn:
        if _summary_row(conn, playlist_id) is None:
            return None
        updates: list[str] = []
        params: list[object] = []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if update_description:
            updates.append("description = ?")
            params.append(description)
        if updates:
            updates.append("updated_at = ?")
            params.append(_now())
            params.append(playlist_id)
            conn.execute(f"UPDATE user_playlists SET {', '.join(updates)} WHERE id = ?", params)
        row = _summary_row(conn, playlist_id)
    return _summary(row)


def delete_playlist(playlist_id: int) -> bool:
    with get_crate_conn() as conn:
        return conn.execute("DELETE FROM user_playlists WHERE id = ?", (playlist_id,)).rowcount > 0


def _track_metadata(track_ids: list[int]) -> dict[int, dict]:
    if not track_ids or not pipeline_db_exists():
        return {}
    placeholders = ",".join("?" for _ in track_ids)
    with get_pipeline_conn() as conn:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(tracks)")}
        requested = (
            "filepath", "filename", "artist", "title", "genre", "comment", "label",
            "bpm", "key_musical", "key_camelot", "duration_sec", "bitrate_kbps",
            "status", "quality_tier", "parse_confidence", "storage_zone",
        )
        selected = [column for column in requested if column in columns]
        rows = conn.execute(
            f"""SELECT id, {', '.join(selected)}
                  FROM tracks WHERE id IN ({placeholders})""",
            track_ids,
        ).fetchall()
    return {
        int(row["id"]): {column: row[column] for column in selected}
        for row in rows
    }


def _sort_tracks(tracks: list[PlaylistTrack], sort: PlaylistSort, order: str) -> list[PlaylistTrack]:
    if sort == "manual":
        return tracks
    reverse = order == "desc"

    if sort == "rating":
        rated = [track for track in tracks if track.rating is not None]
        unrated = [track for track in tracks if track.rating is None]
        rated.sort(key=lambda track: (track.rating or 0, track.position), reverse=reverse)
        return rated + unrated

    def value(track: PlaylistTrack):
        if sort == "favorite":
            return (not track.favorite, track.favorite)
        return ((getattr(track, sort) or "").strip().lower(), track.track_id)

    return sorted(tracks, key=value, reverse=reverse)


def get_playlist(
    playlist_id: int,
    *,
    search: Optional[str] = None,
    sort: PlaylistSort = "manual",
    order: str = "asc",
    favorite_only: bool = False,
) -> Optional[PlaylistDetail]:
    with get_crate_conn() as conn:
        summary_row = _summary_row(conn, playlist_id)
        if summary_row is None:
            return None
        entry_rows = conn.execute(
            """SELECT track_id, position, added_at
                 FROM user_playlist_tracks
                WHERE playlist_id = ?
                ORDER BY position ASC""",
            (playlist_id,),
        ).fetchall()

    ids = [int(row["track_id"]) for row in entry_rows]
    metadata = _track_metadata(ids)
    reviews = track_review_service.summaries(selected_library_root(), ids)
    needle = search.strip().lower() if search else ""
    tracks: list[PlaylistTrack] = []
    for row in entry_rows:
        track_id = int(row["track_id"])
        fields = metadata.get(track_id)
        review = reviews.get(track_id, {})
        if fields is None:
            track = PlaylistTrack(
                track_id=track_id,
                position=int(row["position"]),
                added_at=row["added_at"],
                rating=review.get("rating"),
                favorite=bool(review.get("favorite", False)),
                missing_from_library=True,
            )
        else:
            track = PlaylistTrack(
                track_id=track_id,
                position=int(row["position"]),
                added_at=row["added_at"],
                rating=review.get("rating"),
                favorite=bool(review.get("favorite", False)),
                **{key: fields.get(key) for key in (
                    "artist", "title", "filename", "filepath", "genre", "comment", "label",
                    "bpm", "key_musical", "key_camelot", "duration_sec", "bitrate_kbps",
                    "status", "quality_tier", "parse_confidence", "storage_zone",
                )},
            )
        haystack = " ".join(filter(None, (track.artist, track.title, track.filename, track.genre)))
        if needle and needle not in haystack.lower():
            continue
        if favorite_only and not track.favorite:
            continue
        tracks.append(track)

    tracks = _sort_tracks(tracks, sort, order)
    return PlaylistDetail(**_summary(summary_row).model_dump(), tracks=tracks)


def _existing_and_available(playlist_id: int, track_ids: list[int]) -> tuple[set[int], set[int]]:
    available: set[int] = set()
    if pipeline_db_exists():
        placeholders = ",".join("?" for _ in track_ids)
        with get_pipeline_conn() as conn:
            available = {
                int(row["id"])
                for row in conn.execute(f"SELECT id FROM tracks WHERE id IN ({placeholders})", track_ids).fetchall()
            }
    with get_crate_conn() as conn:
        existing = {
            int(row["track_id"])
            for row in conn.execute(
                f"SELECT track_id FROM user_playlist_tracks WHERE playlist_id = ? AND track_id IN ({placeholders})",
                [playlist_id, *track_ids],
            ).fetchall()
        }
    return existing, available


def preview_add(playlist_id: int, track_ids: list[int]) -> PlaylistAddPreview | None:
    with get_crate_conn() as conn:
        if _summary_row(conn, playlist_id) is None:
            return None
    existing, available = _existing_and_available(playlist_id, track_ids)
    will_add = [track_id for track_id in track_ids if track_id in available and track_id not in existing]
    missing = len(track_ids) - len(available)
    already_present = len([track_id for track_id in track_ids if track_id in existing])
    return PlaylistAddPreview(
        playlist_id=playlist_id,
        selected_count=len(track_ids),
        will_add_count=len(will_add),
        already_present_count=already_present,
        missing_count=missing,
        track_ids=track_ids,
        message=f"{len(will_add)} will be added · {already_present} already present" + (f" · {missing} not found" if missing else ""),
    )


def add_tracks(playlist_id: int, track_ids: list[int]) -> PlaylistMutationResponse | str:
    with get_crate_conn() as conn:
        if _summary_row(conn, playlist_id) is None:
            return "playlist_missing"
    existing, available = _existing_and_available(playlist_id, track_ids)
    to_add = [track_id for track_id in track_ids if track_id in available and track_id not in existing]
    now = _now()
    with get_crate_conn() as conn:
        start = int(conn.execute("SELECT COALESCE(MAX(position), 0) FROM user_playlist_tracks WHERE playlist_id = ?", (playlist_id,)).fetchone()[0])
        for position, track_id in enumerate(to_add, start=start + 1):
            conn.execute(
                "INSERT INTO user_playlist_tracks (playlist_id, track_id, position, added_at) VALUES (?, ?, ?, ?)",
                (playlist_id, track_id, position, now),
            )
        if to_add:
            conn.execute("UPDATE user_playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
    detail = get_playlist(playlist_id)
    assert detail is not None
    missing = len(track_ids) - len(available)
    already_present = len([track_id for track_id in track_ids if track_id in existing])
    return PlaylistMutationResponse(
        playlist=detail,
        selected_count=len(track_ids),
        added_count=len(to_add),
        already_present_count=already_present,
        missing_count=missing,
        message=f"{len(to_add)} added · {already_present} already present" + (f" · {missing} not found" if missing else ""),
    )


def remove_track(playlist_id: int, track_id: int) -> str:
    with get_crate_conn() as conn:
        if _summary_row(conn, playlist_id) is None:
            return "playlist_missing"
        if not conn.execute("DELETE FROM user_playlist_tracks WHERE playlist_id = ? AND track_id = ?", (playlist_id, track_id)).rowcount:
            return "track_missing"
        rows = conn.execute("SELECT track_id FROM user_playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)).fetchall()
        for position, row in enumerate(rows, start=1):
            conn.execute("UPDATE user_playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?", (position, playlist_id, row["track_id"]))
        conn.execute("UPDATE user_playlists SET updated_at = ? WHERE id = ?", (_now(), playlist_id))
    return "removed"


def remove_tracks(playlist_id: int, track_ids: list[int]) -> str:
    with get_crate_conn() as conn:
        if _summary_row(conn, playlist_id) is None:
            return "playlist_missing"
        placeholders = ",".join("?" for _ in track_ids)
        conn.execute(f"DELETE FROM user_playlist_tracks WHERE playlist_id = ? AND track_id IN ({placeholders})", [playlist_id, *track_ids])
        rows = conn.execute("SELECT track_id FROM user_playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)).fetchall()
        for position, row in enumerate(rows, start=1):
            conn.execute("UPDATE user_playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?", (position, playlist_id, row["track_id"]))
        conn.execute("UPDATE user_playlists SET updated_at = ? WHERE id = ?", (_now(), playlist_id))
    return "removed"


def reorder_tracks(playlist_id: int, track_ids: list[int]) -> str:
    with get_crate_conn() as conn:
        if _summary_row(conn, playlist_id) is None:
            return "playlist_missing"
        current = [int(row["track_id"]) for row in conn.execute("SELECT track_id FROM user_playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)).fetchall()]
        if set(current) != set(track_ids) or len(current) != len(track_ids):
            return "invalid_order"
        conn.execute("UPDATE user_playlist_tracks SET position = position + ? WHERE playlist_id = ?", (len(current), playlist_id))
        for position, track_id in enumerate(track_ids, start=1):
            conn.execute("UPDATE user_playlist_tracks SET position = ? WHERE playlist_id = ? AND track_id = ?", (position, playlist_id, track_id))
        conn.execute("UPDATE user_playlists SET updated_at = ? WHERE id = ?", (_now(), playlist_id))
    return "reordered"
