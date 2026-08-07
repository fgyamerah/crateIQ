"""Targeted tests for Cycle 4 Stage 3: plan-first library reconciliation.

Covers DETECT -> PROPOSE -> REVIEW -> VALIDATE PLAN, with an explicit stop
before any apply. No action here may ever mutate an audio file, a tag, or
processed.db; only the plan JSON artifact itself may be written.
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


def _library_db(root: Path, monkeypatch) -> Path:
    db_path = root / "logs" / "processed.db"
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    monkeypatch.setattr(pipeline.config, "DB_PATH", db_path)
    db.init_db()
    return db_path


def _insert_track(db_path: Path, filepath: Path, *, filesize_bytes: int | None = None) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tracks (filepath, filename, status, filesize_bytes) VALUES (?, ?, 'ok', ?)",
            (str(filepath), filepath.name, filesize_bytes),
        )
        conn.commit()
    finally:
        conn.close()


def _plan_dir(root: Path) -> Path:
    return root / "logs" / "path_reconcile"


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    db_path = _library_db(root, monkeypatch)
    with TestClient(backend_main.app) as test_client:
        yield test_client, root, db_path


def _make_auto_safe_rename_fixture(root: Path, db_path: Path) -> tuple[Path, Path]:
    """One missing indexed track + one identical-content untracked file with the
    same basename in a different directory: the existing planner scores this as
    an AUTO_SAFE_CANDIDATE (risk LOW) update_path_reference action."""
    old_path = root / "library" / "old" / "track.mp3"
    new_path = root / "library" / "house" / "track.mp3"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(b"identical-plan-fixture-bytes")
    _insert_track(db_path, old_path, filesize_bytes=new_path.stat().st_size)
    return old_path, new_path


def test_propose_plan_writes_artifact_and_returns_relative_paths_only(library):
    test_client, root, db_path = library
    old_path, new_path = _make_auto_safe_rename_fixture(root, db_path)

    response = test_client.post("/api/reconciliation/plans/propose")
    assert response.status_code == 200
    payload = response.json()
    assert payload["apply_supported"] is False
    assert payload["plan_artifact"].endswith("_path_reconcile_plan.json")

    actions = [a for a in payload["planned_actions"] if a["action"] == "update_path_reference"]
    assert len(actions) == 1
    action = actions[0]
    assert action["old_path"] == "library/old/track.mp3"
    assert action["new_path"] == "library/house/track.mp3"
    assert not str(root) in json.dumps(action)

    artifact_path = _plan_dir(root) / payload["plan_artifact"]
    assert artifact_path.is_file()
    on_disk_plan = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert on_disk_plan["apply_supported"] is False
    on_disk_action = next(a for a in on_disk_plan["planned_actions"] if a["action"] == "update_path_reference")
    assert on_disk_action["old_path"] == str(old_path)


def test_propose_plan_is_deterministic_aside_from_timestamp(library):
    test_client, root, db_path = library
    _make_auto_safe_rename_fixture(root, db_path)

    first = test_client.post("/api/reconciliation/plans/propose").json()
    second = test_client.post("/api/reconciliation/plans/propose").json()
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_propose_and_validate_never_mutate_audio_files_or_tracks_table(library):
    test_client, root, db_path = library
    old_path, new_path = _make_auto_safe_rename_fixture(root, db_path)
    with sqlite3.connect(db_path) as conn:
        before_rows = conn.execute("SELECT id, filepath, filename, status FROM tracks ORDER BY id").fetchall()
    before_bytes = new_path.read_bytes()

    test_client.post("/api/reconciliation/plans/propose")
    test_client.post("/api/reconciliation/validate-plan", json={"latest": True})

    with sqlite3.connect(db_path) as conn:
        after_rows = conn.execute("SELECT id, filepath, filename, status FROM tracks ORDER BY id").fetchall()
    assert after_rows == before_rows
    assert new_path.read_bytes() == before_bytes
    assert not old_path.exists()


def test_validate_plan_reports_valid_auto_safe_action(library):
    test_client, root, db_path = library
    _make_auto_safe_rename_fixture(root, db_path)
    test_client.post("/api/reconciliation/plans/propose")

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["valid_actions"] == 1
    assert payload["invalid_actions"] == 0


def test_validate_plan_flags_stale_finding_when_track_row_removed(library):
    test_client, root, db_path = library
    _make_auto_safe_rename_fixture(root, db_path)
    test_client.post("/api/reconciliation/plans/propose")

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM tracks")
        conn.commit()

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 1
    assert "old_path_not_in_canonical_db" in payload["reasons"]


def test_validate_plan_flags_disappeared_candidate(library):
    test_client, root, db_path = library
    _old_path, new_path = _make_auto_safe_rename_fixture(root, db_path)
    test_client.post("/api/reconciliation/plans/propose")

    new_path.unlink()

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 1
    assert "new_path_missing_on_disk" in payload["reasons"]


def test_validate_plan_rejects_path_outside_root(library, tmp_path):
    test_client, root, db_path = library
    outside = tmp_path / "outside_root" / "escaped.mp3"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"outside-bytes")
    inside = root / "library" / "house" / "inside.mp3"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(b"inside-bytes")
    _insert_track(db_path, outside)

    plan_dir = _plan_dir(root)
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "20260101_path_reconcile_plan.json").write_text(
        json.dumps({
            "root": str(root),
            "planned_actions": [
                {"action": "update_path_reference", "old_path": str(outside), "new_path": str(inside), "confidence": 0.9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE"},
            ],
        }),
        encoding="utf-8",
    )

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 1
    assert "old_path_outside_root" in payload["reasons"]


def test_validate_plan_rejects_symlink_escape(library, tmp_path):
    test_client, root, db_path = library
    secret = tmp_path / "outside_root_secret.mp3"
    secret.write_bytes(b"secret-bytes")
    old_path = root / "library" / "old" / "linked.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(b"linked-bytes")
    escape_link = root / "library" / "house" / "linked.mp3"
    escape_link.parent.mkdir(parents=True, exist_ok=True)
    escape_link.symlink_to(secret)
    _insert_track(db_path, old_path)

    plan_dir = _plan_dir(root)
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "20260101_path_reconcile_plan.json").write_text(
        json.dumps({
            "root": str(root),
            "planned_actions": [
                {"action": "update_path_reference", "old_path": str(old_path), "new_path": str(escape_link), "confidence": 0.9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE"},
            ],
        }),
        encoding="utf-8",
    )

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 1
    assert "new_path_outside_root" in payload["reasons"]


def test_validate_plan_detects_target_path_collision(library):
    test_client, root, db_path = library
    old_a = root / "library" / "old" / "a.mp3"
    old_b = root / "library" / "old" / "b.mp3"
    shared_new = root / "library" / "house" / "shared.mp3"
    shared_new.parent.mkdir(parents=True, exist_ok=True)
    shared_new.write_bytes(b"shared-target-bytes")
    old_a.parent.mkdir(parents=True, exist_ok=True)
    old_a.write_bytes(b"a")
    old_b.write_bytes(b"b")
    _insert_track(db_path, old_a)
    _insert_track(db_path, old_b)

    plan_dir = _plan_dir(root)
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "20260101_path_reconcile_plan.json").write_text(
        json.dumps({
            "root": str(root),
            "planned_actions": [
                {"action": "update_path_reference", "old_path": str(old_a), "new_path": str(shared_new), "confidence": 0.9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE"},
                {"action": "update_path_reference", "old_path": str(old_b), "new_path": str(shared_new), "confidence": 0.9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE"},
            ],
        }),
        encoding="utf-8",
    )

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 2
    assert payload["valid_actions"] == 0
    assert payload["reasons"]["target_path_collision"] == 2


def test_validate_plan_detects_ambiguous_candidate_for_old_path(library):
    test_client, root, db_path = library
    old_path = root / "library" / "old" / "ambiguous.mp3"
    candidate_one = root / "library" / "house" / "one.mp3"
    candidate_two = root / "library" / "techno" / "two.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(b"ambiguous")
    candidate_one.parent.mkdir(parents=True, exist_ok=True)
    candidate_one.write_bytes(b"one")
    candidate_two.parent.mkdir(parents=True, exist_ok=True)
    candidate_two.write_bytes(b"two")
    _insert_track(db_path, old_path)

    plan_dir = _plan_dir(root)
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "20260101_path_reconcile_plan.json").write_text(
        json.dumps({
            "root": str(root),
            "planned_actions": [
                {"action": "update_path_reference", "old_path": str(old_path), "new_path": str(candidate_one), "confidence": 0.9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE"},
                {"action": "update_path_reference", "old_path": str(old_path), "new_path": str(candidate_two), "confidence": 0.9, "risk": "LOW", "review_tier": "AUTO_SAFE_CANDIDATE"},
            ],
        }),
        encoding="utf-8",
    )

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 2
    assert payload["reasons"]["ambiguous_candidate_for_old_path"] == 2


def test_validate_plan_rejects_unsupported_action_type(library):
    test_client, root, _db_path = library
    plan_dir = _plan_dir(root)
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "20260101_path_reconcile_plan.json").write_text(
        json.dumps({"root": str(root), "planned_actions": [{"action": "delete_file", "old_path": "x"}]}),
        encoding="utf-8",
    )

    response = test_client.post("/api/reconciliation/validate-plan", json={"latest": True})
    payload = response.json()
    assert payload["invalid_actions"] == 1
    assert any(reason.startswith("unsupported_action_type") for reason in payload["reasons"])


def test_no_apply_endpoint_exists_for_reconciliation_plans(library):
    test_client, _root, _db_path = library
    for path in ("/api/reconciliation/plans/apply", "/api/reconciliation/apply", "/api/reconciliation/plans/propose/apply"):
        response = test_client.post(path)
        assert response.status_code == 404
