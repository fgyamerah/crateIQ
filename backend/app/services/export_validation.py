"""
export_validation — pre-export track validation without spawning subprocesses.

Mirrors the logic in modules/rekordbox_export.py _resolve_tracks() /
_get_exclusion_reasons() / _categorize_exclusion_reasons() but runs entirely
in-process against the pipeline DB using the read-only connection.

No subprocess calls, no analysis recovery — purely structural/metadata checks.
The goal is fast, deterministic results the UI can display before the user
decides to run the real export job.

Exclusion categories:
  MISSING_ANALYSIS  — BPM or Camelot key absent or out of range
  MISSING_METADATA  — artist, title, or genre missing/junk
  STALE_DB          — file not found on disk at DB path
  JUNK_PLACEHOLDER  — filename matches known junk/placeholder pattern
  OTHER             — anything else
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..schemas.export import (
    ExcludedTrack,
    ExportWarning,
    ValidateResponse,
    ValidationStats,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (mirrors modules/rekordbox_export.py and modules/playlists.py)
# ---------------------------------------------------------------------------

UNKNOWN_ARTISTS: frozenset = frozenset({
    "", "unknown", "unknown artist", "va", "various artists",
    "various", "n/a", "none", "-", "--",
})

_RE_VALID_CAM = re.compile(r"^(1[0-2]|[1-9])[AB]$", re.IGNORECASE)

_RE_JUNK_PLACEHOLDER = re.compile(
    r"^unknown(\s*\(\d+\))?\.(mp3|flac|wav|aiff?|m4a|ogg|opus)$",
    re.IGNORECASE,
)

_RE_GENRE_CAMELOT = re.compile(r"^(1[0-2]|[1-9])[AB]$", re.IGNORECASE)
_RE_GENRE_URL     = re.compile(r"https?://|www\.|\.(com|net|org|fm|dj|io)\b", re.IGNORECASE)

_GENRE_JUNK_EXACT: frozenset = frozenset({
    "unknown", "n/a", "na", "none", "null", "test", "promo",
    "various", "various artists", "va", "-", "--", "?", "??",
    "tbc", "tba", "untitled",
    "tukillas", "squeeze", "djcity", "traxsource", "fordjonly",
    "zipdj", "musicafresca", "beatport", "juno", "junodownload",
})

_MAX_EXCLUDED_RETURNED = 500

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_junk_placeholder(fp: str) -> bool:
    name = Path(fp).name
    if not name or name.startswith("."):
        return True
    return bool(_RE_JUNK_PLACEHOLDER.match(name))


def is_junk_genre(name: str) -> bool:
    if not name:
        return True
    v = name.strip()
    if not v or len(v) <= 1:
        return True
    vl = v.lower()
    if vl in _GENRE_JUNK_EXACT:
        return True
    if _RE_GENRE_CAMELOT.match(v):
        return True
    if _RE_GENRE_URL.search(v):
        return True
    return False


def _parse_filename_meta(fp: str) -> Tuple[str, str]:
    """Best-effort artist/title from 'Artist - Title.ext' filename."""
    stem = Path(fp).stem
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return "", stem.strip()


def _primary_category(reasons: List[str]) -> str:
    """
    Pick the highest-priority category from a list of tagged reason strings.
    Priority: MISSING_ANALYSIS > MISSING_METADATA > STALE_DB > JUNK_PLACEHOLDER > BAD_PATH > OTHER
    """
    for r in reasons:
        if "[MISSING_ANALYSIS]" in r:
            return "MISSING_ANALYSIS"
    for r in reasons:
        if "[MISSING_METADATA]" in r:
            return "MISSING_METADATA"
    for r in reasons:
        if "[STALE_DB]" in r:
            return "STALE_DB"
    for r in reasons:
        if "[JUNK_PLACEHOLDER]" in r:
            return "JUNK_PLACEHOLDER"
    for r in reasons:
        if "[BAD_PATH]" in r:
            return "BAD_PATH"
    return "OTHER"


def _categorize_reasons(raw: List[str]) -> List[str]:
    """Prefix each raw reason string with its [CATEGORY] tag."""
    result = []
    for r in raw:
        rl = r.lower()
        if "missing bpm" in rl or "missing/invalid camelot" in rl or "bpm out of range" in rl or "non-numeric bpm" in rl:
            result.append(f"[MISSING_ANALYSIS] {r}")
        elif "missing" in rl or "unknown artist" in rl or "junk genre" in rl:
            result.append(f"[MISSING_METADATA] {r}")
        else:
            result.append(f"[BAD_PATH] {r}")
    return result or ["[OTHER] unknown reason"]


def _validate_row(row) -> List[str]:
    """
    Return a list of categorized exclusion reasons for one DB row.
    Returns an empty list if the track is valid for export.

    Does NOT do file-existence checks (those are done separately to avoid
    per-row I/O in the hot path when we already know the file exists).
    """
    fp     = str(row["filepath"])
    artist = (row["artist"] or "").strip()
    title  = (row["title"]  or "").strip()
    bpm    = row["bpm"]
    key    = (row["key_camelot"] or "").strip()
    genre  = (row["genre"] or "").strip()

    # Attempt filename fallback for missing artist/title (no I/O)
    if not artist or artist.lower() in UNKNOWN_ARTISTS or not title:
        fb_artist, fb_title = _parse_filename_meta(fp)
        if not title and fb_title:
            title = fb_title
        if (not artist or artist.lower() in UNKNOWN_ARTISTS) and fb_artist:
            artist = fb_artist

    raw: List[str] = []

    # Artist
    if not artist or artist.lower() in UNKNOWN_ARTISTS:
        raw.append(f"missing/unknown artist: '{artist}' (filename fallback failed)")

    # Title
    if not title:
        raw.append("missing title (filename fallback failed)")

    # BPM
    if not bpm:
        raw.append("missing BPM — run analyze-missing to fix")
    else:
        try:
            bpm_f = float(bpm)
            if not (50.0 <= bpm_f <= 220.0):
                raw.append(f"BPM out of range: {bpm_f:.1f} (valid: 50–220)")
        except (TypeError, ValueError):
            raw.append(f"non-numeric BPM: '{bpm}'")

    # Camelot key
    if not key or not _RE_VALID_CAM.match(key):
        raw.append(
            f"missing/invalid Camelot key: '{key}' — run analyze-missing to fix"
            if not key else
            f"invalid Camelot key format: '{key}'"
        )

    # Genre
    if not genre:
        raw.append("missing genre tag")
    elif is_junk_genre(genre):
        raw.append(f"junk genre: '{genre}'")

    if not raw:
        return []
    return _categorize_reasons(raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_validation() -> ValidateResponse:
    """
    Validate all OK tracks from the pipeline DB against export requirements.

    Returns a ValidateResponse with full stats, warnings, and the list of
    excluded tracks (capped at 500).
    """
    if not pipeline_db_exists():
        return ValidateResponse(
            stats=ValidationStats(
                total_scanned=0, valid_count=0, invalid_count=0,
                missing_analysis=0, missing_metadata=0,
                stale_db=0, junk=0, other=0,
                by_category={},
            ),
            warnings=[ExportWarning(
                level="error",
                message="Pipeline database not found. Run the pipeline at least once to populate the library.",
            )],
            excluded=[],
            truncated=False,
            output_paths=_output_paths(),
        )

    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                "SELECT filepath, artist, title, bpm, key_camelot, genre, status "
                "FROM tracks WHERE status = 'ok' ORDER BY artist, title"
            ).fetchall()
    except Exception as exc:
        log.exception("export_validation: DB read failed: %s", exc)
        return ValidateResponse(
            stats=ValidationStats(
                total_scanned=0, valid_count=0, invalid_count=0,
                missing_analysis=0, missing_metadata=0,
                stale_db=0, junk=0, other=0,
                by_category={},
            ),
            warnings=[ExportWarning(
                level="error",
                message=f"Failed to read pipeline database: {exc}",
            )],
            excluded=[],
            truncated=False,
            output_paths=_output_paths(),
        )

    total_scanned = len(rows)
    excluded_list: List[ExcludedTrack] = []
    cat_counts: Dict[str, int] = {}
    valid_count = 0

    for row in rows:
        fp = str(row["filepath"])

        # 1. Junk placeholder
        if _is_junk_placeholder(fp):
            reasons = ["[JUNK_PLACEHOLDER] filename matches known junk/placeholder pattern"]
            _record_excluded(excluded_list, cat_counts, row, reasons)
            continue

        # 2. Stale DB — file not found on disk
        if not Path(fp).exists():
            reasons = ["[STALE_DB] file not found at DB path (drive may not be mounted)"]
            _record_excluded(excluded_list, cat_counts, row, reasons)
            continue

        # 3. Metadata + analysis checks
        reasons = _validate_row(row)
        if reasons:
            _record_excluded(excluded_list, cat_counts, row, reasons)
        else:
            valid_count += 1

    invalid_count = total_scanned - valid_count

    stats = ValidationStats(
        total_scanned=total_scanned,
        valid_count=valid_count,
        invalid_count=invalid_count,
        missing_analysis=cat_counts.get("MISSING_ANALYSIS", 0),
        missing_metadata=cat_counts.get("MISSING_METADATA", 0),
        stale_db=cat_counts.get("STALE_DB", 0),
        junk=cat_counts.get("JUNK_PLACEHOLDER", 0),
        other=cat_counts.get("OTHER", 0) + cat_counts.get("BAD_PATH", 0),
        by_category=dict(cat_counts),
    )

    warnings = _build_warnings(stats, total_scanned)

    truncated = len(excluded_list) > _MAX_EXCLUDED_RETURNED
    if truncated:
        excluded_list = excluded_list[:_MAX_EXCLUDED_RETURNED]

    return ValidateResponse(
        stats=stats,
        warnings=warnings,
        excluded=excluded_list,
        truncated=truncated,
        output_paths=_output_paths(),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _record_excluded(
    excluded_list: List[ExcludedTrack],
    cat_counts: Dict[str, int],
    row,
    reasons: List[str],
) -> None:
    fp = str(row["filepath"])
    bpm_val: Optional[float] = None
    try:
        bpm_val = float(row["bpm"]) if row["bpm"] else None
    except (TypeError, ValueError):
        pass

    cat = _primary_category(reasons)
    cat_counts[cat] = cat_counts.get(cat, 0) + 1

    excluded_list.append(ExcludedTrack(
        filepath    = fp,
        filename    = Path(fp).name,
        artist      = (row["artist"] or "").strip() or None,
        title       = (row["title"]  or "").strip() or None,
        bpm         = bpm_val,
        key_camelot = (row["key_camelot"] or "").strip() or None,
        genre       = (row["genre"] or "").strip() or None,
        reasons     = reasons,
        category    = cat,
    ))


def _build_warnings(stats: ValidationStats, total: int) -> List[ExportWarning]:
    warnings: List[ExportWarning] = []

    if total == 0:
        warnings.append(ExportWarning(
            level="error",
            message="No processed tracks found in the library. "
                    "Run the pipeline first to populate the database.",
        ))
        return warnings

    if stats.valid_count == 0:
        warnings.append(ExportWarning(
            level="error",
            message=f"No tracks would be exported — all {total} tracks are excluded. "
                    "Check the exclusion reasons below.",
        ))

    if stats.missing_analysis > 0:
        pct = 100.0 * stats.missing_analysis / max(total, 1)
        warnings.append(ExportWarning(
            level="warning",
            message=(
                f"{stats.missing_analysis} track(s) ({pct:.1f}%) have no BPM or Camelot key "
                "and will be excluded from export. "
                "Run 'analyze-missing' first, or enable Recover Missing Analysis on the run."
            ),
        ))

    if stats.stale_db > 0:
        warnings.append(ExportWarning(
            level="warning",
            message=(
                f"{stats.stale_db} track(s) have stale DB paths — "
                "files not found on disk. Check that the SSD is mounted at the expected path."
            ),
        ))

    if stats.junk > 0:
        warnings.append(ExportWarning(
            level="info",
            message=(
                f"{stats.junk} track(s) have placeholder/junk filenames "
                "(e.g. 'unknown.mp3') and will be excluded."
            ),
        ))

    if stats.missing_metadata > 0:
        warnings.append(ExportWarning(
            level="warning",
            message=(
                f"{stats.missing_metadata} track(s) have missing or junk metadata "
                "(artist, title, or genre). "
                "Run 'metadata-clean' or fix tags manually."
            ),
        ))

    excl_pct = 100.0 * stats.invalid_count / max(total, 1)
    if stats.valid_count > 0 and excl_pct > 10:
        warnings.append(ExportWarning(
            level="info",
            message=f"{excl_pct:.1f}% of tracks ({stats.invalid_count}) will be excluded from this export.",
        ))

    return warnings


def _output_paths() -> Dict[str, str]:
    """
    Return the configured output paths for context display.
    Read from toolkit config if available; fall back to hard-coded defaults.
    """
    try:
        import importlib.util
        from pathlib import Path as _Path
        _here = _Path(__file__).parent
        _root = _here.parents[2]  # backend/app/services -> backend -> djtoolkit
        spec = importlib.util.spec_from_file_location("_tk_cfg", str(_root / "config.py"))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return {
                "m3u":    str(getattr(mod, "REKORDBOX_M3U_EXPORT_DIR", "/mnt/music_ssd/KKDJ/_PLAYLISTS_M3U_EXPORT")),
                "xml":    str(getattr(mod, "REKORDBOX_XML_EXPORT_DIR", "/mnt/music_ssd/KKDJ/_REKORDBOX_XML_EXPORT")),
                "log":    str(getattr(mod, "LOGS_DIR",                  "/music/logs") + "/rekordbox_export/invalid_tracks.txt"),
            }
    except Exception:
        pass
    return {
        "m3u": "/mnt/music_ssd/KKDJ/_PLAYLISTS_M3U_EXPORT",
        "xml": "/mnt/music_ssd/KKDJ/_REKORDBOX_XML_EXPORT",
        "log": "/music/logs/rekordbox_export/invalid_tracks.txt",
    }
