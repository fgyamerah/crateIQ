"""Bounded, read-only ffprobe wrapper for the W2 extraction engine.

Only stream/format fields required for extraction policy are read: duration,
channel count, sample rate, and codec name. No tags or unrelated metadata are
ingested. ffprobe is never pointed at the user's music library during tests —
callers must supply an already-validated source path.
"""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

from ..core.waveform_limits import (
    FFPROBE_STDOUT_CAP_BYTES,
    MAX_CHANNELS,
    MAX_DURATION_SECONDS,
    MAX_SAMPLE_RATE_HZ,
    MIN_CHANNELS,
    MIN_SAMPLE_RATE_HZ,
    PROBE_TIMEOUT_SECONDS,
    VERSION_CHECK_TIMEOUT_SECONDS,
)
from ..core.waveform_process import CancellationLike, ProcessSupervisor
from ..models.waveform_extraction import ProbeResult, WaveformExtractionError, WaveformExtractionErrorCode

_SAFE_SUBPROCESS_ENV = {
    "LC_ALL": "C",
    "AV_LOG_FORCE_NOCOLOR": "1",
    "PATH": os.environ.get("PATH", ""),
}


def _path_overlaps(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_executable(
    name: str,
    env_var: str,
    *,
    library_root: Path | None = None,
    cache_root: Path | None = None,
) -> str | None:
    """Resolve one absolute, executable, non-library/non-cache binary path."""
    override = os.environ.get(env_var, "").strip()
    resolved_text = shutil.which(override or name)
    if not resolved_text:
        return None
    resolved = Path(resolved_text).expanduser().resolve(strict=False)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    for root in (library_root, cache_root):
        if root is not None and (resolved == root or _path_overlaps(resolved, root)):
            return None
    return str(resolved)


def _parse_duration(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.INVALID_PROBE, "duration is not numeric"
        ) from exc


def validate_probe_payload(payload: object) -> ProbeResult:
    """Validate raw ffprobe JSON into a narrow, trustworthy :class:`ProbeResult`."""
    if not isinstance(payload, dict):
        raise WaveformExtractionError(WaveformExtractionErrorCode.INVALID_PROBE, "probe payload is not a JSON object")

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise WaveformExtractionError(WaveformExtractionErrorCode.INVALID_PROBE, "probe payload has no stream list")

    audio_streams = [s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"]
    if not audio_streams:
        raise WaveformExtractionError(WaveformExtractionErrorCode.INVALID_PROBE, "probe found no audio stream")
    stream = audio_streams[0]

    codec_name = stream.get("codec_name")
    if not codec_name or not isinstance(codec_name, str):
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.UNSUPPORTED_CODEC, "audio stream has no identifiable codec"
        )

    duration_raw = stream.get("duration")
    if duration_raw in (None, "N/A"):
        fmt = payload.get("format")
        duration_raw = fmt.get("duration") if isinstance(fmt, dict) else None
    duration_seconds = _parse_duration(duration_raw)
    if duration_seconds is not None:
        if not math.isfinite(duration_seconds) or duration_seconds < 0:
            raise WaveformExtractionError(
                WaveformExtractionErrorCode.INVALID_PROBE, "duration is negative, NaN, or infinite"
            )
        if duration_seconds > MAX_DURATION_SECONDS:
            raise WaveformExtractionError(
                WaveformExtractionErrorCode.SOURCE_POLICY_REJECTED, "duration exceeds the maximum policy duration"
            )

    try:
        channels = int(stream.get("channels"))
    except (TypeError, ValueError) as exc:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.INVALID_PROBE, "channel count is missing or non-numeric"
        ) from exc
    if not (MIN_CHANNELS <= channels <= MAX_CHANNELS):
        raise WaveformExtractionError(WaveformExtractionErrorCode.INVALID_PROBE, "channel count is out of range")

    try:
        sample_rate = int(stream.get("sample_rate"))
    except (TypeError, ValueError) as exc:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.INVALID_PROBE, "sample rate is missing or non-numeric"
        ) from exc
    if not (MIN_SAMPLE_RATE_HZ <= sample_rate <= MAX_SAMPLE_RATE_HZ):
        raise WaveformExtractionError(WaveformExtractionErrorCode.INVALID_PROBE, "sample rate is out of range")

    return ProbeResult(
        duration_seconds=duration_seconds,
        source_channels=channels,
        source_sample_rate_hz=sample_rate,
        codec_name=codec_name,
    )


def build_probe_argv(ffprobe_bin: str, source_path: str) -> list[str]:
    return [
        ffprobe_bin,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-select_streams", "a:0",
        source_path,
    ]


async def probe_source(
    source_path: str,
    *,
    ffprobe_bin: str,
    supervisor: ProcessSupervisor,
    cancellation: CancellationLike | None = None,
    timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Run a bounded, read-only ffprobe pass and return a validated result.

    ``source_path`` must already be validated by the caller (typically the
    resolved path from a :class:`ValidatedTrackSource`); this function does
    not perform library-root or existence checks itself.
    """
    argv = build_probe_argv(ffprobe_bin, source_path)
    stdout, outcome = await supervisor.run_capped(
        argv,
        env=_SAFE_SUBPROCESS_ENV,
        timeout_seconds=timeout_seconds,
        max_stdout_bytes=FFPROBE_STDOUT_CAP_BYTES,
        cancellation=cancellation,
    )
    if outcome.launch_error:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.PROCESS_LAUNCH_FAILURE, "ffprobe could not be launched", detail=outcome.launch_error
        )
    if outcome.cancelled:
        raise WaveformExtractionError(WaveformExtractionErrorCode.CANCELLED, "probe was cancelled")
    if outcome.timed_out:
        raise WaveformExtractionError(WaveformExtractionErrorCode.TIMEOUT, "probe timed out")
    if outcome.exit_code != 0:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.PROBE_FAILURE,
            "ffprobe exited with a non-zero status",
            detail=outcome.stderr_tail.decode("utf-8", errors="replace")[-500:],
        )
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WaveformExtractionError(WaveformExtractionErrorCode.INVALID_PROBE, "ffprobe output was not valid JSON") from exc
    return validate_probe_payload(payload)


async def verify_extractor_versions(
    *,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    supervisor: ProcessSupervisor,
    timeout_seconds: float = VERSION_CHECK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Bounded, non-audio ``-version`` check. Not wired into readiness in W2.

    Deferred deliberately: W1's readiness contract is already tested end to
    end, and upgrading ``detected`` -> ``ready`` is a W3-scope decision that
    should be made alongside the API/job wiring that actually depends on it.
    This primitive exists so that future wiring does not need to invent the
    safe invocation shape from scratch.
    """

    async def _version(binary: str) -> str | None:
        stdout, outcome = await supervisor.run_capped(
            [binary, "-version"],
            env=_SAFE_SUBPROCESS_ENV,
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=4096,
        )
        if outcome.launch_error or outcome.timed_out or outcome.cancelled or outcome.exit_code != 0:
            return None
        lines = stdout.decode("utf-8", errors="replace").splitlines()
        return lines[0].strip() if lines and lines[0].strip() else None

    ffmpeg_version = await _version(ffmpeg_bin)
    ffprobe_version = await _version(ffprobe_bin)
    return {
        "ffmpeg_verified": ffmpeg_version is not None,
        "ffprobe_verified": ffprobe_version is not None,
        "ffmpeg_version": ffmpeg_version,
        "ffprobe_version": ffprobe_version,
    }
