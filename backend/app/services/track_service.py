"""
track_service — read-only queries against the pipeline's tracks table.

All functions open a fresh read-only connection from pipeline_db and return
Python model objects.  Functions never raise on "DB not found" — they return
empty results so callers can decide what to surface.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..core.library_root import selected_library_root
from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists, storage_zone_predicate
from ..models.track import Track
from ..schemas.track import CompatibleTrackItem, CompatibleTracksResponse, TrackStats, TrackIssueItem
from modules.harmonic import bpm_score, camelot_distance, camelot_score, genre_score

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compatible tracks — Camelot wheel relation labels
# ---------------------------------------------------------------------------
_MATCH_LABELS = {
    "same_key": "Same key",
    "adjacent_key": "Adjacent key",
    "relative_key": "Relative major/minor",
}

# ---------------------------------------------------------------------------
# Allowed sort columns — never interpolate user input directly into SQL
# ---------------------------------------------------------------------------
# Text columns sort case-insensitively with blank/NULL values grouped last
# (both ascending and descending) rather than relying on SQLite's incidental
# NULL/empty-string ordering. "key" prefers key_camelot, falling back to
# key_musical, matching what the Inbox table displays in its Key column.
_TEXT_SORT_COLUMNS = {
    "artist":   "artist",
    "title":    "title",
    "filename": "filename",
    "genre":    "genre",
}
_KEY_SORT_EXPR = "COALESCE(NULLIF(TRIM(key_camelot), ''), NULLIF(TRIM(key_musical), ''))"
_NUMERIC_SORT_COLUMNS = {"bpm": "bpm"}
_RAW_SORT_COLUMNS = {"processed_at": "processed_at"}
# Field-completeness readiness proxy for sorting only: required fields
# (artist/title/genre) missing -> BLOCKED; required fields present but BPM
# or key missing -> WARNING; otherwise READY. This intentionally mirrors
# only the DB-observable half of workspace_service._promotion_readiness
# (required fields + BPM/key warnings) and does not re-derive its
# filesystem-dependent checks (pending tag write-back, missing file,
# destination collision) — those stay exactly as they are, computed only by
# the Promotion Preview endpoint that already powers the Inbox Readiness
# badge. This proxy exists solely to give the Readiness column a
# deterministic, testable sort order.
_READINESS_SORT_EXPR = (
    "(CASE "
    "WHEN TRIM(COALESCE(artist,'')) = '' OR TRIM(COALESCE(title,'')) = '' "
    "OR TRIM(COALESCE(genre,'')) = '' THEN 2 "
    "WHEN bpm IS NULL OR (TRIM(COALESCE(key_camelot,'')) = '' "
    "AND TRIM(COALESCE(key_musical,'')) = '') THEN 1 "
    "ELSE 0 END)"
)
_DEFAULT_SORT = "artist"
VALID_SORT_KEYS = frozenset(
    set(_TEXT_SORT_COLUMNS) | set(_NUMERIC_SORT_COLUMNS) | set(_RAW_SORT_COLUMNS) | {"key", "readiness"}
)


def _build_order_by(sort: str, order: str) -> str:
    """
    Build a validated ORDER BY body (no "ORDER BY" prefix). `sort` must
    already be a key of VALID_SORT_KEYS -- callers that accept sort from a
    request should reject unknown keys explicitly rather than relying on
    the _DEFAULT_SORT fallback here, which exists only as defense in depth
    for internal callers.
    """
    order_dir = "ASC" if order.lower() != "desc" else "DESC"
    key = sort if sort in VALID_SORT_KEYS else _DEFAULT_SORT

    if key == "readiness":
        return f"{_READINESS_SORT_EXPR} {order_dir}, LOWER(COALESCE(artist, '')) ASC, id ASC"

    if key == "key":
        blank = f"({_KEY_SORT_EXPR}) IS NULL"
        value_expr = f"LOWER({_KEY_SORT_EXPR})"
        return f"(CASE WHEN {blank} THEN 1 ELSE 0 END) ASC, {value_expr} {order_dir}, id ASC"

    if key in _NUMERIC_SORT_COLUMNS:
        col = _NUMERIC_SORT_COLUMNS[key]
        return f"(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) ASC, {col} {order_dir}, id ASC"

    if key in _RAW_SORT_COLUMNS:
        col = _RAW_SORT_COLUMNS[key]
        return f"{col} {order_dir}, id ASC"

    # Text column (artist/title/filename/genre).
    col = _TEXT_SORT_COLUMNS[key]
    blank = f"TRIM(COALESCE({col}, '')) = ''"
    value_expr = f"LOWER(TRIM({col}))"
    secondary = ""
    if key == "artist":
        # No deliberate pre-existing default beyond "artist asc" -- use a
        # stable Title secondary order per product decision, ahead of the
        # universal id tiebreak.
        secondary = (
            ", (CASE WHEN TRIM(COALESCE(title,'')) = '' THEN 1 ELSE 0 END) ASC"
            ", LOWER(TRIM(COALESCE(title,''))) ASC"
        )
    return f"(CASE WHEN {blank} THEN 1 ELSE 0 END) ASC, {value_expr} {order_dir}{secondary}, id ASC"
_KNOWN_ISSUES = {
    "missing_bpm",
    "missing_key",
    "missing_artist",
    "missing_title",
    "low_quality",
    "error",
    "needs_review",
    "weak_filename_parse",
    "suspicious_artist",
    "suspicious_title",
}
_MAX_TRACK_LIMIT = 500
_POST_FILTER_ISSUES = {"suspicious_artist", "suspicious_title"}


# ---------------------------------------------------------------------------
# list_tracks
# ---------------------------------------------------------------------------

def _path_prefix_clauses(path_str: str) -> tuple[str, list]:
    """
    Build a SQL WHERE clause fragment and params for filepath prefix matching.
    Tries both canonical and /music-symlink forms of the path so it works
    regardless of whether the pipeline stored paths with or without symlink resolution.
    """
    from pathlib import Path as _Path

    p = path_str.rstrip("/")
    prefixes: set[str] = {p + "/"}
    try:
        resolved = str(_Path(path_str).resolve()).rstrip("/")
        prefixes.add(resolved + "/")
    except Exception:
        pass
    canon = str(selected_library_root()).rstrip("/")
    symlink = "/music"
    for pf in list(prefixes):
        base = pf.rstrip("/")
        if base.startswith(canon):
            prefixes.add(symlink + base[len(canon):] + "/")
        elif base.startswith(symlink):
            prefixes.add(canon + base[len(symlink):] + "/")
    pf_list = list(prefixes)
    clause = "(" + " OR ".join(["filepath LIKE ?" for _ in pf_list]) + ")"
    return clause, [pf + "%" for pf in pf_list]


def _track_has_issue(track: Track, issue: Optional[str]) -> bool:
    if not issue:
        return True
    issue = issue.strip().lower()
    if issue not in _KNOWN_ISSUES:
        return False
    return issue in {item.lower() for item in track.issues}


def _apply_post_filters(rows: list[Track], issue: Optional[str]) -> list[Track]:
    if not issue:
        return rows
    return [row for row in rows if _track_has_issue(row, issue)]


def _issue_sql_clause(issue: Optional[str]) -> str | None:
    if not issue:
        return None
    issue = issue.strip().lower()
    if issue not in _KNOWN_ISSUES or issue in _POST_FILTER_ISSUES:
        return None
    clauses = {
        "missing_bpm": "bpm IS NULL",
        "missing_key": "(TRIM(COALESCE(key_camelot,'')) = '' AND TRIM(COALESCE(key_musical,'')) = '')",
        "missing_artist": "TRIM(COALESCE(artist,'')) = ''",
        "missing_title": "TRIM(COALESCE(title,'')) = ''",
        "low_quality": "quality_tier = 'LOW'",
        "error": "status = 'error'",
        "needs_review": "status = 'needs_review'",
        "weak_filename_parse": "UPPER(TRIM(COALESCE(parse_confidence,''))) IN ('MEDIUM', 'LOW')",
    }
    return clauses.get(issue)


def list_tracks(
    *,
    path: Optional[str] = None,
    q: Optional[str] = None,
    status: Optional[str] = None,
    artist: Optional[str] = None,
    genre: Optional[str] = None,
    key: Optional[str] = None,
    quality_tier: Optional[str] = None,
    bpm_min: Optional[float] = None,
    bpm_max: Optional[float] = None,
    has_key: Optional[bool] = None,
    issue: Optional[str] = None,
    parse_confidence: Optional[str] = None,
    storage_zone: Optional[str] = None,
    sort: str = _DEFAULT_SORT,
    order: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Track], int]:
    """
    Return (rows, total_count) for the given filters.

    total_count is the count with filters applied but without limit/offset,
    so callers can build pagination controls.
    """
    if not pipeline_db_exists():
        return [], 0
    limit = max(1, min(int(limit or 100), _MAX_TRACK_LIMIT))
    offset = max(0, int(offset or 0))

    where_clauses: List[str] = []
    params: List[object] = []

    if path:
        try:
            from pathlib import Path as _Path
            root = selected_library_root()
            p = _Path(path).resolve()
            if p == root or root in p.parents:
                clause, pf_params = _path_prefix_clauses(path)
                where_clauses.append(clause)
                params.extend(pf_params)
        except Exception:
            pass

    if q:
        term = f"%{q}%"
        where_clauses.append(
            "(artist LIKE ? OR title LIKE ? OR filename LIKE ?)"
        )
        params.extend([term, term, term])

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if artist:
        where_clauses.append("LOWER(COALESCE(artist,'')) = LOWER(?)")
        params.append(artist)

    if genre:
        where_clauses.append("LOWER(COALESCE(genre,'')) = LOWER(?)")
        params.append(genre)

    if key:
        where_clauses.append(
            "(LOWER(COALESCE(key_camelot,'')) = LOWER(?) OR LOWER(COALESCE(key_musical,'')) = LOWER(?))"
        )
        params.extend([key, key])

    if quality_tier:
        where_clauses.append("quality_tier = ?")
        params.append(quality_tier.upper())

    if parse_confidence:
        where_clauses.append("UPPER(COALESCE(parse_confidence,'')) = ?")
        params.append(parse_confidence.upper())

    if storage_zone:
        # Pre-Cycle-9 DBs may not have this column yet; ensure it exists
        # (idempotent, defaults existing rows to 'LIBRARY') rather than
        # letting the filtered query silently fail closed to empty results.
        from ..core.library_root import selected_library_root as _selected_root
        from . import library_setup_service as _lib_setup
        try:
            _lib_setup.ensure_storage_zone_column(_selected_root())
        except Exception:
            pass
        where_clauses.append("COALESCE(storage_zone, 'LIBRARY') = ?")
        params.append(storage_zone.upper())

    if bpm_min is not None:
        where_clauses.append("bpm >= ?")
        params.append(bpm_min)

    if bpm_max is not None:
        where_clauses.append("bpm <= ?")
        params.append(bpm_max)

    if has_key is True:
        where_clauses.append(
            "(TRIM(COALESCE(key_camelot,'')) != '' OR TRIM(COALESCE(key_musical,'')) != '')"
        )
    elif has_key is False:
        where_clauses.append(
            "(TRIM(COALESCE(key_camelot,'')) = '' AND TRIM(COALESCE(key_musical,'')) = '')"
        )

    issue_clause = _issue_sql_clause(issue)
    post_filter_issue = issue if issue and issue.strip().lower() in _POST_FILTER_ISSUES else None
    if issue and issue.strip().lower() not in _KNOWN_ISSUES:
        return [], 0
    if issue_clause:
        where_clauses.append(issue_clause)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    order_by_sql = _build_order_by(sort, order)

    try:
        with get_pipeline_conn() as conn:
            if post_filter_issue:
                base_rows = conn.execute(
                    f"""SELECT * FROM tracks {where_sql}
                        ORDER BY {order_by_sql}""",
                    params,
                ).fetchall()
                tracks = _apply_post_filters([Track.from_row(r) for r in base_rows], post_filter_issue)
                return tracks[offset: offset + limit], len(tracks)

            total_row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM tracks {where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"""SELECT * FROM tracks {where_sql}
                    ORDER BY {order_by_sql}
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

        return [Track.from_row(r) for r in rows], int(total_row["cnt"] or 0)

    except FileNotFoundError:
        return [], 0
    except Exception as exc:
        log.exception("list_tracks query failed: %s", exc)
        return [], 0


# ---------------------------------------------------------------------------
# get_track
# ---------------------------------------------------------------------------

def get_track(track_id: int) -> Optional[Track]:
    if not pipeline_db_exists():
        return None
    try:
        with get_pipeline_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        return Track.from_row(row) if row else None
    except Exception as exc:
        log.exception("get_track(%s) failed: %s", track_id, exc)
        return None


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

def get_stats(storage_zone: Optional[str] = None) -> TrackStats:
    empty = TrackStats(
        total=0,
        by_status={},
        by_quality={},
        missing_bpm=0,
        missing_key=0,
        missing_artist=0,
        missing_title=0,
    )
    if not pipeline_db_exists():
        return empty
    where_sql = ""
    params: list[object] = []
    if storage_zone:
        try:
            from ..core.library_root import selected_library_root as _selected_root
            from . import library_setup_service as _lib_setup
            _lib_setup.ensure_storage_zone_column(_selected_root())
        except Exception:
            pass
        where_sql = "WHERE COALESCE(storage_zone, 'LIBRARY') = ?"
        params.append(storage_zone.upper())
    try:
        with get_pipeline_conn() as conn:
            agg = conn.execute(
                f"""SELECT
                       COUNT(*)                                                     AS total,
                       SUM(CASE WHEN bpm IS NULL THEN 1 ELSE 0 END)                AS missing_bpm,
                       SUM(CASE WHEN key_camelot IS NULL
                                 AND key_musical IS NULL THEN 1 ELSE 0 END)        AS missing_key,
                       SUM(CASE WHEN TRIM(COALESCE(artist,'')) = ''
                                THEN 1 ELSE 0 END)                                 AS missing_artist,
                       SUM(CASE WHEN TRIM(COALESCE(title,''))  = ''
                                THEN 1 ELSE 0 END)                                 AS missing_title
                   FROM tracks {where_sql}""",
                params,
            ).fetchone()

            by_status: Dict[str, int] = {
                row["status"]: row["cnt"]
                for row in conn.execute(
                    f"SELECT status, COUNT(*) AS cnt FROM tracks {where_sql} GROUP BY status",
                    params,
                ).fetchall()
            }

            by_quality: Dict[str, int] = {
                (row["quality_tier"] or "UNKNOWN"): row["cnt"]
                for row in conn.execute(
                    f"""SELECT COALESCE(quality_tier,'UNKNOWN') AS quality_tier,
                              COUNT(*) AS cnt
                       FROM tracks {where_sql} GROUP BY quality_tier""",
                    params,
                ).fetchall()
            }

        return TrackStats(
            total=agg["total"] or 0,
            by_status=by_status,
            by_quality=by_quality,
            missing_bpm=agg["missing_bpm"] or 0,
            missing_key=agg["missing_key"] or 0,
            missing_artist=agg["missing_artist"] or 0,
            missing_title=agg["missing_title"] or 0,
        )
    except Exception as exc:
        log.exception("get_stats failed: %s", exc)
        return empty


def get_issue_counts(storage_zone: Optional[str] = None) -> dict[str, int]:
    """Return computed issue counts, optionally restricted to one storage zone."""
    counts = {
        "missing_artist": 0,
        "missing_title": 0,
        "weak_filename_parse": 0,
        "suspicious_artist": 0,
        "suspicious_title": 0,
    }
    if not pipeline_db_exists():
        return counts
    try:
        with get_pipeline_conn() as conn:
            if storage_zone:
                where_sql, params = storage_zone_predicate(conn, storage_zone)
                rows = conn.execute(f"SELECT * FROM tracks WHERE {where_sql}", params).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tracks").fetchall()
        for row in rows:
            track = Track.from_row(row)
            issue_set = set(track.issues)
            for key in counts:
                if key in issue_set:
                    counts[key] += 1
        return counts
    except Exception as exc:
        log.exception("get_issue_counts failed: %s", exc)
        return counts


def get_track_by_id(track_id: int) -> Optional[Track]:
    return get_track(track_id)


# ---------------------------------------------------------------------------
# get_track_identity_and_provenance -- read-only, additive detail
# ---------------------------------------------------------------------------

def get_track_identity_and_provenance(track_id: int) -> Dict[str, Any]:
    """
    Read-only additive identity/provenance detail for one track (Metadata
    Model Phase 2). Uses the same read-only pipeline connection as
    `get_track()` -- never creates schema, matching the plain-GET contract:
    absence of recorded provenance/fingerprint data means exactly that,
    never an error.
    """
    from . import field_provenance_service, track_identity_service

    empty: Dict[str, Any] = {"identity": None, "provenance": {}}
    if not pipeline_db_exists():
        return empty
    try:
        with get_pipeline_conn() as conn:
            fingerprint = track_identity_service.get_for_track(track_id, conn=conn)
            provenance = field_provenance_service.current_for_track(track_id, conn=conn)
        identity = {
            "track_id": track_id,
            "fingerprint_available": fingerprint is not None,
            "fingerprint_algorithm": fingerprint["algorithm"] if fingerprint else None,
            "fingerprint_computed_at": fingerprint["computed_at"] if fingerprint else None,
        }
        return {"identity": identity, "provenance": provenance}
    except Exception as exc:
        log.exception("get_track_identity_and_provenance(%s) failed: %s", track_id, exc)
        return empty


# ---------------------------------------------------------------------------
# get_compatible_tracks
# ---------------------------------------------------------------------------

class CompatibleTracksQueryError(RuntimeError):
    """
    Raised when the compatible-tracks DB query fails for a reason other than
    the pipeline database simply not existing yet (that case is a legitimate
    empty result, not a bug). Callers must not mask this as status: "ok" —
    it means the read genuinely failed and should surface as an error.
    """


def _classify_camelot_match(
    from_key: str,
    to_key: str,
    *,
    include_same_key: bool,
    include_adjacent: bool,
) -> Optional[str]:
    """
    Classify a candidate key against the selected track's key.

    Returns "same_key" | "adjacent_key" | "relative_key", or None when the
    candidate falls outside the three supported Camelot relations (or is
    excluded by the include_* flags). Reuses modules.harmonic's wheel-distance
    math so this stays consistent with the existing set-builder scoring.
    """
    if not to_key:
        return None
    dist, switched = camelot_distance(from_key, to_key)
    if dist == 0 and not switched:
        return "same_key" if include_same_key else None
    if dist == 0 and switched:
        return "relative_key"
    if dist == 1 and not switched:
        return "adjacent_key" if include_adjacent else None
    return None


def get_compatible_tracks(
    track_id: int,
    *,
    limit: int = 8,
    bpm_tolerance: float = 6.0,
    include_same_key: bool = True,
    include_adjacent: bool = True,
    genre: Optional[str] = None,
) -> Optional[CompatibleTracksResponse]:
    """
    Return ranked harmonically-compatible tracks for track_id, or None if the
    track itself does not exist (caller should 404).

    Read-only: never scans audio, never mutates tracks/queue state. Camelot
    compatibility is restricted to the three standard mixable relations (same
    key, adjacent wheel position, relative major/minor) per modules.harmonic;
    BPM closeness and genre are read-only scoring/ranking signals reused from
    the same module, not separate inclusion routes.
    """
    track = get_track(track_id)
    if track is None:
        return None

    from_key = (track.key_camelot or "").strip()
    if not from_key:
        return CompatibleTracksResponse(
            track_id=track_id,
            status="missing_key",
            reason="Track has no Camelot key data.",
            items=[],
        )

    limit = max(1, min(int(limit or 8), 25))
    bpm_tolerance = max(1.0, float(bpm_tolerance or 6.0))
    from_bpm = track.bpm
    from_genre = (track.genre or "").strip()

    if not pipeline_db_exists():
        return CompatibleTracksResponse(track_id=track_id, status="ok", items=[])

    where_clauses = ["id != ?", "TRIM(COALESCE(key_camelot, '')) != ''"]
    params: list = [track_id]
    if genre:
        where_clauses.append("LOWER(COALESCE(genre, '')) = LOWER(?)")
        params.append(genre)
    where_sql = "WHERE " + " AND ".join(where_clauses)

    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(f"SELECT * FROM tracks {where_sql}", params).fetchall()
    except FileNotFoundError:
        # No pipeline run yet — a legitimate empty state, not a query failure.
        return CompatibleTracksResponse(track_id=track_id, status="ok", items=[])
    except Exception as exc:
        # A real query/DB failure (e.g. corrupt DB, schema mismatch) must not
        # be reported as "ok, no matches" — that would hide a genuine bug.
        log.exception("get_compatible_tracks(%s) query failed: %s", track_id, exc)
        raise CompatibleTracksQueryError(
            f"Compatible-tracks lookup failed for track {track_id}."
        ) from exc

    ranked: list[tuple[float, float, CompatibleTrackItem]] = []
    for row in rows:
        cand = Track.from_row(row)
        match_type = _classify_camelot_match(
            from_key,
            (cand.key_camelot or "").strip(),
            include_same_key=include_same_key,
            include_adjacent=include_adjacent,
        )
        if match_type is None:
            continue

        bpm_delta: Optional[float] = None
        if from_bpm is not None and cand.bpm is not None:
            bpm_delta = round(abs(cand.bpm - from_bpm), 1)
            if bpm_delta > bpm_tolerance:
                continue

        c_score = camelot_score(from_key, cand.key_camelot or "")
        b_score = bpm_score(from_bpm, cand.bpm) if from_bpm and cand.bpm else 0.5
        g_score = genre_score(from_genre, cand.genre or "")
        composite = round(100 * (0.55 * c_score + 0.30 * b_score + 0.15 * g_score), 1)

        reason_parts = [_MATCH_LABELS[match_type]]
        if bpm_delta is not None and bpm_delta <= min(2.0, bpm_tolerance):
            reason_parts.append("BPM close")
        if from_genre and cand.genre and from_genre.lower() == cand.genre.strip().lower():
            reason_parts.append("Same genre")

        ranked.append((
            composite,
            bpm_delta if bpm_delta is not None else 9999.0,
            CompatibleTrackItem.from_track(
                cand,
                match_type=match_type,
                compatibility_score=composite,
                compatibility_reason=" · ".join(reason_parts),
                bpm_delta=bpm_delta,
            ),
        ))

    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    items = [entry[2] for entry in ranked[:limit]]

    return CompatibleTracksResponse(
        track_id=track_id,
        status="ok",
        reason=None if items else "No compatible tracks found with current matching rules.",
        items=items,
    )


# ---------------------------------------------------------------------------
# get_orphan_stats
# ---------------------------------------------------------------------------

def get_orphan_stats() -> Dict[str, int]:
    """
    Return a lightweight count summary of orphan categories.

    stale_db_rows — non-stale DB rows whose file is missing on disk
    active_rows   — non-stale rows whose file exists on disk
    """
    if not pipeline_db_exists():
        return {"stale_db_rows": 0, "active_rows": 0}
    try:
        from pathlib import Path as _Path
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                "SELECT filepath FROM tracks WHERE status != 'stale'"
            ).fetchall()
        stale = sum(1 for r in rows if not _Path(r["filepath"]).exists())
        return {"stale_db_rows": stale, "active_rows": len(rows) - stale}
    except Exception as exc:
        log.exception("get_orphan_stats failed: %s", exc)
        return {"stale_db_rows": 0, "active_rows": 0}


# ---------------------------------------------------------------------------
# get_issues
# ---------------------------------------------------------------------------

def get_issues(limit: int = 200) -> List[TrackIssueItem]:
    """Return tracks that have at least one issue flag."""
    if not pipeline_db_exists():
        return []
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM tracks
                   WHERE bpm IS NULL
                      OR (key_camelot IS NULL AND key_musical IS NULL)
                      OR TRIM(COALESCE(artist,'')) = ''
                      OR TRIM(COALESCE(title,''))  = ''
                      OR quality_tier = 'LOW'
                      OR TRIM(COALESCE(parse_confidence,'')) IN ('MEDIUM', 'LOW')
                      OR status IN ('error', 'needs_review')
                   ORDER BY
                       CASE status
                           WHEN 'error'        THEN 0
                           WHEN 'needs_review' THEN 1
                           ELSE                     2
                       END,
                       LOWER(COALESCE(artist,''))
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        items: List[TrackIssueItem] = []
        for row in rows:
            t = Track.from_row(row)
            items.append(
                TrackIssueItem(
                    id=t.id,
                    filepath=t.filepath,
                    filename=t.filename,
                    artist=t.artist,
                    title=t.title,
                    status=t.status,
                    issues=t.issues,
                )
            )
        return items
    except Exception as exc:
        log.exception("get_issues failed: %s", exc)
        return []
