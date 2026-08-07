"""
Shared, read-only path-safety checks for the Guided Publish flow.

Pure functions only — no filesystem writes, no subprocess calls. Used by
both the publish readiness contract and the guarded sync lifecycle so the
same safety rules apply whether a caller is just checking readiness or
about to actually run a sync.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

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

    return blockers, warnings
