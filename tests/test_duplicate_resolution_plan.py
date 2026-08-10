"""Plan-first duplicate resolution: propose, never apply.

Covers GET /api/duplicates/resolution-plan: derives a deterministic,
reproducible per-track plan from the latest saved Duplicate Review snapshot
and its existing human decisions. No action here may ever create/execute a
delete, move, rename, quarantine, or tag/file mutation; there is no
apply/execute endpoint for this contract.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
import db
import pipeline
from backend.app.services import duplicate_review_service

ALLOWED_ACTIONS = {"keep", "candidate_for_reversible_resolution", "no_action", "review_required"}


def _library_db(root: Path, monkeypatch) -> Path:
    db_path = root / "logs" / "processed.db"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    monkeypatch.setattr(pipeline.config, "DB_PATH", db_path)
    db.init_db()
    return db_path


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    db_path = _library_db(root, monkeypatch)
    with TestClient(backend_main.app) as test_client:
        yield test_client, root, db_path


def _make_group(
    group_id: str,
    item_specs: list[dict],
    *,
    match_basis: str = "content_checksum",
    checksum_prefix: str | None = "abc123def456",
    confidence: str = "high",
) -> dict:
    items = []
    for spec in item_specs:
        items.append({
            "track_id": spec["track_id"], "filename": spec.get("filename", f"track{spec['track_id']}.mp3"),
            "title": None, "artist": None, "relative_path": spec.get("relative_path"),
            "size_bytes": spec.get("size_bytes", 100), "genre": None, "bpm": None,
            "key_camelot": None, "key_musical": None, "duration_sec": None,
            "format": "mp3", "missing_metadata": [], "copy_marker": False,
        })
    return {
        "group_id": group_id, "reason": "rmlint duplicate", "confidence": confidence,
        "items": items, "match_basis": match_basis, "checksum_prefix": checksum_prefix,
        "recommendation": {"track_id": None, "reason_code": "insufficient_evidence", "evidence": []},
    }


def _seed_snapshot(db_path: Path, groups: list[dict], source: str = "rmlint_preview") -> int:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        duplicate_review_service._ensure_tables(conn)
        cursor = conn.execute(
            "INSERT INTO duplicate_review_snapshots (created_at, source, groups_json, warnings_json) VALUES (?, ?, ?, ?)",
            (duplicate_review_service._now(), source, json.dumps(groups), json.dumps([])),
        )
        conn.commit()
        return cursor.lastrowid


def _seed_decision(db_path: Path, snapshot_id: int, group_id: str, track_id: int, decision: str) -> None:
    with sqlite3.connect(db_path) as conn:
        duplicate_review_service._ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO duplicate_review_decisions (snapshot_id, group_id, track_id, decision, note, source, updated_at)
            VALUES (?, ?, ?, ?, '', 'test', ?)
            """,
            (snapshot_id, group_id, track_id, decision, duplicate_review_service._now()),
        )
        conn.commit()


def _write_file(root: Path, relative_path: str, content: bytes = b"fixture-bytes") -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_get_plan_with_no_snapshot_is_safe_and_read_only(library):
    test_client, _root, _db_path = library
    response = test_client.get("/api/duplicates/resolution-plan")
    assert response.status_code == 200
    payload = response.json()
    assert payload["groups"] == []
    assert payload["apply_supported"] is False
    assert payload["summary"]["groups"] == 0


def test_advisory_recommendation_alone_stays_blocked(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/keep.mp3")
    _write_file(root, "library/house/dup.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/keep.mp3"},
        {"track_id": 2, "relative_path": "library/house/dup.mp3"},
    ])
    group["recommendation"] = {"track_id": 1, "reason_code": "canonical_filename_no_copy_marker", "evidence": ["advisory only"]}
    _seed_snapshot(db_path, [group])

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "no_keeper_selected" in payload["groups"][0]["blockers"]
    assert all(item["action"] == "review_required" for item in payload["groups"][0]["items"])


def test_one_keeper_and_fully_reviewed_members_produces_ready_plan_no_execution(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/keep.mp3")
    _write_file(root, "library/house/dup.mp3")
    before_keep_bytes = (root / "library/house/keep.mp3").read_bytes()
    before_dup_bytes = (root / "library/house/dup.mp3").read_bytes()
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/keep.mp3"},
        {"track_id": 2, "relative_path": "library/house/dup.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    plan_group = payload["groups"][0]
    assert plan_group["status"] == "ready"
    assert plan_group["blockers"] == []
    assert plan_group["keeper_track_id"] == 1
    by_track = {item["track_id"]: item for item in plan_group["items"]}
    assert by_track[1]["action"] == "keep"
    assert by_track[1]["execution_requirements"] is None
    assert by_track[2]["action"] == "candidate_for_reversible_resolution"
    assert by_track[2]["execution_requirements"] is not None
    assert by_track[2]["execution_requirements"]["identity_evidence"]["type"] == "checksum_prefix"
    assert by_track[2]["execution_requirements"]["identity_evidence"]["value"] == "abc123def456"
    assert payload["apply_supported"] is False

    assert (root / "library/house/keep.mp3").read_bytes() == before_keep_bytes
    assert (root / "library/house/dup.mp3").read_bytes() == before_dup_bytes


def test_zero_keeper_is_blocked(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "ignore")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "no_keeper_selected" in payload["groups"][0]["blockers"]


def test_multiple_keepers_is_blocked(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "keep")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "multiple_keepers_selected" in payload["groups"][0]["blockers"]
    assert payload["groups"][0]["keeper_track_id"] is None


def test_unresolved_member_keeps_group_blocked(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    # track 2 left unresolved (no decision row)

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "unreviewed_members_present" in payload["groups"][0]["blockers"]

    review_later_group = _make_group("dup-2", [
        {"track_id": 3, "relative_path": "library/house/a.mp3"},
        {"track_id": 4, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id_2 = _seed_snapshot(db_path, [review_later_group])
    _seed_decision(db_path, snapshot_id_2, "dup-2", 3, "keep")
    _seed_decision(db_path, snapshot_id_2, "dup-2", 4, "review_later")

    payload_2 = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload_2["groups"][0]["status"] == "blocked"
    assert "unreviewed_members_present" in payload_2["groups"][0]["blockers"]


def test_stale_snapshot_drift_resets_decisions_and_stays_blocked(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    ready_payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert ready_payload["groups"][0]["status"] == "ready"

    # A newer snapshot (e.g. a fresh preview refresh) makes the old decisions
    # inapplicable; the plan must not silently carry them forward.
    _seed_snapshot(db_path, [group])
    drifted_payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert drifted_payload["groups"][0]["status"] == "blocked"
    assert "unreviewed_members_present" in drifted_payload["groups"][0]["blockers"]


def test_out_of_root_path_is_blocked(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "../outside.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "member_path_or_file_state_invalid" in payload["groups"][0]["blockers"]
    by_track = {item["track_id"]: item for item in payload["groups"][0]["items"]}
    assert "path_outside_managed_root" in by_track[2]["blockers"]


def test_missing_file_current_state_is_surfaced_not_silently_accepted(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    # track 2's file is never created on disk.
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/missing.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "member_path_or_file_state_invalid" in payload["groups"][0]["blockers"]
    by_track = {item["track_id"]: item for item in payload["groups"][0]["items"]}
    assert "missing_file_current_state" in by_track[2]["blockers"]


def test_filename_only_match_basis_never_authorizes_action(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group(
        "dup-1",
        [
            {"track_id": 1, "relative_path": "library/house/a.mp3"},
            {"track_id": 2, "relative_path": "library/house/b.mp3"},
        ],
        match_basis="filename_similarity",
        checksum_prefix=None,
        confidence="low",
    )
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    assert payload["groups"][0]["status"] == "blocked"
    assert "evidence_insufficient_for_resolution" in payload["groups"][0]["blockers"]
    assert all(item["action"] == "no_action" for item in payload["groups"][0]["items"])


def test_plan_action_ids_and_order_are_deterministic(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    first = test_client.get("/api/duplicates/resolution-plan").json()
    second = test_client.get("/api/duplicates/resolution-plan").json()
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    assert [item["action_id"] for item in first["groups"][0]["items"]] == ["dup-1:1", "dup-1:2"]


def test_no_plan_action_is_permanent_delete(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    payload = test_client.get("/api/duplicates/resolution-plan").json()
    actions = {item["action"] for group in payload["groups"] for item in group["items"]}
    assert actions <= ALLOWED_ACTIONS
    assert "delete" not in actions


def test_no_apply_or_execute_endpoint_exists(library):
    test_client, _root, _db_path = library
    for path in (
        "/api/duplicates/resolution-plan/apply",
        "/api/duplicates/resolution-plan/execute",
        "/api/duplicates/resolution/apply",
    ):
        assert test_client.post(path).status_code == 404
    assert test_client.post("/api/duplicates/resolution-plan").status_code in (404, 405)


def test_plan_generation_leaves_review_decisions_and_safety_text_unchanged(library):
    test_client, root, db_path = library
    _write_file(root, "library/house/a.mp3")
    _write_file(root, "library/house/b.mp3")
    group = _make_group("dup-1", [
        {"track_id": 1, "relative_path": "library/house/a.mp3"},
        {"track_id": 2, "relative_path": "library/house/b.mp3"},
    ])
    snapshot_id = _seed_snapshot(db_path, [group])
    _seed_decision(db_path, snapshot_id, "dup-1", 1, "keep")
    _seed_decision(db_path, snapshot_id, "dup-1", 2, "ignore")

    review_before = test_client.get("/api/duplicates/review").json()
    test_client.get("/api/duplicates/resolution-plan")
    review_after = test_client.get("/api/duplicates/review").json()
    assert review_before == review_after
    assert review_after["safety"] == ["db_only_review", "no_delete", "no_move", "no_rename", "no_quarantine", "no_tag_writes"]
    assert review_after["message"] == "Review decisions are stored only in CrateIQ's local index. No file action is available."
