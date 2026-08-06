"""W2 tests for deterministic PCM framing and bounded peak accumulation.

No audio tool, subprocess, or real media file is used anywhere in this file.
"""
from __future__ import annotations

import array
import struct

import pytest

from backend.app.services.waveform_peaks import (
    INT16_MAX,
    INT16_MIN,
    PcmFrameParser,
    PeakAccumulator,
    build_resolutions,
    downsample_preserving_extrema,
    interleave_peaks,
)


def _s16le(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


# ---------------------------------------------------------------------------
# PcmFrameParser — arbitrary chunk boundaries
# ---------------------------------------------------------------------------


def test_parser_handles_complete_samples_in_one_chunk():
    parser = PcmFrameParser()
    samples = parser.feed(_s16le(1, 2, 3))
    assert list(samples) == [1, 2, 3]
    assert parser.finalize() == b""


def test_parser_handles_one_sample_split_across_two_chunks():
    parser = PcmFrameParser()
    raw = _s16le(-500)
    first = parser.feed(raw[:1])
    assert list(first) == []
    second = parser.feed(raw[1:])
    assert list(second) == [-500]
    assert parser.finalize() == b""


def test_parser_handles_arbitrary_odd_sized_chunks():
    parser = PcmFrameParser()
    raw = _s16le(10, -10, 20, -20, 30)
    out: list[int] = []
    for i in range(0, len(raw), 3):  # 3-byte chunks, never aligned to 2
        out.extend(parser.feed(raw[i : i + 3]))
    out.extend(parser.feed(b""))
    assert out == [10, -10, 20, -20, 30]
    assert parser.finalize() == b""


def test_parser_never_fabricates_a_sample_from_a_trailing_incomplete_byte():
    parser = PcmFrameParser()
    raw = _s16le(1, 2) + bytes([0xAB])
    samples = parser.feed(raw)
    assert list(samples) == [1, 2]
    leftover = parser.finalize()
    assert leftover == bytes([0xAB])


def test_parser_multiple_samples_per_chunk():
    parser = PcmFrameParser()
    samples = parser.feed(_s16le(*range(-5, 5)))
    assert list(samples) == list(range(-5, 5))


# ---------------------------------------------------------------------------
# PeakAccumulator — correctness
# ---------------------------------------------------------------------------


def test_accumulator_empty_input():
    acc = PeakAccumulator(capacity=256)
    assert acc.pair_count == 0
    assert acc.sample_count == 0
    assert acc.snapshot() == ([], [])


def test_accumulator_one_sample():
    acc = PeakAccumulator(capacity=256)
    acc.add_sample(42)
    assert acc.pair_count == 1
    assert acc.snapshot() == ([42], [42])


def test_accumulator_two_samples_within_capacity():
    acc = PeakAccumulator(capacity=256)
    acc.add_sample(5)
    acc.add_sample(-5)
    assert acc.pair_count == 2
    assert acc.snapshot() == ([5, -5], [5, -5])


def test_accumulator_silence():
    acc = PeakAccumulator(capacity=8)
    acc.add_samples([0] * 8)
    mins, maxs = acc.snapshot()
    assert mins == [0] * 8
    assert maxs == [0] * 8


def test_accumulator_positive_only():
    acc = PeakAccumulator(capacity=4)
    acc.add_samples([1, 2, 3, 4])
    mins, maxs = acc.snapshot()
    assert all(v >= 0 for v in mins)
    assert all(v >= 0 for v in maxs)


def test_accumulator_negative_only():
    acc = PeakAccumulator(capacity=4)
    acc.add_samples([-1, -2, -3, -4])
    mins, maxs = acc.snapshot()
    assert all(v <= 0 for v in mins)
    assert all(v <= 0 for v in maxs)


def test_accumulator_alternating_signs_in_one_bin():
    acc = PeakAccumulator(capacity=1)
    acc.add_samples([10, -10, 20, -20])
    mins, maxs = acc.snapshot()
    assert mins == [-20]
    assert maxs == [20]


def test_accumulator_preserves_int16_extrema():
    acc = PeakAccumulator(capacity=1)
    acc.add_samples([0, INT16_MIN, 0, INT16_MAX, 0])
    mins, maxs = acc.snapshot()
    assert mins == [INT16_MIN]
    assert maxs == [INT16_MAX]


def test_accumulator_rejects_out_of_range_sample():
    acc = PeakAccumulator(capacity=4)
    with pytest.raises(ValueError):
        acc.add_sample(INT16_MAX + 1)
    with pytest.raises(ValueError):
        acc.add_sample(INT16_MIN - 1)


def test_accumulator_repeated_identical_extrema():
    acc = PeakAccumulator(capacity=2)
    acc.add_samples([INT16_MAX, INT16_MAX, INT16_MAX, INT16_MAX])
    mins, maxs = acc.snapshot()
    assert mins == [INT16_MAX, INT16_MAX]
    assert maxs == [INT16_MAX, INT16_MAX]


def test_accumulator_short_stream_does_not_fabricate_bins():
    acc = PeakAccumulator(capacity=1024)
    acc.add_samples([1, 2, 3])
    assert acc.pair_count == 3  # not padded to capacity
    assert acc.snapshot() == ([1, 2, 3], [1, 2, 3])


def test_accumulator_deterministic_repeat_output():
    values = [1, -2, 3, -4, 5, -6, 7, -8, 9, -10]
    acc1 = PeakAccumulator(capacity=4)
    acc1.add_samples(values)
    acc2 = PeakAccumulator(capacity=4)
    acc2.add_samples(values)
    assert acc1.snapshot() == acc2.snapshot()


def test_accumulator_narrow_transient_survives_doubling_merge():
    """A single full-scale spike must not be erased once bins start merging."""
    capacity = 4
    acc = PeakAccumulator(capacity=capacity)
    values = [0] * 20
    values[13] = INT16_MAX  # narrow transient well after capacity is exceeded
    acc.add_samples(values)
    mins, maxs = acc.snapshot()
    assert max(maxs) == INT16_MAX


def test_accumulator_detail_cap_is_never_exceeded():
    acc = PeakAccumulator(capacity=32768)
    acc.add_samples([i % 7 - 3 for i in range(200_000)])
    assert acc.pair_count <= 32768


# ---------------------------------------------------------------------------
# Bounded memory / long-stream simulation (completion-critical)
# ---------------------------------------------------------------------------


def test_accumulator_bounded_memory_for_simulated_long_stream():
    """A long simulated stream must never grow the accumulator past capacity.

    This proves peak storage is O(capacity), not O(duration): the internal
    arrays are preallocated to ``capacity`` and never reassigned to a larger
    size, regardless of how many samples are fed in.
    """
    capacity = 2048
    acc = PeakAccumulator(capacity=capacity)

    def synthetic_long_stream(total_samples: int):
        # Deterministic pseudo-audio without ever materializing a huge buffer.
        for i in range(total_samples):
            yield ((i * 2654435761) % 65536) - 32768

    total_simulated_samples = 500_000  # stands in for hours of 8kHz audio
    for value in synthetic_long_stream(total_simulated_samples):
        acc.add_sample(value)

    assert acc.sample_count == total_simulated_samples
    assert acc.pair_count <= capacity
    assert len(acc._mins) == capacity  # preallocated once, never grown
    assert len(acc._maxs) == capacity


def test_accumulator_add_samples_from_pcm_parser_chunks_matches_direct_feed():
    """Feeding via PcmFrameParser chunks must equal feeding samples directly."""
    values = list(range(-100, 100))
    raw = _s16le(*values)

    direct = PeakAccumulator(capacity=32)
    direct.add_samples(values)

    parser = PcmFrameParser()
    chunked = PeakAccumulator(capacity=32)
    for i in range(0, len(raw), 7):  # deliberately misaligned chunk size
        chunked.add_samples(parser.feed(raw[i : i + 7]))
    parser.finalize()

    assert direct.snapshot() == chunked.snapshot()


# ---------------------------------------------------------------------------
# Extrema-preserving downsampling
# ---------------------------------------------------------------------------


def test_downsample_returns_source_unchanged_when_target_is_not_smaller():
    mins, maxs = [1, 2, 3], [1, 2, 3]
    out_mins, out_maxs = downsample_preserving_extrema(mins, maxs, target_pairs=10)
    assert (out_mins, out_maxs) == (mins, maxs)


def test_downsample_preserves_narrow_maximum_among_quiet_samples():
    n = 1000
    mins = [0] * n
    maxs = [0] * n
    maxs[503] = INT16_MAX  # narrow spike deep inside one future bucket
    out_mins, out_maxs = downsample_preserving_extrema(mins, maxs, target_pairs=10)
    assert max(out_maxs) == INT16_MAX


def test_downsample_preserves_narrow_minimum_among_quiet_samples():
    n = 1000
    mins = [0] * n
    maxs = [0] * n
    mins[17] = INT16_MIN
    out_mins, out_maxs = downsample_preserving_extrema(mins, maxs, target_pairs=20)
    assert min(out_mins) == INT16_MIN


def test_downsample_preserves_both_extrema_in_one_bucket():
    n = 100
    mins = [0] * n
    maxs = [0] * n
    mins[40] = INT16_MIN
    maxs[41] = INT16_MAX
    out_mins, out_maxs = downsample_preserving_extrema(mins, maxs, target_pairs=5)
    # bucket index 2 covers roughly [40, 60) for 5 buckets over 100 items
    assert INT16_MIN in out_mins
    assert INT16_MAX in out_maxs


def test_downsample_handles_uneven_bucket_boundaries():
    n = 4948  # architecture example detail pair count — deliberately not a multiple of 256/1024
    mins = [i % 11 - 5 for i in range(n)]
    maxs = [i % 13 - 2 for i in range(n)]
    player_mins, player_maxs = downsample_preserving_extrema(mins, maxs, target_pairs=1024)
    compact_mins, compact_maxs = downsample_preserving_extrema(mins, maxs, target_pairs=256)
    assert len(player_mins) == len(player_maxs) == 1024
    assert len(compact_mins) == len(compact_maxs) == 256
    assert min(compact_mins) >= min(mins)
    assert max(compact_maxs) <= max(maxs)


def test_downsample_empty_input():
    assert downsample_preserving_extrema([], [], target_pairs=256) == ([], [])


# ---------------------------------------------------------------------------
# Resolution building (compact/player/detail) and interleaving
# ---------------------------------------------------------------------------


def test_interleave_peaks_matches_canonical_order():
    assert interleave_peaks([1, 2], [10, 20]) == [1, 10, 2, 20]


def test_build_resolutions_produces_all_three_levels_bounded_and_extrema_preserving():
    capacity = 4096
    acc = PeakAccumulator(capacity=capacity)
    values = [((i * 97) % 65536) - 32768 for i in range(50_000)]
    values[12345] = INT16_MAX
    acc.add_samples(values)

    resolutions = build_resolutions(acc, compact_pairs=256, player_pairs=1024)
    assert set(resolutions) == {"compact", "player", "detail"}
    for name, peaks in resolutions.items():
        assert len(peaks) % 2 == 0
        pair_count = len(peaks) // 2
        if name == "detail":
            assert pair_count <= capacity
        elif name == "player":
            assert pair_count <= 1024
        elif name == "compact":
            assert pair_count <= 256
        # every value stays within signed 16-bit bounds
        assert all(INT16_MIN <= v <= INT16_MAX for v in peaks)
    # the full-scale transient must survive into every resolution
    assert max(resolutions["detail"]) == INT16_MAX
    assert max(resolutions["player"]) == INT16_MAX
    assert max(resolutions["compact"]) == INT16_MAX


def test_build_resolutions_short_stream_does_not_pad_compact_or_player():
    acc = PeakAccumulator(capacity=1024)
    acc.add_samples([1, -1, 2])
    resolutions = build_resolutions(acc, compact_pairs=256, player_pairs=1024)
    assert len(resolutions["detail"]) == 6  # 3 pairs
    assert len(resolutions["player"]) == 6
    assert len(resolutions["compact"]) == 6
