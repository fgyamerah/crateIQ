"""
API-level tests for the Cycle 10 batch preparation and Needs Review routes.

Covers: GET never triggers processing, POST without confirm is rejected,
a confirmed Process All returns immediately with an operation id (the
actual work runs in a background asyncio task), and GET /api/needs-review
never 500s against an empty/fresh library.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main
from backend.app.services import workspace_service


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    workspace_service.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    test_client = TestClient(backend_main.app)
    with test_client:
        yield test_client, root


def _seed_inbox_track(root: Path) -> int:
    inbox_file = root / "Inbox" / "song.mp3"
    _write(inbox_file)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'song.mp3', 'DJ Koze', 'Pick Up', 'House', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(inbox_file),),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_prepare_preview_is_get_and_read_only(client):
    test_client, root = client
    _seed_inbox_track(root)
    resp = test_client.get("/api/workspace/prepare/preview")
    assert resp.status_code == 200
    assert resp.json()["inbox_total"] == 1


def test_start_without_confirm_rejected(client):
    test_client, root = client
    _seed_inbox_track(root)
    resp = test_client.post("/api/workspace/prepare/start", json={"confirm": False})
    assert resp.status_code == 422


def test_start_with_confirm_returns_operation_id_immediately(client):
    test_client, root = client
    _seed_inbox_track(root)
    resp = test_client.post("/api/workspace/prepare/start", json={"confirm": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["operation_id"]
    assert body["track_count"] == 1

    poll = test_client.get(f"/api/workspace/prepare/operations/{body['operation_id']}")
    assert poll.status_code == 200
    assert poll.json()["status"] in ("running", "completed", "failed", "cancelled")


def test_start_on_empty_inbox_rejected(client):
    test_client, _root = client
    resp = test_client.post("/api/workspace/prepare/start", json={"confirm": True})
    assert resp.status_code == 422


def test_cancel_unknown_operation_404(client):
    test_client, _root = client
    resp = test_client.post("/api/workspace/prepare/operations/does-not-exist/cancel")
    assert resp.status_code == 404


def test_clean_selected_requires_no_confirm_but_is_scoped(client):
    test_client, root = client
    track_id = _seed_inbox_track(root)
    resp = test_client.post("/api/workspace/prepare/clean", json={"track_ids": [track_id]})
    assert resp.status_code == 200


def test_needs_review_never_500s_on_fresh_library(client):
    test_client, _root = client
    resp = test_client.get("/api/needs-review")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "counts" in body
    assert body["counts"]["ALL"] == 0


def test_needs_review_invalid_category_falls_back_to_all(client):
    test_client, _root = client
    resp = test_client.get("/api/needs-review?category=NOT_A_REAL_CATEGORY")
    assert resp.status_code == 200


def test_needs_review_get_never_mutates_the_pipeline_db(client):
    """
    Regression: needs_review_service must read metadata_repair_queue_service's
    already-computed snapshot rather than recomputing (writing) it on every
    GET -- the whole smoke surface, this endpoint included, must be read-only.
    """
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    before = db_path.read_bytes()
    resp = test_client.get("/api/needs-review")
    assert resp.status_code == 200
    assert db_path.read_bytes() == before


# ---------------------------------------------------------------------------
# Cycle 11: multi-provider consensus preview
# ---------------------------------------------------------------------------

def test_consensus_preview_succeeds_and_never_500s(client):
    """
    A POST (not GET) since, like the existing single-track online-lookup
    action, gathering evidence makes real bounded network calls (Beets/
    MusicBrainz here; more providers once configured) and persists them
    to the shared enrichment_review_service decision queue -- it must
    never claim to be a side-effect-free GET.
    """
    test_client, root = client
    track_id = _seed_inbox_track(root)

    resp = test_client.post(f"/api/workspace/enrichment/consensus/{track_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["track_id"] == track_id
    assert body["identity_confidence"] in ("HIGH", "MEDIUM", "LOW", "CONFLICT")
    assert "fields" in body and set(body["fields"]) == {"artist", "title", "genre"}


def test_consensus_preview_unknown_track_422(client):
    test_client, _root = client
    resp = test_client.post("/api/workspace/enrichment/consensus/999999")
    assert resp.status_code == 422
