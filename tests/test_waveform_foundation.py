"""W1 waveform foundation tests.  No audio tool or content decoder is used."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.core import preflight
from backend.app.core.waveform_cache import (
    WaveformCacheSafetyError,
    assert_waveform_cleanup_candidate,
    initialize_waveform_cache_root,
    validate_waveform_cache_root,
    waveform_cache_is_writable,
)
from backend.app.core.waveform_config import (
    DEFAULT_WAVEFORM_MAX_CACHE_BYTES,
    WaveformConfigurationError,
    load_waveform_config,
)
from backend.app.models.waveform import SourceStatSnapshot, WaveformArtifactStatus, WaveformJobStatus
from backend.app.services import track_source_service, waveform_readiness_service, waveform_state_service


def _init_jobs_db(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "operational" / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    return path


def _create_track_db(root: Path, source_path: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tracks (id, filepath, filename, status) VALUES (7, ?, ?, 'ok')",
            (str(source_path), source_path.name),
        )
    return db_path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_waveform_config_safe_defaults(tmp_path):
    config = load_waveform_config({}, backend_data_dir=tmp_path / "backend-data")
    assert config.enabled is True
    assert config.cache_dir == tmp_path / "backend-data" / "cache" / "waveforms"
    assert config.max_concurrent_jobs == 1
    assert config.max_queue_size == 32
    assert config.max_cache_bytes == DEFAULT_WAVEFORM_MAX_CACHE_BYTES


def test_waveform_config_overrides_and_disabled_feature(tmp_path):
    cache = tmp_path / "CrateIQ cache – user's"
    config = load_waveform_config({
        "CRATEIQ_WAVEFORMS_ENABLED": "off",
        "CRATEIQ_WAVEFORM_CACHE_DIR": str(cache),
        "CRATEIQ_WAVEFORM_MAX_CONCURRENCY": "2",
        "CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE": "64",
        "CRATEIQ_WAVEFORM_MAX_CACHE_BYTES": str(4 * 1024 * 1024),
    })
    assert config.enabled is False
    assert config.cache_dir == cache
    assert config.max_concurrent_jobs == 2
    assert config.max_queue_size == 64
    assert config.max_cache_bytes == 4 * 1024 * 1024


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CRATEIQ_WAVEFORM_MAX_CONCURRENCY", "0"),
        ("CRATEIQ_WAVEFORM_MAX_CONCURRENCY", "3"),
        ("CRATEIQ_WAVEFORM_MAX_CONCURRENCY", "many"),
        ("CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE", "0"),
        ("CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE", "1025"),
        ("CRATEIQ_WAVEFORM_MAX_CACHE_BYTES", "0"),
        ("CRATEIQ_WAVEFORM_MAX_CACHE_BYTES", "not-a-size"),
    ],
)
def test_waveform_config_rejects_invalid_limits(name, value, tmp_path):
    with pytest.raises(WaveformConfigurationError):
        load_waveform_config({name: value}, backend_data_dir=tmp_path)


def test_waveform_config_rejects_relative_cache(tmp_path):
    with pytest.raises(WaveformConfigurationError, match="absolute"):
        load_waveform_config({"CRATEIQ_WAVEFORM_CACHE_DIR": "relative/cache"}, backend_data_dir=tmp_path)


# ---------------------------------------------------------------------------
# Cache containment
# ---------------------------------------------------------------------------


def test_valid_application_cache_can_be_initialized_without_artifacts(tmp_path):
    library = tmp_path / "music"
    library.mkdir()
    validated = validate_waveform_cache_root(tmp_path / "app" / "cache" / "waveforms", library)
    assert waveform_cache_is_writable(validated) is True
    created = initialize_waveform_cache_root(validated)
    assert created.is_dir()
    assert list(created.iterdir()) == []
    assert created.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("relation", ["inside", "equal", "contains"])
def test_cache_and_music_root_overlap_is_rejected(tmp_path, relation):
    library = tmp_path / "music"
    library.mkdir()
    cache = {
        "inside": library / "cache",
        "equal": library,
        "contains": tmp_path,
    }[relation]
    with pytest.raises(WaveformCacheSafetyError):
        validate_waveform_cache_root(cache, library)


def test_cache_traversal_is_rejected(tmp_path):
    library = tmp_path / "music"
    library.mkdir()
    with pytest.raises(WaveformCacheSafetyError, match="traversal"):
        validate_waveform_cache_root(tmp_path / "app" / ".." / "cache", library)


def test_symlinked_cache_into_music_root_is_rejected(tmp_path):
    library = tmp_path / "music"
    library.mkdir()
    link = tmp_path / "cache-link"
    try:
        link.symlink_to(library, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    with pytest.raises(WaveformCacheSafetyError):
        validate_waveform_cache_root(link, library)


def test_cleanup_candidate_must_remain_below_validated_cache(tmp_path):
    library = tmp_path / "music"
    cache = tmp_path / "app" / "cache"
    library.mkdir()
    cache.mkdir(parents=True)
    validated = validate_waveform_cache_root(cache, library)
    safe = cache / "v1" / "track.tmp"
    assert assert_waveform_cleanup_candidate(safe, validated) == safe.resolve(strict=False)
    for unsafe in (cache, tmp_path / "outside", Path("relative/file"), cache / ".." / "outside"):
        with pytest.raises(WaveformCacheSafetyError):
            assert_waveform_cleanup_candidate(unsafe, validated)


def test_cleanup_candidate_symlink_escape_is_rejected(tmp_path):
    library = tmp_path / "music"
    cache = tmp_path / "app" / "cache"
    outside = tmp_path / "outside"
    library.mkdir()
    cache.mkdir(parents=True)
    outside.mkdir()
    link = cache / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    validated = validate_waveform_cache_root(cache, library)
    with pytest.raises(WaveformCacheSafetyError):
        assert_waveform_cleanup_candidate(link / "malicious.mp3", validated)


def test_unicode_and_special_character_cache_paths_are_supported(tmp_path):
    library = tmp_path / "Música – DJ's Library"
    cache = tmp_path / "CrateIQ Cache (δοκιμή)"
    library.mkdir()
    validated = validate_waveform_cache_root(cache, library)
    initialize_waveform_cache_root(validated)
    candidate = cache / "v1" / "track -- '..' literal.json"
    assert assert_waveform_cleanup_candidate(candidate, validated) == candidate.resolve(strict=False)


# ---------------------------------------------------------------------------
# State and jobs.db isolation
# ---------------------------------------------------------------------------


def test_new_track_state_is_not_generated_and_hash_is_nullable(tmp_path, monkeypatch):
    db_path = _init_jobs_db(tmp_path, monkeypatch)
    state = waveform_state_service.ensure_track_state(42, library_id="a" * 64)
    assert state.status is WaveformArtifactStatus.NOT_GENERATED
    assert state.source_sha256 is None
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT source_sha256 FROM waveform_track_state WHERE track_id = 42"
        ).fetchone() == (None,)


def test_valid_track_state_transitions_persist_across_connections(tmp_path, monkeypatch):
    _init_jobs_db(tmp_path, monkeypatch)
    identity = "b" * 64
    assert waveform_state_service.transition_track_state(1, "queued", library_id=identity).status.value == "queued"
    assert waveform_state_service.transition_track_state(1, "processing", library_id=identity).status.value == "processing"
    ready = waveform_state_service.transition_track_state(1, "ready", library_id=identity)
    assert ready.generated_at is not None
    assert waveform_state_service.transition_track_state(1, "stale", library_id=identity).status.value == "stale"
    assert waveform_state_service.get_track_state(1, library_id=identity).status.value == "stale"


def test_cheap_source_snapshot_persists_without_content_hash(tmp_path, monkeypatch):
    _init_jobs_db(tmp_path, monkeypatch)
    identity = "2" * 64
    snapshot = SourceStatSnapshot(identity, 11, 12345, 100, 101, 8, 9)
    queued = waveform_state_service.transition_track_state(
        11, "queued", library_id=identity, snapshot=snapshot
    )
    assert queued.source_size_bytes == 12345
    assert queued.source_mtime_ns == 100
    assert queued.source_ctime_ns == 101
    assert queued.source_device == 8
    assert queued.source_inode == 9
    assert queued.source_sha256 is None


def test_invalid_track_state_transition_is_predictable(tmp_path, monkeypatch):
    _init_jobs_db(tmp_path, monkeypatch)
    with pytest.raises(waveform_state_service.InvalidWaveformTransition, match="not_generated -> ready"):
        waveform_state_service.transition_track_state(2, "ready", library_id="c" * 64)


def test_failed_and_cancelled_track_states(tmp_path, monkeypatch):
    _init_jobs_db(tmp_path, monkeypatch)
    failed_id = "d" * 64
    waveform_state_service.transition_track_state(3, "queued", library_id=failed_id)
    failed = waveform_state_service.transition_track_state(3, "failed", library_id=failed_id, error_code="FOUNDATION_TEST")
    assert failed.status.value == "failed"
    assert failed.last_error_code == "FOUNDATION_TEST"
    cancelled_id = "e" * 64
    waveform_state_service.transition_track_state(4, "queued", library_id=cancelled_id)
    assert waveform_state_service.transition_track_state(4, "cancelled", library_id=cancelled_id).status.value == "cancelled"


def test_waveform_job_record_lifecycle_and_cancellation_flag(tmp_path, monkeypatch):
    _init_jobs_db(tmp_path, monkeypatch)
    job = waveform_state_service.create_waveform_job_record(9, library_id="f" * 64)
    assert job.status is WaveformJobStatus.QUEUED
    job = waveform_state_service.request_waveform_job_cancellation(job.id)
    assert job.cancel_requested is True
    job = waveform_state_service.transition_waveform_job(job.id, "cancelled")
    assert job.status is WaveformJobStatus.CANCELLED
    assert job.finished_at is not None
    with pytest.raises(waveform_state_service.InvalidWaveformTransition):
        waveform_state_service.transition_waveform_job(job.id, "processing")


def test_waveform_state_is_written_only_to_jobs_db(tmp_path, monkeypatch):
    library = tmp_path / "library"
    source = library / "track.mp3"
    source.parent.mkdir()
    source.write_bytes(b"fixture-not-decoded")
    processed_db = _create_track_db(library, source)
    before = processed_db.read_bytes()
    jobs_db = _init_jobs_db(tmp_path, monkeypatch)
    waveform_state_service.ensure_track_state(7, library_id="1" * 64)
    waveform_state_service.create_waveform_job_record(7, library_id="1" * 64)
    assert processed_db.read_bytes() == before
    with sqlite3.connect(processed_db) as conn:
        processed_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with sqlite3.connect(jobs_db) as conn:
        jobs_tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "waveform_track_state" not in processed_tables
    assert "waveform_jobs" not in processed_tables
    assert {"waveform_track_state", "waveform_jobs"} <= jobs_tables


def test_library_identity_is_private_and_distinguishes_roots(tmp_path):
    first = track_source_service.library_identity(tmp_path / "one")
    second = track_source_service.library_identity(tmp_path / "two")
    assert first != second
    assert len(first) == len(second) == 64
    assert str(tmp_path) not in first


def test_source_stat_snapshot_reuses_preview_validation_and_never_hashes(tmp_path, monkeypatch):
    library = tmp_path / "library"
    source = library / "sets" / "DJ's – mix (final).flac"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"stat-only-fixture")
    _create_track_db(library, source)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    snapshot = track_source_service.source_stat_snapshot(7)
    assert snapshot.track_id == 7
    assert snapshot.source_size_bytes == len(b"stat-only-fixture")
    assert len(snapshot.library_id) == 64
    assert not hasattr(snapshot, "source_sha256")
    assert not hasattr(snapshot, "path")


def test_source_stat_snapshot_rejects_outside_root(tmp_path, monkeypatch):
    library = tmp_path / "library"
    outside = tmp_path / "outside.mp3"
    library.mkdir()
    outside.write_bytes(b"outside")
    _create_track_db(library, outside)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    with pytest.raises(track_source_service.TrackSourcePathRejected):
        track_source_service.source_stat_snapshot(7)


# ---------------------------------------------------------------------------
# Readiness and privacy
# ---------------------------------------------------------------------------


def _tool_checks(ffmpeg: bool, ffprobe: bool) -> list[dict]:
    return [
        {"name": "binary_ffmpeg", "status": "pass" if ffmpeg else "warn"},
        {"name": "binary_ffprobe", "status": "pass" if ffprobe else "warn"},
    ]


def test_waveform_readiness_disabled(tmp_path):
    result = waveform_readiness_service.get_waveform_readiness(
        _tool_checks(True, True),
        environ={"CRATEIQ_WAVEFORMS_ENABLED": "0"},
        backend_data_dir=tmp_path / "backend",
        library_root=tmp_path / "library",
    )
    assert result["status"] == "disabled"
    assert result["enabled"] is False


def test_waveform_readiness_rejects_unsafe_cache(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    result = waveform_readiness_service.get_waveform_readiness(
        _tool_checks(True, True),
        environ={"CRATEIQ_WAVEFORM_CACHE_DIR": str(library / "cache")},
        library_root=library,
    )
    assert result["status"] == "misconfigured"
    assert result["cache_ready"] is False


def test_waveform_readiness_cache_unavailable(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(waveform_readiness_service, "waveform_cache_is_writable", lambda validated: False)
    result = waveform_readiness_service.get_waveform_readiness(
        _tool_checks(True, True),
        environ={"CRATEIQ_WAVEFORM_CACHE_DIR": str(tmp_path / "cache")},
        library_root=library,
    )
    assert result["status"] == "cache_unavailable"


def test_waveform_readiness_extractor_absent(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(waveform_readiness_service.shutil, "which", lambda name: None)
    result = waveform_readiness_service.get_waveform_readiness(
        _tool_checks(False, False),
        environ={"CRATEIQ_WAVEFORM_CACHE_DIR": str(tmp_path / "cache")},
        library_root=library,
    )
    assert result["status"] == "extractor_unavailable"
    assert result["cache_ready"] is True


def test_waveform_readiness_detected_but_not_runtime_verified(tmp_path, monkeypatch):
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setattr(waveform_readiness_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = waveform_readiness_service.get_waveform_readiness(
        _tool_checks(True, True),
        environ={"CRATEIQ_WAVEFORM_CACHE_DIR": str(tmp_path / "cache")},
        library_root=library,
    )
    assert result["status"] == "detected"
    assert result["engine"] == {
        "name": "ffmpeg",
        "detected": True,
        "ffmpeg_detected": True,
        "ffprobe_detected": True,
        "version_verified": False,
    }
    assert str(tmp_path) not in json.dumps(result)
    assert "/usr/bin" not in json.dumps(result)


def test_readiness_api_is_private_and_app_starts_when_waveforms_unavailable(tmp_path, monkeypatch):
    library = tmp_path / "private library"
    library.mkdir()
    (library / "logs").mkdir()
    (library / "logs" / "processed.db").write_bytes(b"")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(library / "unsafe-cache"))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    with TestClient(backend_main.app) as client:
        response = client.get("/api/runtime/readiness")
        health = client.get("/api/health")
    assert response.status_code == 200
    assert health.status_code == 200
    assert response.json()["waveform"]["status"] == "misconfigured"
    assert str(library) not in response.text
    assert str(tmp_path) not in response.text
