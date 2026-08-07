"""Targeted tests for the read-only orphan/stale-path/quarantine findings contract.

Covers Cycle 4 Stage 2: findings must be truthful, bounded, path-contained,
and produce zero filesystem or database mutation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.services import settings_service


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
            artist TEXT, title TEXT, genre TEXT, bpm REAL,
            key_musical TEXT, key_camelot TEXT, duration_sec REAL,
            bitrate_kbps INTEGER, filesize_bytes INTEGER,
            status TEXT NOT NULL DEFAULT 'pending', error_msg TEXT,
            processed_at TEXT, pipeline_ver TEXT, parse_confidence TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", tmp_path / "local" / "crateiq.env")
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _create_tracks_db(root)
    with TestClient(backend_main.app) as test_client:
        yield test_client, root


def test_findings_empty_before_library_index_exists(tmp_path, monkeypatch):
    root = tmp_path / "uninitialized_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", tmp_path / "local" / "crateiq.env")
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    with TestClient(backend_main.app) as test_client:
        response = test_client.get("/api/reconciliation/findings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["findings"] == []
    assert payload["summary"] == {"indexed_missing_file": 0, "untracked_file": 0, "stale_path": 0, "path_candidate": 0}
    assert "Initialize the local library index" in payload["message"]


def test_findings_report_missing_and_untracked_files_with_relative_paths_only(client):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    missing_path = root / "library" / "house" / "ghost.mp3"
    untracked_path = root / "library" / "house" / "new_track.mp3"
    untracked_path.parent.mkdir(parents=True, exist_ok=True)
    untracked_path.write_bytes(b"untracked-audio-bytes")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO tracks (filepath, filename, status, filesize_bytes) VALUES (?, ?, 'ok', ?)",
            (str(missing_path), "ghost.mp3", 4321),
        )
        before = conn.execute("SELECT id, filepath FROM tracks").fetchall()

    response = test_client.get("/api/reconciliation/findings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["indexed_missing_file"] == 1
    assert payload["summary"]["untracked_file"] == 1

    missing_finding = next(f for f in payload["findings"] if f["finding_type"] == "indexed_missing_file")
    assert missing_finding["db_side"]["relative_path"] == "library/house/ghost.mp3"
    assert missing_finding["db_side"]["filename"] == "ghost.mp3"
    assert missing_finding["filesystem_side"] is None
    assert not missing_finding["db_side"]["relative_path"].startswith(str(root))
    assert not str(root) in missing_finding["finding_id"]

    untracked_finding = next(f for f in payload["findings"] if f["finding_type"] == "untracked_file")
    assert untracked_finding["filesystem_side"]["relative_path"] == "library/house/new_track.mp3"
    assert untracked_finding["filesystem_side"]["size_bytes"] == len(b"untracked-audio-bytes")
    assert untracked_finding["db_side"] is None

    # Read-only: nothing in the DB or on disk changed.
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT id, filepath FROM tracks").fetchall()
    assert after == before
    assert untracked_path.read_bytes() == b"untracked-audio-bytes"
    assert not missing_path.exists()


def test_findings_deterministic_ids_are_stable_across_calls(client):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    missing_path = root / "library" / "house" / "ghost.mp3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks (filepath, filename, status) VALUES (?, ?, 'ok')", (str(missing_path), "ghost.mp3"))

    first = test_client.get("/api/reconciliation/findings").json()
    second = test_client.get("/api/reconciliation/findings").json()
    assert {f["finding_id"] for f in first["findings"]} == {f["finding_id"] for f in second["findings"]}


def test_findings_report_stale_path_superseded_by_existing_path(client):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    old_path = root / "library" / "old" / "gamma.mp3"
    current_path = root / "library" / "house" / "gamma.mp3"
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_bytes(b"stale-path-fixture-bytes")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE processed_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT, filepath TEXT,
                file_size INTEGER, file_mtime TEXT, status TEXT, processed_at TEXT, reason TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO processed_state (stage, filepath, file_size, status) VALUES (?, ?, ?, 'active')",
            ("library-organize", str(old_path), current_path.stat().st_size),
        )
        # The stale-path matcher only considers other processed_state rows whose
        # path still exists on disk as replacement candidates.
        conn.execute(
            "INSERT INTO processed_state (stage, filepath, file_size, status) VALUES (?, ?, ?, 'active')",
            ("library-organize", str(current_path), current_path.stat().st_size),
        )

    response = test_client.get("/api/reconciliation/findings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["stale_path"] == 1
    stale = next(f for f in payload["findings"] if f["finding_type"] == "stale_path")
    assert stale["db_side"]["relative_path"] == "library/old/gamma.mp3"
    assert stale["filesystem_side"]["relative_path"] == "library/house/gamma.mp3"
    assert stale["evidence"]["reason"] == "superseded_by_existing_path"


def test_findings_malformed_database_reports_warning_not_crash(client):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    db_path.write_bytes(b"not a real sqlite database")

    response = test_client.get("/api/reconciliation/findings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["findings"] == []
    assert payload["warnings"]


def test_findings_no_write_endpoint_exists_for_findings_or_quarantine(client):
    test_client, _root = client
    assert test_client.post("/api/reconciliation/findings").status_code in (404, 405)
    assert test_client.post("/api/reconciliation/quarantine").status_code in (404, 405)


def test_quarantine_listing_empty_then_populated_and_never_offers_restore(client):
    test_client, root = client

    empty = test_client.get("/api/reconciliation/quarantine")
    assert empty.status_code == 200
    empty_payload = empty.json()
    assert empty_payload["items"] == []
    assert empty_payload["supported"] is True
    assert "No quarantine directory exists" in empty_payload["message"]

    quarantine_dir = root / ".BIN" / "QUARANTINE" / "house"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    (quarantine_dir / "old_copy.mp3").write_bytes(b"quarantined-bytes")

    populated = test_client.get("/api/reconciliation/quarantine")
    assert populated.status_code == 200
    payload = populated.json()
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["relative_path"] == ".BIN/QUARANTINE/house/old_copy.mp3"
    assert item["size_bytes"] == len(b"quarantined-bytes")
    assert item["restore_supported"] is False
    assert item["original_relative_path"] is None
    assert item["reason"] is None
    assert item["operation_id"] is None
    assert item["quarantined_at"] is None
    assert "restore is not offered" in payload["message"]
    # Read-only listing must never touch the quarantined file itself.
    assert (quarantine_dir / "old_copy.mp3").read_bytes() == b"quarantined-bytes"


def test_quarantine_listing_rejects_symlink_escape_outside_root(client, tmp_path):
    test_client, root = client
    outside_secret = tmp_path / "outside_root_secret.mp3"
    outside_secret.write_bytes(b"should-never-be-exposed")
    quarantine_dir = root / ".BIN" / "QUARANTINE"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    escape_link = quarantine_dir / "escape.mp3"
    escape_link.symlink_to(outside_secret)

    response = test_client.get("/api/reconciliation/quarantine")
    assert response.status_code == 200
    payload = response.json()
    assert all("outside_root_secret" not in item["relative_path"] for item in payload["items"])
    assert all(str(tmp_path) not in item["relative_path"] for item in payload["items"])
