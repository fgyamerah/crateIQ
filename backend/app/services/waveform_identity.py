"""Stat-based waveform generation identity (W3).

This module derives a privacy-safe ``generation_key`` used both to
deduplicate active work and to name the cache artifact.

**It never reads source audio bytes.** The digest is computed over a small
canonical JSON structure describing cheap ``stat`` identity plus the schema,
algorithm, and analysis parameters that determine the derived output. That is
deliberately *not* a content hash:

* ``generation_key`` — SHA-256 of the small metadata structure below. Cheap,
  requires no file read, and changes whenever the source stat identity or the
  analysis contract changes. This is a cache-invalidation mechanism.
* ``source_sha256`` — a true content hash over all source bytes. Still
  nullable and deferred; W3 never computes it.

A pathological in-place modification that preserves size, both timestamps,
device, and inode would not change the generation key. Detecting that would
require a full-file read on every request, which the architecture explicitly
rejects. Strong content identity remains a deferred feature.
"""
from __future__ import annotations

import hashlib
import json

from ..core.waveform_limits import (
    ANALYSIS_SAMPLE_RATE_HZ,
    COMPACT_PAIR_COUNT,
    DETAIL_PAIR_MAX,
    PCM_CHANNELS,
    PLAYER_PAIR_COUNT,
)
from ..models.waveform import (
    WAVEFORM_ALGORITHM_VERSION,
    WAVEFORM_SCHEMA_VERSION,
    SourceStatSnapshot,
    WaveformTrackState,
)

GENERATION_KEY_NAMESPACE = "crateiq-waveform-generation-v1"


def build_generation_signature(snapshot: SourceStatSnapshot) -> dict[str, object]:
    """Return the exact structure that is hashed into a generation key.

    Every value is either a digest, an integer, or a version constant. No
    absolute path, filename, or tag value is included.
    """
    return {
        "namespace": GENERATION_KEY_NAMESPACE,
        "library_id": snapshot.library_id,
        "track_id": snapshot.track_id,
        "source_size_bytes": snapshot.source_size_bytes,
        "source_mtime_ns": snapshot.source_mtime_ns,
        "source_ctime_ns": snapshot.source_ctime_ns,
        "source_device": snapshot.source_device,
        "source_inode": snapshot.source_inode,
        "schema_version": WAVEFORM_SCHEMA_VERSION,
        "algorithm_version": WAVEFORM_ALGORITHM_VERSION,
        "analysis_sample_rate_hz": ANALYSIS_SAMPLE_RATE_HZ,
        "rendered_channels": PCM_CHANNELS,
        "compact_pair_count": COMPACT_PAIR_COUNT,
        "player_pair_count": PLAYER_PAIR_COUNT,
        "detail_pair_max": DETAIL_PAIR_MAX,
    }


def compute_generation_key(snapshot: SourceStatSnapshot) -> str:
    """SHA-256 of the canonical generation signature. Never of audio content."""
    canonical = json.dumps(
        build_generation_signature(snapshot), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def state_matches_snapshot(state: WaveformTrackState, snapshot: SourceStatSnapshot) -> bool:
    """Cheap staleness check: does persisted stat identity still match disk?"""
    return (
        state.source_size_bytes == snapshot.source_size_bytes
        and state.source_mtime_ns == snapshot.source_mtime_ns
        and state.source_ctime_ns == snapshot.source_ctime_ns
        and state.source_device == snapshot.source_device
        and state.source_inode == snapshot.source_inode
    )
