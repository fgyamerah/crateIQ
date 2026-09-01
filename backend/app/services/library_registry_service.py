"""Installation-scoped launcher library registry and read-only classifier.

This module deliberately does not import the managed-workspace setup helpers:
launcher inspection must never create a marker, initialize SQLite, migrate a
schema, scan audio, or otherwise change a candidate library.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import assert_path_under_root, assert_safe_new_root_path

_REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = _REPO_ROOT / ".run" / "local" / "library_registry.json"
LOCAL_ENV_PATH = _REPO_ROOT / ".run" / "local" / "crateiq.env"
REGISTRY_SCHEMA_VERSION = 1
RECENT_LIBRARY_LIMIT = 16
LAUNCHER_RECENT_LIMIT = 4
WORKSPACE_MARKER_NAME = ".crateiq-workspace.json"
WORKSPACE_MARKER_VERSION = 1
ZONE_NAMES = ("Inbox", "Library", "Quarantine")
_AUDIO_SUFFIXES = {".aiff", ".aif", ".alac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
_MAX_CLASSIFICATION_ENTRIES = 512
_SQLITE_IMMUTABLE_MIN_VERSION = (3, 22, 0)

# Every supported historical `processed.db` has this core pipeline schema.
# Requiring both the tables and their characteristic columns avoids treating a
# generic music database with a `tracks` table as CrateIQ-owned.
_LEGACY_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "tracks": frozenset({
        "id", "filepath", "filename", "status", "pipeline_ver",
        "key_musical", "key_camelot",
    }),
    "track_history": frozenset({
        "id", "filepath", "original_meta", "cleaned_meta", "actions",
        "rolled_back",
    }),
    "pipeline_runs": frozenset({
        "id", "run_at", "dry_run", "inbox_count", "processed", "rejected",
        "duplicates", "unsorted", "errors",
    }),
    "duplicate_groups": frozenset({
        "id", "run_id", "original", "duplicate", "reason", "resolved",
    }),
}


class MalformedRegistryError(ValueError):
    """The registry cannot be trusted and must not be rewritten implicitly."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be a UTC Z timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_path(value: str) -> Path:
    return assert_safe_new_root_path(value)


def _validated_entry(raw: object) -> dict[str, str | None]:
    try:
        if not isinstance(raw, dict):
            raise ValueError("recent library entry must be an object")
        if set(raw) - {"path", "display_name", "last_opened_at"}:
            raise ValueError("recent library entry contains unsupported fields")
        path = raw.get("path")
        if not isinstance(path, str):
            raise ValueError("recent library path is invalid")
        canonical = _canonical_path(path)
        if str(canonical) != path:
            raise ValueError("recent library path is not canonical")
        display_name = raw.get("display_name")
        if display_name is not None and (not isinstance(display_name, str) or not display_name.strip()):
            raise ValueError("recent library display_name is invalid")
        _timestamp(raw.get("last_opened_at"))
        return {"path": str(canonical), "display_name": display_name, "last_opened_at": raw["last_opened_at"]}
    except ValueError as exc:
        raise MalformedRegistryError("recent library entry is invalid") from exc


def _read_registry() -> list[dict[str, str | None]]:
    try:
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MalformedRegistryError("library registry is malformed and was left unchanged") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise MalformedRegistryError("library registry has an unsupported schema")
    entries = raw.get("recent_libraries")
    if not isinstance(entries, list):
        raise MalformedRegistryError("library registry recent_libraries is invalid")
    parsed = [_validated_entry(item) for item in entries]
    if len(parsed) > RECENT_LIBRARY_LIMIT:
        raise MalformedRegistryError("library registry exceeds its history limit")
    if len({entry["path"] for entry in parsed}) != len(parsed):
        raise MalformedRegistryError("library registry has duplicate paths")
    return sorted(parsed, key=lambda entry: _timestamp(entry["last_opened_at"]), reverse=True)


def _atomic_write_registry(entries: list[dict[str, str | None]]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"schema_version": REGISTRY_SCHEMA_VERSION, "recent_libraries": entries},
        indent=2,
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".library_registry.", suffix=".tmp", dir=REGISTRY_PATH.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, REGISTRY_PATH)
        directory_fd = os.open(REGISTRY_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_compatibility_root(root: Path | str) -> None:
    """Atomically save the next startup root without touching registry recency.

    The service launcher reads this as literal data; it is deliberately never
    sourced as shell.  The supervisor calls this only after a newly promoted
    backend has independently verified on the stable active endpoint.
    """
    canonical = _canonical_path(str(root))
    if not canonical.is_dir():
        raise ValueError("compatibility root must be an existing directory")
    LOCAL_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(LOCAL_ENV_PATH.parent, 0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".crateiq.env.", suffix=".tmp", dir=LOCAL_ENV_PATH.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("# Managed by CrateIQ. This file is local-only and contains no secrets.\n")
            handle.write(f"CRATEIQ_LIBRARY_ROOT={canonical}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, LOCAL_ENV_PATH)
        directory_fd = os.open(LOCAL_ENV_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def capture_compatibility_root_state() -> bytes | None:
    """Return the exact local compatibility-root file state for a rollback.

    This deliberately does not parse or normalize the file: the handoff
    supervisor must be able to restore the precise pre-promotion state,
    including the rootless no-file state.  The snapshot is kept in memory by
    the supervisor and is never written into activation state.
    """
    try:
        return LOCAL_ENV_PATH.read_bytes()
    except FileNotFoundError:
        return None


def compatibility_root_state_matches(previous: bytes | None) -> bool:
    """Whether the compatibility-root file exactly matches a prior snapshot."""
    try:
        return capture_compatibility_root_state() == previous
    except OSError:
        return False


def compatibility_root_matches(root: Path | str) -> bool:
    """Whether the on-disk compatibility root currently names ``root``."""
    try:
        expected = _canonical_path(str(root))
        actual = _compatibility_root()
    except (OSError, ValueError):
        return False
    return actual == expected


def restore_compatibility_root_state(previous: bytes | None) -> None:
    """Restore an exact pre-promotion compatibility-root state and fsync it.

    A late directory-fsync error may occur after ``os.replace``/``unlink``.
    Callers must therefore always follow this with
    :func:`compatibility_root_state_matches` before treating restoration as
    complete.
    """
    LOCAL_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(LOCAL_ENV_PATH.parent, 0o700)
    if previous is None:
        try:
            LOCAL_ENV_PATH.unlink()
        except FileNotFoundError:
            return
        directory_fd = os.open(LOCAL_ENV_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".crateiq.env.restore.", suffix=".tmp", dir=LOCAL_ENV_PATH.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, LOCAL_ENV_PATH)
        directory_fd = os.open(LOCAL_ENV_PATH.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def record_recent_library(value: str, *, display_name: str | None = None, opened_at: str | None = None) -> dict[str, str | None]:
    """Record an explicit open/compatibility root, newest-first and deduplicated."""
    canonical = _canonical_path(value)
    entries = _read_registry()  # Must happen before any write.
    timestamp = opened_at or _utc_now()
    _timestamp(timestamp)
    name = display_name.strip() if isinstance(display_name, str) and display_name.strip() else None
    entry: dict[str, str | None] = {
        "path": str(canonical),
        "display_name": name,
        "last_opened_at": timestamp,
    }
    updated = [item for item in entries if item["path"] != entry["path"]]
    updated.insert(0, entry)
    _atomic_write_registry(updated[:RECENT_LIBRARY_LIMIT])
    return entry


def _legacy_db_is_crateiq(db_path: Path, root: Path) -> bool:
    """Inspect stable historical CrateIQ schema evidence without SQLite writes."""
    try:
        if db_path.is_symlink() or not db_path.is_file():
            return False
        assert_path_under_root(db_path, root)
        # SQLite immutable mode is supported by the runtime's SQLite 3.45.1.
        # Do not fall back to mode=ro: opening a WAL database that way can
        # create a -shm file or recover/checkpoint state. Immutable mode may
        # conservatively miss uncheckpointed WAL-only evidence, which is safer
        # than mutating a candidate library during classification.
        if sqlite3.sqlite_version_info < _SQLITE_IMMUTABLE_MIN_VERSION:
            return False
        with sqlite3.connect(
            db_path.resolve().as_uri() + "?mode=ro&immutable=1",
            uri=True,
        ) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not set(_LEGACY_TABLE_COLUMNS).issubset(tables):
                return False
            for table, required_columns in _LEGACY_TABLE_COLUMNS.items():
                columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
                if not required_columns.issubset(columns):
                    return False
        return True
    except (OSError, sqlite3.Error, ValueError):
        return False


def _contains_audio_or_unsafe_link(root: Path) -> tuple[bool, bool]:
    """Bounded, no-follow scan returning (contains_audio, unsafe_symlink)."""
    inspected = 0
    try:
        for directory, directories, filenames in os.walk(root, followlinks=False):
            parent = Path(directory)
            safe_directories: list[str] = []
            for name in directories:
                inspected += 1
                if inspected > _MAX_CLASSIFICATION_ENTRIES:
                    return False, True
                candidate = parent / name
                if candidate.is_symlink():
                    return False, True
                safe_directories.append(name)
            directories[:] = safe_directories
            for name in filenames:
                inspected += 1
                if inspected > _MAX_CLASSIFICATION_ENTRIES:
                    return False, True
                candidate = parent / name
                if candidate.is_symlink():
                    return False, True
                if candidate.suffix.lower() in _AUDIO_SUFFIXES:
                    return True, False
    except OSError:
        return False, True
    return False, False


def _classification(path: Path, classification: str, available: bool, message: str) -> dict[str, Any]:
    return {
        "canonical_path": str(path),
        "classification": classification,
        "available": available,
        "message": message,
    }


def classify_library_candidate(value: str) -> dict[str, Any]:
    """Fail-closed, read-only classification for a launcher candidate path."""
    root = _canonical_path(value)
    if not root.exists():
        return _classification(root, "missing", False, "This library path is unavailable because it does not exist.")
    if not root.is_dir():
        return _classification(root, "malformed_or_unsafe", False, "Library path must be a safe directory.")

    marker = root / WORKSPACE_MARKER_NAME
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or not marker.is_file():
            return _classification(root, "malformed_or_unsafe", False, "Workspace marker is unsafe.")
        try:
            if marker.stat().st_size > 64 * 1024:
                raise ValueError("marker is too large")
            data = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != WORKSPACE_MARKER_VERSION:
                raise ValueError("unsupported workspace marker")
            for zone in ZONE_NAMES:
                zone_path = root / zone
                if zone_path.is_symlink() or not zone_path.is_dir():
                    raise ValueError("workspace zone is missing or unsafe")
                assert_path_under_root(zone_path, root)
        except (OSError, ValueError, json.JSONDecodeError):
            return _classification(root, "malformed_or_unsafe", False, "Workspace marker or zones are malformed or unsafe.")
        return _classification(root, "managed_workspace", True, "Valid managed CrateIQ workspace.")

    db_path = root / "logs" / "processed.db"
    if _legacy_db_is_crateiq(db_path, root):
        return _classification(root, "legacy_direct_library", True, "Existing CrateIQ Legacy Direct Library index.")

    has_audio, unsafe_link = _contains_audio_or_unsafe_link(root)
    if unsafe_link:
        return _classification(root, "malformed_or_unsafe", False, "Candidate contains an unsafe symlink or unreadable path.")
    try:
        empty = not any(root.iterdir())
    except OSError:
        empty = False
    if empty:
        return _classification(root, "empty_folder", False, "Empty safe folder; it may later be offered for explicit creation.")
    if has_audio:
        return _classification(root, "external_music_folder", False, "Music files were found without CrateIQ workspace or index evidence.")
    return _classification(root, "malformed_or_unsafe", False, "Folder is not empty and lacks valid CrateIQ library evidence.")


def _compatibility_root() -> Path | None:
    """Return only the root explicitly saved by Settings for rootless seeding."""
    saved_value: str | None = None
    try:
        for line in LOCAL_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("CRATEIQ_LIBRARY_ROOT="):
                saved_value = line.partition("=")[2]
    except (FileNotFoundError, OSError):
        pass
    if not saved_value:
        return None
    try:
        root = _canonical_path(saved_value)
    except ValueError:
        return None
    if root.is_dir() and os.access(root, os.R_OK):
        return root
    return None


def bootstrap_compatibility_registry() -> None:
    """Seed the installation registry at launcher startup, never from a GET."""
    root = _compatibility_root()
    if root is None:
        return
    entries = _read_registry()
    if any(entry["path"] == str(root) for entry in entries):
        return
    # Deliberately re-read through record_recent_library so malformed input
    # cannot be replaced by a compatibility write between read and write.
    record_recent_library(str(root))


def get_launcher_registry() -> dict[str, Any]:
    """Return only the four newest recent entries plus cheap local status."""
    try:
        entries = _read_registry()
    except MalformedRegistryError as exc:
        return {"recent_libraries": [], "registry_status": "malformed", "message": str(exc)}
    recent: list[dict[str, Any]] = []
    for entry in entries[:LAUNCHER_RECENT_LIMIT]:
        classified = classify_library_candidate(str(entry["path"]))
        recent.append({
            **entry,
            "display_name": entry["display_name"] or Path(str(entry["path"])).name,
            "availability": classified["available"],
            "classification": classified["classification"],
        })
    return {"recent_libraries": recent, "registry_status": "ready", "message": None}
