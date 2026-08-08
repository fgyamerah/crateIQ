"""
Publish/SSD Sync source-and-destination resolution.

Source of truth
----------------
The sync/publish source is never a hardcoded personal path. It is derived
from the *active* library root at call time:

  * managed_workspace      -> <root>/Library (promoted, finished music only
                               -- never Inbox or Quarantine)
  * legacy_direct_library  -> <root> itself, preserving pre-managed-workspace
                               compatibility (a single flat root)
  * not_configured         -> raises ValueError; there is nothing safe to
                               sync yet

A pending (not-yet-active) library root never affects this -- only
core.library_root.selected_library_root() (the active root) is consulted.

Destination
-----------
The destination is a user-configured absolute path with no default and no
silent fallback. It is persisted locally (outside any library root, so it
survives a workspace switch) and validated with the same structural-safety
mechanism used for library/workspace roots, plus sync-specific relational
checks (never the source, never nested, never a managed zone).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

from ..core.library_root import assert_safe_new_root_path, selected_library_root
from . import workspace_service
from .publish_safety import describe_sync_destination_safety

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Deliberately repo-scoped rather than library-scoped, like
# settings_service.METADATA_SOURCES_PATH -- the configured destination is a
# runtime/local preference, not part of any music library, and must survive
# switching the active workspace root.
DESTINATION_SETTINGS_PATH = _REPO_ROOT / ".run" / "local" / "publish_sync_settings.json"

DestinationStatus = Literal["ready", "needs_setup", "not_mounted", "unsafe"]


def get_sync_source() -> Path:
    """
    Resolve the current Publish/SSD Sync source from the active workspace.

    Raises ValueError (never falls back to a hardcoded or inferred path) if
    the active root is not configured as either a managed workspace or a
    Legacy Direct Library.
    """
    root = selected_library_root()
    state = workspace_service.workspace_state(root)
    if state["state"] == "managed_workspace":
        return root / "Library"
    if state["state"] == "legacy_direct_library":
        return root
    raise ValueError(
        "The active library root is not configured as a managed workspace or "
        "a Legacy Direct Library yet. Configure a workspace in Settings "
        "before publishing or syncing."
    )


def _load_destination_settings() -> dict[str, Any]:
    try:
        raw = json.loads(DESTINATION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"destination": None}
    return raw if isinstance(raw, dict) else {"destination": None}


def _save_destination_settings(settings: dict[str, Any]) -> None:
    DESTINATION_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=DESTINATION_SETTINGS_PATH.parent, delete=False
    ) as handle:
        handle.write(json.dumps(settings, indent=2) + "\n")
        temporary = Path(handle.name)
    temporary.replace(DESTINATION_SETTINGS_PATH)


def get_configured_destination() -> Optional[Path]:
    """Read-only. Returns None if no destination has ever been configured."""
    raw = _load_destination_settings().get("destination")
    if not raw:
        return None
    return Path(raw)


def validate_destination_path(value: str) -> Path:
    """
    Structural safety check for a candidate destination. Raises ValueError
    with a clear, user-facing message on any unsafe path. Existence/mount
    status is intentionally not checked here -- a drive being unmounted is
    not itself unsafe; see describe_destination_status().

    Relational safety (same-as-source, nested, ancestor, protected system
    paths, and -- when the source is a managed Library folder -- the
    sibling Inbox/Quarantine zones) is delegated to
    publish_safety.describe_sync_destination_safety() so both this save path
    and the guarded sync lifecycle share one rule set.
    """
    resolved = assert_safe_new_root_path(value)
    blockers: List[str] = []
    try:
        source = get_sync_source()
    except ValueError:
        source = None
    if source is not None:
        safety_blockers, _warnings = describe_sync_destination_safety(source, resolved)
        blockers.extend(safety_blockers)
    if blockers:
        raise ValueError("; ".join(blockers))
    return resolved


def set_destination(value: str) -> Path:
    """Validate and persist a new destination. Never creates the directory."""
    resolved = validate_destination_path(value)
    _save_destination_settings({"destination": str(resolved)})
    return resolved


def describe_destination_status() -> Tuple[DestinationStatus, List[str], List[str]]:
    """
    Read-only status for the configured destination. Never mutates anything
    and never spawns a subprocess.

    Returns (status, blockers, warnings):
      * needs_setup -- no destination configured yet
      * unsafe      -- configured destination fails structural safety
      * not_mounted -- safe, but does not currently exist/is not mounted
      * ready       -- safe, exists, and is a directory
    """
    destination = get_configured_destination()
    if destination is None:
        return "needs_setup", ["No Publish/SSD Sync destination is configured yet."], []

    warnings: List[str] = []
    try:
        source = get_sync_source()
    except ValueError as exc:
        return "unsafe", [str(exc)], warnings
    blockers, safety_warnings = describe_sync_destination_safety(source, destination)
    warnings.extend(safety_warnings)
    if blockers:
        return "unsafe", blockers, warnings

    if not destination.exists() or not destination.is_dir():
        return "not_mounted", [f"Destination is not mounted or does not exist: {destination}"], warnings

    if not os.access(destination, os.W_OK):
        warnings.append(f"Destination exists but is not writable: {destination}")

    return "ready", [], warnings
