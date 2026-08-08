"""
Targeted tests for the Cycle 9 storage_zone filter in track_service.list_tracks
and get_stats, including migration safety against a pre-Cycle-9 processed.db
that predates the storage_zone column.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.services import library_setup_service, track_service, workspace_service


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture()
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    workspace_service.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    return root


def _insert_track(root: Path, filename: str, zone: str | None) -> None:
    path = root / "Inbox" / filename if zone == "INBOX" else root / filename
    _write(path)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        if zone is None:
            conn.execute(
                "INSERT INTO tracks (filepath, filename, artist, title, status, processed_at, pipeline_ver) "
                "VALUES (?, ?, 'A', 'T', 'pending', '2026-01-01T00:00:00Z', 'test')",
                (str(path), filename),
            )
        else:
            conn.execute(
                "INSERT INTO tracks (filepath, filename, artist, title, status, processed_at, pipeline_ver, storage_zone) "
                "VALUES (?, ?, 'A', 'T', 'pending', '2026-01-01T00:00:00Z', 'test', ?)",
                (str(path), filename, zone),
            )


def test_library_zone_excludes_inbox(managed_root):
    _insert_track(managed_root, "library-track.mp3", "LIBRARY")
    _insert_track(managed_root, "inbox-track.mp3", "INBOX")

    tracks, total = track_service.list_tracks(storage_zone="LIBRARY")
    assert total == 1
    assert tracks[0].filename == "library-track.mp3"


def test_inbox_zone_excludes_library(managed_root):
    _insert_track(managed_root, "library-track.mp3", "LIBRARY")
    _insert_track(managed_root, "inbox-track.mp3", "INBOX")

    tracks, total = track_service.list_tracks(storage_zone="INBOX")
    assert total == 1
    assert tracks[0].filename == "inbox-track.mp3"


def test_default_storage_zone_is_library_for_legacy_rows(managed_root):
    """Rows inserted without a storage_zone (pre-Cycle-9 style INSERT) default to LIBRARY."""
    _insert_track(managed_root, "legacy-track.mp3", None)

    tracks, total = track_service.list_tracks(storage_zone="LIBRARY")
    assert total == 1
    assert tracks[0].filename == "legacy-track.mp3"


def test_migration_backfills_pre_cycle9_db_without_storage_zone_column(tmp_path, monkeypatch):
    """
    A DB created before Cycle 9 has no storage_zone column at all. Filtering
    by zone must migrate it on the fly rather than silently returning an
    empty Library (which would look like data loss to the user).
    """
    root = tmp_path / "legacy-db"
    root.mkdir()
    (root / "logs").mkdir()
    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                artist TEXT, title TEXT, album TEXT, genre TEXT,
                bpm REAL, key_musical TEXT, key_camelot TEXT,
                duration_sec REAL, bitrate_kbps INTEGER, filesize_bytes INTEGER,
                status TEXT NOT NULL DEFAULT 'pending', error_msg TEXT,
                processed_at TEXT, pipeline_ver TEXT, quality_tier TEXT,
                parse_confidence TEXT
            )
        """)
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, status, processed_at, pipeline_ver) "
            "VALUES (?, 'old.mp3', 'A', 'T', 'pending', '2026-01-01T00:00:00Z', 'legacy')",
            (str(root / "old.mp3"),),
        )
    _write(root / "old.mp3")

    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))

    tracks, total = track_service.list_tracks(storage_zone="LIBRARY")
    assert total == 1
    assert tracks[0].filename == "old.mp3"

    with sqlite3.connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    assert "storage_zone" in cols


def test_get_stats_zone_filter(managed_root):
    _insert_track(managed_root, "library-track.mp3", "LIBRARY")
    _insert_track(managed_root, "inbox-track.mp3", "INBOX")

    library_stats = track_service.get_stats(storage_zone="LIBRARY")
    assert library_stats.total == 1

    inbox_stats = track_service.get_stats(storage_zone="INBOX")
    assert inbox_stats.total == 1
