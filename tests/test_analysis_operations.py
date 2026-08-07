"""Cycle 2 Stage 1 tests: persisted Analysis Jobs (BPM/key) operation history.

These exercise backend/app/services/analysis_operations_service.py and its
jobs.db schema directly (no HTTP layer) -- API-level integration tests for
the confirmed BPM/key run -> persisted history path live in
tests/test_backend_api.py alongside the existing BPM/key runner tests.

No audio tool executes here and no processed.db is touched -- this table
lives only in the backend's own jobs.db.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.app.core import db as backend_db
from backend.app.services import analysis_operations_service as ops


@pytest.fixture()
def jobs_db(tmp_path, monkeypatch):
    path = tmp_path / "operational" / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    return path


def test_schema_creation_is_idempotent_and_additive(jobs_db):
    # init_db() already ran once via the fixture; calling it again (as happens
    # on every real backend startup) must not raise or duplicate anything.
    backend_db.init_db()
    with sqlite3.connect(jobs_db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_operations)")}
    assert {
        "id", "job_type", "mode", "status", "scope_limit", "eligible_total",
        "considered", "processed", "succeeded", "skipped", "failed",
        "remaining_missing", "cancel_requested", "error_reason",
        "warnings_json", "created_at", "started_at", "finished_at",
    }.issubset(columns)


def test_schema_migration_is_safe_on_an_existing_older_jobs_db(tmp_path, monkeypatch):
    """init_db() must tolerate a jobs.db that predates analysis_operations."""
    path = tmp_path / "legacy" / "jobs.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, command TEXT NOT NULL, "
            "args_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending', "
            "created_at TEXT NOT NULL)"
        )
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    with sqlite3.connect(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "analysis_operations" in tables
    assert "jobs" in tables  # the pre-existing table was left alone


def test_start_update_finish_completed_lifecycle(jobs_db):
    op = ops.start_operation("bpm_analysis", scope_limit=10, eligible_total=3, considered=3)
    record = ops.get_operation(op["id"])
    assert record["status"] == "running"
    assert record["job_type"] == "bpm_analysis"
    assert record["mode"] == "apply"
    assert record["scope_limit"] == 10
    assert record["eligible_total"] == 3
    assert record["considered"] == 3
    assert record["processed"] == 0 and record["succeeded"] == 0
    assert record["created_at"] and record["started_at"]
    assert record["finished_at"] is None
    assert record["cancel_requested"] is False

    ops.update_progress(op["id"], processed=1, succeeded=1, skipped=0, failed=0)
    mid = ops.get_operation(op["id"])
    assert mid["processed"] == 1 and mid["succeeded"] == 1 and mid["status"] == "running"

    ops.finish_operation(
        op["id"], status="completed", processed=3, succeeded=2, skipped=1, failed=0,
        remaining_missing=0, warnings=["Skipped alpha.mp3: file is missing or unreadable."],
    )
    done = ops.get_operation(op["id"])
    assert done["status"] == "completed"
    assert (done["processed"], done["succeeded"], done["skipped"], done["failed"]) == (3, 2, 1, 0)
    assert done["remaining_missing"] == 0
    assert done["finished_at"]
    assert done["error_reason"] is None
    assert done["warnings"] == ["Skipped alpha.mp3: file is missing or unreadable."]


def test_finish_failed_records_truthful_error_reason(jobs_db):
    op = ops.start_operation("key_analysis", scope_limit=5, eligible_total=5, considered=5)
    ops.finish_operation(
        op["id"], status="failed", processed=0, succeeded=0, skipped=0, failed=0,
        remaining_missing=None, warnings=[], error_reason="keyfinder-cli could not start",
    )
    record = ops.get_operation(op["id"])
    assert record["status"] == "failed"
    assert record["error_reason"] == "keyfinder-cli could not start"
    assert record["remaining_missing"] is None


def test_finish_operation_rejects_non_terminal_status(jobs_db):
    op = ops.start_operation("bpm_analysis", scope_limit=1, eligible_total=1, considered=1)
    with pytest.raises(ValueError):
        ops.finish_operation(
            op["id"], status="running", processed=0, succeeded=0, skipped=0, failed=0,
            remaining_missing=None, warnings=[],
        )


def test_warnings_are_bounded_not_a_giant_blob(jobs_db):
    op = ops.start_operation("bpm_analysis", scope_limit=50, eligible_total=50, considered=50)
    many_warnings = [f"Skipped track{i}.mp3: file is missing or unreadable." for i in range(200)]
    ops.finish_operation(
        op["id"], status="completed", processed=50, succeeded=0, skipped=50, failed=0,
        remaining_missing=0, warnings=many_warnings,
    )
    record = ops.get_operation(op["id"])
    assert len(record["warnings"]) == 50


def test_cancel_is_idempotent_and_safe_on_terminal_or_unknown_operations(jobs_db):
    assert ops.request_cancel("does-not-exist") is None

    op = ops.start_operation("bpm_analysis", scope_limit=5, eligible_total=5, considered=5)
    assert ops.is_cancel_requested(op["id"]) is False

    first = ops.request_cancel(op["id"])
    assert first["cancel_requested"] is True
    assert first["status"] == "running"
    assert ops.is_cancel_requested(op["id"]) is True

    # Calling cancel again on the same running operation is a harmless no-op.
    second = ops.request_cancel(op["id"])
    assert second["cancel_requested"] is True

    ops.finish_operation(
        op["id"], status="cancelled", processed=1, succeeded=0, skipped=0, failed=0,
        remaining_missing=4, warnings=[], error_reason="user_cancelled",
    )
    # Requesting cancellation on an already-finished operation must not raise
    # or resurrect it into a false-running state.
    after_finish = ops.request_cancel(op["id"])
    assert after_finish["status"] == "cancelled"
    final = ops.get_operation(op["id"])
    assert final["status"] == "cancelled"


def test_update_progress_cannot_resurrect_a_closed_operation(jobs_db):
    """A stale in-flight progress write must never overwrite a terminal row."""
    op = ops.start_operation("bpm_analysis", scope_limit=5, eligible_total=5, considered=5)
    ops.finish_operation(
        op["id"], status="cancelled", processed=1, succeeded=0, skipped=0, failed=0,
        remaining_missing=4, warnings=[], error_reason="user_cancelled",
    )
    ops.update_progress(op["id"], processed=99, succeeded=99, skipped=0, failed=0)
    record = ops.get_operation(op["id"])
    assert record["status"] == "cancelled"
    assert record["processed"] == 1  # untouched by the late update


def test_list_recent_orders_newest_first_and_respects_limit(jobs_db):
    ids = []
    for job_type in ("bpm_analysis", "key_analysis", "bpm_analysis"):
        op = ops.start_operation(job_type, scope_limit=1, eligible_total=1, considered=1)
        ops.finish_operation(
            op["id"], status="completed", processed=1, succeeded=1, skipped=0, failed=0,
            remaining_missing=0, warnings=[],
        )
        ids.append(op["id"])

    listed = ops.list_recent(limit=50)
    assert [record["id"] for record in listed] == list(reversed(ids))

    capped = ops.list_recent(limit=2)
    assert len(capped) == 2


def test_no_history_without_a_started_operation(jobs_db):
    """Confirms the table starts empty -- previews must never create a row."""
    assert ops.list_recent() == []


def test_recover_interrupted_operations_closes_running_rows_as_failed(jobs_db):
    running = ops.start_operation("bpm_analysis", scope_limit=5, eligible_total=5, considered=5)
    already_done = ops.start_operation("key_analysis", scope_limit=5, eligible_total=5, considered=5)
    ops.finish_operation(
        already_done["id"], status="completed", processed=5, succeeded=5, skipped=0, failed=0,
        remaining_missing=0, warnings=[],
    )

    recovered = ops.recover_interrupted_operations()
    assert recovered == 1

    reconciled = ops.get_operation(running["id"])
    assert reconciled["status"] == "failed"
    assert reconciled["error_reason"] == "backend_restarted"
    assert reconciled["finished_at"]

    untouched = ops.get_operation(already_done["id"])
    assert untouched["status"] == "completed"
    assert untouched["error_reason"] is None

    # Recovery must be safe to run again (e.g. a second restart) with nothing left to close.
    assert ops.recover_interrupted_operations() == 0
