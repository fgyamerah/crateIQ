"""
Managed music workspace (Cycle 9).

A managed workspace physically separates a selected library root into three
zones:

    <root>/Inbox/        tracks being prepared, not yet promoted
    <root>/Library/      promoted, finished music (Genre/Artist/Title.ext)
    <root>/Quarantine/   reserved; never an automatic destination

This is additive to the existing "legacy direct library" model (a root the
user already points CrateIQ at directly, with audio files scanned in place
via library_setup_service.import_previewed_library). Configuring a managed
workspace never moves, renames, or reorganizes files that already exist in a
legacy root -- see configure_workspace() for the exact safety gate.

A workspace is distinguished from an arbitrary directory by a small marker
file, .crateiq-workspace.json, written only by configure_workspace().
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from . import field_provenance_service, library_setup_service, tag_write_service, track_service
from ..core.library_root import assert_path_under_root, assert_safe_new_root_path
from ..core.preflight import redact_path
from ..models.track import Track

WORKSPACE_MARKER_NAME = ".crateiq-workspace.json"
WORKSPACE_MARKER_VERSION = 1

_ZONE_DIRS = ("Inbox", "Library", "Quarantine")
_CRATEIQ_OWNED_TOP_LEVEL = {
    "Inbox", "Library", "Quarantine", "logs", "exports", "data", ".run",
    WORKSPACE_MARKER_NAME,
}
_MAX_IMPORT_FILES = 2000
_SAMPLE_LIMIT = 20

WorkspaceState = Literal["managed_workspace", "legacy_direct_library", "not_configured"]


def _audio_extensions() -> set[str]:
    """Resolve lazily so settings/sync/workspace imports cannot form a cycle."""
    return library_setup_service._AUDIO_EXTENSIONS


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

_UNSAFE_SEGMENT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CONTROL_CHAR_RE = re.compile(r'[\x00-\x1f]')
_RESERVED_WINDOWS_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
_MAX_METADATA_FIELD_LENGTH = 200
_MAX_COMMENT_LENGTH = 1000
_MAX_BULK_EDIT_TRACKS = 200
_BULK_METADATA_OPERATIONS = {
    "genre": {"leave", "set", "clear"},
    "comment": {"leave", "set", "append", "clear"},
    "label": {"leave", "set", "clear"},
}
_BULK_FORBIDDEN_IDENTITY_FIELDS = {"artist", "title", "filename"}


def safe_path_segment(value: str | None, fallback: str) -> str:
    """
    Normalize free-text metadata (genre, artist, title) into one filesystem
    path segment safe on both Linux and the Windows-compatible DJ drive this
    library is ultimately exported to.

    Blocks path separators, NUL/control characters, traversal ('.', '..'),
    and Windows-reserved trailing dots/spaces. Preserves meaningful Unicode.
    """
    text = unicodedata.normalize("NFC", value or "").strip()
    text = _UNSAFE_SEGMENT_CHARS.sub("_", text)
    text = text.replace("..", "_")
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text or text in {".", ".."}:
        return fallback
    return text[:120]


# ---------------------------------------------------------------------------
# Workspace state
# ---------------------------------------------------------------------------

def _marker_path(root: Path) -> Path:
    return root / WORKSPACE_MARKER_NAME


def _zone_dir(root: Path, zone: str) -> Path:
    return assert_path_under_root(root / zone, root)


def _require_initialized_db(root: Path) -> Path:
    """
    Resolve the processed.db path, failing closed with a clear ValueError
    rather than letting sqlite3.connect() silently create an empty DB file
    (or crash with an unhandled OperationalError on "no such table") when a
    root has no local index yet.
    """
    db_path = assert_path_under_root(root / "logs" / "processed.db", root)
    if not db_path.is_file():
        raise ValueError("Configure and initialize the managed workspace before previewing promotion.")
    return db_path


def _has_audio_directly_present(root: Path) -> bool:
    """Non-recursive legacy-library signal: audio files sitting directly under root."""
    try:
        for entry in root.iterdir():
            if entry.is_file() and entry.suffix.lower() in _audio_extensions():
                return True
    except OSError:
        return False
    return False


def _has_foreign_top_level_dirs(root: Path) -> bool:
    """True if root has non-CrateIQ-owned subdirectories (legacy library folders)."""
    try:
        for entry in root.iterdir():
            if entry.is_dir() and entry.name not in _CRATEIQ_OWNED_TOP_LEVEL:
                return True
    except OSError:
        return False
    return False


def workspace_state(root: Path) -> dict[str, Any]:
    """
    Read-only classification of a library root. Never creates or modifies
    anything on disk.
    """
    if not root.is_dir():
        return {
            "state": "not_configured",
            "library_root": redact_path(root),
            "inbox_path": None,
            "library_path": None,
            "quarantine_path": None,
            "marker_version": None,
            "message": "The configured library root does not exist yet.",
        }

    marker = _marker_path(root)
    if marker.is_file():
        try:
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker_data = {}
        state: WorkspaceState = "managed_workspace"
        library_setup_service.ensure_storage_zone_column(root)
        return {
            "state": state,
            "library_root": redact_path(root),
            "inbox_path": redact_path(root / "Inbox"),
            "library_path": redact_path(root / "Library"),
            "quarantine_path": redact_path(root / "Quarantine"),
            "marker_version": marker_data.get("version"),
            "message": "Managed workspace. Imports are copied into Inbox; originals remain untouched.",
        }

    if _has_audio_directly_present(root) or _has_foreign_top_level_dirs(root) or (root / "logs" / "processed.db").is_file():
        return {
            "state": "legacy_direct_library",
            "library_root": redact_path(root),
            "inbox_path": None,
            "library_path": None,
            "quarantine_path": None,
            "marker_version": None,
            "message": (
                "This root is an existing direct library, not a managed workspace. "
                "Existing files were not moved or renamed. Configure a managed "
                "workspace (this root, if empty of conflicting content, or a new "
                "dedicated root) to use Inbox/Library/Quarantine and copy-based import."
            ),
        }

    return {
        "state": "not_configured",
        "library_root": redact_path(root),
        "inbox_path": None,
        "library_path": None,
        "quarantine_path": None,
        "marker_version": None,
        "message": "Empty root. Configure a managed workspace to begin.",
    }


def configure_workspace(root: Path) -> dict[str, Any]:
    """
    Idempotently create the managed workspace layout: Inbox/Library/Quarantine
    plus the marker file, reusing library_setup_service for the local index.

    Refuses (ValueError) on a legacy_direct_library root -- per product
    decision, an existing direct library is never silently restructured.
    Calling this again on an already-managed workspace is a safe no-op.
    """
    state = workspace_state(root)
    if state["state"] == "legacy_direct_library":
        raise ValueError(
            "This root already contains a direct library. Choose a new, "
            "dedicated managed root instead of restructuring it automatically."
        )

    root.mkdir(parents=True, exist_ok=True)
    for zone in _ZONE_DIRS:
        _zone_dir(root, zone).mkdir(parents=True, exist_ok=True)

    if state["state"] != "managed_workspace":
        marker = _marker_path(root)
        marker.write_text(
            json.dumps(
                {
                    "version": WORKSPACE_MARKER_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    library_setup_service.initialize_library(str(root))
    return workspace_state(root)


# ---------------------------------------------------------------------------
# Root classification + safe new-folder creation (onboarding)
# ---------------------------------------------------------------------------

def classify_root_candidate(value: str) -> dict[str, Any]:
    """
    Read-only safety classification for a candidate workspace root that may
    not exist yet. Never creates or modifies anything on disk. Raises
    ValueError for a path that is unsafe regardless of whether it exists
    (not absolute, control characters, system/repo path, a file).

    This is the single source of truth Settings onboarding uses to decide
    between "eligible to create", "eligible existing workspace", and
    "existing music folder detected" -- the frontend never re-derives this
    classification itself.
    """
    resolved = assert_safe_new_root_path(value)
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("library_root is a file, not a directory")
        state = workspace_state(resolved)
        return {
            "exists": True,
            "parent_exists": True,
            "parent_writable": None,
            "can_create": False,
            **state,
        }

    parent = resolved.parent
    parent_exists = parent.is_dir()
    parent_writable = parent_exists and os.access(parent, os.W_OK)
    if not parent_exists:
        message = f"Parent folder does not exist: {redact_path(parent)}. Create the parent folder first, or choose a different location."
    elif not parent_writable:
        message = f"Parent folder is not writable: {redact_path(parent)}."
    else:
        message = "This folder does not exist yet. CrateIQ can create it as a new managed workspace."
    return {
        "state": "not_configured",
        "library_root": redact_path(resolved),
        "inbox_path": None,
        "library_path": None,
        "quarantine_path": None,
        "marker_version": None,
        "exists": False,
        "parent_exists": parent_exists,
        "parent_writable": parent_writable,
        "can_create": parent_exists and parent_writable,
        "message": message,
    }


def create_root_directory(value: str, *, confirm: bool) -> dict[str, Any]:
    """
    Safely create ONLY the final requested directory for a new managed
    workspace root -- never a recursive parent tree. Idempotent: calling
    this on an already-existing directory performs no filesystem write and
    just returns its current classification.
    """
    resolved = assert_safe_new_root_path(value)
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("library_root is a file, not a directory")
        return classify_root_candidate(value)

    if not confirm:
        raise ValueError("Creating a new workspace folder requires confirm=true.")

    parent = resolved.parent
    if not parent.is_dir():
        raise ValueError(f"Parent folder does not exist: {redact_path(parent)}")
    if not os.access(parent, os.W_OK):
        raise ValueError(f"Parent folder is not writable: {redact_path(parent)}")

    resolved.mkdir()  # single level only -- never parents=True here
    return classify_root_candidate(value)


# ---------------------------------------------------------------------------
# Import (copy external sources into Inbox)
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_audio_files(source: Path) -> tuple[list[Path], list[str]]:
    """
    Read-only discovery of audio files under a validated external source.
    Never follows symlinks (traversal/escape safety) and reports skipped
    symlinks as warnings rather than silently including or excluding them.
    """
    warnings: list[str] = []
    if source.is_symlink():
        return [], [f"Skipped symlink source: {source.name}"]
    if source.is_file():
        if source.suffix.lower() in _audio_extensions():
            return [source], []
        return [], [f"Unsupported file type: {source.name}"]

    found: list[Path] = []
    import os

    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        dirpath_p = Path(dirpath)
        kept_dirs = []
        for d in dirnames:
            if (dirpath_p / d).is_symlink():
                if len(warnings) < _SAMPLE_LIMIT:
                    warnings.append(f"Skipped symlinked directory: {d}")
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for name in filenames:
            candidate = dirpath_p / name
            if candidate.is_symlink():
                if len(warnings) < _SAMPLE_LIMIT:
                    warnings.append(f"Skipped symlink: {name}")
                continue
            if candidate.suffix.lower() in _audio_extensions():
                found.append(candidate)
    found.sort()
    return found, warnings


def _unique_inbox_destination(inbox_dir: Path, filename: str) -> Path:
    """Deterministic collision handling: never overwrite; append ' (2)', ' (3)', ..."""
    candidate = inbox_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    n = 2
    while True:
        candidate = inbox_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def import_sources(root: Path, source_paths: list[str], *, confirm: bool) -> dict[str, Any]:
    """
    Copy external files/folders into <root>/Inbox. Never touches, moves, or
    renames anything at the source. Never indexes the source path as the
    managed track -- only the verified copy is indexed.
    """
    if not confirm:
        raise ValueError("Import requires confirm=true.")
    if not source_paths:
        raise ValueError("Select at least one file or folder to import.")

    state = workspace_state(root)
    if state["state"] != "managed_workspace":
        raise ValueError("Configure a managed workspace before importing.")

    inbox_dir = _zone_dir(root, "Inbox")
    library_setup_service.ensure_storage_zone_column(root)

    all_sources: list[Path] = []
    validation_errors: list[str] = []
    root_resolved = root.resolve(strict=False)
    for raw in source_paths:
        try:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                validation_errors.append(f"Not an absolute path: {raw}")
                continue
            # strict=True resolves symlinks and also catches a symlink that
            # escapes into the managed root -- the containment checks below
            # then see the real resolved destination, not the link's surface
            # path.
            resolved = candidate.resolve(strict=True)
        except OSError:
            validation_errors.append(f"Source not found: {raw}")
            continue
        try:
            resolved.relative_to(root_resolved)
            validation_errors.append(f"Refusing to import from inside the managed root: {raw}")
            continue
        except ValueError:
            pass
        try:
            root_resolved.relative_to(resolved)
            validation_errors.append(
                f"Refusing to import: the managed workspace is inside this source folder ({raw}). "
                "Choose a source that does not contain the managed workspace."
            )
            continue
        except ValueError:
            pass
        all_sources.append(resolved)

    discovered: list[Path] = []
    warnings: list[str] = list(validation_errors)
    for source in all_sources:
        files, source_warnings = _discover_audio_files(source)
        discovered.extend(files)
        warnings.extend(source_warnings)

    if len(discovered) > _MAX_IMPORT_FILES:
        raise ValueError(
            f"{len(discovered)} audio files selected; imports are limited to "
            f"{_MAX_IMPORT_FILES} files per operation. Import in smaller batches."
        )

    now = datetime.now(timezone.utc).isoformat()
    copied: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    db_path = assert_path_under_root(root / "logs" / "processed.db", root)
    with sqlite3.connect(db_path) as conn:
        for source_file in discovered:
            try:
                dest = _unique_inbox_destination(inbox_dir, source_file.name)
                # Duplicate-content short-circuit: identical bytes already in
                # Inbox under the exact original filename is a true duplicate,
                # not a name collision -- skip the copy rather than doubling it.
                exact_name_dest = inbox_dir / source_file.name
                if exact_name_dest.exists() and exact_name_dest.stat().st_size == source_file.stat().st_size:
                    if _sha256(exact_name_dest) == _sha256(source_file):
                        duplicates.append({"source_filename": source_file.name, "reason": "identical file already in Inbox"})
                        continue

                dest = assert_path_under_root(dest, root)
                source_hash = _sha256(source_file)
                shutil.copy2(source_file, dest)
                if _sha256(dest) != source_hash or dest.stat().st_size != source_file.stat().st_size:
                    dest.unlink(missing_ok=True)
                    failed.append({"source_filename": source_file.name, "reason": "copy verification failed"})
                    continue

                artist, title, album, genre, comment, label, confidence, _tags_present = library_setup_service._track_metadata(dest)
                conn.execute(
                    """
                    INSERT INTO tracks (filepath, filename, artist, title, album, genre, comment, label,
                                         filesize_bytes, status, processed_at, pipeline_ver,
                                         parse_confidence, storage_zone)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 'workspace-import-v1', ?, 'INBOX')
                    """,
                    (str(dest), dest.name, artist, title, album, genre, comment, label,
                     dest.stat().st_size, now, confidence),
                )
                copied.append({"source_filename": source_file.name, "inbox_filename": dest.name})
            except Exception as exc:  # noqa: BLE001 - report per-file, keep batch going
                failed.append({"source_filename": source_file.name, "reason": str(exc)})
        conn.commit()

    return {
        "library_root": redact_path(root),
        "sources_provided": len(source_paths),
        "audio_files_discovered": len(discovered),
        "imported_count": len(copied),
        "duplicate_count": len(duplicates),
        "failed_count": len(failed),
        "imported": copied[:_SAMPLE_LIMIT],
        "duplicates": duplicates[:_SAMPLE_LIMIT],
        "failed": failed[:_SAMPLE_LIMIT],
        "warnings": warnings[:_SAMPLE_LIMIT],
        "message": "Imported files were copied into the managed Inbox. Original source files were not modified.",
    }


# ---------------------------------------------------------------------------
# Promotion (Move Ready to Library)
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = ("artist", "title", "genre")


def _destination_for(root: Path, genre: str, artist: str, title: str, ext: str) -> Path:
    genre_seg = safe_path_segment(genre, "Unsorted")
    artist_seg = safe_path_segment(artist, "Unknown Artist")
    filename_seg = safe_path_segment(f"{artist} - {title}", "Untitled") + ext
    return assert_path_under_root(
        root / "Library" / genre_seg / artist_seg / filename_seg, root
    )


_PREPARATION_STATUS_LABELS = {
    "WRITE_BLOCKED": "Write Blocked",
    "NEEDS_ATTENTION": "Needs Attention",
    "REVIEW": "Review",
    "UNSAVED": "Unsaved",
    "READY": "Ready",
}
_PREPARATION_STATUS_ORDER = {
    "READY": 0,
    "UNSAVED": 1,
    "REVIEW": 2,
    "NEEDS_ATTENTION": 3,
    "WRITE_BLOCKED": 4,
}


def _reason(code: str, label: str, severity: str) -> dict[str, str]:
    return {"code": code, "label": label, "severity": severity}


def _write_blocker_reason(row: sqlite3.Row, blocker: str | None) -> dict[str, str]:
    text = blocker or "Metadata cannot currently be written safely."
    if "outside the configured library root" in text:
        return _reason("source_outside_managed_root", "Managed file is outside the configured library", "blocker")
    if "no longer exists" in text:
        return _reason("managed_file_missing", "Managed Inbox file is missing", "blocker")
    if "not a supported write-back format" in text:
        extension = Path(row["filepath"]).suffix.lstrip(".").upper() or "This format"
        return _reason(
            "unsupported_write_format",
            f"{extension} metadata write-back is not supported",
            "blocker",
        )
    if "local index" in text:
        return _reason("track_missing_from_index", "Track is missing from the local index", "blocker")
    return _reason("metadata_write_blocked", "Metadata cannot currently be written safely", "blocker")


def _review_reasons(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
        suggested = entry.get("suggested_fields") if isinstance(entry.get("suggested_fields"), dict) else {}
        fields = list(evidence) or list(suggested)
        summary = str(entry.get("reason") or "").casefold()
        conflict = any(token in summary for token in ("disagreement", "disagree", "conflict"))
        if fields:
            for field in fields:
                display = str(field).replace("_", " ").title()
                code = f"provider_{'conflict' if conflict else 'review'}_{field}"
                if code in seen:
                    continue
                seen.add(code)
                label = (
                    f"Metadata sources disagree on {display}"
                    if conflict else f"Suggested {display} needs review"
                )
                reasons.append(_reason(code, label, "review"))
        elif "provider_review" not in seen:
            seen.add("provider_review")
            reasons.append(_reason("provider_review", "A metadata suggestion needs review", "review"))
    return reasons


def _active_enrichment_reviews(track_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    # Local import avoids the settings -> sync destination -> workspace
    # import cycle during service initialization.
    from . import enrichment_review_service

    wanted = set(track_ids)
    grouped: dict[int, list[dict[str, Any]]] = {}
    # One latest-snapshot read for the whole request. Ignored, applied,
    # review-later, and superseded snapshots are intentionally not actionable.
    review = enrichment_review_service.get_review()
    for entry in review.get("items", []):
        track_id = entry.get("track_id")
        if track_id in wanted and entry.get("decision") == "pending":
            grouped.setdefault(track_id, []).append(entry)
    return grouped


def _preparation_states_for_rows(root: Path, rows: list[sqlite3.Row]) -> dict[int, dict[str, Any]]:
    """Build authoritative, read-only Inbox preparation state in batch.

    Performance contract: rows and review state are retrieved once, tag-plan
    items read each file at most once, tag-write history is one bounded DB
    query, and destination readiness is one pass. No provider/network work,
    mutation, tag write, move, or background job is triggered here.
    """
    track_ids = [int(row["id"]) for row in rows]
    if not track_ids:
        return {}
    plan_by_id = {
        item["track_id"]: item
        for item in tag_write_service.build_plan_items_for_rows(rows, root)
    }
    reviews_by_id = _active_enrichment_reviews(track_ids)
    latest_outcomes = tag_write_service.latest_track_outcomes(track_ids)
    inbox_root = assert_path_under_root(root / "Inbox", root)
    states: dict[int, dict[str, Any]] = {}

    for row in rows:
        track_id = int(row["id"])
        artist = (row["artist"] or "").strip()
        title = (row["title"] or "").strip()
        genre = (row["genre"] or "").strip()
        path = Path(row["filepath"])
        plan_item = plan_by_id[track_id]
        pending_fields = [field["field"] for field in plan_item.get("fields", [])]

        write_reasons: list[dict[str, str]] = []
        attention_reasons: list[dict[str, str]] = []
        review_entries = reviews_by_id.get(track_id, [])
        review_reasons = _review_reasons(review_entries)
        unsaved_reasons = [
            _reason(
                f"{field}_unsaved",
                f"{field.replace('_', ' ').title()} has changes not yet written to file",
                "unsaved",
            )
            for field in pending_fields
        ]

        source_in_inbox = False
        try:
            resolved_path = assert_path_under_root(path, root)
            resolved_path.relative_to(inbox_root)
            source_in_inbox = True
        except (TypeError, ValueError):
            if not plan_item["blocked"]:
                write_reasons.append(_reason(
                    "source_not_in_inbox", "Managed file is not inside Inbox", "blocker",
                ))
        if plan_item["blocked"]:
            write_reasons.append(_write_blocker_reason(row, plan_item.get("blocker")))

        latest = latest_outcomes.get(track_id)
        active_last_failure = bool(pending_fields and latest and latest.get("failed"))
        if active_last_failure:
            write_reasons.append(_reason(
                "latest_write_failed",
                "The latest metadata write failed and changes remain unsaved",
                "blocker",
            ))

        if not artist:
            attention_reasons.append(_reason("artist_missing", "Artist is missing", "attention"))
        if not title:
            attention_reasons.append(_reason("title_missing", "Title is missing", "attention"))
        if not genre:
            attention_reasons.append(_reason("genre_missing", "Genre is missing", "attention"))

        track_issues = set(Track.from_row(row).issues)
        if "suspicious_artist" in track_issues:
            attention_reasons.append(_reason(
                "suspicious_artist", "Artist may contain promotional or junk text", "attention",
            ))
        if "suspicious_title" in track_issues:
            attention_reasons.append(_reason(
                "suspicious_title", "Title may contain promotional or junk text", "attention",
            ))
        if row["status"] == "error":
            attention_reasons.append(_reason(
                "current_processing_error", "A current processing error needs attention", "attention",
            ))
        elif row["status"] == "needs_review":
            attention_reasons.append(_reason(
                "current_preparation_issue", "A current preparation issue needs attention", "attention",
            ))

        destination: Path | None = None
        collision: Literal["identical", "conflict"] | None = None
        if artist and title and genre and source_in_inbox and path.is_file():
            destination = _destination_for(root, genre, artist, title, path.suffix)
            if destination.exists():
                try:
                    identical = (
                        destination.stat().st_size == path.stat().st_size
                        and _sha256(destination) == _sha256(path)
                    )
                except OSError:
                    identical = False
                collision = "identical" if identical else "conflict"
                if collision == "identical":
                    attention_reasons.append(_reason(
                        "destination_identical",
                        "This track already exists at the Library destination",
                        "attention",
                    ))
                else:
                    attention_reasons.append(_reason(
                        "destination_collision",
                        "A different file already exists at the Library destination",
                        "attention",
                    ))

        warnings: list[dict[str, str]] = []
        if row["bpm"] is None:
            warnings.append({"code": "bpm_missing", "label": "BPM is missing"})
        if not (row["key_camelot"] or row["key_musical"]):
            warnings.append({"code": "key_missing", "label": "Key is missing"})

        if write_reasons:
            status = "WRITE_BLOCKED"
        elif attention_reasons:
            status = "NEEDS_ATTENTION"
        elif review_reasons:
            status = "REVIEW"
        elif unsaved_reasons:
            status = "UNSAVED"
        else:
            status = "READY"
        reasons = write_reasons + attention_reasons + review_reasons + unsaved_reasons
        states[track_id] = {
            "track_id": track_id,
            "status": status,
            "status_label": _PREPARATION_STATUS_LABELS[status],
            "reasons": reasons,
            "warnings": warnings,
            "pending_fields": pending_fields,
            "review_count": len(review_entries),
            "write": {
                "has_unsaved_changes": bool(pending_fields),
                "blocked": bool(write_reasons),
                "blocker_code": write_reasons[0]["code"] if write_reasons else None,
                "last_failure": (
                    "The latest metadata write did not complete" if active_last_failure else None
                ),
            },
            "promotion": {
                "ready": status == "READY",
                "destination": str(destination.relative_to(root)) if destination else None,
                "collision": collision,
            },
        }
    return states


def inbox_preparation_states(
    root: Path, track_ids: list[int] | None = None,
) -> dict[int, dict[str, Any]]:
    """Public read-only projection for Inbox tracks, preserving requested order."""
    db_path = _require_initialized_db(root)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if track_ids is None:
            rows = conn.execute("SELECT * FROM tracks WHERE storage_zone = 'INBOX'").fetchall()
        elif not track_ids:
            rows = []
        else:
            placeholders = ",".join("?" * len(track_ids))
            by_id = {
                row["id"]: row
                for row in conn.execute(
                    f"SELECT * FROM tracks WHERE storage_zone = 'INBOX' AND id IN ({placeholders})",
                    track_ids,
                )
            }
            rows = [by_id[track_id] for track_id in track_ids if track_id in by_id]
    return _preparation_states_for_rows(root, rows)


def inbox_track_page_projection(
    root: Path,
    *,
    search: str | None = None,
    preparation_status: str | None = None,
    sort: str = "artist",
    order: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Project, filter, sort, and paginate the authoritative Inbox state once.

    Status counts are scoped to the current search but not the selected status,
    so the filter chips remain useful while one status is active. All candidate
    rows share one preparation-state projection; the resulting state objects are
    reused for counts, filtering, readiness sorting, and response rendering.
    """
    try:
        db_path = _require_initialized_db(root)
    except ValueError:
        return {
            "items": [],
            "states": {},
            "limit": limit,
            "offset": offset,
            "total": 0,
            "status_counts": {"ALL": 0, **{status: 0 for status in _PREPARATION_STATUS_LABELS}},
            "available_track_ids": [],
        }
    where = ["COALESCE(storage_zone, 'LIBRARY') = 'INBOX'"]
    params: list[object] = []
    normalized_search = (search or "").strip()
    if normalized_search:
        term = f"%{normalized_search}%"
        where.append("(artist LIKE ? OR title LIKE ? OR filename LIKE ? OR genre LIKE ?)")
        params.extend([term, term, term, term])
    where_sql = " AND ".join(where)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        review_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(track_reviews)")}
        review_table_exists = bool(review_columns)
        order_by = track_service.build_order_by(sort, order, review_table_exists=review_table_exists, review_favorite_exists='favorite' in review_columns)
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE {where_sql} ORDER BY {order_by}", params,
        ).fetchall()
        available_track_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM tracks WHERE COALESCE(storage_zone, 'LIBRARY') = 'INBOX' ORDER BY id"
            ).fetchall()
        ]

    states = _preparation_states_for_rows(root, rows)
    status_counts = {status: 0 for status in _PREPARATION_STATUS_LABELS}
    for state in states.values():
        status_counts[state["status"]] += 1

    filtered_rows = (
        [row for row in rows if states[int(row["id"])]["status"] == preparation_status]
        if preparation_status else rows
    )
    if sort == "readiness":
        direction = 1 if order == "asc" else -1
        filtered_rows = sorted(
            filtered_rows,
            key=lambda row: (
                direction * _PREPARATION_STATUS_ORDER[states[int(row["id"])]["status"]],
                (row["artist"] or "").casefold(),
                int(row["id"]),
            ),
        )

    total = len(filtered_rows)
    page_rows = filtered_rows[offset:offset + limit]
    return {
        "items": [Track.from_row(row) for row in page_rows],
        "states": states,
        "limit": limit,
        "offset": offset,
        "total": total,
        "status_counts": {"ALL": len(rows), **status_counts},
        "available_track_ids": available_track_ids,
    }


def inbox_track_inspection(root: Path, track_id: int) -> tuple[Track, dict[str, Any]] | None:
    """Return one Inbox track and its authoritative read-only preparation state."""
    try:
        db_path = _require_initialized_db(root)
    except ValueError:
        return None
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM tracks WHERE id = ? AND COALESCE(storage_zone, 'LIBRARY') = 'INBOX'",
            (track_id,),
        ).fetchone()
    if row is None:
        return None
    state = _preparation_states_for_rows(root, [row])[track_id]
    return Track.from_row(row), state


def _legacy_promotion_item(row: sqlite3.Row, state: dict[str, Any]) -> dict[str, Any]:
    """Keep the established promotion-preview fields while exposing state."""
    blockers: list[str] = []
    unsaved_added = False
    for reason in state["reasons"]:
        if reason["severity"] == "unsaved":
            if not unsaved_added:
                blockers.append("Approved metadata has not been written back to the file yet.")
                unsaved_added = True
        else:
            blockers.append(reason["label"])
    legacy_warning_labels = {
        "bpm_missing": "Missing BPM.",
        "key_missing": "Missing key.",
    }
    return {
        "track_id": row["id"],
        "filename": row["filename"],
        "artist": (row["artist"] or "").strip() or None,
        "title": (row["title"] or "").strip() or None,
        "genre": (row["genre"] or "").strip() or None,
        "ready": state["promotion"]["ready"],
        "blockers": blockers,
        "warnings": [legacy_warning_labels.get(warning["code"], warning["label"]) for warning in state["warnings"]],
        "destination_relative": state["promotion"]["destination"],
        "collision": state["promotion"]["collision"],
        "preparation_state": state,
    }


def promotion_preview(root: Path, track_ids: list[int] | None = None) -> dict[str, Any]:
    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if track_ids is not None:
            if not track_ids:
                rows = []
            else:
                placeholders = ",".join("?" * len(track_ids))
                rows = conn.execute(
                    f"SELECT * FROM tracks WHERE storage_zone = 'INBOX' AND id IN ({placeholders})",
                    track_ids,
                ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tracks WHERE storage_zone = 'INBOX'").fetchall()

    states = _preparation_states_for_rows(root, rows)
    items = [_legacy_promotion_item(row, states[row["id"]]) for row in rows]
    return {
        "library_root": redact_path(root),
        "track_count": len(items),
        "ready_count": sum(1 for i in items if i["ready"]),
        "blocked_count": sum(1 for i in items if not i["ready"]),
        "items": items,
        "message": "Preview only. No files were moved.",
    }


def promote_tracks(root: Path, track_ids: list[int], *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Promotion requires confirm=true after reviewing the preview.")
    if not track_ids:
        raise ValueError("Select at least one track to promote.")

    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)
    results: list[dict[str, Any]] = []
    promoted = failed = 0

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(track_ids))
        rows = {
            row["id"]: row
            for row in conn.execute(
                f"SELECT * FROM tracks WHERE storage_zone = 'INBOX' AND id IN ({placeholders})",
                track_ids,
            )
        }
        states = _preparation_states_for_rows(root, list(rows.values()))
        for track_id in track_ids:
            row = rows.get(track_id)
            if row is None:
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": "Track is not in Inbox."})
                continue

            readiness = _legacy_promotion_item(row, states[track_id])
            if not readiness["ready"]:
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": "; ".join(readiness["blockers"])})
                continue

            source = Path(row["filepath"])
            destination = root / readiness["destination_relative"]
            try:
                destination = assert_path_under_root(destination, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    failed += 1
                    results.append({"track_id": track_id, "status": "failed", "reason": "Destination conflict detected at move time."})
                    continue
                source.rename(destination)
            except OSError:
                try:
                    shutil.move(str(source), str(destination))
                except OSError as exc:
                    failed += 1
                    results.append({"track_id": track_id, "status": "failed", "reason": f"Move failed: {exc}"})
                    continue

            if destination.is_file() and not source.exists():
                conn.execute(
                    "UPDATE tracks SET filepath = ?, filename = ?, storage_zone = 'LIBRARY' WHERE id = ?",
                    (str(destination), destination.name, track_id),
                )
                promoted += 1
                results.append({
                    "track_id": track_id, "status": "promoted",
                    "destination_relative": readiness["destination_relative"],
                })
            else:
                # Truthful failure -- do not claim success on an unverified move.
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": "Move could not be verified."})
        conn.commit()

    return {
        "library_root": redact_path(root),
        "promoted_count": promoted,
        "failed_count": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Inbox inline editing -- Track/file rename, Artist/Title/Genre/Album metadata
# ---------------------------------------------------------------------------

def _require_inbox_row(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:
        raise ValueError("Track not found.")
    if (row["storage_zone"] or "LIBRARY") != "INBOX":
        raise ValueError("Only managed Inbox tracks can be edited here.")
    return row


def _validate_new_basename(value: str) -> str:
    """
    Validate a user-supplied Inbox filename basename (the part before the
    locked extension). Unlike safe_path_segment -- which silently normalizes
    free-text metadata into a generated destination path -- this rejects
    unsafe input outright: a manual rename is a deliberate user action, and
    an unsafe value must be fixed by the user, never silently rewritten.
    """
    text = unicodedata.normalize("NFC", value or "")
    if "\x00" in text or _CONTROL_CHAR_RE.search(text):
        raise ValueError("Filename contains an unsafe control character.")
    if "/" in text or "\\" in text:
        raise ValueError("Filename cannot contain a path separator.")
    if not text.strip():
        raise ValueError("Filename cannot be empty.")
    if text in {".", ".."}:
        raise ValueError("Filename cannot be '.' or '..'.")
    trimmed = text.rstrip(" .")
    if not trimmed:
        raise ValueError("Filename cannot consist only of dots or spaces.")
    if trimmed != text:
        raise ValueError("Filename cannot end with a space or a period.")
    if trimmed.upper() in _RESERVED_WINDOWS_STEMS:
        raise ValueError(f"'{trimmed}' is a reserved filename and cannot be used.")
    return trimmed


def rename_inbox_track(root: Path, track_id: int, new_basename: str) -> dict[str, Any]:
    """
    Rename the managed Inbox copy's filename only.

    Never touches audio bytes, never touches TITLE metadata, never changes
    the extension (always taken from the current file, never from user
    input), and never renames anything outside <root>/Inbox -- Library and
    Quarantine tracks, and external originals, are untouched by this
    function by construction (it only ever resolves paths under the Inbox
    zone directory).
    """
    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)
    inbox_dir = _zone_dir(root, "Inbox")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = _require_inbox_row(conn, track_id)

        # Check symlink-ness on the raw, un-resolved path first: assert_path_
        # under_root resolves symlinks (Path.resolve), so by the time a path
        # comes back from it the link has already been transparently
        # dereferenced and is_symlink() on the *result* would never fire.
        if Path(row["filepath"]).is_symlink():
            raise ValueError("Refusing to rename a symlinked Inbox entry.")
        try:
            current_path = assert_path_under_root(row["filepath"], root)
            current_path.relative_to(inbox_dir)
        except ValueError:
            raise ValueError("Indexed file is not inside the managed Inbox.")
        if not current_path.is_file():
            raise ValueError("Source file no longer exists in Inbox.")

        ext = current_path.suffix
        requested = new_basename or ""
        if ext and requested.lower().endswith(ext.lower()):
            requested = requested[: -len(ext)]
        basename = _validate_new_basename(requested)
        new_filename = f"{basename}{ext}"

        if new_filename == current_path.name:
            return {
                "track_id": track_id, "status": "no_change",
                "filename": current_path.name, "filepath": str(current_path),
            }

        destination = inbox_dir / new_filename
        try:
            destination = assert_path_under_root(destination, root)
            destination.relative_to(inbox_dir)
        except ValueError:
            raise ValueError("Resolved destination escapes the managed Inbox.")
        if destination.is_symlink() or destination.exists():
            raise ValueError(f"A file named '{new_filename}' already exists in Inbox. Choose a different name.")

        try:
            current_path.rename(destination)
        except OSError:
            try:
                shutil.move(str(current_path), str(destination))
            except OSError as exc:
                raise ValueError(f"Rename failed: {exc}")

        if not (destination.is_file() and not current_path.exists()):
            # Truthful failure -- do not claim success on an unverified rename.
            raise ValueError("Rename could not be verified on disk.")

        try:
            conn.execute(
                "UPDATE tracks SET filepath = ?, filename = ? WHERE id = ?",
                (str(destination), destination.name, track_id),
            )
            conn.commit()
        except Exception as exc:
            # Filesystem rename already succeeded -- recover by renaming back
            # so the index and disk never disagree about the Inbox filename.
            try:
                destination.rename(current_path)
                reverted = True
            except OSError:
                reverted = False
            reason = f"Index update failed after rename ({exc}); " + (
                "the file was renamed back to its original name." if reverted
                else "the file remains at its NEW name on disk but the index still shows "
                     "the OLD name -- manual reconciliation required."
            )
            raise ValueError(reason)

    return {"track_id": track_id, "status": "renamed", "filename": destination.name, "filepath": str(destination)}


def _validate_metadata_value(field: str, value: str) -> str:
    text = unicodedata.normalize("NFC", value or "").strip()
    if not text:
        raise ValueError(f"{field.capitalize()} cannot be empty.")
    limit = _MAX_COMMENT_LENGTH if field == "comment" else _MAX_METADATA_FIELD_LENGTH
    if len(text) > limit:
        raise ValueError(f"{field.capitalize()} is too long (max {limit} characters).")
    if _CONTROL_CHAR_RE.search(text):
        raise ValueError(f"{field.capitalize()} contains an unsafe control character.")
    return text


def edit_inbox_track_metadata(
    root: Path,
    track_id: int,
    *,
    artist: str | None = None,
    title: str | None = None,
    genre: str | None = None,
    album: str | None = None,
) -> dict[str, Any]:
    """
    Manual single-track Artist/Title/Genre/Album edit for a managed Inbox copy.

    Updates only the approved local-index metadata and its provenance. The
    managed file is intentionally not written here; explicit Save to File UX
    will later consume the same tracks row through tag_write_service.
    """
    requested = {"artist": artist, "title": title, "genre": genre, "album": album}
    if not any(value is not None for value in requested.values()):
        raise ValueError("Provide at least one of artist, title, genre, or album to update.")

    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = _require_inbox_row(conn, track_id)

        updates: dict[str, str] = {}
        for field, value in requested.items():
            if value is None:
                continue
            normalized = _validate_metadata_value(field, value)
            if normalized != (row[field] or ""):
                updates[field] = normalized

        if not updates:
            status = "no_change"
        else:
            set_sql = ", ".join(f"{field} = ?" for field in updates)
            conn.execute(f"UPDATE tracks SET {set_sql} WHERE id = ?", (*updates.values(), track_id))
            for field, value in updates.items():
                field_provenance_service.record(
                    track_id, field, value, origin="user", source="manual_edit",
                    reason="Manual single-track edit (Inbox DB-first edit).", conn=conn,
                )
            conn.commit()
            status = "updated"

    state = inbox_preparation_states(root, [track_id])[track_id]
    current = {field: updates.get(field, row[field]) for field in requested}

    return {
        "track_id": track_id,
        "status": status,
        "fields_changed": list(updates),
        "artist": current["artist"],
        "title": current["title"],
        "genre": current["genre"],
        "album": current["album"],
        "tag_write": None,
        "preparation_state": state,
    }


def _validate_bulk_operations(operations: dict[str, dict[str, Any]]) -> dict[str, dict[str, str | None]]:
    forbidden = set(operations).intersection(_BULK_FORBIDDEN_IDENTITY_FIELDS)
    if forbidden:
        field = sorted(forbidden)[0]
        raise ValueError(f"Bulk {field.capitalize()} editing is prohibited; edit identity fields one track at a time.")
    unsupported = set(operations) - set(_BULK_METADATA_OPERATIONS)
    if unsupported:
        raise ValueError(f"Unsupported bulk metadata field: {sorted(unsupported)[0]}.")

    validated: dict[str, dict[str, str | None]] = {}
    for field, raw in operations.items():
        operation = str(raw.get("operation") or "").lower()
        if operation not in _BULK_METADATA_OPERATIONS[field]:
            raise ValueError(f"{operation or 'Missing'} is not valid for {field.capitalize()}.")
        value = raw.get("value")
        if operation in {"set", "append"}:
            if not isinstance(value, str):
                raise ValueError(f"{field.capitalize()} {operation} requires a value.")
            value = _validate_metadata_value(field, value)
        elif value not in (None, ""):
            raise ValueError(f"{field.capitalize()} {operation} does not accept a value.")
        validated[field] = {"operation": operation, "value": value if isinstance(value, str) else None}

    if not any(item["operation"] != "leave" for item in validated.values()):
        raise ValueError("Choose at least one metadata change before previewing.")
    return validated


def _bulk_result_value(current: str, operation: dict[str, str | None]) -> str:
    action = operation["operation"]
    value = operation.get("value") or ""
    if action == "set":
        return value
    if action == "clear":
        return ""
    if action == "append":
        existing_lines = [line.strip() for line in current.splitlines() if line.strip()]
        return current if value in existing_lines else (f"{current}\n{value}" if current else value)
    return current


def _bulk_write_capability(track_ids: list[int]) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    for start in range(0, len(track_ids), 50):
        plan = tag_write_service.build_plan(track_ids[start:start + 50])
        items.update({int(item["track_id"]): item for item in plan["items"]})
    return items


def bulk_edit_preview(root: Path, track_ids: list[int], *, operations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Preview explicit Genre/Comment/Label operations and file-write capability."""
    if not track_ids:
        raise ValueError("Select at least one track to bulk edit.")
    if len(track_ids) > _MAX_BULK_EDIT_TRACKS:
        raise ValueError(f"Bulk edit is limited to {_MAX_BULK_EDIT_TRACKS} tracks at a time.")
    if len(set(track_ids)) != len(track_ids):
        raise ValueError("Bulk edit track IDs must be unique.")
    validated = _validate_bulk_operations(operations)
    db_path = _require_initialized_db(root)
    library_setup_service.ensure_editable_metadata_columns(root)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(track_ids))
        rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", track_ids)}

    inbox_ids = [track_id for track_id in track_ids if track_id in rows and (rows[track_id]["storage_zone"] or "LIBRARY") == "INBOX"]
    capabilities = _bulk_write_capability(inbox_ids) if inbox_ids else {}
    unsupported_ids = {track_id for track_id, item in capabilities.items() if item["blocked"]}
    missing = sum(1 for track_id in track_ids if track_id not in rows)
    skipped_not_inbox = sum(
        1 for track_id in track_ids
        if track_id in rows and (rows[track_id]["storage_zone"] or "LIBRARY") != "INBOX"
    )
    eligible_ids = [track_id for track_id in inbox_ids if track_id not in unsupported_ids]

    field_previews: dict[str, Any] = {}
    changeable_ids: set[int] = set()
    for field, operation in validated.items():
        if operation["operation"] == "leave":
            continue
        counts: dict[str, int] = {}
        affected = already_matching = 0
        for track_id in eligible_ids:
            current = str(rows[track_id][field] or "").strip()
            display = current or "Blank"
            counts[display] = counts.get(display, 0) + 1
            next_value = _bulk_result_value(current, operation)
            if next_value == current:
                already_matching += 1
            else:
                affected += 1
                changeable_ids.add(track_id)
        current_values = sorted(counts, key=lambda value: (-counts[value], value.lower()))[:10]
        field_previews[field] = {
            "operation": operation["operation"],
            "value": operation.get("value"),
            "current_values": current_values,
            "mixed": len(counts) > 1,
            "affected_count": affected,
            "already_matching_count": already_matching,
            "skipped_count": missing + skipped_not_inbox + len(unsupported_ids),
        }

    return {
        "selected_count": len(track_ids),
        "eligible_count": len(eligible_ids),
        "changeable_count": len(changeable_ids),
        "skipped_not_inbox": skipped_not_inbox,
        "missing_count": missing,
        "unsupported_count": len(unsupported_ids),
        "fields": field_previews,
        "items": [
            {
                "track_id": track_id,
                "filename": rows[track_id]["filename"] if track_id in rows else None,
                "status": "not_found" if track_id not in rows else (
                    "not_inbox" if track_id not in inbox_ids else (
                        "unsupported" if track_id in unsupported_ids else (
                            "change" if track_id in changeable_ids else "unchanged"
                        )
                    )
                ),
                "reason": capabilities.get(track_id, {}).get("blocker"),
            }
            for track_id in track_ids
        ],
        "message": "Preview only. No metadata, tags, backups, or files were changed.",
    }


def bulk_edit_apply(
    root: Path,
    track_ids: list[int],
    *,
    operations: dict[str, dict[str, Any]],
    confirm: bool,
) -> dict[str, Any]:
    """Apply reviewed shared metadata, then use the existing verified tag writer."""
    if not confirm:
        raise ValueError("Bulk edit requires confirm=true after reviewing the preview.")
    preview = bulk_edit_preview(root, track_ids, operations=operations)
    if preview["missing_count"]:
        raise ValueError("Every selected track must belong to the active library.")
    validated = _validate_bulk_operations(operations)
    db_path = _require_initialized_db(root)
    blocked_by_id = {
        item["track_id"]: item for item in preview["items"]
        if item["status"] in {"not_found", "not_inbox", "unsupported"}
    }
    results: dict[int, dict[str, Any]] = {}
    changed_ids: list[int] = []

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(track_ids))
        rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", track_ids)}
        for track_id in track_ids:
            blocked = blocked_by_id.get(track_id)
            if blocked:
                status = "not_found" if blocked["status"] == "not_found" else "skipped"
                reason = blocked.get("reason") or ("Track is not in Inbox." if blocked["status"] == "not_inbox" else "Track cannot be written safely.")
                results[track_id] = {"track_id": track_id, "status": status, "reason": reason, "metadata_updated": False}
                continue

            row = rows[track_id]
            updates: dict[str, str | None] = {}
            for field, operation in validated.items():
                if operation["operation"] == "leave":
                    continue
                current = str(row[field] or "").strip()
                next_value = _bulk_result_value(current, operation)
                if next_value != current:
                    updates[field] = next_value or None
            if not updates:
                results[track_id] = {"track_id": track_id, "status": "unchanged", "metadata_updated": False}
                continue

            savepoint = f"bulk_track_{track_id}"
            conn.execute(f"SAVEPOINT {savepoint}")
            try:
                set_sql = ", ".join(f"{field} = ?" for field in updates)
                conn.execute(f"UPDATE tracks SET {set_sql} WHERE id = ?", (*updates.values(), track_id))
                for field, value in updates.items():
                    field_provenance_service.record(
                        track_id, field, value, origin="user", source="bulk_edit",
                        reason=f"Manual bulk Inbox {validated[field]['operation']} operation.", conn=conn,
                    )
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                changed_ids.append(track_id)
                results[track_id] = {
                    "track_id": track_id, "status": "pending_write", "fields": list(updates),
                    "metadata_updated": True,
                }
            except Exception as exc:  # noqa: BLE001 - isolate one track's DB failure
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                results[track_id] = {
                    "track_id": track_id, "status": "failed", "reason": f"Working metadata update failed: {exc}",
                    "metadata_updated": False,
                }
        conn.commit()

    write_results: dict[int, dict[str, Any]] = {}
    write_operation_ids: list[str] = []
    if changed_ids:
        from . import preparation_service
        active_fields = {
            field for field, operation in validated.items()
            if operation["operation"] != "leave"
        }
        try:
            write_result = preparation_service.write_tracks(changed_ids, allowed_fields=active_fields)
        except Exception as exc:  # noqa: BLE001 - return truthful per-track failures after committed working edits
            reason = f"Tag-write operation failed: {exc}. Working metadata remains unsaved."
            write_result = {
                "operation_ids": [],
                "results": [
                    {"track_id": track_id, "status": "failed", "reason": reason}
                    for track_id in changed_ids
                ],
            }
        write_results = {int(item["track_id"]): item for item in write_result["results"]}
        write_operation_ids = list(write_result.get("operation_ids", []))

    for track_id in changed_ids:
        entry = results[track_id]
        write = write_results.get(track_id)
        entry["write_status"] = write.get("status") if write else "failed"
        if write and write.get("status") in {"applied", "no_op"}:
            entry["status"] = "succeeded"
        else:
            entry["status"] = "failed"
            entry["reason"] = (write or {}).get("reason") or "No tag-write result was returned. Working metadata remains unsaved."

    inbox_ids = [
        track_id for track_id in track_ids
        if track_id in rows and (rows[track_id]["storage_zone"] or "LIBRARY") == "INBOX"
    ]
    states = inbox_preparation_states(root, inbox_ids)
    for track_id in inbox_ids:
        results[track_id]["preparation_state"] = states[track_id]

    ordered_results = [results[track_id] for track_id in track_ids]
    return {
        "selected_count": len(track_ids),
        "changed_count": len(changed_ids),
        "unchanged_count": sum(1 for item in ordered_results if item["status"] == "unchanged"),
        "succeeded_count": sum(1 for item in ordered_results if item["status"] == "succeeded"),
        "failed_count": sum(1 for item in ordered_results if item["status"] == "failed"),
        "skipped_count": sum(1 for item in ordered_results if item["status"] == "skipped"),
        "not_found_count": sum(1 for item in ordered_results if item["status"] == "not_found"),
        "results": ordered_results,
        "tag_write": {"operation_ids": write_operation_ids, "used_verified_writer": True},
        "message": "Bulk metadata was applied through the existing backup, write, re-read, and verify path.",
    }
