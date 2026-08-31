"""Focused 1B.2B-1 persistence isolation and legacy migration coverage."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from backend.app.core import db as backend_db
from backend.app.core.library_key import library_key_for_root
from backend.app.services import analysis_operations_service, preparation_operations_service
from backend.app.services import job_service, toolkit_runner
from backend.app.services.waveform_scheduler import WaveformScheduler
from backend.app.services.switch_blocker_service import inspect_switch_blockers
from backend.app.services import sync_destination_service


def _select(monkeypatch, root):
    root.mkdir(exist_ok=True)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.delenv("CRATEIQ_BACKEND_LIBRARY_KEY", raising=False)
    return library_key_for_root(root)


def test_library_key_is_canonical_and_rootless_has_none(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert library_key_for_root(root) == library_key_for_root(alias)
    assert library_key_for_root(root) != library_key_for_root(tmp_path / "other")
    assert len(library_key_for_root(root)) == 64
    assert library_key_for_root(None) is None


def test_migrated_rows_remain_null_and_are_excluded_from_current_views(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE analysis_operations (
            id TEXT PRIMARY KEY, job_type TEXT, mode TEXT, status TEXT,
            scope_limit INTEGER, eligible_total INTEGER, considered INTEGER,
            processed INTEGER, succeeded INTEGER, skipped INTEGER, failed INTEGER,
            remaining_missing INTEGER, cancel_requested INTEGER, error_reason TEXT,
            warnings_json TEXT, created_at TEXT, started_at TEXT, finished_at TEXT)""")
        conn.execute("INSERT INTO analysis_operations VALUES ('old-done','bpm_analysis','apply','completed',0,0,0,0,0,0,0,NULL,0,NULL,NULL,'t',NULL,'t')")
        conn.execute("INSERT INTO analysis_operations VALUES ('old-live','bpm_analysis','apply','running',0,0,0,0,0,0,0,NULL,0,NULL,NULL,'t','t',NULL)")
    backend_db.init_db()
    key = _select(monkeypatch, tmp_path / "A")
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(analysis_operations)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(analysis_operations)")}
        assert "library_key" in columns
        assert "idx_analysis_operations_library_key_status" in indexes
        assert "idx_analysis_operations_library_key_created" in indexes
        assert conn.execute("SELECT library_key FROM analysis_operations WHERE id='old-done'").fetchone()[0] is None
    assert analysis_operations_service.list_recent() == []
    blockers = inspect_switch_blockers(key)
    assert blockers["can_switch"] is False
    assert blockers["ambiguous_legacy_active"][0]["operation_id"] == "old-live"


def test_two_library_operations_are_read_recovery_and_blocker_isolated(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    key_a = _select(monkeypatch, tmp_path / "A")
    a = preparation_operations_service.start_operation("process_all", track_count=1)
    _select(monkeypatch, tmp_path / "B")
    b = preparation_operations_service.start_operation("process_all", track_count=1)
    assert [row["id"] for row in preparation_operations_service.list_recent()] == [b["id"]]
    assert preparation_operations_service.get_operation(a["id"]) is None
    assert preparation_operations_service.recover_interrupted_operations() == 1
    with sqlite3.connect(path) as conn:
        a_status = conn.execute("SELECT status FROM preparation_operations WHERE id=?", (a["id"],)).fetchone()[0]
        b_status = conn.execute("SELECT status FROM preparation_operations WHERE id=?", (b["id"],)).fetchone()[0]
    assert a_status == "running"
    assert b_status == "failed"
    _select(monkeypatch, tmp_path / "A")
    blocker = inspect_switch_blockers(key_a)
    assert blocker["can_switch"] is False
    assert blocker["blockers"][0]["reason"] == "active_current_library"


def test_publish_destination_is_per_library_and_legacy_global_is_not_adopted(tmp_path, monkeypatch):
    settings_path = tmp_path / "publish.json"
    monkeypatch.setattr(sync_destination_service, "DESTINATION_SETTINGS_PATH", settings_path)
    _select(monkeypatch, tmp_path / "A")
    destination = tmp_path / "destination-a"
    sync_destination_service.set_destination(str(destination))
    assert sync_destination_service.get_configured_destination() == destination
    _select(monkeypatch, tmp_path / "B")
    assert sync_destination_service.get_configured_destination() is None
    _select(monkeypatch, tmp_path / "A")
    assert sync_destination_service.get_configured_destination() == destination

    settings_path.write_text('{"destination": "/legacy-only"}', encoding="utf-8")
    assert sync_destination_service.get_configured_destination() is None


def test_waveform_scheduler_shutdown_only_reconciles_its_origin_library(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    key_a = _select(monkeypatch, tmp_path / "A")
    key_b = _select(monkeypatch, tmp_path / "B")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE waveform_jobs")
        conn.execute(
            "CREATE TABLE waveform_jobs (id TEXT PRIMARY KEY, library_id TEXT, "
            "track_id INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, "
            "started_at TEXT, finished_at TEXT, cancel_requested INTEGER DEFAULT 0, "
            "error_code TEXT, generation_key TEXT)"
        )
        for job_id, key, track_id in (("a", key_a, 1), ("b", key_b, 2)):
            conn.execute(
                "INSERT INTO waveform_jobs (id, library_id, track_id, status, created_at) "
                "VALUES (?, ?, ?, 'queued', ?)",
                (job_id, key, track_id, now),
            )
            conn.execute(
                "INSERT INTO waveform_track_state (library_id, track_id, status, schema_version, "
                "algorithm_version, updated_at) VALUES (?, ?, 'queued', 1, 'test', ?)",
                (key, track_id, now),
            )
        conn.execute(
            "INSERT INTO waveform_jobs (id, library_id, track_id, status, created_at) "
            "VALUES ('null-job', NULL, 3, 'queued', ?)",
            (now,),
        )

    scheduler = WaveformScheduler(library_key=key_b)
    scheduler._started = True
    import asyncio
    asyncio.run(scheduler.stop(drain_grace_seconds=0))

    with sqlite3.connect(path) as conn:
        statuses = dict(conn.execute("SELECT id, status FROM waveform_jobs"))
        states = dict(conn.execute("SELECT track_id, status FROM waveform_track_state"))
    assert statuses == {"a": "queued", "b": "failed", "null-job": "queued"}
    assert states == {1: "queued", 2: "failed"}


def test_waveform_track_state_active_rows_block_but_terminal_rows_do_not(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    key_a = _select(monkeypatch, tmp_path / "A")
    key_b = _select(monkeypatch, tmp_path / "B")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE waveform_track_state")
        conn.execute(
            "CREATE TABLE waveform_track_state (library_id TEXT, track_id INTEGER NOT NULL, "
            "status TEXT NOT NULL, schema_version INTEGER NOT NULL, "
            "algorithm_version TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        rows = (
            (key_a, 1, "processing"),
            (None, 2, "queued"),
            (key_b, 3, "processing"),
            (key_a, 4, "ready"),
            (key_a, 5, "failed"),
        )
        conn.executemany(
            "INSERT INTO waveform_track_state (library_id, track_id, status, schema_version, "
            "algorithm_version, updated_at) VALUES (?, ?, ?, 1, 'test', ?)",
            [(key, track_id, status, now) for key, track_id, status in rows],
        )
    result = inspect_switch_blockers(key_a)
    blockers = {item["operation_id"]: item["reason"] for item in result["blockers"]}
    ambiguous = {item["operation_id"] for item in result["ambiguous_legacy_active"]}
    assert blockers == {
        "1": "active_current_library",
        "3": "foreign_active_installation_inconsistency",
    }
    assert ambiguous == {"2"}
    assert "4" not in blockers and "5" not in blockers


def test_generic_background_updates_retain_originating_library_key(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    key_a = _select(monkeypatch, tmp_path / "A")
    job = job_service.create_job("audit-quality", [])
    assert job.library_key == key_a
    key_b = _select(monkeypatch, tmp_path / "B")
    job_service.mark_running(job.id, library_key=job.library_key)
    job_service.mark_finished(job.id, "succeeded", 0, library_key=job.library_key)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT library_key, status FROM jobs WHERE id = ?", (job.id,)).fetchone()
    assert row == (key_a, "succeeded")
    assert job_service.get_job(job.id) is None
    assert job_service.get_job(job.id, library_key=key_a).status == "succeeded"
    assert key_b != key_a


def test_generic_background_scheduling_uses_created_job_library_key(tmp_path, monkeypatch):
    import asyncio

    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    key_a = _select(monkeypatch, tmp_path / "A")
    job = job_service.create_job("audit-quality", [])
    _select(monkeypatch, tmp_path / "B")
    seen: list[tuple[str, str]] = []

    async def fake_run(job_id, _cmd, _log_path, library_key):
        seen.append((job_id, library_key))

    monkeypatch.setattr(toolkit_runner, "_run_job", fake_run)

    async def schedule():
        toolkit_runner.create_and_start_job(
            job.id, job.command, job.args, library_key=job.library_key
        )
        await asyncio.gather(*tuple(toolkit_runner._running_tasks))

    asyncio.run(schedule())
    assert seen == [(job.id, key_a)]


def test_waveform_worker_retains_origin_key_after_process_context_changes(
    tmp_path, monkeypatch
):
    import asyncio
    from backend.app.models.waveform import SourceStatSnapshot
    from backend.app.services import waveform_identity, waveform_job_service

    path = tmp_path / "jobs.db"
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", path)
    backend_db.init_db()
    root_a = tmp_path / "A"
    key_a = _select(monkeypatch, root_a)
    snapshot = SourceStatSnapshot(
        library_id=key_a,
        track_id=7,
        source_size_bytes=1,
        source_mtime_ns=2,
        source_ctime_ns=3,
        source_device=4,
        source_inode=5,
    )
    job = waveform_job_service.submit_generation_job(
        snapshot=snapshot,
        generation_key=waveform_identity.compute_generation_key(snapshot),
        force=False,
        max_queue_size=32,
    ).job
    seen: list[str] = []

    async def runner(job_id, _token):
        seen.append(job_id)

    scheduler = WaveformScheduler(
        library_key=key_a, library_root=root_a, runner=runner
    )
    key_b = _select(monkeypatch, tmp_path / "B")

    async def execute():
        await scheduler.start()
        scheduler.enqueue(job.id)
        await asyncio.wait_for(scheduler._queue.join(), timeout=2)
        await scheduler.stop(drain_grace_seconds=0)

    asyncio.run(execute())
    assert seen == [job.id]
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT library_id, status FROM waveform_jobs WHERE id = ?", (job.id,)
        ).fetchone()
    assert row == (key_a, "failed")
    assert key_b != key_a
