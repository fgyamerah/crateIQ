"""Internal waveform extraction engine boundary (W2).

Ties together a validated source descriptor (W1's ``track_source_service``),
a bounded ffprobe policy check, a safe read-only FFmpeg decode process, and a
bounded min/max peak accumulator into one internal
:class:`WaveformExtractionResult`. This module is never imported by an API
route, job worker, or scheduler in this phase — it is a callable engine for a
future W3 to wire up.
"""
from __future__ import annotations

import os

from ..core.waveform_limits import (
    ANALYSIS_SAMPLE_RATE_HZ,
    COMPACT_PAIR_COUNT,
    DECODER_THREADS,
    MAX_SOURCE_SIZE_BYTES,
    PCM_CHANNELS,
    PLAYER_PAIR_COUNT,
    compute_detail_pair_target,
    compute_extraction_timeout_seconds,
)
from ..core.waveform_process import CancellationLike, ProcessSupervisor
from ..models.waveform import SourceStatSnapshot
from ..models.waveform_extraction import (
    ProbeResult,
    WaveformExtractionError,
    WaveformExtractionErrorCode,
    WaveformExtractionResult,
)
from .track_source_service import (
    TrackSourceNotFound,
    TrackSourcePathRejected,
    TrackSourceUnavailable,
    ValidatedTrackSource,
    source_stat_snapshot,
)
from .waveform_peaks import PcmFrameParser, PeakAccumulator, build_resolutions
from .waveform_probe import probe_source

_SAFE_SUBPROCESS_ENV = {
    "LC_ALL": "C",
    "AV_LOG_FORCE_NOCOLOR": "1",
    "PATH": os.environ.get("PATH", ""),
}


def build_decode_argv(ffmpeg_bin: str, source_path: str) -> list[str]:
    """Fixed, minimal read-only decode command: one input, PCM on stdout.

    No output-media path, no metadata mapping, no overwrite flag, no
    ReplayGain/normalization/tag option. ``source_path`` is passed only as an
    argument value, never interpolated into a shell string.
    """
    return [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-threads", str(DECODER_THREADS),
        "-i", source_path,
        "-map", "0:a:0",
        "-vn", "-sn", "-dn",
        "-ac", str(PCM_CHANNELS),
        "-ar", str(ANALYSIS_SAMPLE_RATE_HZ),
        "-f", "s16le",
        "pipe:1",
    ]


def _snapshots_match(a: SourceStatSnapshot, b: SourceStatSnapshot) -> bool:
    return (
        a.source_size_bytes == b.source_size_bytes
        and a.source_mtime_ns == b.source_mtime_ns
        and a.source_ctime_ns == b.source_ctime_ns
        and a.source_device == b.source_device
        and a.source_inode == b.source_inode
    )


async def extract_waveform(
    source: ValidatedTrackSource,
    pre_snapshot: SourceStatSnapshot,
    *,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    supervisor: ProcessSupervisor,
    cancellation: CancellationLike | None = None,
) -> WaveformExtractionResult:
    """Run the full validated-source -> probe -> decode -> peaks pipeline.

    Raises :class:`WaveformExtractionError` with a narrow internal code for
    every failure branch. Never writes a cache artifact, never enqueues a
    job, and the returned result carries no source/library/cache/executable
    path field.
    """
    if pre_snapshot.source_size_bytes > MAX_SOURCE_SIZE_BYTES:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.SOURCE_POLICY_REJECTED, "source exceeds the maximum policy size"
        )
    if cancellation is not None and cancellation.is_cancelled:
        raise WaveformExtractionError(WaveformExtractionErrorCode.CANCELLED, "extraction was cancelled before probing")

    source_path = str(source.path)
    probe: ProbeResult = await probe_source(
        source_path, ffprobe_bin=ffprobe_bin, supervisor=supervisor, cancellation=cancellation
    )

    if cancellation is not None and cancellation.is_cancelled:
        raise WaveformExtractionError(WaveformExtractionErrorCode.CANCELLED, "extraction was cancelled after probing")

    timeout_seconds = compute_extraction_timeout_seconds(probe.duration_seconds)
    detail_target = compute_detail_pair_target(probe.duration_seconds)

    argv = build_decode_argv(ffmpeg_bin, source_path)
    managed = await supervisor.run(
        argv,
        env=_SAFE_SUBPROCESS_ENV,
        timeout_seconds=timeout_seconds,
        cancellation=cancellation,
    )

    parser = PcmFrameParser()
    accumulator = PeakAccumulator(detail_target)
    async for chunk in managed.stdout:
        accumulator.add_samples(parser.feed(chunk))
    parser.finalize()  # trailing partial byte, if any, is discarded, never fabricated

    outcome = managed.outcome
    if outcome.launch_error:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.PROCESS_LAUNCH_FAILURE,
            "decoder could not be launched",
            detail=outcome.launch_error,
        )
    if outcome.cancelled:
        raise WaveformExtractionError(WaveformExtractionErrorCode.CANCELLED, "extraction was cancelled during decode")
    if outcome.timed_out:
        raise WaveformExtractionError(WaveformExtractionErrorCode.TIMEOUT, "decode exceeded its bounded timeout")
    if outcome.exit_code != 0:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.DECODE_FAILURE,
            "decoder exited with a non-zero status",
            detail=outcome.stderr_tail.decode("utf-8", errors="replace")[-500:],
        )

    try:
        post_snapshot = source_stat_snapshot(source.track_id)
    except (TrackSourceNotFound, TrackSourcePathRejected, TrackSourceUnavailable) as exc:
        raise WaveformExtractionError(
            WaveformExtractionErrorCode.SOURCE_CHANGED, "source became unavailable during extraction"
        ) from exc
    if not _snapshots_match(pre_snapshot, post_snapshot):
        raise WaveformExtractionError(WaveformExtractionErrorCode.SOURCE_CHANGED, "source changed during extraction")

    resolutions = build_resolutions(accumulator, compact_pairs=COMPACT_PAIR_COUNT, player_pairs=PLAYER_PAIR_COUNT)
    if probe.duration_seconds is not None:
        duration_ms = round(probe.duration_seconds * 1000)
    else:
        duration_ms = round(accumulator.sample_count / ANALYSIS_SAMPLE_RATE_HZ * 1000)

    return WaveformExtractionResult(
        duration_ms=duration_ms,
        source_channels=probe.source_channels,
        source_sample_rate_hz=probe.source_sample_rate_hz,
        analysis_sample_rate_hz=ANALYSIS_SAMPLE_RATE_HZ,
        encoding="int16_min_max_interleaved",
        resolutions=resolutions,
    )
