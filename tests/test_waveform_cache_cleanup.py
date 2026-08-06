"""W6 tests for cache accounting, LRU eviction, and deletion containment.

Everything runs against temporary directories. No audio tool executes, no
real music library is touched, and the only files any test can delete are
fixtures it created inside a temporary cache root.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from backend.app.core import db as backend_db
from backend.app.core.waveform_cache import (
    ValidatedWaveformCacheRoot,
    WaveformCacheSafetyError,
    validate_waveform_cache_root,
)
from backend.app.models.waveform import WAVEFORM_ALGORITHM_VERSION, SourceStatSnapshot
from backend.app.services import waveform_cache_service as cache_service
from backend.app.services import waveform_identity, waveform_job_service, waveform_state_service

LIBRARY = "c" * 64


@pytest.fixture()
def jobs_db(tmp_path, monkeypatch):
    path = tmp_path / "operational" / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    return path


@pytest.fixture()
def cache(tmp_path):
    library = tmp_path / "music"
    library.mkdir()
    return validate_waveform_cache_root(tmp_path / "cache" / "waveforms", library)


def _key(seed: int) -> str:
    return f"{seed:064x}"


def _write_artifact(cache: ValidatedWaveformCacheRoot, key: str, size: int = 1024) -> Path:
    path = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / key[:2] / f"{key}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _write_temp(cache: ValidatedWaveformCacheRoot, name_hex: str, size: int = 512, age_seconds: float = 0) -> Path:
    path = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / "ab" / f".tmp.{name_hex}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"t" * size)
    if age_seconds:
        old = time.time() - age_seconds
        import os
        os.utime(path, (old, old))
    return path


def _snapshot(track_id: int, mtime: int = 1000) -> SourceStatSnapshot:
    return SourceStatSnapshot(
        library_id=LIBRARY, track_id=track_id, source_size_bytes=4096,
        source_mtime_ns=mtime, source_ctime_ns=mtime + 1,
        source_device=1, source_inode=track_id,
    )


def _make_ready(track_id: int, key: str, *, accessed_at: str | None = None) -> None:
    """Put a track into ready state pointing at `key`."""
    snapshot = _snapshot(track_id)
    waveform_state_service.transition_track_state(track_id, "queued", library_id=LIBRARY, snapshot=snapshot)
    waveform_state_service.transition_track_state(track_id, "processing", library_id=LIBRARY)
    waveform_state_service.transition_track_state(
        track_id, "ready", library_id=LIBRARY, snapshot=snapshot, cache_key=key
    )
    if accessed_at:
        with backend_db.get_conn() as conn:
            conn.execute(
                "UPDATE waveform_track_state SET last_accessed_at = ? WHERE library_id = ? AND track_id = ?",
                (accessed_at, LIBRARY, track_id),
            )


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def test_empty_cache_reports_zero(cache):
    usage = cache_service.cache_usage(cache)
    assert usage.total_bytes == 0
    assert usage.artifact_count == 0
    assert usage.temp_count == 0


def test_accounting_counts_artifacts_and_temps_separately(cache):
    _write_artifact(cache, _key(1), size=1000)
    _write_artifact(cache, _key(2), size=2000)
    _write_temp(cache, "a" * 32, size=500)
    usage = cache_service.cache_usage(cache)
    assert usage.artifact_count == 2
    assert usage.artifact_bytes == 3000
    assert usage.temp_count == 1
    assert usage.temp_bytes == 500
    assert usage.total_bytes == 3500


def test_accounting_ignores_unknown_files(cache):
    _write_artifact(cache, _key(1), size=1000)
    unknown = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / "ab" / "not-ours.txt"
    unknown.parent.mkdir(parents=True, exist_ok=True)
    unknown.write_bytes(b"y" * 9999)
    usage = cache_service.cache_usage(cache)
    assert usage.total_bytes == 1000, "unknown files must not be counted as cache usage"


def test_accounting_never_counts_source_audio(cache):
    (cache.library_root / "song.mp3").write_bytes(b"z" * 50_000)
    _write_artifact(cache, _key(1), size=100)
    assert cache_service.cache_usage(cache).total_bytes == 100


def test_accounting_ignores_unknown_top_level_directories(cache):
    stray = cache.root / "something-else" / "deep"
    stray.mkdir(parents=True)
    (stray / f"{_key(9)}.json.gz").write_bytes(b"q" * 4242)
    assert cache_service.cache_usage(cache).total_bytes == 0


def test_cache_status_is_privacy_safe(cache, tmp_path):
    _write_artifact(cache, _key(1), size=10)
    status = cache_service.cache_status(cache, max_cache_bytes=1000)
    rendered = repr(status)
    assert str(tmp_path) not in rendered
    assert str(cache.root) not in rendered
    assert status["current_cache_bytes"] == 10
    assert status["max_cache_bytes"] == 1000
    assert status["over_limit"] is False


# ---------------------------------------------------------------------------
# Size-driven cleanup
# ---------------------------------------------------------------------------


def test_below_limit_removes_nothing(cache, jobs_db):
    _write_artifact(cache, _key(1), size=100)
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=10_000)
    assert outcome.removed_total == 0
    assert outcome.over_limit is False
    assert cache_service.cache_usage(cache).total_bytes == 100


def test_exactly_at_limit_removes_nothing(cache, jobs_db):
    _write_artifact(cache, _key(1), size=1000)
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=1000)
    assert outcome.over_limit is False, "cleanup triggers only above the limit, not at it"
    assert outcome.removed_total == 0


def test_above_limit_prunes_toward_eighty_percent(cache, jobs_db):
    for i in range(1, 11):
        key = _key(i)
        _write_artifact(cache, key, size=100)
        _make_ready(i, key, accessed_at=f"2026-01-{i:02d}T00:00:00+00:00")
    assert cache_service.cache_usage(cache).total_bytes == 1000

    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=500)
    assert outcome.over_limit is True
    assert outcome.target_met is True
    # Target is 80% of 500 = 400 bytes.
    assert cache_service.cache_usage(cache).total_bytes <= 400
    assert outcome.removed_lru >= 6


def test_lru_evicts_oldest_access_first(cache, jobs_db):
    for i in range(1, 6):
        key = _key(i)
        _write_artifact(cache, key, size=100)
        # Track 1 is oldest, track 5 newest.
        _make_ready(i, key, accessed_at=f"2026-0{i}-01T00:00:00+00:00")

    cache_service.cleanup_cache(cache, max_cache_bytes=300)

    surviving = {e.generation_key for e in cache_service.scan_cache_entries(cache) if not e.is_temp}
    assert _key(5) in surviving, "most recently accessed artifact must survive"
    assert _key(1) not in surviving, "least recently accessed artifact must be evicted first"


def test_eviction_marks_track_state_stale(cache, jobs_db):
    key = _key(1)
    _write_artifact(cache, key, size=1000)
    _make_ready(1, key, accessed_at="2026-01-01T00:00:00+00:00")
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status.value == "ready"

    cache_service.cleanup_cache(cache, max_cache_bytes=100)

    state = waveform_state_service.get_track_state(1, library_id=LIBRARY)
    assert state.status.value == "stale", "jobs.db must not advertise an evicted artifact as ready"
    assert state.cache_key is None


def test_just_published_artifact_is_protected_from_immediate_eviction(cache, jobs_db):
    protected = _key(1)
    other = _key(2)
    _write_artifact(cache, protected, size=1000)
    _write_artifact(cache, other, size=1000)
    _make_ready(1, protected, accessed_at="2026-01-01T00:00:00+00:00")  # oldest
    _make_ready(2, other, accessed_at="2026-09-01T00:00:00+00:00")

    cache_service.cleanup_cache(cache, max_cache_bytes=500, protect_generation_key=protected)

    surviving = {e.generation_key for e in cache_service.scan_cache_entries(cache) if not e.is_temp}
    assert protected in surviving, "a publication must never immediately evict itself"


def test_orphans_are_removed_before_ready_artifacts(cache, jobs_db):
    referenced = _key(1)
    orphan = _key(2)
    _write_artifact(cache, referenced, size=1000)
    _write_artifact(cache, orphan, size=1000)
    _make_ready(1, referenced, accessed_at="2026-01-01T00:00:00+00:00")

    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=1500)

    surviving = {e.generation_key for e in cache_service.scan_cache_entries(cache) if not e.is_temp}
    assert outcome.removed_orphan == 1
    assert orphan not in surviving
    assert referenced in surviving, "an unreferenced orphan is cheaper to lose than a ready artifact"


def test_stale_state_artifact_removed_before_ready(cache, jobs_db):
    ready_key = _key(1)
    stale_key = _key(2)
    _write_artifact(cache, ready_key, size=1000)
    _write_artifact(cache, stale_key, size=1000)
    _make_ready(1, ready_key, accessed_at="2026-09-01T00:00:00+00:00")
    _make_ready(2, stale_key, accessed_at="2026-01-01T00:00:00+00:00")
    waveform_state_service.transition_track_state(2, "stale", library_id=LIBRARY)

    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=1500)

    surviving = {e.generation_key for e in cache_service.scan_cache_entries(cache) if not e.is_temp}
    assert outcome.removed_stale == 1
    assert stale_key not in surviving
    assert ready_key in surviving


def test_accounting_after_deletion_is_consistent(cache, jobs_db):
    for i in range(1, 6):
        key = _key(i)
        _write_artifact(cache, key, size=200)
        _make_ready(i, key, accessed_at=f"2026-0{i}-01T00:00:00+00:00")
    cache_service.cleanup_cache(cache, max_cache_bytes=500)
    recount = cache_service.cache_usage(cache)
    entries = cache_service.scan_cache_entries(cache)
    assert recount.total_bytes == sum(e.size_bytes for e in entries)


# ---------------------------------------------------------------------------
# Temp file handling
# ---------------------------------------------------------------------------


def test_old_temp_file_is_removed(cache, jobs_db):
    old = _write_temp(cache, "a" * 32, age_seconds=48 * 3600)
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=10_000)
    assert outcome.removed_temp == 1
    assert not old.exists()


def test_fresh_temp_file_is_retained(cache, jobs_db):
    fresh = _write_temp(cache, "b" * 32, age_seconds=0)
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=10_000)
    assert outcome.removed_temp == 0
    assert fresh.exists(), "a young temp file may belong to an in-flight publication"


def test_unknown_temp_like_file_is_not_removed(cache, jobs_db):
    weird = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / "ab" / ".tmp.notours.dat"
    weird.parent.mkdir(parents=True, exist_ok=True)
    weird.write_bytes(b"k")
    import os
    old = time.time() - 99999
    os.utime(weird, (old, old))
    cache_service.cleanup_cache(cache, max_cache_bytes=10_000)
    assert weird.exists(), "only CrateIQ's own temp naming may be swept"


# ---------------------------------------------------------------------------
# Deletion containment — completion-critical
# ---------------------------------------------------------------------------


def test_cleanup_never_deletes_source_music(cache, jobs_db):
    song = cache.library_root / "keep me.mp3"
    song.write_bytes(b"audio" * 1000)
    for i in range(1, 6):
        key = _key(i)
        _write_artifact(cache, key, size=1000)
        _make_ready(i, key, accessed_at=f"2026-0{i}-01T00:00:00+00:00")

    cache_service.cleanup_cache(cache, max_cache_bytes=100)

    assert song.is_file(), "source music must survive any cache cleanup"
    assert song.read_bytes() == b"audio" * 1000


def test_cleanup_never_removes_the_cache_root_or_its_parent(cache, jobs_db):
    _write_artifact(cache, _key(1), size=5000)
    _make_ready(1, _key(1), accessed_at="2026-01-01T00:00:00+00:00")
    cache_service.cleanup_cache(cache, max_cache_bytes=10)
    assert cache.root.is_dir()
    assert cache.root.parent.is_dir()
    assert cache.library_root.is_dir()


def test_symlinked_file_inside_cache_is_not_followed(cache, jobs_db, tmp_path):
    outside = tmp_path / "outside-treasure.bin"
    outside.write_bytes(b"do not delete me")
    link_dir = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / "ab"
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / f"{_key(7)}.json.gz"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    cache_service.cleanup_cache(cache, max_cache_bytes=0)

    assert outside.is_file(), "cleanup must never delete a symlink target outside the cache"
    assert outside.read_bytes() == b"do not delete me"


def test_symlinked_directory_inside_cache_is_not_traversed(cache, jobs_db, tmp_path):
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    victim = outside_dir / f"{_key(8)}.json.gz"
    victim.write_bytes(b"external artifact-shaped file")

    parent = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION
    parent.mkdir(parents=True, exist_ok=True)
    try:
        (parent / "ab").symlink_to(outside_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    usage = cache_service.cache_usage(cache)
    cache_service.cleanup_cache(cache, max_cache_bytes=0)

    assert usage.total_bytes == 0, "a symlinked directory must not be walked into"
    assert victim.is_file(), "files behind a symlinked directory must never be deleted"


def test_delete_helper_refuses_paths_outside_the_cache(cache, tmp_path):
    outside = tmp_path / "outside.json.gz"
    outside.write_bytes(b"safe")
    freed = cache_service._delete_contained(outside, cache)
    assert freed == 0
    assert outside.is_file()


def test_delete_helper_refuses_the_cache_root_itself(cache):
    cache.root.mkdir(parents=True, exist_ok=True)
    assert cache_service._delete_contained(cache.root, cache) == 0
    assert cache.root.is_dir(), "the cache root itself must never be deletable"


def test_delete_helper_refuses_traversal_paths(cache):
    traversal = cache.root / ".." / "escape.json.gz"
    assert cache_service._delete_contained(traversal, cache) == 0


def test_cache_root_overlapping_library_is_still_rejected(tmp_path):
    library = tmp_path / "music"
    library.mkdir()
    with pytest.raises(WaveformCacheSafetyError):
        validate_waveform_cache_root(library / "inner-cache", library)


# ---------------------------------------------------------------------------
# Ready-state reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_marks_ready_state_stale_when_artifact_missing(cache, jobs_db):
    key = _key(1)
    _make_ready(1, key)  # state says ready, but no file was ever written
    repaired = cache_service.reconcile_ready_states(cache)
    assert repaired == 1
    state = waveform_state_service.get_track_state(1, library_id=LIBRARY)
    assert state.status.value == "stale"
    assert state.last_error_code == waveform_job_service.ERROR_ARTIFACT_MISSING


def test_reconcile_leaves_valid_ready_states_alone(cache, jobs_db):
    key = _key(1)
    _write_artifact(cache, key)
    _make_ready(1, key)
    assert cache_service.reconcile_ready_states(cache) == 0
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status.value == "ready"


def test_reconcile_is_idempotent(cache, jobs_db):
    _make_ready(1, _key(1))
    assert cache_service.reconcile_ready_states(cache) == 1
    assert cache_service.reconcile_ready_states(cache) == 0
    assert cache_service.reconcile_ready_states(cache) == 0


def test_startup_reconcile_sweeps_temp_and_repairs_state(cache, jobs_db):
    stale_temp = _write_temp(cache, "c" * 32, age_seconds=72 * 3600)
    _make_ready(1, _key(1))
    repaired, usage = cache_service.startup_reconcile(cache, max_cache_bytes=10_000)
    assert repaired == 1
    assert not stale_temp.exists()
    assert usage.total_bytes == 0


def test_startup_reconcile_performs_no_extraction(cache, jobs_db, monkeypatch):
    """Startup maintenance must never invoke the extractor."""
    import asyncio as _asyncio

    def _forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("startup reconciliation must not spawn a process")

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _forbidden)
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    cache_service.startup_reconcile(cache, max_cache_bytes=10_000)


# ---------------------------------------------------------------------------
# LRU access metadata
# ---------------------------------------------------------------------------


def test_touch_access_records_a_timestamp(cache, jobs_db):
    _make_ready(1, _key(1))
    with backend_db.get_conn() as conn:
        conn.execute(
            "UPDATE waveform_track_state SET generated_at = ?, last_accessed_at = NULL "
            "WHERE library_id = ? AND track_id = ?",
            ("2020-01-01T00:00:00+00:00", LIBRARY, 1),
        )
    assert waveform_job_service.touch_artifact_access(LIBRARY, 1) is True
    refs = {r.track_id: r for r in waveform_job_service.list_cached_artifacts()}
    assert refs[1].last_used_at.startswith("20")


def test_touch_access_is_rate_limited(cache, jobs_db):
    _make_ready(1, _key(1))
    with backend_db.get_conn() as conn:
        conn.execute(
            "UPDATE waveform_track_state SET generated_at = ?, last_accessed_at = NULL "
            "WHERE library_id = ? AND track_id = ?",
            ("2020-01-01T00:00:00+00:00", LIBRARY, 1),
        )
    assert waveform_job_service.touch_artifact_access(LIBRARY, 1) is True
    # Immediately afterwards the timestamp is fresh, so no second write occurs.
    assert waveform_job_service.touch_artifact_access(LIBRARY, 1) is False


def test_touch_access_on_unknown_track_is_a_no_op(cache, jobs_db):
    assert waveform_job_service.touch_artifact_access(LIBRARY, 4242) is False


def test_artifacts_never_read_are_still_orderable(cache, jobs_db):
    """A never-accessed artifact falls back to generated_at, not 'infinitely new'."""
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    refs = waveform_job_service.list_cached_artifacts()
    assert len(refs) == 1
    assert refs[0].last_used_at, "ordering key must never be empty"


# ---------------------------------------------------------------------------
# Superseded schema/algorithm layouts
# ---------------------------------------------------------------------------


def _write_superseded(
    cache: ValidatedWaveformCacheRoot,
    key: str,
    *,
    size: int = 1024,
    age_seconds: float = 0,
    layout: str = "v1",
    algorithm: str = "mono-minmax-s16-v0",
) -> Path:
    """Write an artifact under a layout this build can never serve."""
    path = cache.root / layout / algorithm / key[:2] / f"{key}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"o" * size)
    if age_seconds:
        import os
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def test_aged_superseded_artifact_is_removed_without_storage_pressure(cache, jobs_db):
    old = _write_superseded(cache, _key(1), age_seconds=8 * 24 * 3600)
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert outcome.removed_superseded == 1
    assert not old.exists()
    assert outcome.over_limit is False, "a superseded sweep must not require size pressure"


def test_recent_superseded_artifact_is_retained(cache, jobs_db):
    recent = _write_superseded(cache, _key(2), age_seconds=24 * 3600)
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert outcome.removed_superseded == 0
    assert recent.exists(), "a version bump that gets reverted must not lose the cache"


def test_superseded_schema_version_is_also_swept(cache, jobs_db):
    old = _write_superseded(
        cache, _key(3), age_seconds=8 * 24 * 3600, layout="v0", algorithm=WAVEFORM_ALGORITHM_VERSION
    )
    cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert not old.exists()


def test_current_layout_is_never_treated_as_superseded(cache, jobs_db):
    current = _write_artifact(cache, _key(4))
    import os
    old = time.time() - 365 * 24 * 3600
    os.utime(current, (old, old))
    outcome = cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert outcome.removed_superseded == 0
    assert current.exists(), "age alone must never evict a servable artifact"


def test_emptied_superseded_directories_are_pruned(cache, jobs_db):
    old = _write_superseded(cache, _key(5), age_seconds=8 * 24 * 3600)
    layout_dir = old.parent.parent
    cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert not layout_dir.exists()
    assert cache.root.exists(), "the cache root itself is never removed"


def test_superseded_directory_holding_a_foreign_file_survives(cache, jobs_db):
    old = _write_superseded(cache, _key(6), age_seconds=8 * 24 * 3600)
    foreign = old.parent / "someone-elses-notes.txt"
    foreign.write_text("not ours")
    cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert not old.exists()
    assert foreign.exists(), "rmdir must never remove a directory with content in it"


def test_current_layout_directory_is_never_pruned(cache, jobs_db):
    current = _write_artifact(cache, _key(7))
    layout_dir = current.parent.parent
    current.unlink()
    cache_service.cleanup_cache(cache, max_cache_bytes=10 * 1024 * 1024)
    assert layout_dir.exists(), "the live publication directory must survive an empty cache"


def test_superseded_bytes_are_reported_in_usage(cache, jobs_db):
    _write_superseded(cache, _key(8), size=2048)
    _write_artifact(cache, _key(9), size=1024)
    usage = cache_service.cache_usage(cache)
    assert usage.superseded_count == 1
    assert usage.superseded_bytes == 2048
    assert usage.total_bytes == 3072


# ---------------------------------------------------------------------------
# Manual clear — preview
# ---------------------------------------------------------------------------


def test_clear_preview_reports_counts_and_bytes(cache, jobs_db):
    _write_artifact(cache, _key(1), size=1000)
    _write_artifact(cache, _key(2), size=2000)
    _write_temp(cache, "d" * 32, size=500)
    _make_ready(1, _key(1))
    preview = cache_service.preview_clear_cache(cache)
    assert preview.artifact_count == 2
    assert preview.temp_count == 1
    assert preview.total_bytes == 3500
    assert preview.ready_track_count == 1


def test_clear_preview_deletes_nothing(cache, jobs_db):
    artifact = _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    cache_service.preview_clear_cache(cache)
    assert artifact.exists()
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status.value == "ready"


def test_clear_preview_on_empty_cache_is_all_zero(cache, jobs_db):
    preview = cache_service.preview_clear_cache(cache)
    assert (preview.artifact_count, preview.temp_count, preview.total_bytes) == (0, 0, 0)
    assert preview.ready_track_count == 0


# ---------------------------------------------------------------------------
# Manual clear — execution and containment
# ---------------------------------------------------------------------------


def test_clear_removes_every_owned_cache_file(cache, jobs_db):
    artifacts = [_write_artifact(cache, _key(i), size=100) for i in range(1, 4)]
    temp = _write_temp(cache, "e" * 32, size=50)
    fresh_temp = _write_temp(cache, "f" * 32, size=50, age_seconds=0)
    outcome = cache_service.clear_cache(cache)
    assert outcome.removed_files == 5
    assert outcome.freed_bytes == 400
    assert outcome.remaining_files == 0
    assert not any(path.exists() for path in artifacts)
    assert not temp.exists() and not fresh_temp.exists()


def test_clear_resets_ready_track_states(cache, jobs_db):
    _write_artifact(cache, _key(1))
    _write_artifact(cache, _key(2))
    _make_ready(1, _key(1))
    _make_ready(2, _key(2))
    outcome = cache_service.clear_cache(cache)
    assert outcome.reset_track_states == 2
    for track_id in (1, 2):
        state = waveform_state_service.get_track_state(track_id, library_id=LIBRARY)
        assert state.status.value == "stale"
        assert state.last_error_code == waveform_job_service.ERROR_CACHE_CLEARED
        assert state.cache_key is None


def test_clear_never_deletes_source_music(cache, jobs_db):
    track = cache.library_root / "Set – DJ's mix (final).flac"
    track.write_bytes(b"pretend-audio")
    _write_artifact(cache, _key(1))
    cache_service.clear_cache(cache)
    assert track.exists() and track.read_bytes() == b"pretend-audio"


def test_clear_leaves_unknown_files_alone(cache, jobs_db):
    _write_artifact(cache, _key(1))
    stranger = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / "ab" / "README.txt"
    stranger.parent.mkdir(parents=True, exist_ok=True)
    stranger.write_text("not a CrateIQ artifact")
    outcome = cache_service.clear_cache(cache)
    assert outcome.removed_files == 1
    assert stranger.exists()


def test_clear_does_not_follow_symlinks_out_of_the_cache(cache, jobs_db, tmp_path):
    outside = tmp_path / "outside.json.gz"
    outside.write_bytes(b"must survive")
    link = cache.root / "v1" / WAVEFORM_ALGORITHM_VERSION / "ab" / f"{_key(1)}.json.gz"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)
    cache_service.clear_cache(cache)
    assert outside.exists() and outside.read_bytes() == b"must survive"


def test_clear_never_removes_the_cache_root(cache, jobs_db):
    _write_artifact(cache, _key(1))
    cache_service.clear_cache(cache)
    assert cache.root.exists()
    assert cache.library_root.exists()


def test_clear_is_idempotent(cache, jobs_db):
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    first = cache_service.clear_cache(cache)
    second = cache_service.clear_cache(cache)
    assert first.removed_files == 1 and first.reset_track_states == 1
    assert second.removed_files == 0 and second.reset_track_states == 0


def test_cleared_track_can_be_generated_again(cache, jobs_db):
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    cache_service.clear_cache(cache)
    result = waveform_job_service.submit_generation_job(
        snapshot=_snapshot(1), generation_key=_key(1), force=False, max_queue_size=32
    )
    assert result.outcome == "queued", "clearing must not block future explicit generation"


def test_clear_logs_contain_no_paths(cache, jobs_db, caplog, tmp_path):
    import logging
    caplog.set_level(logging.INFO)
    _write_artifact(cache, _key(1))
    cache_service.clear_cache(cache)
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert str(tmp_path) not in rendered
    assert "/home/" not in rendered
    assert "removed_files" in rendered


# ---------------------------------------------------------------------------
# Operational-row retention
# ---------------------------------------------------------------------------


def _age_job(job_id: str, days: float) -> None:
    from datetime import datetime, timedelta, timezone
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with backend_db.get_conn() as conn:
        conn.execute("UPDATE waveform_jobs SET finished_at = ? WHERE id = ?", (when, job_id))


def _age_track_state(track_id: int, days: float) -> None:
    from datetime import datetime, timedelta, timezone
    when = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with backend_db.get_conn() as conn:
        conn.execute(
            "UPDATE waveform_track_state SET updated_at = ?, last_accessed_at = NULL "
            "WHERE library_id = ? AND track_id = ?",
            (when, LIBRARY, track_id),
        )


def _finish(track_id: int, status: str, error_code: str = "X") -> str:
    from backend.app.models.waveform import WaveformArtifactStatus, WaveformJobStatus
    job = waveform_job_service.submit_generation_job(
        snapshot=_snapshot(track_id), generation_key=_key(track_id),
        force=False, max_queue_size=32,
    ).job
    waveform_job_service.finish_job_unsuccessfully(
        job.id,
        job_status=WaveformJobStatus(status),
        track_status=WaveformArtifactStatus.FAILED if status == "failed"
        else WaveformArtifactStatus.CANCELLED,
        error_code=error_code,
    )
    return job.id


def test_old_failed_job_rows_are_purged(cache, jobs_db):
    job_id = _finish(1, "failed")
    _age_job(job_id, 40)
    assert waveform_job_service.purge_expired_job_rows() == 1
    assert waveform_job_service.get_job(job_id) is None


def test_old_cancelled_job_rows_are_purged(cache, jobs_db):
    job_id = _finish(1, "cancelled")
    _age_job(job_id, 40)
    assert waveform_job_service.purge_expired_job_rows() == 1


def test_recent_job_rows_are_retained(cache, jobs_db):
    job_id = _finish(1, "failed")
    _age_job(job_id, 3)
    assert waveform_job_service.purge_expired_job_rows() == 0
    assert waveform_job_service.get_job(job_id) is not None


def test_active_job_rows_are_never_purged(cache, jobs_db):
    job = waveform_job_service.submit_generation_job(
        snapshot=_snapshot(1), generation_key=_key(1), force=False, max_queue_size=32
    ).job
    with backend_db.get_conn() as conn:
        conn.execute(
            "UPDATE waveform_jobs SET created_at = ?, finished_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00", job.id),
        )
    assert waveform_job_service.purge_expired_job_rows() == 0
    assert waveform_job_service.get_job(job.id) is not None


def test_purging_job_rows_deletes_no_cache_file(cache, jobs_db):
    artifact = _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    job_id = _finish(2, "failed")
    _age_job(job_id, 40)
    waveform_job_service.purge_expired_job_rows()
    assert artifact.exists(), "row retention must never touch the cache"
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status.value == "ready"


def test_quiet_artifactless_track_states_are_purged(cache, jobs_db):
    job_id = _finish(1, "failed")
    _age_job(job_id, 40)
    _age_track_state(1, 40)
    assert waveform_job_service.purge_expired_track_states() == 1
    # A forgotten row simply reads back as the pre-request default.
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status.value == "not_generated"


def test_ready_track_states_are_never_purged(cache, jobs_db):
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    _age_track_state(1, 400)
    assert waveform_job_service.purge_expired_track_states() == 0
    assert waveform_state_service.get_track_state(1, library_id=LIBRARY).status.value == "ready"


def test_recent_track_states_are_retained(cache, jobs_db):
    job_id = _finish(1, "failed")
    _age_job(job_id, 40)
    _age_track_state(1, 2)
    assert waveform_job_service.purge_expired_track_states() == 0


def test_startup_reconcile_applies_row_retention(cache, jobs_db):
    job_id = _finish(1, "failed")
    _age_job(job_id, 40)
    _age_track_state(1, 40)
    cache_service.startup_reconcile(cache, max_cache_bytes=10_000)
    assert waveform_job_service.get_job(job_id) is None


# ---------------------------------------------------------------------------
# Concurrent maintenance — multiple tabs / overlapping passes
# ---------------------------------------------------------------------------


def test_overlapping_cleanup_passes_are_serialized(cache, jobs_db):
    """Two passes must not both claim the same bytes."""
    import asyncio

    for seed in range(1, 6):
        _write_artifact(cache, _key(seed), size=1000)
        _make_ready(seed, _key(seed))

    async def _both():
        return await asyncio.gather(
            cache_service.cleanup_cache_locked(cache, max_cache_bytes=2000),
            cache_service.cleanup_cache_locked(cache, max_cache_bytes=2000),
        )

    first, second = asyncio.run(_both())
    total_removed = first.removed_total + second.removed_total
    on_disk = len(cache_service.scan_cache_entries(cache))
    assert total_removed + on_disk == 5, "each artifact may be counted removed exactly once"


def test_overlapping_clear_and_cleanup_are_serialized(cache, jobs_db):
    import asyncio

    for seed in range(1, 4):
        _write_artifact(cache, _key(seed), size=1000)
        _make_ready(seed, _key(seed))

    async def _both():
        return await asyncio.gather(
            cache_service.clear_cache_locked(cache),
            cache_service.cleanup_cache_locked(cache, max_cache_bytes=100),
        )

    cleared, cleaned = asyncio.run(_both())
    assert cleared.removed_files + cleaned.removed_total == 3
    assert cache_service.scan_cache_entries(cache) == []


def test_maintenance_lock_survives_a_new_event_loop(cache, jobs_db):
    """A second `asyncio.run` in one process must not hit a bound-loop error."""
    import asyncio

    _write_artifact(cache, _key(1))
    asyncio.run(cache_service.cleanup_cache_locked(cache, max_cache_bytes=10_000))
    asyncio.run(cache_service.cleanup_cache_locked(cache, max_cache_bytes=10_000))


def test_clearing_does_not_disturb_an_in_flight_job_row(cache, jobs_db):
    """A queued job survives a clear and can still publish afterwards."""
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    queued = waveform_job_service.submit_generation_job(
        snapshot=_snapshot(2), generation_key=_key(2), force=False, max_queue_size=32
    ).job
    cache_service.clear_cache(cache)
    assert waveform_job_service.get_job(queued.id).status.value == "queued"


def test_repeated_reads_from_many_tabs_write_at_most_once_per_window(cache, jobs_db):
    """Polling from several tabs must not amplify into a write per read."""
    _write_artifact(cache, _key(1))
    _make_ready(1, _key(1))
    with backend_db.get_conn() as conn:
        conn.execute(
            "UPDATE waveform_track_state SET generated_at = ?, last_accessed_at = NULL "
            "WHERE library_id = ? AND track_id = ?",
            ("2020-01-01T00:00:00+00:00", LIBRARY, 1),
        )
    writes = [waveform_job_service.touch_artifact_access(LIBRARY, 1) for _ in range(25)]
    assert writes.count(True) == 1
