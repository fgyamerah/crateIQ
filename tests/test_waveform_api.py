"""API tests for the waveform generation lifecycle (W3) and cache actions (W6).

Every test runs against a temporary fixture library, a temporary jobs.db, and
a temporary cache directory. A module-wide autouse guard makes any attempt to
spawn a subprocess an immediate failure, so these tests prove the API surface
never reaches FFmpeg or ffprobe.
"""
from __future__ import annotations

import asyncio
import gzip
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.core.waveform_cache import validate_waveform_cache_root
from backend.app.models.waveform import (
    WAVEFORM_ALGORITHM_VERSION,
    SourceStatSnapshot,
    WaveformArtifactStatus,
    WaveformJobStatus,
)
from backend.app.models.waveform_extraction import WaveformExtractionResult
from backend.app.services import (
    track_source_service,
    waveform_artifact_service,
    waveform_identity,
    waveform_job_service,
    waveform_readiness_service,
    waveform_scheduler,
    waveform_state_service,
)

TRACK_ID = 7


# ---------------------------------------------------------------------------
# Hard guard: the API surface must never spawn a process
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _forbid_subprocesses(monkeypatch):
    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("waveform API tests must never spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _forbidden)


class _InertScheduler(waveform_scheduler.WaveformScheduler):
    """Accepts jobs and records them, but never runs the production runner."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.enqueued: list[str] = []
        self.cancelled: list[str] = []

    async def start(self) -> None:  # no workers: jobs stay queued
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def enqueue(self, job_id: str) -> bool:
        self.enqueued.append(job_id)
        return True

    def signal_cancel(self, job_id: str) -> bool:
        self.cancelled.append(job_id)
        return True


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A temporary library, jobs.db, cache root, and an inert scheduler."""
    library = tmp_path / "library"
    (library / "logs").mkdir(parents=True)
    source = library / "Set – DJ's mix (final).mp3"
    source.write_bytes(b"synthetic-fixture-never-decoded")

    with sqlite3.connect(library / "logs" / "processed.db") as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tracks (id, filepath, filename, status) VALUES (?, ?, ?, 'ok')",
            (TRACK_ID, str(source), source.name),
        )

    cache_dir = tmp_path / "app-cache" / "waveforms"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()

    # Pretend the toolchain exists so capability gating does not mask API tests.
    monkeypatch.setattr(
        "backend.app.services.waveform_readiness_service.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}",
        raising=False,
    )
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: f"/usr/bin/{name}",
    )
    # Pre-seed W6's startup extractor verification so lifespan has nothing to
    # verify. The subprocess guard above must stay meaningful for the request
    # surface, which is what these tests are actually about.
    monkeypatch.setattr(
        waveform_readiness_service,
        "_verification_cache",
        waveform_readiness_service.ExtractorVerification(
            verified=True, ffmpeg_verified=True, ffprobe_verified=True,
            ffmpeg_version="ffmpeg version 6.0", ffprobe_version="ffprobe version 6.0",
        ),
    )

    scheduler = _InertScheduler()
    waveform_scheduler.set_scheduler(scheduler)
    yield {
        "library": library,
        "source": source,
        "cache": validate_waveform_cache_root(cache_dir, library),
        "scheduler": scheduler,
        "tmp_path": tmp_path,
    }
    waveform_scheduler.set_scheduler(None)


@pytest.fixture()
def client(env):
    with TestClient(backend_main.app) as test_client:
        yield test_client


def _library_id(env) -> str:
    return track_source_service.library_identity(env["library"])


def _publish_ready(env, *, duration_ms: int = 247381) -> str:
    """Publish a synthetic ready artifact and mark the track ready."""
    snapshot = track_source_service.source_stat_snapshot(TRACK_ID)
    key = waveform_identity.compute_generation_key(snapshot)
    result = WaveformExtractionResult(
        duration_ms=duration_ms,
        source_channels=2,
        source_sample_rate_hz=44100,
        analysis_sample_rate_hz=8000,
        encoding="int16_min_max_interleaved",
        resolutions={
            "compact": [-100, 100] * 4,
            "player": [-200, 200] * 6,
            "detail": [-300, 300] * 10,
        },
    )
    document = waveform_artifact_service.build_artifact_document(
        result, generation_key=key, snapshot=snapshot
    )
    waveform_artifact_service.publish_artifact(
        env["cache"], key, waveform_artifact_service.serialize_artifact(document)
    )
    library_id = snapshot.library_id
    waveform_state_service.transition_track_state(TRACK_ID, "queued", library_id=library_id, snapshot=snapshot)
    waveform_state_service.transition_track_state(TRACK_ID, "processing", library_id=library_id)
    waveform_state_service.transition_track_state(
        TRACK_ID, "ready", library_id=library_id, snapshot=snapshot, cache_key=key
    )
    return key


# ---------------------------------------------------------------------------
# GET semantics
# ---------------------------------------------------------------------------


def test_get_missing_track_returns_404(client):
    assert client.get("/api/tracks/9999/waveform").status_code == 404


def test_get_valid_track_without_waveform_is_a_normal_200_state(client):
    response = client.get(f"/api/tracks/{TRACK_ID}/waveform")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_generated"
    assert body["track_id"] == TRACK_ID
    assert body["peaks"] is None


def test_get_rejects_an_unknown_resolution(client):
    assert client.get(f"/api/tracks/{TRACK_ID}/waveform?resolution=ultra").status_code == 422


@pytest.mark.parametrize("resolution", ["compact", "player", "detail"])
def test_get_ready_returns_peaks_for_every_named_resolution(client, env, resolution):
    _publish_ready(env)
    response = client.get(f"/api/tracks/{TRACK_ID}/waveform?resolution={resolution}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["resolution"] == resolution
    assert body["duration_ms"] == 247381
    assert body["algorithm_version"] == WAVEFORM_ALGORITHM_VERSION
    assert body["encoding"] == {
        "type": "int16_min_max_interleaved",
        "scale": 32767,
        "rendered_channels": 1,
    }
    assert len(body["peaks"]) == body["pair_count"] * 2
    assert body["pair_count"] > 0


def test_get_reports_queued_and_processing_states(client, env):
    library_id = _library_id(env)
    snapshot = track_source_service.source_stat_snapshot(TRACK_ID)
    key = waveform_identity.compute_generation_key(snapshot)
    submitted = waveform_job_service.submit_generation_job(
        snapshot=snapshot, generation_key=key, force=False, max_queue_size=32
    )
    body = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert body["status"] == "queued"
    assert body["job_id"] == submitted.job.id

    waveform_job_service.claim_job(submitted.job.id)
    body = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert body["status"] == "processing"
    assert body["job_id"] == submitted.job.id


def test_get_reports_stale_when_the_source_changed(client, env):
    _publish_ready(env)
    env["source"].write_bytes(b"the source was replaced after generation")
    body = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert body["status"] == "stale"
    assert body["peaks"] is None


def test_get_degrades_safely_when_the_cache_artifact_is_missing(client, env):
    key = _publish_ready(env)
    waveform_artifact_service.artifact_path(env["cache"], key).unlink()
    body = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert body["status"] == "stale"
    assert body["peaks"] is None


def test_get_degrades_safely_when_the_cache_artifact_is_corrupt(client, env):
    key = _publish_ready(env)
    waveform_artifact_service.artifact_path(env["cache"], key).write_bytes(b"corrupted not gzip")
    response = client.get(f"/api/tracks/{TRACK_ID}/waveform")
    assert response.status_code == 200
    assert response.json()["status"] == "stale"
    # The degraded read repaired operational state rather than crashing.
    assert waveform_state_service.get_track_state(
        TRACK_ID, library_id=_library_id(env)
    ).status is WaveformArtifactStatus.STALE


def test_get_degrades_safely_for_a_gzip_bomb(client, env):
    key = _publish_ready(env)
    oversized = b"{" + b" " * (waveform_artifact_service.MAX_DECOMPRESSED_ARTIFACT_BYTES + 1024)
    waveform_artifact_service.artifact_path(env["cache"], key).write_bytes(gzip.compress(oversized))
    assert client.get(f"/api/tracks/{TRACK_ID}/waveform").json()["status"] == "stale"


def test_get_reports_a_previous_failure_without_raw_detail(client, env):
    library_id = _library_id(env)
    snapshot = track_source_service.source_stat_snapshot(TRACK_ID)
    waveform_state_service.transition_track_state(TRACK_ID, "queued", library_id=library_id, snapshot=snapshot)
    waveform_state_service.transition_track_state(
        TRACK_ID, "failed", library_id=library_id, error_code="decode_failure"
    )
    body = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert body["status"] == "failed"
    assert body["error_code"] == "decode_failure"


def test_get_never_enqueues_a_job_or_creates_a_cache_artifact(client, env):
    for resolution in ("compact", "player", "detail"):
        client.get(f"/api/tracks/{TRACK_ID}/waveform?resolution={resolution}")
    assert env["scheduler"].enqueued == []
    with sqlite3.connect(backend_db.JOBS_DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM waveform_jobs").fetchone()[0] == 0
    assert list(env["cache"].root.rglob("*.json.gz")) == []


def test_get_never_reads_source_audio_content(client, env, monkeypatch):
    """A read-only GET must stat the source but never open its contents."""
    real_open = Path.open

    def _guard(self, *args, **kwargs):
        if self == env["source"]:  # pragma: no cover - must never run
            raise AssertionError("GET must not read source audio content")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _guard)
    _publish_ready(env)
    assert client.get(f"/api/tracks/{TRACK_ID}/waveform").status_code == 200


def test_get_responses_never_leak_paths(client, env):
    _publish_ready(env)
    text = client.get(f"/api/tracks/{TRACK_ID}/waveform").text
    for forbidden in (str(env["library"]), str(env["cache"].root), str(env["tmp_path"]), "/usr/bin", ".mp3"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# ETag
# ---------------------------------------------------------------------------


def test_ready_response_carries_a_deterministic_etag(client, env):
    _publish_ready(env)
    first = client.get(f"/api/tracks/{TRACK_ID}/waveform")
    second = client.get(f"/api/tracks/{TRACK_ID}/waveform")
    assert first.headers["etag"]
    assert first.headers["etag"] == second.headers["etag"]


def test_etag_differs_per_resolution(client, env):
    _publish_ready(env)
    compact = client.get(f"/api/tracks/{TRACK_ID}/waveform?resolution=compact").headers["etag"]
    detail = client.get(f"/api/tracks/{TRACK_ID}/waveform?resolution=detail").headers["etag"]
    assert compact != detail


def test_matching_if_none_match_returns_304_with_no_payload(client, env):
    _publish_ready(env)
    etag = client.get(f"/api/tracks/{TRACK_ID}/waveform").headers["etag"]
    response = client.get(
        f"/api/tracks/{TRACK_ID}/waveform", headers={"If-None-Match": etag}
    )
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag


def test_stale_etag_still_returns_a_full_payload(client, env):
    _publish_ready(env)
    response = client.get(
        f"/api/tracks/{TRACK_ID}/waveform", headers={"If-None-Match": '"not-the-current-etag"'}
    )
    assert response.status_code == 200
    assert response.json()["peaks"]


def test_etag_contains_no_filesystem_detail(client, env):
    _publish_ready(env)
    etag = client.get(f"/api/tracks/{TRACK_ID}/waveform").headers["etag"]
    assert str(env["cache"].root) not in etag
    assert str(env["library"]) not in etag


# ---------------------------------------------------------------------------
# POST semantics
# ---------------------------------------------------------------------------


def test_post_queues_a_job(client, env):
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate", json={"force": False})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["deduplicated"] is False
    assert body["job_id"]
    assert env["scheduler"].enqueued == [body["job_id"]]


def test_post_without_a_body_defaults_to_non_forced(client, env):
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_post_deduplicates_an_active_job(client, env):
    first = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()
    second = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()
    assert second["job_id"] == first["job_id"]
    assert second["deduplicated"] is True
    assert len(env["scheduler"].enqueued) == 1


def test_post_force_does_not_duplicate_an_active_job(client, env):
    first = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate", json={"force": False}).json()
    forced = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate", json={"force": True}).json()
    assert forced["job_id"] == first["job_id"]
    assert forced["deduplicated"] is True
    assert len(env["scheduler"].enqueued) == 1


def test_post_with_ready_cache_and_force_false_returns_ready_without_queueing(client, env):
    _publish_ready(env)
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate", json={"force": False})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["job_id"] is None
    assert env["scheduler"].enqueued == []


def test_post_force_true_queues_regeneration_and_keeps_the_old_waveform_readable(client, env):
    _publish_ready(env)
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate", json={"force": True})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    # The previously published waveform must remain servable meanwhile.
    still_ready = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert still_ready["status"] == "ready"
    assert still_ready["peaks"]


def test_post_missing_track_returns_404(client):
    assert client.post("/api/tracks/9999/waveform/generate").status_code == 404


def test_post_returns_429_when_the_queue_is_full(client, env, monkeypatch):
    monkeypatch.setattr(env["scheduler"], "max_queue_size", 0)
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 429
    assert response.json()["detail"] == "WAVEFORM_QUEUE_FULL"
    assert response.headers.get("retry-after") == "5"


def test_post_rejects_a_source_over_the_size_policy(client, env, monkeypatch):
    from backend.app.core import waveform_limits

    monkeypatch.setattr(waveform_limits, "MAX_SOURCE_SIZE_BYTES", 1)
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 413
    assert response.json()["detail"] == "WAVEFORM_POLICY_REJECTED"


def test_post_is_unavailable_when_the_feature_is_disabled(client, env, monkeypatch):
    monkeypatch.setenv("CRATEIQ_WAVEFORMS_ENABLED", "0")
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 503
    assert response.json()["detail"] == "WAVEFORM_DISABLED"


def test_post_is_unavailable_when_the_extractor_is_missing(client, env, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: None,
    )
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 503
    assert response.json()["detail"] == "WAVEFORM_EXTRACTOR_UNAVAILABLE"


def test_post_is_unavailable_when_the_cache_overlaps_the_library(client, env, monkeypatch):
    monkeypatch.setenv("CRATEIQ_WAVEFORM_CACHE_DIR", str(env["library"] / "waveform-cache"))
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 503
    assert response.json()["detail"] == "WAVEFORM_MISCONFIGURED"


def test_cached_waveform_stays_readable_when_the_extractor_disappears(client, env, monkeypatch):
    """Reading cache and generating new cache are separate capabilities."""
    _publish_ready(env)
    monkeypatch.setattr(
        "backend.app.services.waveform_probe.resolve_executable",
        lambda name, env_var, **kw: None,
    )
    response = client.get(f"/api/tracks/{TRACK_ID}/waveform")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").status_code == 503


def test_post_responses_never_leak_paths(client, env):
    text = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").text
    for forbidden in (str(env["library"]), str(env["tmp_path"]), "/usr/bin", ".mp3"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# Job status and cancellation
# ---------------------------------------------------------------------------


def test_job_status_reports_the_lifecycle(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    body = client.get(f"/api/waveform-jobs/{job_id}").json()
    assert body["job_id"] == job_id
    assert body["track_id"] == TRACK_ID
    assert body["status"] == "queued"
    assert body["created_at"]

    waveform_job_service.claim_job(job_id)
    assert client.get(f"/api/waveform-jobs/{job_id}").json()["status"] == "processing"


def test_job_status_for_a_succeeded_job(client, env):
    snapshot = track_source_service.source_stat_snapshot(TRACK_ID)
    key = waveform_identity.compute_generation_key(snapshot)
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    waveform_job_service.claim_job(job_id)
    waveform_job_service.complete_job_ready(job_id, generation_key=key, snapshot=snapshot)
    body = client.get(f"/api/waveform-jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    assert body["finished_at"]


def test_job_status_for_a_failed_job(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    waveform_job_service.claim_job(job_id)
    waveform_job_service.finish_job_unsuccessfully(
        job_id,
        job_status=WaveformJobStatus.FAILED,
        track_status=WaveformArtifactStatus.FAILED,
        error_code="decode_failure",
    )
    body = client.get(f"/api/waveform-jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert body["error_code"] == "decode_failure"


def test_missing_job_returns_404(client):
    assert client.get("/api/waveform-jobs/does-not-exist").status_code == 404
    assert client.delete("/api/waveform-jobs/does-not-exist").status_code == 404


def test_job_response_is_privacy_safe(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    body = client.get(f"/api/waveform-jobs/{job_id}").json()
    assert set(body) == {
        "job_id", "track_id", "status", "created_at",
        "started_at", "finished_at", "cancel_requested", "error_code",
    }
    text = client.get(f"/api/waveform-jobs/{job_id}").text
    for forbidden in (str(env["library"]), str(env["tmp_path"]), "/usr/bin", "ffmpeg", ".mp3"):
        assert forbidden not in text


def test_job_id_is_opaque_and_encodes_no_path(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    assert "/" not in job_id and "." not in job_id
    assert env["source"].name not in job_id


def test_cancel_a_queued_job(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    response = client.delete(f"/api/waveform-jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.get(f"/api/tracks/{TRACK_ID}/waveform").json()["status"] == "cancelled"


def test_cancel_a_processing_job_signals_the_scheduler(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    waveform_job_service.claim_job(job_id)
    response = client.delete(f"/api/waveform-jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["cancel_requested"] is True
    assert env["scheduler"].cancelled == [job_id]


def test_cancel_after_success_keeps_the_ready_artifact(client, env):
    key = _publish_ready(env)
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate", json={"force": True}).json()["job_id"]
    waveform_job_service.claim_job(job_id)
    snapshot = track_source_service.source_stat_snapshot(TRACK_ID)
    waveform_job_service.complete_job_ready(job_id, generation_key=key, snapshot=snapshot)

    assert client.delete(f"/api/waveform-jobs/{job_id}").json()["status"] == "succeeded"
    ready = client.get(f"/api/tracks/{TRACK_ID}/waveform")
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    assert waveform_artifact_service.artifact_path(env["cache"], key).is_file()


def test_cancel_is_idempotent_over_http(client, env):
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    first = client.delete(f"/api/waveform-jobs/{job_id}")
    second = client.delete(f"/api/waveform-jobs/{job_id}")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "cancelled"


def test_cancelling_then_regenerating_is_allowed(client, env):
    first = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    client.delete(f"/api/waveform-jobs/{first}")
    again = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert again.status_code == 202
    assert again.json()["job_id"] != first


# ---------------------------------------------------------------------------
# Database isolation and startup behaviour
# ---------------------------------------------------------------------------


def test_generation_never_writes_to_processed_db(client, env):
    processed = env["library"] / "logs" / "processed.db"
    before = processed.read_bytes()
    client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    client.get(f"/api/tracks/{TRACK_ID}/waveform")
    assert processed.read_bytes() == before
    with sqlite3.connect(processed) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "waveform_jobs" not in tables and "waveform_track_state" not in tables


def test_application_startup_generates_nothing(env):
    """Merely starting the backend must never create waveform work."""
    with TestClient(backend_main.app):
        pass
    with sqlite3.connect(backend_db.JOBS_DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM waveform_jobs").fetchone()[0] == 0
    if env["cache"].root.exists():
        assert list(env["cache"].root.rglob("*.json.gz")) == []
    assert env["scheduler"].enqueued == []


def test_restart_does_not_resume_persisted_jobs_through_the_app(client, env):
    """A queued job survives a restart as a terminal row, never as new work."""
    job_id = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").json()["job_id"]
    env["scheduler"].enqueued.clear()

    with TestClient(backend_main.app) as restarted:
        body = restarted.get(f"/api/waveform-jobs/{job_id}").json()
        assert body["status"] == "cancelled"
        assert body["error_code"] == waveform_job_service.ERROR_BACKEND_RESTARTED
    assert env["scheduler"].enqueued == []


def test_source_outside_the_library_root_is_rejected(client, env):
    outside = env["tmp_path"] / "outside.mp3"
    outside.write_bytes(b"outside the library")
    with sqlite3.connect(env["library"] / "logs" / "processed.db") as conn:
        conn.execute("UPDATE tracks SET filepath = ? WHERE id = ?", (str(outside), TRACK_ID))
    assert client.post(f"/api/tracks/{TRACK_ID}/waveform/generate").status_code == 403


# ---------------------------------------------------------------------------
# W6 — cache status and the manual clear action
# ---------------------------------------------------------------------------


def test_cache_status_reports_an_empty_cache(client):
    body = client.get("/api/waveform-cache").json()
    assert body["artifact_count"] == 0
    assert body["temp_count"] == 0
    assert body["current_cache_bytes"] == 0
    assert body["ready_track_count"] == 0
    assert body["over_limit"] is False
    assert body["max_cache_bytes"] > 0


def test_cache_status_counts_a_published_artifact(client, env):
    _publish_ready(env)
    body = client.get("/api/waveform-cache").json()
    assert body["artifact_count"] == 1
    assert body["ready_track_count"] == 1
    assert body["current_cache_bytes"] > 0


def test_cache_status_exposes_no_paths(client, env):
    _publish_ready(env)
    raw = client.get("/api/waveform-cache").text
    assert str(env["tmp_path"]) not in raw
    assert "/usr/bin" not in raw
    assert ".mp3" not in raw
    assert "library" not in raw.lower()


def test_cache_status_is_read_only(client, env):
    key = _publish_ready(env)
    artifact = waveform_artifact_service.artifact_path(env["cache"], key)
    client.get("/api/waveform-cache")
    assert artifact.exists()
    assert env["scheduler"].enqueued == []


def test_clear_requires_explicit_confirmation(client, env):
    key = _publish_ready(env)
    artifact = waveform_artifact_service.artifact_path(env["cache"], key)
    response = client.post("/api/waveform-cache/clear", json={"confirm": False})
    assert response.status_code == 400
    assert response.json()["detail"] == "WAVEFORM_CACHE_CLEAR_NOT_CONFIRMED"
    assert artifact.exists(), "an unconfirmed clear must delete nothing"


def test_clear_with_an_empty_body_is_rejected(client, env):
    key = _publish_ready(env)
    assert client.post("/api/waveform-cache/clear").status_code == 400
    assert waveform_artifact_service.artifact_path(env["cache"], key).exists()


def test_confirmed_clear_removes_the_artifact_and_resets_state(client, env):
    key = _publish_ready(env)
    artifact = waveform_artifact_service.artifact_path(env["cache"], key)
    body = client.post("/api/waveform-cache/clear", json={"confirm": True}).json()
    assert body["removed_files"] == 1
    assert body["reset_track_states"] == 1
    assert body["remaining_files"] == 0
    assert body["current_cache_bytes"] == 0
    assert not artifact.exists()
    state = client.get(f"/api/tracks/{TRACK_ID}/waveform").json()
    assert state["status"] == "stale"


def test_confirmed_clear_never_touches_source_audio(client, env):
    before = env["source"].read_bytes()
    _publish_ready(env)
    client.post("/api/waveform-cache/clear", json={"confirm": True})
    assert env["source"].exists()
    assert env["source"].read_bytes() == before
    assert env["library"].exists()


def test_confirmed_clear_never_writes_to_processed_db(client, env):
    processed = env["library"] / "logs" / "processed.db"
    _publish_ready(env)
    before = processed.read_bytes()
    client.post("/api/waveform-cache/clear", json={"confirm": True})
    assert processed.read_bytes() == before


def test_confirmed_clear_regenerates_nothing_on_its_own(client, env):
    _publish_ready(env)
    env["scheduler"].enqueued.clear()
    client.post("/api/waveform-cache/clear", json={"confirm": True})
    assert env["scheduler"].enqueued == [], "clearing must never queue replacement work"


def test_a_cleared_track_can_be_explicitly_regenerated(client, env):
    _publish_ready(env)
    client.post("/api/waveform-cache/clear", json={"confirm": True})
    response = client.post(f"/api/tracks/{TRACK_ID}/waveform/generate")
    assert response.status_code == 202
    assert response.json()["job_id"]


def test_confirmed_clear_is_idempotent(client, env):
    _publish_ready(env)
    first = client.post("/api/waveform-cache/clear", json={"confirm": True}).json()
    second = client.post("/api/waveform-cache/clear", json={"confirm": True}).json()
    assert first["removed_files"] == 1
    assert second["removed_files"] == 0
    assert second["reset_track_states"] == 0


def test_clear_response_exposes_no_paths(client, env):
    _publish_ready(env)
    raw = client.post("/api/waveform-cache/clear", json={"confirm": True}).text
    assert str(env["tmp_path"]) not in raw
    assert ".mp3" not in raw


def test_startup_verifies_the_extractor_toolchain_once(env, monkeypatch):
    """Runtime verification belongs to startup, not to the readiness GET."""
    calls: list[int] = []

    async def _record(*args, **kwargs):
        calls.append(1)
        return None

    monkeypatch.setattr(backend_main, "verify_extractor_runtime", _record)
    with TestClient(backend_main.app):
        pass
    assert calls == [1]


def test_startup_survives_a_failing_extractor_verification(env, monkeypatch):
    async def _explode(*args, **kwargs):
        raise RuntimeError("toolchain check blew up")

    monkeypatch.setattr(backend_main, "verify_extractor_runtime", _explode)
    with TestClient(backend_main.app) as started:
        assert started.get("/api/waveform-cache").status_code == 200
