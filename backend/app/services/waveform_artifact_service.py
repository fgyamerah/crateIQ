"""Versioned gzip-JSON waveform cache artifacts (W3).

Every artifact is a disposable, CrateIQ-owned derived file inside the
validated waveform cache root. Deleting the whole cache must have no effect
on playback, tags, metadata, crates, reviews, exports, or DJ software.

Artifact paths are derived only from a validated 64-character hex generation
key — never from a client-supplied name, a source path, or a filename — so
traversal and symlink escape are structurally impossible.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..core.waveform_cache import (
    ValidatedWaveformCacheRoot,
    assert_waveform_cleanup_candidate,
)
from ..core.waveform_limits import (
    ANALYSIS_SAMPLE_RATE_HZ,
    COMPACT_PAIR_COUNT,
    DETAIL_PAIR_MAX,
    PCM_CHANNELS,
    PLAYER_PAIR_COUNT,
)
from ..models.waveform import WAVEFORM_ALGORITHM_VERSION, WAVEFORM_SCHEMA_VERSION, SourceStatSnapshot
from ..models.waveform_extraction import WaveformExtractionResult
from ..services.waveform_peaks import INT16_MAX, INT16_MIN

CACHE_LAYOUT_VERSION = "v1"
MAX_DECOMPRESSED_ARTIFACT_BYTES = 4 * 1024 * 1024
INT16_SCALE = 32767
ARTIFACT_ENCODING_TYPE = "int16_min_max_interleaved"

_GENERATION_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_ALGORITHM_DIR_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_RESOLUTION_LIMITS: dict[str, int] = {
    "compact": COMPACT_PAIR_COUNT,
    "player": PLAYER_PAIR_COUNT,
    "detail": DETAIL_PAIR_MAX,
}
RESOLUTION_NAMES = tuple(_RESOLUTION_LIMITS)


class WaveformArtifactError(ValueError):
    """Raised when an artifact is malformed, oversized, or unsafe to trust."""


def _require_generation_key(generation_key: str) -> str:
    if not isinstance(generation_key, str) or not _GENERATION_KEY_RE.match(generation_key):
        raise WaveformArtifactError("generation key must be 64 lowercase hex characters")
    return generation_key


def artifact_path(validated: ValidatedWaveformCacheRoot, generation_key: str) -> Path:
    """Derive the canonical artifact path from a validated hex key only."""
    key = _require_generation_key(generation_key)
    if not _ALGORITHM_DIR_RE.match(WAVEFORM_ALGORITHM_VERSION):
        raise WaveformArtifactError("algorithm version is not a safe directory name")
    return (
        validated.root
        / CACHE_LAYOUT_VERSION
        / WAVEFORM_ALGORITHM_VERSION
        / key[:2]
        / f"{key}.json.gz"
    )


def build_artifact_document(
    result: WaveformExtractionResult,
    *,
    generation_key: str,
    snapshot: SourceStatSnapshot,
) -> dict[str, Any]:
    """Build the artifact document. Contains derived and stat data only."""
    _require_generation_key(generation_key)
    resolutions: dict[str, Any] = {}
    for name in RESOLUTION_NAMES:
        peaks = list(result.resolutions.get(name, []))
        resolutions[name] = {"pair_count": len(peaks) // 2, "peaks": peaks}
    return {
        "schema_version": WAVEFORM_SCHEMA_VERSION,
        "algorithm_version": WAVEFORM_ALGORITHM_VERSION,
        "generation_key": generation_key,
        "source": {
            "size_bytes": snapshot.source_size_bytes,
            "mtime_ns": snapshot.source_mtime_ns,
        },
        "analysis": {
            "sample_rate_hz": result.analysis_sample_rate_hz,
            "rendered_channels": PCM_CHANNELS,
        },
        "audio": {
            "duration_ms": result.duration_ms,
            "source_channels": result.source_channels,
            "source_sample_rate_hz": result.source_sample_rate_hz,
        },
        "encoding": {"type": result.encoding, "scale": INT16_SCALE},
        "resolutions": resolutions,
    }


def validate_artifact_document(
    document: Any,
    *,
    expected_generation_key: str | None = None,
) -> dict[str, Any]:
    """Validate a decoded artifact. Never trust cache merely because we wrote it."""
    if not isinstance(document, dict):
        raise WaveformArtifactError("artifact is not a JSON object")

    if document.get("schema_version") != WAVEFORM_SCHEMA_VERSION:
        raise WaveformArtifactError("artifact schema version is unsupported")
    if document.get("algorithm_version") != WAVEFORM_ALGORITHM_VERSION:
        raise WaveformArtifactError("artifact algorithm version is unsupported")

    key = document.get("generation_key")
    _require_generation_key(key if isinstance(key, str) else "")
    if expected_generation_key is not None and key != expected_generation_key:
        raise WaveformArtifactError("artifact generation key does not match the expected key")

    audio = document.get("audio")
    if not isinstance(audio, dict):
        raise WaveformArtifactError("artifact is missing its audio block")
    duration_ms = audio.get("duration_ms")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
        raise WaveformArtifactError("artifact duration is invalid")

    encoding = document.get("encoding")
    if not isinstance(encoding, dict) or encoding.get("type") != ARTIFACT_ENCODING_TYPE:
        raise WaveformArtifactError("artifact encoding is unsupported")

    resolutions = document.get("resolutions")
    if not isinstance(resolutions, dict):
        raise WaveformArtifactError("artifact is missing its resolutions block")
    if set(resolutions) - set(RESOLUTION_NAMES):
        raise WaveformArtifactError("artifact contains an unknown resolution name")
    for name in RESOLUTION_NAMES:
        block = resolutions.get(name)
        if not isinstance(block, dict):
            raise WaveformArtifactError(f"artifact resolution {name} is missing")
        peaks = block.get("peaks")
        pair_count = block.get("pair_count")
        if not isinstance(peaks, list):
            raise WaveformArtifactError(f"artifact resolution {name} has no peak list")
        if not isinstance(pair_count, int) or isinstance(pair_count, bool) or pair_count < 0:
            raise WaveformArtifactError(f"artifact resolution {name} has an invalid pair count")
        if len(peaks) % 2 != 0 or len(peaks) // 2 != pair_count:
            raise WaveformArtifactError(f"artifact resolution {name} pair count is inconsistent")
        if pair_count > _RESOLUTION_LIMITS[name]:
            raise WaveformArtifactError(f"artifact resolution {name} exceeds its pair limit")
        for value in peaks:
            if not isinstance(value, int) or isinstance(value, bool):
                raise WaveformArtifactError(f"artifact resolution {name} has a non-integer peak")
            if value < INT16_MIN or value > INT16_MAX:
                raise WaveformArtifactError(f"artifact resolution {name} has an out-of-range peak")
    return document


def serialize_artifact(document: Mapping[str, Any]) -> bytes:
    """Serialize to deterministic gzip JSON with a fixed mtime for stability."""
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _safe_unlink(candidate: Path, validated: ValidatedWaveformCacheRoot) -> None:
    """Delete only a proven descendant of the validated waveform cache root."""
    try:
        contained = assert_waveform_cleanup_candidate(candidate, validated)
    except Exception:
        return
    try:
        contained.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def publish_artifact(
    validated: ValidatedWaveformCacheRoot,
    generation_key: str,
    payload: bytes,
) -> Path:
    """Atomically publish one artifact: temp file in the final directory, then replace.

    A ready state must never become visible before this returns successfully.
    The temporary file is always created inside the validated cache root and
    never beside source music.
    """
    final = artifact_path(validated, generation_key)
    assert_waveform_cleanup_candidate(final, validated)
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    temp = final.parent / f".tmp.{uuid.uuid4().hex}.json.gz"
    assert_waveform_cleanup_candidate(temp, validated)
    try:
        with open(temp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, final)
    except BaseException:
        _safe_unlink(temp, validated)
        raise
    return final


def read_artifact(
    validated: ValidatedWaveformCacheRoot,
    generation_key: str,
    *,
    expected_generation_key: str | None = None,
) -> dict[str, Any]:
    """Read, bound, decompress, and validate one cache artifact.

    Raises :class:`WaveformArtifactError` for anything unusable: a missing
    file, corrupt gzip, invalid JSON, an oversized payload, or a document
    that fails validation. Callers translate that into a safe degraded state
    and never surface the raw parser error.
    """
    path = artifact_path(validated, generation_key)
    assert_waveform_cleanup_candidate(path, validated)
    try:
        with gzip.open(path, "rb") as handle:
            raw = handle.read(MAX_DECOMPRESSED_ARTIFACT_BYTES + 1)
    except FileNotFoundError as exc:
        raise WaveformArtifactError("artifact is missing") from exc
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise WaveformArtifactError("artifact could not be decompressed") from exc
    if len(raw) > MAX_DECOMPRESSED_ARTIFACT_BYTES:
        raise WaveformArtifactError("artifact exceeds the decompressed size limit")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WaveformArtifactError("artifact is not valid JSON") from exc
    return validate_artifact_document(
        document, expected_generation_key=expected_generation_key or generation_key
    )


def delete_artifact(validated: ValidatedWaveformCacheRoot, generation_key: str) -> None:
    """Remove one CrateIQ-owned artifact after containment validation."""
    _safe_unlink(artifact_path(validated, generation_key), validated)


def resolution_payload(document: Mapping[str, Any], resolution: str) -> tuple[int, list[int]]:
    """Return ``(pair_count, peaks)`` for one validated named resolution."""
    if resolution not in _RESOLUTION_LIMITS:
        raise WaveformArtifactError("unknown waveform resolution")
    block = document["resolutions"][resolution]
    return int(block["pair_count"]), list(block["peaks"])
