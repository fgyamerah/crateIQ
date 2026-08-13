"""Stage 4A–D confirmed reference-artifact writer and rollback coverage."""
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


def test_stage1_to_stage4_real_workflow_reaches_track_id_writers(tmp_path, monkeypatch):
    client, root, db_path, _old, target = _setup(tmp_path, monkeypatch)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET id=2 WHERE id=1")
        conn.execute("CREATE TABLE field_provenance (id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL, field_name TEXT NOT NULL, value TEXT, origin TEXT, source TEXT, confidence TEXT, reason TEXT, evidence_json TEXT, is_current INTEGER NOT NULL, recorded_at TEXT, updated_at TEXT)")
        conn.execute("CREATE UNIQUE INDEX idx_field_provenance_current ON field_provenance(track_id, field_name) WHERE is_current=1")
        conn.execute("INSERT INTO field_provenance VALUES (1, 99, 'genre', 'House', 'provider', 'test', 'HIGH', 'keep', '{}', 1, 'then', 'now')")
        conn.execute("CREATE TABLE track_fingerprints (track_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL, duration_sec REAL, algorithm TEXT NOT NULL, computed_at TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO track_fingerprints VALUES (?, 'same-audio', 245.5, 'chromaprint', 'now')",
            [(99,), (2,)],
        )
    crates = root / "logs" / "manual_crates.db"
    with sqlite3.connect(crates) as conn:
        conn.execute("CREATE TABLE manual_crate_tracks (crate_id INTEGER NOT NULL, track_id INTEGER NOT NULL, position INTEGER NOT NULL, added_at TEXT NOT NULL, note TEXT, PRIMARY KEY (crate_id, track_id))")
        conn.execute("INSERT INTO manual_crate_tracks VALUES (7, 99, 4, 'then', 'keep-order')")

    findings = client.get("/api/reconciliation/reference-findings").json()["findings"]
    writable = [item for item in findings if item["artifact_type"] in {"field_provenance", "crate_track"}]
    assert len(writable) == 2
    assert {item["candidate_replacement"] for item in writable} == {2}
    assert {item["evidence_source"] for item in writable} == {"proven_replacement"}
    assert all(item["confidence"] == "HIGH" and item["blockers"] == [] for item in writable)

    proposal = client.post("/api/reconciliation/reference-plan/propose").json()
    actions = [item for item in proposal["planned_actions"] if item["artifact_type"] in {"field_provenance", "crate_track"}]
    assert len(actions) == 2
    assert all(item["action_type"] == "rehome_track_id" and item["executable"] for item in actions)
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
        assert conn.execute("SELECT track_id FROM field_provenance WHERE id=1").fetchone()[0] == 2
        assert conn.execute("SELECT bpm, key_musical FROM tracks WHERE id=2").fetchone() == (128.0, "Am")
    with sqlite3.connect(crates) as conn:
        assert conn.execute("SELECT track_id, position, note FROM manual_crate_tracks WHERE crate_id=7").fetchone() == (2, 4, "keep-order")


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


def test_filepath_rollback_migrates_pre_task28_ledger_before_appending(tmp_path, monkeypatch):
    client, root, db_path, old, target = _setup(tmp_path, monkeypatch)
    ledger_id = "stage4b-existing-apply"
    backup = root / "logs" / "reference_artifact_backups" / f"{ledger_id}_processed.db"
    backup.parent.mkdir()
    before = {"id": 1, "filepath": str(old), "cue_type": "drop", "time_sec": 64.0, "cue_data": "MIK-cues", "source": "mixed-in-key"}
    after = {**before, "filepath": str(target)}
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE reference_artifact_ledger (
                ledger_id TEXT PRIMARY KEY, parent_ledger_id TEXT, created_at TEXT NOT NULL,
                root TEXT NOT NULL, plan_path TEXT, plan_id TEXT, plan_sha256 TEXT,
                action_id TEXT, artifact_type TEXT NOT NULL, artifact_identifier TEXT NOT NULL,
                table_name TEXT NOT NULL, row_id INTEGER NOT NULL, reference_field TEXT NOT NULL,
                old_path TEXT NOT NULL, new_path TEXT NOT NULL, before_values_json TEXT NOT NULL,
                after_values_json TEXT NOT NULL, backup_path TEXT NOT NULL, backup_sha256 TEXT NOT NULL,
                status TEXT NOT NULL
            )"""
        )
        with sqlite3.connect(backup) as backup_conn:
            conn.backup(backup_conn)
        backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
        conn.execute("UPDATE cue_points SET filepath=? WHERE id=1", (str(target),))
        conn.execute(
            "INSERT INTO reference_artifact_ledger VALUES (?, NULL, 'then', ?, 'plan.json', 'stage4b', 'sha', 'action', 'cue_point', 'cue_points:1', 'cue_points', 1, 'filepath', ?, ?, ?, ?, ?, ?, 'applied')",
            (ledger_id, str(root), str(old), str(target), json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True), str(backup), backup_hash),
        )

    rolled_back = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert rolled_back.status_code == 200
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reference_artifact_ledger)")}
        assert {"backup_database", "backup_owner_ledger_id"} <= columns
        assert conn.execute("SELECT status, backup_path FROM reference_artifact_ledger WHERE ledger_id=?", (ledger_id,)).fetchone() == ("applied", str(backup))
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger").fetchone()[0] == 2
        assert conn.execute("SELECT filepath FROM cue_points WHERE id=1").fetchone()[0] == str(old)


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


def _rehome_action(root, *, artifact, old_id=99, new_id=2, crate_id=7):
    if artifact == "field_provenance":
        identifier, extra = "field_provenance:1", {"field_name": "genre"}
    else:
        identifier, extra = f"manual_crate_tracks:{crate_id}:{old_id}", {}
    action = {
        "classification": "B", "action_type": "rehome_track_id", "artifact_type": artifact,
        "artifact_identifier": identifier, "reference_field": "track_id", "finding_ids": ["rehome-finding"],
        "old_track_id": old_id, "new_track_id": new_id, "identity_evidence": "proven_replacement",
        "blockers": [], "confidence": "HIGH", "evidence": "proven_replacement", "executable": True,
        "reversibility": "rollback_capable", **extra,
    }
    action["action_id"] = reference_plan_service._action_id(action)
    return action


def _rehome_finding(action):
    return {
        "finding_id": "rehome-finding", "artifact_type": action["artifact_type"],
        "artifact_identifier": action["artifact_identifier"], "reference_field": "track_id",
        "stale_value": action["old_track_id"], "candidate_replacement": action["new_track_id"],
        "evidence_source": action["evidence"], "classification": "B", "confidence": "HIGH", "blockers": [],
    }


def _setup_rehome(tmp_path, monkeypatch, *, artifact):
    client, root, db_path, old, target = _setup(tmp_path, monkeypatch)
    second = root / "Library" / "replacement.mp3"
    second.write_bytes(b"replacement")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks VALUES (2, ?, 126.0, 'Cm')", (str(second),))
        if artifact == "field_provenance":
            conn.execute("CREATE TABLE field_provenance (id INTEGER PRIMARY KEY, track_id INTEGER NOT NULL, field_name TEXT NOT NULL, value TEXT, origin TEXT, source TEXT, confidence TEXT, reason TEXT, evidence_json TEXT, is_current INTEGER NOT NULL, recorded_at TEXT, updated_at TEXT)")
            conn.execute("CREATE UNIQUE INDEX idx_field_provenance_current ON field_provenance(track_id, field_name) WHERE is_current=1")
            conn.execute("INSERT INTO field_provenance VALUES (1, 99, 'genre', 'House', 'provider', 'test', 'HIGH', 'keep', '{}', 1, 'then', 'now')")
        else:
            crates = root / "logs" / "manual_crates.db"
            with sqlite3.connect(crates) as crate_conn:
                crate_conn.execute("CREATE TABLE manual_crate_tracks (crate_id INTEGER NOT NULL, track_id INTEGER NOT NULL, position INTEGER NOT NULL, added_at TEXT NOT NULL, note TEXT, PRIMARY KEY (crate_id, track_id))")
                crate_conn.execute("INSERT INTO manual_crate_tracks VALUES (7, 99, 4, 'then', 'do not lose')")
    action = _rehome_action(root, artifact=artifact)
    path, plan = _plan(root, action)
    monkeypatch.setattr(reference_apply_service.reference_findings_service, "get_reference_findings", lambda: {"findings": [_rehome_finding(action)]})
    preview = _preview(client, path, plan, action)
    assert preview["actions"][0]["eligible"] is True
    return client, root, db_path, path, plan, action, preview


def test_field_provenance_rehome_backup_rollback_and_collision(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="field_provenance")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        before = dict(conn.execute("SELECT * FROM field_provenance WHERE id=1").fetchone())
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert applied.status_code == 200
    result = applied.json()["results"][0]
    backup = root / "logs" / "reference_artifact_backups" / f"{result['ledger_id']}_processed.db"
    assert backup.is_file()
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT track_id FROM field_provenance WHERE id=1").fetchone()[0] == 99
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        after = dict(conn.execute("SELECT * FROM field_provenance WHERE id=1").fetchone())
        assert after["track_id"] == 2
        assert {key: value for key, value in after.items() if key != "track_id"} == {key: value for key, value in before.items() if key != "track_id"}
    assert client.post(f"/api/reconciliation/reference-ledger/{result['ledger_id']}/rollback", json={"confirm": True}).status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT track_id FROM field_provenance WHERE id=1").fetchone()[0] == 99

    client, _root, db_path, path, plan, action, _preview_data = _setup_rehome(tmp_path / "collision", monkeypatch, artifact="field_provenance")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO field_provenance VALUES (2, 2, 'genre', 'Other', 'user', 'test', NULL, NULL, NULL, 1, 'then', 'now')")
    response = client.post("/api/reconciliation/reference-apply/preview", json={"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]})
    assert "target_current_provenance_collision" in response.json()["actions"][0]["blockers"]


def test_field_provenance_rehome_rejects_live_drift(tmp_path, monkeypatch):
    client, _root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="field_provenance")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE field_provenance SET field_name='drifted' WHERE id=1")
    response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert response.json()["detail"]["code"] == "action_not_eligible"


def test_field_provenance_rollback_rejects_live_drift(tmp_path, monkeypatch):
    client, _root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="field_provenance")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE field_provenance SET reason='drifted' WHERE id=1")
    response = client.post(f"/api/reconciliation/reference-ledger/{applied['results'][0]['ledger_id']}/rollback", json={"confirm": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rollback_state_drift"


def test_field_provenance_rollback_rejects_reappeared_old_track_id(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="field_provenance")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks VALUES (99, ?, 120.0, 'Dm')", (str(root / "Library" / "reused.mp3"),))
    response = client.post(f"/api/reconciliation/reference-ledger/{applied['results'][0]['ledger_id']}/rollback", json={"confirm": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rollback_old_track_no_longer_orphaned"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT track_id FROM field_provenance WHERE id=1").fetchone()[0] == 2


def test_manual_crate_rehome_backup_rollback_and_collision(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert applied.status_code == 200
    result = applied.json()["results"][0]
    backup = root / "logs" / "reference_artifact_backups" / f"ref-prepare-{result['ledger_id'][4:]}_manual_crates.db"
    # The prepared ledger, not the completion row, owns the verified crate backup.
    with sqlite3.connect(db_path) as conn:
        owner = conn.execute("SELECT backup_owner_ledger_id, backup_database FROM reference_artifact_ledger WHERE ledger_id=?", (result["ledger_id"],)).fetchone()
    assert owner[1] == "manual_crates.db"
    backup = root / "logs" / "reference_artifact_backups" / f"{owner[0]}_manual_crates.db"
    assert backup.is_file()
    detail = client.get(f"/api/reconciliation/reference-ledger/{result['ledger_id']}").json()
    assert detail["backup_database"] == "manual_crates.db"
    assert detail["backup_owner_ledger_id"] == owner[0]
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        row = conn.execute("SELECT track_id, position, note FROM manual_crate_tracks WHERE crate_id=7").fetchone()
        assert row == (2, 4, "do not lose")
    assert client.post(f"/api/reconciliation/reference-ledger/{result['ledger_id']}/rollback", json={"confirm": True}).status_code == 200
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id, position, note FROM manual_crate_tracks WHERE crate_id=7").fetchone() == (99, 4, "do not lose")

    client, _root, db_path, path, plan, action, _preview_data = _setup_rehome(tmp_path / "collision", monkeypatch, artifact="crate_track")
    with sqlite3.connect(_root / "logs" / "manual_crates.db") as conn:
        conn.execute("INSERT INTO manual_crate_tracks VALUES (7, 2, 5, 'then', 'already there')")
    response = client.post("/api/reconciliation/reference-apply/preview", json={"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]})
    assert "target_crate_track_collision" in response.json()["actions"][0]["blockers"]


def test_manual_crate_rollback_rejects_reappeared_old_track_id(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks VALUES (99, ?, 120.0, 'Dm')", (str(root / "Library" / "reused.mp3"),))
    response = client.post(f"/api/reconciliation/reference-ledger/{applied['results'][0]['ledger_id']}/rollback", json={"confirm": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rollback_old_track_no_longer_orphaned"
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 2


def test_manual_crate_apply_does_not_initialize_missing_processed_db_during_recovery(tmp_path, monkeypatch):
    client, _root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    db_path.unlink()
    response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "library_not_initialized"
    assert not db_path.exists()


def test_manual_crate_apply_holds_processed_writer_lock_through_crate_update(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    real_connect = reference_apply_service.sqlite3.connect
    lock_observed = []

    class LockCheckingCrateConnection:
        def __init__(self, connection):
            object.__setattr__(self, "_connection", connection)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __setattr__(self, name, value):
            setattr(self._connection, name, value)

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def execute(self, statement, *args, **kwargs):
            if statement.startswith("UPDATE manual_crate_tracks SET track_id="):
                contender = real_connect(db_path, timeout=0)
                try:
                    contender.execute("DELETE FROM tracks WHERE id=2")
                except sqlite3.OperationalError:
                    lock_observed.append(True)
                else:
                    raise AssertionError("processed.db writer lock was released before the crate update")
                finally:
                    contender.close()
            return self._connection.execute(statement, *args, **kwargs)

    def check_only_crate_connection(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if str(database) == str(root / "logs" / "manual_crates.db"):
            return LockCheckingCrateConnection(connection)
        return connection

    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", check_only_crate_connection)
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert applied.status_code == 200
    assert lock_observed == [True]


def test_manual_crate_rehome_rejects_ambiguous_exact_identity(tmp_path, monkeypatch):
    client, root, _db_path, path, plan, action, _preview_data = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        conn.execute("ALTER TABLE manual_crate_tracks RENAME TO original_manual_crate_tracks")
        conn.execute("CREATE TABLE manual_crate_tracks (crate_id INTEGER NOT NULL, track_id INTEGER NOT NULL, position INTEGER NOT NULL, added_at TEXT NOT NULL, note TEXT)")
        conn.execute("INSERT INTO manual_crate_tracks SELECT * FROM original_manual_crate_tracks")
        conn.execute("INSERT INTO manual_crate_tracks VALUES (7, 99, 5, 'later', 'duplicate identity')")
    response = client.post("/api/reconciliation/reference-apply/preview", json={"plan_path": path.name, "plan_id": plan["plan_id"], "reviewed_action_ids": [action["action_id"]]})
    assert response.status_code == 200
    assert "crate_row_identity_ambiguous" in response.json()["actions"][0]["blockers"]


def test_manual_crate_rehome_rejects_live_drift_and_preserves_prepared_recovery_record(tmp_path, monkeypatch):
    client, root, _db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        conn.execute("UPDATE manual_crate_tracks SET track_id=98 WHERE crate_id=7 AND track_id=99")
    response = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert response.json()["detail"]["code"] == "action_not_eligible"


def test_manual_crate_rollback_rejects_live_drift(tmp_path, monkeypatch):
    client, root, _db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        conn.execute("UPDATE manual_crate_tracks SET note='drifted' WHERE crate_id=7 AND track_id=2")
    response = client.post(f"/api/reconciliation/reference-ledger/{applied['results'][0]['ledger_id']}/rollback", json={"confirm": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "rollback_state_drift"


def test_manual_crate_apply_finalizes_an_interrupted_prepared_ledger_on_retry(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    real_insert = reference_apply_service._insert_ledger

    def fail_completion(*args, **kwargs):
        if kwargs.get("status") == "applied" and kwargs.get("artifact") == "crate_track":
            raise RuntimeError("simulated ledger finalization loss")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(reference_apply_service, "_insert_ledger", fail_completion)
    failed = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert failed.json()["detail"]["code"] == "manual_crate_ledger_finalization_required"
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 2
    monkeypatch.setattr(reference_apply_service, "_insert_ledger", real_insert)
    real_completion = reference_apply_service._append_manual_completion
    recovery_lock_observed = []

    def check_apply_recovery_lock(*args, **kwargs):
        contender = sqlite3.connect(root / "logs" / "manual_crates.db", timeout=0)
        try:
            contender.execute("UPDATE manual_crate_tracks SET note='concurrent' WHERE crate_id=7 AND track_id=2")
        except sqlite3.OperationalError:
            recovery_lock_observed.append(True)
        else:
            raise AssertionError("manual-crate recovery finalized an unlocked state proof")
        finally:
            contender.close()
        return real_completion(*args, **kwargs)

    monkeypatch.setattr(reference_apply_service, "_append_manual_completion", check_apply_recovery_lock)
    recovered = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert recovered.status_code == 200
    assert recovered.json()["results"][0]["status"] == "applied_recovered"
    assert recovery_lock_observed == [True]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE artifact_type='crate_track' AND status='applied'").fetchone()[0] == 1


def test_manual_crate_recovery_blocks_regenerated_plan_and_preserves_exact_plan_result(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    real_insert = reference_apply_service._insert_ledger

    def fail_completion(*args, **kwargs):
        if kwargs.get("status") == "applied" and kwargs.get("artifact") == "crate_track":
            raise RuntimeError("simulated ledger finalization loss")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(reference_apply_service, "_insert_ledger", fail_completion)
    failed = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert failed.json()["detail"]["code"] == "manual_crate_ledger_finalization_required"
    monkeypatch.setattr(reference_apply_service, "_insert_ledger", real_insert)

    # Re-proposing the same physical crate-row transition gives it a new plan
    # artifact and SHA.  It must first close Plan A's prepare, then reject
    # Plan B rather than perform a second mutation or claim Plan A's apply.
    reproposed = {**plan, "planned_actions": [action, _rehome_action(root, artifact="crate_track", crate_id=8)]}
    reproposed["plan_id"] = reference_plan_service._plan_id(root, reproposed["planned_actions"])
    reproposed_path = path.with_name("reference_artifact_apply_crate_track_reproposed.json")
    reproposed_path.write_text(json.dumps(reproposed), encoding="utf-8")
    reproposed_preview = {"plan_sha256": hashlib.sha256(reproposed_path.read_bytes()).hexdigest()}
    wrong_plan = client.post(
        "/api/reconciliation/reference-apply",
        json=_apply_payload(reproposed_path, reproposed, action, reproposed_preview),
    )
    assert wrong_plan.status_code == 422
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE artifact_type='crate_track' AND status='applied'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE artifact_type='crate_track' AND status='manual_crate_apply_prepared'").fetchone()[0] == 1
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 2

    # Plan A can no longer find a prepared record to finalize merely because
    # Plan B observed its expected after-state.
    original_retry = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert original_retry.status_code == 422
    assert original_retry.json()["detail"]["code"] == "action_not_eligible"


def test_manual_crate_apply_closes_a_prepared_record_when_crate_commit_never_happened(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    real_connect = reference_apply_service.sqlite3.connect

    class FailingCrateConnection:
        def __init__(self, connection):
            object.__setattr__(self, "_connection", connection)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __setattr__(self, name, value):
            setattr(self._connection, name, value)

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def execute(self, statement, *args, **kwargs):
            if statement.startswith("UPDATE manual_crate_tracks SET track_id="):
                raise RuntimeError("simulated crate write failure")
            return self._connection.execute(statement, *args, **kwargs)

    def fail_only_crate_write(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if str(database) == str(root / "logs" / "manual_crates.db"):
            return FailingCrateConnection(connection)
        return connection

    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", fail_only_crate_write)
    failed = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "manual_crate_recovery_required"
    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", real_connect)
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 99
    recovered = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert recovered.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE status='manual_crate_apply_not_committed'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE artifact_type='crate_track' AND status='applied'").fetchone()[0] == 1


def test_manual_crate_regenerated_plan_closes_uncommitted_prepare_before_mutation(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    real_connect = reference_apply_service.sqlite3.connect

    class FailingCrateConnection:
        def __init__(self, connection):
            object.__setattr__(self, "_connection", connection)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __setattr__(self, name, value):
            setattr(self._connection, name, value)

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def execute(self, statement, *args, **kwargs):
            if statement.startswith("UPDATE manual_crate_tracks SET track_id="):
                raise RuntimeError("simulated crate write failure")
            return self._connection.execute(statement, *args, **kwargs)

    def fail_only_crate_write(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if str(database) == str(root / "logs" / "manual_crates.db"):
            return FailingCrateConnection(connection)
        return connection

    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", fail_only_crate_write)
    failed = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview))
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "manual_crate_recovery_required"
    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", real_connect)

    # A different plan artifact with a different SHA describes the same exact
    # crate-row transition.  It must close the failed prepare before making
    # its own durable prepare and physical mutation.
    reproposed = {**plan, "planned_actions": [action, _rehome_action(root, artifact="crate_track", crate_id=8)]}
    reproposed["plan_id"] = reference_plan_service._plan_id(root, reproposed["planned_actions"])
    reproposed_path = path.with_name("reference_artifact_apply_crate_track_reproposed_after_failure.json")
    reproposed_path.write_text(json.dumps(reproposed), encoding="utf-8")
    reproposed_preview = {"plan_sha256": hashlib.sha256(reproposed_path.read_bytes()).hexdigest()}
    applied = client.post(
        "/api/reconciliation/reference-apply",
        json=_apply_payload(reproposed_path, reproposed, action, reproposed_preview),
    )
    assert applied.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE status='manual_crate_apply_not_committed'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE artifact_type='crate_track' AND status='applied'").fetchone()[0] == 1
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 2


def test_manual_crate_rollback_finalizes_an_interrupted_prepared_ledger_on_retry(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    ledger_id = applied["results"][0]["ledger_id"]
    real_insert = reference_apply_service._insert_ledger

    def fail_rollback_completion(*args, **kwargs):
        if kwargs.get("status") == "rolled_back" and kwargs.get("artifact") == "crate_track":
            raise RuntimeError("simulated rollback ledger finalization loss")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(reference_apply_service, "_insert_ledger", fail_rollback_completion)
    failed = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert failed.json()["detail"]["code"] == "manual_crate_ledger_finalization_required"
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 99
    monkeypatch.setattr(reference_apply_service, "_insert_ledger", real_insert)
    recovery_lock_observed = []

    def check_rollback_recovery_lock(*args, **kwargs):
        if kwargs.get("status") == "rolled_back" and kwargs.get("artifact") == "crate_track":
            contender = sqlite3.connect(root / "logs" / "manual_crates.db", timeout=0)
            try:
                contender.execute("UPDATE manual_crate_tracks SET note='concurrent' WHERE crate_id=7 AND track_id=99")
            except sqlite3.OperationalError:
                recovery_lock_observed.append(True)
            else:
                raise AssertionError("manual-crate rollback recovery finalized an unlocked state proof")
            finally:
                contender.close()
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(reference_apply_service, "_insert_ledger", check_rollback_recovery_lock)
    recovered = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "rolled_back_recovered"
    assert recovery_lock_observed == [True]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE parent_ledger_id=? AND status='rolled_back'", (ledger_id,)).fetchone()[0] == 1


def test_manual_crate_rollback_closes_uncommitted_prepare_then_retries(tmp_path, monkeypatch):
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    ledger_id = applied["results"][0]["ledger_id"]
    real_connect = reference_apply_service.sqlite3.connect

    class FailingRollbackCrateConnection:
        def __init__(self, connection):
            object.__setattr__(self, "_connection", connection)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def __setattr__(self, name, value):
            setattr(self._connection, name, value)

        def __enter__(self):
            self._connection.__enter__()
            return self

        def __exit__(self, *args):
            return self._connection.__exit__(*args)

        def execute(self, statement, *args, **kwargs):
            if statement.startswith("UPDATE manual_crate_tracks SET track_id="):
                raise RuntimeError("simulated rollback crate write failure")
            return self._connection.execute(statement, *args, **kwargs)

    def fail_only_rollback_crate_write(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if str(database) == str(root / "logs" / "manual_crates.db"):
            return FailingRollbackCrateConnection(connection)
        return connection

    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", fail_only_rollback_crate_write)
    failed = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert failed.status_code == 422
    assert failed.json()["detail"]["code"] == "manual_crate_recovery_required"
    monkeypatch.setattr(reference_apply_service.sqlite3, "connect", real_connect)
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 2
    retried = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert retried.status_code == 200
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE status='manual_crate_rollback_not_committed'").fetchone()[0] == 1


def test_manual_crate_rollback_rejects_duplicate_after_normal_completion(tmp_path, monkeypatch):
    client, _root, _db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    ledger_id = applied["results"][0]["ledger_id"]
    assert client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True}).status_code == 200
    duplicate = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "rollback_already_completed"


def test_manual_crate_rollback_rejects_duplicate_after_recovered_completion(tmp_path, monkeypatch):
    """A rollback finalized via recovery must still be recognized as complete.

    The recovered completion record is parented to the original apply ledger
    (not to the prepared record), so a later rollback attempt must find it
    through that lineage rather than raising manual_crate_recovery_required.
    """
    client, root, db_path, path, plan, action, preview = _setup_rehome(tmp_path, monkeypatch, artifact="crate_track")
    applied = client.post("/api/reconciliation/reference-apply", json=_apply_payload(path, plan, action, preview)).json()
    ledger_id = applied["results"][0]["ledger_id"]
    real_insert = reference_apply_service._insert_ledger

    def fail_rollback_completion(*args, **kwargs):
        if kwargs.get("status") == "rolled_back" and kwargs.get("artifact") == "crate_track":
            raise RuntimeError("simulated rollback ledger finalization loss")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(reference_apply_service, "_insert_ledger", fail_rollback_completion)
    failed = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert failed.json()["detail"]["code"] == "manual_crate_ledger_finalization_required"
    monkeypatch.setattr(reference_apply_service, "_insert_ledger", real_insert)
    recovered = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "rolled_back_recovered"
    duplicate = client.post(f"/api/reconciliation/reference-ledger/{ledger_id}/rollback", json={"confirm": True})
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "rollback_already_completed"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_artifact_ledger WHERE parent_ledger_id=? AND status='rolled_back'", (ledger_id,)).fetchone()[0] == 1
    with sqlite3.connect(root / "logs" / "manual_crates.db") as conn:
        assert conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=7").fetchone()[0] == 99
