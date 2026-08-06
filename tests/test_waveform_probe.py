"""W2 tests for the bounded ffprobe wrapper. No real music file is probed."""
from __future__ import annotations

import json

import pytest

from backend.app.core.waveform_limits import MAX_DURATION_SECONDS
from backend.app.core.waveform_process import ProcessSupervisor
from backend.app.models.waveform_extraction import WaveformExtractionError, WaveformExtractionErrorCode
from backend.app.services.waveform_probe import (
    build_probe_argv,
    probe_source,
    resolve_executable,
    validate_probe_payload,
    verify_extractor_versions,
)
from tests.conftest import async_test


def _audio_stream(**overrides) -> dict:
    stream = {
        "codec_type": "audio",
        "codec_name": "mp3",
        "channels": 2,
        "sample_rate": "44100",
        "duration": "247.381",
    }
    stream.update(overrides)
    return stream


def _payload(streams: list[dict] | None = None, fmt: dict | None = None) -> dict:
    return {"streams": streams if streams is not None else [_audio_stream()], "format": fmt or {}}


# ---------------------------------------------------------------------------
# validate_probe_payload — structural / range validation
# ---------------------------------------------------------------------------


def test_validate_probe_payload_accepts_a_valid_result():
    result = validate_probe_payload(_payload())
    assert result.duration_seconds == pytest.approx(247.381)
    assert result.source_channels == 2
    assert result.source_sample_rate_hz == 44100
    assert result.codec_name == "mp3"


def test_validate_probe_payload_rejects_non_dict():
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(["not", "a", "dict"])
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


def test_validate_probe_payload_rejects_missing_stream_list():
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload({"format": {}})
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


def test_validate_probe_payload_rejects_no_audio_stream():
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[{"codec_type": "video"}]))
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


def test_validate_probe_payload_rejects_missing_codec_name_as_unsupported():
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[_audio_stream(codec_name=None)]))
    assert exc.value.code is WaveformExtractionErrorCode.UNSUPPORTED_CODEC


def test_validate_probe_payload_treats_missing_duration_as_unknown():
    result = validate_probe_payload(_payload(streams=[_audio_stream(duration=None)]))
    assert result.duration_seconds is None


def test_validate_probe_payload_falls_back_to_format_duration():
    result = validate_probe_payload(
        _payload(streams=[_audio_stream(duration="N/A")], fmt={"duration": "10.5"})
    )
    assert result.duration_seconds == pytest.approx(10.5)


@pytest.mark.parametrize("bad_duration", ["-5", "nan", "inf", "-inf"])
def test_validate_probe_payload_rejects_negative_nan_or_infinite_duration(bad_duration):
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[_audio_stream(duration=bad_duration)]))
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


def test_validate_probe_payload_rejects_duration_over_six_hours():
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[_audio_stream(duration=str(MAX_DURATION_SECONDS + 1))]))
    assert exc.value.code is WaveformExtractionErrorCode.SOURCE_POLICY_REJECTED


def test_validate_probe_payload_rejects_non_numeric_duration():
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[_audio_stream(duration="not-a-number")]))
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


@pytest.mark.parametrize("channels", [0, -1, None, "abc", 999])
def test_validate_probe_payload_rejects_invalid_channels(channels):
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[_audio_stream(channels=channels)]))
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


@pytest.mark.parametrize("sample_rate", [0, -1, None, "abc", 10_000_000])
def test_validate_probe_payload_rejects_invalid_sample_rate(sample_rate):
    with pytest.raises(WaveformExtractionError) as exc:
        validate_probe_payload(_payload(streams=[_audio_stream(sample_rate=sample_rate)]))
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


def test_build_probe_argv_has_no_shell_and_ends_with_the_single_input():
    argv = build_probe_argv("ffprobe", "/tmp/some input (weird) 'name'.mp3")
    assert argv[0] == "ffprobe"
    assert argv[-1] == "/tmp/some input (weird) 'name'.mp3"
    assert "-i" not in argv  # ffprobe reads the trailing positional input


# ---------------------------------------------------------------------------
# resolve_executable
# ---------------------------------------------------------------------------


def test_resolve_executable_rejects_binary_inside_library_or_cache(monkeypatch, tmp_path):
    library = tmp_path / "library"
    cache = tmp_path / "cache"
    fake_bin = library / "ffprobe"
    library.mkdir()
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr("backend.app.services.waveform_probe.shutil.which", lambda name: str(fake_bin))
    resolved = resolve_executable("ffprobe", "FFPROBE_BIN", library_root=library, cache_root=cache)
    assert resolved is None


def test_resolve_executable_accepts_a_normal_binary(monkeypatch, tmp_path):
    real_bin = tmp_path / "usr" / "bin" / "ffprobe"
    real_bin.parent.mkdir(parents=True)
    real_bin.write_text("#!/bin/sh\n")
    real_bin.chmod(0o755)
    monkeypatch.setattr("backend.app.services.waveform_probe.shutil.which", lambda name: str(real_bin))
    resolved = resolve_executable("ffprobe", "FFPROBE_BIN", library_root=tmp_path / "library", cache_root=tmp_path / "cache")
    assert resolved == str(real_bin.resolve())


def test_resolve_executable_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr("backend.app.services.waveform_probe.shutil.which", lambda name: None)
    assert resolve_executable("ffprobe", "FFPROBE_BIN") is None


# ---------------------------------------------------------------------------
# probe_source — mocked subprocess execution end to end
# ---------------------------------------------------------------------------


class _FakeSupervisorResult:
    def __init__(self, stdout: bytes, outcome):
        self._stdout = stdout
        self._outcome = outcome

    async def run_capped(self, *args, **kwargs):
        return self._stdout, self._outcome


def _outcome(**overrides):
    from backend.app.core.waveform_process import ProcessOutcome

    return ProcessOutcome(**{"exit_code": 0, "stderr_tail": b"", "timed_out": False, "cancelled": False, "launch_error": None, **overrides})


@async_test
async def test_probe_source_returns_validated_result():
    stdout = json.dumps(_payload()).encode()
    supervisor = _FakeSupervisorResult(stdout, _outcome())
    result = await probe_source("fixture.mp3", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert result.source_channels == 2


@async_test
async def test_probe_source_maps_launch_failure():
    supervisor = _FakeSupervisorResult(b"", _outcome(exit_code=None, launch_error="not found"))
    with pytest.raises(WaveformExtractionError) as exc:
        await probe_source("fixture.mp3", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.PROCESS_LAUNCH_FAILURE


@async_test
async def test_probe_source_maps_cancellation():
    supervisor = _FakeSupervisorResult(b"", _outcome(cancelled=True))
    with pytest.raises(WaveformExtractionError) as exc:
        await probe_source("fixture.mp3", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.CANCELLED


@async_test
async def test_probe_source_maps_timeout():
    supervisor = _FakeSupervisorResult(b"", _outcome(timed_out=True))
    with pytest.raises(WaveformExtractionError) as exc:
        await probe_source("fixture.mp3", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.TIMEOUT


@async_test
async def test_probe_source_maps_nonzero_exit_to_probe_failure():
    supervisor = _FakeSupervisorResult(b"", _outcome(exit_code=1, stderr_tail=b"Invalid data found"))
    with pytest.raises(WaveformExtractionError) as exc:
        await probe_source("fixture.mp3", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.PROBE_FAILURE


@async_test
async def test_probe_source_maps_malformed_json():
    supervisor = _FakeSupervisorResult(b"{not-json", _outcome())
    with pytest.raises(WaveformExtractionError) as exc:
        await probe_source("fixture.mp3", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert exc.value.code is WaveformExtractionErrorCode.INVALID_PROBE


# ---------------------------------------------------------------------------
# verify_extractor_versions — bounded, non-audio commands only
# ---------------------------------------------------------------------------


class _RecordingSupervisor:
    def __init__(self, responses: dict[str, tuple[bytes, object]]):
        self._responses = responses
        self.calls: list[list[str]] = []

    async def run_capped(self, argv, **kwargs):
        self.calls.append(argv)
        return self._responses[argv[0]]


@async_test
async def test_verify_extractor_versions_uses_only_version_flag_no_audio_path():
    responses = {
        "ffmpeg": (b"ffmpeg version 6.0 Copyright (c) FFmpeg\nmore\n", _outcome()),
        "ffprobe": (b"ffprobe version 6.0\n", _outcome()),
    }
    supervisor = _RecordingSupervisor(responses)
    result = await verify_extractor_versions(ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert result["ffmpeg_verified"] is True
    assert result["ffprobe_verified"] is True
    assert result["ffmpeg_version"] == "ffmpeg version 6.0 Copyright (c) FFmpeg"
    for call in supervisor.calls:
        assert call[-1] == "-version"
        assert len(call) == 2  # binary + "-version" only, never a media path


@async_test
async def test_verify_extractor_versions_handles_one_tool_failing():
    responses = {
        "ffmpeg": (b"ffmpeg version 6.0\n", _outcome()),
        "ffprobe": (b"", _outcome(exit_code=1)),
    }
    supervisor = _RecordingSupervisor(responses)
    result = await verify_extractor_versions(ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert result["ffmpeg_verified"] is True
    assert result["ffprobe_verified"] is False


@async_test
async def test_verify_extractor_versions_handles_malformed_output():
    responses = {
        "ffmpeg": (b"", _outcome()),
        "ffprobe": (b"\x00\x01garbage", _outcome()),
    }
    supervisor = _RecordingSupervisor(responses)
    result = await verify_extractor_versions(ffmpeg_bin="ffmpeg", ffprobe_bin="ffprobe", supervisor=supervisor)
    assert result["ffmpeg_verified"] is False  # empty output is not a version
    assert result["ffprobe_verified"] is True  # garbage still decodes to a non-empty first line
