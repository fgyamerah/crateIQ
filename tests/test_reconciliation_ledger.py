import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
import db
import pipeline


def _ledger_db(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "logs" / "processed.db"
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    monkeypatch.setattr(pipeline.config, "DB_PATH", db_path)
    db.init_db()
    return db_path


def _insert_ledger(db_path: Path, **row) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO reconciliation_ledger (
                ledger_id, created_at, root, operation_type, old_path, new_path,
                affected_tables, before_values_json, after_values_json, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["ledger_id"],
                row.get("created_at"),
                row.get("root"),
                row.get("operation_type"),
                row.get("old_path"),
                row.get("new_path"),
                row.get("affected_tables"),
                row.get("before_values_json"),
                row.get("after_values_json"),
                row.get("status"),
                row.get("error"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _write_track_db(
    db_path: Path,
    *,
    tracks: list[Path],
    processed: list[Path] | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for track in tracks:
            conn.execute(
                "INSERT INTO tracks(filepath, filename, status) VALUES (?, ?, ?)",
                (str(track), track.name, "ok"),
            )
        if processed:
            for path in processed:
                conn.execute(
                    "INSERT INTO processed_state(stage, filepath, file_size, file_mtime, status, processed_at, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("library-organize", str(path), 1, 0, "success", "2026-05-05T00:00:00Z", ""),
                )
        conn.commit()
    finally:
        conn.close()


def _write_plan(root: Path, name: str, plan: dict) -> Path:
    plan_dir = root / "logs" / "path_reconcile"
    plan_dir.mkdir(parents=True, exist_ok=True)
    path = plan_dir / name
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path


def test_schema_creation_is_idempotent(tmp_path, monkeypatch):
    db_path = _ledger_db(tmp_path, monkeypatch)

    # A second init should be harmless and keep the schema intact.
    db.init_db()

    conn = sqlite3.connect(db_path)
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reconciliation_ledger'"
        ).fetchone()
        assert table is not None

        columns = [row[1] for row in conn.execute("PRAGMA table_info(reconciliation_ledger)").fetchall()]
        assert columns == [
            "ledger_id",
            "created_at",
            "root",
            "operation_type",
            "old_path",
            "new_path",
            "affected_tables",
            "before_values_json",
            "after_values_json",
            "status",
            "error",
        ]

        indexes = {row[1] for row in conn.execute("PRAGMA index_list('reconciliation_ledger')").fetchall()}
        assert {
            "idx_reconciliation_ledger_ledger_id",
            "idx_reconciliation_ledger_created_at",
            "idx_reconciliation_ledger_operation_type",
            "idx_reconciliation_ledger_status",
        }.issubset(indexes)
    finally:
        conn.close()


def test_read_only_helpers_return_recent_rows(tmp_path, monkeypatch):
    db_path = _ledger_db(tmp_path, monkeypatch)
    _insert_ledger(
        db_path,
        ledger_id="ledger-a",
        created_at="2026-05-05T10:00:00Z",
        root=str(tmp_path),
        operation_type="path-audit",
        old_path=str(tmp_path / "old-a.mp3"),
        new_path=str(tmp_path / "new-a.mp3"),
        affected_tables=json.dumps(["tracks", "processed_state"]),
        before_values_json=json.dumps({"old_path": "old-a.mp3"}),
        after_values_json=json.dumps({"new_path": "new-a.mp3"}),
        status="ready",
        error=None,
    )
    _insert_ledger(
        db_path,
        ledger_id="ledger-b",
        created_at="2026-05-05T11:00:00Z",
        root=str(tmp_path),
        operation_type="path-audit",
        old_path=str(tmp_path / "old-b.mp3"),
        new_path=str(tmp_path / "new-b.mp3"),
        affected_tables=json.dumps(["tracks"]),
        before_values_json=json.dumps({"old_path": "old-b.mp3"}),
        after_values_json=json.dumps({"new_path": "new-b.mp3"}),
        status="ready",
        error=None,
    )

    rows = db.list_reconciliation_ledger()
    assert [row["ledger_id"] for row in rows] == ["ledger-b", "ledger-a"]
    assert db.get_reconciliation_ledger("ledger-a")["ledger_id"] == "ledger-a"
    assert db.get_reconciliation_ledger("missing") is None


def test_path_reconcile_ledger_listing_is_read_only(tmp_path, monkeypatch, capsys):
    db_path = _ledger_db(tmp_path, monkeypatch)
    _insert_ledger(
        db_path,
        ledger_id="ledger-read-only",
        created_at="2026-05-05T11:00:00Z",
        root=str(tmp_path),
        operation_type="path-audit",
        old_path=str(tmp_path / "old.mp3"),
        new_path=str(tmp_path / "new.mp3"),
        affected_tables=json.dumps(["tracks"]),
        before_values_json=json.dumps({"old_path": "old.mp3"}),
        after_values_json=json.dumps({"new_path": "new.mp3"}),
        status="ready",
        error=None,
    )

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(
            root=None,
            ledger=True,
            verify_ledger=None,
            dry_run=False,
            apply=False,
            apply_auto_safe_only=False,
            mark_stale_pstate=False,
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "path-reconcile LEDGER" in out
    assert "ledger-read-only" in out
    assert not (tmp_path / "logs" / "path_reconcile").exists()


def test_path_reconcile_verify_ledger_reports_consistency(tmp_path, monkeypatch, capsys):
    db_path = _ledger_db(tmp_path, monkeypatch)
    root = tmp_path / "library"
    old_path = root / "sorted" / "old.mp3"
    new_path = root / "sorted" / "new.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")

    _insert_ledger(
        db_path,
        ledger_id="ledger-verify",
        created_at="2026-05-05T11:00:00Z",
        root=str(root),
        operation_type="path-update",
        old_path=str(old_path),
        new_path=str(new_path),
        affected_tables=json.dumps(["tracks", "processed_state"]),
        before_values_json=json.dumps({"old_path": str(old_path)}),
        after_values_json=json.dumps({"new_path": str(new_path)}),
        status="ready",
        error=None,
    )

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(
            root=None,
            ledger=False,
            verify_ledger="ledger-verify",
            dry_run=False,
            apply=False,
            apply_auto_safe_only=False,
            mark_stale_pstate=False,
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "VERIFY LEDGER" in out
    assert "Status                : OK" in out
    assert "tracks, processed_state" in out


@pytest.fixture()
def backend_client(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    db_path = _ledger_db(root, monkeypatch)
    _insert_ledger(
        db_path,
        ledger_id="ledger-api-1",
        created_at="2026-05-05T09:00:00Z",
        root=str(root),
        operation_type="path-audit",
        old_path=str(root / "old.mp3"),
        new_path=str(root / "new.mp3"),
        affected_tables=json.dumps(["tracks"]),
        before_values_json=json.dumps({"before": 1}),
        after_values_json=json.dumps({"after": 2}),
        status="ready",
        error=None,
    )
    with TestClient(backend_main.app) as client:
        yield client


def test_backend_reconciliation_ledger_endpoints(backend_client):
    response = backend_client.get("/api/reconciliation/ledger")
    assert response.status_code == 200
    assert response.json()[0]["ledger_id"] == "ledger-api-1"

    detail = backend_client.get("/api/reconciliation/ledger/ledger-api-1")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["ledger_id"] == "ledger-api-1"
    assert payload["affected_tables"] == json.dumps(["tracks"])


def test_validate_plan_accepts_valid_update_path_reference(tmp_path, monkeypatch, capsys):
    root = tmp_path / "library"
    db_path = _ledger_db(root, monkeypatch)
    old_path = root / "sorted" / "old.mp3"
    new_path = root / "sorted" / "new.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")
    _write_track_db(db_path, tracks=[old_path])

    plan = {
        "root": str(root),
        "planned_actions": [
            {
                "action": "update_path_reference",
                "old_path": str(old_path),
                "new_path": str(new_path),
                "confidence": 0.95,
                "reason": "same_basename",
                "risk": "LOW",
                "review_tier": "AUTO_SAFE_CANDIDATE",
            }
        ],
    }
    plan_path = _write_plan(root, "20260507_path_reconcile_plan.json", plan)

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(
            validate_plan=str(plan_path),
            root=None,
            ledger=False,
            verify_ledger=None,
            dry_run=False,
            apply=False,
            apply_auto_safe_only=False,
            mark_stale_pstate=False,
        )
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Valid actions         : 1" in out
    result_paths = list((root / "logs" / "path_reconcile").glob("*_validate_plan.json"))
    assert len(result_paths) == 1
    result_path = result_paths[0]
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["valid_actions"] == 1


def test_validate_plan_rejects_weak_match(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _ledger_db(root, monkeypatch)
    old_path = root / "sorted" / "old.mp3"
    new_path = root / "sorted" / "new.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")

    plan_path = _write_plan(
        root,
        "20260507_path_reconcile_plan.json",
        {
            "root": str(root),
            "planned_actions": [
                {
                    "action": "update_path_reference",
                    "old_path": str(old_path),
                    "new_path": str(new_path),
                    "confidence": 0.5,
                    "reason": "fuzzy_filename",
                    "risk": "REVIEW_REQUIRED",
                    "review_tier": "WEAK_MATCH",
                }
            ],
        },
    )

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(
            validate_plan=str(plan_path),
            root=None,
            ledger=False,
            verify_ledger=None,
            dry_run=False,
            apply=False,
            apply_auto_safe_only=False,
            mark_stale_pstate=False,
        )
    )
    assert rc == 1


def test_validate_plan_rejects_cross_root_new_path(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _ledger_db(root, monkeypatch)
    other_root = tmp_path / "other"
    old_path = root / "sorted" / "old.mp3"
    new_path = other_root / "new.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")

    plan_path = _write_plan(
        root,
        "20260507_path_reconcile_plan.json",
        {
            "root": str(root),
            "planned_actions": [
                {
                    "action": "update_path_reference",
                    "old_path": str(old_path),
                    "new_path": str(new_path),
                    "confidence": 0.95,
                    "reason": "same_basename",
                    "risk": "LOW",
                    "review_tier": "AUTO_SAFE_CANDIDATE",
                }
            ],
        },
    )

    result = pipeline._path_reconcile_validate_plan(plan_path)
    assert result["invalid_actions"] == 1
    assert "new_path_outside_root" in result["reasons"]


def test_validate_plan_rejects_missing_new_path(tmp_path, monkeypatch):
    root = tmp_path / "library"
    db_path = _ledger_db(root, monkeypatch)
    old_path = root / "sorted" / "old.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    _write_track_db(db_path, tracks=[old_path])

    plan_path = _write_plan(
        root,
        "20260507_path_reconcile_plan.json",
        {
            "root": str(root),
            "planned_actions": [
                {
                    "action": "update_path_reference",
                    "old_path": str(old_path),
                    "new_path": str(root / "sorted" / "missing.mp3"),
                    "confidence": 0.95,
                    "reason": "same_basename",
                    "risk": "LOW",
                    "review_tier": "AUTO_SAFE_CANDIDATE",
                }
            ],
        },
    )

    result = pipeline._path_reconcile_validate_plan(plan_path)
    assert result["invalid_actions"] == 1
    assert "new_path_missing_on_disk" in result["reasons"]


def test_validate_plan_requires_approval_for_review_required(tmp_path, monkeypatch):
    root = tmp_path / "library"
    db_path = _ledger_db(root, monkeypatch)
    old_path = root / "sorted" / "old.mp3"
    new_path = root / "sorted" / "new.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")
    _write_track_db(db_path, tracks=[old_path])

    plan = {
        "root": str(root),
        "planned_actions": [
            {
                "action": "update_path_reference",
                "old_path": str(old_path),
                "new_path": str(new_path),
                "confidence": 0.7,
                "reason": "relocation",
                "risk": "REVIEW_REQUIRED",
                "review_tier": "REVIEW_CAREFULLY",
            }
        ],
    }
    plan_path = _write_plan(root, "20260507_path_reconcile_plan.json", plan)

    result = pipeline._path_reconcile_validate_plan(plan_path)
    assert result["invalid_actions"] == 1
    assert "review_required_not_approved" in result["reasons"]

    review_state_path = plan_path.with_name(f"{plan_path.stem}_review_state.json")
    review_state_path.write_text(
        json.dumps(
            {
                "approved_actions": [
                    {
                        "action": "update_path_reference",
                        "old_path": str(old_path),
                        "new_path": str(new_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    approved = pipeline._path_reconcile_validate_plan(plan_path)
    assert approved["valid_actions"] == 1
    assert approved["invalid_actions"] == 0


def test_backend_validate_plan_latest_endpoint(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    db_path = _ledger_db(root, monkeypatch)
    old_path = root / "sorted" / "old.mp3"
    new_path = root / "sorted" / "new.mp3"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("old", encoding="utf-8")
    new_path.write_text("new", encoding="utf-8")
    _write_track_db(db_path, tracks=[old_path])
    _write_plan(
        root,
        "20260507_path_reconcile_plan.json",
        {
            "root": str(root),
            "planned_actions": [
                {
                    "action": "update_path_reference",
                    "old_path": str(old_path),
                    "new_path": str(new_path),
                    "confidence": 0.95,
                    "reason": "same_basename",
                    "risk": "LOW",
                    "review_tier": "AUTO_SAFE_CANDIDATE",
                }
            ],
        },
    )

    with TestClient(backend_main.app) as client:
        response = client.post("/api/reconciliation/validate-plan", json={"latest": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid_actions"] == 1
