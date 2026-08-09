"""Tests for BPM analysis hardening against malformed-but-decodable audio.

Direct aubio decoding fails on some real-world MP3s with malformed frames
(e.g. "Header missing" / "AUBIO ERROR: source_avcodec: error when sending
packet") that FFmpeg can still decode with only recoverable warnings. These
tests exercise analysis_jobs_service._run_bpm_analysis and
_attempt_ffmpeg_fallback directly against an isolated processed.db +
jobs.db, with all aubio/ffmpeg subprocess calls faked -- no real audio
tooling is required to run this file, and the fixture source "audio" files
are disposable placeholder bytes, never real music.
"""
from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.core import db as backend_db
from backend.app.services import analysis_jobs_service, analysis_operations_service


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
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, status) "
            "VALUES (?, ?, 'Artist', 'Title', 'House', ?, 'ok')",
            (str(filepath), filename, bpm),
        )
        return cur.lastrowid


@pytest.fixture()
def bpm_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(analysis_jobs_service, "selected_library_root", lambda: root)
    db_path = _create_tracks_db(root)
    track_dir = root / "Inbox"
    track_dir.mkdir()
    return SimpleNamespace(root=root, db_path=db_path, track_dir=track_dir)


def _bpm_row(db_path: Path, filename: str):
    with sqlite3.connect(db_path) as conn:
        return conn.execute(
            "SELECT bpm, bpm_source FROM tracks WHERE filename = ?", (filename,)
        ).fetchone()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _durable_findings(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "quality_review_findings" not in tables:
            return []
        return conn.execute(
            "SELECT track_id, reason_code, source, severity, occurrence_count, decision "
            "FROM quality_review_findings ORDER BY id"
        ).fetchall()


# ---------------------------------------------------------------------------
# A. Direct aubio success -- FFmpeg must never be invoked
# ---------------------------------------------------------------------------

def test_direct_aubio_success_skips_ffmpeg_and_records_provenance(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "clean.mp3"
    source.write_bytes(b"clean-disposable-fixture-bytes")
    before_hash = _sha256(source)
    _insert_track(bpm_env.db_path, source, "clean.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service, "_resolve_ffmpeg_binary",
        lambda: (_ for _ in ()).throw(AssertionError("ffmpeg must not be resolved on a direct success")),
    )

    def fake_run(command, **kwargs):
        assert command == ["/fake/aubio", "tempo", str(source)]
        return SimpleNamespace(returncode=0, stdout="128.0 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["analyzed"], result["updated"], result["failed"], result["recovered"]) == (1, 1, 0, 0)
    assert result["outcome"] == "complete"
    assert result["warnings"] == []
    assert _bpm_row(bpm_env.db_path, "clean.mp3") == (128.0, "aubio")
    assert _sha256(source) == before_hash
    assert _durable_findings(bpm_env.db_path) == []


# ---------------------------------------------------------------------------
# B. Direct failure -> FFmpeg fallback success (the real-world Fefe case)
# ---------------------------------------------------------------------------

def test_ffmpeg_fallback_recovers_bpm_when_direct_aubio_fails(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "malformed.mp3"
    source.write_bytes(b"malformed-disposable-fixture-bytes")
    before_hash = _sha256(source)
    _insert_track(bpm_env.db_path, source, "malformed.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "/fake/aubio" and command[-1] == str(source):
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n",
            )
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(
                returncode=0, stdout="",
                stderr="Header missing\nInvalid data found when processing input\n",
            )
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=0, stdout="125.27 bpm\n", stderr="")
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    # One track attempted, not two -- despite three subprocess invocations.
    assert (result["analyzed"], result["updated"], result["failed"], result["recovered"]) == (1, 1, 0, 1)
    assert result["outcome"] == "completed_with_warnings"
    assert len(calls) == 3
    assert any("FFmpeg recovered enough audio" in w and "125.27" in w for w in result["warnings"])
    assert _bpm_row(bpm_env.db_path, "malformed.mp3") == (125.27, "aubio_ffmpeg_decode")
    assert _sha256(source) == before_hash

    # Direct aubio showed decode-error evidence (non-zero exit) and FFmpeg
    # recovery succeeded -- a durable, non-blocking Quality finding survives
    # this BPM run, distinct from the operation-history warning above.
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    track_id, reason_code, source_tag, severity, occurrence_count, decision = findings[0]
    assert reason_code == "recoverable_audio_decode_warning"
    assert source_tag == "bpm_analysis"
    assert severity == "warning"
    assert occurrence_count == 1
    assert decision == "unresolved"


# ---------------------------------------------------------------------------
# C. Direct failure -> FFmpeg decode also fails -> final track failure
# ---------------------------------------------------------------------------

def test_ffmpeg_decode_failure_records_track_failure_and_no_bpm_write(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "undecodable.mp3"
    source.write_bytes(b"undecodable-disposable-fixture-bytes")
    before_hash = _sha256(source)
    _insert_track(bpm_env.db_path, source, "undecodable.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/aubio":
            # Real-style decoder evidence -- required for a durable
            # audio_decode_failed finding, not just a non-zero exit.
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n",
            )
        if command[0] == "/fake/ffmpeg":
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found when processing input\n")
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["analyzed"], result["updated"], result["failed"], result["recovered"]) == (1, 0, 1, 0)
    assert result["outcome"] == "completed_with_errors"
    assert any("FFmpeg could not recover a decodable audio stream" in w for w in result["warnings"])
    assert _bpm_row(bpm_env.db_path, "undecodable.mp3") == (None, None)
    assert _sha256(source) == before_hash

    # Both stages genuinely failed to decode, with real decoder evidence from
    # direct aubio -- a durable, high-severity finding is recorded even
    # though no BPM was ever written.
    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    track_id, reason_code, source_tag, severity, occurrence_count, decision = findings[0]
    assert reason_code == "audio_decode_failed"
    assert source_tag == "bpm_analysis"
    assert severity == "error"
    assert decision == "unresolved"


def test_generic_nonzero_exit_with_ffmpeg_decode_failure_creates_no_durable_finding(bpm_env, monkeypatch):
    """A non-zero aubio exit alone is NOT proof of malformed audio.

    Even when FFmpeg also genuinely fails to produce a decodable stream, a
    generic/unrecognized aubio stderr (no explicit decoder-error signal)
    must not be escalated into a durable "malformed audio" finding -- that
    would be a false positive falsely labelling healthy-but-mysteriously-
    failing audio as corrupt.
    """
    source = bpm_env.track_dir / "generic-failure.mp3"
    source.write_bytes(b"generic-failure-disposable-fixture-bytes")
    before_hash = _sha256(source)
    _insert_track(bpm_env.db_path, source, "generic-failure.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=1, stdout="", stderr="unexpected internal error\n")
        if command[0] == "/fake/ffmpeg":
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found when processing input\n")
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["analyzed"], result["updated"], result["failed"], result["recovered"]) == (1, 0, 1, 0)
    assert _bpm_row(bpm_env.db_path, "generic-failure.mp3") == (None, None)
    assert _sha256(source) == before_hash
    assert _durable_findings(bpm_env.db_path) == []


def test_repeated_decode_failure_stays_paused_and_does_not_duplicate_finding(bpm_env, monkeypatch):
    """A genuinely-undecodable track is paused after its first proven failure,
    so a second normal (unscoped) run must not re-attempt it at all -- the
    durable finding's occurrence_count stays 1. Retry pause/reactivation
    behavior (via the explicit retry_track bypass) is covered in
    tests/test_bpm_retry_policy.py.
    """
    source = bpm_env.track_dir / "still-undecodable.mp3"
    source.write_bytes(b"still-undecodable-disposable-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "still-undecodable.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "/fake/aubio":
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n",
            )
        if command[0] == "/fake/ffmpeg":
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid data found when processing input\n")
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    first = analysis_jobs_service._run_bpm_analysis(10)
    assert first["suppressed_count"] == 0
    second = analysis_jobs_service._run_bpm_analysis(10)

    # The track was paused by the first run's proven two-stage failure --
    # the second run must skip it entirely (no new tool calls) rather than
    # re-attempting it.
    assert second["analyzed"] == 0
    assert second["suppressed_count"] == 1
    # Only the first run's 2 calls (direct aubio + ffmpeg decode -- ffmpeg
    # itself failed to decode, so no fallback tempo call ever happens); the
    # second run makes zero calls because the track was already excluded.
    assert len(calls) == 2

    findings = _durable_findings(bpm_env.db_path)
    assert len(findings) == 1
    track_id, reason_code, source_tag, severity, occurrence_count, decision = findings[0]
    assert reason_code == "audio_decode_failed"
    assert occurrence_count == 1
    assert decision == "unresolved"


# ---------------------------------------------------------------------------
# D. FFmpeg recovers audio, but the fallback aubio still finds no usable BPM
# ---------------------------------------------------------------------------

def test_fallback_aubio_no_usable_bpm_records_track_failure(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "silence.mp3"
    source.write_bytes(b"silence-disposable-fixture-bytes")
    before_hash = _sha256(source)
    _insert_track(bpm_env.db_path, source, "silence.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0] == "/fake/aubio" and command[-1] == str(source):
            return SimpleNamespace(returncode=1, stdout="", stderr="decode error\n")
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=0, stdout="\n", stderr="")  # no numeric BPM
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["analyzed"], result["updated"], result["failed"], result["recovered"]) == (1, 0, 1, 0)
    assert result["outcome"] == "completed_with_errors"
    assert any("aubio could not determine a usable BPM" in w for w in result["warnings"])
    assert _bpm_row(bpm_env.db_path, "silence.mp3") == (None, None)
    assert _sha256(source) == before_hash

    # FFmpeg successfully decoded valid audio -- aubio simply found no tempo
    # in it. That does not prove the source is defective, so no durable
    # audio_decode_failed finding is recorded despite direct aubio's non-zero
    # exit (see AGENTS.md-scoped task: "fallback aubio no tempo is not
    # automatically quality").
    assert _durable_findings(bpm_env.db_path) == []


# ---------------------------------------------------------------------------
# D2. Fallback succeeds with a real BPM, but direct aubio showed no decode
#     evidence (ran cleanly, just found no tempo) -- not "malformed audio".
# ---------------------------------------------------------------------------

def test_fallback_success_without_decode_evidence_creates_no_durable_finding(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "no-evidence.mp3"
    source.write_bytes(b"no-evidence-disposable-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "no-evidence.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0] == "/fake/aubio" and command[-1] == str(source):
            # Direct aubio exits cleanly (0) but finds no numeric tempo --
            # this is "no_tempo", not "decode_error".
            return SimpleNamespace(returncode=0, stdout="\n", stderr="")
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=0, stdout="118.0 bpm\n", stderr="")
        raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["updated"], result["failed"], result["recovered"]) == (1, 0, 1)
    assert _bpm_row(bpm_env.db_path, "no-evidence.mp3") == (118.0, "aubio_ffmpeg_decode")
    assert _durable_findings(bpm_env.db_path) == []


# ---------------------------------------------------------------------------
# E. Timeout handling: no partial write, temp cleanup, truthful failure
# ---------------------------------------------------------------------------

def test_direct_aubio_timeout_falls_back_to_ffmpeg_successfully(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "slow.mp3"
    source.write_bytes(b"slow-disposable-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "slow.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/aubio" and command[-1] == str(source):
            raise subprocess.TimeoutExpired(cmd=command, timeout=20)
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="130.0 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["updated"], result["failed"], result["recovered"]) == (1, 0, 1)
    assert _bpm_row(bpm_env.db_path, "slow.mp3") == (130.0, "aubio_ffmpeg_decode")
    # A timeout is a transient tool problem, never evidence the audio itself
    # is defective -- no durable finding, even though recovery succeeded.
    assert _durable_findings(bpm_env.db_path) == []


def test_ffmpeg_timeout_records_final_failure_with_no_partial_write(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "ffmpeg-timeout.mp3"
    source.write_bytes(b"timeout-disposable-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "ffmpeg-timeout.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/aubio":
            return SimpleNamespace(
                returncode=1, stdout="",
                stderr="[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n",
            )
        raise subprocess.TimeoutExpired(cmd=command, timeout=60)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["updated"], result["failed"], result["recovered"]) == (0, 1, 0)
    assert any("FFmpeg timed out" in w for w in result["warnings"])
    assert _bpm_row(bpm_env.db_path, "ffmpeg-timeout.mp3") == (None, None)
    # FFmpeg never actually ran to completion -- it timed out, which is a
    # transient tool problem, not proof recovery was impossible. No durable
    # audio_decode_failed finding, even though direct aubio showed real
    # decode evidence -- both stages must genuinely fail to decode, not just
    # one of them plus an unrelated tool timeout.
    assert _durable_findings(bpm_env.db_path) == []


def test_fallback_aubio_timeout_records_final_failure(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "fallback-timeout.mp3"
    source.write_bytes(b"timeout-disposable-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "fallback-timeout.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run(command, **kwargs):
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[0] == "/fake/aubio" and command[-1] == str(source):
            return SimpleNamespace(returncode=1, stdout="", stderr="decode error\n")
        raise subprocess.TimeoutExpired(cmd=command, timeout=20)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["updated"], result["failed"], result["recovered"]) == (0, 1, 0)
    assert any("aubio timed out" in w for w in result["warnings"])
    assert _bpm_row(bpm_env.db_path, "fallback-timeout.mp3") == (None, None)
    # FFmpeg itself decoded a valid WAV -- the timeout happened afterward, in
    # the fallback tempo-detection step, so it is not a decode failure.
    assert _durable_findings(bpm_env.db_path) == []


# ---------------------------------------------------------------------------
# E2. FFmpeg unavailable: direct evidence alone is not "proven independently"
# ---------------------------------------------------------------------------

def test_ffmpeg_unavailable_creates_no_durable_finding_despite_direct_evidence(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "no-ffmpeg.mp3"
    source.write_bytes(b"no-ffmpeg-disposable-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "no-ffmpeg.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: None)

    def fake_run(command, **kwargs):
        assert command[0] == "/fake/aubio"
        return SimpleNamespace(
            returncode=1, stdout="",
            stderr="[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n",
        )

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert (result["updated"], result["failed"], result["recovered"]) == (0, 1, 0)
    assert any("FFmpeg is not available" in w for w in result["warnings"])
    assert _bpm_row(bpm_env.db_path, "no-ffmpeg.mp3") == (None, None)
    # FFmpeg was never attempted -- direct evidence alone does not prove
    # recovery was impossible, so no durable audio_decode_failed finding.
    assert _durable_findings(bpm_env.db_path) == []


# ---------------------------------------------------------------------------
# F / G. Existing / trusted BPM is never touched and never re-analyzed
# ---------------------------------------------------------------------------

def test_existing_bpm_is_untouched_and_never_triggers_any_tool_call(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "already-known.mp3"
    source.write_bytes(b"already-known-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "already-known.mp3", bpm=120.0)

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no tool should run for a track with an existing BPM")),
    )

    result = analysis_jobs_service._run_bpm_analysis(10)
    assert (result["analyzed"], result["updated"], result["failed"]) == (0, 0, 0)
    assert _bpm_row(bpm_env.db_path, "already-known.mp3") == (120.0, None)


def test_mixed_in_key_trusted_bpm_is_preserved(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "mik.mp3"
    source.write_bytes(b"mik-fixture-bytes")
    with sqlite3.connect(bpm_env.db_path) as conn:
        conn.execute("ALTER TABLE tracks ADD COLUMN bpm_source TEXT")
        conn.execute("ALTER TABLE tracks ADD COLUMN bpm_trusted INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, bpm_source, bpm_trusted, status) "
            "VALUES (?, 'mik.mp3', 'Artist', 'Title', 'House', 120.0, 'mixed_in_key', 1, 'ok')",
            (str(source),),
        )

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("MIK-trusted tracks must never be re-analyzed")),
    )

    analysis_jobs_service._run_bpm_analysis(10)
    with sqlite3.connect(bpm_env.db_path) as conn:
        row = conn.execute(
            "SELECT bpm, bpm_source, bpm_trusted FROM tracks WHERE filename = 'mik.mp3'"
        ).fetchone()
    assert row == (120.0, "mixed_in_key", 1)


# ---------------------------------------------------------------------------
# H. Path safety: a track outside the selected root is skipped, never falls
#    back, and never reaches either tool.
# ---------------------------------------------------------------------------

def test_path_outside_root_is_skipped_without_any_tool_invocation(bpm_env, monkeypatch, tmp_path):
    outside = tmp_path / "outside-root" / "escaped.mp3"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"escaped-fixture-bytes")
    _insert_track(bpm_env.db_path, outside, "escaped.mp3")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("a path-invalid track must never reach any tool")),
    )

    result = analysis_jobs_service._run_bpm_analysis(10)
    assert (result["analyzed"], result["skipped"], result["failed"]) == (0, 1, 0)
    assert any("outside the selected library root" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# Temp cleanup: guaranteed removal on both fallback success and failure
# ---------------------------------------------------------------------------

def test_fallback_temp_artifacts_are_always_cleaned_up(bpm_env, monkeypatch):
    def _leftover_dirs() -> set[Path]:
        return set(Path(tempfile.gettempdir()).glob("crateiq-bpm-*"))

    before = _leftover_dirs()

    source_ok = bpm_env.track_dir / "cleanup-ok.mp3"
    source_ok.write_bytes(b"cleanup-ok-bytes")
    _insert_track(bpm_env.db_path, source_ok, "cleanup-ok.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: "/fake/ffmpeg")

    def fake_run_success(command, **kwargs):
        if command[0] == "/fake/aubio" and command[-1] == str(source_ok):
            return SimpleNamespace(returncode=1, stdout="", stderr="err\n")
        if command[0] == "/fake/ffmpeg":
            Path(command[-1]).write_bytes(b"RIFF0000WAVEfmt ")
            return SimpleNamespace(returncode=0, stdout="", stderr="warn\n")
        return SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run_success)
    analysis_jobs_service._run_bpm_analysis(10)
    assert _leftover_dirs() == before, "a successful fallback must not leave a temp directory behind"

    source_fail = bpm_env.track_dir / "cleanup-fail.mp3"
    source_fail.write_bytes(b"cleanup-fail-bytes")
    _insert_track(bpm_env.db_path, source_fail, "cleanup-fail.mp3")

    def fake_run_fail(command, **kwargs):
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=1, stdout="", stderr="err\n")
        return SimpleNamespace(returncode=1, stdout="", stderr="ffmpeg fail\n")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run_fail)
    analysis_jobs_service._run_bpm_analysis(10)
    assert _leftover_dirs() == before, "a failed fallback must not leave a temp directory behind"


# ---------------------------------------------------------------------------
# J. Operation-level outcome semantics: cancelled / operation exception
# ---------------------------------------------------------------------------

def test_cancelled_operation_outcome_is_cancelled(bpm_env, monkeypatch):
    source1 = bpm_env.track_dir / "one.mp3"
    source1.write_bytes(b"one-fixture-bytes")
    source2 = bpm_env.track_dir / "two.mp3"
    source2.write_bytes(b"two-fixture-bytes")
    _insert_track(bpm_env.db_path, source1, "one.mp3")
    _insert_track(bpm_env.db_path, source2, "two.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        operation_id = analysis_jobs_service.history()["history"][0]["id"]
        analysis_operations_service.request_cancel(operation_id)
        return SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)
    result = analysis_jobs_service._run_bpm_analysis(10)

    assert result["cancelled"] is True
    assert result["outcome"] == "cancelled"
    assert len(calls) == 1  # cancellation was noticed before a second track started
    assert _durable_findings(bpm_env.db_path) == []


def test_operation_level_exception_marks_operation_failed(bpm_env, monkeypatch):
    source = bpm_env.track_dir / "any.mp3"
    source.write_bytes(b"any-fixture-bytes")
    _insert_track(bpm_env.db_path, source, "any.mp3")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated operation-level crash")

    monkeypatch.setattr(analysis_operations_service, "update_progress", boom)

    with pytest.raises(RuntimeError):
        analysis_jobs_service._run_bpm_analysis(10)

    history = analysis_operations_service.list_recent()
    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert history[0]["outcome"] == "failed"


# ---------------------------------------------------------------------------
# Unit-level coverage for the smaller building blocks
# ---------------------------------------------------------------------------

def test_ffmpeg_fallback_reports_truthfully_when_ffmpeg_is_not_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffmpeg_binary", lambda: None)
    bpm, message, ffmpeg_decode_failed = analysis_jobs_service._attempt_ffmpeg_fallback(
        "/fake/aubio", tmp_path / "source.mp3", "source.mp3"
    )
    assert bpm is None
    assert "FFmpeg is not available" in message
    assert ffmpeg_decode_failed is False


def test_resolve_ffmpeg_binary_respects_env_override(monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "/usr/bin/does-not-exist-ffmpeg")
    assert analysis_jobs_service._resolve_ffmpeg_binary() is None


def test_sanitize_diagnostic_dedupes_and_caps_length():
    noisy = "\n".join(["Header missing"] * 50 + ["Invalid data found when processing input"])
    condensed = analysis_jobs_service._sanitize_diagnostic(noisy)
    assert condensed.count("Header missing") == 1
    assert "Invalid data found when processing input" in condensed
    assert len(condensed) <= analysis_jobs_service._DIAGNOSTIC_MAX_LEN


# ---------------------------------------------------------------------------
# Direct-aubio failure classification: a non-zero exit alone is NOT decode
# evidence. Only an explicit, known decoder/media-error signal in stderr
# earns "decode_error"; every other non-zero exit is a neutral "process_error"
# that never becomes a durable "malformed audio" finding.
# ---------------------------------------------------------------------------

def test_classify_zero_exit_no_bpm_is_no_tempo():
    assert analysis_jobs_service._classify_direct_aubio_failure(0, "") == "no_tempo"


def test_classify_timeout_is_tool_error():
    assert analysis_jobs_service._classify_direct_aubio_failure(None, "", timed_out=True) == "tool_error"


def test_classify_os_error_is_tool_error():
    assert analysis_jobs_service._classify_direct_aubio_failure(None, "", os_error=True) == "tool_error"


def test_classify_nonzero_generic_stderr_is_process_error_not_decode_error():
    assert analysis_jobs_service._classify_direct_aubio_failure(1, "unexpected internal error\n") == "process_error"


def test_classify_nonzero_empty_stderr_is_process_error():
    assert analysis_jobs_service._classify_direct_aubio_failure(1, "") == "process_error"


def test_classify_nonzero_real_style_decoder_evidence_is_decode_error():
    stderr = "[mp3float] Header missing\nAUBIO ERROR: source_avcodec: error when sending packet\n"
    assert analysis_jobs_service._classify_direct_aubio_failure(1, stderr) == "decode_error"


@pytest.mark.parametrize(
    "stderr",
    [
        "Header missing\n",
        "Invalid data found when processing input\n",
        "AUBIO ERROR: source_avcodec: could not open codec\n",
        "error when sending packet to decoder\n",
        "HEADER MISSING (uppercase)\n",
    ],
)
def test_has_decode_evidence_matches_known_signals(stderr):
    assert analysis_jobs_service._has_decode_evidence(stderr) is True


@pytest.mark.parametrize("stderr", ["", "decode error\n", "unexpected internal error\n", "segmentation fault\n"])
def test_has_decode_evidence_rejects_generic_text(stderr):
    assert analysis_jobs_service._has_decode_evidence(stderr) is False


@pytest.mark.parametrize(
    ("status", "failed", "recovered", "expected"),
    [
        ("completed", 0, 0, "complete"),
        ("completed", 0, 2, "completed_with_warnings"),
        ("completed", 1, 0, "completed_with_errors"),
        ("completed", 1, 2, "completed_with_errors"),
        ("cancelled", 0, 0, "cancelled"),
        ("failed", 0, 0, "failed"),
        ("running", 0, 0, "running"),
    ],
)
def test_derive_outcome_matrix(status, failed, recovered, expected):
    assert analysis_operations_service.derive_outcome(status, failed, recovered) == expected
