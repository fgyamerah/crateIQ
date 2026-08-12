"""Stage 4A/B confirmed reference-artifact writer and rollback coverage."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import db as backend_db
from backend.app.services import reference_apply_service, reference_plan_service, settings_service


def _setup(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "LOCAL_ENV_PATH", tmp_path / "local" / "crateiq.env")
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir()
    target = root / "Library" / "current.mp3"
    target.parent.mkdir()
    target.write_bytes(b"unchanged-media-bytes")
    old = root / "Inbox" / "old.mp3"
    old.parent.mkdir()
    old.write_bytes(b"old-media-bytes")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, bpm REAL, key_musical TEXT)")
        conn.execute("INSERT INTO tracks VALUES (1, ?, 128.0, 'Am')", (str(target),))
        conn.execute("CREATE TABLE cue_points (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, cue_type TEXT NOT NULL, time_sec REAL, cue_data TEXT, source TEXT)")
        conn.execute("INSERT INTO cue_points VALUES (1, ?, 'drop', 64.0, 'MIK-cues', 'mixed-in-key')", (str(old),))
        conn.execute("CREATE TABLE set_playlist_tracks (id INTEGER PRIMARY KEY, set_id INTEGER, position INTEGER, filepath TEXT NOT NULL, phase TEXT, transition_note TEXT)")
        conn.execute("INSERT INTO set_playlist_tracks VALUES (1, 7, 3, ?, 'peak', 'keep-energy')", (str(old),))
    return TestClient(backend_main.app), root, db_path, old, target


def _action(root, target, artifact="cue_point"):
    table = "cue_points" if artifact == "cue_point" else "set_playlist_tracks"
    action = {
        "classification": "B", "action_type": "update_path_reference", "artifact_type": artifact,
        "artifact_identifier": f"{table}:1", "reference_field": "filepath", "finding_ids": ["finding-1"],
        "old_path": str(root / "Inbox" / "old.mp3"), "new_path": str(target), "blockers": [],
        "confidence": "HIGH", "evidence": "same_track_id_different_path", "executable": True,
        "reversibility": "rollback_capable",
    }
    action["action_id"] = reference_plan_service._action_id(action)
    return action


def _plan(root, action):
    plan = {
        "plan_kind": "reference_artifact_reconciliation_plan", "schema_version": 1,
        "generated_at": "2026-08-12T00:00:00+00:00", "root": str(root),
        "database": {"processed_db": str(root / "logs" / "processed.db")}, "finding_summary": {},
        "planned_actions": [action], "limitations": [], "message": "Plan only; no mutation has occurred.",
    }
    plan["plan_id"] = reference_plan_service._plan_id(root, plan["planned_actions"])
    directory = root / "logs" / "path_reconcile"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"reference_artifact_apply_{action['artifact_type']}.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path, plan


def _finding(action):
    return {
        "finding_id": "finding-1", "artifact_type": action["artifact_type"],
        "artifact_identifier": action["artifact_identifier"], "reference_field": "filepath",
        "stale_value": action["old_path"], "candidate_replacement": action["new_path"],
        "evidence_source": action["evidence"], "classification": "B", "confidence": "HIGH", "blockers": [],
    }


def _preview(client, path, plan, action):
    response = client.post("/api/reconciliation/reference-apply/preview", json={
        "plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]],
    })
    assert response.status_code == 200
    return response.json()


def _apply_payload(path, plan, action, preview, **overrides):
    payload = {
        "plan_path": path.name, "plan_id": plan["plan_id"], "plan_sha256": preview["plan_sha256"],
        "reviewed_action_ids": [action["action_id"]], "confirm": True,
    }
    payload.update(overrides)
    return payload


def _patch_findings(monkeypatch, action):
    monkeypatch.setattr(reference_apply_service.reference_findings_service, "get_reference_findings", lambda: {"findings": [_finding(action)]})


def test_apply_requires_confirmation_one_action_and_exact_preview_binding(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    payload = _apply_payload(path, plan, action, preview, confirm=False)
    assert client.post("/api/reconciliation/reference-apply", json=payload).json()["detail"]["code"] == "confirmation_required"
    payload = _apply_payload(path, plan, action, preview, reviewed_action_ids=[])
    assert client.post("/api/reconciliation/reference-apply", json=payload).json()["detail"]["code"] == "action_selection_required"
    payload = _apply_payload(path, plan, action, preview, reviewed_action_ids=[action["action_id"], "other"])
    assert client.post("/api/reconciliation/reference-apply", json=payload).json()["detail"]["code"] == "single_action_required"
    payload = _apply_payload(path, plan, action, preview, plan_id="wrong")
    assert client.post("/api/reconciliation/reference-apply", json=payload).json()["detail"]["code"] == "plan_id_mismatch"
    payload = _apply_payload(path, plan, action, preview, plan_sha256="0" * 64)
    assert client.post("/api/reconciliation/reference-apply", json=payload).json()["detail"]["code"] == "stale_plan_sha256"
    plan["message"] = "tampered after preview"
    path.write_text(json.dumps(plan), encoding="utf-8")
    payload = _apply_payload(path, plan, action, preview)
    assert client.post("/api/reconciliation/reference-apply", json=payload).json()["detail"]["code"] == "stale_plan_sha256"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM cue_points WHERE id=1").fetchone()[0] == str(root / "Inbox" / "old.mp3")


def test_apply_rechecks_stage3_and_blocks_target_disappearance_and_row_drift(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    target.unlink()
    response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert response.json()["detail"]["code"] == "action_not_eligible"
    target.write_bytes(b"restored")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cue_points SET filepath=? WHERE id=1", (str(root / "Inbox" / "drift.mp3"),))
    response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert response.json()["detail"]["code"] == "action_not_eligible"


def test_apply_rechecks_target_after_backup_before_reference_write(tmp_path, monkeypatch):
    client, root, db_path, old, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    real_backup = reference_apply_service._backup_locked

    def backup_then_remove(*args, **kwargs):
        result = real_backup(*args, **kwargs)
        target.unlink()
        return result

    monkeypatch.setattr(reference_apply_service, "_backup_locked", backup_then_remove)
    response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "target_path_missing_at_apply"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM cue_points WHERE id=1").fetchone()[0] == str(old)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reference_artifact_ledger'").fetchone() is None
    backup_dir = root / "logs" / "reference_artifact_backups"
    assert not backup_dir.exists() or list(backup_dir.iterdir()) == []


def test_stage1_to_stage4_real_workflow_reaches_both_path_writers(tmp_path, monkeypatch):
    client, root, db_path, old, target = _setup(tmp_path, monkeypatch)
    old.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE track_history (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, original_path TEXT)")
        conn.execute("INSERT INTO track_history VALUES (1, ?, ?)", (str(target), str(old)))

    findings = client.get("/api/reconciliation/reference-findings").json()["findings"]
    writable = [item for item in findings if item["artifact_type"] in {"cue_point", "playlist_track"}]
    assert len(writable) == 2
    assert {item["candidate_replacement"] for item in writable} == {"Library/current.mp3"}
    assert {item["evidence_source"] for item in writable} == {"historical_original_path_current_canonical_track"}

    proposal = client.post("/api/reconciliation/reference-plan/propose").json()
    actions = [item for item in proposal["planned_actions"] if item["artifact_type"] in {"cue_point", "playlist_track"}]
    assert len(actions) == 2
    assert all(item["executable"] for item in actions)
    for action in actions:
        preview = client.post("/api/reconciliation/reference-apply/preview", json={
            "plan_path": proposal["plan_artifact"], "plan_id": proposal["plan_id"],
            "reviewed_action_ids": [action["action_id"]],
        }).json()
        assert preview["actions"][0]["eligible"] is True
        response = client.post("/api/reconciliation/reference-apply", json={
            "plan_path": proposal["plan_artifact"], "plan_id": proposal["plan_id"],
            "plan_sha256": preview["plan_sha256"], "reviewed_action_ids": [action["action_id"]], "confirm": True,
        })
        assert response.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM cue_points WHERE id=1").fetchone()[0] == str(target)
        assert conn.execute("SELECT filepath FROM set_playlist_tracks WHERE id=1").fetchone()[0] == str(target)


def test_stage1_deduplicates_repeated_history_for_one_canonical_target(tmp_path, monkeypatch):
    client, root, db_path, old, target = _setup(tmp_path, monkeypatch)
    old.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE track_history (id INTEGER PRIMARY KEY, filepath TEXT NOT NULL, original_path TEXT)")
        conn.executemany(
            "INSERT INTO track_history VALUES (?, ?, ?)",
            [(1, str(target), str(old)), (2, str(target), str(old))],
        )

    findings = client.get("/api/reconciliation/reference-findings").json()["findings"]
    writable = [item for item in findings if item["artifact_type"] in {"cue_point", "playlist_track"}]
    assert len(writable) == 2
    assert all(item["candidate_replacement"] == "Library/current.mp3" for item in writable)
    assert all(item["blockers"] == [] for item in writable)


def test_backup_destination_must_remain_under_selected_root(tmp_path, monkeypatch):
    _client, root, _db_path, _old, _target = _setup(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "logs" / "reference_artifact_backups").symlink_to(outside, target_is_directory=True)
    try:
        reference_apply_service._safe_backup_path(root, "ref-test")
    except reference_apply_service.ReferenceApplyPreviewError as exc:
        assert exc.code == "backup_path_outside_selected_root"
    else:
        raise AssertionError("unsafe backup directory was accepted")


def test_apply_preserves_full_rows_mik_tracks_and_media_for_both_surfaces(tmp_path, monkeypatch):
    client, root, db_path, old, target = _setup(tmp_path, monkeypatch)
    media_before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (old, target)}
    for artifact, table in (("cue_point", "cue_points"), ("playlist_track", "set_playlist_tracks")):
        action = _action(root, target, artifact)
        path, plan = _plan(root, action)
        _patch_findings(monkeypatch, action)
        preview = _preview(client, path, plan, action)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            before = dict(conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone())
        response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
        assert response.status_code == 200
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            after = dict(conn.execute(f"SELECT * FROM {table} WHERE id=1").fetchone())
            assert {key: value for key, value in after.items() if key != "filepath"} == {key: value for key, value in before.items() if key != "filepath"}
            assert after["filepath"] == str(target)
            assert tuple(conn.execute("SELECT bpm, key_musical FROM tracks WHERE id=1").fetchone()) == (128.0, "Am")
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (old, target)} == media_before


def test_apply_backup_ledger_and_verified_rollback(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target, "playlist_track")
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    result = applied["results"][0]
    backup = root / "logs" / "reference_artifact_backups" / f"{result['ledger_id']}_processed.db"
    assert backup.is_file()
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == result["backup_sha256"]
    with sqlite3.connect(backup) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT filepath FROM set_playlist_tracks WHERE id=1").fetchone()[0] == str(root / "Inbox" / "old.mp3")
    listed = client.get("/api/reconciliation/reference-ledger")
    assert listed.status_code == 200
    assert listed.json()[0]["plan_sha256"] == preview["plan_sha256"]
    detail = client.get(f"/api/reconciliation/reference-ledger/{result['ledger_id']}")
    assert detail.status_code == 200
    assert detail.json()["before_values_json"] != detail.json()["after_values_json"]
    assert client.post(f"/api/reconciliation/reference-ledger/{result['ledger_id']}/rollback", json={"confirm": False}).json()["detail"]["code"] == "confirmation_required"
    rolled_back = client.post(f"/api/reconciliation/reference-ledger/{result['ledger_id']}/rollback", json={"confirm": True})
    assert rolled_back.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM set_playlist_tracks WHERE id=1").fetchone()[0] == str(root / "Inbox" / "old.mp3")
        child = conn.execute("SELECT parent_ledger_id, status FROM reference_artifact_ledger WHERE ledger_id=?", (rolled_back.json()["ledger_id"],)).fetchone()
        assert child == (result["ledger_id"], "rolled_back")
    duplicate = client.post(f"/api/reconciliation/reference-ledger/{result['ledger_id']}/rollback", json={"confirm": True})
    assert duplicate.json()["detail"]["code"] == "rollback_already_completed"


def test_rollback_restores_root_relative_prestate(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    relative_old = "Inbox/old.mp3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cue_points SET filepath=? WHERE id=1", (relative_old,))
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert applied.status_code == 200
    ledger_id = applied.json()["results"][0]["ledger_id"]
    rolled_back = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert rolled_back.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM cue_points WHERE id=1").fetchone()[0] == relative_old


def test_rollback_rejects_tampered_backup_and_live_drift(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    ledger_id = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()["results"][0]["ledger_id"]
    backup = root / "logs" / "reference_artifact_backups" / f"{ledger_id}_processed.db"
    backup.write_bytes(b"tampered")
    response = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert response.json()["detail"]["code"] == "rollback_backup_hash_mismatch"
    # A fresh apply proves drift is also a separate, fail-closed rollback gate.
    client, root, db_path, _old, target = _setup(tmp_path / "drift", monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    ledger_id = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()["results"][0]["ledger_id"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE cue_points SET cue_data='drifted' WHERE id=1")
    response = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert response.json()["detail"]["code"] == "rollback_state_drift"


def test_rollback_rejects_missing_backup_without_creating_new_state(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    ledger_id = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()["results"][0]["ledger_id"]
    backup = root / "logs" / "reference_artifact_backups" / f"{ledger_id}_processed.db"
    backup.unlink()
    response = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rollback_backup_missing"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger").fetchone()[0] == 1


def test_apply_rejects_cue_target_collision(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO cue_points VALUES (2, ?, 'drop', 8.0, 'other', 'manual')", (str(target),))
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    assert preview["actions"][0]["eligible"] is False
    assert "artifact_target_collision" in preview["actions"][0]["blockers"]


def test_cue_collisions_compare_canonical_paths_and_block_rollback(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    # A legacy root-relative row must collide with the canonical target, too.
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO cue_points VALUES (2, ?, 'drop', 8.0, 'other', 'manual')", ("Library/current.mp3",))
    action = _action(root, target)
    path, plan = _plan(root, action)
    _patch_findings(monkeypatch, action)
    preview = _preview(client, path, plan, action)
    assert preview["actions"][0]["eligible"] is False
    assert "artifact_target_collision" in preview["actions"][0]["blockers"]

    # Remove that collision, apply, then add a collision at the historical
    # target. Rollback must fail before it creates an unledgered backup.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM cue_points WHERE id=2")
    preview = _preview(client, path, plan, action)
    ledger_id = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()["results"][0]["ledger_id"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO cue_points VALUES (2, ?, 'drop', 8.0, 'other', 'manual')", ("Inbox/old.mp3",))
    response = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rollback_target_collision"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger").fetchone()[0] == 1
    assert len(list((root / "logs" / "reference_artifact_backups").iterdir())) == 1
