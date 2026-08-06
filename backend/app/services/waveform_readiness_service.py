"""Passive, privacy-safe waveform readiness evaluation (W1, W6).

The readiness report itself never executes anything. W6 adds a bounded
``-version`` runtime check that runs once from the backend lifespan and caches
its outcome here; the report only reads that cache.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.library_root import selected_library_root
from ..core.waveform_cache import (
    ValidatedWaveformCacheRoot,
    WaveformCacheSafetyError,
    validate_waveform_cache_root,
    waveform_cache_is_writable,
)
from ..core.waveform_config import WaveformConfig, WaveformConfigurationError, load_waveform_config
from ..models.waveform import WaveformCapabilityStatus

log = logging.getLogger(__name__)


def _check_status(checks: Sequence[dict[str, Any]], name: str) -> bool | None:
    for check in checks:
        if check.get("name") == name:
            return check.get("status") == "pass"
    return None


def _path_overlaps(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _passively_detect_tool(
    name: str,
    env_name: str,
    *,
    checks: Sequence[dict[str, Any]],
    library_root: Path,
    cache_root: Path,
    environ: Mapping[str, str],
) -> bool:
    reported = _check_status(checks, f"binary_{name}")
    if reported is False:
        return False
    override = environ.get(env_name, "").strip()
    resolved_text = shutil.which(override or name)
    if not resolved_text:
        return bool(reported)
    resolved = Path(resolved_text).expanduser().resolve(strict=False)
    if resolved == library_root or _path_overlaps(resolved, library_root):
        return False
    if resolved == cache_root or _path_overlaps(resolved, cache_root):
        return False
    return True


def _response(
    *,
    enabled: bool,
    status: WaveformCapabilityStatus,
    cache_ready: bool,
    ffmpeg_detected: bool,
    ffprobe_detected: bool,
    message: str,
    version_verified: bool = False,
) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "status": status.value,
        "cache_ready": cache_ready,
        "engine": {
            "name": "ffmpeg",
            "detected": ffmpeg_detected and ffprobe_detected,
            "ffmpeg_detected": ffmpeg_detected,
            "ffprobe_detected": ffprobe_detected,
            "version_verified": version_verified,
        },
        "message": message,
    }


def get_waveform_readiness(
    checks: Sequence[dict[str, Any]] = (),
    *,
    environ: Mapping[str, str] | None = None,
    backend_data_dir: Path | None = None,
    library_root: Path | None = None,
) -> dict[str, Any]:
    """Evaluate configuration/cache/tool presence without running any binary."""
    values = os.environ if environ is None else environ
    try:
        config = load_waveform_config(values, backend_data_dir=backend_data_dir)
    except WaveformConfigurationError:
        return _response(
            enabled=True,
            status=WaveformCapabilityStatus.MISCONFIGURED,
            cache_ready=False,
            ffmpeg_detected=False,
            ffprobe_detected=False,
            message="Waveform configuration is invalid.",
        )
    if not config.enabled:
        return _response(
            enabled=False,
            status=WaveformCapabilityStatus.DISABLED,
            cache_ready=False,
            ffmpeg_detected=False,
            ffprobe_detected=False,
            message="Waveform support is disabled by configuration.",
        )

    try:
        root = (library_root or selected_library_root()).resolve(strict=False)
        validated_cache = validate_waveform_cache_root(config.cache_dir, root)
    except (RuntimeError, OSError, WaveformCacheSafetyError):
        return _response(
            enabled=True,
            status=WaveformCapabilityStatus.MISCONFIGURED,
            cache_ready=False,
            ffmpeg_detected=False,
            ffprobe_detected=False,
            message="Waveform cache configuration is unsafe or invalid.",
        )

    cache_ready = waveform_cache_is_writable(validated_cache)
    ffmpeg_detected = _passively_detect_tool(
        "ffmpeg", "FFMPEG_BIN", checks=checks, library_root=root,
        cache_root=validated_cache.root, environ=values,
    )
    ffprobe_detected = _passively_detect_tool(
        "ffprobe", "FFPROBE_BIN", checks=checks, library_root=root,
        cache_root=validated_cache.root, environ=values,
    )
    if not cache_ready:
        return _response(
            enabled=True,
            status=WaveformCapabilityStatus.CACHE_UNAVAILABLE,
            cache_ready=False,
            ffmpeg_detected=ffmpeg_detected,
            ffprobe_detected=ffprobe_detected,
            message="Waveform cache storage is unavailable.",
        )
    if not (ffmpeg_detected and ffprobe_detected):
        return _response(
            enabled=True,
            status=WaveformCapabilityStatus.EXTRACTOR_UNAVAILABLE,
            cache_ready=True,
            ffmpeg_detected=ffmpeg_detected,
            ffprobe_detected=ffprobe_detected,
            message="The optional waveform extractor toolchain is unavailable.",
        )
    # Report the cached runtime verification if one has been performed. This
    # is a pure read: the readiness endpoint never executes a subprocess.
    verification = cached_extractor_verification()
    if verification is not None and verification.verified:
        return _response(
            enabled=True,
            status=WaveformCapabilityStatus.READY,
            cache_ready=True,
            ffmpeg_detected=True,
            ffprobe_detected=True,
            version_verified=True,
            message="Waveform extraction is configured and the toolchain was runtime-verified.",
        )
    if verification is not None and not verification.verified:
        return _response(
            enabled=True,
            status=WaveformCapabilityStatus.EXTRACTOR_UNAVAILABLE,
            cache_ready=True,
            ffmpeg_detected=verification.ffmpeg_verified,
            ffprobe_detected=verification.ffprobe_verified,
            message="The waveform extractor toolchain failed runtime verification.",
        )
    return _response(
        enabled=True,
        status=WaveformCapabilityStatus.DETECTED,
        cache_ready=True,
        ffmpeg_detected=True,
        ffprobe_detected=True,
        message="Waveform foundation is configured and the extractor toolchain was detected but not runtime-verified.",
    )


# ---------------------------------------------------------------------------
# W3 runtime resolution
#
# These helpers resolve the *current* validated waveform runtime for the
# generation lifecycle. They deliberately separate two different questions:
#
#   * can CrateIQ read an already-cached artifact?   -> resolve_cache_runtime
#   * can CrateIQ generate a new artifact?           -> generation_blocker
#
# A missing FFmpeg must never stop a valid cached waveform from being served.
# ---------------------------------------------------------------------------


class WaveformRuntimeError(RuntimeError):
    """Raised when the waveform runtime cannot be safely resolved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve_cache_runtime(
    *,
    environ: Mapping[str, str] | None = None,
    library_root: Path | None = None,
) -> tuple[WaveformConfig, ValidatedWaveformCacheRoot]:
    """Return the validated config and cache root, or raise a coded error.

    This performs no executable detection: reading cached waveform data must
    keep working even when the optional extractor toolchain disappears.
    """
    values = os.environ if environ is None else environ
    try:
        config = load_waveform_config(values)
    except WaveformConfigurationError as exc:
        raise WaveformRuntimeError("WAVEFORM_MISCONFIGURED", "Waveform configuration is invalid.") from exc
    if not config.enabled:
        raise WaveformRuntimeError("WAVEFORM_DISABLED", "Waveform support is disabled by configuration.")
    try:
        root = (library_root or selected_library_root()).resolve(strict=False)
        validated = validate_waveform_cache_root(config.cache_dir, root)
    except (RuntimeError, OSError, WaveformCacheSafetyError) as exc:
        raise WaveformRuntimeError(
            "WAVEFORM_MISCONFIGURED", "Waveform cache configuration is unsafe or invalid."
        ) from exc
    return config, validated


def generation_blocker(
    validated: ValidatedWaveformCacheRoot,
    *,
    environ: Mapping[str, str] | None = None,
) -> WaveformRuntimeError | None:
    """Return a coded error when new generation cannot run, else ``None``."""
    from .waveform_probe import resolve_executable

    values = os.environ if environ is None else environ
    if not waveform_cache_is_writable(validated):
        return WaveformRuntimeError("WAVEFORM_CACHE_UNAVAILABLE", "Waveform cache storage is unavailable.")
    ffmpeg = resolve_executable(
        "ffmpeg", "FFMPEG_BIN", library_root=validated.library_root, cache_root=validated.root
    )
    ffprobe = resolve_executable(
        "ffprobe", "FFPROBE_BIN", library_root=validated.library_root, cache_root=validated.root
    )
    if not (ffmpeg and ffprobe):
        return WaveformRuntimeError(
            "WAVEFORM_EXTRACTOR_UNAVAILABLE",
            "The optional waveform extractor toolchain is unavailable.",
        )
    return None


def resolve_extractor_binaries(validated: ValidatedWaveformCacheRoot) -> tuple[str, str]:
    """Resolve both executables or raise a coded runtime error."""
    from .waveform_probe import resolve_executable

    ffmpeg = resolve_executable(
        "ffmpeg", "FFMPEG_BIN", library_root=validated.library_root, cache_root=validated.root
    )
    ffprobe = resolve_executable(
        "ffprobe", "FFPROBE_BIN", library_root=validated.library_root, cache_root=validated.root
    )
    if not (ffmpeg and ffprobe):
        raise WaveformRuntimeError(
            "WAVEFORM_EXTRACTOR_UNAVAILABLE",
            "The optional waveform extractor toolchain is unavailable.",
        )
    return ffmpeg, ffprobe


# ---------------------------------------------------------------------------
# W6 runtime extractor verification
#
# W2 shipped `verify_extractor_versions` as an unwired primitive. W6 completes
# the deferred `detected -> ready` transition, with one hard constraint:
#
#   the readiness GET must never spawn a subprocess.
#
# `/api/runtime/readiness` is part of the read-only smoke surface, and a
# passive status report is the wrong place to execute anything. Verification
# therefore runs once from the backend lifespan (see `main.py`) and caches its
# result process-wide; the readiness report only ever *reads* that cache.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractorVerification:
    """Cached outcome of a bounded, non-media `-version` check."""

    verified: bool
    ffmpeg_verified: bool
    ffprobe_verified: bool
    ffmpeg_version: str | None
    ffprobe_version: str | None


_verification_cache: ExtractorVerification | None = None

# FFmpeg/ffprobe identify themselves on the first line of `-version`. Anything
# that does not look like that is treated as unverified rather than parsed
# optimistically.
_VERSION_LINE_RE = re.compile(r"^(ffmpeg|ffprobe)\s+version\s+\S+", re.IGNORECASE)


def _normalize_version(tool: str, raw: str | None) -> str | None:
    """Accept only a plausible `<tool> version <something>` first line."""
    if not raw or not isinstance(raw, str):
        return None
    line = raw.strip()
    if not line or len(line) > 200:
        return None
    match = _VERSION_LINE_RE.match(line)
    if not match or match.group(1).lower() != tool.lower():
        return None
    return line


def cached_extractor_verification() -> ExtractorVerification | None:
    """Read the cached verification. Never executes anything."""
    return _verification_cache


def reset_extractor_verification() -> None:
    """Clear the cache. Used by tests and by explicit re-verification."""
    global _verification_cache
    _verification_cache = None


async def verify_extractor_runtime(
    *,
    library_root: Path | None = None,
    force: bool = False,
    supervisor: object | None = None,
) -> ExtractorVerification | None:
    """Run `ffmpeg -version` / `ffprobe -version` once and cache the outcome.

    Returns ``None`` when verification is not applicable (feature disabled,
    cache unsafe, or the binaries were not even detected) — in that case
    nothing is executed. No media path is ever passed to either executable.
    """
    global _verification_cache
    if _verification_cache is not None and not force:
        return _verification_cache

    from ..core.waveform_process import ProcessSupervisor
    from .waveform_probe import verify_extractor_versions

    try:
        _config, validated = resolve_cache_runtime(library_root=library_root)
        ffmpeg_bin, ffprobe_bin = resolve_extractor_binaries(validated)
    except WaveformRuntimeError:
        return None

    raw = await verify_extractor_versions(
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        supervisor=supervisor or ProcessSupervisor(),
    )
    ffmpeg_version = _normalize_version("ffmpeg", raw.get("ffmpeg_version"))  # type: ignore[arg-type]
    ffprobe_version = _normalize_version("ffprobe", raw.get("ffprobe_version"))  # type: ignore[arg-type]
    verification = ExtractorVerification(
        verified=bool(ffmpeg_version and ffprobe_version),
        ffmpeg_verified=ffmpeg_version is not None,
        ffprobe_verified=ffprobe_version is not None,
        ffmpeg_version=ffmpeg_version,
        ffprobe_version=ffprobe_version,
    )
    _verification_cache = verification
    if verification.verified:
        log.info("waveform extractor verified engine=ffmpeg")
    else:
        log.info(
            "waveform extractor unverified ffmpeg_ok=%s ffprobe_ok=%s",
            verification.ffmpeg_verified, verification.ffprobe_verified,
        )
    return verification
