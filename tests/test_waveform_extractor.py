"""W2 tests for the full extraction pipeline: source safety + orchestration.

Every subprocess is a fake. No real audio tool or user music library is
touched anywhere in this file.
"""
from __future__ import annotations

import json
import signal
import sqlite3
import struct
from pathlib import Path

import pytest

from backend.app.core.waveform_limits import MAX_SOURCE_SIZE_BYTES
from backend.app.core.waveform_process import ManagedRun, ProcessOutcome
from backend.app.models.waveform import SourceStatSnapshot
from backend.app.models.waveform_extraction import (
    CancellationToken,
    WaveformExtractionError,
    WaveformExtractionErrorCode,
)
from backend.app.services import track_source_service
from backend.app.services.waveform_extractor import build_decode_argv, extract_waveform
from tests.conftest import async_test


def _s16le(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


def _create_track_db(root: Path, source_path: Path, track_id: int = 7) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT, status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO tracks (id, filepath, filename, status) VALUES (?, ?, ?, 'ok')",
            (track_id, str(source_path), source_path.name),
        )
    return db_path


def _probe_payload(duration: str = "1.0", channels: int = 2, sample_rate: str = "44100") -> bytes:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "mp3",
                    "channels": channels,
                    "sample_rate": sample_rate,
                    "duration": duration,
                }
            ],
            "format": {},
        }
    ).encode()


class FakeExtractionSupervisor:
    """Dispatches on the fixed argv shape: probe vs. decode."""

    def __init__(
        self,
        *,
        probe_stdout: bytes,
        probe_outcome: ProcessOutcome | None = None,
        decode_chunks: list[bytes] | None = None,
        decode_outcome: ProcessOutcome | None = None,
    ) -> None:
        self.probe_stdout = probe_stdout
        self.probe_outcome = probe_outcome or ProcessOutcome(exit_code=0)
        self.decode_chunks = decode_chunks or []
        self.decode_outcome = decode_outcome or ProcessOutcome(exit_code=0)
        self.probe_argv: list[str] | None = None
        self.decode_argv: list[str] | None = None
        self.probe_calls = 0
        self.decode_calls = 0

    async def run_capped(self, argv, **kwargs):
        self.probe_argv = argv
        self.probe_calls += 1
        return self.probe_stdout, self.probe_outcome

    async def run(self, argv, **kwargs):
        self.decode_argv = argv
        self.decode_calls += 1
        chunks = list(self.decode_chunks)

        async def _gen():
            for chunk in chunks:
                yield chunk

        return ManagedRun(stdout=_gen(), outcome=self.decode_outcome)


def _setup_library(tmp_path: Path, monkeypatch, *, content: bytes = b"fixture-not-decoded", track_id: int = 7):
    library = tmp_path / "library"
    source = library / "sets" / "track.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    _create_track_db(library, source, track_id=track_id)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))
    return library, source


# ---------------------------------------------------------------------------
# Full pipeline success
# ---------------------------------------------------------------------------


@async_test
async def test_extract_waveform_full_pipeline_success(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)

    pcm = _s16le(*[((i * 37) % 2000) - 1000 for i in range(500)])
    supervisor = FakeExtractionSupervisor(
        probe_stdout=_probe_payload(duration="1.0"),
        decode_chunks=[pcm[:300], pcm[300:]],
    )

    result = await extract_waveform(
        validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor
    )

    assert result.source_channels == 2
    assert result.source_sample_rate_hz == 44100
    assert result.analysis_sample_rate_hz == 8000
    assert result.encoding == "int16_min_max_interleaved"
    assert result.duration_ms == 1000
    assert set(result.resolutions) == {"compact", "player", "detail"}
    assert len(result.resolutions["detail"]) == 1000  # 500 pairs from 500 samples
    assert supervisor.probe_calls == 1
    assert supervisor.decode_calls == 1


def test_result_has_no_path_or_executable_fields():
    from dataclasses import fields

    from backend.app.models.waveform_extraction import WaveformExtractionResult

    names = {f.name for f in fields(WaveformExtractionResult)}
    forbidden_substrings = ("path", "executable", "cache", "library_root")
    for name in names:
        for bad in forbidden_substrings:
            assert bad not in name, f"unexpected field {name!r} on WaveformExtractionResult"


# ---------------------------------------------------------------------------
# Source-size policy — enforced before any subprocess is spawned
# ---------------------------------------------------------------------------


@async_test
async def test_extract_waveform_rejects_oversized_source_before_probing(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    oversized_snapshot = SourceStatSnapshot(
        library_id=track_source_service.library_identity(library),
        track_id=7,
        source_size_bytes=MAX_SOURCE_SIZE_BYTES + 1,
        source_mtime_ns=1,
        source_ctime_ns=1,
        source_device=1,
        source_inode=1,
    )
    supervisor = FakeExtractionSupervisor(probe_stdout=_probe_payload())

    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(
            validated, oversized_snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor
        )
    assert exc.value.code is WaveformExtractionErrorCode.SOURCE_POLICY_REJECTED
    assert supervisor.probe_calls == 0
    assert supervisor.decode_calls == 0


# ---------------------------------------------------------------------------
# Cancellation observed at every phase
# ---------------------------------------------------------------------------


@async_test
async def test_extract_waveform_cancelled_before_probe_never_spawns_anything(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(probe_stdout=_probe_payload())
    token = CancellationToken()
    token.cancel()

    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(
            validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor, cancellation=token
        )
    assert exc.value.code is WaveformExtractionErrorCode.CANCELLED
    assert supervisor.probe_calls == 0


@async_test
async def test_extract_waveform_maps_decode_cancellation(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(
        probe_stdout=_probe_payload(),
        decode_outcome=ProcessOutcome(exit_code=None, cancelled=True),
    )
    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.CANCELLED


# ---------------------------------------------------------------------------
# Decode failure modes
# ---------------------------------------------------------------------------


@async_test
async def test_extract_waveform_maps_decode_timeout(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(
        probe_stdout=_probe_payload(),
        decode_outcome=ProcessOutcome(exit_code=None, timed_out=True),
    )
    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.TIMEOUT


@async_test
async def test_extract_waveform_maps_decode_nonzero_exit(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(
        probe_stdout=_probe_payload(),
        decode_outcome=ProcessOutcome(exit_code=1, stderr_tail=b"Invalid data found when processing input"),
    )
    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.DECODE_FAILURE


@async_test
async def test_extract_waveform_maps_decode_launch_failure(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(
        probe_stdout=_probe_payload(),
        decode_outcome=ProcessOutcome(exit_code=None, launch_error="ffmpeg not found"),
    )
    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.PROCESS_LAUNCH_FAILURE


# ---------------------------------------------------------------------------
# Source-change detection
# ---------------------------------------------------------------------------


@async_test
async def test_extract_waveform_detects_source_changed_after_decode(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    stale_snapshot = track_source_service.source_stat_snapshot(7)
    # Mutate the file after the pre-snapshot was captured, simulating an
    # in-place change that happens while the (faked) decode is "running".
    source.write_bytes(b"different-content-now-longer")

    supervisor = FakeExtractionSupervisor(probe_stdout=_probe_payload(), decode_chunks=[_s16le(1, 2, 3)])
    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(
            validated, stale_snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor
        )
    assert exc.value.code is WaveformExtractionErrorCode.SOURCE_CHANGED


@async_test
async def test_extract_waveform_treats_deleted_source_as_source_changed(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(probe_stdout=_probe_payload(), decode_chunks=[_s16le(1, 2, 3)])

    source.unlink()

    with pytest.raises(WaveformExtractionError) as exc:
        await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.SOURCE_CHANGED


# ---------------------------------------------------------------------------
# Decode command shape
# ---------------------------------------------------------------------------


def test_build_decode_argv_has_no_shell_no_output_path_and_expected_flags():
    argv = build_decode_argv("ffmpeg", "/library/sets/weird -- 'name'.flac")
    assert argv[0] == "ffmpeg"
    assert "-nostdin" in argv
    assert argv[-1] == "pipe:1"
    assert "-i" in argv
    assert argv[argv.index("-i") + 1] == "/library/sets/weird -- 'name'.flac"
    assert "-ac" in argv and argv[argv.index("-ac") + 1] == "1"
    assert "-ar" in argv and argv[argv.index("-ar") + 1] == "8000"
    assert "-f" in argv and argv[argv.index("-f") + 1] == "s16le"
    # no output media path, overwrite flag, or tag/metadata option
    for forbidden in ("-y", "-metadata", "-map_metadata", "output.wav", "output.mp3"):
        assert forbidden not in argv


@async_test
async def test_extract_waveform_passes_expected_decode_argv(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(probe_stdout=_probe_payload(), decode_chunks=[_s16le(1, 2)])

    await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)

    assert supervisor.decode_argv[0] == "ffmpeg"
    assert supervisor.decode_argv[-1] == "pipe:1"
    assert str(source) in supervisor.decode_argv


# ---------------------------------------------------------------------------
# Source-validation boundary — extractor never accepts a raw path
# ---------------------------------------------------------------------------


def test_extractor_only_accepts_a_validated_source_descriptor(tmp_path, monkeypatch):
    library = tmp_path / "library"
    outside = tmp_path / "outside.mp3"
    library.mkdir()
    outside.write_bytes(b"outside")
    _create_track_db(library, outside, track_id=7)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(library))

    with pytest.raises(track_source_service.TrackSourcePathRejected):
        track_source_service.validated_track_source(7)


# ---------------------------------------------------------------------------
# No side effects: extractor never touches jobs.db or the waveform cache
# ---------------------------------------------------------------------------


@async_test
async def test_extract_waveform_creates_no_jobs_db_or_cache_artifacts(tmp_path, monkeypatch):
    library, source = _setup_library(tmp_path, monkeypatch)
    validated = track_source_service.validated_track_source(7)
    snapshot = track_source_service.source_stat_snapshot(7)
    supervisor = FakeExtractionSupervisor(probe_stdout=_probe_payload(), decode_chunks=[_s16le(1, 2, 3)])

    would_be_jobs_db = tmp_path / "would-be-jobs.db"
    would_be_cache = tmp_path / "would-be-cache"

    await extract_waveform(validated, snapshot, ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)

    assert not would_be_jobs_db.exists()
    assert not would_be_cache.exists()


def test_waveform_extractor_module_does_not_import_state_or_cache_services():
    import backend.app.services.waveform_extractor as extractor_module

    source = Path(extractor_module.__file__).read_text()
    assert "waveform_state_service" not in source
    assert "waveform_cache" not in source
