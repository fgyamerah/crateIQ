"""Tests for the single-mirrored-waveform spectral tint (low/mid/high energy).

Pure numerical tests: no audio tool, subprocess, or media file is used. numpy
is the only runtime dependency exercised here and is already a declared
project dependency.
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.app.core.waveform_cache import validate_waveform_cache_root
from backend.app.models.waveform import SourceStatSnapshot
from backend.app.models.waveform_extraction import WaveformExtractionResult
from backend.app.services import waveform_artifact_service as artifacts
from backend.app.services.waveform_peaks import (
    compute_band_color_weights,
    resize_color_bands,
)

SAMPLE_RATE = 22050


@pytest.fixture()
def cache_root(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    return validate_waveform_cache_root(tmp_path / "cache", library)


def _sine(frequency_hz: int, seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    count = int(seconds * sample_rate)
    t = np.arange(count, dtype=np.float64) / sample_rate
    return (np.sin(2 * np.pi * frequency_hz * t) * 12000).astype(np.int16)


def _sums_to_one(triplets: list[float]) -> bool:
    for i in range(0, len(triplets), 3):
        total = triplets[i] + triplets[i + 1] + triplets[i + 2]
        if not np.isclose(total, 1.0, atol=1e-6):
            return False
    return True


# ---------------------------------------------------------------------------
# compute_band_color_weights
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    assert compute_band_color_weights(np.array([], dtype=np.int16), SAMPLE_RATE, 0) == []
    assert compute_band_color_weights(np.array([0] * 100, dtype=np.int16), SAMPLE_RATE, 0) == []


def test_output_length_matches_bucket_count() -> None:
    samples = _sine(440, 0.5)
    weights = compute_band_color_weights(samples, SAMPLE_RATE, bucket_count=16)
    assert len(weights) == 16 * 3
    assert _sums_to_one(weights)


def test_silence_resolves_to_even_split() -> None:
    samples = np.zeros(4096, dtype=np.int16)
    weights = compute_band_color_weights(samples, SAMPLE_RATE, bucket_count=4)
    assert weights == [1 / 3, 1 / 3, 1 / 3] * 4


def test_low_tone_is_low_dominant() -> None:
    samples = _sine(100, 1.0)  # bass: below the 250 Hz low edge
    weights = compute_band_color_weights(samples, SAMPLE_RATE, bucket_count=8)
    for i in range(0, len(weights), 3):
        low, mid, high = weights[i], weights[i + 1], weights[i + 2]
        assert low > high
        assert low > mid


def test_high_tone_is_high_dominant() -> None:
    samples = _sine(6000, 1.0)  # transient: above the 4000 Hz high edge
    weights = compute_band_color_weights(samples, SAMPLE_RATE, bucket_count=8)
    for i in range(0, len(weights), 3):
        low, mid, high = weights[i], weights[i + 1], weights[i + 2]
        assert high > low
        assert high > mid


def test_bucket_count_is_capped() -> None:
    samples = _sine(440, 0.2)
    weights = compute_band_color_weights(samples, SAMPLE_RATE, bucket_count=10_000)
    assert len(weights) % 3 == 0
    # Never more buckets than the source sample count allows.
    assert len(weights) // 3 <= samples.size


# ---------------------------------------------------------------------------
# resize_color_bands
# ---------------------------------------------------------------------------


def test_resize_downsample_averages() -> None:
    # Two source buckets: [1,0,0] and [0,1,0] averaged into one => [0.5,0.5,0].
    source = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    assert resize_color_bands(source, 1) == [0.5, 0.5, 0.0]


def test_resize_upsample_repeats_nearest() -> None:
    source = [1.0, 0.0, 0.0]
    assert resize_color_bands(source, 3) == [1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0]


def test_resize_empty_input() -> None:
    assert resize_color_bands([], 4) == []
    assert resize_color_bands([1.0, 0.0, 0.0], 0) == []


# ---------------------------------------------------------------------------
# Artifact serialization round-trip carries the tint without changing peaks
# ---------------------------------------------------------------------------


def _snapshot() -> SourceStatSnapshot:
    return SourceStatSnapshot(
        library_id="c" * 64,
        track_id=7,
        source_size_bytes=28499123,
        source_mtime_ns=1785950000000000000,
        source_ctime_ns=1785950000000000001,
        source_device=66306,
        source_inode=1234567,
    )


def _result() -> WaveformExtractionResult:
    detail: list[int] = []
    for i in range(8):
        detail.append(-(i + 1))
        detail.append(i + 1)
    return WaveformExtractionResult(
        duration_ms=247381,
        source_channels=2,
        source_sample_rate_hz=44100,
        analysis_sample_rate_hz=SAMPLE_RATE,
        encoding="int16_min_max_interleaved",
        resolutions={"compact": detail[:8], "player": detail[:12], "detail": detail},
        color_bands={
            "player": [0.2, 0.5, 0.3] * 6,
            "compact": [0.2, 0.5, 0.3] * 4,
        },
    )


def test_artifact_roundtrip_preserves_color_bands(cache_root) -> None:
    doc = artifacts.build_artifact_document(_result(), generation_key="a" * 64, snapshot=_snapshot())
    assert doc["resolutions"]["player"]["color_bands"] == [0.2, 0.5, 0.3] * 6
    assert "color_bands" not in doc["resolutions"]["detail"]

    artifacts.publish_artifact(cache_root, "a" * 64, artifacts.serialize_artifact(doc))
    loaded = artifacts.read_artifact(cache_root, "a" * 64)
    assert artifacts.resolution_color_bands(loaded, "player") == [0.2, 0.5, 0.3] * 6
    assert artifacts.resolution_color_bands(loaded, "detail") == []


def test_artifact_validation_rejects_mismatched_color_band_length(cache_root) -> None:
    doc = artifacts.build_artifact_document(_result(), generation_key="a" * 64, snapshot=_snapshot())
    doc["resolutions"]["player"]["color_bands"] = [0.2, 0.5, 0.3]  # too short for 6 pairs
    with pytest.raises(artifacts.WaveformArtifactError):
        artifacts.validate_artifact_document(doc, expected_generation_key="a" * 64)
