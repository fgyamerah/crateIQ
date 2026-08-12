"""Stage 1 read-only reference-artifact reconciliation coverage."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.services import settings_service, track_source_service


def _db(root: Path) -> Path:
    path = root / "logs" / "processed.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, filename TEXT NOT NULL)")
    return path


@pytest.fixture()
def reference_client(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", tmp_path / "local" / "crateiq.env")
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    yield TestClient(backend_main.app), root, _db(root), backend_db.JOBS_DB_PATH


def _find(payload, artifact_type, field=None):
    return next(item for item in payload["findings"] if item["artifact_type"] == artifact_type and (field is None or item["reference_field"] == field))


def test_cue_point_path_stale_detected_without_mutation(reference_client):
    client, root, path, _jobs = reference_client
    old = root / "Inbox" / "old.mp3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cue_points (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL)")
        conn.execute("INSERT INTO cue_points VALUES (1, ?)", (str(old),))
        before = conn.execute("SELECT * FROM cue_points").fetchall()
    payload = client.get("/api/reconciliation/reference-findings").json()
    finding = _find(payload, "cue_point")
    assert finding["classification"] == "B"
    assert finding["stale_value"] == "Inbox/old.mp3"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM cue_points").fetchall() == before


def test_playlist_track_path_stale_detected(reference_client):
    client, root, path, _jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE set_playlist_tracks (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL)")
        conn.execute("INSERT INTO set_playlist_tracks VALUES (1, ?)", (str(root / "Library" / "old.mp3"),))
    assert _find(client.get("/api/reconciliation/reference-findings").json(), "playlist_track")["classification"] == "B"


def test_crate_track_orphaned_and_not_stale_on_rename(reference_client):
    client, root, path, _jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?, 'x.mp3')", (str(root / "Library" / "renamed.mp3"),))
    crates = root / "logs" / "manual_crates.db"
    with sqlite3.connect(crates) as conn:
        conn.execute("CREATE TABLE manual_crate_tracks (crate_id INTEGER, track_id INTEGER, position INTEGER)")
        conn.execute("INSERT INTO manual_crate_tracks VALUES (1, 1, 0)")
        conn.execute("INSERT INTO manual_crate_tracks VALUES (1, 99, 1)")
    payload = client.get("/api/reconciliation/reference-findings").json()
    crate_findings = [f for f in payload["findings"] if f["artifact_type"] == "crate_track"]
    assert len(crate_findings) == 1
    assert crate_findings[0]["stale_value"] == 99


def test_repair_queue_stale_path_detected_as_regenerate(reference_client):
    client, root, path, _jobs = reference_client
    current = root / "Library" / "new.mp3"
    current.parent.mkdir()
    current.write_bytes(b"audio")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?, 'new.mp3')", (str(current),))
        conn.execute("CREATE TABLE metadata_repair_queue (id INTEGER PRIMARY KEY, track_id INTEGER, relative_path TEXT)")
        conn.execute("INSERT INTO metadata_repair_queue VALUES (1, 1, ?)", (str(root / "Inbox" / "old.mp3"),))
    finding = _find(client.get("/api/reconciliation/reference-findings").json(), "repair_queue_item", "relative_path")
    assert finding["classification"] == "C"
    assert finding["recommended_disposition"] == "regenerate"
    assert finding["candidate_replacement"] == "Library/new.mp3"


def test_bpm_anomaly_stale_path_uses_same_track_id_evidence(reference_client):
    client, root, path, jobs = reference_client
    current = root / "Library" / "new.mp3"
    old = root / "Inbox" / "old.mp3"
    current.parent.mkdir()
    old.parent.mkdir()
    current.write_bytes(b"current")
    old.write_bytes(b"historical copy")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?, 'new.mp3')", (str(current),))
    with sqlite3.connect(jobs) as conn:
        conn.execute(
            "INSERT INTO bpm_anomalies (track_id, filepath, reason, detected_at) VALUES (1, ?, 'outlier', 'now')",
            (str(old),),
        )
    finding = _find(client.get("/api/reconciliation/reference-findings").json(), "bpm_anomaly", "filepath")
    assert finding["stale_value"] == "Inbox/old.mp3"
    assert finding["candidate_replacement"] == "Library/new.mp3"
    assert finding["evidence_source"] == "same_track_id_current_canonical_path"
    assert finding["confidence"] == "HIGH"
    assert finding["recommended_disposition"] == "regenerate"

    non_filepath = [f for f in client.get("/api/reconciliation/reference-findings").json()["findings"]
                    if f["artifact_type"] == "bpm_anomaly" and f["reference_field"] == "track_id"]
    assert non_filepath == []


def test_review_snapshot_path_uses_same_track_id_evidence(reference_client):
    client, root, path, _jobs = reference_client
    current = root / "Library" / "new.mp3"
    old = root / "Inbox" / "old.mp3"
    current.parent.mkdir()
    old.parent.mkdir()
    current.write_bytes(b"current")
    old.write_bytes(b"historical copy")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?, 'new.mp3')", (str(current),))
        conn.execute("CREATE TABLE enrichment_review_snapshots (id INTEGER PRIMARY KEY, items_json TEXT)")
        conn.execute(
            "INSERT INTO enrichment_review_snapshots VALUES (1, ?)",
            ('[{"track_id": 1, "relative_path": "Inbox/old.mp3"}]',),
        )
    finding = _find(
        client.get("/api/reconciliation/reference-findings").json(),
        "enrichment_review_snapshot",
        "relative_path",
    )
    assert finding["candidate_replacement"] == "Library/new.mp3"
    assert finding["confidence"] == "HIGH"
    assert finding["recommended_disposition"] == "regenerate"


@pytest.mark.parametrize("table,field,artifact", [
    ("enrichment_review_snapshots", "items_json", "enrichment_review_snapshot"),
    ("beets_review_snapshots", "items_json", "beets_review_snapshot"),
    ("duplicate_review_snapshots", "groups_json", "duplicate_review_snapshot"),
    ("quality_review_snapshots", "items_json", "quality_review_snapshot"),
    ("genre_review_snapshots", "items_json", "genre_review_snapshot"),
])
def test_review_snapshot_orphans_detected(reference_client, table, field, artifact):
    client, _root, path, _jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {field} TEXT)")
        conn.execute(f"INSERT INTO {table} VALUES (1, ?)", ('[{"track_id": 404}]',))
    finding = _find(client.get("/api/reconciliation/reference-findings").json(), artifact)
    assert finding["classification"] == "C"
    assert finding["recommended_disposition"] == "regenerate"


def test_review_decision_orphan_detected(reference_client):
    client, _root, path, _jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE enrichment_review_decisions (id INTEGER PRIMARY KEY, track_id INTEGER)")
        conn.execute("INSERT INTO enrichment_review_decisions VALUES (1, 404)")
    finding = _find(client.get("/api/reconciliation/reference-findings").json(), "enrichment_review_snapshot")
    assert finding["reference_field"] == "track_id"
    assert finding["classification"] == "C"
    assert finding["recommended_disposition"] == "regenerate"


def test_durable_finding_and_cache_are_non_actionable(reference_client):
    client, root, path, jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE quality_review_findings (id INTEGER PRIMARY KEY, track_id INTEGER)")
        conn.execute("INSERT INTO quality_review_findings VALUES (1, 88)")
        conn.execute("CREATE TABLE track_fingerprints (id INTEGER PRIMARY KEY, track_id INTEGER)")
        conn.execute("INSERT INTO track_fingerprints VALUES (1, 89)")
        conn.execute("CREATE TABLE metadata_lookup_cache (artist TEXT, title TEXT)")
        conn.execute("INSERT INTO metadata_lookup_cache VALUES ('a', 'b')")
    with sqlite3.connect(jobs) as conn:
        conn.execute("INSERT INTO waveform_jobs (id, library_id, track_id, status, created_at) VALUES ('j', ?, 90, 'failed', 'now')", (track_source_service.library_identity(root),))
        conn.execute("INSERT INTO waveform_jobs (id, library_id, track_id, status, created_at) VALUES ('other', 'other-library', 91, 'failed', 'now')")
    payload = client.get("/api/reconciliation/reference-findings").json()
    assert _find(payload, "quality_review_finding")["recommended_disposition"] == "mark_unresolvable"
    for item in payload["findings"]:
        if item["classification"] == "E":
            assert item["recommended_disposition"] == "ignore"
            assert item["blockers"]
    assert all("metadata_lookup_cache" not in item["artifact_identifier"] for item in payload["findings"])
    assert _find(payload, "waveform_job")["stale_value"] == 90
    assert all(item["stale_value"] != 91 for item in payload["findings"])


def test_queue_detection_fails_closed_and_does_not_scan_exports(reference_client):
    client, root, _path, _jobs = reference_client
    data = root / "data"
    data.mkdir()
    (data / "safe_queue.jsonl").write_text('{"filepath": "Inbox/missing.mp3"}\n', encoding="utf-8")
    (data / "bad_queue.jsonl").write_text("not-json\n", encoding="utf-8")
    (data / "unknown_queue.json").write_text('42', encoding="utf-8")
    export = root / "exports" / "old.m3u8"
    export.parent.mkdir()
    export.write_text(str(root / "Inbox" / "missing.mp3"), encoding="utf-8")
    payload = client.get("/api/reconciliation/reference-findings").json()
    assert _find(payload, "queue_entry")["classification"] == "B"
    assert any("Skipped malformed queue" in warning for warning in payload["warnings"])
    assert any("unsupported queue" in warning for warning in payload["warnings"])
    assert all(item["artifact_type"] != "export_file" for item in payload["findings"])


def test_already_current_references_have_no_false_positive(reference_client):
    client, root, path, _jobs = reference_client
    current = root / "Library" / "current.mp3"
    current.parent.mkdir()
    current.write_bytes(b"audio")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?, 'current.mp3')", (str(current),))
        conn.execute("CREATE TABLE cue_points (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL)")
        conn.execute("INSERT INTO cue_points VALUES (1, ?)", (str(current),))
        conn.execute("CREATE TABLE metadata_repair_queue (id INTEGER PRIMARY KEY, track_id INTEGER, relative_path TEXT)")
        conn.execute("INSERT INTO metadata_repair_queue VALUES (1, 1, ?)", (str(current),))
    payload = client.get("/api/reconciliation/reference-findings").json()
    assert payload["findings"] == []


def test_historical_provenance_is_ignore_and_endpoint_is_read_only(reference_client):
    client, root, path, jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE track_history (id INTEGER PRIMARY KEY, filepath TEXT, original_path TEXT)")
        conn.execute("INSERT INTO track_history VALUES (1, ?, ?)", (str(root / "Library" / "gone.mp3"), str(root / "Inbox" / "gone.mp3")))
    crates = root / "logs" / "manual_crates.db"
    with sqlite3.connect(crates) as conn:
        conn.execute("CREATE TABLE manual_crate_tracks (crate_id INTEGER, track_id INTEGER)")
    data = root / "data"
    data.mkdir()
    queue = data / "safe_queue.jsonl"
    queue.write_text('{"filepath": "Library/gone.mp3"}\n', encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    crates_before = hashlib.sha256(crates.read_bytes()).hexdigest()
    jobs_before = hashlib.sha256(jobs.read_bytes()).hexdigest()
    queue_before = hashlib.sha256(queue.read_bytes()).hexdigest()
    payload = client.get("/api/reconciliation/reference-findings").json()
    after = hashlib.sha256(path.read_bytes()).hexdigest()
    historical = _find(payload, "historical_reference")
    assert historical["classification"] == "A"
    assert historical["recommended_disposition"] == "ignore"
    assert historical["blockers"]
    assert before == after
    assert crates_before == hashlib.sha256(crates.read_bytes()).hexdigest()
    assert jobs_before == hashlib.sha256(jobs.read_bytes()).hexdigest()
    assert queue_before == hashlib.sha256(queue.read_bytes()).hexdigest()


def test_tag_write_blob_findings_include_the_blob_field_in_their_identity(reference_client):
    client, _root, _path, jobs = reference_client
    payload = '[{"filepath": "Inbox/missing.mp3"}]'
    with sqlite3.connect(jobs) as conn:
        conn.execute(
            "INSERT INTO tag_write_operations (id, status, track_count, plan_json, backup_manifest_json, result_json, created_at) "
            "VALUES ('op-1', 'completed', 1, ?, ?, ?, 'now')",
            (payload, payload, payload),
        )
    findings = [
        item for item in client.get("/api/reconciliation/reference-findings").json()["findings"]
        if item["artifact_type"] == "tag_write_operation"
    ]
    assert len(findings) == 3
    assert len({item["finding_id"] for item in findings}) == 3
    assert {item["artifact_identifier"].split(":")[1] for item in findings} == {
        "plan_json", "backup_manifest_json", "result_json"
    }


def test_uninitialized_root_skips_crate_and_jobs_references(reference_client):
    client, root, path, jobs = reference_client
    path.rename(root / "logs" / "uninitialized.db")
    crates = root / "logs" / "manual_crates.db"
    with sqlite3.connect(crates) as conn:
        conn.execute("CREATE TABLE manual_crate_tracks (crate_id INTEGER, track_id INTEGER)")
        conn.execute("INSERT INTO manual_crate_tracks VALUES (1, 404)")
    with sqlite3.connect(jobs) as conn:
        conn.execute("INSERT INTO waveform_jobs (id, library_id, track_id, status, created_at) VALUES ('j', 'library', 404, 'failed', 'now')")
    payload = client.get("/api/reconciliation/reference-findings").json()
    assert payload["findings"] == []
    assert any("processed.db is not initialized" in warning for warning in payload["warnings"])
    assert any("Skipped crate/jobs" in warning for warning in payload["warnings"])


def test_queue_utf8_and_symlinked_data_fail_closed(reference_client):
    client, root, _path, _jobs = reference_client
    data = root / "data"
    data.mkdir()
    (data / "invalid_queue.json").write_bytes(b"\xff")
    payload = client.get("/api/reconciliation/reference-findings").json()
    assert payload["findings"] == []
    assert any("Skipped unreadable queue file" in warning for warning in payload["warnings"])


def test_symlinked_queue_data_directory_fails_closed(reference_client):
    client, root, _path, _jobs = reference_client
    outside = root.parent / "outside-data"
    outside.mkdir()
    (outside / "unsafe_queue.json").write_text('{"filepath": "Inbox/missing.mp3"}', encoding="utf-8")
    (root / "data").symlink_to(outside, target_is_directory=True)
    payload = client.get("/api/reconciliation/reference-findings").json()
    assert payload["findings"] == []
    assert any("Skipped unsafe queue data directory" in warning for warning in payload["warnings"])


def test_missing_optional_path_values_are_skipped(reference_client):
    client, _root, path, _jobs = reference_client
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE cue_points (id INTEGER PRIMARY KEY, filepath TEXT)")
        conn.execute("INSERT INTO cue_points VALUES (1, NULL)")
        conn.execute("CREATE TABLE set_playlist_tracks (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO set_playlist_tracks VALUES (1)")
    assert client.get("/api/reconciliation/reference-findings").json()["findings"] == []
