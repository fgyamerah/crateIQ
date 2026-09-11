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
