"""Passive, privacy-safe waveform readiness evaluation for W1."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.library_root import selected_library_root
from ..core.waveform_cache import (
    WaveformCacheSafetyError,
    validate_waveform_cache_root,
    waveform_cache_is_writable,
)
from ..core.waveform_config import WaveformConfigurationError, load_waveform_config
from ..models.waveform import WaveformCapabilityStatus


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
            "version_verified": False,
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
    return _response(
        enabled=True,
        status=WaveformCapabilityStatus.DETECTED,
        cache_ready=True,
        ffmpeg_detected=True,
        ffprobe_detected=True,
        message="Waveform foundation is configured and the extractor toolchain was detected but not runtime-verified.",
    )
