"""Local, deterministic CRUD for manually assembled crates.

Crate state lives beside the selected library; track data stays read-only in
processed.db. Nothing here reads or changes audio files, tags, or MIK data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..core.crate_db import get_crate_conn
from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..schemas.crate import CrateDetail, CrateSummary, CrateTrack


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary(row) -> CrateSummary:
    return CrateSummary(id=int(row["id"]), name=row["name"], notes=row["notes"], created_at=row["created_at"], updated_at=row["updated_at"], track_count=int(row["track_count"] or 0))


def _get_summary_row(conn, crate_id: int):
    return conn.execute("""SELECT c.id, c.name, c.notes, c.created_at, c.updated_at, COUNT(ct.track_id) AS track_count
        FROM manual_crates c LEFT JOIN manual_crate_tracks ct ON ct.crate_id = c.id
        WHERE c.id = ? GROUP BY c.id""", (crate_id,)).fetchone()


def list_crates() -> list[CrateSummary]:
    with get_crate_conn() as conn:
        rows = conn.execute("""SELECT c.id, c.name, c.notes, c.created_at, c.updated_at, COUNT(ct.track_id) AS track_count
            FROM manual_crates c LEFT JOIN manual_crate_tracks ct ON ct.crate_id = c.id
            GROUP BY c.id ORDER BY c.updated_at DESC, c.id DESC""").fetchall()
    return [_summary(row) for row in rows]


def create_crate(name: str, notes: Optional[str]) -> CrateSummary:
    now = _now()
    with get_crate_conn() as conn:
        result = conn.execute("INSERT INTO manual_crates (name, notes, created_at, updated_at) VALUES (?, ?, ?, ?)", (name, notes, now, now))
        row = _get_summary_row(conn, int(result.lastrowid))
    return _summary(row)


def _track_metadata(track_ids: list[int]) -> dict[int, dict]:
    if not track_ids or not pipeline_db_exists():
        return {}
    placeholders = ",".join("?" for _ in track_ids)
    with get_pipeline_conn() as conn:
        rows = conn.execute(f"SELECT id, artist, title, filename, genre, bpm, key_camelot, duration_sec FROM tracks WHERE id IN ({placeholders})", track_ids).fetchall()
    return {int(row["id"]): {"artist": row["artist"], "title": row["title"], "filename": row["filename"], "genre": row["genre"], "bpm": float(row["bpm"]) if row["bpm"] is not None else None, "key_camelot": row["key_camelot"], "duration_sec": float(row["duration_sec"]) if row["duration_sec"] is not None else None} for row in rows}


def get_crate(crate_id: int) -> Optional[CrateDetail]:
    with get_crate_conn() as conn:
        summary_row = _get_summary_row(conn, crate_id)
        if summary_row is None:
            return None
        item_rows = conn.execute("SELECT track_id, position, added_at, note FROM manual_crate_tracks WHERE crate_id = ? ORDER BY position", (crate_id,)).fetchall()
    metadata = _track_metadata([int(row["track_id"]) for row in item_rows])
    tracks = [CrateTrack(track_id=int(row["track_id"]), position=int(row["position"]), added_at=row["added_at"], note=row["note"], **metadata.get(int(row["track_id"]), {"missing_from_library": True})) for row in item_rows]
    return CrateDetail(**_summary(summary_row).model_dump(), tracks=tracks)


def update_crate(crate_id: int, *, name: Optional[str], notes: Optional[str], update_notes: bool) -> Optional[CrateSummary]:
    with get_crate_conn() as conn:
        if _get_summary_row(conn, crate_id) is None:
            return None
        updates, params = [], []
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if update_notes:
            updates.append("notes = ?")
            params.append(notes)
        if updates:
            updates.append("updated_at = ?")
            params.append(_now())
            params.append(crate_id)
            conn.execute(f"UPDATE manual_crates SET {', '.join(updates)} WHERE id = ?", params)
        row = _get_summary_row(conn, crate_id)
    return _summary(row)


def delete_crate(crate_id: int) -> bool:
    with get_crate_conn() as conn:
        return conn.execute("DELETE FROM manual_crates WHERE id = ?", (crate_id,)).rowcount > 0


def _track_exists(track_id: int) -> bool:
    if not pipeline_db_exists():
        return False
    with get_pipeline_conn() as conn:
        return conn.execute("SELECT 1 FROM tracks WHERE id = ?", (track_id,)).fetchone() is not None


def add_track(crate_id: int, track_id: int, note: Optional[str]) -> str:
    if not _track_exists(track_id):
        return "track_missing"
    with get_crate_conn() as conn:
        if _get_summary_row(conn, crate_id) is None:
            return "crate_missing"
        if conn.execute("SELECT 1 FROM manual_crate_tracks WHERE crate_id = ? AND track_id = ?", (crate_id, track_id)).fetchone():
            return "duplicate"
        position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM manual_crate_tracks WHERE crate_id = ?", (crate_id,)).fetchone()[0]
        conn.execute("INSERT INTO manual_crate_tracks (crate_id, track_id, position, added_at, note) VALUES (?, ?, ?, ?, ?)", (crate_id, track_id, position, _now(), note))
        conn.execute("UPDATE manual_crates SET updated_at = ? WHERE id = ?", (_now(), crate_id))
    return "added"


def remove_track(crate_id: int, track_id: int) -> str:
    with get_crate_conn() as conn:
        if _get_summary_row(conn, crate_id) is None:
            return "crate_missing"
        deleted = conn.execute("DELETE FROM manual_crate_tracks WHERE crate_id = ? AND track_id = ?", (crate_id, track_id)).rowcount
        if not deleted:
            return "track_missing"
        rows = conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id = ? ORDER BY position", (crate_id,)).fetchall()
        for position, row in enumerate(rows, start=1):
            conn.execute("UPDATE manual_crate_tracks SET position = ? WHERE crate_id = ? AND track_id = ?", (position, crate_id, row["track_id"]))
        conn.execute("UPDATE manual_crates SET updated_at = ? WHERE id = ?", (_now(), crate_id))
    return "removed"


def reorder_tracks(crate_id: int, track_ids: list[int]) -> str:
    with get_crate_conn() as conn:
        if _get_summary_row(conn, crate_id) is None:
            return "crate_missing"
        current = [row["track_id"] for row in conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id = ? ORDER BY position", (crate_id,)).fetchall()]
        if set(current) != set(track_ids) or len(current) != len(track_ids):
            return "invalid_order"
        conn.execute("UPDATE manual_crate_tracks SET position = position + ? WHERE crate_id = ?", (len(current), crate_id))
        for position, track_id in enumerate(track_ids, start=1):
            conn.execute("UPDATE manual_crate_tracks SET position = ? WHERE crate_id = ? AND track_id = ?", (position, crate_id, track_id))
        conn.execute("UPDATE manual_crates SET updated_at = ? WHERE id = ?", (_now(), crate_id))
    return "reordered"
