"""Tests for the optional track-id-scoped BPM/key analysis contract.

Covers analysis_jobs_service._normalize_track_ids, the scoped
_bpm_candidates/_key_candidates selection, _run_bpm_analysis/_run_key_analysis
scoped execution, and preview() scope parity. All aubio/keyfinder-cli
subprocess calls are faked -- no real audio tooling required, and fixture
source "audio" files are disposable placeholder bytes, never real music.

API-level scoped-run/preview tests live in tests/test_backend_api.py; the
Process All regression proving an out-of-batch track is never analyzed lives
in tests/test_preparation_service.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core import db as backend_db
from backend.app.services import analysis_jobs_service


def _create_tracks_db(root: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            genre TEXT,
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _insert_track(
    db_path: Path, filepath: Path, filename: str, *,
    bpm: float | None = None, key_musical: str | None = None, key_camelot: str | None = None,
    write_bytes: bool = True,
) -> int:
    if write_bytes:
        filepath.write_bytes(b"disposable-fixture-bytes")
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, key_musical, key_camelot, status) "
            "VALUES (?, ?, 'Artist', 'Title', 'House', ?, ?, ?, 'ok')",
            (str(filepath), filename, bpm, key_musical, key_camelot),
        )
        return cur.lastrowid


def _bpm_by_id(db_path: Path) -> dict[int, float | None]:
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute("SELECT id, bpm FROM tracks").fetchall())


def _key_by_id(db_path: Path) -> dict[int, tuple[str | None, str | None]]:
    with sqlite3.connect(db_path) as conn:
        return {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id, key_musical, key_camelot FROM tracks")}


@pytest.fixture()
def scoped_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(analysis_jobs_service, "selected_library_root", lambda: root)
    db_path = _create_tracks_db(root)
    track_dir = root / "Inbox"
    track_dir.mkdir()
    return SimpleNamespace(root=root, db_path=db_path, track_dir=track_dir)


def _fake_bpm_run(command, **kwargs):
    return SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr="")


def _fake_key_run(command, **kwargs):
    return SimpleNamespace(returncode=0, stdout="8A\n", stderr="")


# ---------------------------------------------------------------------------
# _normalize_track_ids -- pure validation
# ---------------------------------------------------------------------------

def test_normalize_track_ids_none_passes_through_as_global():
    assert analysis_jobs_service._normalize_track_ids(None) is None


def test_normalize_track_ids_dedupes_preserving_order():
    assert analysis_jobs_service._normalize_track_ids([5, 3, 5, 5, 3]) == [5, 3]


def test_normalize_track_ids_rejects_non_positive():
    with pytest.raises(ValueError):
        analysis_jobs_service._normalize_track_ids([1, 0])
    with pytest.raises(ValueError):
        analysis_jobs_service._normalize_track_ids([-5])


def test_normalize_track_ids_rejects_oversized_scope():
    with pytest.raises(ValueError):
        analysis_jobs_service._normalize_track_ids(list(range(1, analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 2)))


def test_normalize_track_ids_with_max_track_ids_none_accepts_oversized_scope():
    """Trusted internal callers (Process All) opt out of the external-API
    bound by passing max_track_ids=None -- validation (positive ints,
    dedup) still applies."""
    oversized = list(range(1, analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 502)) + [3, 3]
    normalized = analysis_jobs_service._normalize_track_ids(oversized, max_track_ids=None)
    assert len(normalized) == analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 501  # deduplicated
    assert normalized[:5] == [1, 2, 3, 4, 5]

    with pytest.raises(ValueError):
        analysis_jobs_service._normalize_track_ids([0], max_track_ids=None)
    with pytest.raises(ValueError):
        analysis_jobs_service._normalize_track_ids([-1], max_track_ids=None)


# ---------------------------------------------------------------------------
# A. Global BPM backward compatibility
# ---------------------------------------------------------------------------

def test_global_bpm_run_unchanged_when_track_ids_omitted(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    id2 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    assert result["updated"] == 2
    assert result["remaining_missing_bpm"] == 0
    bpm_by_id = _bpm_by_id(scoped_env.db_path)
    assert bpm_by_id[id1] == 120.0 and bpm_by_id[id2] == 120.0


# ---------------------------------------------------------------------------
# B. Scoped BPM
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_processes_only_requested_track(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    id2 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")
    id3 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "c.mp3", "c.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    def fake_run(command, **kwargs):
        assert command[-1] == str(scoped_env.track_dir / "b.mp3")
        return _fake_bpm_run(command, **kwargs)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[id2])

    assert (result["analyzed"], result["updated"]) == (1, 1)
    bpm_by_id = _bpm_by_id(scoped_env.db_path)
    assert bpm_by_id[id2] == 120.0
    assert bpm_by_id[id1] is None and bpm_by_id[id3] is None


# ---------------------------------------------------------------------------
# C. Scoped key -- same proof for key_analysis
# ---------------------------------------------------------------------------

def test_scoped_key_run_processes_only_requested_track(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    id2 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_key_run)

    result = analysis_jobs_service._run_key_analysis(10, track_ids=[id2])

    assert (result["analyzed"], result["updated"]) == (1, 1)
    key_by_id = _key_by_id(scoped_env.db_path)
    assert key_by_id[id2] == ("A minor", "8A")
    assert key_by_id[id1] == (None, None)


# ---------------------------------------------------------------------------
# D. Explicit empty scope -- never a global fallback
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_with_explicit_empty_scope_touches_nothing(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("aubio must never run for an empty scope")),
    )

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[])

    assert (result["analyzed"], result["updated"], result["failed"]) == (0, 0, 0)
    assert result["remaining_missing_bpm"] == 0  # scoped remaining: zero within an empty scope
    assert _bpm_by_id(scoped_env.db_path)[id1] is None  # untouched, not silently analyzed


def test_scoped_key_run_with_explicit_empty_scope_touches_nothing(scoped_env, monkeypatch):
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("keyfinder-cli must never run for an empty scope")),
    )

    result = analysis_jobs_service._run_key_analysis(10, track_ids=[])

    assert (result["analyzed"], result["updated"]) == (0, 0)
    assert result["remaining_missing_key"] == 0


# ---------------------------------------------------------------------------
# E. Nonexistent IDs -- no widening
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_ignores_nonexistent_ids_without_global_fallback(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[id1, 999999])

    assert result["updated"] == 1
    assert _bpm_by_id(scoped_env.db_path)[id1] == 120.0


def test_scoped_bpm_run_with_only_nonexistent_ids_yields_zero_candidates(scoped_env, monkeypatch):
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")  # globally eligible, out of scope
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fall back to the global queue")),
    )

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[999999])

    assert (result["analyzed"], result["updated"]) == (0, 0)


# ---------------------------------------------------------------------------
# F. Mixed eligibility -- only missing-data candidates in scope are processed
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_skips_already_filled_track_in_scope(scoped_env, monkeypatch):
    missing_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3", bpm=None)
    filled_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3", bpm=99.0)
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    def fake_run(command, **kwargs):
        assert command[-1] == str(scoped_env.track_dir / "a.mp3")
        return _fake_bpm_run(command, **kwargs)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[missing_id, filled_id])

    assert result["updated"] == 1
    bpm_by_id = _bpm_by_id(scoped_env.db_path)
    assert bpm_by_id[missing_id] == 120.0
    assert bpm_by_id[filled_id] == 99.0  # never overwritten


def test_scoped_key_run_skips_already_filled_track_in_scope(scoped_env, monkeypatch):
    missing_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    filled_id = _insert_track(
        scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3", key_musical="C", key_camelot="8B",
    )
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_key_run)

    result = analysis_jobs_service._run_key_analysis(10, track_ids=[missing_id, filled_id])

    assert result["updated"] == 1
    key_by_id = _key_by_id(scoped_env.db_path)
    assert key_by_id[missing_id] == ("A minor", "8A")
    assert key_by_id[filled_id] == ("C", "8B")  # never overwritten


# ---------------------------------------------------------------------------
# G. Limit applies only inside scope
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_limit_never_reaches_outside_scope(scoped_env, monkeypatch):
    # Lower id, globally eligible, deliberately excluded from scope.
    outside_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "outside.mp3", "outside.mp3")
    scope_a = _insert_track(scoped_env.db_path, scoped_env.track_dir / "scope_a.mp3", "scope_a.mp3")
    scope_b = _insert_track(scoped_env.db_path, scoped_env.track_dir / "scope_b.mp3", "scope_b.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(1, track_ids=[scope_a, scope_b])

    assert result["updated"] == 1
    bpm_by_id = _bpm_by_id(scoped_env.db_path)
    assert bpm_by_id[outside_id] is None  # never touched despite having the lowest id
    assert bpm_by_id[scope_a] == 120.0  # deterministic id-ordering within scope
    assert bpm_by_id[scope_b] is None


# ---------------------------------------------------------------------------
# Internal (trusted) scopes larger than the external API's 2000-entry bound
# -- the prerequisite for a Process All batch spanning multiple imports.
# ---------------------------------------------------------------------------

def _seed_lightweight_missing_bpm_tracks(db_path: Path, count: int) -> None:
    """Bulk-insert placeholder tracks with no real audio files -- candidate
    selection is pure SQL and never touches the filesystem, so this is a
    cheap way to exceed _MAX_SCOPED_TRACK_IDS without writing real files."""
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm) "
            "VALUES (?, ?, 'Artist', 'Title', 'House', NULL)",
            [(f"/fake/track_{i}.mp3", f"track_{i}.mp3") for i in range(count)],
        )


def test_bpm_candidates_chunks_a_scope_larger_than_2000_and_applies_limit_inside_it(scoped_env):
    lightweight_count = analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 500
    _seed_lightweight_missing_bpm_tracks(scoped_env.db_path, lightweight_count)
    outside_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "outside.mp3", "outside.mp3")

    scope = list(range(1, lightweight_count + 1))
    assert len(scope) > analysis_jobs_service._MAX_SCOPED_TRACK_IDS
    assert len(scope) > analysis_jobs_service._SQLITE_ID_CHUNK_SIZE * 4  # forces multiple chunks

    with sqlite3.connect(scoped_env.db_path) as conn:
        candidates = analysis_jobs_service._bpm_candidates(conn, scoped_env.root, limit=25, track_ids=scope)

    assert [row["id"] for row in candidates] == list(range(1, 26))  # limit applied inside the full scope
    assert outside_id not in [row["id"] for row in candidates]  # never widened outside the requested scope


def test_run_bpm_analysis_default_still_rejects_scope_larger_than_2000(scoped_env):
    """The external-API-facing bound is unchanged by default -- only a
    caller that explicitly opts out with max_track_ids=None may exceed it."""
    oversized = list(range(1, analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 2))
    with pytest.raises(ValueError, match="cannot exceed"):
        analysis_jobs_service._run_bpm_analysis(25, oversized)


def test_run_bpm_analysis_accepts_scope_larger_than_2000_when_unbounded(scoped_env, monkeypatch):
    """A trusted internal caller (Process All) must never be rejected merely
    for a scope larger than the external API's bound -- SQL chunking has no
    dependency on that number."""
    lightweight_count = analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 500
    _seed_lightweight_missing_bpm_tracks(scoped_env.db_path, lightweight_count)
    outside_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "outside.mp3", "outside.mp3")
    scope = list(range(1, lightweight_count + 1))

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("placeholder paths are outside root; aubio must never run")),
    )

    result = analysis_jobs_service._run_bpm_analysis(25, scope, max_track_ids=None)

    assert result["skipped"] == 25  # every selected candidate's placeholder path is outside the library root
    assert result["failed"] == 0
    operation = analysis_jobs_service.operation_detail(result["operation_id"])
    assert operation["mode"] == "apply_scoped"
    assert operation["eligible_total"] == lightweight_count
    assert operation["considered"] == 25  # limit applied inside the full >2000 scope
    assert _bpm_by_id(scoped_env.db_path)[outside_id] is None  # untouched, never substituted in


def test_run_dispatch_accepts_scope_larger_than_2000_when_unbounded(scoped_env, monkeypatch):
    """Proves the public run() entry point -- what preparation_service
    actually calls -- threads max_track_ids through to the BPM/key runners."""
    lightweight_count = analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 10
    _seed_lightweight_missing_bpm_tracks(scoped_env.db_path, lightweight_count)
    scope = list(range(1, lightweight_count + 1))
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("placeholder paths are outside root; aubio must never run")),
    )

    result = analysis_jobs_service.run(
        "bpm_analysis", confirm=True, limit=25, track_ids=scope, max_track_ids=None,
    )

    assert result["skipped"] == 25


# ---------------------------------------------------------------------------
# H. Remaining count is scoped, not global
# ---------------------------------------------------------------------------

def test_scoped_bpm_remaining_missing_counts_only_scope(scoped_env, monkeypatch):
    scope_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    other_missing_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[scope_id])

    assert result["remaining_missing_bpm"] == 0  # scope fully satisfied
    assert _bpm_by_id(scoped_env.db_path)[other_missing_id] is None  # still globally missing, untouched


# ---------------------------------------------------------------------------
# I. Duplicate IDs -- one candidate maximum
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_deduplicates_repeated_ids(scoped_env, monkeypatch):
    track_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[track_id, track_id, track_id])

    assert (result["analyzed"], result["updated"]) == (1, 1)


# ---------------------------------------------------------------------------
# J. Path failure -- no substitute global track
# ---------------------------------------------------------------------------

def test_scoped_bpm_path_failure_does_not_substitute_global_track(scoped_env, monkeypatch):
    ghost_id = _insert_track(
        scoped_env.db_path, scoped_env.track_dir / "ghost.mp3", "ghost.mp3", write_bytes=False,
    )
    other_global_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "real.mp3", "real.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not analyze a track outside the requested scope")),
    )

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[ghost_id])

    assert (result["skipped"], result["updated"]) == (1, 0)
    assert _bpm_by_id(scoped_env.db_path)[other_global_id] is None


# ---------------------------------------------------------------------------
# Preview scope parity
# ---------------------------------------------------------------------------

def test_scoped_bpm_preview_matches_scoped_run_candidate_universe(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")  # eligible, out of scope
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    preview = analysis_jobs_service.preview("bpm_analysis", track_ids=[id1])

    assert preview["candidate_count"] == 1
    assert [item["track_id"] for item in preview["samples"]] == [id1]


def test_scoped_bpm_preview_with_explicit_empty_scope_returns_zero_candidates(scoped_env, monkeypatch):
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    preview = analysis_jobs_service.preview("bpm_analysis", track_ids=[])

    assert preview["candidate_count"] == 0
    assert preview["samples"] == []


def test_global_bpm_preview_unchanged_when_track_ids_omitted(scoped_env, monkeypatch):
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    preview = analysis_jobs_service.preview("bpm_analysis")

    assert preview["candidate_count"] == 2


def test_scoped_key_preview_matches_scoped_run_candidate_universe(scoped_env, monkeypatch):
    id1 = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "b.mp3", "b.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")

    preview = analysis_jobs_service.preview("key_analysis", track_ids=[id1])

    assert preview["candidate_count"] == 1
    assert [item["track_id"] for item in preview["samples"]] == [id1]


# ---------------------------------------------------------------------------
# Operation history mode reflects scoped vs global runs
# ---------------------------------------------------------------------------

def test_scoped_bpm_run_persists_apply_scoped_mode(scoped_env, monkeypatch):
    track_id = _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(10, track_ids=[track_id])

    operation = analysis_jobs_service.operation_detail(result["operation_id"])
    assert operation["mode"] == "apply_scoped"
    assert operation["eligible_total"] == 1
    assert operation["considered"] == 1


def test_global_bpm_run_persists_apply_mode(scoped_env, monkeypatch):
    _insert_track(scoped_env.db_path, scoped_env.track_dir / "a.mp3", "a.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _fake_bpm_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    operation = analysis_jobs_service.operation_detail(result["operation_id"])
    assert operation["mode"] == "apply"
