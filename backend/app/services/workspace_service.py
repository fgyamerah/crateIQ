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

from . import library_setup_service, tag_write_service
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
