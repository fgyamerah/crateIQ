"""Local-operator-only filesystem administration for the Library Launcher.

HTTP access control lives in the launcher router. This service supplies the
second safety layer: constrained, bounded browsing; canonical path and symlink
checks; strict existing-library registration; and rollback-safe creation of a
new managed workspace. It never activates a library or contacts the supervisor.
"""
from __future__ import annotations

import os
import shutil
import stat
import unicodedata
from pathlib import Path
from typing import Any

from . import library_registry_service, workspace_service
from ..core.library_root import assert_safe_new_root_path

BROWSE_DEFAULT_LIMIT = 50
BROWSE_MAX_LIMIT = 100
_MAX_BROWSE_SCAN_ENTRIES = 512
_MAX_LIBRARY_NAME_LENGTH = 120
_ACTIVATABLE = frozenset({"managed_workspace", "legacy_direct_library"})
_RESERVED_NAMES = {
    ".", "..", "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_ALLOWED_NAME_PUNCTUATION = frozenset(" -_().[]")
_ROLLBACK_TOP_LEVEL_DIRS = frozenset({"Inbox", "Library", "Quarantine", "logs", "exports"})
_ROLLBACK_LOG_FILES = frozenset({
    "processed.db", "processed.db-journal", "processed.db-wal", "processed.db-shm",
})


class BrowseLocationError(ValueError):
    """A browse location is unavailable, outside allowed roots, or unsafe."""


class LibraryNameError(ValueError):
    """A requested new-library name is unsafe."""


class LibraryCollisionError(FileExistsError):
    """A requested new-library target already exists."""


class LibraryInitializationError(RuntimeError):
    def __init__(self, *, partial_left: bool) -> None:
        self.partial_left = partial_left
        message = "The library could not be initialized."
        if partial_left:
            message += " A partial directory was left in place because safe rollback could not be proven."
        else:
            message += " The operation-created directory was rolled back."
        super().__init__(message)


class RegistryAfterCreateError(RuntimeError):
    """Initialization succeeded but registry persistence did not."""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_has_symlink_component(value: str | Path) -> bool:
    """Check existing path components without following a symlink."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return False
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise BrowseLocationError("The selected location is unavailable.") from exc
        if stat.S_ISLNK(mode):
            return True
    return False


def _candidate_browse_roots() -> list[Path]:
    """Return local, non-secret starting roots without hardcoded user paths."""
    home = Path.home().expanduser()
    candidates = [home / "Music", home, Path("/mnt"), Path("/media")]
    try:
        candidates.extend(Path(str(entry["path"])).parent for entry in library_registry_service._read_registry())
    except library_registry_service.MalformedRegistryError:
        pass

    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = assert_safe_new_root_path(str(candidate))
        except ValueError:
            continue
        if (
            not resolved.is_dir()
            or _path_has_symlink_component(candidate)
            or not os.access(resolved, os.R_OK | os.X_OK)
            or resolved in roots
        ):
            continue
        roots.append(resolved)
    return roots


def _browse_roots() -> list[Path]:
    """Indirection kept small so focused tests never inspect the real host."""
    return _candidate_browse_roots()


def _require_allowed_location(value: str, *, writable: bool = False) -> Path:
    if _path_has_symlink_component(value):
        raise BrowseLocationError("Symlink locations are not allowed.")
    try:
        resolved = assert_safe_new_root_path(value)
    except ValueError as exc:
        raise BrowseLocationError("The selected location is unsafe.") from exc
    roots = _browse_roots()
    if not roots or not any(_is_within(resolved, root) for root in roots):
        raise BrowseLocationError("The selected location is outside the allowed browse roots.")
    if not resolved.exists():
        raise BrowseLocationError("The selected location does not exist.")
    if not resolved.is_dir():
        raise BrowseLocationError("The selected location is not a directory.")
    access = os.R_OK | os.X_OK | (os.W_OK if writable else 0)
    if not os.access(resolved, access):
        raise BrowseLocationError("The selected location is not accessible.")
    return resolved


def _root_payload(root: Path) -> dict[str, str]:
    return {"display_name": root.name or "Home", "path": str(root)}


def _unsafe_entry(path: Path, *, reason: str, entry_type: str = "directory") -> dict[str, Any]:
    return {
        "display_name": path.name,
        "path": str(path),
        "entry_type": entry_type,
        "selectable": False,
        "classification": "unsafe",
        "reason": reason,
    }


def _classify_browse_entry(path: Path) -> dict[str, Any]:
    try:
        classified = library_registry_service.classify_library_candidate(str(path))
    except (OSError, ValueError):
        return _unsafe_entry(path, reason="This directory is unsafe or unavailable.")
    return {
        "display_name": path.name,
        "path": str(path),
        "entry_type": "directory",
        "selectable": classified["available"] and classified["classification"] in _ACTIVATABLE,
        "classification": classified["classification"],
        "reason": classified["message"],
    }


def browse_directories(location: str | None, *, offset: int, limit: int) -> dict[str, Any]:
    """List one directory level with fixed bounds and deterministic ordering."""
    roots = _browse_roots()
    if not roots:
        raise BrowseLocationError("No safe browse starting location is available.")
    current = _require_allowed_location(location, writable=False) if location else roots[0]

    scanned: list[tuple[str, Path, bool]] = []
    scan_truncated = False
    try:
        with os.scandir(current) as iterator:
            for index, entry in enumerate(iterator):
                if index >= _MAX_BROWSE_SCAN_ENTRIES:
                    scan_truncated = True
                    break
                if entry.name.startswith("."):
                    continue
                is_link = entry.is_symlink()
                if is_link or entry.is_dir(follow_symlinks=False):
                    scanned.append((entry.name, Path(entry.path), is_link))
    except (OSError, PermissionError) as exc:
        raise BrowseLocationError("The selected location cannot be read.") from exc

    scanned.sort(key=lambda item: (item[0].casefold(), item[0]))
    page = scanned[offset:offset + limit]
    entries: list[dict[str, Any]] = []
    for _, path, is_link in page:
        if is_link:
            entries.append(_unsafe_entry(path, entry_type="symlink", reason="Symlink entries cannot be browsed or registered."))
        else:
            entries.append(_classify_browse_entry(path))

    parent = current.parent
    parent_path = None
    if parent != current and any(_is_within(parent, root) for root in roots):
        parent_path = str(parent)
    return {
        "current_path": str(current),
        "parent_path": parent_path,
        "roots": [_root_payload(root) for root in roots],
        "entries": entries,
        "offset": offset,
        "limit": limit,
        "truncated": scan_truncated or offset + limit < len(scanned),
    }


def register_existing_library(value: str) -> dict[str, Any]:
    """Revalidate a selected path and register only strict activatable types."""
    root = _require_allowed_location(value)
    try:
        return library_registry_service.register_library(str(root))
    except library_registry_service.UnsafeRegistryLibraryError:
        raise
    except OSError as exc:
        raise RuntimeError("The library registry could not be updated.") from exc


def validate_library_name(value: str) -> str:
    """Validate an exact single path segment; never normalize into another target."""
    if not value or value != value.strip():
        raise LibraryNameError("Library name must be non-empty and must not start or end with whitespace.")
    if len(value) > _MAX_LIBRARY_NAME_LENGTH:
        raise LibraryNameError(f"Library name must be at most {_MAX_LIBRARY_NAME_LENGTH} characters.")
    if value != unicodedata.normalize("NFC", value):
        raise LibraryNameError("Library name must use normalized Unicode characters.")
    if value.startswith(".") or value.endswith((".", " ")):
        raise LibraryNameError("Library name cannot be hidden or end with a dot or space.")
    if value.rstrip(". ").split(".", 1)[0].upper() in _RESERVED_NAMES:
        raise LibraryNameError("Library name is reserved.")
    if any(not (character.isalnum() or character in _ALLOWED_NAME_PUNCTUATION) for character in value):
        raise LibraryNameError("Library name contains a path separator, control character, or command syntax.")
    return value


def _rollback_created_root(root: Path, identity: tuple[int, int]) -> bool:
    """Remove only the exact operation-created workspace tree when provable."""
    try:
        current = root.lstat()
        if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != identity:
            return False
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                return False
            relative = candidate.relative_to(root)
            if len(relative.parts) == 1:
                if candidate.is_dir() and relative.name in _ROLLBACK_TOP_LEVEL_DIRS:
                    continue
                if candidate.is_file() and relative.name == workspace_service.WORKSPACE_MARKER_NAME:
                    continue
                return False
            if len(relative.parts) == 2 and relative.parts[0] == "logs" and candidate.is_file():
                if relative.parts[1] in _ROLLBACK_LOG_FILES:
                    continue
            return False
        shutil.rmtree(root)
        return not root.exists()
    except OSError:
        return False


def create_managed_library(parent_directory: str, name: str) -> dict[str, Any]:
    """Create, initialize, validate, then register a new managed workspace."""
    parent = _require_allowed_location(parent_directory, writable=True)
    safe_name = validate_library_name(name)
    try:
        target = assert_safe_new_root_path(str(parent / safe_name))
    except ValueError as exc:
        raise LibraryNameError("Library name resolves to an unsafe target.") from exc
    if not _is_within(target, parent):  # Defensive even after single-segment validation.
        raise LibraryNameError("Library name escapes the selected parent directory.")
    if os.path.lexists(target):
        raise LibraryCollisionError("A file or directory with this library name already exists.")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_fd = os.open(parent, flags)
    except OSError as exc:
        raise BrowseLocationError("The selected parent directory is unavailable.") from exc
    created = False
    identity: tuple[int, int] | None = None
    try:
        parent_stat = os.fstat(parent_fd)
        current_parent_stat = parent.stat()
        if (parent_stat.st_dev, parent_stat.st_ino) != (current_parent_stat.st_dev, current_parent_stat.st_ino):
            raise BrowseLocationError("The selected parent directory changed during creation.")
        try:
            os.mkdir(safe_name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise LibraryCollisionError("A file or directory with this library name already exists.") from exc
        except OSError as exc:
            raise BrowseLocationError("The new library directory could not be created.") from exc
        created = True
        try:
            created_stat = os.stat(safe_name, dir_fd=parent_fd, follow_symlinks=False)
            path_stat = target.lstat()
        except OSError as exc:
            try:
                os.rmdir(safe_name, dir_fd=parent_fd)
                created = False
            except OSError:
                pass
            raise BrowseLocationError("The new library directory could not be verified.") from exc
        identity = (created_stat.st_dev, created_stat.st_ino)
        if identity != (path_stat.st_dev, path_stat.st_ino) or stat.S_ISLNK(path_stat.st_mode):
            try:
                os.rmdir(safe_name, dir_fd=parent_fd)
                created = False
            except OSError:
                pass
            raise BrowseLocationError("The new library directory changed during creation.")
    finally:
        os.close(parent_fd)

    try:
        workspace_service.configure_workspace(target)
        classified = library_registry_service.classify_library_candidate(str(target))
        if not classified["available"] or classified["classification"] != "managed_workspace":
            raise ValueError("new workspace did not pass managed-library classification")
    except Exception as exc:
        rolled_back = bool(created and identity and _rollback_created_root(target, identity))
        raise LibraryInitializationError(partial_left=not rolled_back) from exc

    try:
        return library_registry_service.register_library(str(target), display_name=safe_name)
    except Exception as exc:
        raise RegistryAfterCreateError(
            "The managed library was created successfully, but registry persistence failed; retry registration."
        ) from exc
