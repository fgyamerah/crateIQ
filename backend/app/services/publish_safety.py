"""
Shared, read-only path-safety checks for the Guided Publish flow.

Pure functions only — no filesystem writes, no subprocess calls. Used by
both the publish readiness contract and the guarded sync lifecycle so the
same safety rules apply whether a caller is just checking readiness or
about to actually run a sync.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

# Exact-match protected paths. Only equality is checked here (not "is under"),
# since many legitimate destinations legitimately live under /home or /mnt.
_PROTECTED_EXACT: Tuple[Path, ...] = (
    Path("/"),
    Path.home(),
    Path("/etc"),
    Path("/usr"),
    Path("/bin"),
    Path("/sbin"),
    Path("/boot"),
    Path("/root"),
    Path("/var"),
    Path("/sys"),
    Path("/proc"),
    Path("/dev"),
)


def describe_sync_destination_safety(
    source: Path, destination: Path
) -> Tuple[List[str], List[str]]:
    """
    Return (blockers, warnings) for a proposed sync destination relative to
    a source directory.

    Blockers mean the destination must not be used as-is:
      - destination resolves to the same path as the source
      - destination is nested inside the source, or is an ancestor of it
      - destination resolves to a bare filesystem root or another
        protected system path (home, /etc, /usr, ...)

    This never touches the filesystem beyond path resolution (no reads
    of file contents, no writes, no directory creation).
    """
    blockers: List[str] = []
    warnings: List[str] = []

    try:
        src_resolved = source.expanduser().resolve(strict=False)
        dst_resolved = destination.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        blockers.append(f"Could not resolve destination path: {exc}")
        return blockers, warnings

    if dst_resolved == src_resolved:
        blockers.append("Sync destination is the same path as the source.")
    else:
        try:
            dst_resolved.relative_to(src_resolved)
            blockers.append("Sync destination is nested inside the source.")
        except ValueError:
            pass
        try:
            src_resolved.relative_to(dst_resolved)
            blockers.append("Sync destination is an ancestor of the source.")
        except ValueError:
            pass

    if dst_resolved in _PROTECTED_EXACT:
        blockers.append(
            f"Sync destination resolves to a protected system path: {dst_resolved}"
        )

    # A managed-workspace source resolves to <root>/Library. Inbox and
    # Quarantine are siblings of Library, not nested inside it, so the
    # nested/ancestor checks above never catch them -- reject explicitly.
    if src_resolved.name == "Library":
        for zone in ("Inbox", "Quarantine"):
            if dst_resolved == src_resolved.parent / zone:
                blockers.append(f"Sync destination cannot be the managed {zone} folder.")

    return blockers, warnings


def evaluate_sync_paths(
    source: Optional[Path], destination: Path
) -> Tuple[List[str], List[str]]:
    """
    Full structural sync-safety evaluation shared by the readiness contract
    and the guarded sync lifecycle: existence/mount checks plus
    describe_sync_destination_safety() above. Read-only; no side effects.
    """
    blockers: List[str] = []
    warnings: List[str] = []

    dest_available = destination.exists() and destination.is_dir()
    if not dest_available:
        blockers.append(f"Sync destination is not mounted or does not exist: {destination}")

    if source is None:
        blockers.append("Unknown sync source.")
        return blockers, warnings

    source_available = source.exists() and source.is_dir()
    if not source_available:
        blockers.append(f"Sync source not found: {source}")
        return blockers, warnings

    safety_blockers, safety_warnings = describe_sync_destination_safety(source, destination)
    blockers.extend(safety_blockers)
    warnings.extend(safety_warnings)
    return blockers, warnings
