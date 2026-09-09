"""Focused contract tests for inline Inbox enrichment review.

These tests verify that the Inbox Inspector reads and acts on the *same*
enrichment_review_service snapshot/decision queue as the specialist Enrichment
Review page -- no new persistence, no provider/network calls, no tag writes.

State-transition notes (verified against authoritative precedence in
workspace_service._preparation_states_for_rows):

  * WRITE_BLOCKED > NEEDS_ATTENTION > REVIEW > UNSAVED > READY.
  * A track is REVIEW only when artist/title/genre are all present, no write
    blocker, and it has pending review entries.
  * apply_selected() only ever fills *empty* artist/title/genre fields; it
    never overwrites a non-empty value. Because a missing artist/title/genre
    is itself a NEEDS_ATTENTION reason, the transition "accept -> UNSAVED"
    starts from NEEDS_ATTENTION (missing field) rather than REVIEW.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.services import (
    enrichment_review_service,
    field_provenance_service,
    tag_write_service,
    workspace_service,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    workspace_service.configure_workspace(root)

    file_tags: dict[Path, dict[str, str]] = {}
    monkeypatch.setattr(
        tag_write_service,
        "_read_file_tags",
        lambda path: file_tags.get(Path(path), {field: "" for field in ("artist", "title", "album", "genre")}),
    )
    return root, file_tags


def _seed(
    env,
    *,
    filename: str = "track.mp3",
    artist: str | None = "Artist",
    title: str | None = "Title",
    genre: str | None = "House",
    content: bytes = b"audio",
) -> int:
    root, file_tags = env
    path = root / "Inbox" / filename
    path.write_bytes(content)
    file_tags[path] = {"artist": artist or "", "title": title or "", "album": "", "genre": genre or ""}
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        cursor = conn.execute(
            """INSERT INTO tracks (
                   filepath, filename, artist, title, album, genre, bpm,
                   key_camelot, status, parse_confidence, storage_zone
               ) VALUES (?, ?, ?, ?, '', ?, 122.0, '8A', 'pending', 'HIGH', 'INBOX')""",
            (str(path), filename, artist, title, genre),
        )
        return int(cursor.lastrowid)


def _queue_review(env, track_id: int, **overrides) -> str:
    """Insert one pending review item through the real shared persistence."""
    item = {
        "suggestion_id": "suggestion-1",
        "track_id": track_id,
        "source_id": "consensus_review",
        "confidence": "medium",
        "reason": "Provider consensus needs review: genre: non_authority_genre_evidence (MEDIUM).",
        "filename": "track.mp3",
        "relative_path": "Inbox/track.mp3",
        "current_fields": {"artist": "Artist", "title": "Title", "genre": None},
        "suggested_fields": {"genre": "Deep House"},
        "allowed_fields": ["genre"],
        "evidence": {"genre": ["beets: Deep House"]},
    }
    item.update(overrides)
    enrichment_review_service.queue_consensus_suggestions([item])
    return item["suggestion_id"]


def _state(env, track_id: int) -> dict:
    return workspace_service.inbox_preparation_states(env[0], [track_id])[track_id]


def _db_row(env, track_id: int) -> sqlite3.Row:
    with sqlite3.connect(env[0] / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


# ---------------------------------------------------------------------------
# Read surface: shared persistence, actionable-only
# ---------------------------------------------------------------------------


def test_inbox_reads_track_review_from_shared_persistence(env):
    track_id = _seed(env, genre=None)
    _queue_review(env, track_id, suggestion_id="sug-1")
    with TestClient(backend_main.app) as client:
        response = client.get(f"/api/workspace/inbox/tracks/{track_id}/enrichment-review")
    assert response.status_code == 200
    body = response.json()
    assert body["track_id"] == track_id
    assert body["count"] == 1
    assert body["items"][0]["suggestion_id"] == "sug-1"
    assert body["items"][0]["decision"] == "pending"


def test_only_actionable_pending_items_appear(env):
    track_id = _seed(env)
    pending = _queue_review(env, track_id, suggestion_id="sug-pending", confidence="medium", source_id="beets")
    ignored = _queue_review(env, track_id, suggestion_id="sug-ignored", confidence="low", source_id="musicbrainz")
    applied = _queue_review(env, track_id, suggestion_id="sug-applied", confidence="medium", source_id="discogs")

    enrichment_review_service.update_suggestion(track_id, ignored, "ignored", "", {})
    enrichment_review_service.update_suggestion(track_id, applied, "applied", "", {"genre": "Deep House"})

    body = enrichment_review_service.get_track_review(track_id)
    assert [item["suggestion_id"] for item in body["items"]] == [pending]


def test_resolved_items_disappear(env):
    track_id = _seed(env)
    suggestion_id = _queue_review(env, track_id)
    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    body = enrichment_review_service.get_track_review(track_id)
    assert body["count"] == 0
    assert body["items"] == []

# ---------------------------------------------------------------------------
# Decisions reuse the existing mutation contracts
# ---------------------------------------------------------------------------


def test_use_suggested_uses_existing_apply_contract(env):
    track_id = _seed(env, genre=None)
    suggestion_id = _queue_review(env, track_id)
    enrichment_review_service.update_suggestion(
        track_id, suggestion_id, "pending", "", {"genre": "Deep House"},
    )
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    assert result["applied"] == 1
    assert result["skipped"] == 0
    assert result["failed"] == 0


def test_keep_current_uses_existing_contract_and_does_not_modify_metadata(env):
    track_id = _seed(env, genre="House")
    suggestion_id = _queue_review(
        env, track_id,
        suggested_fields={"genre": "Deep House"}, allowed_fields=["genre"],
        current_fields={"artist": "Artist", "title": "Title", "genre": "House"},
    )
    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    row = _db_row(env, track_id)
    assert row["genre"] == "House"
    review = enrichment_review_service.get_review()
    decided = next(item for item in review["items"] if item["suggestion_id"] == suggestion_id)
    assert decided["decision"] == "ignored"


def test_accept_updates_working_metadata_without_tag_write(env, monkeypatch):
    track_id = _seed(env, genre=None)
    path = env[0] / "Inbox" / "track.mp3"
    before = path.read_bytes()
    written: list[str] = []

    def forbidden_write(*args, **kwargs):  # pragma: no cover - must never run
        written.append(str(args[0]))
        raise AssertionError("review apply must not write tags")

    monkeypatch.setattr(tag_write_service, "_write_easy_tags", forbidden_write)
    suggestion_id = _queue_review(env, track_id)
    enrichment_review_service.update_suggestion(
        track_id, suggestion_id, "pending", "", {"genre": "Deep House"},
    )
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    assert result["applied"] == 1
    assert _db_row(env, track_id)["genre"] == "Deep House"
    assert path.read_bytes() == before
    assert written == []



def test_accept_records_provider_provenance(env):
    track_id = _seed(env, genre=None)
    suggestion_id = _queue_review(env, track_id)
    enrichment_review_service.update_suggestion(
        track_id, suggestion_id, "pending", "", {"genre": "Deep House"},
    )
    enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    current = field_provenance_service.current_for_track(track_id)
    assert current["genre"]["origin"] == "provider"
    assert current["genre"]["source"] == "consensus_review"
    assert current["genre"]["confidence"] == "MEDIUM"
    assert current["genre"]["value"] == "Deep House"


def test_use_suggested_applies_field_subset_only(env):
    track_id = _seed(env, artist=None, title=None, genre=None)
    suggestion_id = _queue_review(
        env, track_id,
        suggested_fields={"artist": "New Artist", "title": "New Title"},
        allowed_fields=["artist", "title"],
        current_fields={"artist": None, "title": None, "genre": None},
    )
    enrichment_review_service.update_suggestion(
        track_id, suggestion_id, "pending", "", {"artist": "New Artist"},
    )
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"artist": "New Artist"}}],
        confirm=True,
    )
    assert result["applied"] == 1
    row = _db_row(env, track_id)
    assert row["artist"] == "New Artist"
    assert row["title"] is None


# ---------------------------------------------------------------------------
# Preparation-state refresh after decisions (authoritative precedence)
# ---------------------------------------------------------------------------


def test_accept_refreshes_state_to_unsaved_when_db_and_file_differ(env):
    track_id = _seed(env, genre=None)
    suggestion_id = _queue_review(env, track_id)
    assert _state(env, track_id)["status"] == "NEEDS_ATTENTION"  # missing genre precedes review

    enrichment_review_service.update_suggestion(
        track_id, suggestion_id, "pending", "", {"genre": "Deep House"},
    )
    enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    state = _state(env, track_id)
    assert state["status"] == "UNSAVED"
    assert "genre" in state["pending_fields"]


def test_review_resolves_to_ready_when_keep_current_and_synchronized(env):
    track_id = _seed(env, genre="House")
    suggestion_id = _queue_review(
        env, track_id,
        confidence="low", suggested_fields={}, allowed_fields=[],
        evidence={"genre": ["discogs: Afro Tech", "beets: Afro House"]},
        reason="Provider consensus needs review: genre: genre_provider_disagreement (CONFLICT).",
    )
    assert _state(env, track_id)["status"] == "REVIEW"

    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    state = _state(env, track_id)
    assert state["status"] == "READY"
    assert state["review_count"] == 0


def test_missing_required_metadata_remains_needs_attention_after_resolution(env):
    track_id = _seed(env, genre=None)
    suggestion_id = _queue_review(env, track_id)
    assert _state(env, track_id)["status"] == "NEEDS_ATTENTION"

    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    state = _state(env, track_id)
    assert state["status"] == "NEEDS_ATTENTION"
    assert any(reason["code"] == "genre_missing" for reason in state["reasons"])


def test_write_blocked_persists_after_review_resolution(env):
    track_id = _seed(env, filename="track.wav")
    suggestion_id = _queue_review(
        env, track_id,
        suggested_fields={"artist": "New Artist"}, allowed_fields=["artist"],
        current_fields={"artist": "Artist", "title": "Title", "genre": "House"},
    )
    assert _state(env, track_id)["status"] == "WRITE_BLOCKED"

    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    state = _state(env, track_id)
    assert state["status"] == "WRITE_BLOCKED"


# ---------------------------------------------------------------------------
# Stale / already-resolved handling and read-only guarantees
# ---------------------------------------------------------------------------


def test_stale_suggestion_is_not_found_and_does_not_apply(env):
    track_id = _seed(env, genre=None)
    _queue_review(env, track_id)  # ensures a snapshot exists
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": "does-not-exist", "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    assert result["applied"] == 0
    assert result["failed"] == 1
    assert "not found" in result["warnings"][0]


def test_apply_requires_saved_selection(env):
    track_id = _seed(env, genre=None)
    suggestion_id = _queue_review(env, track_id)
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    assert result["applied"] == 0
    assert result["failed"] == 1
    assert "Save selected fields before applying" in result["warnings"][0]


def test_no_duplicate_review_persistence(env):
    _seed(env)
    with sqlite3.connect(env[0] / "logs" / "processed.db") as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    review_tables = {name for name in tables if "review" in name or "enrichment" in name}
    assert review_tables <= {"enrichment_review_snapshots", "enrichment_review_decisions", "metadata_lookup_cache"}


def test_specialist_review_sees_inbox_decisions(env):
    track_id = _seed(env)
    suggestion_id = _queue_review(env, track_id)
    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    review = enrichment_review_service.get_review()
    decided = next(item for item in review["items"] if item["suggestion_id"] == suggestion_id)
    assert decided["decision"] == "ignored"


def test_inbox_sees_decisions_made_by_specialist_workflow(env):
    track_id = _seed(env)
    suggestion_id = _queue_review(env, track_id)
    enrichment_review_service.update_suggestion(track_id, suggestion_id, "ignored", "", {})
    body = enrichment_review_service.get_track_review(track_id)
    assert body["count"] == 0


def test_review_read_and_apply_make_no_network_calls(env, monkeypatch):
    from backend.app.services import musicbrainz_client, provider_routing_service

    def forbidden(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("review surface must not make provider/network calls")

    monkeypatch.setattr(musicbrainz_client, "match_track_candidates", forbidden)
    monkeypatch.setattr(musicbrainz_client, "search_recordings", forbidden)
    monkeypatch.setattr(provider_routing_service, "gather_evidence", forbidden)

    track_id = _seed(env, genre=None)
    suggestion_id = _queue_review(env, track_id)
    assert enrichment_review_service.get_track_review(track_id)["count"] == 1
    enrichment_review_service.update_suggestion(
        track_id, suggestion_id, "pending", "", {"genre": "Deep House"},
    )
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": suggestion_id, "fields": {"genre": "Deep House"}}],
        confirm=True,
    )
    assert result["applied"] == 1
