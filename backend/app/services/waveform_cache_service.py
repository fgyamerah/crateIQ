"""Bounded waveform cache accounting, eviction, and reconciliation (W6).

The waveform cache is derived, disposable, CrateIQ-owned data. Deleting all of
it must have no effect on source audio, tags, metadata, playback, crates,
Music Review, Serato, Rekordbox, or Mixed In Key.

Every deletion in this module is structurally contained:

* candidates are always *derived internally* from a validated cache root and
  a known filename layout — never supplied by an API caller;
* the directory walk never follows symlinks, and any symlink encountered is
  skipped rather than resolved and deleted through;
* each candidate passes W1's :func:`assert_waveform_cleanup_candidate`, which
  canonicalizes, rejects traversal, requires strict containment below the
  cache root, refuses the cache root itself, and refuses anything overlapping
  the configured music library root;
* only regular files matching CrateIQ's own artifact/temp naming are ever
  removed. Unknown files and all directories are left alone.

Nothing here reads, opens, decodes, or hashes source audio.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import weakref
from dataclasses import dataclass
from pathlib import Path

from ..core.waveform_cache import (
    ValidatedWaveformCacheRoot,
    WaveformCacheSafetyError,
    assert_waveform_cleanup_candidate,
)
from ..models.waveform import WAVEFORM_ALGORITHM_VERSION
from . import waveform_artifact_service, waveform_job_service

log = logging.getLogger(__name__)

# Prune only once usage exceeds the configured maximum, then free down to this
# fraction of it. The gap is deliberate hysteresis: without it the cache would
# thrash, deleting a file on every publication that sits near the limit.
CLEANUP_TARGET_RATIO = 0.80

# A temp file younger than this may still belong to an in-flight publication.
# The documented policy in the waveform ADR (section 19) is 24 hours; the
# longest an extraction can legitimately run is `MAX_TIMEOUT_SECONDS` (20
# minutes), so this leaves a very wide margin.
TEMP_FILE_MIN_AGE_SECONDS = 24 * 3600.0

# Artifacts under a superseded schema/algorithm layout can never be served —
# a version mismatch is always a cache miss — so they are pure dead weight.
# They are still given a grace period before removal so an accidental version
# bump that gets reverted does not throw away a usable cache.
SUPERSEDED_LAYOUT_MIN_AGE_SECONDS = 7 * 24 * 3600.0

_ARTIFACT_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json\.gz$")
_TEMP_NAME_RE = re.compile(r"^\.tmp\.[0-9a-f]{32}\.json\.gz$")

# One backend process owns this cache, so an asyncio lock is enough to stop
# two maintenance passes from racing each other over the same candidates.
#
# The lock is created per running loop rather than once at import. An
# `asyncio.Lock` binds itself to the first loop that awaits it and then
# refuses any other, which would turn a second `asyncio.run` in one process
# into a RuntimeError. The scheduler already tolerates being restarted under
# a new loop; cache maintenance must too.
_cleanup_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _cleanup_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _cleanup_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _cleanup_locks[loop] = lock
    return lock


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    size_bytes: int
    generation_key: str | None
    is_temp: bool
    modified_at: float
    superseded_layout: bool = False


@dataclass(frozen=True)
class CacheUsage:
    total_bytes: int
    artifact_count: int
    artifact_bytes: int
    temp_count: int
    temp_bytes: int
    superseded_count: int = 0
    superseded_bytes: int = 0


@dataclass(frozen=True)
class CleanupOutcome:
    removed_temp: int = 0
    removed_superseded: int = 0
    removed_orphan: int = 0
    removed_stale: int = 0
    removed_lru: int = 0
    freed_bytes: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    over_limit: bool = False
    target_met: bool = True

    @property
    def removed_total(self) -> int:
        return (
            self.removed_temp
            + self.removed_superseded
            + self.removed_orphan
            + self.removed_stale
            + self.removed_lru
        )


@dataclass(frozen=True)
class CacheClearPreview:
    """What a confirmed "clear waveform cache" would remove. Nothing is deleted."""

    artifact_count: int
    temp_count: int
    total_bytes: int
    ready_track_count: int


@dataclass(frozen=True)
class CacheClearOutcome:
    removed_files: int
    freed_bytes: int
    reset_track_states: int
    remaining_files: int


def _algorithm_dirs(root: Path) -> list[Path]:
    """Return CrateIQ waveform layout directories, current and superseded.

    Only ``<root>/v*/<algorithm>/`` shapes are considered. Unknown top-level
    directories inside the cache root are ignored entirely rather than
    guessed at.
    """
    found: list[Path] = []
    try:
        layout_dirs = sorted(entry for entry in root.iterdir() if entry.is_dir() and not entry.is_symlink())
    except (OSError, FileNotFoundError):
        return found
    for layout in layout_dirs:
        if not re.fullmatch(r"v\d+", layout.name):
            continue
        try:
            for algorithm in sorted(layout.iterdir()):
                if algorithm.is_dir() and not algorithm.is_symlink():
                    found.append(algorithm)
        except OSError:
            continue
    return found


def _current_layout_dir(root: Path) -> Path:
    """The one layout directory artifacts are currently published into."""
    return root / waveform_artifact_service.CACHE_LAYOUT_VERSION / WAVEFORM_ALGORITHM_VERSION


def scan_cache_entries(validated: ValidatedWaveformCacheRoot) -> list[CacheEntry]:
    """Enumerate CrateIQ-owned cache files without following symlinks."""
    entries: list[CacheEntry] = []
    current_layout = _current_layout_dir(validated.root)
    for algorithm_dir in _algorithm_dirs(validated.root):
        superseded = algorithm_dir != current_layout
        # followlinks=False keeps a symlinked directory planted inside the
        # cache from steering the walk out to unrelated files.
        for dirpath, dirnames, filenames in os.walk(algorithm_dir, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]
            for name in filenames:
                candidate = current / name
                if candidate.is_symlink():
                    continue  # never resolve or delete through a link
                artifact = _ARTIFACT_NAME_RE.match(name)
                temp = _TEMP_NAME_RE.match(name)
                if not artifact and not temp:
                    continue  # unknown file: not ours, leave it alone
                try:
                    stat_result = candidate.stat()
                except OSError:
                    continue
                entries.append(CacheEntry(
                    path=candidate,
                    size_bytes=stat_result.st_size,
                    generation_key=name[:64] if artifact else None,
                    is_temp=bool(temp),
                    modified_at=stat_result.st_mtime,
                    superseded_layout=superseded,
                ))
    return entries


def cache_usage(validated: ValidatedWaveformCacheRoot) -> CacheUsage:
    """Total bytes owned by the waveform cache. Never counts source audio."""
    entries = scan_cache_entries(validated)
    artifacts = [entry for entry in entries if not entry.is_temp]
    temps = [entry for entry in entries if entry.is_temp]
    superseded = [entry for entry in entries if entry.superseded_layout]
    artifact_bytes = sum(entry.size_bytes for entry in artifacts)
    temp_bytes = sum(entry.size_bytes for entry in temps)
    return CacheUsage(
        total_bytes=artifact_bytes + temp_bytes,
        artifact_count=len(artifacts),
        artifact_bytes=artifact_bytes,
        temp_count=len(temps),
        temp_bytes=temp_bytes,
        superseded_count=len(superseded),
        superseded_bytes=sum(entry.size_bytes for entry in superseded),
    )


def _delete_contained(path: Path, validated: ValidatedWaveformCacheRoot) -> int:
    """Delete one proven-contained regular file. Returns bytes freed."""
    try:
        contained = assert_waveform_cleanup_candidate(path, validated)
    except WaveformCacheSafetyError:
        log.warning("waveform cleanup refused an uncontained candidate")
        return 0
    if contained.is_symlink() or not contained.is_file():
        return 0
    try:
        size = contained.stat().st_size
        contained.unlink()
    except OSError:
        return 0
    return size


def _prune_empty_superseded_dirs(validated: ValidatedWaveformCacheRoot) -> int:
    """Remove empty directories left behind by a superseded layout.

    Only ``rmdir`` is used, so a directory holding anything at all — including
    a file CrateIQ does not own — survives. The current layout, the cache
    root, and anything outside the cache root are never candidates.
    """
    current_layout = _current_layout_dir(validated.root)
    removed = 0
    for algorithm_dir in _algorithm_dirs(validated.root):
        if algorithm_dir == current_layout:
            continue
        # topdown=False walks deepest-first, so a shard directory is emptied
        # before the layout directory that contains it.
        for dirpath, _dirnames, _filenames in os.walk(
            algorithm_dir, topdown=False, followlinks=False
        ):
            candidate = Path(dirpath)
            if candidate.is_symlink():
                continue
            try:
                assert_waveform_cleanup_candidate(candidate, validated)
            except WaveformCacheSafetyError:
                continue
            try:
                candidate.rmdir()  # fails unless genuinely empty
                removed += 1
            except OSError:
                continue
        # The `v<n>` parent is only removed once every layout under it is gone.
        parent = algorithm_dir.parent
        if parent != validated.root:
            try:
                assert_waveform_cleanup_candidate(parent, validated)
                parent.rmdir()
                removed += 1
            except (WaveformCacheSafetyError, OSError):
                pass
    return removed


def cleanup_cache(
    validated: ValidatedWaveformCacheRoot,
    *,
    max_cache_bytes: int,
    protect_generation_key: str | None = None,
    temp_min_age_seconds: float = TEMP_FILE_MIN_AGE_SECONDS,
    superseded_min_age_seconds: float = SUPERSEDED_LAYOUT_MIN_AGE_SECONDS,
    now: float | None = None,
) -> CleanupOutcome:
    """Free cache space in ascending order of regret.

    Order: abandoned temp files, then aged-out superseded-layout artifacts,
    then orphan artifacts nothing references, then artifacts whose track state
    is no longer ``ready``, and only then least-recently-used ready artifacts.
    ``protect_generation_key`` shields a just-published artifact so a
    publication cannot immediately evict itself.

    Abandoned temp files and aged-out superseded layouts are always swept
    regardless of the size limit — neither can ever be served. The size-driven
    tiers run only while usage exceeds ``max_cache_bytes``.
    """
    current_time = time.time() if now is None else now
    entries = scan_cache_entries(validated)
    bytes_before = sum(entry.size_bytes for entry in entries)

    removed = {"temp": 0, "superseded": 0, "orphan": 0, "stale": 0, "lru": 0}
    freed = 0
    usage = bytes_before
    swept: set[Path] = set()

    def _sweep(entry: CacheEntry, bucket: str) -> None:
        """Unconditionally remove one dead file, outside the size tiers."""
        nonlocal freed, usage
        released = _delete_contained(entry.path, validated)
        if released or not entry.path.exists():
            swept.add(entry.path)
            removed[bucket] += 1
            freed += released
            usage -= released

    # 1. Abandoned temp files. A young temp file may be an active publication.
    for entry in entries:
        if not entry.is_temp:
            continue
        if current_time - entry.modified_at < temp_min_age_seconds:
            continue
        _sweep(entry, "temp")

    # 2. Superseded schema/algorithm layouts. A version mismatch is always a
    # cache miss, so these can never be served; they age out on their own
    # schedule rather than waiting for storage pressure.
    for entry in entries:
        if not entry.superseded_layout or entry.path in swept:
            continue
        if current_time - entry.modified_at < superseded_min_age_seconds:
            continue
        _sweep(entry, "superseded")
    _prune_empty_superseded_dirs(validated)

    target_bytes = int(max_cache_bytes * CLEANUP_TARGET_RATIO)
    over_limit = usage > max_cache_bytes
    if not over_limit:
        return CleanupOutcome(
            removed_temp=removed["temp"],
            removed_superseded=removed["superseded"],
            freed_bytes=freed,
            bytes_before=bytes_before,
            bytes_after=usage,
            over_limit=False,
            target_met=True,
        )

    artifacts = {
        entry.generation_key: entry
        for entry in entries
        if not entry.is_temp and entry.generation_key and entry.path not in swept
    }
    referenced = waveform_job_service.list_referenced_generation_keys()
    ordered_refs = waveform_job_service.list_cached_artifacts()

    def _try_remove(key: str, bucket: str) -> None:
        """Evict one artifact if still over target. Counts only real deletions."""
        nonlocal freed, usage
        if usage <= target_bytes:
            return
        if key == protect_generation_key:
            return
        entry = artifacts.get(key)
        if entry is None:
            return
        released = _delete_contained(entry.path, validated)
        if entry.path.exists():
            return  # refused by containment or undeletable; keep accounting honest
        artifacts.pop(key, None)
        removed[bucket] += 1
        freed += released
        usage -= released

    # 3. Orphans: present on disk, referenced by no track state at all.
    for key in [k for k in artifacts if k not in referenced]:
        _try_remove(key, "orphan")

    # 4. Artifacts whose track no longer advertises them as ready.
    for ref in ordered_refs:
        if ref.status != "ready":
            _try_remove(ref.generation_key, "stale")

    # 5. Least-recently-used ready artifacts, oldest access first.
    for ref in ordered_refs:
        if ref.status != "ready":
            continue
        if ref.generation_key not in artifacts:
            continue
        _try_remove(ref.generation_key, "lru")
        # jobs.db must not keep advertising an artifact W6 just deleted.
        if ref.generation_key not in artifacts:
            waveform_job_service.mark_artifact_unavailable(ref.library_id, ref.track_id)

    outcome = CleanupOutcome(
        removed_temp=removed["temp"],
        removed_superseded=removed["superseded"],
        removed_orphan=removed["orphan"],
        removed_stale=removed["stale"],
        removed_lru=removed["lru"],
        freed_bytes=freed,
        bytes_before=bytes_before,
        bytes_after=usage,
        over_limit=True,
        target_met=usage <= target_bytes,
    )
    log.info(
        "waveform cache cleanup removed=%d temp=%d superseded=%d orphan=%d stale=%d lru=%d "
        "freed_bytes=%d bytes_before=%d bytes_after=%d target_met=%s",
        outcome.removed_total, outcome.removed_temp, outcome.removed_superseded,
        outcome.removed_orphan, outcome.removed_stale, outcome.removed_lru,
        outcome.freed_bytes, outcome.bytes_before, outcome.bytes_after, outcome.target_met,
    )
    return outcome


async def cleanup_cache_locked(
    validated: ValidatedWaveformCacheRoot,
    *,
    max_cache_bytes: int,
    protect_generation_key: str | None = None,
) -> CleanupOutcome:
    """Run one cleanup pass, serialized against any other pass in-process."""
    async with _cleanup_lock():
        return await asyncio.to_thread(
            cleanup_cache,
            validated,
            max_cache_bytes=max_cache_bytes,
            protect_generation_key=protect_generation_key,
        )


def reconcile_ready_states(validated: ValidatedWaveformCacheRoot) -> int:
    """Repair tracks claiming ``ready`` whose artifact is gone.

    Covers a crash between atomic publication and the ready transaction, an
    externally deleted cache file, and eviction by an earlier pass. Nothing is
    regenerated — the track simply becomes ``stale`` and a later explicit
    request can rebuild it.
    """
    repaired = 0
    for ref in waveform_job_service.list_ready_states():
        try:
            path = waveform_artifact_service.artifact_path(validated, ref.generation_key)
        except waveform_artifact_service.WaveformArtifactError:
            waveform_job_service.mark_artifact_unavailable(
                ref.library_id, ref.track_id,
                error_code=waveform_job_service.ERROR_ARTIFACT_MISSING,
            )
            repaired += 1
            continue
        if not path.is_file():
            waveform_job_service.mark_artifact_unavailable(
                ref.library_id, ref.track_id,
                error_code=waveform_job_service.ERROR_ARTIFACT_MISSING,
            )
            repaired += 1
    if repaired:
        log.info("waveform startup reconciliation repaired_ready_states=%d", repaired)
    return repaired


def startup_reconcile(
    validated: ValidatedWaveformCacheRoot,
    *,
    max_cache_bytes: int,
) -> tuple[int, CacheUsage]:
    """Lightweight startup pass: sweep old temps and repair missing artifacts.

    Deliberately does not decode audio, hash sources, scan the music library,
    or regenerate anything.
    """
    repaired = reconcile_ready_states(validated)
    cleanup_cache(validated, max_cache_bytes=max_cache_bytes)
    # Operational-row retention. These delete only CrateIQ's own bookkeeping
    # rows in jobs.db; no artifact, source, or trusted pipeline row is touched.
    purged_jobs = waveform_job_service.purge_expired_job_rows()
    purged_states = waveform_job_service.purge_expired_track_states()
    usage = cache_usage(validated)
    log.info(
        "waveform cache startup usage_bytes=%d limit_bytes=%d artifacts=%d temps=%d "
        "superseded=%d purged_job_rows=%d purged_state_rows=%d",
        usage.total_bytes, max_cache_bytes, usage.artifact_count, usage.temp_count,
        usage.superseded_count, purged_jobs, purged_states,
    )
    return repaired, usage


# ---------------------------------------------------------------------------
# Manual "clear waveform cache" action
#
# Preview first, then an explicitly confirmed clear. Both are scoped to the
# validated cache root and to CrateIQ's own artifact/temp naming, so the
# worst case of a confirmed clear is that every waveform has to be explicitly
# regenerated. No source audio, tag, playlist, crate, review, or DJ-database
# value is involved in either direction.
# ---------------------------------------------------------------------------


def preview_clear_cache(validated: ValidatedWaveformCacheRoot) -> CacheClearPreview:
    """Report exactly what a confirmed clear would remove. Deletes nothing."""
    usage = cache_usage(validated)
    return CacheClearPreview(
        artifact_count=usage.artifact_count,
        temp_count=usage.temp_count,
        total_bytes=usage.total_bytes,
        ready_track_count=len(waveform_job_service.list_ready_states()),
    )


def clear_cache(validated: ValidatedWaveformCacheRoot) -> CacheClearOutcome:
    """Remove every CrateIQ-owned cache file and drop all ready claims.

    Each deletion goes through the same containment assertion as ordinary
    cleanup, so an unknown file, a symlink, a directory, or anything outside
    the validated cache root survives untouched. Track states are reset only
    after their artifact is actually gone, and nothing is regenerated — a
    cleared waveform simply returns to ``stale`` until explicitly rebuilt.
    """
    removed = 0
    freed = 0
    for entry in scan_cache_entries(validated):
        released = _delete_contained(entry.path, validated)
        if entry.path.exists():
            continue
        removed += 1
        freed += released

    reset = 0
    for ref in waveform_job_service.list_ready_states():
        waveform_job_service.mark_artifact_unavailable(
            ref.library_id, ref.track_id,
            error_code=waveform_job_service.ERROR_CACHE_CLEARED,
        )
        reset += 1

    _prune_empty_superseded_dirs(validated)
    remaining = len(scan_cache_entries(validated))
    log.info(
        "waveform cache cleared removed_files=%d freed_bytes=%d reset_track_states=%d remaining_files=%d",
        removed, freed, reset, remaining,
    )
    return CacheClearOutcome(
        removed_files=removed,
        freed_bytes=freed,
        reset_track_states=reset,
        remaining_files=remaining,
    )


async def clear_cache_locked(validated: ValidatedWaveformCacheRoot) -> CacheClearOutcome:
    """Clear the cache off the event loop, serialized against cleanup passes."""
    async with _cleanup_lock():
        return await asyncio.to_thread(clear_cache, validated)


def cache_status(validated: ValidatedWaveformCacheRoot, *, max_cache_bytes: int) -> dict[str, object]:
    """Privacy-safe cache summary. Contains no path, root, or username."""
    usage = cache_usage(validated)
    return {
        "current_cache_bytes": usage.total_bytes,
        "max_cache_bytes": max_cache_bytes,
        "artifact_count": usage.artifact_count,
        "temp_count": usage.temp_count,
        "superseded_count": usage.superseded_count,
        "over_limit": usage.total_bytes > max_cache_bytes,
        "algorithm_version": WAVEFORM_ALGORITHM_VERSION,
    }
