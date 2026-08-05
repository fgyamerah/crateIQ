"""Validated backend configuration for the optional waveform foundation."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import BACKEND_DATA_DIR


DEFAULT_WAVEFORM_MAX_CONCURRENT_JOBS = 1
DEFAULT_WAVEFORM_MAX_QUEUE_SIZE = 32
DEFAULT_WAVEFORM_MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024
MAX_WAVEFORM_CONCURRENT_JOBS = 2
MAX_WAVEFORM_QUEUE_SIZE = 1024
MIN_WAVEFORM_CACHE_BYTES = 1024 * 1024


class WaveformConfigurationError(ValueError):
    """Raised when an operator-supplied waveform setting is invalid."""


@dataclass(frozen=True)
class WaveformConfig:
    enabled: bool
    cache_dir: Path
    max_concurrent_jobs: int
    max_queue_size: int
    max_cache_bytes: int


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise WaveformConfigurationError(f"{name} must be a boolean value")


def _parse_int(value: str, name: str, *, minimum: int, maximum: int | None = None) -> int:
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError) as exc:
        raise WaveformConfigurationError(f"{name} must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        limit = f" between {minimum} and {maximum}" if maximum is not None else f" at least {minimum}"
        raise WaveformConfigurationError(f"{name} must be{limit}")
    return parsed


def load_waveform_config(
    environ: Mapping[str, str] | None = None,
    *,
    backend_data_dir: Path | None = None,
) -> WaveformConfig:
    """Load and validate the small W1 environment configuration surface."""
    values = os.environ if environ is None else environ
    data_dir = (backend_data_dir or BACKEND_DATA_DIR).resolve(strict=False)
    enabled = _parse_bool(values.get("CRATEIQ_WAVEFORMS_ENABLED", "1"), "CRATEIQ_WAVEFORMS_ENABLED")

    raw_cache = values.get("CRATEIQ_WAVEFORM_CACHE_DIR", "").strip()
    if raw_cache:
        if any(char in raw_cache for char in ("\x00", "\n", "\r")):
            raise WaveformConfigurationError("CRATEIQ_WAVEFORM_CACHE_DIR contains an unsafe character")
        cache_dir = Path(raw_cache).expanduser()
        if not cache_dir.is_absolute():
            raise WaveformConfigurationError("CRATEIQ_WAVEFORM_CACHE_DIR must be absolute")
    else:
        cache_dir = data_dir / "cache" / "waveforms"

    concurrency = _parse_int(
        values.get("CRATEIQ_WAVEFORM_MAX_CONCURRENCY", str(DEFAULT_WAVEFORM_MAX_CONCURRENT_JOBS)),
        "CRATEIQ_WAVEFORM_MAX_CONCURRENCY",
        minimum=1,
        maximum=MAX_WAVEFORM_CONCURRENT_JOBS,
    )
    queue_size = _parse_int(
        values.get("CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE", str(DEFAULT_WAVEFORM_MAX_QUEUE_SIZE)),
        "CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE",
        minimum=1,
        maximum=MAX_WAVEFORM_QUEUE_SIZE,
    )
    cache_bytes = _parse_int(
        values.get("CRATEIQ_WAVEFORM_MAX_CACHE_BYTES", str(DEFAULT_WAVEFORM_MAX_CACHE_BYTES)),
        "CRATEIQ_WAVEFORM_MAX_CACHE_BYTES",
        minimum=MIN_WAVEFORM_CACHE_BYTES,
    )
    return WaveformConfig(
        enabled=enabled,
        cache_dir=cache_dir,
        max_concurrent_jobs=concurrency,
        max_queue_size=queue_size,
        max_cache_bytes=cache_bytes,
    )
