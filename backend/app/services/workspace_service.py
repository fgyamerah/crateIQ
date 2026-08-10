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

from . import field_provenance_service, library_setup_service, tag_write_service
from ..core.library_root import assert_path_under_root, assert_safe_new_root_path
from ..core.preflight import redact_path

WORKSPACE_MARKER_NAME = ".crateiq-workspace.json"
WORKSPACE_MARKER_VERSION = 1

_ZONE_DIRS = ("Inbox", "Library", "Quarantine")
_CRATEIQ_OWNED_TOP_LEVEL = {
    "Inbox", "Library", "Quarantine", "logs", "exports", "data", ".run",
    WORKSPACE_MARKER_NAME,
}
_AUDIO_EXTENSIONS = library_setup_service._AUDIO_EXTENSIONS
_MAX_IMPORT_FILES = 2000
_SAMPLE_LIMIT = 20

WorkspaceState = Literal["managed_workspace", "legacy_direct_library", "not_configured"]


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
            if entry.is_file() and entry.suffix.lower() in _AUDIO_EXTENSIONS:
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
        if source.suffix.lower() in _AUDIO_EXTENSIONS:
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
            if candidate.suffix.lower() in _AUDIO_EXTENSIONS:
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

                tags = library_setup_service._embedded_tags(dest)
                artist, title, album, genre, confidence, _tags_present = library_setup_service._track_metadata(dest)
                conn.execute(
                    """
                    INSERT INTO tracks (filepath, filename, artist, title, album, genre,
                                         filesize_bytes, status, processed_at, pipeline_ver,
                                         parse_confidence, storage_zone)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 'workspace-import-v1', ?, 'INBOX')
                    """,
                    (str(dest), dest.name, artist, title, album, genre,
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


def _promotion_readiness(row: sqlite3.Row, root: Path) -> dict[str, Any]:
    """
    Note: the metadata-write-verification check below reuses
    tag_write_service.build_plan(), which internally re-derives its own root
    via selected_library_root() rather than accepting one as a parameter.
    This function's `root` argument is used directly for path/destination
    construction and must therefore always equal selected_library_root() at
    call time -- true by construction for every caller in this module
    (promotion_preview/promote_tracks are only ever invoked by the workspace
    API route with root = selected_library_root()).
    """
    blockers: list[str] = []
    warnings: list[str] = []
    artist = (row["artist"] or "").strip()
    title = (row["title"] or "").strip()
    genre = (row["genre"] or "").strip()
    if not artist:
        blockers.append("Artist is required.")
    if not title:
        blockers.append("Title is required.")
    if not genre:
        blockers.append("Genre is required.")

    try:
        plan = tag_write_service.build_plan([row["id"]])
        item = plan["items"][0] if plan["items"] else None
    except ValueError:
        item = None
    if item is not None:
        if item["blocked"] and item["blocker"] != "Source file no longer exists.":
            # Unsupported format etc. is a serious blocker; a missing file is
            # reported separately below with a clearer message.
            blockers.append(f"Metadata write verification blocked: {item['blocker']}")
        elif item["fields"]:
            blockers.append("Approved metadata has not been written back to the file yet.")

    path = Path(row["filepath"])
    if not path.is_file():
        blockers.append("Source file no longer exists in Inbox.")

    if row["bpm"] is None:
        warnings.append("Missing BPM.")
    if not (row["key_camelot"] or row["key_musical"]):
        warnings.append("Missing key.")

    ext = path.suffix
    destination = None
    collision = None
    if not blockers:
        destination = _destination_for(root, genre, artist, title, ext)
        if destination.exists():
            try:
                identical = destination.stat().st_size == path.stat().st_size and _sha256(destination) == _sha256(path)
            except OSError:
                identical = False
            collision = "identical" if identical else "conflict"
            if collision == "conflict":
                blockers.append("A different file already exists at the destination path.")

    return {
        "track_id": row["id"],
        "filename": row["filename"],
        "artist": artist or None,
        "title": title or None,
        "genre": genre or None,
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "destination_relative": str(destination.relative_to(root)) if destination else None,
        "collision": collision,
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

    items = [_promotion_readiness(row, root) for row in rows]
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
        for track_id in track_ids:
            row = rows.get(track_id)
            if row is None:
                failed += 1
                results.append({"track_id": track_id, "status": "failed", "reason": "Track is not in Inbox."})
                continue

            readiness = _promotion_readiness(row, root)
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
# Inbox inline editing -- Track/file rename, Artist/Genre single + bulk edit
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
    if len(text) > _MAX_METADATA_FIELD_LENGTH:
        raise ValueError(f"{field.capitalize()} is too long (max {_MAX_METADATA_FIELD_LENGTH} characters).")
    if _CONTROL_CHAR_RE.search(text):
        raise ValueError(f"{field.capitalize()} contains an unsafe control character.")
    return text


def edit_inbox_track_metadata(
    root: Path, track_id: int, *, artist: str | None = None, genre: str | None = None,
) -> dict[str, Any]:
    """
    Manual single-track Artist/Genre edit for a managed Inbox copy.

    Updates the local index (the same "approved value" tag_write_service
    already treats as authoritative) and then reuses
    preparation_service.write_tracks -- the exact build_plan -> backup ->
    write -> re-read -> verify contract Process All's write stage uses -- to
    push the change into the file. No second, simplified tag writer exists
    here; this is a thin caller of the same one.
    """
    if artist is None and genre is None:
        raise ValueError("Provide at least one of artist or genre to update.")

    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = _require_inbox_row(conn, track_id)

        updates: dict[str, str] = {}
        if artist is not None:
            new_artist = _validate_metadata_value("artist", artist)
            if new_artist != (row["artist"] or ""):
                updates["artist"] = new_artist
        if genre is not None:
            new_genre = _validate_metadata_value("genre", genre)
            if new_genre != (row["genre"] or ""):
                updates["genre"] = new_genre

        if not updates:
            return {
                "track_id": track_id, "status": "no_change", "fields_changed": [],
                "artist": row["artist"], "genre": row["genre"], "tag_write": None,
            }

        set_sql = ", ".join(f"{field} = ?" for field in updates)
        conn.execute(f"UPDATE tracks SET {set_sql} WHERE id = ?", (*updates.values(), track_id))
        for field, value in updates.items():
            field_provenance_service.record(
                track_id, field, value, origin="user", source="manual_edit",
                reason="Manual single-track edit (Inbox inline edit).", conn=conn,
            )
        conn.commit()

    from . import preparation_service  # local import breaks the module import cycle
    write_result = preparation_service.write_tracks([track_id])

    return {
        "track_id": track_id,
        "status": "updated",
        "fields_changed": list(updates),
        "artist": updates.get("artist", row["artist"]),
        "genre": updates.get("genre", row["genre"]),
        "tag_write": write_result,
    }


def bulk_edit_preview(root: Path, track_ids: list[int], *, artist: str | None, genre: str | None) -> dict[str, Any]:
    """Read-only preview for bulk Artist/Genre edit. Never writes anything."""
    if artist is None and genre is None:
        raise ValueError("Select at least one field (Artist or Genre) to bulk edit.")
    if not track_ids:
        raise ValueError("Select at least one track to bulk edit.")

    fields: dict[str, str] = {}
    if artist is not None:
        fields["artist"] = _validate_metadata_value("artist", artist)
    if genre is not None:
        fields["genre"] = _validate_metadata_value("genre", genre)

    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(track_ids))
        rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", track_ids)}

    field_previews: dict[str, Any] = {}
    for field, new_value in fields.items():
        counts: dict[str, int] = {}
        for track_id in track_ids:
            row = rows.get(track_id)
            if row is None:
                continue
            current = (row[field] or "").strip() or "Unknown"
            counts[current] = counts.get(current, 0) + 1
        current_values = sorted(counts, key=lambda v: (-counts[v], v.lower()))[:10]
        field_previews[field] = {"current_values": current_values, "new_value": new_value}

    eligible = skipped_not_inbox = missing = 0
    for track_id in track_ids:
        row = rows.get(track_id)
        if row is None:
            missing += 1
        elif (row["storage_zone"] or "LIBRARY") != "INBOX":
            skipped_not_inbox += 1
        else:
            eligible += 1

    return {
        "selected_count": len(track_ids),
        "eligible_count": eligible,
        "skipped_not_inbox": skipped_not_inbox,
        "missing_count": missing,
        "fields": field_previews,
        "message": "Preview only. No metadata or files were changed.",
    }


def bulk_edit_apply(
    root: Path, track_ids: list[int], *, artist: str | None, genre: str | None, confirm: bool,
) -> dict[str, Any]:
    """
    Apply a bulk Artist/Genre edit to every selected managed-Inbox track.

    Per track: confirm it still exists and is still storage_zone=INBOX,
    diff against the current DB value (skip real no-ops), update the index,
    then reuse preparation_service.write_tracks (tag_write_service's exact
    backup/write/re-read/verify contract) for the changed tracks as one
    batch. One track's tag-write failure never flips another track's already
    -successful result, and vice versa -- each track's final status is
    reported independently and truthfully.
    """
    if not confirm:
        raise ValueError("Bulk edit requires confirm=true after reviewing the preview.")
    if artist is None and genre is None:
        raise ValueError("Select at least one field (Artist or Genre) to bulk edit.")
    if not track_ids:
        raise ValueError("Select at least one track to bulk edit.")

    fields: dict[str, str] = {}
    if artist is not None:
        fields["artist"] = _validate_metadata_value("artist", artist)
    if genre is not None:
        fields["genre"] = _validate_metadata_value("genre", genre)

    db_path = _require_initialized_db(root)
    library_setup_service.ensure_storage_zone_column(root)

    results: dict[int, dict[str, Any]] = {}
    changed_ids: list[int] = []

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(track_ids))
        rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", track_ids)}

        for track_id in track_ids:
            row = rows.get(track_id)
            if row is None:
                results[track_id] = {"track_id": track_id, "status": "not_found", "reason": "Track not found."}
                continue
            if (row["storage_zone"] or "LIBRARY") != "INBOX":
                results[track_id] = {"track_id": track_id, "status": "skipped", "reason": "Track is not in Inbox."}
                continue

            updates = {f: v for f, v in fields.items() if v != (row[f] or "")}
            if not updates:
                results[track_id] = {"track_id": track_id, "status": "unchanged"}
                continue

            set_sql = ", ".join(f"{f} = ?" for f in updates)
            conn.execute(f"UPDATE tracks SET {set_sql} WHERE id = ?", (*updates.values(), track_id))
            for field, value in updates.items():
                field_provenance_service.record(
                    track_id, field, value, origin="user", source="bulk_edit",
                    reason="Manual bulk Inbox edit.", conn=conn,
                )
            changed_ids.append(track_id)
            results[track_id] = {"track_id": track_id, "status": "changed", "fields": list(updates)}
        conn.commit()

    from . import preparation_service  # local import breaks the module import cycle
    write_result = (
        preparation_service.write_tracks(changed_ids) if changed_ids
        else {"written_count": 0, "failed_count": 0, "warnings": [], "results": []}
    )
    for wr in write_result.get("results", []):
        entry = results.get(wr.get("track_id"))
        if entry is None:
            continue
        tw_status = wr.get("status")
        entry["tag_write_status"] = tw_status
        if tw_status in ("failed", "blocked"):
            entry["status"] = "failed"
            entry["reason"] = wr.get("reason") or "Metadata write-back to the file failed."
        else:
            # "applied" or "no_op" (approved value already matched the file)
            # both mean the field is correctly reflected on disk -- success.
            entry["status"] = "succeeded"

    for track_id, entry in results.items():
        if entry["status"] == "changed":
            # A DB update happened but no matching tag-write result came
            # back (e.g. build_plan itself failed for the whole chunk) --
            # do not silently claim success on an unverified write.
            entry["status"] = "failed"
            entry.setdefault("reason", "Metadata write-back could not be verified.")

    ordered_results = [results[tid] for tid in track_ids if tid in results]
    return {
        "selected_count": len(track_ids),
        "changed_count": len(changed_ids),
        "unchanged_count": sum(1 for r in ordered_results if r["status"] == "unchanged"),
        "succeeded_count": sum(1 for r in ordered_results if r["status"] == "succeeded"),
        "failed_count": sum(1 for r in ordered_results if r["status"] == "failed"),
        "skipped_count": sum(1 for r in ordered_results if r["status"] == "skipped"),
        "not_found_count": sum(1 for r in ordered_results if r["status"] == "not_found"),
        "results": ordered_results,
        "tag_write": write_result,
        "message": "Bulk edit applied to the managed Inbox copies.",
    }
