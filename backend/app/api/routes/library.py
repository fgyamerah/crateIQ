"""
Library tree + stats routes.

GET /api/library/tree                                    — directory tree under the selected library root
GET /api/library/stats                                   — global + folder-scoped track counts
GET /api/library/runs                                    — list recent pipeline run summaries
GET /api/library/runs/{command}/{prefix}/summary         — full run summary JSON
GET /api/library/runs/{command}/{prefix}/detail/{g}/{p} — paged detail file (modified/skipped/errors)
"""
from __future__ import annotations

import json as _json
import re as _re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Logs directory — try config first, fall back to project root /logs
try:
    from ...core.config import LOGS_DIR as _LOGS_DIR  # type: ignore[attr-defined]
except (ImportError, AttributeError):
    _LOGS_DIR: Path = Path(__file__).resolve().parents[4] / "logs"

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...core.library_root import selected_library_root
from ...core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ...schemas.library_readiness import LibraryReadinessResponse
from ...services import library_readiness_service, read_only as read_only_service
from ...services import track_service
from modules import metadata_repair, metadata_sanitation

router = APIRouter(tags=["library"])

# Directories under the current, workspace-selected library root (see
# core.library_root.selected_library_root — not the legacy pipeline.py
# config.MUSIC_ROOT) that contain no audio files and should not appear as
# job targets. Keep this list small and explicit.
_SKIP_ROOT_NAMES: frozenset = frozenset({
    "logs",
    "data",
    "playlists",
    "_PLAYLISTS_M3U_EXPORT",
    "_REKORDBOX_XML_EXPORT",
    "processing",   # transient working dir — not a stable target
    "__pycache__",
    ".git",
})


class LibraryNode(BaseModel):
    label:      str
    path:       str
    executable: bool = True   # all returned nodes are real filesystem paths
    children:   List["LibraryNode"] = []

LibraryNode.model_rebuild()


class LibraryTreeResponse(BaseModel):
    root: LibraryNode


def _safe_iterdir(directory: Path) -> list[Path]:
    """List immediate subdirectories, sorted case-insensitively.  Never raises."""
    try:
        return sorted(
            [p for p in directory.iterdir() if p.is_dir()],
            key=lambda p: p.name.lower(),
        )
    except (PermissionError, OSError):
        return []


def _build_node(directory: Path, current_depth: int, max_depth: int) -> LibraryNode:
    node = LibraryNode(label=directory.name, path=str(directory))
    if current_depth >= max_depth:
        return node
    for child in _safe_iterdir(directory):
        node.children.append(_build_node(child, current_depth + 1, max_depth))
    return node


def _build_tree(max_depth: int) -> LibraryNode:
    """
    Build the library tree rooted at the selected library root (current
    workspace model — see core.library_root.selected_library_root).

    Top-level non-audio directories are excluded.  All other directories,
    including library sub-categories and inbox sub-categories, are included.
    The 'sorted' directory is shown but its letter-based children are only
    included if max_depth allows.
    """
    library_root = selected_library_root()
    root = LibraryNode(label="KKDJ", path=str(library_root))

    for entry in _safe_iterdir(library_root):
        if entry.name in _SKIP_ROOT_NAMES:
            continue
        child = _build_node(entry, 1, max_depth)
        root.children.append(child)

    return root


# ---------------------------------------------------------------------------
# GET /api/library/stats
# ---------------------------------------------------------------------------

class LibraryStatsResponse(BaseModel):
    global_count: int
    folder_count: int


class FolderStatItem(BaseModel):
    folder: str
    track_count: int
    issue_count: int


class LibraryOverviewResponse(BaseModel):
    total_tracks: int
    tracks_with_bpm: int
    tracks_with_camelot_key: int
    tracks_analyzed: int
    tracks_missing_artist: int
    tracks_missing_title: int
    parse_confidence_breakdown: Dict[str, int]
    genre_top_counts: List[Dict[str, Any]]


class QualityQueueSummary(BaseModel):
    queue_total: int
    pending: int
    approved: int
    partial: int
    applied: int
    no_op: int
    by_confidence: Dict[str, int]


class QualityCoverageResponse(BaseModel):
    with_artist: int
    with_title: int
    with_bpm: int
    with_camelot: int
    with_genre: int


class QualityActionItem(BaseModel):
    label: str
    reason: str
    target: str


class LibraryQualityResponse(BaseModel):
    total_tracks: int
    issue_total: int
    issues_by_type: Dict[str, int]
    metadata_repair: QualityQueueSummary
    metadata_sanitation: QualityQueueSummary
    coverage: QualityCoverageResponse
    recommended_next_actions: List[QualityActionItem]


def _filepath_prefixes(path_str: str) -> list[str]:
    """
    Return all filepath prefix variants to match against.

    The pipeline DB may store filepaths with the selected library root as either:
    - the resolved canonical path  (/home/user/Music/music/...)
    - the /music symlink            (/music/...)
    We try both so the LIKE query works regardless of how the pipeline was run.
    """
    p = path_str.rstrip("/")
    prefixes: set[str] = {p + "/"}
    try:
        resolved = str(Path(path_str).resolve()).rstrip("/")
        prefixes.add(resolved + "/")
    except Exception:
        pass
    # Substitute between canonical root and /music symlink
    canon = str(selected_library_root()).rstrip("/")
    symlink = "/music"
    for pf in list(prefixes):
        base = pf.rstrip("/")
        if base.startswith(canon):
            prefixes.add(symlink + base[len(canon):] + "/")
        elif base.startswith(symlink):
            prefixes.add(canon + base[len(symlink):] + "/")
    return list(prefixes)


def _is_safe_path(path_str: str) -> bool:
    """Accept only paths that resolve inside the selected library root."""
    try:
        p = Path(path_str).resolve()
        root = selected_library_root()
        return p == root or root in p.parents
    except Exception:
        return False


@router.get("/library/stats", response_model=LibraryStatsResponse)
async def get_library_stats(
    path: Optional[str] = Query(default=None, description="Directory path to scope the count"),
) -> LibraryStatsResponse:
    """
    Return global track count and, if a path is given, a folder-scoped count.

    Handles both canonical and /music-symlink filepath forms in the DB.
    """
    empty = LibraryStatsResponse(global_count=0, folder_count=0)
    if not pipeline_db_exists():
        return empty
    try:
        with get_pipeline_conn() as conn:
            global_count: int = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            folder_count = global_count

            if path and _is_safe_path(path):
                prefixes = _filepath_prefixes(path)
                placeholders = " OR ".join(["filepath LIKE ?" for _ in prefixes])
                row = conn.execute(
                    f"SELECT COUNT(*) FROM tracks WHERE {placeholders}",
                    [pf + "%" for pf in prefixes],
                ).fetchone()
                folder_count = row[0] if row else 0

        return LibraryStatsResponse(global_count=global_count, folder_count=folder_count)
    except Exception:
        return empty


# ---------------------------------------------------------------------------
# GET /api/library/folders
# ---------------------------------------------------------------------------

@router.get("/library/folders", response_model=List[FolderStatItem])
async def get_library_folders() -> List[FolderStatItem]:
    return [FolderStatItem(**item) for item in read_only_service.list_folder_stats()]


# ---------------------------------------------------------------------------
# GET /api/library/overview
# ---------------------------------------------------------------------------

@router.get("/library/overview", response_model=LibraryOverviewResponse)
async def get_library_overview() -> LibraryOverviewResponse:
    return LibraryOverviewResponse(**read_only_service.build_overview_payload())


def _quality_queue_summary(payload: dict[str, Any]) -> dict[str, Any]:
    counts = payload.get("counts", {}) if isinstance(payload, dict) else {}
    by_confidence = counts.get("by_confidence", {}) if isinstance(counts, dict) else {}
    return {
        "queue_total": int(payload.get("queue_total", 0) or 0),
        "pending": int(payload.get("pending_count", 0) or 0),
        "approved": int(payload.get("approved_count", 0) or 0),
        "partial": int(payload.get("partial_count", 0) or 0) + int(payload.get("partial_applied_count", 0) or 0),
        "applied": int(payload.get("applied_count", 0) or 0),
        "no_op": int(payload.get("no_op_count", 0) or 0),
        "by_confidence": {
            "HIGH": int(by_confidence.get("HIGH", 0) or 0),
            "MEDIUM": int(by_confidence.get("MEDIUM", 0) or 0),
            "LOW": int(by_confidence.get("LOW", 0) or 0),
        },
    }


def _quality_coverage_payload(total_tracks: int, overview: dict[str, Any]) -> dict[str, int]:
    with_bpm = int(overview.get("tracks_with_bpm", 0) or 0)
    with_camelot = int(overview.get("tracks_with_camelot_key", 0) or 0)
    missing_artist = int(overview.get("tracks_missing_artist", 0) or 0)
    missing_title = int(overview.get("tracks_missing_title", 0) or 0)
    with_artist = max(0, total_tracks - missing_artist)
    with_title = max(0, total_tracks - missing_title)
    with_genre = 0
    if pipeline_db_exists():
        try:
            with get_pipeline_conn() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM tracks WHERE TRIM(COALESCE(genre,'')) != ''"
                ).fetchone()
            with_genre = int(row["cnt"] or 0) if row else 0
        except Exception:
            with_genre = 0
    return {
        "with_artist": with_artist,
        "with_title": with_title,
        "with_bpm": with_bpm,
        "with_camelot": with_camelot,
        "with_genre": with_genre,
    }


def _quality_actions(
    *,
    issue_counts: dict[str, int],
    repair_summary: dict[str, Any],
    sanitation_summary: dict[str, Any],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    sanitation_high = int(sanitation_summary.get("by_confidence", {}).get("HIGH", 0) or 0)
    repair_high = int(repair_summary.get("by_confidence", {}).get("HIGH", 0) or 0)
    weak_parse = int(issue_counts.get("weak_filename_parse", 0) or 0)
    missing_artist = int(issue_counts.get("missing_artist", 0) or 0)
    missing_title = int(issue_counts.get("missing_title", 0) or 0)
    suspicious_artist = int(issue_counts.get("suspicious_artist", 0) or 0)
    suspicious_title = int(issue_counts.get("suspicious_title", 0) or 0)

    if sanitation_high:
        actions.append({
            "label": "Review HIGH metadata sanitation proposals",
            "reason": f"{sanitation_high} high-confidence sanitation proposal(s) are ready.",
            "target": "/metadata-sanitation",
        })
    if repair_high:
        actions.append({
            "label": "Review HIGH metadata repair proposals",
            "reason": f"{repair_high} high-confidence metadata repair proposal(s) are ready.",
            "target": "/metadata-repair",
        })
    if missing_artist or missing_title:
        actions.append({
            "label": "Investigate missing metadata",
            "reason": f"{missing_artist} missing artist and {missing_title} missing title issue(s) remain.",
            "target": "/issues",
        })
    if suspicious_artist or suspicious_title:
        actions.append({
            "label": "Review suspicious metadata issues",
            "reason": f"{suspicious_artist + suspicious_title} suspicious artist/title issue(s) remain.",
            "target": "/issues",
        })
    if weak_parse:
        actions.append({
            "label": "Review weak filename parses",
            "reason": f"{weak_parse} track(s) still have weak filename parsing.",
            "target": "/issues",
        })
    if not actions:
        actions.append({
            "label": "Open Library",
            "reason": "No active cleanup actions are queued right now.",
            "target": "/",
        })
    return actions[:4]


@router.get("/library/quality", response_model=LibraryQualityResponse)
async def get_library_quality() -> LibraryQualityResponse:
    overview = read_only_service.build_overview_payload()
    total_tracks = int(overview.get("total_tracks", 0) or 0)
    issue_counts = track_service.get_issue_counts()
    issue_total = sum(int(issue_counts.get(key, 0) or 0) for key in (
        "missing_artist",
        "missing_title",
        "suspicious_artist",
        "suspicious_title",
        "weak_filename_parse",
    ))
    try:
        root = selected_library_root()
    except Exception:
        root = None
    if root is not None:
        repair_summary = metadata_repair.summary(root)
        sanitation_summary = metadata_sanitation.summary(root)
    else:
        repair_summary = metadata_repair.summary(Path("/nonexistent"))
        sanitation_summary = metadata_sanitation.summary(Path("/nonexistent"))

    response = {
        "total_tracks": total_tracks,
        "issue_total": issue_total,
        "issues_by_type": {
            "missing_artist": int(issue_counts.get("missing_artist", 0) or 0),
            "missing_title": int(issue_counts.get("missing_title", 0) or 0),
            "suspicious_artist": int(issue_counts.get("suspicious_artist", 0) or 0),
            "suspicious_title": int(issue_counts.get("suspicious_title", 0) or 0),
            "weak_filename_parse": int(issue_counts.get("weak_filename_parse", 0) or 0),
        },
        "metadata_repair": _quality_queue_summary(repair_summary),
        "metadata_sanitation": _quality_queue_summary(sanitation_summary),
        "coverage": _quality_coverage_payload(total_tracks, overview),
        "recommended_next_actions": _quality_actions(
            issue_counts=issue_counts,
            repair_summary=repair_summary,
            sanitation_summary=sanitation_summary,
        ),
    }
    return LibraryQualityResponse(**response)


# ---------------------------------------------------------------------------
# GET /api/library/readiness
# ---------------------------------------------------------------------------

@router.get("/library/readiness", response_model=LibraryReadinessResponse)
async def get_library_readiness() -> LibraryReadinessResponse:
    """Conservative, explainable "ready for crates" readiness contract (Cycle 8)."""
    return LibraryReadinessResponse(**library_readiness_service.build_readiness())


# ---------------------------------------------------------------------------
# Run results — Pydantic models
# ---------------------------------------------------------------------------

class RunListItem(BaseModel):
    prefix:     str
    command:    str
    started_at: Optional[str] = None
    label:      str


class RunSummary(BaseModel):
    prefix:           str
    command:          str
    started_at:       Optional[str] = None
    finished_at:      Optional[str] = None
    duration:         Optional[float] = None
    files_scanned:    Optional[int] = None
    files_processed:  Optional[int] = None
    changed:          Optional[int] = None
    skipped:          Optional[int] = None
    errors:           Optional[int] = None
    review_count:     Optional[int] = None
    moved_to_ignored: Optional[int] = None
    detail_groups:    Dict[str, List[str]] = {}


class RunDetailEntry(BaseModel):
    filepath: Optional[str] = None
    reason:   Optional[str] = None
    details:  Optional[Any] = None


# ---------------------------------------------------------------------------
# Run results — helpers
# ---------------------------------------------------------------------------

def _valid_slug(s: str) -> bool:
    return bool(_re.match(r'^[a-zA-Z0-9_-]+$', s))


def _valid_prefix(s: str) -> bool:
    return bool(_re.match(r'^[a-zA-Z0-9_.-]+$', s))


def _logs_path(command: str, filename: str) -> Optional[Path]:
    """Return resolved path only if it stays within _LOGS_DIR/command."""
    cmd_dir = (_LOGS_DIR / command).resolve()
    try:
        candidate = (cmd_dir / filename).resolve()
        candidate.relative_to(cmd_dir)
        return candidate
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------------------
# GET /api/library/runs
# ---------------------------------------------------------------------------

@router.get("/library/runs", response_model=List[RunListItem])
async def list_runs(
    command: Optional[str] = Query(default=None),
    limit:   int           = Query(default=20, ge=1, le=100),
) -> List[RunListItem]:
    """List recent pipeline runs sorted by start time, newest first."""
    if not _LOGS_DIR.exists():
        return []

    cmd_dirs: List[Path] = []
    if command:
        if not _valid_slug(command):
            raise HTTPException(status_code=400, detail="Invalid command name")
        d = _LOGS_DIR / command
        if d.is_dir():
            cmd_dirs = [d]
    else:
        try:
            cmd_dirs = [d for d in _LOGS_DIR.iterdir() if d.is_dir()]
        except (PermissionError, OSError):
            return []

    items: List[RunListItem] = []
    for cmd_dir in cmd_dirs:
        try:
            for sf in sorted(
                cmd_dir.glob("*_summary.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            ):
                prefix = sf.name[: -len("_summary.json")]
                try:
                    started_at = _json.loads(sf.read_text("utf-8")).get("started_at")
                except Exception:
                    started_at = None
                items.append(RunListItem(
                    prefix=prefix,
                    command=cmd_dir.name,
                    started_at=started_at,
                    label=f"{cmd_dir.name} · {prefix}",
                ))
        except (PermissionError, OSError):
            continue

    items.sort(key=lambda r: r.started_at or "", reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# GET /api/library/runs/{command}/{prefix}/summary
# ---------------------------------------------------------------------------

@router.get("/library/runs/{command}/{prefix}/summary", response_model=RunSummary)
async def get_run_summary(command: str, prefix: str) -> RunSummary:
    if not _valid_slug(command):
        raise HTTPException(status_code=400, detail="Invalid command")
    if not _valid_prefix(prefix):
        raise HTTPException(status_code=400, detail="Invalid prefix")

    p = _logs_path(command, f"{prefix}_summary.json")
    if p is None or not p.exists():
        raise HTTPException(status_code=404, detail="Summary not found")

    try:
        data: Dict[str, Any] = _json.loads(p.read_text("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    detail_groups: Dict[str, List[str]] = {}
    cmd_dir = _LOGS_DIR / command
    for group in ("modified", "skipped", "errors"):
        pages = []
        for pg in sorted(cmd_dir.glob(f"{prefix}_{group}_*.json")):
            m = _re.search(r'_(\d+)\.json$', pg.name)
            if m:
                pages.append(m.group(1))
        if pages:
            detail_groups[group] = pages

    return RunSummary(
        prefix=prefix,
        command=command,
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        duration=data.get("duration"),
        files_scanned=data.get("files_scanned"),
        files_processed=data.get("files_processed"),
        changed=data.get("changed"),
        skipped=data.get("skipped"),
        errors=data.get("errors"),
        review_count=data.get("review_count"),
        moved_to_ignored=data.get("moved_to_ignored"),
        detail_groups=detail_groups,
    )


# ---------------------------------------------------------------------------
# GET /api/library/runs/{command}/{prefix}/detail/{group}/{page}
# ---------------------------------------------------------------------------

@router.get(
    "/library/runs/{command}/{prefix}/detail/{group}/{page}",
    response_model=List[RunDetailEntry],
)
async def get_run_detail(
    command: str,
    prefix:  str,
    group:   str,
    page:    str,
) -> List[RunDetailEntry]:
    if not _valid_slug(command):
        raise HTTPException(status_code=400, detail="Invalid command")
    if not _valid_prefix(prefix):
        raise HTTPException(status_code=400, detail="Invalid prefix")
    if group not in ("modified", "skipped", "errors"):
        raise HTTPException(status_code=400, detail="Invalid group")
    if not _re.match(r'^\d{1,6}$', page):
        raise HTTPException(status_code=400, detail="Invalid page")

    p = _logs_path(command, f"{prefix}_{group}_{page}.json")
    if p is None or not p.exists():
        raise HTTPException(status_code=404, detail="Detail file not found")

    try:
        raw = _json.loads(p.read_text("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="Expected JSON array")

    entries: List[RunDetailEntry] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(RunDetailEntry(
                filepath=item.get("filepath") or item.get("path") or item.get("file"),
                reason=item.get("reason"),
                details=item.get("details") or item.get("changes") or item.get("error"),
            ))
        elif isinstance(item, str):
            entries.append(RunDetailEntry(filepath=item))
    return entries


# ---------------------------------------------------------------------------
# Library tree
# ---------------------------------------------------------------------------

@router.get("/library/tree", response_model=LibraryTreeResponse)
async def get_library_tree(
    depth: int = Query(
        default=2,
        ge=1,
        le=4,
        description=(
            "How many directory levels to expand below the library root. "
            "depth=1 shows only top-level folders; depth=2 expands inbox/* and library/*."
        ),
    ),
) -> LibraryTreeResponse:
    """
    Return the audio library folder tree rooted at the selected library root.

    Every returned node is a real filesystem directory.  Its `path` field can
    be passed directly as --input to metadata-sanitize and other pipeline jobs.
    Non-audio directories (logs, playlists, exports) are excluded.
    """
    return LibraryTreeResponse(root=_build_tree(max_depth=depth))
