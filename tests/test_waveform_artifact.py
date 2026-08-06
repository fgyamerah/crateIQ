"""W3 tests for waveform cache artifacts: serialization, validation, publication.

Pure filesystem/serialization behaviour against temporary directories. No
audio tool runs and no real music file is touched.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from backend.app.core.waveform_cache import WaveformCacheSafetyError, validate_waveform_cache_root
from backend.app.models.waveform import (
    WAVEFORM_ALGORITHM_VERSION,
    WAVEFORM_SCHEMA_VERSION,
    SourceStatSnapshot,
)
from backend.app.models.waveform_extraction import WaveformExtractionResult
from backend.app.services import waveform_artifact_service as artifacts
from backend.app.services.waveform_artifact_service import WaveformArtifactError

KEY = "a" * 64
OTHER_KEY = "b" * 64


@pytest.fixture()
def cache_root(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    return validate_waveform_cache_root(tmp_path / "cache", library)


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


def _result(*, detail_pairs: int = 8) -> WaveformExtractionResult:
    detail = []
    for i in range(detail_pairs):
        detail.extend([-(i + 1), i + 1])
    return WaveformExtractionResult(
        duration_ms=247381,
        source_channels=2,
        source_sample_rate_hz=44100,
        analysis_sample_rate_hz=8000,
        encoding="int16_min_max_interleaved",
        resolutions={"compact": detail[:8], "player": detail[:12], "detail": detail},
    )


def _document() -> dict:
    return artifacts.build_artifact_document(_result(), generation_key=KEY, snapshot=_snapshot())


# ---------------------------------------------------------------------------
# Document construction and privacy
# ---------------------------------------------------------------------------


def test_document_has_expected_versions_and_blocks():
    doc = _document()
    assert doc["schema_version"] == WAVEFORM_SCHEMA_VERSION
    assert doc["algorithm_version"] == WAVEFORM_ALGORITHM_VERSION
    assert doc["generation_key"] == KEY
    assert doc["audio"]["duration_ms"] == 247381
    assert doc["analysis"]["sample_rate_hz"] == 8000
    assert doc["encoding"]["scale"] == 32767
    assert set(doc["resolutions"]) == {"compact", "player", "detail"}


def test_document_contains_no_path_username_or_content_hash(tmp_path):
    serialized = json.dumps(_document())
    for forbidden in ("/home/", str(tmp_path), "filepath", "filename", "source_sha256", "sha256"):
        assert forbidden not in serialized


def test_document_pair_counts_match_peak_lengths():
    doc = _document()
    for block in doc["resolutions"].values():
        assert block["pair_count"] == len(block["peaks"]) // 2


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_gzip_roundtrip_preserves_every_resolution(cache_root):
    doc = _document()
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(doc))
    loaded = artifacts.read_artifact(cache_root, KEY)
    assert loaded == doc


def test_roundtrip_preserves_int16_extrema(cache_root):
    result = WaveformExtractionResult(
        duration_ms=1000,
        source_channels=1,
        source_sample_rate_hz=44100,
        analysis_sample_rate_hz=8000,
        encoding="int16_min_max_interleaved",
        resolutions={"compact": [-32768, 32767], "player": [-32768, 32767], "detail": [-32768, 32767]},
    )
    doc = artifacts.build_artifact_document(result, generation_key=KEY, snapshot=_snapshot())
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(doc))
    loaded = artifacts.read_artifact(cache_root, KEY)
    assert loaded["resolutions"]["detail"]["peaks"] == [-32768, 32767]


def test_resolution_payload_returns_requested_level(cache_root):
    doc = _document()
    pair_count, peaks = artifacts.resolution_payload(doc, "player")
    assert pair_count == len(peaks) // 2
    with pytest.raises(WaveformArtifactError):
        artifacts.resolution_payload(doc, "enormous")


def test_serialization_is_deterministic():
    assert artifacts.serialize_artifact(_document()) == artifacts.serialize_artifact(_document())


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_validate_rejects_non_object():
    with pytest.raises(WaveformArtifactError):
        artifacts.validate_artifact_document([1, 2, 3])


def test_validate_rejects_unknown_schema_version():
    doc = _document()
    doc["schema_version"] = 99
    with pytest.raises(WaveformArtifactError, match="schema version"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_unknown_algorithm_version():
    doc = _document()
    doc["algorithm_version"] = "some-other-algorithm-v9"
    with pytest.raises(WaveformArtifactError, match="algorithm version"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_mismatched_generation_key():
    with pytest.raises(WaveformArtifactError, match="generation key"):
        artifacts.validate_artifact_document(_document(), expected_generation_key=OTHER_KEY)


def test_validate_rejects_missing_required_block():
    doc = _document()
    del doc["audio"]
    with pytest.raises(WaveformArtifactError, match="audio"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_unknown_resolution_name():
    doc = _document()
    doc["resolutions"]["gigantic"] = {"pair_count": 0, "peaks": []}
    with pytest.raises(WaveformArtifactError, match="unknown resolution"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_inconsistent_pair_count():
    doc = _document()
    doc["resolutions"]["player"]["pair_count"] += 5
    with pytest.raises(WaveformArtifactError, match="pair count is inconsistent"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_odd_peak_list():
    doc = _document()
    doc["resolutions"]["player"]["peaks"].append(1)
    with pytest.raises(WaveformArtifactError, match="pair count is inconsistent"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_out_of_range_peak():
    doc = _document()
    doc["resolutions"]["detail"]["peaks"][0] = 40000
    with pytest.raises(WaveformArtifactError, match="out-of-range"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_non_integer_peak():
    doc = _document()
    doc["resolutions"]["detail"]["peaks"][0] = "loud"
    with pytest.raises(WaveformArtifactError, match="non-integer"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_pair_count_over_resolution_limit():
    doc = _document()
    doc["resolutions"]["compact"] = {"pair_count": 999, "peaks": [0, 0] * 999}
    with pytest.raises(WaveformArtifactError, match="exceeds its pair limit"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_negative_duration():
    doc = _document()
    doc["audio"]["duration_ms"] = -1
    with pytest.raises(WaveformArtifactError, match="duration"):
        artifacts.validate_artifact_document(doc)


def test_validate_rejects_unsupported_encoding():
    doc = _document()
    doc["encoding"]["type"] = "float32_rms"
    with pytest.raises(WaveformArtifactError, match="encoding"):
        artifacts.validate_artifact_document(doc)


# ---------------------------------------------------------------------------
# Corrupt / oversized reads
# ---------------------------------------------------------------------------


def test_read_rejects_missing_artifact(cache_root):
    with pytest.raises(WaveformArtifactError, match="missing"):
        artifacts.read_artifact(cache_root, KEY)


def test_read_rejects_truncated_gzip(cache_root):
    path = artifacts.artifact_path(cache_root, KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = artifacts.serialize_artifact(_document())
    path.write_bytes(payload[: len(payload) // 2])
    with pytest.raises(WaveformArtifactError):
        artifacts.read_artifact(cache_root, KEY)


def test_read_rejects_non_gzip_bytes(cache_root):
    path = artifacts.artifact_path(cache_root, KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"this is not gzip at all")
    with pytest.raises(WaveformArtifactError):
        artifacts.read_artifact(cache_root, KEY)


def test_read_rejects_malformed_json(cache_root):
    path = artifacts.artifact_path(cache_root, KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(b"{not valid json"))
    with pytest.raises(WaveformArtifactError, match="valid JSON"):
        artifacts.read_artifact(cache_root, KEY)


def test_read_rejects_oversized_decompressed_payload(cache_root):
    """A gzip bomb must be bounded, never allocated in full."""
    path = artifacts.artifact_path(cache_root, KEY)
    path.parent.mkdir(parents=True, exist_ok=True)
    oversized = b"{" + b" " * (artifacts.MAX_DECOMPRESSED_ARTIFACT_BYTES + 1024)
    path.write_bytes(gzip.compress(oversized))
    with pytest.raises(WaveformArtifactError, match="decompressed size limit"):
        artifacts.read_artifact(cache_root, KEY)


# ---------------------------------------------------------------------------
# Path derivation, containment, and malicious keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "../../../etc/passwd",
        "a" * 63,
        "a" * 65,
        "A" * 64,          # uppercase hex is rejected
        "g" * 64,          # non-hex
        "",
        "a/../b",
        "..",
    ],
)
def test_malicious_or_malformed_generation_key_is_rejected(cache_root, bad_key):
    with pytest.raises(WaveformArtifactError):
        artifacts.artifact_path(cache_root, bad_key)


def test_artifact_path_is_inside_cache_root_with_prefix_sharding(cache_root):
    path = artifacts.artifact_path(cache_root, KEY)
    assert path.is_relative_to(cache_root.root)
    assert path.parent.name == KEY[:2]
    assert path.name == f"{KEY}.json.gz"
    assert WAVEFORM_ALGORITHM_VERSION in path.parts


def test_artifact_path_never_lands_in_the_music_library(cache_root):
    path = artifacts.artifact_path(cache_root, KEY)
    assert not path.is_relative_to(cache_root.library_root)


def test_cache_root_overlapping_library_is_still_rejected(tmp_path):
    library = tmp_path / "music"
    library.mkdir()
    with pytest.raises(WaveformCacheSafetyError):
        validate_waveform_cache_root(library / "cache", library)


# ---------------------------------------------------------------------------
# Atomic publication
# ---------------------------------------------------------------------------


def test_publish_creates_artifact_and_leaves_no_temp_file(cache_root):
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(_document()))
    final = artifacts.artifact_path(cache_root, KEY)
    assert final.is_file()
    leftovers = [p for p in cache_root.root.rglob(".tmp.*")]
    assert leftovers == []


def test_publish_uses_atomic_replace_from_inside_the_cache_root(cache_root, monkeypatch):
    seen: dict[str, Path] = {}
    real_replace = artifacts.os.replace

    def _record(src, dst):
        seen["src"] = Path(src)
        seen["dst"] = Path(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(artifacts.os, "replace", _record)
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(_document()))

    assert seen["src"].is_relative_to(cache_root.root), "temp file must live inside the cache root"
    assert seen["src"].name.startswith(".tmp."), "publication must go through a temp file"
    assert seen["dst"] == artifacts.artifact_path(cache_root, KEY)
    assert seen["src"].parent == seen["dst"].parent, "replace must be same-directory to stay atomic"


def test_failed_publication_leaves_no_final_artifact_and_cleans_temp(cache_root, monkeypatch):
    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(artifacts.os, "replace", _boom)
    with pytest.raises(OSError):
        artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(_document()))

    assert not artifacts.artifact_path(cache_root, KEY).exists()
    assert [p for p in cache_root.root.rglob(".tmp.*")] == []


def test_republish_replaces_previous_artifact_in_place(cache_root):
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(_document()))
    updated = _document()
    updated["audio"]["duration_ms"] = 999
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(updated))
    assert artifacts.read_artifact(cache_root, KEY)["audio"]["duration_ms"] == 999


def test_existing_artifact_survives_a_failed_regeneration(cache_root, monkeypatch):
    """A force-regeneration that fails must not destroy the working waveform."""
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(_document()))

    def _boom(src, dst):
        raise OSError("no space left")

    monkeypatch.setattr(artifacts.os, "replace", _boom)
    replacement = _document()
    replacement["audio"]["duration_ms"] = 4242
    with pytest.raises(OSError):
        artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(replacement))

    still_valid = artifacts.read_artifact(cache_root, KEY)
    assert still_valid["audio"]["duration_ms"] == 247381


def test_delete_artifact_only_removes_the_cache_file(cache_root):
    artifacts.publish_artifact(cache_root, KEY, artifacts.serialize_artifact(_document()))
    final = artifacts.artifact_path(cache_root, KEY)
    assert final.is_file()
    artifacts.delete_artifact(cache_root, KEY)
    assert not final.exists()
    assert cache_root.root.is_dir()
    assert cache_root.library_root.is_dir()


def test_delete_artifact_is_idempotent(cache_root):
    artifacts.delete_artifact(cache_root, KEY)  # nothing to remove; must not raise
    artifacts.delete_artifact(cache_root, KEY)
