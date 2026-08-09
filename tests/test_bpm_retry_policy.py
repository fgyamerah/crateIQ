"""Tests for BPM automatic-retry pause/resume policy.

A track may only be paused from future automatic BPM retries after a
*proven* two-stage decode failure (direct aubio decode-error evidence, then
a genuine FFmpeg decode failure) -- see analysis_jobs_service._run_bpm_analysis
and bpm_retry_policy_service. This file covers:

  A. Suppression trigger: strict evidence only (pause created/not created)
  B. Candidate selection: pause exclusion, explicit [] stays empty, limits
     apply after pause filtering
  C. Explicit "Retry BPM now" (analysis_jobs_service.retry_track): exact-
     track isolation, success clears pause + resolves the finding, transient
     failure preserves the prior pause, repeated genuine failure stays
     paused and reactivates the same finding row
  D. Explicit "Resume automatic retries" (analysis_jobs_service.resume_retry):
     policy-only, idempotent, preserves BPM/decision/note
  E. Needs Review integration: one truthful active item, no duplicate

All aubio/ffmpeg subprocess calls are faked -- no real audio tooling
required. Fixture source "audio" files are disposable placeholder bytes,
never real music.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core import db as backend_db
from backend.app.services import (
    analysis_jobs_service,
    bpm_retry_policy_service,
    needs_review_service,
    quality_findings_service,
    quality_review_service,
)

_DECODE_STDERR = "[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n"


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


def _insert_track(db_path: Path, filepath: Path, filename: str, *, bpm: float | None = None) -> int:
    filepath.write_bytes(f"disposable-fixture-bytes-{filename}".encode())
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, status) "
            "VALUES (?, ?, 'Artist', 'Title', 'House', ?, 'ok')",
            (str(filepath), filename, bpm),
        )
        return cur.lastrowid


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bpm_row(db_path: Path, track_id: int):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT bpm, bpm_source FROM tracks WHERE id = ?", (track_id,)).fetchone()


def _durable_findings(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "quality_review_findings" not in tables:
            return []
        return conn.execute(
            "SELECT track_id, reason_code, occurrence_count, decision, note, resolved_at "
            "FROM quality_review_findings ORDER BY id"
        ).fetchall()


@pytest.fixture()
def bpm_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(analysis_jobs_service, "selected_library_root", lambda: root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = _create_tracks_db(root)
    track_dir = root / "Inbox"
    track_dir.mkdir()
    return SimpleNamespace(root=root, db_path=db_path, track_dir=track_dir)


def _two_stage_failure(command, **kwargs):
    if command[0] == "/fake/aubio" and command[1] == "tempo":
        return SimpleNamespace(returncode=1, stdout="", stderr=_DECODE_STDERR)
    if command[0] == "/fake/ffmpeg":
        return SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found when processing input\n")
    raise AssertionError(f"unexpected command {command}")


def _prime_pause(bpm_env, monkeypatch, filename: str = "paused.mp3") -> int:
    """Insert one track and drive it through a proven two-stage failure."""
    source = bpm_env.track_dir / filename
    track_id = _insert_track(bpm_env.db_path, source, filename)
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _two_stage_failure)
    result = analysis_jobs_service._run_bpm_analysis(10, [track_id], max_track_ids=None)
    assert result["failed"] == 1
    assert bpm_retry_policy_service.is_paused(track_id)
    return track_id


# ---------------------------------------------------------------------------
# A. Suppression trigger: strict evidence only
# ---------------------------------------------------------------------------

def test_proven_two_stage_failure_creates_pause(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    assert findings[0][0] == track_id
    assert findings[0][1] == "audio_decode_failed"


@pytest.mark.parametrize(
    "aubio_response, ffmpeg_response",
    [
        # Direct success: FFmpeg is never even attempted.
        (SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr=""), None),
        # Generic non-zero aubio (no decode evidence) + genuine FFmpeg decode failure.
        (
            SimpleNamespace(returncode=1, stdout="", stderr="unexpected internal error\n"),
            SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found when processing input\n"),
        ),
        # Clean exit, no tempo found (no decode evidence) + FFmpeg decode failure.
        (
            SimpleNamespace(returncode=0, stdout="\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found when processing input\n"),
        ),
    ],
)
def test_non_decode_failures_never_pause(bpm_env, monkeypatch, aubio_response, ffmpeg_response):
    source = bpm_env.track_dir / "not-paused.mp3"
    track_id = _insert_track(bpm_env.db_path, source, "not-paused.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/aubio":
            return aubio_response
        if command[0] == "/fake/ffmpeg":
            assert ffmpeg_response is not None
            return ffmpeg_response
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    analysis_jobs_service._run_bpm_analysis(10, [track_id], max_track_ids=None)
    assert bpm_retry_policy_service.is_paused(track_id) is False


def test_recoverable_ffmpeg_success_does_not_pause(bpm_env, monkeypatch):
    """Decode evidence + FFmpeg recovery success is a *warning*, not a pause."""
    source = bpm_env.track_dir / "recovered.mp3"
    track_id = _insert_track(bpm_env.db_path, source, "recovered.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0] == "/fake/aubio" and command[-1] == str(source):
            return SimpleNamespace(returncode=1, stdout="", stderr=_DECODE_STDERR)
        return SimpleNamespace(returncode=0, stdout="125.0 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10, [track_id], max_track_ids=None)
    assert result["updated"] == 1
    assert bpm_retry_policy_service.is_paused(track_id) is False


def test_missing_ffmpeg_tool_never_pauses(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "no-ffmpeg.mp3"
    track_id = _insert_track(bpm_env.db_path, source, "no-ffmpeg.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: None)
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=_DECODE_STDERR),
    )
    analysis_jobs_service._run_bpm_analysis(10, [track_id], max_track_ids=None)
    assert bpm_retry_policy_service.is_paused(track_id) is False


def test_timeout_never_pauses(bpm_env, monkeypatch):
    import subprocess as real_subprocess

    source = bpm_env.track_dir / "timeout.mp3"
    track_id = _insert_track(bpm_env.db_path, source, "timeout.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=1, stdout="", stderr=_DECODE_STDERR)
        raise real_subprocess.TimeoutExpired(cmd=command, timeout=60)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    analysis_jobs_service._run_bpm_analysis(10, [track_id], max_track_ids=None)
    assert bpm_retry_policy_service.is_paused(track_id) is False


def test_cancellation_prevents_pause_for_a_never_started_track(bpm_env, monkeypatch):
    """Cancellation is only checked between tracks, so a track already
    in-flight when cancel is requested still finishes with real evidence
    (and may legitimately pause) -- but a track cancellation prevents from
    ever starting must never be paused: it was never evaluated at all."""
    from backend.app.services import analysis_operations_service

    source1 = bpm_env.track_dir / "one.mp3"
    source2 = bpm_env.track_dir / "two.mp3"
    track_id_1 = _insert_track(bpm_env.db_path, source1, "one.mp3")
    track_id_2 = _insert_track(bpm_env.db_path, source2, "two.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        operation_id = analysis_jobs_service.history()["history"][0]["id"]
        analysis_operations_service.request_cancel(operation_id)
        return SimpleNamespace(returncode=1, stdout="", stderr=_DECODE_STDERR)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10, [track_id_1, track_id_2], max_track_ids=None)
    assert result["cancelled"] is True
    assert bpm_retry_policy_service.is_paused(track_id_2) is False


# ---------------------------------------------------------------------------
# B. Candidate selection: pause exclusion, empty scope, limit-after-filter
# ---------------------------------------------------------------------------

def test_paused_track_excluded_from_global_candidates(bpm_env, monkeypatch):
    paused_id = _prime_pause(bpm_env, monkeypatch, "paused.mp3")
    other_source = bpm_env.track_dir / "other.mp3"
    other_id = _insert_track(bpm_env.db_path, other_source, "other.mp3")

    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr=""),
    )
    result = analysis_jobs_service._run_bpm_analysis(10)
    assert result["analyzed"] == 1
    assert result["suppressed_count"] == 1
    assert _bpm_row(bpm_env.db_path, other_id) == (120.0, "aubio")
    assert _bpm_row(bpm_env.db_path, paused_id) == (None, None)


def test_paused_track_excluded_from_scoped_candidates(bpm_env, monkeypatch):
    paused_id = _prime_pause(bpm_env, monkeypatch, "paused.mp3")
    other_source = bpm_env.track_dir / "other.mp3"
    other_id = _insert_track(bpm_env.db_path, other_source, "other.mp3")

    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr=""),
    )
    result = analysis_jobs_service._run_bpm_analysis(10, [paused_id, other_id], max_track_ids=None)
    assert result["suppressed_count"] == 1
    assert result["analyzed"] == 1
    assert _bpm_row(bpm_env.db_path, other_id) == (120.0, "aubio")
    assert _bpm_row(bpm_env.db_path, paused_id) == (None, None)


def test_explicit_empty_scope_remains_empty_despite_paused_tracks(bpm_env, monkeypatch):
    _prime_pause(bpm_env, monkeypatch, "paused.mp3")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no tool call for an explicit empty scope")),
    )
    result = analysis_jobs_service._run_bpm_analysis(10, [], max_track_ids=None)
    assert result["analyzed"] == 0
    assert result["suppressed_count"] == 0


def test_limit_applies_after_pause_filtering(bpm_env, monkeypatch):
    """A paused track ordered before an eligible one must not consume the limit."""
    paused_id = _prime_pause(bpm_env, monkeypatch, "aaa-paused.mp3")
    eligible_source = bpm_env.track_dir / "zzz-eligible.mp3"
    eligible_id = _insert_track(bpm_env.db_path, eligible_source, "zzz-eligible.mp3")
    assert paused_id < eligible_id  # paused track sorts first by id

    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="130.0 bpm\n", stderr=""),
    )
    result = analysis_jobs_service._run_bpm_analysis(1)
    assert result["analyzed"] == 1
    assert _bpm_row(bpm_env.db_path, eligible_id) == (130.0, "aubio")
    assert _bpm_row(bpm_env.db_path, paused_id) == (None, None)


def test_preview_matches_run_candidate_set_and_suppressed_count(bpm_env, monkeypatch):
    paused_id = _prime_pause(bpm_env, monkeypatch, "paused.mp3")
    other_source = bpm_env.track_dir / "other.mp3"
    _insert_track(bpm_env.db_path, other_source, "other.mp3")

    preview = analysis_jobs_service.preview("bpm_analysis")
    assert preview["suppressed_count"] == 1
    assert preview["candidate_count"] == 1
    assert all(sample["track_id"] != paused_id for sample in preview["samples"])
    assert any("paused" in warning for warning in preview["warnings"])


# ---------------------------------------------------------------------------
# C. Explicit "Retry BPM now" (analysis_jobs_service.retry_track)
# ---------------------------------------------------------------------------

def test_retry_track_rejects_non_positive_id(bpm_env):
    with pytest.raises(ValueError):
        analysis_jobs_service.retry_track(0)
    with pytest.raises(ValueError):
        analysis_jobs_service.retry_track(-1)


def test_retry_track_isolation_with_lower_id_global_candidates_present(bpm_env, monkeypatch):
    """A lower-id, unpaused, globally-eligible track must never be touched by
    an exact-track retry -- even though it would sort first in a global run."""
    lower_source = bpm_env.track_dir / "lower.mp3"
    lower_id = _insert_track(bpm_env.db_path, lower_source, "lower.mp3")
    paused_id = _prime_pause(bpm_env, monkeypatch, "higher-paused.mp3")
    assert lower_id < paused_id

    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retry_track must never touch another track")),
    )
    # The bypassed track itself will call subprocess -- swap in a working fake
    # only for that exact path, and assert the other track is never touched.
    def fake_run(command, **kwargs):
        assert str(bpm_env.track_dir / "higher-paused.mp3") in command
        return SimpleNamespace(returncode=0, stdout="140.0 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service.retry_track(paused_id)

    assert result["analyzed"] == 1
    assert result["updated"] == 1
    assert _bpm_row(bpm_env.db_path, paused_id) == (140.0, "aubio")
    assert _bpm_row(bpm_env.db_path, lower_id) == (None, None)


def test_retry_track_direct_success_clears_pause_and_resolves_finding(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="128.0 bpm\n", stderr=""),
    )

    result = analysis_jobs_service.retry_track(track_id)

    assert result["updated"] == 1
    assert _bpm_row(bpm_env.db_path, track_id) == (128.0, "aubio")
    assert bpm_retry_policy_service.is_paused(track_id) is False
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    assert findings[0][1] == "audio_decode_failed"
    assert findings[0][5] is not None  # resolved_at set -- history retained, not deleted


def test_retry_track_ffmpeg_recovered_success_clears_pause_resolves_failure_retains_warning(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)

    def fake_run(command, **kwargs):
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[-1] == str(bpm_env.track_dir / "paused.mp3"):
            return SimpleNamespace(returncode=1, stdout="", stderr=_DECODE_STDERR)
        return SimpleNamespace(returncode=0, stdout="121.5 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service.retry_track(track_id)

    assert result["updated"] == 1
    assert result["recovered"] == 1
    assert _bpm_row(bpm_env.db_path, track_id) == (121.5, "aubio_ffmpeg_decode")
    assert bpm_retry_policy_service.is_paused(track_id) is False

    findings = {row[1]: row for row in _durable_findings(bpm_env.db_path)}
    assert findings["audio_decode_failed"][5] is not None  # resolved
    assert "recoverable_audio_decode_warning" in findings  # recovery warning retained/recorded


def test_retry_track_repeated_genuine_failure_stays_paused_and_reactivates_same_row(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _two_stage_failure)

    result = analysis_jobs_service.retry_track(track_id)

    assert result["failed"] == 1
    assert bpm_retry_policy_service.is_paused(track_id) is True
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1  # same row reactivated, not duplicated
    assert findings[0][2] == 2  # occurrence_count advanced
    assert findings[0][5] is None  # resolved_at cleared back to active


def test_retry_track_transient_failure_keeps_previous_pause_and_finding(bpm_env, monkeypatch):
    import subprocess as real_subprocess

    track_id = _prime_pause(bpm_env, monkeypatch)

    def fake_run(command, **kwargs):
        raise real_subprocess.TimeoutExpired(cmd=command, timeout=20)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service.retry_track(track_id)

    assert result["updated"] == 0
    assert bpm_retry_policy_service.is_paused(track_id) is True
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    assert findings[0][2] == 1  # occurrence_count unchanged -- no new failure was proven
    assert findings[0][5] is None  # still active


def test_retry_track_preserves_human_decision_and_note_through_reactivation(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    finding = quality_findings_service.list_findings()[0]
    quality_findings_service.update_decision(finding["finding_id"], "review_later", "Checking with the artist")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", _two_stage_failure)
    analysis_jobs_service.retry_track(track_id)

    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    assert findings[0][3] == "review_later"
    assert findings[0][4] == "Checking with the artist"


def test_retry_track_never_overwrites_existing_bpm(bpm_env, monkeypatch):
    """If the track already has a BPM by the time retry runs (e.g. via a
    concurrent explicit write), retry must be a safe no-op, never a clobber."""
    track_id = _prime_pause(bpm_env, monkeypatch)
    with sqlite3.connect(bpm_env.db_path) as conn:
        conn.execute("UPDATE tracks SET bpm = 99.0 WHERE id = ?", (track_id,))
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no tool call once bpm is already set")),
    )
    result = analysis_jobs_service.retry_track(track_id)
    assert result["analyzed"] == 0
    assert _bpm_row(bpm_env.db_path, track_id) == (99.0, None)


def test_retry_track_source_hash_unchanged(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    source = bpm_env.track_dir / "paused.mp3"
    before = _sha256(source)
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="128.0 bpm\n", stderr=""),
    )
    analysis_jobs_service.retry_track(track_id)
    assert _sha256(source) == before


# ---------------------------------------------------------------------------
# D. Explicit "Resume automatic retries" (analysis_jobs_service.resume_retry)
# ---------------------------------------------------------------------------

def test_resume_retry_rejects_non_positive_id(bpm_env):
    with pytest.raises(ValueError):
        analysis_jobs_service.resume_retry(0)


def test_resume_clears_only_policy_preserves_bpm_null_decision_and_finding(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    finding = quality_findings_service.list_findings()[0]
    quality_findings_service.update_decision(finding["finding_id"], "review_later", "Waiting on a re-rip")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resume must never invoke any analysis tool")),
    )

    result = analysis_jobs_service.resume_retry(track_id)

    assert result == {"track_id": track_id, "resumed": True, "was_paused": True}
    assert bpm_retry_policy_service.is_paused(track_id) is False
    assert _bpm_row(bpm_env.db_path, track_id) == (None, None)  # BPM still NULL, no write
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1  # finding preserved, not deleted
    assert findings[0][3] == "review_later"
    assert findings[0][4] == "Waiting on a re-rip"
    assert findings[0][5] is None  # not falsely resolved -- the technical failure is still real


def test_resume_is_idempotent_on_an_already_unpaused_track(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    first = analysis_jobs_service.resume_retry(track_id)
    second = analysis_jobs_service.resume_retry(track_id)
    assert first["was_paused"] is True
    assert second["was_paused"] is False
    assert second["resumed"] is True


def test_normal_run_after_resume_can_succeed_and_resolve_failure(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    analysis_jobs_service.resume_retry(track_id)

    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="118.0 bpm\n", stderr=""),
    )
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert result["updated"] == 1
    assert _bpm_row(bpm_env.db_path, track_id) == (118.0, "aubio")
    findings = _durable_findings(bpm_env.db_path)
    assert findings[0][5] is not None  # resolved by the normal run's success


# ---------------------------------------------------------------------------
# E. Needs Review integration
# ---------------------------------------------------------------------------

def test_needs_review_shows_one_active_quality_item_no_duplicate_suppression_item(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    result = needs_review_service.list_items("QUALITY")
    matching = [item for item in result["items"] if item.get("track_id") == track_id]
    assert len(matching) == 1
    assert matching[0]["reason_code"] == "audio_decode_failed"
    assert matching[0]["severity"] == "HIGH"


def test_needs_review_hides_resolved_finding_after_successful_retry(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    assert needs_review_service.list_items("QUALITY")["counts"]["QUALITY"] == 1

    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="128.0 bpm\n", stderr=""),
    )
    analysis_jobs_service.retry_track(track_id)

    result = needs_review_service.list_items("QUALITY")
    assert all(item.get("track_id") != track_id for item in result["items"])


def test_quality_review_item_reports_retry_paused_flag(bpm_env, monkeypatch):
    track_id = _prime_pause(bpm_env, monkeypatch)
    review = quality_review_service.get_review()
    item = next(item for item in review["items"] if item["track_id"] == track_id)
    assert item["retry_paused"] is True

    analysis_jobs_service.resume_retry(track_id)
    review = quality_review_service.get_review()
    item = next(item for item in review["items"] if item["track_id"] == track_id)
    assert item["retry_paused"] is False
