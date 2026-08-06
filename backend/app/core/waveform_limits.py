"""Centralized policy constants for the W2 waveform extraction engine.

These are safety/resource constants from the accepted waveform architecture
(``docs/architecture/WAVEFORM_ARCHITECTURE.md``), not routine UI settings.
Nothing here executes a subprocess or reads audio.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Source policy
# ---------------------------------------------------------------------------

MAX_SOURCE_SIZE_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB
MAX_DURATION_SECONDS = 6 * 60 * 60  # 6 hours

# ---------------------------------------------------------------------------
# Probe validation ranges
# ---------------------------------------------------------------------------

MIN_CHANNELS = 1
MAX_CHANNELS = 32
MIN_SAMPLE_RATE_HZ = 1000
MAX_SAMPLE_RATE_HZ = 384000

# ---------------------------------------------------------------------------
# Decoder output contract
# ---------------------------------------------------------------------------

ANALYSIS_SAMPLE_RATE_HZ = 8000
PCM_SAMPLE_WIDTH_BYTES = 2  # signed 16-bit little-endian
PCM_CHANNELS = 1  # mono

# ---------------------------------------------------------------------------
# Process/IO bounds
# ---------------------------------------------------------------------------

PCM_READ_CHUNK_BYTES = 64 * 1024
STDERR_TAIL_BYTES = 64 * 1024
FFPROBE_STDOUT_CAP_BYTES = 1024 * 1024
DECODER_THREADS = 1
PROCESS_NICENESS = 10

# ---------------------------------------------------------------------------
# Timeout policy
# ---------------------------------------------------------------------------

UNKNOWN_DURATION_TIMEOUT_SECONDS = 600.0  # 10 minutes
MIN_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 1200.0  # 20 minutes hard ceiling
TIMEOUT_DURATION_FACTOR = 0.15
TIMEOUT_BASE_SECONDS = 60.0
TERMINATION_GRACE_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 10.0
VERSION_CHECK_TIMEOUT_SECONDS = 3.0

# ---------------------------------------------------------------------------
# Resolution / peak policy
# ---------------------------------------------------------------------------

COMPACT_PAIR_COUNT = 256
PLAYER_PAIR_COUNT = 1024
DETAIL_PAIR_MIN = 2048
DETAIL_PAIR_MAX = 32768
DETAIL_PAIRS_PER_SECOND = 20


def compute_extraction_timeout_seconds(duration_seconds: float | None) -> float:
    """Duration-aware decode timeout: ``clamp(120s, duration*0.15+60s, 1200s)``.

    Unknown duration uses a fixed 10-minute ceiling instead of the formula.
    """
    if duration_seconds is None:
        return UNKNOWN_DURATION_TIMEOUT_SECONDS
    if duration_seconds < 0 or not math.isfinite(duration_seconds):
        return UNKNOWN_DURATION_TIMEOUT_SECONDS
    estimated = duration_seconds * TIMEOUT_DURATION_FACTOR + TIMEOUT_BASE_SECONDS
    return max(MIN_TIMEOUT_SECONDS, min(estimated, MAX_TIMEOUT_SECONDS))


def compute_detail_pair_target(duration_seconds: float | None) -> int:
    """Adaptive detail pair target per the architecture's long-file policy.

    Unknown duration returns the hard cap; the streaming accumulator still
    bounds memory to this capacity regardless of actual decoded length.
    """
    if duration_seconds is None or duration_seconds <= 0 or not math.isfinite(duration_seconds):
        return DETAIL_PAIR_MAX
    target = math.ceil(duration_seconds * DETAIL_PAIRS_PER_SECOND)
    return max(DETAIL_PAIR_MIN, min(target, DETAIL_PAIR_MAX))


def estimate_total_samples(duration_seconds: float | None) -> int | None:
    """Rough expected sample count used only to size the bounded accumulator."""
    if duration_seconds is None or duration_seconds <= 0 or not math.isfinite(duration_seconds):
        return None
    return math.ceil(duration_seconds * ANALYSIS_SAMPLE_RATE_HZ)
