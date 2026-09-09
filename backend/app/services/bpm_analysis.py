"""
bpm_analysis — BPM anomaly detection and review-state management.

Detection logic
---------------
Mirrors the heuristics in modules/analyzer.py _apply_bpm_correction() so
that the anomaly flags are consistent with what the analyzer would have done.
Detection is purely read-only: it reads the pipeline DB and writes anomaly
records into the backend DB (jobs.db).  It never touches audio files or
the pipeline DB.

Anomaly reasons
---------------
  missing_bpm     — NULL or zero BPM stored
  too_low_10x     — BPM < 20, likely a ×10 scale artifact (e.g. 12.1 → 121)
  likely_halved   — 20 ≤ BPM < 90, common aubio halving artifact (e.g. 64 → 128)
  likely_doubled  — 160 < BPM ≤ 240, not a high-BPM genre (e.g. 174 → 87)
  too_high        — BPM > 240, almost certainly wrong for any genre

High-BPM genres (DNB, jungle, hardcore, gabber) are exempt from the
likely_doubled check since 170–200 BPM is expected for those tracks.

Review statuses
---------------
  pending   — newly detected, awaiting review
  reviewed  — operator has verified the BPM (accepted as-is or manually corrected)
  ignored   — operator has dismissed this flag (won't appear in default view)
  requeued  — a re-analysis job has been submitted for this track
  resolved  — was anomalous but BPM looks normal after a later scan
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..core.db import get_conn
from ..core.library_key import current_library_key
from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..schemas.bpm_analysis import BpmAnomalyResponse

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BPM anomaly classification
# ---------------------------------------------------------------------------

# Genres where 160–200+ BPM is expected
_HIGH_BPM_GENRES = frozenset(
    {"drum and bass", "dnb", "jungle", "hardcore", "gabber", "speedcore"}
)

REASON_LABELS: Dict[str, str] = {
    "missing_bpm":    "Missing BPM",
    "too_low_10x":    "Too Low (10× error?)",
    "likely_halved":  "Likely Halved",
    "likely_doubled": "Likely Doubled",
    "too_high":       "Too High",
}


def classify_bpm(
    bpm: Optional[float],
    genre: Optional[str] = None,
) -> Optional[Tuple[str, Optional[float]]]:
    """
    Return (reason, suggested_bpm) if the BPM value looks anomalous, else None.

    suggested_bpm is the heuristic correction (may still be wrong — the user
    reviews it).  It is None for missing_bpm since no suggestion is possible.
    """
    if bpm is None or bpm <= 0:
        return ("missing_bpm", None)

    genre_lower = (genre or "").lower()
    is_high_bpm = any(g in genre_lower for g in _HIGH_BPM_GENRES)

    # 10× scale artifact: 0 < bpm < 20  (e.g. 12.1 → 121)
    if bpm < 20:
        return ("too_low_10x", round(bpm * 10, 1))

    # Likely halved: 20 ≤ bpm < 90
    if bpm < 90:
        return ("likely_halved", round(bpm * 2, 1))

    # 90–160: clean range for most genres — no flag

    # Likely doubled: > 160, not a high-BPM genre
    if bpm > 160 and not is_high_bpm:
        if bpm > 240:
            return ("too_high", round(bpm / 2, 1))
        return ("likely_doubled", round(bpm / 2, 1))

    # Too high even for high-BPM genres
    if bpm > 240:
        return ("too_high", round(bpm / 2, 1))

    return None  # Looks fine


# ---------------------------------------------------------------------------
# Scan and persist anomalies
# ---------------------------------------------------------------------------

def run_bpm_check() -> Tuple[int, int, int, List[BpmAnomalyResponse]]:
    """
    Scan all tracks in the pipeline DB, classify BPM anomalies, and upsert
    the results into the backend's bpm_anomalies table.

    Tracks that were previously flagged but now look fine are marked 'resolved'.

    Returns: (tracks_scanned, new_anomalies, resolved_count, anomaly_list)
    """
    if not pipeline_db_exists():
        log.info("bpm-check: pipeline DB not found — returning empty result")
        return 0, 0, 0, []

    _now = lambda: datetime.now(timezone.utc).isoformat()

    try:
        with get_pipeline_conn() as pconn:
            rows = pconn.execute(
                "SELECT id, filepath, artist, title, genre, bpm FROM tracks"
            ).fetchall()
    except Exception as exc:
        log.exception("bpm-check: failed to read pipeline DB: %s", exc)
        return 0, 0, 0, []

    scanned = len(rows)
    new_count = 0
    resolved_count = 0
    library_key = current_library_key()

    with get_conn() as conn:
        # Build a set of track_ids currently stored as non-resolved anomalies
        existing_ids = {
            row[0]
            for row in conn.execute(
                "SELECT track_id FROM bpm_anomalies WHERE library_key = ? AND review_status != 'resolved'", (library_key,)
            ).fetchall()
        }

        newly_anomalous_ids: set[int] = set()

        for row in rows:
            track_id = row["id"]
            bpm      = row["bpm"]
            genre    = row["genre"]

            result = classify_bpm(bpm, genre)
            if result is None:
                # Track looks fine — resolve if it was previously flagged
                if track_id in existing_ids:
                    conn.execute(
                        """UPDATE bpm_anomalies
                           SET review_status='resolved', reviewed_at=?
                           WHERE track_id=? AND library_key = ? AND review_status NOT IN ('reviewed','ignored')""",
                        (_now(), track_id, library_key),
                    )
                    resolved_count += 1
                continue

            reason, suggested = result
            newly_anomalous_ids.add(track_id)

            if track_id not in existing_ids:
                new_count += 1

            # Upsert: insert new or refresh BPM/reason on existing pending record.
            # Don't overwrite human review decisions (reviewed/ignored/requeued).
            conn.execute(
                """INSERT INTO bpm_anomalies
                       (track_id, filepath, artist, title, genre, current_bpm,
                        suggested_bpm, reason, review_status, detected_at, library_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                   ON CONFLICT(library_key, track_id) DO UPDATE SET
                       filepath      = excluded.filepath,
                       artist        = excluded.artist,
                       title         = excluded.title,
                       genre         = excluded.genre,
                       current_bpm   = excluded.current_bpm,
                       suggested_bpm = excluded.suggested_bpm,
                       reason        = excluded.reason,
                       detected_at   = excluded.detected_at,
                       -- Only reset to pending if not already in a human review state
                       review_status = CASE
                           WHEN review_status IN ('reviewed', 'ignored', 'requeued')
                           THEN review_status
                           ELSE 'pending'
                       END""",
                (
                    track_id,
                    row["filepath"],
                    row["artist"],
                    row["title"],
                    genre,
                    float(bpm) if bpm is not None else None,
                    suggested,
                    reason,
                    _now(), library_key,
                ),
            )

        # Fetch all non-resolved anomalies to return
        anomaly_rows = conn.execute(
            """SELECT * FROM bpm_anomalies
               WHERE library_key = ? AND review_status != 'resolved'
               ORDER BY
                   CASE reason
                       WHEN 'too_low_10x'    THEN 0
                       WHEN 'missing_bpm'    THEN 1
                       WHEN 'likely_halved'  THEN 2
                       WHEN 'likely_doubled' THEN 3
                       WHEN 'too_high'       THEN 4
                       ELSE                       5
                   END,
                   LOWER(COALESCE(artist, ''))"""
        , (library_key,)).fetchall()

    items = [_row_to_response(r) for r in anomaly_rows]
    log.info(
        "bpm-check: scanned=%d new=%d resolved=%d total_active=%d",
        scanned, new_count, resolved_count, len(items),
    )
    return scanned, new_count, resolved_count, items


# ---------------------------------------------------------------------------
# List stored anomalies
# ---------------------------------------------------------------------------

def list_anomalies(
    status: Optional[str] = None,
    reason: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[BpmAnomalyResponse]:
    """Return stored anomaly records with optional filters."""
    clauses: List[str] = ["library_key = ?"]
    params: List[object] = [current_library_key()]

    if status and status != "all":
        clauses.append("review_status = ?")
        params.append(status)
    elif not status:
        clauses.append("review_status != 'resolved'")

    if reason:
        clauses.append("reason = ?")
        params.append(reason)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM bpm_anomalies {where}
                ORDER BY
                    CASE reason
                        WHEN 'too_low_10x'    THEN 0
                        WHEN 'missing_bpm'    THEN 1
                        WHEN 'likely_halved'  THEN 2
                        WHEN 'likely_doubled' THEN 3
                        WHEN 'too_high'       THEN 4
                        ELSE                       5
                    END,
                    LOWER(COALESCE(artist,''))
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
    return [_row_to_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Update review status
# ---------------------------------------------------------------------------

def update_anomaly(
    anomaly_id: int,
    review_status: str,
    review_note: Optional[str] = None,
    reanalysis_job_id: Optional[str] = None,
) -> Optional[BpmAnomalyResponse]:
    """
    Update the review status of an anomaly record.
    Returns the updated record or None if not found.
    """
    valid_statuses = {"reviewed", "ignored", "requeued", "pending", "resolved"}
    if review_status not in valid_statuses:
        raise ValueError(
            f"Invalid review_status {review_status!r}. "
            f"Allowed: {sorted(valid_statuses)}"
        )

    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        # Build update fields
        fields = ["review_status = ?", "reviewed_at = ?"]
        values: list = [review_status, now]

        if review_note is not None:
            fields.append("review_note = ?")
            values.append(review_note)

        if reanalysis_job_id is not None:
            fields.append("reanalysis_job_id = ?")
            values.append(reanalysis_job_id)

        values.extend((anomaly_id, current_library_key()))
        conn.execute(
            f"UPDATE bpm_anomalies SET {', '.join(fields)} WHERE id = ? AND library_key = ?",
            values,
        )
        row = conn.execute(
            "SELECT * FROM bpm_anomalies WHERE id = ? AND library_key = ?", (anomaly_id, current_library_key())
        ).fetchone()

    return _row_to_response(row) if row else None


# ---------------------------------------------------------------------------
# Get single anomaly
# ---------------------------------------------------------------------------

def get_anomaly(anomaly_id: int) -> Optional[BpmAnomalyResponse]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bpm_anomalies WHERE id = ? AND library_key = ?", (anomaly_id, current_library_key())
        ).fetchone()
    return _row_to_response(row) if row else None


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------

def get_summary() -> Dict[str, int]:
    """Return counts grouped by review_status."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT review_status, COUNT(*) AS cnt FROM bpm_anomalies WHERE library_key = ? GROUP BY review_status", (current_library_key(),)
        ).fetchall()
    return {r["review_status"]: r["cnt"] for r in rows}


def get_summary_by_reason() -> Dict[str, Dict[str, int]]:
    """Return counts grouped by review_status and by reason."""
    with get_conn() as conn:
        by_status = {
            r["review_status"]: r["cnt"]
            for r in conn.execute(
                "SELECT review_status, COUNT(*) AS cnt FROM bpm_anomalies WHERE library_key = ? GROUP BY review_status", (current_library_key(),)
            ).fetchall()
        }
        by_reason_raw = {
            r["reason"]: r["cnt"]
            for r in conn.execute(
                "SELECT reason, COUNT(*) AS cnt FROM bpm_anomalies WHERE library_key = ? GROUP BY reason", (current_library_key(),)
            ).fetchall()
        }
    by_reason = {REASON_LABELS.get(k, k): v for k, v in by_reason_raw.items()}
    return {"by_status": by_status, "by_reason": by_reason}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _row_to_response(row) -> BpmAnomalyResponse:
    return BpmAnomalyResponse(
        id=row["id"],
        track_id=row["track_id"],
        filepath=row["filepath"],
        artist=row["artist"],
        title=row["title"],
        genre=row["genre"],
        current_bpm=row["current_bpm"],
        suggested_bpm=row["suggested_bpm"],
        reason=row["reason"],
        reason_label=REASON_LABELS.get(row["reason"], row["reason"]),
        review_status=row["review_status"],
        detected_at=row["detected_at"],
        reviewed_at=row["reviewed_at"],
        review_note=row["review_note"],
        reanalysis_job_id=row["reanalysis_job_id"],
    )
