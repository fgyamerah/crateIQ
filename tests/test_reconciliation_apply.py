"""Safety contract for current-backend reviewed, DB-only reconciliation apply."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
import db
from backend.app.services import reconciliation_apply_service
from utils import path_reconciliation


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def library(tmp_path, monkeypatch):
    # SQLite URIs must preserve valid local-root characters such as '#'.
    # Keep this in the shared fixture so both preflight and locked apply /
    # backup paths exercise the escaped URI construction.
    root = tmp_path / "library#uri"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = root / "logs" / "processed.db"
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    db.init_db()

    with TestClient(backend_main.app) as client:
        yield client, root, db_path


def _track(db_path: Path, path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO tracks(filepath, filename, status) VALUES (?, ?, 'ok')", (str(path), path.name))


def _plan(root: Path, actions: list[dict], name: str = "reviewed_path_reconcile_plan.json") -> Path:
    path = root / "logs" / "path_reconcile" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"root": str(root), "planned_actions": actions}), encoding="utf-8")
    return path


def _update(old: Path, new: Path, **extra) -> dict:
    return {"action": "update_path_reference", "old_path": str(old), "new_path": str(new), "confidence": .9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE", **extra}


def _preview(client: TestClient, path: Path, action: dict) -> dict:
    action_id = path_reconciliation.path_reconcile_action_id(0, action)
    response = client.post("/api/reconciliation/apply/preview", json={"plan_path": str(path), "reviewed_action_ids": [action_id]})
    assert response.status_code == 200, response.text
    return response.json()


def _apply(client: TestClient, path: Path, action: dict, *, confirm: bool = True, plan_id: str | None = None):
    preview = _preview(client, path, action)
    return client.post("/api/reconciliation/apply", json={"plan_path": str(path), "plan_id": plan_id or preview["plan_id"], "reviewed_action_ids": [preview["actions"][0]["action_id"]], "confirm": confirm})


def test_no_confirm_does_not_mutate_and_preview_is_read_only(library):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"fixture-audio")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT filepath, filename FROM tracks").fetchall()
    response = _apply(client, plan, action, confirm=False)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "confirmation_required"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath, filename FROM tracks").fetchall() == before
    assert not (root / "logs" / "reconciliation_backups").exists()
    assert new.read_bytes() == b"fixture-audio"


@pytest.mark.parametrize("action", [
    {"action": "delete_file", "old_path": "x"},
    {"action": "update_path_reference", "old_path": "/outside/old.mp3", "new_path": "/outside/new.mp3", "review_tier": "AUTO_SAFE_CANDIDATE", "risk": "LOW"},
    {"action": "update_path_reference", "old_path": "x", "new_path": "y", "review_tier": "WEAK_MATCH", "risk": "LOW"},
])
def test_unsupported_cross_root_or_weak_actions_do_not_mutate(library, action):
    client, root, db_path = library
    plan = _plan(root, [action])
    before = _sha(db_path)
    action_id = path_reconciliation.path_reconcile_action_id(0, action)
    preview = client.post("/api/reconciliation/apply/preview", json={"plan_path": str(plan), "reviewed_action_ids": [action_id]})
    assert preview.status_code == 200
    assert preview.json()["actions"][0]["eligible"] is False
    apply = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview.json()["plan_id"], "reviewed_action_ids": [action_id], "confirm": True})
    assert apply.status_code == 422 and apply.json()["detail"]["code"] == "action_not_eligible"
    assert _sha(db_path) == before
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0


def test_stale_plan_root_mismatch_and_collision_block_apply(library, tmp_path):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"new")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    preview = _preview(client, plan, action)
    stale = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": "plan-stale", "reviewed_action_ids": [preview["actions"][0]["action_id"]], "confirm": True})
    assert stale.status_code == 422 and stale.json()["detail"]["code"] == "stale_plan_identity"
    other = tmp_path / "other"; other.mkdir()
    mismatched = other / "logs" / "path_reconcile" / "plan.json"; mismatched.parent.mkdir(parents=True)
    mismatched.write_text(json.dumps({"root": str(other), "planned_actions": []}), encoding="utf-8")
    mismatch = client.post("/api/reconciliation/apply/preview", json={"plan_path": str(mismatched), "reviewed_action_ids": ["x"]})
    assert mismatch.status_code == 422
    _track(db_path, new)
    collision = _preview(client, plan, action)
    assert "target_canonical_row_collision" in collision["actions"][0]["blockers"]


def test_exact_selected_action_applies_with_verified_backup_and_ledger(library):
    client, root, db_path = library
    old_a, new_a = root / "old-a.mp3", root / "new-a.mp3"
    old_b, new_b = root / "old-b.mp3", root / "new-b.mp3"
    new_a.write_bytes(b"a"); new_b.write_bytes(b"b")
    _track(db_path, old_a); _track(db_path, old_b)
    actions = [_update(old_a, new_a), _update(old_b, new_b)]
    plan = _plan(root, actions)
    action_id = path_reconciliation.path_reconcile_action_id(0, actions[0])
    preview = client.post("/api/reconciliation/apply/preview", json={"plan_path": str(plan), "reviewed_action_ids": [action_id]}).json()
    response = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview["plan_id"], "reviewed_action_ids": [action_id], "confirm": True})
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    backup = Path(result["backup_path"])
    assert backup.is_file() and _sha(backup) == result["backup_sha256"]
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT filepath, filename FROM tracks ORDER BY id").fetchall()
        ledger = conn.execute("SELECT before_values_json, after_values_json, status FROM reconciliation_ledger WHERE ledger_id=?", (result["ledger_id"],)).fetchone()
    assert rows == [(str(new_a), new_a.name), (str(old_b), old_b.name)]
    assert ledger[2] == "applied"
    assert json.loads(ledger[0])["rows"]["tracks"][0]["filepath"] == str(old_a)
    assert json.loads(ledger[1])["rows"]["tracks"][0]["filepath"] == str(new_a)
    assert json.loads(ledger[0])["verification_status"] == "verified"
    assert json.loads(ledger[1])["verification_status"] == "verified"
    assert new_a.read_bytes() == b"a" and new_b.read_bytes() == b"b"


def test_wal_backup_contains_pre_apply_state_and_rollback_restores_it(library):
    client, root, db_path = library
    old, new = root / "wal-old.mp3", root / "wal-new.mp3"
    new.write_bytes(b"wal-target")
    writer = sqlite3.connect(db_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        writer.execute("INSERT INTO tracks(filepath, filename, status) VALUES (?, ?, 'ok')", (str(old), old.name))
        writer.commit()
        assert (root / "logs" / "processed.db-wal").is_file()

        action, plan = _update(old, new), _plan(root, [_update(old, new)], "wal_path_reconcile_plan.json")
        response = _apply(client, plan, action)
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        with sqlite3.connect(result["backup_path"]) as backup:
            assert backup.execute("SELECT filepath, filename FROM tracks WHERE filepath=?", (str(old),)).fetchone() == (str(old), old.name)
        with sqlite3.connect(db_path) as current:
            assert current.execute("SELECT filepath FROM tracks").fetchone()[0] == str(new)

        rollback = client.post(f"/api/reconciliation/ledger/{result['ledger_id']}/rollback", json={"confirm": True})
        assert rollback.status_code == 200, rollback.text
        with sqlite3.connect(db_path) as restored:
            assert restored.execute("SELECT filepath, filename FROM tracks").fetchone() == (str(old), old.name)
    finally:
        writer.close()


def test_backup_creation_or_verification_failure_leaves_no_mutation_or_success_ledger(library, monkeypatch):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    preview = _preview(client, plan, action)
    real_sqlite_backup = reconciliation_apply_service._sqlite_backup

    monkeypatch.setattr(reconciliation_apply_service, "_sqlite_backup", lambda *_args: (_ for _ in ()).throw(sqlite3.OperationalError("injected")))
    creation = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview["plan_id"], "reviewed_action_ids": [preview["actions"][0]["action_id"]], "confirm": True})
    assert creation.status_code == 422 and creation.json()["detail"]["code"] == "backup_creation_failed"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(old)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0

    monkeypatch.setattr(reconciliation_apply_service, "_sqlite_backup", real_sqlite_backup)
    monkeypatch.setattr(reconciliation_apply_service, "_verify_backup", lambda *_args: (_ for _ in ()).throw(reconciliation_apply_service.ReconciliationApplyError("backup_verification_failed", "injected")))
    verification = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview["plan_id"], "reviewed_action_ids": [preview["actions"][0]["action_id"]], "confirm": True})
    assert verification.status_code == 422 and verification.json()["detail"]["code"] == "backup_verification_failed"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(old)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0


def test_target_removed_after_preview_is_rechecked_inside_apply_window(library, monkeypatch):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    preview = _preview(client, plan, action)
    real_backup = reconciliation_apply_service._backup_locked

    def backup_then_remove(*args, **kwargs):
        result = real_backup(*args, **kwargs)
        new.unlink()
        return result

    monkeypatch.setattr(reconciliation_apply_service, "_backup_locked", backup_then_remove)
    response = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview["plan_id"], "reviewed_action_ids": [preview["actions"][0]["action_id"]], "confirm": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "target_path_missing_at_apply"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(old)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0


def test_replaced_plan_and_multiple_action_scope_fail_closed(library):
    client, root, db_path = library
    old_a, new_a = root / "old-a.mp3", root / "new-a.mp3"
    old_b, new_b = root / "old-b.mp3", root / "new-b.mp3"
    new_a.write_bytes(b"a"); new_b.write_bytes(b"b")
    _track(db_path, old_a); _track(db_path, old_b)
    actions = [_update(old_a, new_a), _update(old_b, new_b)]
    plan = _plan(root, actions)
    preview = _preview(client, plan, actions[0])
    action_ids = [path_reconciliation.path_reconcile_action_id(index, action) for index, action in enumerate(actions)]
    plan.write_text(json.dumps({"root": str(root), "planned_actions": [actions[1], actions[0]]}), encoding="utf-8")
    stale = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview["plan_id"], "reviewed_action_ids": [action_ids[0]], "confirm": True})
    assert stale.status_code == 422 and stale.json()["detail"]["code"] == "stale_plan_identity"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks ORDER BY id").fetchall() == [(str(old_a),), (str(old_b),)]

    multi = client.post("/api/reconciliation/apply/preview", json={"plan_path": str(plan), "reviewed_action_ids": action_ids})
    assert multi.status_code == 422 and multi.json()["detail"]["code"] == "single_action_required"


def test_postcondition_failure_rolls_back_without_success_ledger(library):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TRIGGER sabotage_reconciliation AFTER UPDATE OF filepath ON tracks BEGIN UPDATE tracks SET filename='tampered' WHERE id=NEW.id; END")
    response = _apply(client, plan, action)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "tracks_postcondition_failed"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath, filename FROM tracks").fetchone() == (str(old), old.name)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0


def test_old_path_drift_and_injected_failure_rollback_transaction(library, monkeypatch):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"a"); _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    preview = _preview(client, plan, action)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET filepath=? WHERE filepath=?", (str(root / "drift.mp3"), str(old)))
    drift = client.post("/api/reconciliation/apply", json={"plan_path": str(plan), "plan_id": preview["plan_id"], "reviewed_action_ids": [preview["actions"][0]["action_id"]], "confirm": True})
    assert drift.status_code == 422
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tracks SET filepath=?, filename=?", (str(old), old.name))
    monkeypatch.setattr(reconciliation_apply_service, "_insert_ledger", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(RuntimeError):
        reconciliation_apply_service.apply(str(plan), _preview(client, plan, action)["plan_id"], [preview["actions"][0]["action_id"]], confirm=True)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(old)


def test_processed_state_stale_and_rollback_refuses_drift_then_restores(library):
    client, root, db_path = library
    old, replacement = root / "old.mp3", root / "replacement.mp3"
    replacement.write_bytes(b"replacement")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO processed_state(stage, filepath, file_size, file_mtime, status, processed_at, reason) VALUES (?, ?, 1, 0, 'success', 'x', '')", ("stage", str(old)))
        source_id = conn.execute("SELECT id FROM processed_state WHERE filepath=?", (str(old),)).fetchone()[0]
        conn.execute("INSERT INTO processed_state(stage, filepath, file_size, file_mtime, status, processed_at, reason) VALUES (?, ?, 1, 0, 'success', 'x', '')", ("stage", str(replacement)))
    action = {"action": "mark_stale_processed_state_path", "old_path": str(old), "replacement_path": str(replacement), "stage": "stage", "source_rows": [{"table": "processed_state", "id": source_id, "stage": "stage"}], "risk": "LOW", "report_only": True}
    plan = _plan(root, [action])
    assert _preview(client, plan, action)["actions"][0]["eligible"] is True
    response = _apply(client, plan, action)
    assert response.status_code == 200, response.text
    original_ledger = response.json()["results"][0]["ledger_id"]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE processed_state SET reason='drift' WHERE id=?", (source_id,))
    drift = client.post(f"/api/reconciliation/ledger/{original_ledger}/rollback", json={"confirm": True})
    assert drift.status_code == 422 and drift.json()["detail"]["code"] == "rollback_state_drift"
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE processed_state SET reason=? WHERE id=?", (f"superseded_by_existing_path:{replacement}", source_id))
    rollback = client.post(f"/api/reconciliation/ledger/{original_ledger}/rollback", json={"confirm": True})
    assert rollback.status_code == 200
    with sqlite3.connect(db_path) as conn:
        status = conn.execute("SELECT status, reason FROM processed_state WHERE id=?", (source_id,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0]
    assert status == ("success", "")
    assert count == 2

    repeated = client.post(f"/api/reconciliation/ledger/{original_ledger}/rollback", json={"confirm": True})
    assert repeated.status_code == 422 and repeated.json()["detail"]["code"] == "rollback_already_completed"
    with sqlite3.connect(db_path) as conn:
        original, rollback_entry = conn.execute("SELECT ledger_id, status FROM reconciliation_ledger ORDER BY created_at, rowid").fetchall()
    assert original == (original_ledger, "applied")
    assert rollback_entry[1] == "rolled_back"


def test_report_only_cannot_bypass_review_required_path_update(library):
    client, root, db_path = library
    old, new = root / "missing.mp3", root / "candidate.mp3"
    new.write_bytes(b"candidate")
    _track(db_path, old)
    action = _update(
        old,
        new,
        risk="REVIEW_REQUIRED",
        review_tier="REVIEW_CAREFULLY",
        report_only=True,
    )
    plan = _plan(root, [action])

    preview = _preview(client, plan, action)
    assert preview["actions"][0]["eligible"] is False
    assert "review_required_not_approved" in preview["actions"][0]["blockers"]
    response = client.post(
        "/api/reconciliation/apply",
        json={
            "plan_path": str(plan),
            "plan_id": preview["plan_id"],
            "reviewed_action_ids": [preview["actions"][0]["action_id"]],
            "confirm": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "action_not_eligible"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(old)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0


def test_symlinked_in_root_target_uses_canonical_ledger_paths_and_round_trips(library):
    client, root, db_path = library
    archive = root / "Archive"
    archive.mkdir()
    old = archive / "old.mp3"
    canonical_new = archive / "new.mp3"
    canonical_new.write_bytes(b"target")
    old_alias = root / "Old"
    old_alias.symlink_to(archive, target_is_directory=True)
    planned_new = old_alias / "new.mp3"
    _track(db_path, old)
    action, plan = _update(old, planned_new), _plan(root, [_update(old, planned_new)])

    preview = _preview(client, plan, action)
    assert preview["actions"][0]["eligible"] is True
    applied = _apply(client, plan, action)
    assert applied.status_code == 200, applied.text
    original_ledger_id = applied.json()["results"][0]["ledger_id"]
    with sqlite3.connect(db_path) as conn:
        filepath = conn.execute("SELECT filepath FROM tracks").fetchone()[0]
        old_path, new_path = conn.execute(
            "SELECT old_path, new_path FROM reconciliation_ledger WHERE ledger_id=?",
            (original_ledger_id,),
        ).fetchone()
    assert filepath == str(canonical_new)
    assert old_path == str(old)
    assert new_path == filepath

    rollback = client.post(f"/api/reconciliation/ledger/{original_ledger_id}/rollback", json={"confirm": True})
    assert rollback.status_code == 200, rollback.text
    with sqlite3.connect(db_path) as conn:
        filepath = conn.execute("SELECT filepath FROM tracks").fetchone()[0]
        original, rollback_entry = conn.execute(
            "SELECT ledger_id, operation_type FROM reconciliation_ledger ORDER BY rowid"
        ).fetchall()
    assert filepath == str(old)
    assert original == (original_ledger_id, "update_path_reference")
    assert rollback_entry[0] == rollback.json()["ledger_id"]
    assert rollback_entry[1] == "rollback:update_path_reference"


def test_rollback_writes_canonical_before_path_after_validating_alias_provenance(library):
    client, root, db_path = library
    archive = root / "Archive"
    archive.mkdir()
    old = archive / "old.mp3"
    new = archive / "new.mp3"
    new.write_bytes(b"target")
    alias = root / "Old"
    alias.symlink_to(archive, target_is_directory=True)
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    original_ledger_id = _apply(client, plan, action).json()["results"][0]["ledger_id"]

    # This simulates a legacy in-root alias spelling in the recorded before
    # row while preserving canonical ledger columns/provenance. Rollback must
    # restore the validated canonical value, not this raw alias spelling.
    with sqlite3.connect(db_path) as conn:
        raw_before = conn.execute(
            "SELECT before_values_json FROM reconciliation_ledger WHERE ledger_id=?",
            (original_ledger_id,),
        ).fetchone()[0]
        before = json.loads(raw_before)
        before["rows"]["tracks"][0]["filepath"] = str(alias / "old.mp3")
        conn.execute(
            "UPDATE reconciliation_ledger SET before_values_json=? WHERE ledger_id=?",
            (json.dumps(before), original_ledger_id),
        )

    rollback = client.post(f"/api/reconciliation/ledger/{original_ledger_id}/rollback", json={"confirm": True})
    assert rollback.status_code == 200, rollback.text
    with sqlite3.connect(db_path) as conn:
        filepath, filename = conn.execute("SELECT filepath, filename FROM tracks").fetchone()
        rollback_after = json.loads(conn.execute(
            "SELECT after_values_json FROM reconciliation_ledger WHERE ledger_id=?",
            (rollback.json()["ledger_id"],),
        ).fetchone()[0])
    assert filepath == str(old)
    assert filename == old.name
    assert rollback_after["rows"]["tracks"][0]["filepath"] == str(old)


def _insert_ledger(db_path: Path, *, ledger_id: str, root: Path, operation: str, old_path: str, new_path: str, before: object, after: object, status: str = "applied") -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO reconciliation_ledger (ledger_id, created_at, root, operation_type, old_path, new_path, affected_tables, before_values_json, after_values_json, status, error) VALUES (?, '2026-08-09T00:00:00Z', ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (ledger_id, str(root), operation, old_path, new_path, json.dumps(["tracks"]), json.dumps(before) if not isinstance(before, str) else before, json.dumps(after) if not isinstance(after, str) else after, status),
        )


def _rollback_contract_rows(old: Path, new: Path, track_id: int = 1) -> tuple[dict, dict]:
    base = {
        "apply_contract": reconciliation_apply_service._APPLY_CONTRACT,
        "verification_status": "verified",
        "plan_id": "plan-fixture",
        "action_id": "0-fixture",
        "backup_path": "/fixture/backup.db",
        "backup_sha256": "fixture-sha",
    }
    return (
        {**base, "rows": {"tracks": [{"id": track_id, "filepath": str(old), "filename": old.name}]}},
        {**base, "rows": {"tracks": [{"id": track_id, "filepath": str(new), "filename": new.name}]}},
    )


@pytest.mark.parametrize("operation", ["delete_audio", "unknown_future_operation"])
def test_rollback_rejects_unsupported_operation_without_mutation_or_success_ledger(library, operation):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    _track(db_path, new)
    before, after = _rollback_contract_rows(old, new)
    _insert_ledger(db_path, ledger_id="legacy-op", root=root, operation=operation, old_path=str(old), new_path=str(new), before=before, after=after)

    response = client.post("/api/reconciliation/ledger/legacy-op/rollback", json={"confirm": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "ledger_operation_unsupported"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(new)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 1


def test_rollback_rejects_malformed_and_legacy_tracks_shaped_ledger_rows(library):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    _track(db_path, new)
    _insert_ledger(db_path, ledger_id="malformed", root=root, operation="update_path_reference", old_path=str(old), new_path=str(new), before="{not-json", after="{}")
    _insert_ledger(
        db_path, ledger_id="legacy-shaped", root=root, operation="update_path_reference", old_path=str(old), new_path=str(new),
        before={"rows": {"tracks": [{"id": 1, "filepath": str(old), "filename": old.name}]}},
        after={"rows": {"tracks": [{"id": 1, "filepath": str(new), "filename": new.name}]}},
    )
    malformed = client.post("/api/reconciliation/ledger/malformed/rollback", json={"confirm": True})
    legacy = client.post("/api/reconciliation/ledger/legacy-shaped/rollback", json={"confirm": True})
    assert malformed.status_code == 422 and malformed.json()["detail"]["code"] == "ledger_state_unreadable"
    assert legacy.status_code == 422 and legacy.json()["detail"]["code"] == "ledger_rollback_provenance_missing"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(new)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 2


def test_rollback_rejects_recorded_before_path_outside_root_without_mutation(library, tmp_path):
    client, root, db_path = library
    outside = tmp_path / "outside.mp3"
    current = root / "current.mp3"
    _track(db_path, current)
    before, after = _rollback_contract_rows(outside, current)
    _insert_ledger(db_path, ledger_id="outside-before", root=root, operation="update_path_reference", old_path=str(outside), new_path=str(current), before=before, after=after)

    response = client.post("/api/reconciliation/ledger/outside-before/rollback", json={"confirm": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "rollback_path_outside_selected_root"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(current)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 1


def test_backup_directory_symlink_escape_rejects_apply_without_external_write(library, tmp_path):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    external = tmp_path / "external-backups"; external.mkdir()
    backups = root / "logs" / "reconciliation_backups"
    backups.symlink_to(external, target_is_directory=True)

    response = _apply(client, plan, action)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "backup_path_outside_selected_root"
    assert list(external.iterdir()) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(old)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 0


def test_backup_directory_symlink_escape_rejects_rollback_without_external_write(library, tmp_path):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    applied = _apply(client, plan, action).json()["results"][0]["ledger_id"]
    backups = root / "logs" / "reconciliation_backups"
    backups.rename(root / "logs" / "reconciliation_backups_saved")
    external = tmp_path / "external-backups"; external.mkdir()
    backups.symlink_to(external, target_is_directory=True)

    response = client.post(f"/api/reconciliation/ledger/{applied}/rollback", json={"confirm": True})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "backup_path_outside_selected_root"
    assert list(external.iterdir()) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT filepath FROM tracks").fetchone()[0] == str(new)
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_ledger").fetchone()[0] == 1


def test_preview_uses_one_immutable_plan_snapshot_for_actions_digest_and_validation(library, monkeypatch):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    original_read = Path.read_bytes
    reads = 0

    def counted_read(path: Path) -> bytes:
        nonlocal reads
        if path == plan:
            reads += 1
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read)
    preview = _preview(client, plan, action)
    assert preview["actions"][0]["action_id"] == path_reconciliation.path_reconcile_action_id(0, action)
    assert preview["plan_id"] == "plan-" + hashlib.sha256(original_read(plan)).hexdigest()
    assert reads == 1


def test_rollback_ledger_persists_verification_status_and_old_rows_remain_readable(library):
    client, root, db_path = library
    old, new = root / "old.mp3", root / "new.mp3"
    new.write_bytes(b"target")
    _track(db_path, old)
    action, plan = _update(old, new), _plan(root, [_update(old, new)])
    applied = _apply(client, plan, action).json()["results"][0]["ledger_id"]
    rollback = client.post(f"/api/reconciliation/ledger/{applied}/rollback", json={"confirm": True})
    assert rollback.status_code == 200
    with sqlite3.connect(db_path) as conn:
        before, after = conn.execute("SELECT before_values_json, after_values_json FROM reconciliation_ledger WHERE ledger_id=?", (rollback.json()["ledger_id"],)).fetchone()
    assert json.loads(before)["verification_status"] == "verified"
    assert json.loads(after)["verification_status"] == "verified"
    # Historical rows lacking this field remain visible, but are deliberately
    # not granted current DB-only rollback provenance.
    _insert_ledger(db_path, ledger_id="old-readable", root=root, operation="path-audit", old_path=str(old), new_path=str(new), before={"rows": {}}, after={"rows": {}}, status="ready")
    response = client.get("/api/reconciliation/ledger/old-readable")
    assert response.status_code == 200 and response.json()["ledger_id"] == "old-readable"
