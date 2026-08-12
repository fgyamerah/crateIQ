"""Stage 3 read-only reference-artifact preview coverage."""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.services import reference_apply_service, reference_plan_service, settings_service


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
    target = root / "Library" / "current.mp3"
    target.parent.mkdir()
    target.write_bytes(b"audio")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL)")
        conn.execute("CREATE TABLE cue_points (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, cue_data TEXT)")
        conn.execute("INSERT INTO tracks VALUES (1, ?)", (str(target),))
        conn.execute("INSERT INTO cue_points VALUES (1, ?, ?)", (str(root / "Inbox" / "old.mp3"), "MIK-cues"))
    return TestClient(backend_main.app), root, db_path, target


def _plan(root, action):
    action["action_id"] = reference_plan_service._action_id(action)
    payload = {"plan_kind": "reference_artifact_reconciliation_plan", "schema_version": 1,
               "generated_at": "2026-08-12T00:00:00+00:00", "root": str(root),
               "database": {"processed_db": str(root / "logs" / "processed.db")},
               "finding_summary": {}, "planned_actions": [action], "limitations": [],
               "message": "Plan only; no mutation has occurred."}
    payload["plan_id"] = reference_plan_service._plan_id(root, payload["planned_actions"])
    directory = root / "logs" / "path_reconcile"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reference_artifact_plan_preview.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def _action(root, target):
    return {"classification": "B", "action_type": "update_path_reference", "artifact_type": "cue_point",
            "artifact_identifier": "cue_points:1", "reference_field": "filepath", "finding_ids": ["finding-1"],
            "old_path": str(root / "Inbox" / "old.mp3"), "new_path": str(target), "blockers": [],
            "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True,
            "reversibility": "rollback_capable"}


def _finding(action):
    return {"finding_id": "finding-1", "artifact_type": action["artifact_type"],
            "artifact_identifier": action["artifact_identifier"], "reference_field": action["reference_field"],
            "stale_value": action["old_path"], "candidate_replacement": action["new_path"],
            "evidence_source": action["evidence"], "classification": "B", "confidence": "HIGH", "blockers": []}


def _set_findings(monkeypatch, action):
    monkeypatch.setattr(reference_apply_service.reference_findings_service, "get_reference_findings",
                        lambda: {"findings": [_finding(action)]})


def test_preview_revalidates_one_current_action_without_mutation(tmp_path, monkeypatch):
    client, root, db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _set_findings(monkeypatch, action)
    before_db, before_plan = db_path.read_bytes(), path.read_bytes()
    response = client.post("/api/reconciliation/reference-apply/preview", json={"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]})
    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["plan_sha256"]
    assert body["actions"][0]["eligible"] is True
    assert db_path.read_bytes() == before_db
    assert path.read_bytes() == before_plan


def test_preview_rejects_invalid_selection_and_plan_identity(tmp_path, monkeypatch):
    client, root, _db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _set_findings(monkeypatch, action)
    payload = {"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": []}
    assert client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["detail"]["code"] == "action_selection_required"
    payload["reviewed_action_ids"] = [action["action_id"], "other"]
    assert client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["detail"]["code"] == "single_action_required"
    payload["reviewed_action_ids"] = [action["action_id"], action["action_id"]]
    assert client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["detail"]["code"] == "duplicate_action_id"
    payload["reviewed_action_ids"] = ["missing"]
    missing = client.post("/api/reconciliation/reference-apply/preview", json=payload)
    assert missing.status_code == 200
    assert missing.json()["actions"][0]["blockers"] == ["action_id_not_in_plan"]
    payload.update(plan_id="wrong", reviewed_action_ids=[action["action_id"]])
    assert client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["detail"]["code"] == "plan_id_mismatch"


def test_preview_blocks_source_and_artifact_drift_and_missing_target(tmp_path, monkeypatch):
    client, root, db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _set_findings(monkeypatch, action)
    payload = {"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]}
    monkeypatch.setattr(reference_apply_service.reference_findings_service, "get_reference_findings", lambda: {"findings": []})
    assert "source_finding_no_longer_current" in client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["actions"][0]["blockers"]
    _set_findings(monkeypatch, action)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cue_points SET filepath=? WHERE id=1", (str(root / "Inbox" / "changed.mp3"),))
    assert "artifact_expected_before_mismatch" in client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["actions"][0]["blockers"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cue_points SET filepath=? WHERE id=1", (action["old_path"],))
    target.unlink()
    assert "target_path_missing_or_not_canonical" in client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["actions"][0]["blockers"]


def test_non_executable_queue_remains_fail_closed(tmp_path, monkeypatch):
    client, root, _db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    action.update(artifact_type="queue_entry", artifact_identifier="queue.json:1:filepath",
                  blockers=["queue_schema_not_authorized_for_mutation"], executable=False)
    path, plan = _plan(root, action)
    response = client.post("/api/reconciliation/reference-apply/preview", json={"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]})
    assert response.status_code == 200
    assert "queue_schema_not_authorized_for_mutation" in response.json()["actions"][0]["blockers"]


def test_preview_rejects_root_mismatch_and_tampered_action_identity(tmp_path, monkeypatch):
    client, root, _db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _set_findings(monkeypatch, action)
    payload = {"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]}
    contents = json.loads(path.read_text(encoding="utf-8"))
    contents["root"] = str(tmp_path / "other")
    path.write_text(json.dumps(contents), encoding="utf-8")
    assert client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["detail"]["code"] == "plan_root_mismatch"
    path, plan = _plan(root, action)
    contents = json.loads(path.read_text(encoding="utf-8"))
    contents["planned_actions"][0]["new_path"] = str(root / "Library" / "tampered.mp3")
    path.write_text(json.dumps(contents), encoding="utf-8")
    assert client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["detail"]["code"] == "plan_validation_failed"


def test_preview_blocks_source_candidate_drift_and_nonexecutable_category(tmp_path, monkeypatch):
    client, root, _db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    changed = _finding(action)
    changed["candidate_replacement"] = "Library/changed.mp3"
    monkeypatch.setattr(reference_apply_service.reference_findings_service, "get_reference_findings", lambda: {"findings": [changed]})
    payload = {"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]}
    assert "source_finding_drifted" in client.post("/api/reconciliation/reference-apply/preview", json=payload).json()["actions"][0]["blockers"]
    action.update(classification="C", action_type="regenerate_review_snapshot", executable=False,
                  artifact_type="quality_review_snapshot", blockers=[])
    for key in ("old_path", "new_path", "confidence", "evidence"):
        action.pop(key, None)
    action.update(reason="stale", stale_reference_count=1)
    path, plan = _plan(root, action)
    response = client.post("/api/reconciliation/reference-apply/preview", json={"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]})
    assert response.status_code == 200
    assert response.json()["actions"][0]["eligible"] is False


def test_preview_fails_closed_for_mismatched_artifact_identity(tmp_path, monkeypatch):
    client, root, _db_path, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    action["artifact_identifier"] = "set_playlist_tracks:1"
    path, plan = _plan(root, action)
    _set_findings(monkeypatch, action)
    response = client.post("/api/reconciliation/reference-apply/preview", json={
        "plan_path": path.name,
        "plan_id": plan["plan_id"],
        "reviewed_action_ids": [action["action_id"]],
    })
    assert response.status_code == 200
    assert response.json()["actions"][0]["blockers"] == ["artifact_identity_incomplete"]
