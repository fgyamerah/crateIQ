from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.app.api.routes.reviews import BulkSignalRequest, Update, preview_signals, update
from backend.app.services import track_review_service
from backend.app.services import track_service
from backend.app.services import workspace_service


def _root(tmp_path, name: str):
    root = tmp_path / name
    workspace_service.configure_workspace(root)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.executemany(
            "INSERT INTO tracks(id, filepath, filename, artist, title, status, storage_zone) VALUES (?, ?, ?, ?, ?, 'ok', 'LIBRARY')",
            [(1, str(root / "one.mp3"), "one.mp3", "One", "First"), (2, str(root / "two.mp3"), "two.mp3", "Two", "Second")],
        )
    return root


def _add_track(root, track_id: int, *, zone: str):
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks(
                id, filepath, filename, artist, title, status, storage_zone
            ) VALUES (?, ?, ?, ?, ?, 'ok', ?)""",
            (
                track_id,
                str(root / zone.title() / f"{track_id}.mp3"),
                f"{track_id}.mp3",
                "Inbox Artist",
                "Inbox Title",
                zone,
            ),
        )


def test_favorites_include_inbox_tracks_and_unfavorite_without_removing_them(tmp_path, monkeypatch):
    root = _root(tmp_path, "library")
    _add_track(root, 3, zone="INBOX")
    _add_track(root, 4, zone="INBOX")
    _add_track(root, 5, zone="QUARANTINE")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))

    track_review_service.update(root, 1, favorite=True)
    track_review_service.update(root, 3, favorite=True)
    track_review_service.update(root, 5, favorite=True)
    favorites, total = track_service.list_tracks(favorite_only=True, sort="favorite", order="desc")

    assert total == 2
    assert [track.id for track in favorites] == [1, 3]
    assert len({track.id for track in favorites}) == 2
    assert next(track for track in favorites if track.id == 3).storage_zone == "INBOX"
    assert all(track.id != 4 for track in favorites)

    track_review_service.update(root, 3, favorite=False)
    remaining, remaining_total = track_service.list_tracks(favorite_only=True)
    assert remaining_total == 1
    assert [track.id for track in remaining] == [1]
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT storage_zone FROM tracks WHERE id=3").fetchone()
    assert row == ("INBOX",)


def test_favorites_are_isolated_to_the_active_library_root(tmp_path, monkeypatch):
    first = _root(tmp_path, "first")
    second = _root(tmp_path, "second")
    _add_track(first, 3, zone="INBOX")
    track_review_service.update(first, 1, favorite=True)
    track_review_service.update(first, 3, favorite=True)

    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(first))
    first_items, first_total = track_service.list_tracks(favorite_only=True)
    assert first_total == 2
    assert {track.id for track in first_items} == {1, 3}

    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(second))
    second_items, second_total = track_service.list_tracks(favorite_only=True)
    assert second_total == 0
    assert second_items == []


def test_single_signals_are_independent_and_history_is_preserved(tmp_path):
    root = _root(tmp_path, "library")
    saved = track_review_service.update(root, 1, review_status="reviewed", notes="Keep this note")
    assert saved["review_status"] == "reviewed"

    saved = track_review_service.update(root, 1, rating=5)
    assert saved["rating"] == 5 and saved["favorite"] is False
    saved = track_review_service.update(root, 1, favorite=True)
    assert saved["rating"] == 5 and saved["favorite"] is True
    saved = track_review_service.update(root, 1, favorite=False)
    assert saved["rating"] == 5 and saved["favorite"] is False
    saved = track_review_service.update(root, 1, favorite=True)
    saved = track_review_service.update(root, 1, rating=None)
    assert saved["rating"] is None and saved["favorite"] is True
    assert saved["review_status"] == "reviewed" and saved["notes"] == "Keep this note"
    assert track_review_service.update(root, 1, rating=0)["rating"] is None


def test_bulk_preview_apply_and_safety(tmp_path, monkeypatch):
    root = _root(tmp_path, "library")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    track_review_service.update(root, 1, favorite=True, rating=2)
    preview = track_review_service.bulk_preview(
        root, [1, 2], operations={"rating": {"operation": "set", "value": 4}, "favorite": {"operation": "set", "value": True}},
    )
    assert preview["changeable_count"] == 2
    assert preview["fields"]["rating"]["already_matching_count"] == 0
    assert preview["fields"]["favorite"]["already_matching_count"] == 1
    result = track_review_service.bulk_apply(
        root, [1, 2], operations={"rating": {"operation": "set", "value": 4}, "favorite": {"operation": "set", "value": True}}, confirm=True,
    )
    assert result["succeeded_count"] == 2
    assert track_review_service.summaries(root, [1, 2])[2]["favorite"] is True
    rated, rated_total = track_service.list_tracks(sort="rating", order="desc", rating_filter="4plus")
    assert rated_total == 2 and {track.id for track in rated} == {1, 2}
    favorites, favorite_total = track_service.list_tracks(sort="favorite", order="desc", favorite_only=True)
    assert favorite_total == 2 and {track.id for track in favorites} == {1, 2}

    with pytest.raises(ValueError, match="unique"):
        track_review_service.bulk_preview(root, [1, 1], operations={"favorite": {"operation": "set", "value": True}})
    with pytest.raises(ValueError, match="integer"):
        track_review_service.bulk_preview(root, [1], operations={"rating": {"operation": "set", "value": 0}})
    with pytest.raises(ValueError, match="limited"):
        track_review_service.bulk_preview(root, list(range(1, 202)), operations={"favorite": {"operation": "set", "value": True}})


def test_api_rejects_invalid_values_and_isolates_active_root(tmp_path, monkeypatch):
    first = _root(tmp_path, "first")
    second = _root(tmp_path, "second")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(first))
    assert update(Update(rating=5, favorite=True), 1)["favorite"] is True
    with pytest.raises(ValidationError):
        Update(rating=6)
    with pytest.raises(ValidationError):
        Update(rating=2.5)
    with pytest.raises(ValidationError):
        Update.model_validate({"favorite": True, "unexpected": True})
    with pytest.raises(HTTPException):
        preview_signals(BulkSignalRequest(track_ids=[1, 1], operations={"favorite": {"operation": "set", "value": True}}))
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(second))
    assert track_review_service.summaries(second, [1])[1]["rating"] is None
    assert track_review_service.summaries(second, [1])[1]["favorite"] is False
