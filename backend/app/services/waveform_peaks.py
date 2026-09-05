"""Deterministic PCM framing and bounded min/max peak accumulation for W2.

Nothing here touches a filesystem, network, or subprocess. Everything is a
pure, synchronous transform over in-memory byte/sample data so it can be
tested exhaustively without any audio tool.
"""
from __future__ import annotations

import array
import sys

INT16_MIN = -32768
INT16_MAX = 32767


class PcmFrameParser:
    """Frames arbitrary stdout chunk boundaries into complete s16le samples.

    Handles complete samples, multiple samples per chunk, one sample split
    across two chunks, and arbitrary odd-sized chunks by carrying at most one
    leftover byte between calls. A trailing incomplete byte at end-of-stream
    is never fabricated into a sample; call :meth:`finalize` to retrieve it
    for diagnostics.
    """

    def __init__(self) -> None:
        self._carry = b""

    def feed(self, chunk: bytes) -> array.array:
        data = self._carry + chunk if self._carry else chunk
        usable = len(data) - (len(data) % 2)
        self._carry = data[usable:]
        samples = array.array("h")
        samples.frombytes(data[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
        return samples

    def finalize(self) -> bytes:
        """Return (and clear) any undecoded trailing byte."""
        leftover = self._carry
        self._carry = b""
        return leftover


class PeakAccumulator:
    """Bounded streaming min/max peak accumulator.

    One doubling-merge strategy serves both known- and unknown-duration
    input: bins start at width 1 sample and double — merging adjacent pairs
    with extrema preserved, never averaged — whenever the next sample would
    exceed the configured capacity. This bounds memory to O(capacity)
    regardless of total decoded duration, requires no upfront sample count,
    and leaves short streams with exactly as many bins as they have samples
    (no fabricated padding).

    Complexity: amortized O(n) for n accumulated samples. Each merge pass
    costs O(capacity) and merges occur O(log2(n / capacity)) times, so total
    work is O(n + capacity * log2(n / capacity)) — linear in the number of
    decoded samples for any capacity that is small relative to n.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.capacity = capacity
        self._mins: list[int] = [0] * capacity
        self._maxs: list[int] = [0] * capacity
        self._filled = 0
        self._bin_width = 1
        self._sample_index = 0

    @property
    def pair_count(self) -> int:
        return self._filled

    @property
    def sample_count(self) -> int:
        return self._sample_index

    def add_sample(self, value: int) -> None:
        if value < INT16_MIN or value > INT16_MAX:
            raise ValueError("sample out of signed 16-bit range")
        idx = self._sample_index // self._bin_width
        if idx >= self.capacity:
            self._merge_double()
            idx = self._sample_index // self._bin_width
        if idx >= self._filled:
            self._mins[idx] = value
            self._maxs[idx] = value
            self._filled = idx + 1
        else:
            if value < self._mins[idx]:
                self._mins[idx] = value
            if value > self._maxs[idx]:
                self._maxs[idx] = value
        self._sample_index += 1

    def add_samples(self, values) -> None:
        for value in values:
            self.add_sample(value)

    def _merge_double(self) -> None:
        new_filled = (self._filled + 1) // 2
        for i in range(new_filled):
            lo_idx, hi_idx = 2 * i, 2 * i + 1
            lo = self._mins[lo_idx]
            hi = self._maxs[lo_idx]
            if hi_idx < self._filled:
                if self._mins[hi_idx] < lo:
                    lo = self._mins[hi_idx]
                if self._maxs[hi_idx] > hi:
                    hi = self._maxs[hi_idx]
            self._mins[i] = lo
            self._maxs[i] = hi
        self._filled = new_filled
        self._bin_width *= 2

    def snapshot(self) -> tuple[list[int], list[int]]:
        """Return (mins, maxs) copies sized to the current pair count only."""
        return list(self._mins[: self._filled]), list(self._maxs[: self._filled])


def downsample_preserving_extrema(mins: list[int], maxs: list[int], target_pairs: int) -> tuple[list[int], list[int]]:
    """Reduce (mins, maxs) to at most ``target_pairs`` buckets.

    Each output bucket is the minimum of contributing minima and the maximum
    of contributing maxima — never an average — so a narrow transient cannot
    be erased by quiet neighbors. Never fabricates buckets: if the source is
    already at or below ``target_pairs``, it is returned unchanged.
    """
    n = len(mins)
    if n == 0 or target_pairs <= 0:
        return [], []
    if target_pairs >= n:
        return list(mins), list(maxs)
    out_mins = [0] * target_pairs
    out_maxs = [0] * target_pairs
    for i in range(target_pairs):
        start = (i * n) // target_pairs
        end = ((i + 1) * n) // target_pairs
        if end <= start:
            end = start + 1
        end = min(end, n)
        out_mins[i] = min(mins[start:end])
        out_maxs[i] = max(maxs[start:end])
    return out_mins, out_maxs


def interleave_peaks(mins: list[int], maxs: list[int]) -> list[int]:
    """``[min0, max0, min1, max1, ...]`` per the canonical waveform encoding."""
    out: list[int] = []
    for lo, hi in zip(mins, maxs):
        out.append(lo)
        out.append(hi)
    return out


def build_resolutions(
    accumulator: PeakAccumulator,
    *,
    compact_pairs: int,
    player_pairs: int,
) -> dict[str, list[int]]:
    """Derive compact/player/detail interleaved peak arrays from one accumulator."""
    detail_mins, detail_maxs = accumulator.snapshot()
    player_mins, player_maxs = downsample_preserving_extrema(detail_mins, detail_maxs, player_pairs)
    compact_mins, compact_maxs = downsample_preserving_extrema(detail_mins, detail_maxs, compact_pairs)
    return {
        "detail": interleave_peaks(detail_mins, detail_maxs),
        "player": interleave_peaks(player_mins, player_maxs),
        "compact": interleave_peaks(compact_mins, compact_maxs),
    }


# ---------------------------------------------------------------------------
# Spectral color weighting (single mirrored waveform)
#
# These band-energy fractions exist ONLY to tint each slice of the one
# mirrored waveform. They are never rendered as three separate Low/Mid/High
# rows, and they never change the peak (height) data.
# ---------------------------------------------------------------------------

# Colour analysis is capped so a pathological bucket count cannot make
# extraction unreasonably slow; the visual only ever needs player resolution.
MAX_COLOR_ANALYSIS_BUCKETS = 4096


def compute_band_color_weights(
    samples,
    sample_rate: int,
    bucket_count: int,
    *,
    low_edge: int = 250,
    high_edge: int = 4000,
) -> list[float]:
    """Return interleaved ``[low, mid, high]`` energy fractions per bucket.

    Each triplet sums to ~1.0 (silence resolves to an even 1/3 split). The
    values are scale-invariant ratios, so raw 16-bit samples are fine without
    amplitude normalization.
    """
    import numpy as np

    x = np.asarray(samples, dtype=np.float32)
    total = int(x.size)
    if total == 0 or bucket_count <= 0:
        return []
    bucket_count = max(1, min(bucket_count, total, MAX_COLOR_ANALYSIS_BUCKETS))

    nyquist = sample_rate / 2.0
    mid_upper = min(high_edge, nyquist)
    weights: list[float] = []

    for i in range(bucket_count):
        start = i * total // bucket_count
        end = (i + 1) * total // bucket_count
        if end <= start:
            end = start + 1
        seg = x[start:end]
        n = int(seg.size)
        window = np.hanning(n).astype(np.float32) if n > 1 else np.ones(1, dtype=np.float32)
        seg = seg * window
        fft_size = 1
        while fft_size < n:
            fft_size <<= 1
        power = np.abs(np.fft.rfft(seg, n=fft_size)) ** 2
        freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
        low = float(power[freqs < low_edge].sum())
        mid = float(power[(freqs >= low_edge) & (freqs < mid_upper)].sum())
        high = float(power[freqs >= mid_upper].sum())
        total_energy = low + mid + high
        if total_energy <= 1e-12:
            weights.extend((1 / 3, 1 / 3, 1 / 3))
        else:
            weights.extend((low / total_energy, mid / total_energy, high / total_energy))
    return weights


def resize_color_bands(color_bands: list[float], target_pairs: int) -> list[float]:
    """Resample interleaved band triplets to ``target_pairs`` buckets.

    Downsampling averages band fractions (never extrema); upsampling repeats
    the nearest source bucket. The result has ``target_pairs * 3`` values when
    both inputs are positive, else an empty list.
    """
    source_pairs = len(color_bands) // 3
    if source_pairs == 0 or target_pairs <= 0:
        return []
    if target_pairs >= source_pairs:
        out: list[float] = []
        for i in range(target_pairs):
            src = min(source_pairs - 1, i * source_pairs // target_pairs)
            out.extend(color_bands[src * 3 : src * 3 + 3])
        return out
    out = []
    for i in range(target_pairs):
        start = i * source_pairs // target_pairs
        end = (i + 1) * source_pairs // target_pairs
        if end <= start:
            end = start + 1
        end = min(end, source_pairs)
        lo = mid = hi = 0.0
        for j in range(start, end):
            lo += color_bands[j * 3]
            mid += color_bands[j * 3 + 1]
            hi += color_bands[j * 3 + 2]
        span = end - start
        out.extend((lo / span, mid / span, hi / span))
    return out
