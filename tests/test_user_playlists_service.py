from __future__ import annotations

import sqlite3

from backend.app.services import user_playlist_service


def _library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE tracks (
                id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT,
                artist TEXT, title TEXT, genre TEXT, bpm REAL,
                key_musical TEXT, key_camelot TEXT, duration_sec REAL,
                bitrate_kbps INTEGER, status TEXT, quality_tier TEXT,
                parse_confidence TEXT, storage_zone TEXT
            )"""
        )
        conn.executemany(
            "INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, str(root / "Library" / "a.mp3"), "a.mp3", "Alpha", "First", "House", 120, "A minor", "8A", 180, 320, "ok", "HIGH", "HIGH", "LIBRARY"),
                (2, str(root / "Inbox" / "b.mp3"), "b.mp3", "Beta", "Second", "House", 124, "B minor", "10A", 190, 320, "ok", "HIGH", "HIGH", "INBOX"),
            ],
        )
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    return root


def test_user_playlist_service_preserves_many_to_many_order_and_review_projection(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    first = user_playlist_service.create_playlist("Warm Up", "First hour")
    second = user_playlist_service.create_playlist("Peak", None)

    preview = user_playlist_service.preview_add(first.id, [1, 2])
    assert preview is not None and preview.will_add_count == 2
    applied = user_playlist_service.add_tracks(first.id, [1, 2])
    assert applied.added_count == 2
    assert user_playlist_service.add_tracks(second.id, [2]).added_count == 1

    detail = user_playlist_service.get_playlist(first.id)
    assert detail is not None
    assert [item.track_id for item in detail.tracks] == [1, 2]
    assert user_playlist_service.reorder_tracks(first.id, [2, 1]) == "reordered"
    reordered = user_playlist_service.get_playlist(first.id)
    assert reordered is not None
    assert [(item.track_id, item.position) for item in reordered.tracks] == [(2, 1), (1, 2)]
    assert user_playlist_service.remove_track(first.id, 1) == "removed"
    with sqlite3.connect(tmp_path / "library" / "logs" / "processed.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 2


def test_user_playlist_service_rejects_invalid_reorder_and_reports_missing_active_track(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    playlist = user_playlist_service.create_playlist("Only Local", None)
    preview = user_playlist_service.preview_add(playlist.id, [999])
    assert preview is not None
    assert preview.missing_count == 1
    result = user_playlist_service.add_tracks(playlist.id, [999])
    assert result.added_count == 0
    assert result.missing_count == 1
    assert user_playlist_service.reorder_tracks(playlist.id, [1]) == "invalid_order"
