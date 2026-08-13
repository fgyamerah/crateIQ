"""Stage 2 plan-only reference-artifact reconciliation coverage."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.services import reference_plan_service, settings_service


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", tmp_path / "local" / "crateiq.env")
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL)")
    return TestClient(backend_main.app), root, db_path


def _write_plan(root: Path, actions: list[dict]) -> Path:
    actions = reference_plan_service._finalize(actions)
    plan = {
        "plan_kind": "reference_artifact_reconciliation_plan",
        "schema_version": 1,
        "plan_id": reference_plan_service._plan_id(root, actions),
        "generated_at": "2026-08-12T00:00:00+00:00",
        "root": str(root),
        "database": {"processed_db": str(root / "logs" / "processed.db")},
        "finding_summary": {}, "planned_actions": actions, "limitations": [],
        "message": "Plan only; no mutation has occurred.",
    }
    directory = root / "logs" / "path_reconcile"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "20260812_reference_artifact_plan_test.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_propose_creates_distinct_plan_artifact_only(tmp_path, monkeypatch):
    client, root, db_path = _setup(tmp_path, monkeypatch)
    before = db_path.read_bytes()
    response = client.post("/api/reconciliation/reference-plan/propose")
    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"].startswith("ref-plan-")
    assert body["plan_artifact"].endswith(".json")
    assert "reference_artifact_plan" in body["plan_artifact"]
    assert body["message"] == "Plan only; no mutation has occurred."
    assert db_path.read_bytes() == before
    assert (root / "logs" / "path_reconcile" / body["plan_artifact"]).is_file()
    validation = client.post("/api/reconciliation/reference-plan/validate", json={"plan_path": body["plan_artifact"]})
    assert validation.status_code == 200
    assert validation.json()["plan_id"] == body["plan_id"]


def test_missing_and_outside_targets_are_blocked(tmp_path, monkeypatch):
    _client, root, _db = _setup(tmp_path, monkeypatch)
    missing = _write_plan(root, [{"classification": "B", "action_type": "update_path_reference", "artifact_type": "cue_point", "artifact_identifier": "cue_points:1", "reference_field": "filepath", "old_path": "Inbox/old.mp3", "new_path": "Library/missing.mp3", "blockers": [], "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True, "reversibility": "rollback_capable"}])
    assert reference_plan_service.validate_plan(missing)["reasons"] == {"target_path_missing_or_not_canonical": 1}
    outside = _write_plan(root, [{"classification": "B", "action_type": "update_path_reference", "artifact_type": "cue_point", "artifact_identifier": "cue_points:2", "reference_field": "filepath", "old_path": "Inbox/old.mp3", "new_path": "/tmp/outside.mp3", "blockers": [], "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True, "reversibility": "rollback_capable"}])
    assert reference_plan_service.validate_plan(outside)["reasons"] == {"path_outside_selected_root": 1}


def test_ambiguous_targets_and_provenance_collision_fail_closed(tmp_path, monkeypatch):
    _client, root, db_path = _setup(tmp_path, monkeypatch)
    first = root / "Library" / "one.mp3"
    second = root / "Library" / "two.mp3"
    first.parent.mkdir()
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    with sqlite3.connect(db_path) as conn:
        conn.executemany("INSERT INTO tracks VALUES (?, ?)", [(1, str(first)), (2, str(second))])
        conn.execute("CREATE TABLE field_provenance (id INTEGER PRIMARY KEY, track_id INTEGER, field_name TEXT, is_current INTEGER)")
        conn.execute("INSERT INTO field_provenance VALUES (1, 2, 'genre', 1)")
    actions = [
        {"classification": "B", "action_type": "update_path_reference", "artifact_type": "cue_point", "artifact_identifier": "cue_points:1", "reference_field": "filepath", "old_path": "Inbox/old.mp3", "new_path": "Library/one.mp3", "blockers": [], "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True, "reversibility": "rollback_capable"},
        {"classification": "B", "action_type": "update_path_reference", "artifact_type": "cue_point", "artifact_identifier": "cue_points:1", "reference_field": "filepath", "old_path": "Inbox/old.mp3", "new_path": "Library/two.mp3", "blockers": [], "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True, "reversibility": "rollback_capable"},
        {"classification": "B", "action_type": "rehome_track_id", "artifact_type": "field_provenance", "artifact_identifier": "field_provenance:9", "reference_field": "track_id", "old_track_id": 99, "new_track_id": 2, "field_name": "genre", "identity_evidence": "proven_replacement", "blockers": [], "confidence": "HIGH", "evidence": "proven_replacement", "executable": True, "reversibility": "rollback_capable"},
    ]
    result = reference_plan_service.validate_plan(_write_plan(root, actions))
    assert result["reasons"]["ambiguous"] == 2
    assert result["reasons"]["target_current_provenance_collision"] == 1


def test_category_a_e_and_queue_stay_non_executable(tmp_path, monkeypatch):
    _client, root, _db = _setup(tmp_path, monkeypatch)
    path = _write_plan(root, [
        {"classification": "A", "action_type": "no_action_historical", "artifact_type": "historical_reference", "artifact_identifier": "history:1", "reference_field": "filepath", "finding_ids": ["a"], "blockers": ["immutable_historical_provenance"], "executable": False, "reversibility": "not_applicable"},
        {"classification": "E", "action_type": "no_action_cache", "artifact_type": "waveform_job", "artifact_identifier": "job:1", "reference_field": "track_id", "finding_ids": ["e"], "blockers": ["disposable_operational_state"], "executable": False, "reversibility": "not_applicable"},
        {"classification": "B", "action_type": "update_path_reference", "artifact_type": "queue_entry", "artifact_identifier": "queue:1", "reference_field": "filepath", "old_path": "Inbox/old.mp3", "new_path": "Library/new.mp3", "blockers": ["queue_schema_not_authorized_for_mutation"], "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": False, "reversibility": "rollback_capable"},
    ])
    result = reference_plan_service.validate_plan(path)
    assert result["non_executable_actions"] == 2
    assert result["reasons"]["queue_schema_not_authorized_for_mutation"] == 1


def test_category_d_export_action_stays_non_executable_on_validation(tmp_path, monkeypatch):
    _client, root, _db = _setup(tmp_path, monkeypatch)
    path = _write_plan(root, [
        {"classification": "D", "action_type": "regenerate_export", "artifact_type": "export_file",
         "artifact_identifier": "exports/crate.m3u8:3", "finding_ids": ["d"], "reference_field": "path",
         "reason": "stale_export_path", "blockers": ["export_regeneration_required"],
         "executable": False, "reversibility": "regenerable"},
    ])
    result = reference_plan_service.validate_plan(path)
    assert result["invalid_actions"] == 0
    assert result["non_executable_actions"] == 1


def test_validation_rejects_wrong_kind_category_patch_and_unsafe_artifact_path(tmp_path, monkeypatch):
    _client, root, _db = _setup(tmp_path, monkeypatch)
    wrong_kind = _write_plan(root, [])
    payload = json.loads(wrong_kind.read_text(encoding="utf-8"))
    payload["plan_kind"] = "path_reconcile_plan"
    wrong_kind.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="not a reference-artifact"):
        reference_plan_service.validate_plan(wrong_kind)

    category_patch = _write_plan(root, [{"classification": "C", "action_type": "update_path_reference", "artifact_type": "cue_point", "artifact_identifier": "cue_points:1", "reference_field": "filepath", "old_path": "Inbox/old.mp3", "new_path": "Library/new.mp3", "blockers": [], "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True, "reversibility": "rollback_capable"}])
    result = reference_plan_service.validate_plan(category_patch)
    assert result["reasons"] == {"action_type_not_allowed_for_classification": 1}

    outside = tmp_path / "outside-reference-plan.json"
    outside.write_text("{}", encoding="utf-8")
    escaped = root / "logs" / "path_reconcile" / "escaped_reference_artifact_plan.json"
    escaped.symlink_to(outside)
    with pytest.raises(ValueError, match="outside selected root"):
        reference_plan_service.validate_plan(escaped)


def test_proposal_consumes_findings_with_safe_category_semantics(tmp_path, monkeypatch):
    _client, root, db_path = _setup(tmp_path, monkeypatch)
    target = root / "Library" / "current.mp3"
    target.parent.mkdir()
    target.write_bytes(b"audio")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?)", (str(target),))
    before = db_path.read_bytes()
    findings = [
        {"finding_id": "b", "artifact_type": "cue_point", "artifact_owner": "db.py", "artifact_identifier": "cue_points:1", "reference_field": "filepath", "stale_value": "Inbox/old.mp3", "candidate_replacement": "Library/current.mp3", "evidence_source": "same_track_id_different_path", "classification": "B", "confidence": "HIGH", "blockers": [], "recommended_disposition": "correct", "generated_at": "now"},
        {"finding_id": "c", "artifact_type": "enrichment_review_snapshot", "artifact_owner": "review", "artifact_identifier": "snapshot:1", "reference_field": "relative_path", "stale_value": "Inbox/old.mp3", "candidate_replacement": "Library/current.mp3", "evidence_source": "same_track_id_current_canonical_path", "classification": "C", "confidence": "HIGH", "blockers": ["snapshot_scoped_reference"], "recommended_disposition": "regenerate", "generated_at": "now"},
        {"finding_id": "a", "artifact_type": "historical_reference", "artifact_owner": "db.py", "artifact_identifier": "history:1", "reference_field": "filepath", "stale_value": "Inbox/old.mp3", "candidate_replacement": None, "evidence_source": "check", "classification": "A", "confidence": None, "blockers": ["immutable_historical_provenance"], "recommended_disposition": "ignore", "generated_at": "now"},
        {"finding_id": "e", "artifact_type": "waveform_job", "artifact_owner": "jobs", "artifact_identifier": "job:1", "reference_field": "track_id", "stale_value": 99, "candidate_replacement": None, "evidence_source": "check", "classification": "E", "confidence": None, "blockers": ["disposable_operational_state"], "recommended_disposition": "ignore", "generated_at": "now"},
        {"finding_id": "d", "artifact_type": "export_file", "artifact_owner": "export_services", "artifact_identifier": "exports/crate.m3u8:3", "reference_field": "path", "stale_value": "Inbox/old.mp3", "candidate_replacement": None, "evidence_source": "bounded_export_path_check", "classification": "D", "confidence": None, "blockers": ["export_regeneration_required"], "recommended_disposition": "regenerate", "generated_at": "now"},
    ]
    monkeypatch.setattr(reference_plan_service.reference_findings_service, "get_reference_findings", lambda: {"summary": {}, "findings": findings})
    proposal = reference_plan_service.propose_plan()
    actions = proposal["planned_actions"]
    assert any(item["action_type"] == "update_path_reference" and item["executable"] for item in actions)
    assert any(item["action_type"] == "regenerate_review_snapshot" and not item["executable"] for item in actions)
    review_action = next(item for item in actions if item["action_type"] == "regenerate_review_snapshot")
    assert review_action["decision_preservation"] == "preserve_surviving_track_decisions"
    assert any(item["action_type"] == "regenerate_export" and not item["executable"] for item in actions)
    assert all(not item["executable"] for item in actions if item["action_type"] in {"no_action_historical", "no_action_cache"})
    assert db_path.read_bytes() == before


def test_category_c_notifications_preserve_snapshot_and_surviving_decisions(tmp_path, monkeypatch):
    client, root, db_path = _setup(tmp_path, monkeypatch)
    current = root / "Library" / "current.mp3"
    current.parent.mkdir()
    current.write_bytes(b"audio")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks VALUES (1, ?)", (str(current),))
        conn.execute("CREATE TABLE enrichment_review_snapshots (id INTEGER PRIMARY KEY, items_json TEXT)")
        conn.execute("CREATE TABLE enrichment_review_decisions (id INTEGER PRIMARY KEY, track_id INTEGER, decision TEXT)")
        conn.execute("CREATE TABLE quality_review_findings (id INTEGER PRIMARY KEY, track_id INTEGER)")
        conn.execute("INSERT INTO enrichment_review_snapshots VALUES (1, ?)", ('[{"track_id": 1, "path": "Inbox/old.mp3"}]',))
        conn.execute("INSERT INTO enrichment_review_decisions VALUES (1, 1, 'keep')")
        conn.execute("INSERT INTO quality_review_findings VALUES (1, 404)")
    before = db_path.read_bytes()
    proposal = client.post("/api/reconciliation/reference-plan/propose").json()
    actions = proposal["planned_actions"]
    assert any(action["action_type"] == "regenerate_review_snapshot" for action in actions)
    assert any(action["action_type"] == "mark_durable_finding_unresolvable" for action in actions)
    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT track_id, decision FROM enrichment_review_decisions").fetchall() == [(1, "keep")]
