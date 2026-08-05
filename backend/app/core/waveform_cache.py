"""Canonical containment primitives for CrateIQ-owned waveform cache data."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class WaveformCacheSafetyError(ValueError):
    """Raised when a cache root or cleanup target is not safely contained."""


@dataclass(frozen=True)
class ValidatedWaveformCacheRoot:
    root: Path
    library_root: Path


def _contains(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_traversal(path: Path, label: str) -> None:
    if ".." in path.parts:
        raise WaveformCacheSafetyError(f"{label} must not contain traversal segments")


def validate_waveform_cache_root(
    cache_dir: Path | str,
    library_root: Path | str,
) -> ValidatedWaveformCacheRoot:
    """Canonicalize and prove that cache and music roots do not overlap."""
    raw_cache = Path(cache_dir).expanduser()
    if not raw_cache.is_absolute():
        raise WaveformCacheSafetyError("waveform cache root must be absolute")
    _reject_traversal(raw_cache, "waveform cache root")
    raw_library = Path(library_root).expanduser()
    if not raw_library.is_absolute():
        raise WaveformCacheSafetyError("library root must be absolute")

    canonical_cache = raw_cache.resolve(strict=False)
    canonical_library = raw_library.resolve(strict=False)
    if canonical_cache == canonical_library:
        raise WaveformCacheSafetyError("waveform cache root must not equal the library root")
    if _contains(canonical_cache, canonical_library):
        raise WaveformCacheSafetyError("waveform cache root must not be inside the library root")
    if _contains(canonical_library, canonical_cache):
        raise WaveformCacheSafetyError("waveform cache root must not contain the library root")
    return ValidatedWaveformCacheRoot(canonical_cache, canonical_library)


def waveform_cache_is_writable(validated: ValidatedWaveformCacheRoot) -> bool:
    """Check whether the root exists writable or can be created safely."""
    root = validated.root
    if root.exists():
        return root.is_dir() and os.access(root, os.W_OK | os.X_OK)
    parent = root.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)


def initialize_waveform_cache_root(validated: ValidatedWaveformCacheRoot) -> Path:
    """Create only the validated application cache directory, never artifacts."""
    if not waveform_cache_is_writable(validated):
        raise WaveformCacheSafetyError("waveform cache root is not writable")
    existed = validated.root.exists()
    validated.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    canonical = validated.root.resolve(strict=True)
    revalidated = validate_waveform_cache_root(canonical, validated.library_root)
    if revalidated.root != validated.root:
        raise WaveformCacheSafetyError("waveform cache root changed during initialization")
    if not existed:
        validated.root.chmod(0o700)
    return validated.root


def assert_waveform_cleanup_candidate(
    candidate: Path | str,
    validated: ValidatedWaveformCacheRoot,
) -> Path:
    """Return one canonical cache descendant or refuse future deletion."""
    raw_candidate = Path(candidate).expanduser()
    if not raw_candidate.is_absolute():
        raise WaveformCacheSafetyError("cleanup candidate must be absolute")
    _reject_traversal(raw_candidate, "cleanup candidate")
    canonical = raw_candidate.resolve(strict=False)
    if canonical == validated.root or not _contains(canonical, validated.root):
        raise WaveformCacheSafetyError("cleanup candidate is outside the waveform cache root")
    if canonical == validated.library_root or _contains(validated.library_root, canonical):
        raise WaveformCacheSafetyError("cleanup candidate overlaps the library root")
    return canonical
