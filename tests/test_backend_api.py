from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core.library_root import assert_path_under_root
from backend.app.services import analysis_jobs_service, mik_metadata_service, settings_service
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
    audio_path = root / "library" / "house" / "Muyè & Friends (Live).mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"ID3preview-audio-fixture")
    track_id = test_client.get("/api/tracks", params={"limit": 1}).json()["items"][0]["id"]
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute("UPDATE tracks SET filepath = ? WHERE id = ?", (str(audio_path), track_id))

    full = test_client.get(f"/api/tracks/{track_id}/preview-audio")
    assert full.status_code == 200
    assert full.content == b"ID3preview-audio-fixture"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"].startswith("audio/mpeg")

    partial = test_client.get(f"/api/tracks/{track_id}/preview-audio", headers={"Range": "bytes=3-9"})
    assert partial.status_code == 206
    assert partial.content == b"preview"
    assert partial.headers["content-range"] == "bytes 3-9/24"

    suffix = test_client.get(f"/api/tracks/{track_id}/preview-audio", headers={"Range": "bytes=-7"})
    assert suffix.status_code == 206
    assert suffix.content == b"fixture"
    assert suffix.headers["content-range"] == "bytes 17-23/24"

    invalid = test_client.get(f"/api/tracks/{track_id}/preview-audio", headers={"Range": "bytes=24-"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */24"


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
    assert payload["preferences"]["analysis"]["analyze_bpm"] is False
    assert payload["preferences"]["analysis"]["analyze_key"] is False
    assert payload["preferences"]["analysis"]["use_mik_when_present"] is True
    assert payload["capabilities"]["core"]["library_import"]["available"] is True
    assert payload["capabilities"]["policies"]["preserve_mik_values"] is True


def test_settings_persists_safe_preference_and_analysis_preferences(client):
    test_client, root = client
    updated = test_client.patch(
        "/api/settings",
        json={
            "default_export_path_mode": "relative",
            "analysis": {"analyze_bpm": True, "analyze_key": True, "use_external_tools": False},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["preferences"]["default_export_path_mode"] == "relative"
    assert updated.json()["preferences"]["analysis"] == {
        "analyze_bpm": True,
        "analyze_key": True,
        "use_mik_when_present": True,
        "preserve_existing_bpm_key_cues": True,
        "missing_data_only": True,
        "use_external_tools": False,
    }
    settings_path = root / "logs" / "app_settings.json"
    assert json.loads(settings_path.read_text(encoding="utf-8"))["analysis"]["analyze_bpm"] is True
    assert test_client.get("/api/settings").json()["preferences"]["default_export_path_mode"] == "relative"
    invalid = test_client.patch("/api/settings", json={"default_export_path_mode": "../../unsafe"})
    assert invalid.status_code == 422
    locked = test_client.patch("/api/settings", json={"analysis": {"missing_data_only": False}})
    assert locked.status_code == 422
    assert "locked safety policy" in locked.json()["detail"]


def test_settings_capabilities_only_disable_workflows_missing_their_tools(client, monkeypatch):
    test_client, _ = client
    missing_tools_report = {
        "status": "degraded",
        "checks": [
            {
                "name": f"binary_{name.replace('-', '_')}",
                "status": "warn",
                "message": f"{name} not found",
                "metadata": {"source": "PATH"},
            }
            for name in ("ffprobe", "ffmpeg", "keyfinder-cli", "aubio", "beet", "rmlint", "rsync")
        ],
    }
    monkeypatch.setattr(settings_service, "run_preflight", lambda: missing_tools_report)

    response = test_client.get("/api/settings/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert all(item["available"] is True for item in payload["core"].values())
    assert payload["analysis"]["bpm_analysis"]["status"] == "missing"
    assert payload["analysis"]["key_analysis"]["required_tool"] == "keyfinder-cli"
    assert payload["analysis"]["beets_enrichment"]["status"] == "missing"
    assert payload["analysis"]["duplicate_detection"]["status"] == "missing"
    assert payload["analysis"]["audio_quality_probe"]["status"] == "missing"
    assert payload["analysis"]["mixed_in_key_coverage"]["status"] == "available"
    assert payload["analysis"]["mixed_in_key_coverage"]["locked"] is True


def test_analysis_jobs_are_preview_only_and_tool_gated(client, monkeypatch):
    test_client, root = client
    missing_tools_report = {
        "status": "degraded",
        "checks": [
            {
                "name": f"binary_{name.replace('-', '_')}",
                "status": "warn",
                "message": f"{name} not found",
                "metadata": {"source": "PATH"},
            }
            for name in ("ffprobe", "ffmpeg", "keyfinder-cli", "aubio", "beet", "rmlint", "rsync")
        ],
    }
    monkeypatch.setattr(settings_service, "run_preflight", lambda: missing_tools_report)
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: None)
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: None)

    listed = test_client.get("/api/analysis/jobs")
    assert listed.status_code == 200
    jobs = {item["type"]: item for item in listed.json()["jobs"]}
    assert set(jobs) == {
        "mixed_in_key_coverage", "bpm_analysis", "key_analysis",
        "beets_enrichment", "duplicate_detection", "audio_quality_probe",
    }
    assert jobs["mixed_in_key_coverage"]["status"] == "ready"
    assert jobs["bpm_analysis"]["status"] == "missing_tool"
    assert jobs["key_analysis"]["status"] == "missing_tool"
    assert jobs["beets_enrichment"]["status"] == "missing_tool"
    assert jobs["duplicate_detection"]["status"] == "missing_tool"
    assert jobs["audio_quality_probe"]["status"] == "missing_tool"

    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT bpm, key_musical, key_camelot FROM tracks WHERE filename = 'delta.mp3'").fetchone()
    bpm_preview = test_client.get("/api/analysis/jobs/bpm_analysis/preview")
    key_preview = test_client.get("/api/analysis/jobs/key_analysis/preview")
    assert bpm_preview.status_code == 200
    assert bpm_preview.json()["candidate_count"] == 1
    assert key_preview.status_code == 200
    assert key_preview.json()["candidate_count"] == 1
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT bpm, key_musical, key_camelot FROM tracks WHERE filename = 'delta.mp3'").fetchone()
    assert after == before == (None, None, None)

    pending = test_client.post("/api/analysis/jobs/bpm_analysis/run", json={"confirm": True})
    assert pending.status_code == 409
    assert "aubio is not available" in pending.json()["detail"]
    mik_run = test_client.post("/api/analysis/jobs/mixed_in_key_coverage/run")
    assert mik_run.status_code == 409
    assert "Settings" in mik_run.json()["detail"]
    history = test_client.get("/api/analysis/jobs/history")
    assert history.status_code == 200
    assert history.json()["history"] == []


def test_bpm_runner_is_confirmed_aubio_only_and_preserves_existing_values(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    delta_path = root / "library" / "misc" / "delta.mp3"
    delta_path.parent.mkdir(parents=True, exist_ok=True)
    delta_path.write_bytes(b"not-real-audio")
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE tracks ADD COLUMN bpm_source TEXT")
        conn.execute("ALTER TABLE tracks ADD COLUMN bpm_trusted INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE tracks SET bpm_source = 'mixed_in_key', bpm_trusted = 1 WHERE filename = 'alpha.mp3'")

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")

    def fake_aubio(command, **kwargs):
        assert command[:2] == ["/fake/aubio", "tempo"]
        assert kwargs["timeout"] == 20
        return SimpleNamespace(returncode=0, stdout="0.371 123.26\n1.100 123.26\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_aubio)
    preview = test_client.get("/api/analysis/jobs/bpm_analysis/preview")
    assert preview.status_code == 200
    assert preview.json()["candidate_count"] == 1
    assert preview.json()["runner_implemented"] is True
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT bpm FROM tracks WHERE filename = 'delta.mp3'").fetchone()
    assert before == (None,)

    unconfirmed = test_client.post("/api/analysis/jobs/bpm_analysis/run", json={"confirm": False, "limit": 1})
    assert unconfirmed.status_code == 422
    completed = test_client.post("/api/analysis/jobs/bpm_analysis/run", json={"confirm": True, "limit": 1})
    assert completed.status_code == 200
    assert completed.json()["updated"] == 1
    assert completed.json()["remaining_missing_bpm"] == 0
    with sqlite3.connect(db_path) as conn:
        delta = conn.execute("SELECT bpm, bpm_source, bpm_trusted, bpm_analyzed_at FROM tracks WHERE filename = 'delta.mp3'").fetchone()
        alpha = conn.execute("SELECT bpm, bpm_source, bpm_trusted FROM tracks WHERE filename = 'alpha.mp3'").fetchone()
    assert delta[:3] == (123.26, "aubio", 0)
    assert delta[3]
    assert alpha == (120.0, "mixed_in_key", 1)
    assert test_client.get("/api/analysis/jobs/bpm_analysis/preview").json()["candidate_count"] == 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET bpm = NULL WHERE filename = 'delta.mp3'")

    def timeout_aubio(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="aubio", timeout=20)

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", timeout_aubio)
    timed_out = test_client.post("/api/analysis/jobs/bpm_analysis/run", json={"confirm": True, "limit": 1})
    assert timed_out.status_code == 200
    assert timed_out.json()["updated"] == 0
    assert timed_out.json()["failed"] == 1


def test_aubio_bpm_parser_accepts_known_output_and_rejects_implausible_values():
    assert analysis_jobs_service._parse_aubio_bpm("128.000000\n") == 128.0
    assert analysis_jobs_service._parse_aubio_bpm("0.371 123.26\n1.100 123.26 bpm\n") == 123.26
    assert analysis_jobs_service._parse_aubio_bpm("12.0 bpm\n") is None
    assert analysis_jobs_service._parse_aubio_bpm("300.0 bpm\n") is None


def test_key_runner_is_confirmed_and_preserves_existing_keys(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    delta_path = root / "library" / "misc" / "delta.mp3"
    delta_path.parent.mkdir(parents=True, exist_ok=True)
    delta_path.write_bytes(b"not-real-audio")
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE tracks ADD COLUMN key_source TEXT")
        conn.execute("ALTER TABLE tracks ADD COLUMN key_trusted INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE tracks SET key_musical = 'A minor', key_camelot = '8A', key_source = 'mixed_in_key', key_trusted = 1 WHERE filename = 'alpha.mp3'")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder-cli")

    def fake_keyfinder(command, **kwargs):
        assert command[0] == "/fake/keyfinder-cli" and len(command) == 2
        assert kwargs["timeout"] == 20
        return SimpleNamespace(returncode=0, stdout="Key: Am\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_keyfinder)
    preview = test_client.get("/api/analysis/jobs/key_analysis/preview")
    assert preview.status_code == 200 and preview.json()["candidate_count"] == 1
    assert preview.json()["runner_implemented"] is True
    assert test_client.post("/api/analysis/jobs/key_analysis/run", json={"confirm": False}).status_code == 422
    completed = test_client.post("/api/analysis/jobs/key_analysis/run", json={"confirm": True, "limit": 1})
    assert completed.status_code == 200 and completed.json()["updated"] == 1
    with sqlite3.connect(db_path) as conn:
        delta = conn.execute("SELECT key_musical, key_camelot, key_source, key_trusted, key_analyzed_at FROM tracks WHERE filename = 'delta.mp3'").fetchone()
        alpha = conn.execute("SELECT key_musical, key_camelot, key_source, key_trusted FROM tracks WHERE filename = 'alpha.mp3'").fetchone()
    assert delta[:4] == ("Am", "8A", "keyfinder-cli", 0) and delta[4]
    assert alpha == ("A minor", "8A", "mixed_in_key", 1)
    assert test_client.get("/api/analysis/jobs/key_analysis/preview").json()["candidate_count"] == 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET key_musical = NULL, key_camelot = NULL WHERE filename = 'delta.mp3'")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: None)
    missing = test_client.post("/api/analysis/jobs/key_analysis/run", json={"confirm": True})
    assert missing.status_code == 409 and "keyfinder-cli is not available" in missing.json()["detail"]


def test_keyfinder_parser_accepts_known_keys_and_rejects_unknown_values():
    assert analysis_jobs_service._parse_keyfinder_output("Am\n") == ("Am", "8A")
    assert analysis_jobs_service._parse_keyfinder_output("Detected key: 9B\n") == ("G major", "9B")
    assert analysis_jobs_service._parse_keyfinder_output("not a key\n") == (None, None)


def test_beets_preview_is_local_db_only_and_never_targets_analysis_fields(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    monkeypatch.setattr(
        analysis_jobs_service.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("beet must not run during preview")),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET genre = NULL WHERE filename = 'delta.mp3'")
        before = conn.execute("SELECT bpm, key_musical, key_camelot, genre FROM tracks WHERE filename = 'delta.mp3'").fetchone()
    preview = test_client.get("/api/analysis/jobs/beets_enrichment/preview")
    assert preview.status_code == 200
    assert preview.json()["candidate_count"] == 1
    assert preview.json()["samples"][0]["missing_fields"] == ["genre"]
    assert preview.json()["runner_implemented"] is False
    assert any("does not invoke beet" in warning for warning in preview.json()["warnings"])
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT bpm, key_musical, key_camelot, genre FROM tracks WHERE filename = 'delta.mp3'").fetchone()
    assert after == before

    run = test_client.post("/api/analysis/jobs/beets_enrichment/run", json={"confirm": True})
    assert run.status_code == 409
    assert "not implemented" in run.json()["detail"]


def test_beets_review_applies_only_saved_selected_local_fields(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    monkeypatch.setattr(
        analysis_jobs_service.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Beets review must not spawn a subprocess")),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET genre = NULL WHERE filename = 'delta.mp3'")
        before = conn.execute(
            "SELECT id, artist, title, genre, bpm, key_musical, key_camelot FROM tracks WHERE filename = 'delta.mp3'"
        ).fetchone()

    empty = test_client.get("/api/enrichment/beets/review")
    assert empty.status_code == 200 and empty.json()["items"] == []
    refreshed = test_client.post("/api/enrichment/beets/preview-refresh")
    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["source"] == "crateiq_metadata_candidate"
    assert payload["safety"] == ["db_only_apply", "review_before_apply", "no_tag_writes", "no_file_moves", "no_audio_changes", "no_bpm_key_camelot_cue_changes"]
    item = next(item for item in payload["items"] if item["track_id"] == before[0])
    assert item["allowed_fields"] == ["genre"]
    assert item["selected_fields"] == {}

    unsaved = test_client.post("/api/enrichment/beets/apply", json={"confirm": True, "items": [{"track_id": before[0], "fields": {"genre": "Afro House"}}]})
    assert unsaved.status_code == 200
    assert unsaved.json()["failed"] == 1
    assert "Save the selected fields" in unsaved.json()["warnings"][0]
    missing_confirm = test_client.post("/api/enrichment/beets/apply", json={"confirm": False, "items": [{"track_id": before[0], "fields": {"genre": "Afro House"}}]})
    assert missing_confirm.status_code == 422
    forbidden = test_client.patch(
        f"/api/enrichment/beets/tracks/{before[0]}",
        json={"decision": "pending", "selected_fields": {"bpm": "128"}},
    )
    assert forbidden.status_code == 422

    saved = test_client.patch(
        f"/api/enrichment/beets/tracks/{before[0]}",
        json={"decision": "pending", "note": "Use this local genre", "selected_fields": {"genre": "Afro House"}},
    )
    assert saved.status_code == 200
    assert next(item for item in saved.json()["items"] if item["track_id"] == before[0])["selected_fields"] == {"genre": "Afro House"}
    applied = test_client.post("/api/enrichment/beets/apply", json={"confirm": True, "items": [{"track_id": before[0], "fields": {"genre": "Afro House"}}]})
    assert applied.status_code == 200 and applied.json()["applied"] == 1
    assert applied.json()["review"]["summary"]["applied"] == 1
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT artist, title, genre, bpm, key_musical, key_camelot, enrichment_source, enrichment_updated_at FROM tracks WHERE id = ?",
            (before[0],),
        ).fetchone()
    assert after[:6] == (before[1], before[2], "Afro House", before[4], before[5], before[6])
    assert after[6] == "crateiq_metadata_candidate" and after[7]

    repeated = test_client.post("/api/enrichment/beets/apply", json={"confirm": True, "items": [{"track_id": before[0], "fields": {"genre": "Afro House"}}]})
    assert repeated.status_code == 200 and repeated.json()["skipped"] == 1
    assert "overwrite is not supported" in repeated.json()["warnings"][0]
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"beets_review_snapshots", "beets_review_decisions"}.issubset(tables)


def test_duplicate_preview_uses_rmlint_json_only_and_never_writes(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    alpha_path = root / "library" / "house" / "alpha.mp3"
    beta_path = root / "library" / "house" / "beta.mp3"
    alpha_path.parent.mkdir(parents=True, exist_ok=True)
    alpha_path.write_bytes(b"same-safe-test-audio-bytes")
    beta_path.write_bytes(b"same-safe-test-audio-bytes")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET filesize_bytes = ? WHERE filename IN ('alpha.mp3', 'beta.mp3')", (alpha_path.stat().st_size,))
        before = conn.execute("SELECT id, filepath, filesize_bytes FROM tracks ORDER BY id").fetchall()

    monkeypatch.setattr(analysis_jobs_service, "_resolve_rmlint_binary", lambda: "/fake/rmlint")

    def fake_rmlint(command, **kwargs):
        assert command[:6] == ["/fake/rmlint", "-T", "df", "-o", "json", "--no-followlinks"]
        assert "--no-with-color" in command and "--no-crossdev" in command
        assert not {"--dedupe", "--followlinks", "--xattr"}.intersection(command)
        assert kwargs.get("shell", False) is False
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0, stdout=json.dumps([
            {"description": "rmlint json-dump"},
            {"type": "duplicate_file", "checksum": "same-content", "path": str(alpha_path), "size": alpha_path.stat().st_size},
            {"type": "duplicate_file", "checksum": "same-content", "path": str(beta_path), "size": beta_path.stat().st_size},
            {"duplicates": 1},
        ]), stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_rmlint)
    listed = {job["type"]: job for job in test_client.get("/api/analysis/jobs").json()["jobs"]}
    assert listed["duplicate_detection"]["status"] == "ready"
    assert listed["duplicate_detection"]["candidate_count"] == 2
    preview = test_client.get("/api/analysis/jobs/duplicate_detection/preview")
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["preview_only"] is True and payload["runner_implemented"] is False
    assert payload["summary"] == {"total_tracks_checked": 2, "duplicate_groups": 1, "duplicate_candidates": 2}
    assert payload["groups"][0]["reason"] == "rmlint duplicate"
    assert [item["relative_path"] for item in payload["groups"][0]["items"]] == ["library/house/alpha.mp3", "library/house/beta.mp3"]
    assert all(not item["relative_path"].startswith(str(root)) for item in payload["groups"][0]["items"])
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT id, filepath, filesize_bytes FROM tracks ORDER BY id").fetchall()
    assert after == before

    pending = test_client.post("/api/analysis/jobs/duplicate_detection/run", json={"confirm": True})
    assert pending.status_code == 409
    assert "preview-only" in pending.json()["detail"]

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("rmlint", 30)))
    timed_out = test_client.get("/api/analysis/jobs/duplicate_detection/preview")
    assert timed_out.status_code == 200
    assert any("timed out" in warning for warning in timed_out.json()["warnings"])


def test_duplicate_review_persists_db_only_decisions(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    alpha_path = root / "library" / "house" / "alpha.mp3"
    beta_path = root / "library" / "house" / "beta.mp3"
    alpha_path.parent.mkdir(parents=True, exist_ok=True)
    alpha_path.write_bytes(b"same-review-safe-bytes")
    beta_path.write_bytes(b"same-review-safe-bytes")
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT id, filepath, filename FROM tracks ORDER BY id").fetchall()

    monkeypatch.setattr(analysis_jobs_service, "_resolve_rmlint_binary", lambda: "/fake/rmlint")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps([
                {"type": "duplicate_file", "checksum": "review-content", "path": str(alpha_path)},
                {"type": "duplicate_file", "checksum": "review-content", "path": str(beta_path)},
            ]),
            stderr="",
        ),
    )

    empty = test_client.get("/api/duplicates/review")
    assert empty.status_code == 200
    assert empty.json()["groups"] == []
    assert "Refresh" in empty.json()["message"]

    refreshed = test_client.post("/api/duplicates/review/preview-refresh")
    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["safety"] == ["db_only_review", "no_delete", "no_move", "no_rename", "no_quarantine", "no_tag_writes"]
    assert payload["summary"] == {"groups": 1, "candidates": 2, "unresolved": 2, "keep": 0, "ignore": 0, "review_later": 0}
    item = payload["groups"][0]["items"][0]
    assert not item["relative_path"].startswith(str(root))

    saved = test_client.patch(
        f"/api/duplicates/review/groups/dup-1/items/{item['track_id']}",
        json={"decision": "keep", "note": "Use this local copy"},
    )
    assert saved.status_code == 200
    assert saved.json()["summary"]["keep"] == 1
    assert saved.json()["groups"][0]["items"][0]["note"] == "Use this local copy"
    reread = test_client.get("/api/duplicates/review")
    assert reread.json()["groups"][0]["items"][0]["decision"] == "keep"

    invalid = test_client.patch(
        f"/api/duplicates/review/groups/dup-1/items/{item['track_id']}",
        json={"decision": "delete", "note": "No"},
    )
    assert invalid.status_code == 422
    missing = test_client.patch("/api/duplicates/review/groups/not-a-group/items/1", json={"decision": "ignore"})
    assert missing.status_code == 404
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT id, filepath, filename FROM tracks ORDER BY id").fetchall()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert after == before
    assert {"duplicate_review_snapshots", "duplicate_review_decisions"}.issubset(tables)
    assert alpha_path.read_bytes() == beta_path.read_bytes() == b"same-review-safe-bytes"


def test_audio_quality_preview_uses_ffprobe_json_only_and_never_writes(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    alpha_path = root / "library" / "house" / "alpha.mp3"
    alpha_path.parent.mkdir(parents=True, exist_ok=True)
    alpha_path.write_bytes(b"safe-quality-preview-fixture")
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT id, filepath, bpm, key_musical, key_camelot FROM tracks ORDER BY id").fetchall()

    tool_report = {
        "status": "ready",
        "checks": [
            {"name": "binary_ffprobe", "status": "pass", "message": "ffprobe available", "metadata": {"source": "PATH"}},
            {"name": "binary_ffmpeg", "status": "warn", "message": "ffmpeg missing", "metadata": {"source": "PATH"}},
        ],
    }
    monkeypatch.setattr(settings_service, "run_preflight", lambda: tool_report)
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffprobe_binary", lambda: "/fake/ffprobe")

    def fake_ffprobe(command, **kwargs):
        assert command == [
            "/fake/ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(alpha_path),
        ]
        assert kwargs.get("shell", False) is False
        assert kwargs["timeout"] == 15
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "format": {"format_name": "mp3", "duration": "201.25", "bit_rate": "192000", "size": "1234"},
            "streams": [{"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2}],
        }), stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_ffprobe)
    capability = test_client.get("/api/settings/capabilities").json()["analysis"]["audio_quality_probe"]
    assert capability["available"] is True
    assert capability["required_tools"] == ["ffprobe"]
    listed = {job["type"]: job for job in test_client.get("/api/analysis/jobs").json()["jobs"]}
    assert listed["audio_quality_probe"]["status"] == "ready"
    assert listed["audio_quality_probe"]["write_behavior"].startswith("preview_only")

    preview = test_client.get("/api/analysis/jobs/audio_quality_probe/preview")
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["preview_only"] is True and payload["runner_implemented"] is False
    alpha = payload["quality_probes"][0]
    assert alpha == {
        "track_id": 1, "filename": "alpha.mp3", "relative_path": "library/house/alpha.mp3",
        "status": "probe_ok", "container": "mp3", "codec": "mp3", "duration_sec": 201.25,
        "bitrate_kbps": 192, "sample_rate_hz": 44100, "channels": 2, "file_size_bytes": 1234,
    }
    assert all(not (item["relative_path"] or "").startswith(str(root)) for item in payload["quality_probes"])
    assert any("No transcode" in warning or "No transcode" in warning.capitalize() for warning in payload["warnings"])
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT id, filepath, bpm, key_musical, key_camelot FROM tracks ORDER BY id").fetchall()
    assert after == before


def test_quality_review_persists_ffprobe_findings_and_decisions(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    alpha_path = root / "library" / "house" / "alpha.mp3"
    alpha_path.parent.mkdir(parents=True, exist_ok=True)
    alpha_path.write_bytes(b"safe-quality-review-fixture")
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT id, filepath, filename FROM tracks ORDER BY id").fetchall()

    monkeypatch.setattr(
        settings_service,
        "run_preflight",
        lambda: {"status": "ready", "checks": [
            {"name": "binary_ffprobe", "status": "pass", "message": "ffprobe available", "metadata": {"source": "PATH"}},
            {"name": "binary_ffmpeg", "status": "warn", "message": "ffmpeg missing", "metadata": {"source": "PATH"}},
        ]},
    )
    monkeypatch.setattr(analysis_jobs_service, "_resolve_ffprobe_binary", lambda: "/fake/ffprobe")

    def fake_ffprobe(command, **kwargs):
        assert command == ["/fake/ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(alpha_path)]
        assert kwargs.get("shell", False) is False
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "format": {"format_name": "mp3", "bit_rate": "128000", "size": "1234"},
            "streams": [{"codec_type": "audio", "codec_name": "mp3", "sample_rate": "44100", "channels": 2}],
        }), stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_ffprobe)
    empty = test_client.get("/api/quality/review")
    assert empty.status_code == 200 and empty.json()["items"] == []

    refreshed = test_client.post("/api/quality/review/preview-refresh")
    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["safety"] == ["db_only_review", "probe_only", "no_transcode", "no_file_writes", "no_tag_writes"]
    assert payload["low_bitrate_threshold_kbps"] == 192
    alpha = next(item for item in payload["items"] if item["track_id"] == 1)
    assert alpha["status"] == "probe_ok"
    assert alpha["flags"] == ["missing_duration", "low_bitrate"]
    assert alpha["relative_path"] == "library/house/alpha.mp3"
    assert payload["summary"]["tracks_checked"] == len(before)
    assert payload["summary"]["findings"] == len(before)

    saved = test_client.patch("/api/quality/review/tracks/1", json={"decision": "reviewed", "note": "Checked locally"})
    assert saved.status_code == 200
    assert saved.json()["summary"]["reviewed"] == 1
    assert next(item for item in saved.json()["items"] if item["track_id"] == 1)["note"] == "Checked locally"
    reread = test_client.get("/api/quality/review")
    assert next(item for item in reread.json()["items"] if item["track_id"] == 1)["decision"] == "reviewed"

    invalid = test_client.patch("/api/quality/review/tracks/1", json={"decision": "transcode"})
    assert invalid.status_code == 422
    missing = test_client.patch("/api/quality/review/tracks/999", json={"decision": "ignore"})
    assert missing.status_code == 404
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT id, filepath, filename FROM tracks ORDER BY id").fetchall()
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert after == before
    assert {"quality_review_snapshots", "quality_review_decisions"}.issubset(tables)
    assert alpha_path.read_bytes() == b"safe-quality-review-fixture"

    pending = test_client.post("/api/analysis/jobs/audio_quality_probe/run", json={"confirm": True})
    assert pending.status_code == 409
    assert "preview-only" in pending.json()["detail"]

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffprobe", 15)))
    timed_out = test_client.get("/api/analysis/jobs/audio_quality_probe/preview")
    assert timed_out.status_code == 200
    assert timed_out.json()["quality_probes"][0]["status"] == "probe_error"
    assert any("timed out" in warning for warning in timed_out.json()["warnings"])


def test_mik_coverage_preview_and_db_only_import(client, monkeypatch):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id, filepath FROM tracks ORDER BY id LIMIT 3").fetchall()
        conn.execute("UPDATE tracks SET bpm = NULL, key_musical = NULL, key_camelot = NULL WHERE id IN (?, ?)", (rows[0][0], rows[1][0]))
        conn.execute(
            "UPDATE tracks SET bpm = 128.0, key_musical = 'F minor', key_camelot = '4A' WHERE id = ?",
            (rows[2][0],),
        )

    for _, filepath in rows:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tag-fixture")

    def fake_tag_reader(path: Path):
        if path.name == Path(rows[0][1]).name:
            return 122.0, "8A", "A minor"
        if path.name == Path(rows[1][1]).name:
            return None, "3B", "Db major"
        return 110.0, "9B", "G major"

    monkeypatch.setattr(mik_metadata_service, "_safe_tag_values", fake_tag_reader)

    coverage = test_client.get("/api/analysis/mik/coverage")
    assert coverage.status_code == 200
    assert coverage.json()["cue_support"] == "unavailable"
    assert coverage.json()["summary"]["fallback_bpm_candidates"] >= 2

    preview = test_client.post("/api/analysis/mik/preview")
    assert preview.status_code == 200
    assert preview.json()["summary"]["with_bpm"] >= 1
    assert preview.json()["summary"]["with_camelot"] >= 2
    assert len(preview.json()["samples"]) == 3
    with sqlite3.connect(db_path) as conn:
        unchanged = conn.execute("SELECT bpm, key_camelot FROM tracks WHERE id = ?", (rows[0][0],)).fetchone()
    assert unchanged == (None, None)

    imported = test_client.post("/api/analysis/mik/import")
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 2
    with sqlite3.connect(db_path) as conn:
        first = conn.execute("SELECT bpm, key_camelot, bpm_source, key_source, metadata_trusted FROM tracks WHERE id = ?", (rows[0][0],)).fetchone()
        second = conn.execute("SELECT bpm, key_camelot, bpm_source, key_source, metadata_trusted FROM tracks WHERE id = ?", (rows[1][0],)).fetchone()
        preserved = conn.execute("SELECT bpm, key_camelot FROM tracks WHERE id = ?", (rows[2][0],)).fetchone()
    assert first == (122.0, "8A", "mik_compatible_tag", "mik_compatible_tag", 1)
    assert second == (None, "3B", None, "mik_compatible_tag", 1)
    assert preserved == (128.0, "4A")

    repeated = test_client.post("/api/analysis/mik/import")
    assert repeated.status_code == 200
    assert repeated.json()["imported_count"] == 0
    assert repeated.json()["unchanged_count"] >= 2


def test_settings_recognizes_the_repository_demo_library():
    assert settings_service._is_demo_root(Path(__file__).resolve().parents[1] / ".run" / "demo-library")


def test_metadata_sources_are_safe_local_settings_and_never_echo_credentials(client, monkeypatch, tmp_path):
    test_client, _ = client
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")

    initial = test_client.get("/api/settings/metadata-sources")
    assert initial.status_code == 200
    sources = {source["id"]: source for source in initial.json()["sources"]}
    assert {"local_tags", "filename_hints", "mixed_in_key", "beets", "musicbrainz", "discogs", "spotify", "deezer", "beatport", "lastfm"} == set(sources)
    assert sources["spotify"]["enabled"] is False
    assert sources["mixed_in_key"]["credentials_status"] == "not_required"

    saved = test_client.patch("/api/settings/metadata-sources", json={"sources": [{
        "id": "spotify", "enabled": True, "priority": 33,
        "credentials": {"client_id": "safe-client-id", "client_secret": "safe-client-secret"},
    }]})
    assert saved.status_code == 200
    payload = saved.json()
    assert "safe-client-id" not in json.dumps(payload)
    assert "safe-client-secret" not in json.dumps(payload)
    spotify = next(source for source in payload["sources"] if source["id"] == "spotify")
    assert spotify["enabled"] is True
    assert spotify["priority"] == 33
    assert spotify["credentials_status"] == "saved"
    assert set(spotify["saved_credential_fields"]) == {"client_id", "client_secret"}

    invalid_field = test_client.patch("/api/settings/metadata-sources", json={"sources": [{"id": "spotify", "credentials": {"token": "nope"}}]})
    assert invalid_field.status_code == 422
    unknown = test_client.patch("/api/settings/metadata-sources", json={"sources": [{"id": "unknown", "enabled": True}]})
    assert unknown.status_code == 422

    tested = test_client.post("/api/settings/metadata-sources/spotify/test")
    assert tested.status_code == 200
    assert tested.json()["connection_status"] == "not_implemented"
    assert tested.json()["network_used"] is False

    cleared = test_client.post("/api/settings/metadata-sources/spotify/clear-credentials")
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True
    after_clear = test_client.get("/api/settings/metadata-sources").json()
    assert next(source for source in after_clear["sources"] if source["id"] == "spotify")["credentials_status"] == "missing"


def test_multisource_enrichment_review_is_local_only_and_rejects_forbidden_fields(client):
    test_client, _ = client
    preview = test_client.post("/api/enrichment/review/preview-refresh")
    assert preview.status_code == 200
    body = preview.json()
    assert "no_tag_writes" in body["safety"]
    assert any("No external API calls" in warning for warning in body["warnings"])
    assert all(item["source_id"] in {"filename_hints", "local_tags", "beets"} for item in body["items"])
    assert all("bpm" not in item["allowed_fields"] for item in body["items"])
    assert test_client.post("/api/enrichment/review/apply", json={"items": []}).status_code == 422
    if body["items"]:
        item = body["items"][0]
        forbidden = test_client.patch(f"/api/enrichment/review/tracks/{item['track_id']}/suggestions/{item['suggestion_id']}", json={"selected_fields": {"bpm": "120"}})
        assert forbidden.status_code == 422


def test_listening_review_is_db_only_and_validates_review_fields(client):
    test_client, root = client
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        track_id = conn.execute("SELECT id FROM tracks LIMIT 1").fetchone()[0]
        before = conn.execute("SELECT bpm, key_camelot FROM tracks WHERE id=?", (track_id,)).fetchone()
    initial = test_client.get("/api/reviews/tracks").json()
    item = next(row for row in initial["items"] if row["track_id"] == track_id)
    assert item["review_status"] == "unreviewed" and item["rating"] is None
    saved = test_client.patch(f"/api/reviews/tracks/{track_id}", json={"review_status": "favorite", "rating": 5, "notes": "Warmup"})
    assert saved.status_code == 200 and saved.json()["review_status"] == "favorite"
    assert test_client.get("/api/reviews/tracks", params={"status": "favorite"}).json()["summary"]["favorite"] >= 1
    assert test_client.patch(f"/api/reviews/tracks/{track_id}", json={"review_status": "invalid"}).status_code == 422
    assert test_client.patch(f"/api/reviews/tracks/{track_id}", json={"rating": 6}).status_code == 422
    played = test_client.post(f"/api/reviews/tracks/{track_id}/played").json()
    assert played["play_count"] == 1 and played["last_played_at"]
    assert test_client.get("/api/reviews/tracks/999999").status_code == 404
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        assert conn.execute("SELECT bpm, key_camelot FROM tracks WHERE id=?", (track_id,)).fetchone() == before


def test_review_summary_is_read_only_and_returns_requested_ids(client):
    test_client, root = client
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM tracks LIMIT 2")]
        before = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='track_reviews'").fetchone()[0]
    response = test_client.get("/api/reviews/summary", params={"track_ids": f"{ids[0]},bad,{ids[1]},999999"})
    assert response.status_code == 200
    assert set(response.json()["reviews"]) == {str(value) for value in ids}
    assert response.json()["reviews"][str(ids[0])]["review_status"] == "unreviewed"
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='track_reviews'").fetchone()[0] == before


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
    nested_audio = library_root / "music" / "afro" / "Artist Two - Nested Track.flac"
    nested_audio.parent.mkdir(parents=True)
    nested_audio.write_bytes(b"fixture-audio")
    (library_root / "sets" / "notes.txt").write_text("ignore", encoding="utf-8")
    test_client.post("/api/settings/library/initialize", json={"library_root": str(library_root)}).raise_for_status()
    db_path = library_root / "logs" / "processed.db"

    preview = test_client.post("/api/library/scan-preview", json={"library_root": str(library_root)})
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["track_count"] == 2
    assert preview_payload["supported_audio_files"] == 2
    assert preview_payload["total_files"] == 3
    assert preview_payload["unsupported_file_count"] == 1
    assert preview_payload["folders_scanned"] >= 3
    assert preview_payload["importable"] is True
    assert preview_payload["sample_tracks"] == [
        "music/afro/Artist Two - Nested Track.flac",
        "sets/Artist One - First Track.mp3",
    ]
    assert "sets/notes.txt" in preview.json()["unsupported_files"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0

    unconfirmed = test_client.post("/api/library/import", json={"library_root": str(library_root)})
    assert unconfirmed.status_code == 422
    imported = test_client.post("/api/library/import", json={"library_root": str(library_root), "confirm": True})
    assert imported.status_code == 200
    assert imported.json()["imported_count"] == 2
    assert imported.json()["existing_count"] == 0
    assert imported.json()["total_indexed_count"] == 2
    assert imported.json()["next_actions"][0]["route"] == "/"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT artist, title, bpm, key_camelot FROM tracks ORDER BY filepath").fetchall()
    assert rows == [
        ("Artist Two", "Nested Track", None, None),
        ("Artist One", "First Track", None, None),
    ]
    repeated = test_client.post("/api/library/import", json={"library_root": str(library_root), "confirm": True})
    assert repeated.status_code == 200
    assert repeated.json()["imported_count"] == 0
    assert repeated.json()["existing_count"] == 2
    assert repeated.json()["total_indexed_count"] == 2
    assert audio.read_bytes() == b"fixture-audio"
    assert nested_audio.read_bytes() == b"fixture-audio"
