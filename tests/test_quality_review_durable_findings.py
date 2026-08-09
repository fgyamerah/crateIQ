"""Tests for durable audio-quality findings that survive an ffprobe refresh.

quality_review_service's snapshot model (`quality_review_snapshots`) fully
replaces its items_json on every refresh_preview() call, so a per-event
finding (e.g. a BPM-analysis decode warning) cannot live there without being
lost on the next refresh. quality_findings_service is the neutral, durable
persistence layer for that finding class; these tests exercise it directly
and through quality_review_service's unified get_review()/update_decision(),
plus needs_review_service's QUALITY aggregation of the merged result.

All fixtures use a disposable temp library root + minimal tracks table --
no real audio tooling or production library data is touched.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.app.services import needs_review_service, quality_findings_service, quality_review_service


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    (root / "logs").mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
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
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, status) "
            "VALUES ('/inbox/malformed.mp3', 'malformed.mp3', 'Artist', 'Title', 'House', 125.27, 'ok')"
        )
    return root, db_path


def _track_id(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT id FROM tracks WHERE filename = 'malformed.mp3'").fetchone()[0]


# ---------------------------------------------------------------------------
# A. Recording a durable finding
# ---------------------------------------------------------------------------

def test_record_recovery_finding_is_unresolved_and_non_blocking(library):
    _root, db_path = library
    track_id = _track_id(db_path)

    finding = quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    assert finding["reason_code"] == "recoverable_audio_decode_warning"
    assert finding["severity"] == "warning"
    assert finding["blocking"] is False
    assert finding["decision"] == "unresolved"
    assert finding["occurrence_count"] == 1


# ---------------------------------------------------------------------------
# A2. details_json must always be valid JSON, never truncated mid-document
# ---------------------------------------------------------------------------

def _raw_details_json(db_path: Path, track_id: int, reason_code: str) -> str | None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT details_json FROM quality_review_findings WHERE track_id = ? AND reason_code = ?",
            (track_id, reason_code),
        ).fetchone()
        return row[0] if row else None


def test_long_diagnostic_produces_valid_bounded_json(library):
    _root, db_path = library
    track_id = _track_id(db_path)
    # Deliberately longer than any reasonable per-field cap -- a naive
    # json.dumps(...)[:n] slice on a payload this size would very likely
    # land mid-string and produce invalid JSON.
    long_diagnostic = "Header missing " * 200

    finding = quality_findings_service.record_audio_decode_warning(track_id, 125.27, long_diagnostic)

    raw = _raw_details_json(db_path, track_id, "recoverable_audio_decode_warning")
    assert raw is not None
    parsed = json.loads(raw)  # must not raise -- proves details_json is valid JSON
    assert isinstance(parsed["diagnostic"], str)
    assert len(parsed["diagnostic"]) < len(long_diagnostic)
    assert finding["details"] is not None
    assert finding["details"]["diagnostic"] == parsed["diagnostic"]


def test_diagnostic_with_special_characters_round_trips_as_valid_json(library):
    _root, db_path = library
    track_id = _track_id(db_path)
    tricky = 'quote:" backslash:\\ newline:\nend'

    quality_findings_service.record_audio_decode_failure(track_id, tricky)

    raw = _raw_details_json(db_path, track_id, "audio_decode_failed")
    assert raw is not None
    parsed = json.loads(raw)  # must not raise
    assert parsed["diagnostic"].startswith('quote:" backslash:\\ newline:')


# ---------------------------------------------------------------------------
# B. Survives an ffprobe refresh -- the central regression this task exists for
# ---------------------------------------------------------------------------

def test_durable_finding_survives_ffprobe_snapshot_replace(library, monkeypatch):
    _root, db_path = library
    track_id = _track_id(db_path)
    quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    # Simulate refresh_preview() creating a new ffprobe snapshot that finds
    # nothing (the durable finding is unrelated to ffprobe's own flags).
    monkeypatch.setattr(
        quality_review_service.analysis_jobs_service, "preview",
        lambda job_type: {"quality_probes": [], "warnings": [], "summary": {"total_tracks_checked": 1}},
    )
    quality_review_service.refresh_preview()

    review = quality_review_service.get_review()
    durable = [item for item in review["items"] if item["reason_code"] == "recoverable_audio_decode_warning"]
    assert len(durable) == 1
    assert durable[0]["track_id"] == track_id
    assert durable[0]["decision"] == "unresolved"


# ---------------------------------------------------------------------------
# C. Idempotent upsert -- no duplicate UI rows
# ---------------------------------------------------------------------------

def test_repeated_recording_upserts_not_duplicates(library):
    _root, db_path = library
    track_id = _track_id(db_path)

    quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")
    second = quality_findings_service.record_audio_decode_warning(track_id, 125.30, "Header missing")

    review = quality_review_service.get_review()
    matching = [item for item in review["items"] if item["reason_code"] == "recoverable_audio_decode_warning"]
    assert len(matching) == 1
    assert second["occurrence_count"] == 2


# ---------------------------------------------------------------------------
# D. Decision persistence across an ffprobe refresh
# ---------------------------------------------------------------------------

def test_durable_decision_persists_across_ffprobe_refresh(library, monkeypatch):
    _root, db_path = library
    track_id = _track_id(db_path)
    finding = quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    updated = quality_review_service.update_decision(track_id, "reviewed", "Checked locally", finding["finding_key"])
    saved = next(item for item in updated["items"] if item["finding_key"] == finding["finding_key"])
    assert saved["decision"] == "reviewed"
    assert saved["note"] == "Checked locally"

    monkeypatch.setattr(
        quality_review_service.analysis_jobs_service, "preview",
        lambda job_type: {"quality_probes": [], "warnings": [], "summary": {"total_tracks_checked": 1}},
    )
    quality_review_service.refresh_preview()

    review = quality_review_service.get_review()
    reread = next(item for item in review["items"] if item["finding_key"] == finding["finding_key"])
    assert reread["decision"] == "reviewed"
    assert reread["note"] == "Checked locally"


# ---------------------------------------------------------------------------
# E. Multiple findings on one track are addressed unambiguously
# ---------------------------------------------------------------------------

def test_multiple_findings_same_track_decisions_do_not_cross_contaminate(library, monkeypatch):
    _root, db_path = library
    track_id = _track_id(db_path)

    # One ffprobe (snapshot) finding...
    monkeypatch.setattr(
        quality_review_service.analysis_jobs_service, "preview",
        lambda job_type: {
            "quality_probes": [{
                "track_id": track_id, "filename": "malformed.mp3", "status": "probe_ok",
                "codec": "mp3", "bitrate_kbps": 96, "duration_sec": 200.0,
            }],
            "warnings": [], "summary": {"total_tracks_checked": 1},
        },
    )
    quality_review_service.refresh_preview()
    # ...plus one durable BPM finding, on the same track.
    durable = quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    review = quality_review_service.get_review()
    assert len(review["items"]) == 2
    ffprobe_key = f"ffprobe:{track_id}"

    quality_review_service.update_decision(track_id, "ignore", "", ffprobe_key)
    after_ffprobe = quality_review_service.get_review()
    by_key = {item["finding_key"]: item for item in after_ffprobe["items"]}
    assert by_key[ffprobe_key]["decision"] == "ignore"
    assert by_key[durable["finding_key"]]["decision"] == "unresolved"

    quality_review_service.update_decision(track_id, "reviewed", "", durable["finding_key"])
    after_durable = quality_review_service.get_review()
    by_key = {item["finding_key"]: item for item in after_durable["items"]}
    assert by_key[ffprobe_key]["decision"] == "ignore"
    assert by_key[durable["finding_key"]]["decision"] == "reviewed"


def test_finding_key_for_wrong_track_is_rejected(library):
    _root, db_path = library
    track_id = _track_id(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, status) "
            "VALUES ('/inbox/other.mp3', 'other.mp3', 'Artist', 'Other', 'House', 'ok')"
        )
        other_track_id = conn.execute("SELECT id FROM tracks WHERE filename = 'other.mp3'").fetchone()[0]

    finding = quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")
    with pytest.raises(LookupError):
        quality_review_service.update_decision(other_track_id, "reviewed", "", finding["finding_key"])


# ---------------------------------------------------------------------------
# F. Orphan track safety
# ---------------------------------------------------------------------------

def test_orphaned_finding_does_not_crash_get_review(library):
    _root, db_path = library
    track_id = _track_id(db_path)
    quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    review = quality_review_service.get_review()
    assert review["items"] == []
    assert review["summary"]["findings"] == 0


# ---------------------------------------------------------------------------
# Needs Review integration
# ---------------------------------------------------------------------------

def test_needs_review_surfaces_unresolved_recovery_and_failure_findings(library):
    _root, db_path = library
    track_id = _track_id(db_path)
    quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, status) "
            "VALUES ('/inbox/broken.mp3', 'broken.mp3', 'Artist', 'Broken', 'House', 'ok')"
        )
        failed_track_id = conn.execute("SELECT id FROM tracks WHERE filename = 'broken.mp3'").fetchone()[0]
    quality_findings_service.record_audio_decode_failure(failed_track_id, "Invalid data found when processing input")

    result = needs_review_service.list_items("QUALITY")
    reason_codes = {item["reason_code"]: item for item in result["items"]}
    assert reason_codes["recoverable_audio_decode_warning"]["severity"] == "MEDIUM"
    assert reason_codes["audio_decode_failed"]["severity"] == "HIGH"
    assert result["counts"]["QUALITY"] == 2


def test_needs_review_hides_reviewed_and_ignored_findings(library):
    _root, db_path = library
    track_id = _track_id(db_path)
    finding = quality_findings_service.record_audio_decode_warning(track_id, 125.27, "Header missing")

    quality_review_service.update_decision(track_id, "ignore", "", finding["finding_key"])
    result = needs_review_service.list_items("QUALITY")
    assert result["counts"]["QUALITY"] == 0

    quality_findings_service.record_audio_decode_failure(track_id + 1000, "n/a")  # no matching track: orphan, ignored safely
    assert needs_review_service.list_items("QUALITY")["counts"]["QUALITY"] == 0
