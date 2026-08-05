from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core.library_root import assert_path_under_root
from backend.app.services import settings_service
from modules import metadata_repair, metadata_sanitation


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
            duration_sec REAL,
            bitrate_kbps INTEGER,
            filesize_bytes INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            error_msg TEXT,
            processed_at TEXT,
            pipeline_ver TEXT,
            quality_tier TEXT
            ,parse_confidence TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                str(root / "library" / "house" / "alpha.mp3"),
                "alpha.mp3",
                "Alpha",
                "First",
                "House",
                120.0,
                "8A",
                None,
                300.0,
                320,
                1234,
                "ok",
                None,
                "2026-05-05T10:00:00Z",
                "1.4.0",
                "HIGH",
                "HIGH",
            ),
            (
                str(root / "library" / "house" / "beta.mp3"),
                "beta.mp3",
                "Beta",
                "Second",
                "Techno",
                124.0,
                "9A",
                None,
                301.0,
                320,
                2222,
                "needs_review",
                None,
                "2026-05-05T11:00:00Z",
                "1.4.0",
                "MEDIUM",
                "MEDIUM",
            ),
            (
                str(root / "library" / "techno" / "gamma.mp3"),
                "gamma.mp3",
                "Gamma",
                "Third",
                "House",
                126.0,
                "10A",
                None,
                302.0,
                320,
                3333,
                "error",
                "bad file",
                "2026-05-05T12:00:00Z",
                "1.4.0",
                "LOW",
                "LOW",
            ),
            (
                str(root / "library" / "misc" / "delta.mp3"),
                "delta.mp3",
                "Music Corp",
                "Downloads",
                "House",
                None,
                None,
                None,
                303.0,
                320,
                4444,
                "ok",
                None,
                "2026-05-05T13:00:00Z",
                "1.4.0",
                "HIGH",
                "LOW",
            ),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _write_audit(root: Path) -> Path:
    audit_dir = root / "logs" / "path_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "path_audit_20260505_130000.json"
    audit_payload = {
        "summary": {
            "disk_audio_files": 12,
            "missing_files": 2,
            "untracked_files": 3,
            "stale_processed_state_rows_total": 4,
            "canonical_source": "tracks",
        },
        "root": str(root),
    }
    audit_path.write_text(json.dumps(audit_payload), encoding="utf-8")
    return audit_path


def _write_queue(root: Path) -> Path:
    queue_path = root / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "filepath": str(root / "library" / "house" / "alpha.mp3"),
                        "confidence": "HIGH",
                        "action_suggestion": "auto_candidate",
                        "score": 0.98,
                    }
                ),
                json.dumps(
                    {
                        "filepath": str(root / "library" / "house" / "beta.mp3"),
                        "confidence": "MEDIUM",
                        "action_suggestion": "review",
                        "score": 0.81,
                    }
                ),
                json.dumps(
                    {
                        "filepath": str(root / "library" / "techno" / "gamma.mp3"),
                        "confidence": "LOW",
                        "action_suggestion": "ignore",
                        "score": 0.32,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return queue_path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", tmp_path / "local" / "crateiq.env")
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _create_tracks_db(root)
    _write_audit(root)
    _write_queue(root)
    with TestClient(backend_main.app) as test_client:
        yield test_client, root


def test_health_endpoint_reports_selected_root_and_db(client):
    test_client, root = client

    response = test_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "library_root": str(root.resolve()),
        "db_path": str((root / "logs" / "processed.db").resolve()),
        "db_exists": True,
    }


def test_tracks_pagination_and_search(client):
    test_client, root = client

    response = test_client.get("/api/tracks", params={"limit": 1, "offset": 1})
    payload = response.json()

    assert response.status_code == 200
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["total"] == 4
    assert len(payload["items"]) == 1
    assert payload["items"][0]["artist"] == "Beta"

    search_response = test_client.get("/api/tracks", params={"search": "Gamma"})
    search_payload = search_response.json()
    assert search_response.status_code == 200
    assert search_payload["total"] == 1
    assert search_payload["items"][0]["filepath"] == str(root / "library" / "techno" / "gamma.mp3")


def test_track_filters_cover_issue_bpm_key_genre_and_parse_confidence(client):
    test_client, root = client

    issue_response = test_client.get("/api/tracks", params={"issue": "weak_filename_parse"})
    issue_payload = issue_response.json()
    assert issue_response.status_code == 200
    assert issue_payload["total"] == 3

    suspicious_response = test_client.get("/api/tracks", params={"issue": "suspicious_artist"})
    suspicious_payload = suspicious_response.json()
    assert suspicious_response.status_code == 200
    assert suspicious_payload["total"] == 1
    assert suspicious_payload["items"][0]["filepath"] == str(root / "library" / "misc" / "delta.mp3")

    bpm_response = test_client.get("/api/tracks", params={"bpm_min": 125})
    bpm_payload = bpm_response.json()
    assert bpm_response.status_code == 200
    assert bpm_payload["total"] == 1
    assert bpm_payload["items"][0]["artist"] == "Gamma"

    key_response = test_client.get("/api/tracks", params={"has_key": False})
    key_payload = key_response.json()
    assert key_response.status_code == 200
    assert key_payload["total"] == 1
    assert key_payload["items"][0]["artist"] == "Music Corp"

    genre_response = test_client.get("/api/tracks", params={"genre": "house", "parse_confidence": "HIGH"})
    genre_payload = genre_response.json()
    assert genre_response.status_code == 200
    assert genre_payload["total"] == 1
    assert genre_payload["items"][0]["artist"] == "Alpha"


def test_track_issues_return_grouped_counts(client):
    test_client, root = client

    response = test_client.get("/api/tracks/issues", params={"limit": 10})
    payload = response.json()

    assert response.status_code == 200
    assert payload == {
        "missing_artist": 0,
        "missing_title": 0,
        "weak_filename_parse": 3,
        "suspicious_artist": 1,
        "suspicious_title": 1,
    }


def test_enrichment_queue_filtering(client):
    test_client, _root = client

    response = test_client.get(
        "/api/enrichment/queue",
        params={"action": "review", "confidence": "MEDIUM"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 1
    assert payload["counts"] == {
        "by_action": {"review": 1},
        "by_confidence": {"MEDIUM": 1},
    }
    assert payload["items"][0]["action_suggestion"] == "review"
    assert payload["items"][0]["confidence"] == "MEDIUM"


def test_enrichment_review_state_endpoints_persist_and_echo(client):
    test_client, root = client
    state_path = root / "data" / "intelligence" / "enrichment_review_state.json"

    empty_state = test_client.get("/api/enrichment/review/state")
    assert empty_state.status_code == 200
    assert empty_state.json()["approved"] == []
    assert empty_state.json()["rejected"] == []
    assert empty_state.json()["deferred"] == []

    approve = test_client.post("/api/enrichment/review/1/approve")
    reject = test_client.post("/api/enrichment/review/2/reject")
    defer = test_client.post("/api/enrichment/review/3/defer")

    assert approve.status_code == 200
    assert reject.status_code == 200
    assert defer.status_code == 200
    assert approve.json()["review_status"] == "approved"
    assert reject.json()["review_status"] == "rejected"
    assert defer.json()["review_status"] == "deferred"
    assert state_path.exists()

    state_payload = test_client.get("/api/enrichment/review/state").json()
    assert state_payload["approved"] == [1]
    assert state_payload["rejected"] == [2]
    assert state_payload["deferred"] == [3]
    assert state_payload["counts"] == {"approved": 1, "rejected": 1, "deferred": 1}
    assert state_payload["items"]["1"]["review_status"] == "approved"
    assert state_payload["items"]["2"]["review_status"] == "rejected"
    assert state_payload["items"]["3"]["review_status"] == "deferred"

    queue_payload = test_client.get("/api/enrichment/queue").json()
    review_map = {item["track_id"]: item["review_status"] for item in queue_payload["items"]}
    assert review_map[1] == "approved"
    assert review_map[2] == "rejected"
    assert review_map[3] == "deferred"

    track_payload = test_client.get("/api/tracks/1").json()
    assert track_payload["enrichment_queue_item"]["review_status"] == "approved"


def test_enrichment_review_export_and_summary(client):
    test_client, _root = client

    test_client.post("/api/enrichment/review/1/approve")
    test_client.post("/api/enrichment/review/2/reject")
    test_client.post("/api/enrichment/review/3/defer")

    export_response = test_client.get("/api/enrichment/review/export")
    assert export_response.status_code == 200
    assert "attachment" in export_response.headers.get("content-disposition", "")
    export_payload = export_response.json()
    assert export_payload["approved"] == [1]
    assert export_payload["rejected"] == [2]
    assert export_payload["deferred"] == [3]
    assert export_payload["counts"] == {"approved": 1, "rejected": 1, "deferred": 1}
    assert export_payload["updated_at"] is not None

    summary_response = test_client.get("/api/enrichment/review/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["pending_count"] == 0
    assert summary["approved_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["deferred_count"] == 1
    assert summary["approved_high_count"] == 1
    assert summary["approved_medium_count"] == 0
    assert summary["rejected_by_reason"] == {}
    assert summary["last_updated"] is not None


def test_enrichment_apply_approved_endpoints_require_confirm_and_apply(client):
    test_client, root = client

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.execute("ALTER TABLE tracks ADD COLUMN album TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN label TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN isrc TEXT")
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "incoming" / "apply-endpoint.flac"),
            "apply-endpoint.flac",
            None,
            None,
            "House",
            127.0,
            "7A",
            "07A",
            301.0,
            320,
            5555,
            "ok",
            None,
            "2026-05-05T14:00:00Z",
            "1.4.0",
            "HIGH",
            "LOW",
        ),
    )
    track_id = conn.execute("SELECT id FROM tracks WHERE filepath = ?", (str(root / "library" / "incoming" / "apply-endpoint.flac"),)).fetchone()[0]
    conn.commit()
    conn.close()

    state_path = root / "data" / "intelligence" / "enrichment_review_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-05-06T12:00:00Z",
                "queue_total": 1,
                "items": {
                    str(track_id): {
                        "track_id": track_id,
                        "review_status": "approved",
                        "updated_at": "2026-05-06T12:00:00Z",
                        "queue_item": {
                            "filepath": str(root / "library" / "incoming" / "apply-endpoint.flac"),
                            "confidence": "HIGH",
                            "provider": "discogs",
                            "score": 0.99,
                            "best_match": {
                                "artist": "Applied Artist",
                                "title": "Applied Title",
                                "album": "Applied Album",
                            },
                        },
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    dry_run = test_client.post("/api/enrichment/apply-approved/dry-run")
    assert dry_run.status_code == 200
    assert dry_run.json()["proposed_count"] == 1

    missing_confirm = test_client.post("/api/enrichment/apply-approved/apply")
    assert missing_confirm.status_code == 400

    apply_response = test_client.post("/api/enrichment/apply-approved/apply", params={"confirm": True})
    assert apply_response.status_code == 200
    payload = apply_response.json()
    assert payload["applied_count"] == 1
    assert payload["proposed_count"] == 1

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    finally:
        conn.close()
    assert row["artist"] == "Applied Artist"
    assert row["title"] == "Applied Title"
    assert row["album"] == "Applied Album"
    assert row["label"] is None
    assert row["isrc"] is None
    assert row["bpm"] == 127.0
    assert row["key_musical"] == "7A"
    assert row["key_camelot"] == "07A"


def test_enrichment_review_is_safe_without_db(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)

    with TestClient(backend_main.app) as test_client:
        state_response = test_client.get("/api/enrichment/review/state")
        assert state_response.status_code == 200
        assert state_response.json()["approved"] == []

        action_response = test_client.post("/api/enrichment/review/1/approve")
        assert action_response.status_code == 404


def test_metadata_repair_endpoints_review_and_apply(client):
    test_client, root = client
    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "repair" / "Endpoint Artist - Endpoint Title.mp3"),
            "Endpoint Artist - Endpoint Title.mp3",
            None,
            "Old Endpoint Title",
            "House",
            128.0,
            "6A",
            "06A",
            300.0,
            320,
            9999,
            "ok",
            None,
            "2026-05-06T12:00:00Z",
            "1.4.0",
            "HIGH",
            "HIGH",
        ),
    )
    track_id = conn.execute(
        "SELECT id FROM tracks WHERE filename = ?",
        ("Endpoint Artist - Endpoint Title.mp3",),
    ).fetchone()[0]
    conn.commit()
    conn.close()
    metadata_repair.scan(root)

    queue_response = test_client.get("/api/metadata-repair/queue")
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    assert queue_payload["total"] >= 1
    assert any(item["track_id"] == track_id for item in queue_payload["items"])

    summary_response = test_client.get("/api/metadata-repair/summary")
    assert summary_response.status_code == 200
    assert summary_response.json()["queue_total"] == queue_payload["total"]

    edit_artist = test_client.patch(
        f"/api/metadata-repair/{track_id}/field/artist/proposal",
        json={"proposed": "Edited Endpoint Artist"},
    )
    assert edit_artist.status_code == 200
    edit_payload = edit_artist.json()
    assert edit_payload["field"] == "artist"
    assert edit_payload["proposed"] == "Edited Endpoint Artist"
    state_field = edit_payload["state"]["items"][str(track_id)]["fields"]["artist"]
    assert state_field["proposed"] == "Edited Endpoint Artist"
    assert state_field["original_proposed"] == "Endpoint Artist"
    assert state_field["edited"] is True

    empty_edit = test_client.patch(
        f"/api/metadata-repair/{track_id}/field/title/proposal",
        json={"proposed": "   "},
    )
    assert empty_edit.status_code == 400

    approve_artist = test_client.post(f"/api/metadata-repair/{track_id}/field/artist/approve")
    assert approve_artist.status_code == 200
    assert approve_artist.json()["field"] == "artist"
    assert approve_artist.json()["review_status"] == "approved"

    reject_title = test_client.post(f"/api/metadata-repair/{track_id}/field/title/reject")
    assert reject_title.status_code == 200
    assert reject_title.json()["field"] == "title"
    assert reject_title.json()["review_status"] == "rejected"

    dry_run = test_client.post("/api/metadata-repair/apply-approved/dry-run")
    assert dry_run.status_code == 200
    assert dry_run.json()["proposed_count"] == 1
    assert dry_run.json()["applied_count"] == 0

    missing_confirm = test_client.post("/api/metadata-repair/apply-approved/apply")
    assert missing_confirm.status_code == 400

    apply_response = test_client.post("/api/metadata-repair/apply-approved/apply", params={"confirm": True})
    assert apply_response.status_code == 200
    assert apply_response.json()["applied_count"] == 1

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    conn.close()
    assert row["artist"] == "Edited Endpoint Artist"
    assert row["title"] == "Old Endpoint Title"
    assert row["bpm"] == 128.0
    assert row["key_musical"] == "6A"
    assert row["key_camelot"] == "06A"


def test_metadata_issue_routing_and_generate_endpoints(client):
    test_client, root = client
    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "issues" / "19. Anza, Chumee - Sing It Back (Extended Mix) (fordjonly.com).mp3"),
            "19. Anza, Chumee - Sing It Back (Extended Mix) (fordjonly.com).mp3",
            None,
            None,
            "House",
            122.0,
            "7A",
            "07A",
            300.0,
            320,
            9999,
            "ok",
            None,
            "2026-05-06T12:00:00Z",
            "1.4.0",
            "LOW",
            "LOW",
        ),
    )
    repair_track_id = conn.execute(
        "SELECT id FROM tracks WHERE filename = ?",
        ("19. Anza, Chumee - Sing It Back (Extended Mix) (fordjonly.com).mp3",),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "issues" / "suspicious-title.mp3"),
            "suspicious-title.mp3",
            "Route Artist",
            "TrackName fordjonly.com",
            "House",
            124.0,
            "8A",
            "08A",
            300.0,
            320,
            9999,
            "ok",
            None,
            "2026-05-06T13:00:00Z",
            "1.4.0",
            "LOW",
            "LOW",
        ),
    )
    sanitation_track_id = conn.execute(
        "SELECT id FROM tracks WHERE filename = ?",
        ("suspicious-title.mp3",),
    ).fetchone()[0]
    conn.commit()
    conn.close()

    repair_rows = test_client.get("/api/tracks", params={"issue": "missing_artist"}).json()["items"]
    repair_row = next(item for item in repair_rows if item["id"] == repair_track_id)
    assert repair_row["recommended_action"] == "Repair"
    assert repair_row["recommended_route"] == "metadata-repair"

    sanitation_rows = test_client.get("/api/tracks", params={"issue": "suspicious_title"}).json()["items"]
    sanitation_row = next(item for item in sanitation_rows if item["id"] == sanitation_track_id)
    assert sanitation_row["recommended_action"] == "Sanitize"
    assert sanitation_row["recommended_route"] == "metadata-sanitation"

    repair_generate = test_client.post(f"/api/metadata-repair/generate/{repair_track_id}")
    assert repair_generate.status_code == 200
    repair_payload = repair_generate.json()
    assert repair_payload["generated"] is True
    assert repair_payload["proposal"]["proposed"]["artist"] == "Anza, Chumee"
    assert repair_payload["proposal"]["proposed"]["title"] == "Sing It Back (Extended Mix)"

    repair_duplicate = test_client.post(f"/api/metadata-repair/generate/{repair_track_id}")
    assert repair_duplicate.status_code == 200
    assert repair_duplicate.json()["generated"] is False
    repair_queue = test_client.get("/api/metadata-repair/queue")
    assert sum(1 for item in repair_queue.json()["items"] if item["track_id"] == repair_track_id) == 1

    sanitation_generate = test_client.post(f"/api/metadata-sanitation/generate/{sanitation_track_id}")
    assert sanitation_generate.status_code == 200
    sanitation_payload = sanitation_generate.json()
    assert sanitation_payload["generated"] is True
    assert sanitation_payload["proposal"]["proposed"]["title"] == "TrackName"

    sanitation_duplicate = test_client.post(f"/api/metadata-sanitation/generate/{sanitation_track_id}")
    assert sanitation_duplicate.status_code == 200
    assert sanitation_duplicate.json()["generated"] is False
    sanitation_queue = test_client.get("/api/metadata-sanitation/queue")
    assert sum(1 for item in sanitation_queue.json()["items"] if item["track_id"] == sanitation_track_id) == 1

    quality = test_client.get("/api/library/quality").json()
    assert quality["metadata_repair"]["queue_total"] >= 1
    assert quality["metadata_sanitation"]["queue_total"] >= 1


def test_metadata_sanitation_endpoints_edit_and_apply(client):
    test_client, root = client
    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "sanitation" / "Saxophone MaciaDownloads.mp3"),
            "Saxophone MaciaDownloads.mp3",
            "Endpoint Artist",
            "Saxophone MaciaDownloads",
            "House",
            126.0,
            "5A",
            "05A",
            300.0,
            320,
            9999,
            "ok",
            None,
            "2026-05-06T12:00:00Z",
            "1.4.0",
            "HIGH",
            "HIGH",
        ),
    )
    track_id = conn.execute(
        "SELECT id FROM tracks WHERE filename = ?",
        ("Saxophone MaciaDownloads.mp3",),
    ).fetchone()[0]
    conn.commit()
    conn.close()
    metadata_sanitation.scan(root)

    queue_response = test_client.get("/api/metadata-sanitation/queue")
    assert queue_response.status_code == 200
    queue_payload = queue_response.json()
    proposal = next(item for item in queue_payload["items"] if item["track_id"] == track_id)
    assert proposal["proposed"]["title"] == "Saxophone"
    assert proposal["risk_flags"] == ["junk_suffix_removed"]

    edit_title = test_client.patch(
        f"/api/metadata-sanitation/{track_id}/field/title/proposal",
        json={"proposed": "Edited Saxophone"},
    )
    assert edit_title.status_code == 200
    state_field = edit_title.json()["state"]["items"][str(track_id)]["fields"]["title"]
    assert state_field["proposed"] == "Edited Saxophone"
    assert state_field["original_proposed"] == "Saxophone"
    assert state_field["edited"] is True

    empty_edit = test_client.patch(
        f"/api/metadata-sanitation/{track_id}/field/title/proposal",
        json={"proposed": "   "},
    )
    assert empty_edit.status_code == 400

    approve_title = test_client.post(f"/api/metadata-sanitation/{track_id}/field/title/approve")
    assert approve_title.status_code == 200
    assert approve_title.json()["field"] == "title"
    assert approve_title.json()["review_status"] == "approved"

    dry_run = test_client.post("/api/metadata-sanitation/apply-approved/dry-run")
    assert dry_run.status_code == 200
    assert dry_run.json()["proposed_count"] == 1

    missing_confirm = test_client.post("/api/metadata-sanitation/apply-approved/apply")
    assert missing_confirm.status_code == 400

    apply_response = test_client.post("/api/metadata-sanitation/apply-approved/apply", params={"confirm": True})
    assert apply_response.status_code == 200
    assert apply_response.json()["applied_field_count"] == 1

    active_queue = test_client.get("/api/metadata-sanitation/queue")
    assert active_queue.status_code == 200
    assert all(item["track_id"] != track_id for item in active_queue.json()["items"])

    applied_queue = test_client.get("/api/metadata-sanitation/queue", params={"include_applied": True})
    assert applied_queue.status_code == 200
    assert any(item["track_id"] == track_id for item in applied_queue.json()["items"])

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    conn.close()
    assert row["artist"] == "Endpoint Artist"
    assert row["title"] == "Edited Saxophone"
    assert row["bpm"] == 126.0
    assert row["key_musical"] == "5A"
    assert row["key_camelot"] == "05A"

    metadata_sanitation.scan(root)
    rescan_queue = test_client.get("/api/metadata-sanitation/queue", params={"include_applied": True})
    assert rescan_queue.status_code == 200
    assert all(item["track_id"] != track_id for item in rescan_queue.json()["items"])


def test_latest_audit_endpoint_returns_latest_report(client):
    test_client, root = client

    response = test_client.get("/api/audit/latest")
    payload = response.json()

    assert response.status_code == 200
    assert payload["summary"]["canonical_source"] == "tracks"
    assert payload["summary"]["disk_audio_files"] == 12
    assert payload["root"] == str(root)


def test_track_detail_includes_enrichment_info(client):
    test_client, root = client

    response = test_client.get("/api/tracks/1")
    payload = response.json()

    assert response.status_code == 200
    assert payload["filesystem_path"] == str(root / "library" / "house" / "alpha.mp3")
    assert payload["parse_confidence"] == "HIGH"
    assert payload["enrichment_queue_item"]["action_suggestion"] == "auto_candidate"


def test_stats_endpoint_uses_latest_audit_without_scanning(client):
    test_client, _root = client

    response = test_client.get("/api/stats")
    payload = response.json()

    assert response.status_code == 200
    assert payload["tracks_count"] == 4
    assert payload["disk_audio_files"] == 12
    assert payload["missing_files"] == 2
    assert payload["untracked_files"] == 3
    assert payload["stale_processed_state_total"] == 4
    assert payload["canonical_source"] == "tracks"
    assert payload["last_audit_report"]["summary"]["canonical_source"] == "tracks"


def test_library_folder_and_overview_endpoints(client):
    test_client, root = client

    folders_response = test_client.get("/api/library/folders")
    folders = folders_response.json()
    assert folders_response.status_code == 200
    assert folders == [
        {"folder": str(root / "library" / "house"), "track_count": 2, "issue_count": 1},
        {"folder": str(root / "library" / "misc"), "track_count": 1, "issue_count": 1},
        {"folder": str(root / "library" / "techno"), "track_count": 1, "issue_count": 1},
    ]

    overview_response = test_client.get("/api/library/overview")
    overview = overview_response.json()
    assert overview_response.status_code == 200
    assert overview["total_tracks"] == 4
    assert overview["tracks_with_bpm"] == 3
    assert overview["tracks_with_camelot_key"] == 3
    assert overview["tracks_missing_artist"] == 0
    assert overview["tracks_missing_title"] == 0
    assert overview["parse_confidence_breakdown"] == {"HIGH": 1, "MEDIUM": 1, "LOW": 2}
    assert overview["genre_top_counts"][0]["count"] == 3


def test_library_quality_endpoint_reports_progress_and_actions(client):
    test_client, root = client

    response = test_client.get("/api/library/quality")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_tracks"] == 4
    assert payload["issue_total"] == 5
    assert payload["issues_by_type"] == {
        "missing_artist": 0,
        "missing_title": 0,
        "suspicious_artist": 1,
        "suspicious_title": 1,
        "weak_filename_parse": 3,
    }
    assert payload["metadata_repair"]["queue_total"] == 0
    assert payload["metadata_sanitation"]["queue_total"] == 0
    assert payload["coverage"] == {
        "with_artist": 4,
        "with_title": 4,
        "with_bpm": 3,
        "with_camelot": 3,
        "with_genre": 4,
    }
    assert payload["recommended_next_actions"]
    assert any(action["target"] == "/issues" for action in payload["recommended_next_actions"])


def test_library_quality_endpoint_handles_missing_queue_files(tmp_path, monkeypatch):
    root = tmp_path / "quality_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _create_tracks_db(root)

    with TestClient(backend_main.app) as test_client:
        response = test_client.get("/api/library/quality")
        payload = response.json()

    assert response.status_code == 200
    assert payload["metadata_repair"]["queue_total"] == 0
    assert payload["metadata_sanitation"]["queue_total"] == 0
    assert payload["metadata_repair"]["pending"] == 0
    assert payload["metadata_sanitation"]["pending"] == 0


def test_missing_db_is_handled_safely(tmp_path, monkeypatch):
    root = tmp_path / "empty_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)

    with TestClient(backend_main.app) as test_client:
        health = test_client.get("/api/health").json()
        stats = test_client.get("/api/stats").json()
        tracks = test_client.get("/api/tracks").json()
        audit = test_client.get("/api/audit/latest").json()
        folders = test_client.get("/api/library/folders").json()
        overview = test_client.get("/api/library/overview").json()
        issue_counts = test_client.get("/api/tracks/issues").json()

    assert health["db_exists"] is False
    assert stats["tracks_count"] == 0
    assert stats["last_audit_report"] is None
    assert tracks == {"items": [], "limit": 100, "offset": 0, "total": 0}
    assert audit == {"available": False}
    assert folders == []
    assert overview["total_tracks"] == 0
    assert issue_counts == {
        "missing_artist": 0,
        "missing_title": 0,
        "weak_filename_parse": 0,
        "suspicious_artist": 0,
        "suspicious_title": 0,
    }


def test_read_only_requests_do_not_mutate_db(client):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    before = db_path.read_bytes()

    test_client.get("/api/tracks", params={"issue": "weak_filename_parse"})
    test_client.get("/api/tracks/1")
    test_client.get("/api/library/folders")
    test_client.get("/api/library/overview")
    test_client.get("/api/tracks/issues")

    assert db_path.read_bytes() == before


def test_root_containment_rejects_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ValueError, match="path outside selected root"):
        assert_path_under_root("../escape.mp3", root)


# ---------------------------------------------------------------------------
# modules.harmonic.camelot_distance — public helper used by track_service
# ---------------------------------------------------------------------------

def test_camelot_distance_public_helper_matches_private_alias():
    from modules import harmonic

    assert harmonic.camelot_distance("8A", "8A") == (0, False)
    assert harmonic.camelot_distance("8A", "8B") == (0, True)
    assert harmonic.camelot_distance("8A", "9A") == (1, False)
    # The private name is kept only as a backward-compatible alias for
    # existing internal callers (e.g. modules/set_builder.py).
    assert harmonic._camelot_distance("8A", "9A") == harmonic.camelot_distance("8A", "9A")


# ---------------------------------------------------------------------------
# GET /api/tracks/{id}/compatible
# ---------------------------------------------------------------------------

def _create_compat_tracks_db(root: Path) -> Path:
    """
    Dedicated fixture DB for compatible-tracks tests: an 8A anchor plus one
    candidate per Camelot relation, a BPM-tolerance edge case, a genre-only
    match, a Camelot clash, and a missing-key track.
    """
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
            duration_sec REAL,
            bitrate_kbps INTEGER,
            filesize_bytes INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            error_msg TEXT,
            processed_at TEXT,
            pipeline_ver TEXT,
            quality_tier TEXT,
            parse_confidence TEXT
        )
        """
    )
    # (filepath, artist, title, genre, bpm, key_musical, key_camelot, status)
    rows = [
        ("anchor.mp3",   "Anchor Artist",   "Anchor Track",   "Afro House",  122.0, "A Minor", "8A", "ok"),
        ("samekey.mp3",  "Same Key Artist", "Same Key Track", "Amapiano",    121.0, "A Minor", "8A", "ok"),
        ("adjacent.mp3", "Adjacent Artist", "Adjacent Track", "Deep House",  124.0, "E Minor", "9A", "ok"),
        ("relative.mp3", "Relative Artist", "Relative Track", "Progressive House", 120.0, "C Major", "8B", "ok"),
        ("farbpm.mp3",   "Far BPM Artist",  "Far BPM Track",  "Amapiano",    140.0, "A Minor", "8A", "ok"),
        ("clash.mp3",    "Clash Artist",    "Clash Track",    "Techno",      126.0, "D Minor", "2A", "ok"),
        ("nokey.mp3",    "No Key Artist",   "No Key Track",   "House",       122.0, None,      None, "ok"),
    ]
    conn.executemany(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (str(root / "library" / fp), fp, artist, title, genre, bpm, key_musical, key_camelot, status)
            for fp, artist, title, genre, bpm, key_musical, key_camelot, status in rows
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def compat_client(tmp_path, monkeypatch):
    root = tmp_path / "compat_library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _create_compat_tracks_db(root)
    with TestClient(backend_main.app) as test_client:
        yield test_client


def _track_id_by_filename(test_client, filename: str) -> int:
    payload = test_client.get("/api/tracks", params={"search": filename}).json()
    matches = [item for item in payload["items"] if item["filename"] == filename]
    assert matches, f"no track found for filename {filename!r}"
    return matches[0]["id"]


def test_compatible_tracks_returns_same_key_adjacent_and_relative_matches(compat_client):
    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    response = compat_client.get(f"/api/tracks/{anchor_id}/compatible")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    match_types = {item["filename"]: item["match_type"] for item in payload["items"]}
    assert match_types["samekey.mp3"] == "same_key"
    assert match_types["adjacent.mp3"] == "adjacent_key"
    assert match_types["relative.mp3"] == "relative_key"
    # Camelot clash (2A, 6 wheel positions from 8A) is never a candidate.
    assert "clash.mp3" not in match_types
    reasons = {item["filename"]: item["compatibility_reason"] for item in payload["items"]}
    assert reasons["samekey.mp3"].startswith("Same key")
    assert reasons["adjacent.mp3"].startswith("Adjacent key")
    assert reasons["relative.mp3"].startswith("Relative major/minor")


def test_compatible_tracks_excludes_the_selected_track_itself(compat_client):
    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    payload = compat_client.get(f"/api/tracks/{anchor_id}/compatible").json()

    assert all(item["id"] != anchor_id for item in payload["items"])


def test_compatible_tracks_bpm_tolerance_controls_inclusion(compat_client):
    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    # farbpm.mp3 is the same key (8A) as the anchor but 18 BPM away.
    wide = compat_client.get(
        f"/api/tracks/{anchor_id}/compatible", params={"bpm_tolerance": 20}
    ).json()
    tight = compat_client.get(
        f"/api/tracks/{anchor_id}/compatible", params={"bpm_tolerance": 5}
    ).json()

    wide_names = {item["filename"] for item in wide["items"]}
    tight_names = {item["filename"] for item in tight["items"]}
    assert "farbpm.mp3" in wide_names
    assert "farbpm.mp3" not in tight_names


def test_compatible_tracks_include_flags_gate_same_key_and_adjacent(compat_client):
    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    payload = compat_client.get(
        f"/api/tracks/{anchor_id}/compatible",
        params={"include_same_key": False, "include_adjacent": False, "bpm_tolerance": 30},
    ).json()

    match_types = {item["match_type"] for item in payload["items"]}
    # Only the relative-key relation remains selectable; same-key/adjacent are gated off.
    assert match_types <= {"relative_key"}
    assert "same_key" not in match_types
    assert "adjacent_key" not in match_types


def test_compatible_tracks_missing_camelot_key_returns_safe_empty_response(compat_client):
    nokey_id = _track_id_by_filename(compat_client, "nokey.mp3")

    response = compat_client.get(f"/api/tracks/{nokey_id}/compatible")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "missing_key"
    assert payload["items"] == []
    assert payload["reason"]


def test_compatible_tracks_unknown_track_id_returns_404(compat_client):
    response = compat_client.get("/api/tracks/999999/compatible")
    assert response.status_code == 404


def test_compatible_tracks_limit_is_respected(compat_client):
    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    payload = compat_client.get(
        f"/api/tracks/{anchor_id}/compatible",
        params={"limit": 1, "bpm_tolerance": 30},
    ).json()

    assert len(payload["items"]) == 1


def test_compatible_tracks_endpoint_is_read_only(compat_client):
    root_db = None
    # Recover the db path via the health endpoint rather than the fixture,
    # to exercise the same read path the running app uses.
    health = compat_client.get("/api/health").json()
    root_db = Path(health["db_path"])
    before = root_db.read_bytes()

    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")
    compat_client.get(f"/api/tracks/{anchor_id}/compatible")
    compat_client.get(f"/api/tracks/{anchor_id}/compatible", params={"genre": "Amapiano"})

    assert root_db.read_bytes() == before


def test_compatible_tracks_genre_filter_restricts_candidates(compat_client):
    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    payload = compat_client.get(
        f"/api/tracks/{anchor_id}/compatible",
        params={"genre": "Amapiano", "bpm_tolerance": 30},
    ).json()

    genres = {item["genre"] for item in payload["items"]}
    assert genres <= {"Amapiano"}


def test_compatible_tracks_real_query_failure_returns_500_without_leaking_internals(
    compat_client, monkeypatch
):
    """
    A genuine query/DB failure (corrupt DB, schema mismatch, etc.) must
    surface as an error, not as status: "ok" with an empty items list —
    that would silently hide a real backend bug from the UI.
    """
    from backend.app.services import track_service

    anchor_id = _track_id_by_filename(compat_client, "anchor.mp3")

    sensitive_detail = "/very/secret/internal/path/processed.db syntax error near TRACKS"
    real_get_pipeline_conn = track_service.get_pipeline_conn
    call_count = {"n": 0}

    @contextlib.contextmanager
    def _flaky_conn():
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call: the internal get_track() lookup — let it succeed
            # so we reach the compatible-tracks query itself.
            with real_get_pipeline_conn() as conn:
                yield conn
            return
        raise RuntimeError(sensitive_detail)
        yield  # pragma: no cover - unreachable, keeps this a generator function

    monkeypatch.setattr(track_service, "get_pipeline_conn", _flaky_conn)

    response = compat_client.get(f"/api/tracks/{anchor_id}/compatible")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] != "ok"
    assert "status" not in body or body.get("status") != "ok"
    assert sensitive_detail not in response.text
    assert "processed.db" not in response.text
    assert "RuntimeError" not in response.text


def test_manual_crates_create_list_and_detail(client):
    test_client, _root = client

    created = test_client.post("/api/crates", json={"name": "Afro House Warmup", "notes": "Open-air set"})
    assert created.status_code == 201
    crate = created.json()
    assert crate["name"] == "Afro House Warmup"
    assert crate["track_count"] == 0

    listed = test_client.get("/api/crates")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [crate["id"]]

    detail = test_client.get(f"/api/crates/{crate['id']}")
    assert detail.status_code == 200
    assert detail.json()["notes"] == "Open-air set"
    assert detail.json()["tracks"] == []


def test_manual_crates_add_prevent_duplicate_remove_reorder_and_delete(client):
    test_client, _root = client
    crate = test_client.post("/api/crates", json={"name": "Peak Time Amapiano"}).json()
    first_id = test_client.get("/api/tracks", params={"limit": 4}).json()["items"][0]["id"]
    second_id = test_client.get("/api/tracks", params={"limit": 4}).json()["items"][1]["id"]

    first_add = test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": first_id})
    assert first_add.status_code == 201
    assert first_add.json()["tracks"][0]["track_id"] == first_id

    duplicate = test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": first_id})
    assert duplicate.status_code == 409
    assert "already" in duplicate.json()["detail"].lower()

    second_add = test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": second_id})
    assert second_add.status_code == 201
    reordered = test_client.patch(
        f"/api/crates/{crate['id']}/tracks/reorder", json={"track_ids": [second_id, first_id]}
    )
    assert reordered.status_code == 200
    assert [item["track_id"] for item in reordered.json()["tracks"]] == [second_id, first_id]
    assert [item["position"] for item in reordered.json()["tracks"]] == [1, 2]

    removed = test_client.delete(f"/api/crates/{crate['id']}/tracks/{second_id}")
    assert removed.status_code == 200
    assert [item["track_id"] for item in removed.json()["tracks"]] == [first_id]
    assert removed.json()["tracks"][0]["position"] == 1

    deleted = test_client.delete(f"/api/crates/{crate['id']}")
    assert deleted.status_code == 204
    assert test_client.get(f"/api/crates/{crate['id']}").status_code == 404


def test_smart_crates_presets_preview_filters_and_empty_state(client):
    test_client, _root = client

    presets = test_client.get("/api/smart-crates/presets")
    assert presets.status_code == 200
    assert {item["id"] for item in presets.json()} >= {"afro-house-warmup", "peak-amapiano"}

    bpm = test_client.post("/api/smart-crates/preview", json={"name": "BPM", "bpm_min": 123, "bpm_max": 126, "issue_free_only": False, "limit": 10})
    assert bpm.status_code == 200
    assert all(123 <= item["bpm"] <= 126 for item in bpm.json()["tracks"])

    genre = test_client.post("/api/smart-crates/preview", json={"name": "House", "genres": ["House"], "issue_free_only": False, "limit": 10})
    assert genre.status_code == 200
    assert genre.json()["tracks"]
    assert all(item["genre"] == "House" for item in genre.json()["tracks"])

    exact = test_client.post("/api/smart-crates/preview", json={"name": "Exact", "camelot_key": "8A", "harmonic_mode": "exact", "issue_free_only": False, "limit": 10})
    assert exact.status_code == 200
    assert all(item["key_camelot"] == "8A" for item in exact.json()["tracks"])

    clean = test_client.post("/api/smart-crates/preview", json={"name": "Clean", "issue_free_only": True, "limit": 10})
    assert clean.status_code == 200
    assert all("Issue-free" in item["reasons"] for item in clean.json()["tracks"])

    empty = test_client.post("/api/smart-crates/preview", json={"name": "None", "genres": ["Not A Genre"], "limit": 10})
    assert empty.status_code == 200
    assert empty.json()["tracks"] == []
    assert empty.json()["warnings"]


def test_smart_crates_save_creates_ordered_manual_crate(client):
    test_client, _root = client
    request = {"name": "Saved Smart House", "genres": ["House"], "issue_free_only": False, "limit": 3}
    preview = test_client.post("/api/smart-crates/preview", json=request)
    assert preview.status_code == 200
    preview_ids = [item["track_id"] for item in preview.json()["tracks"]]
    assert preview_ids

    saved = test_client.post("/api/smart-crates/save", json=request)
    assert saved.status_code == 201
    assert saved.json()["name"] == "Saved Smart House"
    assert [item["track_id"] for item in saved.json()["tracks"]] == preview_ids
    assert [item["position"] for item in saved.json()["tracks"]] == list(range(1, len(preview_ids) + 1))


def test_crate_exports_preview_order_formats_and_safe_write(client):
    test_client, root = client
    crate = test_client.post("/api/crates", json={"name": "Export / Safe", "notes": "Portable"}).json()
    ids = [item["id"] for item in test_client.get("/api/tracks", params={"limit": 2}).json()["items"]]
    for track_id in ids:
        assert test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": track_id}).status_code == 201

    listed = test_client.get("/api/exports/crates")
    assert listed.status_code == 200
    assert any(item["id"] == crate["id"] for item in listed.json())
    csv_preview = test_client.get(f"/api/exports/crates/{crate['id']}/preview", params={"format": "csv", "path_mode": "filename"})
    assert csv_preview.status_code == 200
    assert "position,title,artist" in csv_preview.json()["content"]
    assert [str(track_id) for track_id in ids] != []
    json_preview = test_client.get(f"/api/exports/crates/{crate['id']}/preview", params={"format": "json", "path_mode": "filename"})
    assert json_preview.status_code == 200
    assert [item["track_id"] for item in json.loads(json_preview.json()["content"])["tracks"]] == ids
    m3u8_preview = test_client.get(f"/api/exports/crates/{crate['id']}/preview", params={"format": "m3u8"})
    assert m3u8_preview.status_code == 200
    assert m3u8_preview.json()["content"].startswith("#EXTM3U\n#EXTINF:")

    exported = test_client.post(f"/api/exports/crates/{crate['id']}", json={"format": "m3u8", "path_mode": "filename"})
    assert exported.status_code == 201
    output = Path(exported.json()["output_path"])
    assert output.is_file()
    assert output.parent == root / "exports"
    assert output.name.startswith("Export_Safe_")
    assert output.suffix == ".m3u8"


def test_crate_exports_reject_invalid_options_and_handle_empty_crate(client):
    test_client, _root = client
    crate = test_client.post("/api/crates", json={"name": "Empty Export"}).json()
    empty = test_client.get(f"/api/exports/crates/{crate['id']}/preview", params={"format": "m3u"})
    assert empty.status_code == 200
    assert empty.json()["content"] == "#EXTM3U\n"
    assert empty.json()["warnings"]
    invalid = test_client.get(f"/api/exports/crates/{crate['id']}/preview", params={"format": "../../bad"})
    assert invalid.status_code == 422


def test_serato_staged_preview_and_write_preserve_crate_order(client):
    test_client, root = client
    crate = test_client.post("/api/crates", json={"name": "Serato / Safe"}).json()
    ids = [item["id"] for item in test_client.get("/api/tracks", params={"limit": 2}).json()["items"]]
    for track_id in ids:
        assert test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": track_id}).status_code == 201

    preview = test_client.get(f"/api/exports/serato/preview/{crate['id']}")
    assert preview.status_code == 200
    planned = preview.json()
    assert planned["exact_crate_binary_supported"] is False
    assert "live _Serato_ folder" in " ".join(planned["warnings"])
    assert planned["m3u8_content"].startswith("#EXTM3U\n")
    assert planned["m3u8_content"].find("alpha.mp3") < planned["m3u8_content"].find("beta.mp3")

    dry_run = test_client.post(f"/api/exports/serato/{crate['id']}", json={"dry_run": True})
    assert dry_run.status_code == 200
    assert dry_run.json()["written"] is False
    assert not Path(dry_run.json()["staged_directory"]).exists()

    written = test_client.post(f"/api/exports/serato/{crate['id']}", json={"dry_run": False})
    assert written.status_code == 200
    result = written.json()
    folder = Path(result["staged_directory"])
    assert folder.parent == root / "exports" / "serato"
    assert Path(result["m3u8_path"]).is_file()
    assert Path(result["manifest_path"]).is_file()
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["exact_crate_binary_supported"] is False
    assert manifest["track_count"] == 2

    second = test_client.post(f"/api/exports/serato/{crate['id']}", json={"dry_run": False})
    assert second.status_code == 200
    assert second.json()["staged_directory"] != result["staged_directory"]


def test_serato_staged_export_handles_empty_missing_paths_and_unsafe_destinations(client):
    test_client, root = client
    empty = test_client.post("/api/crates", json={"name": "Empty Serato"}).json()
    empty_preview = test_client.get(f"/api/exports/serato/preview/{empty['id']}")
    assert empty_preview.status_code == 200
    assert empty_preview.json()["track_count"] == 0
    assert "empty" in " ".join(empty_preview.json()["warnings"]).lower()

    crate = test_client.post("/api/crates", json={"name": "Missing Path"}).json()
    track_id = test_client.get("/api/tracks", params={"limit": 1}).json()["items"][0]["id"]
    assert test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": track_id}).status_code == 201
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    missing_preview = test_client.get(f"/api/exports/serato/preview/{crate['id']}")
    assert missing_preview.status_code == 200
    assert "no usable library path" in " ".join(missing_preview.json()["warnings"]).lower()

    unsafe = test_client.post(
        f"/api/exports/serato/{crate['id']}",
        json={"destination_mode": "custom", "destination_path": "../../_Serato_", "dry_run": False},
    )
    assert unsafe.status_code == 422


def test_rekordbox_staged_preview_and_write_preserve_crate_order(client):
    test_client, root = client
    crate = test_client.post("/api/crates", json={"name": "Rekordbox / Safe"}).json()
    ids = [item["id"] for item in test_client.get("/api/tracks", params={"limit": 2}).json()["items"]]
    for track_id in ids:
        assert test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": track_id}).status_code == 201

    preview = test_client.get(f"/api/exports/rekordbox/preview/{crate['id']}")
    assert preview.status_code == 200
    planned = preview.json()
    assert str(root) not in planned["xml_content"]
    document = ET.fromstring(planned["xml_content"])
    collection = document.find("COLLECTION")
    playlist = document.find("./PLAYLISTS/NODE/NODE")
    assert collection is not None and collection.attrib["Entries"] == "2"
    assert playlist is not None and playlist.attrib["Name"] == "Rekordbox / Safe"
    assert [track.attrib["Key"] for track in playlist.findall("TRACK")] == ["1", "2"]

    dry_run = test_client.post(f"/api/exports/rekordbox/{crate['id']}", json={"dry_run": True})
    assert dry_run.status_code == 200
    assert dry_run.json()["written"] is False
    assert not Path(dry_run.json()["output_path"]).exists()

    written = test_client.post(f"/api/exports/rekordbox/{crate['id']}", json={"dry_run": False})
    assert written.status_code == 200
    output = Path(written.json()["output_path"])
    assert output.is_file()
    assert output.parent == root / "exports" / "rekordbox"
    assert ET.parse(output).getroot().tag == "DJ_PLAYLISTS"

    second = test_client.post(f"/api/exports/rekordbox/{crate['id']}", json={"dry_run": False})
    assert second.status_code == 200
    assert second.json()["output_path"] != written.json()["output_path"]


def test_rekordbox_staged_export_escapes_metadata_and_handles_edge_cases(client):
    test_client, root = client
    crate = test_client.post("/api/crates", json={"name": "XML & <Safe>"}).json()
    track_id = test_client.get("/api/tracks", params={"limit": 1}).json()["items"][0]["id"]
    assert test_client.post(f"/api/crates/{crate['id']}/tracks", json={"track_id": track_id}).status_code == 201
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute("UPDATE tracks SET title = ?, filepath = ? WHERE id = ?", ("A & <B>", "", track_id))

    preview = test_client.get(f"/api/exports/rekordbox/preview/{crate['id']}")
    assert preview.status_code == 200
    planned = preview.json()
    assert "A &amp; &lt;B&gt;" in planned["xml_content"]
    assert "no usable library path" in " ".join(planned["warnings"]).lower()
    document = ET.fromstring(planned["xml_content"])
    assert document.find("./COLLECTION/TRACK").attrib["Name"] == "A & <B>"  # type: ignore[union-attr]

    empty = test_client.post("/api/crates", json={"name": "Empty Rekordbox"}).json()
    empty_preview = test_client.get(f"/api/exports/rekordbox/preview/{empty['id']}")
    assert empty_preview.status_code == 200
    assert empty_preview.json()["track_count"] == 0
    assert "empty" in " ".join(empty_preview.json()["warnings"]).lower()

    unsafe = test_client.post(
        f"/api/exports/rekordbox/{crate['id']}",
        json={"destination_mode": "../../Rekordbox", "dry_run": False},
    )
    assert unsafe.status_code == 422


def test_preview_audio_serves_allowed_file_and_byte_ranges(client):
    test_client, root = client
    audio_path = root / "library" / "house" / "alpha.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"ID3preview-audio-fixture")
    track_id = test_client.get("/api/tracks", params={"limit": 1}).json()["items"][0]["id"]

    full = test_client.get(f"/api/tracks/{track_id}/preview-audio")
    assert full.status_code == 200
    assert full.content == b"ID3preview-audio-fixture"
    assert full.headers["accept-ranges"] == "bytes"

    partial = test_client.get(f"/api/tracks/{track_id}/preview-audio", headers={"Range": "bytes=3-9"})
    assert partial.status_code == 206
    assert partial.content == b"preview"
    assert partial.headers["content-range"] == "bytes 3-9/24"


def test_preview_audio_rejects_unsafe_and_missing_files(client):
    test_client, root = client
    track_id = test_client.get("/api/tracks", params={"limit": 1}).json()["items"][0]["id"]
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute("UPDATE tracks SET filepath = ? WHERE id = ?", ("/tmp/outside-library.mp3", track_id))
    unsafe = test_client.get(f"/api/tracks/{track_id}/preview-audio")
    assert unsafe.status_code == 400
    assert "outside the selected library" in unsafe.json()["detail"]

    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute("UPDATE tracks SET filepath = ? WHERE id = ?", (str(root / "library" / "missing.mp3"), track_id))
    missing = test_client.get(f"/api/tracks/{track_id}/preview-audio")
    assert missing.status_code == 404
    assert "unavailable" in missing.json()["detail"].lower()


def test_settings_reports_safe_library_tools_and_locked_policies(client):
    test_client, root = client
    response = test_client.get("/api/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["library"]["mode"] == "configured"
    assert payload["library"]["processed_db"] == str(root / "logs" / "processed.db")
    assert payload["library"]["manual_crates_db"] == str(root / "logs" / "manual_crates.db")
    assert payload["library"]["exports_root"] == str(root / "exports")
    assert payload["library"]["library_initialized"] is True
    assert payload["library"]["pending_library_root"] is None
    assert payload["library"]["restart_required"] is False
    assert {tool["name"] for tool in payload["tools"]} == {"ffprobe", "ffmpeg", "keyfinder-cli", "aubio", "beet", "rmlint", "rsync"}
    assert payload["safety"]["mixed_in_key_authoritative"] is True
    assert payload["safety"]["no_live_serato_writes"] is True
    assert payload["preferences"]["default_export_path_mode"] == "filename"


def test_settings_persists_safe_preference_and_rejects_invalid_updates(client):
    test_client, root = client
    updated = test_client.patch("/api/settings", json={"default_export_path_mode": "relative"})
    assert updated.status_code == 200
    assert updated.json()["preferences"]["default_export_path_mode"] == "relative"
    settings_path = root / "logs" / "app_settings.json"
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {"default_export_path_mode": "relative"}
    assert test_client.get("/api/settings").json()["preferences"]["default_export_path_mode"] == "relative"
    invalid = test_client.patch("/api/settings", json={"default_export_path_mode": "../../unsafe"})
    assert invalid.status_code == 422


def test_settings_recognizes_the_repository_demo_library():
    assert settings_service._is_demo_root(Path(__file__).resolve().parents[1] / ".run" / "demo-library")


def test_settings_validates_and_saves_a_pending_library_root(client, monkeypatch, tmp_path):
    test_client, active_root = client
    pending_root = tmp_path / "next-library"
    pending_root.mkdir()
    local_env = tmp_path / "runtime" / "crateiq.env"
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", local_env)

    valid = test_client.post("/api/settings/library/validate", json={"library_root": str(pending_root)})
    assert valid.status_code == 200
    assert valid.json()["valid"] is True
    assert valid.json()["library_root"] == str(pending_root)

    saved = test_client.patch("/api/settings/library", json={"library_root": str(pending_root)})
    assert saved.status_code == 200
    library = saved.json()["library"]
    assert library["library_root"] == str(active_root)
    assert library["pending_library_root"] == str(pending_root)
    assert library["restart_required"] is True
    assert library["restart_command"] == "scripts/crateiq-local-services.sh stop && scripts/crateiq-local-services.sh start"
    assert local_env.read_text(encoding="utf-8").endswith(f"CRATEIQ_LIBRARY_ROOT={pending_root}\n")


def test_settings_library_root_validation_rejects_missing_files_and_forbidden_roots(client, tmp_path):
    test_client, _ = client
    missing = test_client.post("/api/settings/library/validate", json={"library_root": str(tmp_path / "missing")})
    assert missing.status_code == 422
    assert "does not exist" in missing.json()["detail"]

    file_path = tmp_path / "not-a-directory"
    file_path.write_text("fixture", encoding="utf-8")
    file_response = test_client.post("/api/settings/library/validate", json={"library_root": str(file_path)})
    assert file_response.status_code == 422
    assert "directory" in file_response.json()["detail"]

    forbidden = test_client.post("/api/settings/library/validate", json={"library_root": "/"})
    assert forbidden.status_code == 422
    assert "system or CrateIQ runtime" in forbidden.json()["detail"]


def test_library_initialize_is_idempotent_and_does_not_scan(client, tmp_path):
    test_client, _ = client
    library_root = tmp_path / "new-library"
    library_root.mkdir()
    (library_root / "Artist - Unindexed.mp3").write_bytes(b"not-real-audio")

    first = test_client.post("/api/settings/library/initialize", json={"library_root": str(library_root)})
    assert first.status_code == 200
    assert first.json()["initialized"] is True
    db_path = library_root / "logs" / "processed.db"
    assert db_path.is_file()
    assert (library_root / "exports").is_dir()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0

    second = test_client.post("/api/settings/library/initialize", json={"library_root": str(library_root)})
    assert second.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0

    unsafe = test_client.post("/api/settings/library/initialize", json={"library_root": "/"})
    assert unsafe.status_code == 422


def test_library_scan_preview_and_explicit_import_only_write_the_local_index(client, tmp_path):
    test_client, _ = client
    library_root = tmp_path / "scan-library"
    library_root.mkdir()
    audio = library_root / "sets" / "Artist One - First Track.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"fixture-audio")
    (library_root / "sets" / "notes.txt").write_text("ignore", encoding="utf-8")
    test_client.post("/api/settings/library/initialize", json={"library_root": str(library_root)}).raise_for_status()
    db_path = library_root / "logs" / "processed.db"

    preview = test_client.post("/api/library/scan-preview", json={"library_root": str(library_root)})
    assert preview.status_code == 200
    assert preview.json()["track_count"] == 1
    assert preview.json()["sample_tracks"] == ["sets/Artist One - First Track.mp3"]
    assert "sets/notes.txt" in preview.json()["unsupported_files"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0

    unconfirmed = test_client.post("/api/library/import", json={"library_root": str(library_root)})
    assert unconfirmed.status_code == 422
    imported = test_client.post("/api/library/import", json={"library_root": str(library_root), "confirm": True})
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT artist, title, bpm, key_camelot FROM tracks").fetchone()
    assert row == ("Artist One", "First Track", None, None)
    assert audio.read_bytes() == b"fixture-audio"
