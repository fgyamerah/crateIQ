"""
Targeted tests for Cycle 10 batch preparation: preparation_service,
preparation_operations_service, and needs_review_service.

Covers: explicit-confirm gating, deterministic clean-stage safety, write
reuse of tag_write_service, operation lifecycle (start/progress/finish/
cancel/restart-recovery), and Needs Review aggregation correctness
(category filtering never distorts the always-full counts).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.app.core import db as backend_db
from backend.app.services import (
    needs_review_service,
    preparation_operations_service,
    preparation_service,
    workspace_service as svc,
)
from tests.conftest import async_test


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_inbox_track(root: Path, *, artist="dj koze", title="pick up   [djcity.com]", genre="House", filename="song.mp3") -> int:
    inbox_file = root / "Inbox" / filename
    _write(inbox_file)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(inbox_file), filename, artist, title, genre),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.fixture()
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    return root


# ---------------------------------------------------------------------------
# Preflight preview -- read-only
# ---------------------------------------------------------------------------

def test_preflight_preview_is_read_only(managed_root):
    track_id = _seed_inbox_track(managed_root)
    before = (managed_root / "Inbox" / "song.mp3").read_bytes()

    preview = preparation_service.preflight_preview(managed_root)

    assert preview["inbox_total"] == 1
    assert preview["need_cleaning"] >= 1  # "dj koze" casing + junk token
    assert (managed_root / "Inbox" / "song.mp3").read_bytes() == before
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT artist FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert row[0] == "dj koze", "preview must not mutate the local index"


def test_preflight_preview_excludes_library_tracks(managed_root):
    _seed_inbox_track(managed_root)
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, status, processed_at, pipeline_ver, storage_zone) "
            "VALUES ('/x/y.mp3', 'y.mp3', 'A', 'T', 'House', 'pending', '2026-01-01T00:00:00Z', 'test', 'LIBRARY')"
        )
    preview = preparation_service.preflight_preview(managed_root)
    assert preview["inbox_total"] == 1


# ---------------------------------------------------------------------------
# Clean stage
# ---------------------------------------------------------------------------

def test_clean_tracks_applies_deterministic_changes(managed_root):
    track_id = _seed_inbox_track(managed_root, title="Pick Up   [djcity.com]")

    result = preparation_service.clean_tracks(managed_root, [track_id])

    assert result["cleaned_count"] == 1
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT title FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert "djcity.com" not in row[0]


def test_clean_tracks_no_op_when_already_clean(managed_root):
    track_id = _seed_inbox_track(managed_root, artist="DJ Koze", title="Pick Up", genre="House")
    result = preparation_service.clean_tracks(managed_root, [track_id])
    assert result["cleaned_count"] == 0
    assert result["results"][0]["status"] == "no_change"


def test_clean_tracks_never_touches_library_zone(managed_root):
    _seed_inbox_track(managed_root, title="Pick Up   [djcity.com]")
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            "INSERT INTO tracks (filepath, filename, artist, title, genre, status, processed_at, pipeline_ver, storage_zone) "
            "VALUES ('/x/y.mp3', 'y.mp3', 'A', 'Junky Title [djcity.com]', 'House', 'pending', '2026-01-01T00:00:00Z', 'test', 'LIBRARY')"
        )
        library_id = conn.execute("SELECT id FROM tracks WHERE storage_zone='LIBRARY'").fetchone()[0]

    # clean_tracks is called with a track_id that IS in the Library zone --
    # it must be silently excluded (WHERE storage_zone = 'INBOX' in the query).
    result = preparation_service.clean_tracks(managed_root, [library_id])
    assert result["cleaned_count"] == 0
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        row = conn.execute("SELECT title FROM tracks WHERE id = ?", (library_id,)).fetchone()
    assert "djcity.com" in row[0], "Library-zone tracks must never be touched by Inbox batch clean"


# ---------------------------------------------------------------------------
# Process All -- confirm gating and idempotent no-op safety
# ---------------------------------------------------------------------------

def test_start_process_all_requires_confirm(managed_root):
    _seed_inbox_track(managed_root)
    with pytest.raises(ValueError, match="confirm=true"):
        preparation_service.start_process_all(managed_root, confirm=False)


def test_start_process_all_requires_non_empty_inbox(managed_root):
    with pytest.raises(ValueError, match="Inbox is empty"):
        preparation_service.start_process_all(managed_root, confirm=True)


@async_test
async def test_run_process_all_reaches_terminal_state(managed_root):
    track_id = _seed_inbox_track(managed_root, artist="DJ Koze", title="Pick Up", genre="House")
    operation = preparation_operations_service.start_operation("process_all", track_count=1)

    await preparation_service.run_process_all(operation["id"], managed_root, [track_id])

    saved = preparation_operations_service.get_operation(operation["id"])
    assert saved["status"] in ("completed", "failed", "cancelled")
    assert saved["status"] != "running"


@async_test
async def test_run_process_all_respects_cancellation(managed_root):
    track_id = _seed_inbox_track(managed_root)
    operation = preparation_operations_service.start_operation("process_all", track_count=1)
    preparation_operations_service.request_cancel(operation["id"])

    await preparation_service.run_process_all(operation["id"], managed_root, [track_id])

    saved = preparation_operations_service.get_operation(operation["id"])
    assert saved["status"] == "cancelled"


@async_test
async def test_run_process_all_analysis_stage_is_scoped_to_its_own_batch(managed_root, monkeypatch, tmp_path):
    """Cycle: Process All's ANALYZE stage must never reach outside its captured
    Inbox batch -- an unrelated indexed track missing BPM/key must not be
    touched, even though it would be globally eligible."""
    from backend.app.services import analysis_jobs_service

    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "isolated_jobs.db")
    backend_db.init_db()

    batch_id_10 = _seed_inbox_track(managed_root, artist="Artist Ten", title="Ten", filename="ten.mp3")
    batch_id_11 = _seed_inbox_track(managed_root, artist="Artist Eleven", title="Eleven", filename="eleven.mp3")
    outside_file = managed_root / "Inbox" / "ninetynine.mp3"
    _write(outside_file)
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'ninetynine.mp3', 'Artist NN', 'NN', 'House', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(outside_file),),
        )
        outside_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")

    def fake_run(command, **kwargs):
        from types import SimpleNamespace
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="8A\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    batch = [batch_id_10, batch_id_11]
    operation = preparation_operations_service.start_operation("process_all", track_count=len(batch))
    await preparation_service.run_process_all(operation["id"], managed_root, batch)

    saved = preparation_operations_service.get_operation(operation["id"])
    assert saved["status"] == "completed"

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        rows = dict(conn.execute("SELECT id, bpm FROM tracks WHERE id IN (?, ?, ?)", (*batch, outside_id)).fetchall())
    assert rows[batch_id_10] == 120.0
    assert rows[batch_id_11] == 120.0
    assert rows[outside_id] is None, "an untouched track outside the Process All batch must never be analyzed"


@async_test
async def test_run_process_all_skips_paused_bpm_but_not_key_and_never_touches_outside_batch(managed_root, monkeypatch, tmp_path):
    """A track paused after a proven two-stage BPM decode failure must be
    excluded from Process All's BPM stage (BPM stays NULL, no tool call for
    it) while key analysis still runs on it, the pause must not fail the
    batch, exactly one bounded warning must surface, and a track outside the
    captured batch must remain fully untouched."""
    from backend.app.services import analysis_jobs_service, bpm_retry_policy_service

    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "isolated_jobs.db")
    backend_db.init_db()

    paused_id = _seed_inbox_track(managed_root, artist="Paused Artist", title="Paused", filename="paused.mp3")
    normal_id = _seed_inbox_track(managed_root, artist="Normal Artist", title="Normal", filename="normal.mp3")
    outside_file = managed_root / "Inbox" / "outside.mp3"
    _write(outside_file)
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, 'outside.mp3', 'Outside Artist', 'Outside', 'House', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(outside_file),),
        )
        outside_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    bpm_retry_policy_service.pause(paused_id)

    called_for: list[int] = []

    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")

    def fake_run(command, **kwargs):
        from types import SimpleNamespace
        called_for.append(command[0])
        if command[0] == "/fake/aubio":
            return SimpleNamespace(returncode=0, stdout="120.0 bpm\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="8A\n", stderr="")

    monkeypatch.setattr(analysis_jobs_service.subprocess, "run", fake_run)

    batch = [paused_id, normal_id]
    operation = preparation_operations_service.start_operation("process_all", track_count=len(batch))
    await preparation_service.run_process_all(operation["id"], managed_root, batch)

    saved = preparation_operations_service.get_operation(operation["id"])
    assert saved["status"] == "completed"

    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        rows = {
            row[0]: {"bpm": row[1], "key": row[2]}
            for row in conn.execute(
                "SELECT id, bpm, key_musical FROM tracks WHERE id IN (?, ?, ?)",
                (paused_id, normal_id, outside_id),
            ).fetchall()
        }

    assert rows[paused_id]["bpm"] is None, "a BPM-paused track's BPM must stay untouched by Process All"
    assert rows[paused_id]["key"] is not None, "key analysis must still run on a BPM-paused track"
    assert rows[normal_id]["bpm"] == 120.0
    assert rows[normal_id]["key"] is not None
    assert rows[outside_id]["bpm"] is None
    assert rows[outside_id]["key"] is None, "a track outside the batch must never be touched"
    assert called_for.count("/fake/aubio") == 1, "aubio must never run for the paused track"

    suppression_warnings = [w for w in saved["warnings"] if "Audio Quality Review" in w]
    assert len(suppression_warnings) == 1, "at most one bounded suppression warning must surface"
    assert "1 missing-BPM track" in suppression_warnings[0]


@async_test
async def test_run_process_all_analysis_stage_accepts_a_batch_larger_than_2000(managed_root, monkeypatch, tmp_path):
    """An Inbox can legitimately accumulate more than 2000 tracks across
    multiple imports (workspace_service's _MAX_IMPORT_FILES only bounds a
    single import operation). Process All's ANALYZE stage must not be
    silently skipped merely because its captured batch exceeds the external
    API's 2000-entry track_ids bound -- it is a trusted internal caller and
    must pass max_track_ids=None. Earlier stages are stubbed out (irrelevant
    to this regression) and no real audio files are created."""
    from backend.app.services import analysis_jobs_service

    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "isolated_jobs.db")
    backend_db.init_db()

    lightweight_count = analysis_jobs_service._MAX_SCOPED_TRACK_IDS + 100
    with sqlite3.connect(managed_root / "logs" / "processed.db") as conn:
        conn.executemany(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, 'Artist', 'Title', 'House', 'pending',
                       '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            [(f"/fake/inbox_{i}.mp3", f"inbox_{i}.mp3") for i in range(lightweight_count)],
        )
        batch = [row[0] for row in conn.execute("SELECT id FROM tracks ORDER BY id")]
    assert len(batch) > analysis_jobs_service._MAX_SCOPED_TRACK_IDS

    # This regression targets only the ANALYZE stage's scope validation --
    # stub the unrelated clean/enrich/write stages and the promotion-preview
    # tail so the test stays fast and focused.
    monkeypatch.setattr(preparation_service, "clean_tracks", lambda root, ids: {"cleaned_count": 0})
    monkeypatch.setattr(preparation_service, "enrich_tracks", lambda root, ids: {"enriched_count": 0, "warnings": []})
    monkeypatch.setattr(preparation_service, "write_tracks", lambda ids: {"written_count": 0, "failed_count": 0, "warnings": []})
    monkeypatch.setattr(
        preparation_service.workspace_service, "promotion_preview",
        lambda root, ids: {"ready_count": 0, "blocked_count": 0},
    )
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/fake/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_resolve_keyfinder_binary", lambda: "/fake/keyfinder")
    monkeypatch.setattr(
        analysis_jobs_service.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("placeholder paths are outside root; no tool should ever run")),
    )

    operation = preparation_operations_service.start_operation("process_all", track_count=len(batch))
    await preparation_service.run_process_all(operation["id"], managed_root, batch)

    saved = preparation_operations_service.get_operation(operation["id"])
    assert saved["status"] == "completed"
    assert not any("cannot exceed" in w for w in saved["warnings"]), "the analysis stage must not be rejected for scope size"
    assert not any("Analysis step skipped" in w for w in saved["warnings"])

    from backend.app.services import analysis_operations_service
    records = analysis_operations_service.list_recent(limit=2)
    assert len(records) == 2  # bpm_analysis + key_analysis both ran
    for record in records:
        assert record["mode"] == "apply_scoped"
        assert record["eligible_total"] == lightweight_count
        assert record["considered"] == 25  # the existing Process All analysis cap, applied inside the full scope


# ---------------------------------------------------------------------------
# preparation_operations_service lifecycle
# ---------------------------------------------------------------------------

def test_operation_lifecycle():
    operation = preparation_operations_service.start_operation("clean_selected", track_count=5)
    assert preparation_operations_service.get_operation(operation["id"])["status"] == "running"

    preparation_operations_service.update_progress(
        operation["id"], cleaned_count=2, enriched_count=0, written_count=0,
        needs_review_count=0, ready_count=0, failed_count=0,
    )
    mid = preparation_operations_service.get_operation(operation["id"])
    assert mid["cleaned_count"] == 2
    assert mid["status"] == "running"

    preparation_operations_service.finish_operation(
        operation["id"], status="completed", cleaned_count=5, enriched_count=0,
        written_count=0, needs_review_count=1, ready_count=4, failed_count=0, warnings=[],
    )
    done = preparation_operations_service.get_operation(operation["id"])
    assert done["status"] == "completed"
    assert done["ready_count"] == 4


def test_request_cancel_on_missing_operation_returns_none():
    assert preparation_operations_service.request_cancel("does-not-exist") is None


def test_recover_interrupted_operations_closes_running_rows():
    operation = preparation_operations_service.start_operation("process_all", track_count=3)
    recovered = preparation_operations_service.recover_interrupted_operations()
    assert recovered == 1
    saved = preparation_operations_service.get_operation(operation["id"])
    assert saved["status"] == "failed"
    assert saved["error_reason"] == "backend_restarted"


def test_finish_operation_rejects_non_terminal_status():
    operation = preparation_operations_service.start_operation("process_all", track_count=1)
    with pytest.raises(ValueError):
        preparation_operations_service.finish_operation(
            operation["id"], status="running", cleaned_count=0, enriched_count=0,
            written_count=0, needs_review_count=0, ready_count=0, failed_count=0, warnings=[],
        )


# ---------------------------------------------------------------------------
# needs_review_service aggregation
# ---------------------------------------------------------------------------

def test_needs_review_counts_are_always_full_regardless_of_filter(managed_root):
    from backend.app.services import metadata_repair_queue_service
    _seed_inbox_track(managed_root, artist="", title="", genre="", filename="a.mp3")
    _seed_inbox_track(managed_root, artist="X", title="Y", genre="", filename="b.mp3")
    metadata_repair_queue_service.refresh()

    all_result = needs_review_service.list_items("ALL")
    genre_result = needs_review_service.list_items("GENRE")

    assert all_result["counts"] == genre_result["counts"], (
        "counts must reflect the full unfiltered set regardless of the active category filter"
    )
    assert genre_result["counts"]["GENRE"] >= 1
    assert all(item["category"] == "GENRE" for item in genre_result["items"])


def test_needs_review_items_carry_deep_link_actions(managed_root):
    from backend.app.services import metadata_repair_queue_service
    _seed_inbox_track(managed_root, artist="", title="", genre="House", filename="a.mp3")
    metadata_repair_queue_service.refresh()

    result = needs_review_service.list_items("IDENTITY_ENRICHMENT")
    assert result["items"]
    for item in result["items"]:
        assert item["actions"]
        assert item["actions"][0]["route"].startswith("/")


# ---------------------------------------------------------------------------
# Regression: preflight_preview must degrade safely, never crash
# ---------------------------------------------------------------------------

def test_preflight_preview_degrades_safely_on_uninitialized_root(tmp_path):
    root = tmp_path / "not-initialized"
    root.mkdir()
    preview = preparation_service.preflight_preview(root)
    assert preview["inbox_total"] == 0


def test_preflight_preview_degrades_safely_on_missing_root(tmp_path):
    root = tmp_path / "does-not-exist"
    preview = preparation_service.preflight_preview(root)
    assert preview["inbox_total"] == 0
