"""Focused contract tests for the read-only Inbox preparation projection."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.core.library_key import current_library_key
from backend.app.services import enrichment_review_service, tag_write_service, workspace_service


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
    bpm: float | None = 122.0,
    key: str | None = "8A",
    status: str = "pending",
    parse_confidence: str = "HIGH",
    content: bytes = b"audio",
) -> int:
    root, file_tags = env
    path = root / "Inbox" / filename
    path.write_bytes(content)
    file_tags[path] = {
        "artist": artist or "",
        "title": title or "",
        "album": "",
        "genre": genre or "",
    }
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        cursor = conn.execute(
            """INSERT INTO tracks (
                   filepath, filename, artist, title, album, genre, bpm,
                   key_camelot, status, parse_confidence, storage_zone
               ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'INBOX')""",
            (str(path), filename, artist, title, genre, bpm, key, status, parse_confidence),
        )
        return int(cursor.lastrowid)


def _state(env, track_id: int) -> dict:
    return workspace_service.inbox_preparation_states(env[0], [track_id])[track_id]


def _queue_review(env, track_id: int, *, decision: str | None = None) -> None:
    root, _ = env
    item = {
        "suggestion_id": "suggestion-1",
        "track_id": track_id,
        "source_id": "consensus_review",
        "confidence": "low",
        "reason": "Provider consensus needs review: genre_provider_disagreement (CONFLICT).",
        "filename": "track.mp3",
        "relative_path": "Inbox/track.mp3",
        "current_fields": {"artist": "Artist", "title": "Title", "genre": "House"},
        "suggested_fields": {},
        "allowed_fields": [],
        "evidence": {"genre": ["discogs: House", "beatport: Techno"]},
    }
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS enrichment_review_snapshots "
            "(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, items_json TEXT NOT NULL, warnings_json TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS enrichment_review_decisions "
            "(snapshot_id INTEGER NOT NULL, suggestion_id TEXT NOT NULL, track_id INTEGER NOT NULL, "
            "decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', selected_fields_json TEXT NOT NULL DEFAULT '{}', "
            "updated_at TEXT NOT NULL, applied_at TEXT, PRIMARY KEY(snapshot_id, suggestion_id))"
        )
        conn.execute(
            "INSERT INTO enrichment_review_snapshots(id, created_at, items_json, warnings_json) VALUES (1, '2026-09-02', ?, '[]')",
            (json.dumps([item]),),
        )
        if decision:
            conn.execute(
                "INSERT INTO enrichment_review_decisions "
                "(snapshot_id, suggestion_id, track_id, decision, updated_at) VALUES (1, 'suggestion-1', ?, ?, '2026-09-02')",
                (track_id, decision),
            )


def test_good_supported_file_is_ready(env):
    state = _state(env, _seed(env))
    assert state["status"] == "READY"
    assert state["reasons"] == []
    assert state["promotion"]["ready"] is True


def test_inbox_api_exposes_preparation_state(env):
    track_id = _seed(env)
    with TestClient(backend_main.app) as client:
        response = client.get("/api/workspace/inbox/tracks")
    assert response.status_code == 200
    item = next(item for item in response.json()["items"] if item["id"] == track_id)
    assert item["preparation_state"]["status"] == "READY"
    assert item["preparation_state"]["status_label"] == "Ready"


def test_projection_has_no_operational_db_creation_side_effect(env):
    track_id = _seed(env)
    jobs_path = Path(backend_db.JOBS_DB_PATH)
    jobs_path.unlink()

    assert _state(env, track_id)["status"] == "READY"
    assert not jobs_path.exists()


def test_projection_batches_review_and_reads_each_file_once(env, monkeypatch):
    first_id = _seed(env, filename="first.mp3")
    second_id = _seed(env, filename="second.mp3")
    reads: list[Path] = []

    def read_tags(path: Path) -> dict[str, str]:
        resolved = Path(path)
        reads.append(resolved)
        return env[1][resolved]

    review_calls = 0

    def get_review():
        nonlocal review_calls
        review_calls += 1
        return {"items": []}

    monkeypatch.setattr(tag_write_service, "_read_file_tags", read_tags)
    monkeypatch.setattr(enrichment_review_service, "get_review", get_review)

    states = workspace_service.inbox_preparation_states(env[0], [first_id, second_id])

    assert set(states) == {first_id, second_id}
    assert reads == [env[0] / "Inbox" / "first.mp3", env[0] / "Inbox" / "second.mp3"]
    assert review_calls == 1


@pytest.mark.parametrize(
    ("field", "code"),
    [("artist", "artist_missing"), ("title", "title_missing"), ("genre", "genre_missing")],
)
def test_missing_required_metadata_needs_attention(env, field, code):
    values = {"artist": "Artist", "title": "Title", "genre": "House"}
    values[field] = None
    state = _state(env, _seed(env, **values))
    assert state["status"] == "NEEDS_ATTENTION"
    assert code in {reason["code"] for reason in state["reasons"]}


def test_approved_db_difference_is_unsaved_with_exact_pending_fields(env):
    track_id = _seed(env)
    path = env[0] / "Inbox" / "track.mp3"
    env[1][path]["artist"] = "Old Artist"
    env[1][path]["genre"] = "Techno"

    state = _state(env, track_id)

    assert state["status"] == "UNSAVED"
    assert state["pending_fields"] == ["artist", "genre"]
    assert [reason["code"] for reason in state["reasons"]] == ["artist_unsaved", "genre_unsaved"]


def test_unsupported_m4a_is_write_blocked(env):
    state = _state(env, _seed(env, filename="track.m4a"))
    assert state["status"] == "WRITE_BLOCKED"
    assert state["write"]["blocker_code"] == "unsupported_write_format"
    assert state["reasons"][0]["label"] == "M4A metadata write-back is not supported"


def test_missing_managed_file_is_write_blocked(env):
    track_id = _seed(env)
    (env[0] / "Inbox" / "track.mp3").unlink()
    state = _state(env, track_id)
    assert state["status"] == "WRITE_BLOCKED"
    assert state["write"]["blocker_code"] == "managed_file_missing"


def test_actionable_provider_conflict_is_review(env):
    track_id = _seed(env)
    _queue_review(env, track_id)
    state = _state(env, track_id)
    assert state["status"] == "REVIEW"
    assert state["review_count"] == 1
    assert state["reasons"][0]["label"] == "Metadata sources disagree on Genre"


def test_ignored_historical_review_is_not_review(env):
    track_id = _seed(env)
    _queue_review(env, track_id, decision="ignored")
    assert _state(env, track_id)["status"] == "READY"


@pytest.mark.parametrize(("bpm", "key", "warning"), [(None, "8A", "bpm_missing"), (122.0, None, "key_missing")])
def test_missing_analysis_value_is_ready_with_warning(env, bpm, key, warning):
    state = _state(env, _seed(env, bpm=bpm, key=key))
    assert state["status"] == "READY"
    assert warning in {item["code"] for item in state["warnings"]}


def test_missing_waveform_does_not_demote_ready(env):
    # No waveform operational row/cache artifact exists for this track.
    assert _state(env, _seed(env))["status"] == "READY"


def test_serious_current_error_is_not_ready(env):
    state = _state(env, _seed(env, status="error"))
    assert state["status"] == "NEEDS_ATTENTION"
    assert "current_processing_error" in {reason["code"] for reason in state["reasons"]}


def test_existing_suspicious_metadata_flag_needs_attention(env):
    state = _state(env, _seed(env, artist="DJCity promo download"))
    assert state["status"] == "NEEDS_ATTENTION"
    assert "suspicious_artist" in {reason["code"] for reason in state["reasons"]}


def test_status_precedence_is_write_attention_review_unsaved_ready(env):
    write_id = _seed(env, filename="blocked.m4a", genre=None)
    assert _state(env, write_id)["status"] == "WRITE_BLOCKED"

    attention_id = _seed(env, filename="attention.mp3", genre=None)
    env[1][env[0] / "Inbox" / "attention.mp3"]["artist"] = "Old Artist"
    assert _state(env, attention_id)["status"] == "NEEDS_ATTENTION"

    review_id = _seed(env, filename="track.mp3")
    env[1][env[0] / "Inbox" / "track.mp3"]["artist"] = "Old Artist"
    _queue_review(env, review_id)
    assert _state(env, review_id)["status"] == "REVIEW"


def test_different_destination_collision_needs_attention(env):
    track_id = _seed(env)
    destination = env[0] / "Library" / "House" / "Artist" / "Artist - Title.mp3"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different")
    state = _state(env, track_id)
    assert state["status"] == "NEEDS_ATTENTION"
    assert state["promotion"]["collision"] == "conflict"


def test_identical_destination_blocks_preview_and_apply_consistently(env):
    track_id = _seed(env, content=b"same")
    destination = env[0] / "Library" / "House" / "Artist" / "Artist - Title.mp3"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"same")

    preview = workspace_service.promotion_preview(env[0], [track_id])["items"][0]
    applied = workspace_service.promote_tracks(env[0], [track_id], confirm=True)

    assert preview["ready"] is False
    assert preview["collision"] == "identical"
    assert applied["promoted_count"] == 0
    assert applied["failed_count"] == 1
    assert (env[0] / "Inbox" / "track.mp3").is_file()


def test_synchronized_file_is_not_blocked_by_historical_failure(env):
    track_id = _seed(env)
    with backend_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO tag_write_operations "
            "(id, status, track_count, plan_json, result_json, created_at, library_key) "
            "VALUES ('failed-write', 'failed', 1, ?, ?, '2026-09-01', ?)",
            (
                json.dumps([{"track_id": track_id}]),
                json.dumps([{"track_id": track_id, "status": "failed"}]),
                current_library_key(),
            ),
        )
        conn.execute(
            "INSERT INTO tag_write_operations "
            "(id, status, track_count, plan_json, result_json, created_at, library_key) "
            "VALUES ('successful-write', 'completed', 1, ?, ?, '2026-09-02', ?)",
            (
                json.dumps([{"track_id": track_id}]),
                json.dumps([{"track_id": track_id, "status": "applied"}]),
                current_library_key(),
            ),
        )

    state = _state(env, track_id)
    assert state["status"] == "READY"
    assert state["write"]["last_failure"] is None


def test_latest_failed_write_with_unresolved_difference_is_write_blocked(env):
    track_id = _seed(env)
    env[1][env[0] / "Inbox" / "track.mp3"]["artist"] = "Old Artist"
    with backend_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO tag_write_operations "
            "(id, status, track_count, plan_json, result_json, created_at, library_key) "
            "VALUES ('failed-write', 'failed', 1, ?, ?, '2026-09-02', ?)",
            (
                json.dumps([{"track_id": track_id}]),
                json.dumps([{"track_id": track_id, "status": "failed"}]),
                current_library_key(),
            ),
        )
    state = _state(env, track_id)
    assert state["status"] == "WRITE_BLOCKED"
    assert state["write"]["last_failure"] is not None


def _seed_all_preparation_statuses(env) -> dict[str, int]:
    ids = {
        "READY": _seed(env, filename="ready.mp3"),
        "NEEDS_ATTENTION": _seed(env, filename="attention.mp3", genre=None),
        "REVIEW": _seed(env, filename="review.mp3"),
        "UNSAVED": _seed(env, filename="unsaved.mp3"),
        "WRITE_BLOCKED": _seed(env, filename="blocked.m4a"),
    }
    _queue_review(env, ids["REVIEW"])
    env[1][env[0] / "Inbox" / "unsaved.mp3"]["artist"] = "Old Artist"
    return ids


@pytest.mark.parametrize(
    "requested_status",
    ["READY", "NEEDS_ATTENTION", "REVIEW", "UNSAVED", "WRITE_BLOCKED"],
)
def test_inbox_api_filters_each_authoritative_status(env, requested_status):
    ids = _seed_all_preparation_statuses(env)
    with TestClient(backend_main.app) as client:
        response = client.get(
            "/api/workspace/inbox/tracks",
            params={"preparation_status": requested_status},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [ids[requested_status]]
    assert body["items"][0]["preparation_state"]["status"] == requested_status


def test_inbox_status_filter_composes_with_search_including_genre(env):
    matching = _seed(env, filename="matching.mp3", artist="Alpha", genre="Deep House")
    _seed(env, filename="other-ready.mp3", artist="Beta", genre="Techno")
    _seed(env, filename="attention.mp3", artist="Gamma", genre=None)

    with TestClient(backend_main.app) as client:
        response = client.get(
            "/api/workspace/inbox/tracks",
            params={"search": "Deep House", "preparation_status": "READY"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [matching]
    assert body["status_counts"]["ALL"] == 1


def test_inbox_status_filter_composes_with_sort(env):
    zeta = _seed(env, filename="zeta.mp3", artist="Zeta")
    alpha = _seed(env, filename="alpha.mp3", artist="Alpha")
    _seed(env, filename="attention.mp3", artist="Middle", title=None)

    with TestClient(backend_main.app) as client:
        response = client.get(
            "/api/workspace/inbox/tracks",
            params={"preparation_status": "READY", "sort": "artist", "order": "desc"},
        )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [zeta, alpha]


def test_inbox_readiness_sort_uses_authoritative_status_precedence(env):
    ids = _seed_all_preparation_statuses(env)
    expected_ascending = [
        ids["READY"], ids["UNSAVED"], ids["REVIEW"],
        ids["NEEDS_ATTENTION"], ids["WRITE_BLOCKED"],
    ]
    with TestClient(backend_main.app) as client:
        ascending = client.get(
            "/api/workspace/inbox/tracks", params={"sort": "readiness", "order": "asc"}
        )
        descending = client.get(
            "/api/workspace/inbox/tracks", params={"sort": "readiness", "order": "desc"}
        )

    assert [item["id"] for item in ascending.json()["items"]] == expected_ascending
    assert [item["id"] for item in descending.json()["items"]] == list(reversed(expected_ascending))


def test_inbox_status_filter_paginates_after_filter_and_reports_full_total(env):
    ready_ids = [_seed(env, filename=f"ready-{index}.mp3", artist=f"Artist {index}") for index in range(3)]
    attention_id = _seed(env, filename="attention.mp3", genre=None)

    with TestClient(backend_main.app) as client:
        response = client.get(
            "/api/workspace/inbox/tracks",
            params={
                "preparation_status": "READY",
                "sort": "artist",
                "limit": 1,
                "offset": 1,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [ready_ids[1]]
    assert body["status_counts"] == {
        "ALL": 4,
        "WRITE_BLOCKED": 0,
        "NEEDS_ATTENTION": 1,
        "REVIEW": 0,
        "UNSAVED": 0,
        "READY": 3,
    }
    assert body["available_track_ids"] == sorted(ready_ids + [attention_id])


def test_inbox_status_request_reads_each_candidate_file_once(env, monkeypatch):
    _seed(env, filename="first.mp3")
    _seed(env, filename="second.mp3")
    reads: list[Path] = []

    def read_tags(path: Path) -> dict[str, str]:
        resolved = Path(path)
        reads.append(resolved)
        return env[1][resolved]

    monkeypatch.setattr(tag_write_service, "_read_file_tags", read_tags)
    with TestClient(backend_main.app) as client:
        response = client.get("/api/workspace/inbox/tracks", params={"preparation_status": "READY"})

    assert response.status_code == 200
    assert reads == [env[0] / "Inbox" / "first.mp3", env[0] / "Inbox" / "second.mp3"]


def test_inbox_status_request_reads_review_snapshot_once(env, monkeypatch):
    _seed(env, filename="first.mp3")
    _seed(env, filename="second.mp3")
    review_calls = 0

    def get_review():
        nonlocal review_calls
        review_calls += 1
        return {"items": []}

    monkeypatch.setattr(enrichment_review_service, "get_review", get_review)
    with TestClient(backend_main.app) as client:
        response = client.get("/api/workspace/inbox/tracks", params={"preparation_status": "READY"})

    assert response.status_code == 200
    assert review_calls == 1


def test_inbox_api_rejects_unsupported_preparation_status(env):
    _seed(env)
    with TestClient(backend_main.app) as client:
        response = client.get(
            "/api/workspace/inbox/tracks",
            params={"preparation_status": "NOT_A_REAL_STATUS"},
        )
    assert response.status_code == 422


def test_inbox_api_without_status_filter_keeps_complete_list_behavior(env):
    ids = [_seed(env, filename="ready.mp3"), _seed(env, filename="attention.mp3", genre=None)]
    with TestClient(backend_main.app) as client:
        response = client.get("/api/workspace/inbox/tracks", params={"sort": "filename"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == set(ids)


def test_inbox_inspection_is_read_only_and_reuses_preparation_contract(env):
    track_id = _seed(env, filename="inspect.mp3", bpm=None)
    with TestClient(backend_main.app) as client:
        response = client.get(f"/api/workspace/inbox/tracks/{track_id}/inspection")
    assert response.status_code == 200
    item = response.json()
    assert item["id"] == track_id
    assert item["preparation_state"]["status"] == "READY"
    assert item["preparation_state"]["warnings"] == [{"code": "bpm_missing", "label": "BPM is missing"}]
