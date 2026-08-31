"""
Targeted tests for Cycle 12: wiring provider_routing_service + consensus_service
into preparation_service's Process All enrich stage.

Covers: routing is actually used (not the old Beets+MusicBrainz-only rule),
unconfigured/missing-credential providers are skipped without failing the
batch, HIGH field consensus auto-applies while MEDIUM/LOW/CONFLICT never do,
a HIGH track identity never forces genre to HIGH, existing valid metadata is
never silently replaced (only the approved junk/invalid exception), provider
evidence survives into the Needs Review queue, one track's provider failure
never crashes the batch, writes stay confined to managed Inbox copies, and
repeated runs are idempotent.

Provider responses are mocked throughout -- no real network calls, no
provider quota used.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.app.services import (
    analysis_jobs_service,
    enrichment_review_service,
    needs_review_service,
    preparation_service,
    provider_routing_service as routing,
    settings_service,
    workspace_service as svc,
)
from backend.app.core import db as backend_db
from backend.app.services.providers.base import ProviderCandidate
from backend.app.services.operation_admission_gate import (
    LibraryOperationDrainingError,
    OperationAdmissionGate,
)
from tests.conftest import async_test

_HIGH_ARTIST_TITLE = {
    "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up")],
    "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="DJ Koze", title="Pick Up")],
}


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_inbox_track(root: Path, *, artist="", title="", genre="", filename="song.mp3") -> int:
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


def _seed_library_track(root: Path, *, artist="A", title="Junky [djcity.com]", genre="House", filename="lib.mp3") -> int:
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES ('/x/lib.mp3', ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z', 'test', 'LIBRARY')""",
            (filename, artist, title, genre),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_genre_mappings(root: Path, pairs: dict[str, str]) -> None:
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS genre_mappings(raw_genre TEXT, normalized_genre TEXT, enabled INTEGER DEFAULT 1)")
        conn.executemany(
            "INSERT INTO genre_mappings (raw_genre, normalized_genre, enabled) VALUES (?, ?, 1)",
            [(k.strip().casefold(), v) for k, v in pairs.items()],
        )
        conn.commit()


@pytest.fixture()
def managed_root(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")
    return root


def _current_fields(root: Path, track_id: int) -> dict:
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT artist, title, genre FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# 1 & 2 & 3: Process All calls provider routing; only configured/available
# providers actually run; missing credentials skip cleanly, no crash.
# ---------------------------------------------------------------------------

def test_enrich_tracks_routes_through_provider_routing_service(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="a.mp3")

    calls = []

    def fake_gather_evidence(tid, **kwargs):
        calls.append(tid)
        return {}

    monkeypatch.setattr(routing, "gather_evidence", fake_gather_evidence)

    preparation_service.enrich_tracks(managed_root, [track_id])

    assert calls == [track_id], "Process All's enrich stage must call provider_routing_service.gather_evidence"


def test_enrich_tracks_only_configured_providers_run_missing_credentials_skip_cleanly(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="b.mp3")
    monkeypatch.setattr(enrichment_review_service, "online_lookup", lambda tid, src: {"items": []})

    deezer_calls = []
    other_calls = []

    def fake_deezer_search(*a, **k):
        deezer_calls.append(1)
        from backend.app.services.providers.base import ProviderResult
        return ProviderResult(candidates=[])

    def fake_other_search(*a, **k):
        other_calls.append(1)
        from backend.app.services.providers.base import ProviderResult
        return ProviderResult(candidates=[])

    monkeypatch.setattr(routing.deezer_client, "search_track", fake_deezer_search)
    for adapter in (routing.discogs_client, routing.beatport_client, routing.spotify_client, routing.lastfm_client, routing.youtube_client):
        monkeypatch.setattr(adapter, "search_track", fake_other_search)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    # Deezer requires no credentials and is genuinely "ready" -- it must run.
    assert deezer_calls, "deezer has no credential requirement and must be queried"
    # Every credential-requiring provider has no saved credentials in this
    # fresh tmp settings file -- must be skipped, never called.
    assert not other_calls, "credential-requiring providers with no saved credentials must be skipped, not called"
    assert result["warnings"] == [] or all("failed" not in w.lower() for w in result["warnings"])


# ---------------------------------------------------------------------------
# 4-7: HIGH auto-applies; MEDIUM / LOW / CONFLICT never auto-apply.
# ---------------------------------------------------------------------------

def test_enrich_tracks_high_field_consensus_auto_applies(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="c.mp3")
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: _HIGH_ARTIST_TITLE)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    assert result["enriched_count"] == 1
    fields = _current_fields(managed_root, track_id)
    assert fields["artist"] == "DJ Koze"
    assert fields["title"] == "Pick Up"

    review = enrichment_review_service.get_review()
    applied = [i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_apply"]
    assert applied and applied[0]["decision"] == "applied"


def test_enrich_tracks_medium_never_auto_applies(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, artist="DJ Koze", title="Pick Up", filename="d.mp3")
    _seed_genre_mappings(managed_root, {"deep house": "Deep House"})
    # beets alone with its own 'high' raw tier -> identity MEDIUM (single_source_high_confidence);
    # beatport genre authority present but identity isn't HIGH -> genre MEDIUM.
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up", raw_confidence="high")],
        "beatport": [ProviderCandidate(provider="beatport", genre="Deep House")],
    }
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    assert result["enriched_count"] == 0
    assert _current_fields(managed_root, track_id)["genre"] == ""
    review = enrichment_review_service.get_review()
    pending = [i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_review" and i["decision"] == "pending"]
    assert pending, "MEDIUM genre verdict must be queued for review, never auto-applied"


def test_enrich_tracks_low_never_auto_applies(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="e.mp3")
    evidence = {"beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title=None)]}
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    assert result["enriched_count"] == 0
    assert _current_fields(managed_root, track_id)["artist"] == ""


def test_enrich_tracks_conflict_never_auto_applies(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="f.mp3")
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up")],
        "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="Someone Else", title="Different Song")],
    }
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    assert result["enriched_count"] == 0
    fields = _current_fields(managed_root, track_id)
    assert fields["artist"] == "" and fields["title"] == ""
    review = enrichment_review_service.get_review()
    pending = [i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_review"]
    assert pending, "CONFLICT verdict must surface in the review queue"


# ---------------------------------------------------------------------------
# 8: HIGH track identity must NOT force genre to HIGH when authorities conflict.
# ---------------------------------------------------------------------------

def test_high_identity_with_conflicting_genre_does_not_auto_apply_genre(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="g.mp3")
    _seed_genre_mappings(managed_root, {"deep house": "Deep House", "amapiano": "Amapiano"})
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up", genre="Deep House")],
        "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="DJ Koze", title="Pick Up")],
        "beatport": [ProviderCandidate(provider="beatport", genre="Deep House")],
        "discogs": [ProviderCandidate(provider="discogs", genre="Amapiano")],
    }
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    fields = _current_fields(managed_root, track_id)
    # Identity (artist/title) is HIGH and gets auto-applied...
    assert fields["artist"] == "DJ Koze"
    assert fields["title"] == "Pick Up"
    # ...but genre authorities disagree, so genre must stay untouched.
    assert fields["genre"] == ""
    assert result["enriched_count"] == 1
    review = enrichment_review_service.get_review()
    pending = [i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_review"]
    assert pending, "conflicting genre authorities must surface for review even though identity is HIGH"
    assert "genre" in pending[0]["reason"]


# ---------------------------------------------------------------------------
# 9: existing valid non-empty metadata is never replaced without the junk rule.
# ---------------------------------------------------------------------------

def test_existing_valid_metadata_is_not_replaced_without_junk_evidence(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, artist="Daft Punk", title="One More Time", filename="h.mp3")
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up")],
        "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="DJ Koze", title="Pick Up")],
    }
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    preparation_service.enrich_tracks(managed_root, [track_id])

    fields = _current_fields(managed_root, track_id)
    assert fields["artist"] == "Daft Punk", "valid existing artist must never be silently overwritten"
    assert fields["title"] == "One More Time"
    review = enrichment_review_service.get_review()
    pending = [i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_review"]
    assert pending, "a HIGH suggestion blocked by existing valid data must still surface for manual review"


def test_junk_current_value_is_replaced_when_replacement_is_high(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, artist="Unknown Artist", title="One More Time", filename="i.mp3")
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="Daft Punk", title="One More Time")],
        "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="Daft Punk", title="One More Time")],
    }
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    result = preparation_service.enrich_tracks(managed_root, [track_id])

    assert result["enriched_count"] == 1
    assert _current_fields(managed_root, track_id)["artist"] == "Daft Punk"


# ---------------------------------------------------------------------------
# 10: provenance survives into the review/write state.
# ---------------------------------------------------------------------------

def test_provenance_survives_into_review_and_applied_items(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="j.mp3")
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: _HIGH_ARTIST_TITLE)

    preparation_service.enrich_tracks(managed_root, [track_id])

    review = enrichment_review_service.get_review()
    applied = next(i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_apply")
    assert applied["evidence"]["artist"], "field-level provider evidence must be preserved on the applied item"
    assert "beets" in " ".join(applied["evidence"]["artist"]) or "musicbrainz" in " ".join(applied["evidence"]["artist"])


# ---------------------------------------------------------------------------
# 11: provider/consensus failure on one track never crashes the batch.
# ---------------------------------------------------------------------------

def test_provider_failure_on_one_track_does_not_crash_the_batch(managed_root, monkeypatch):
    ok_id = _seed_inbox_track(managed_root, filename="k.mp3")
    bad_id = _seed_inbox_track(managed_root, filename="l.mp3")

    def flaky_gather_evidence(tid, **kwargs):
        if tid == bad_id:
            raise RuntimeError("simulated provider outage")
        return _HIGH_ARTIST_TITLE

    monkeypatch.setattr(routing, "gather_evidence", flaky_gather_evidence)

    result = preparation_service.enrich_tracks(managed_root, [ok_id, bad_id])

    assert result["enriched_count"] == 1
    assert any(str(bad_id) in w for w in result["warnings"])
    assert _current_fields(managed_root, ok_id)["artist"] == "DJ Koze"


# ---------------------------------------------------------------------------
# 12 & 13: source integrity -- only managed Inbox copies are ever touched.
# ---------------------------------------------------------------------------

def test_enrich_tracks_never_considers_library_zone_tracks(managed_root, monkeypatch):
    inbox_id = _seed_inbox_track(managed_root, filename="m.mp3")
    library_id = _seed_library_track(managed_root)

    calls = []
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: calls.append(tid) or {})

    preparation_service.enrich_tracks(managed_root, [inbox_id, library_id])

    assert calls == [inbox_id], "Library-zone tracks must never enter the batch enrichment path"


def test_external_original_source_untouched_by_enrich(managed_root, monkeypatch, tmp_path):
    external_dir = tmp_path / "external-original-import-source"
    external_file = external_dir / "original.mp3"
    _write(external_file, b"original-bytes-untouched")
    before = external_file.read_bytes()

    track_id = _seed_inbox_track(managed_root, filename="n.mp3")
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: _HIGH_ARTIST_TITLE)

    preparation_service.enrich_tracks(managed_root, [track_id])

    assert external_file.read_bytes() == before, "external import sources must never be touched by Process All"


# ---------------------------------------------------------------------------
# 14: repeated Process All is idempotent -- no duplicate work, no re-query.
# ---------------------------------------------------------------------------

def test_enrich_tracks_is_idempotent_on_repeated_runs(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="o.mp3")
    _seed_genre_mappings(managed_root, {"deep house": "Deep House"})
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up")],
        "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="DJ Koze", title="Pick Up")],
        "beatport": [ProviderCandidate(provider="beatport", genre="Deep House")],
        "lastfm": [ProviderCandidate(provider="lastfm", genre="Deep House")],
    }
    calls = []

    def fake_gather_evidence(tid, **kwargs):
        calls.append(tid)
        return evidence

    monkeypatch.setattr(routing, "gather_evidence", fake_gather_evidence)

    first = preparation_service.enrich_tracks(managed_root, [track_id])
    assert first["enriched_count"] == 1
    fields = _current_fields(managed_root, track_id)
    assert fields["artist"] == "DJ Koze" and fields["title"] == "Pick Up" and fields["genre"] == "Deep House"

    second = preparation_service.enrich_tracks(managed_root, [track_id])
    assert second["considered"] == 0, "a fully-enriched track must no longer be eligible on the next run"
    assert calls == [track_id], "gather_evidence must not be called again once the track needs no more enrichment"


# ---------------------------------------------------------------------------
# 15: Process All's real entrypoint owns a reserved scope from admission.
# ---------------------------------------------------------------------------

@async_test
async def test_process_all_cancelled_before_coroutine_starts_releases_reserved_scope(monkeypatch, tmp_path):
    gate = OperationAdmissionGate()
    scheduled: list[asyncio.Task] = []
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)
    monkeypatch.setattr(preparation_service, "_inbox_track_ids", lambda *_: [1])
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()

    async def should_not_start(*_args, **_kwargs):
        raise AssertionError("cancelled Process All coroutine must not start")

    monkeypatch.setattr(preparation_service, "run_process_all", should_not_start)
    real_create_task = asyncio.create_task

    def capture(coro):
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr(preparation_service.asyncio, "create_task", capture)
    started = preparation_service.start_process_all(tmp_path, confirm=True)
    scheduled[0].cancel()
    await asyncio.gather(scheduled[0], return_exceptions=True)
    parent = preparation_service.preparation_operations_service.get_operation(started["operation_id"])
    assert parent is not None and parent["status"] == "cancelled"
    assert gate.status()["active_operation_scopes"] == 0
    await gate.begin_draining_async()


def test_process_all_row_or_task_creation_failure_releases_reserved_scope(monkeypatch, tmp_path):
    gate = OperationAdmissionGate()
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)
    monkeypatch.setattr(preparation_service, "_inbox_track_ids", lambda *_: [1])
    with pytest.raises(RuntimeError, match="row failed"):
        monkeypatch.setattr(
            preparation_service.preparation_operations_service, "start_operation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("row failed")),
        )
        preparation_service.start_process_all(tmp_path, confirm=True)
    assert gate.status()["active_operation_scopes"] == 0

    finished: list[str] = []
    monkeypatch.setattr(preparation_service.preparation_operations_service, "start_operation", lambda *_args, **_kwargs: {"id": "op"})
    monkeypatch.setattr(
        preparation_service.preparation_operations_service, "finish_operation",
        lambda operation_id, **_kwargs: finished.append(operation_id),
    )

    def fail_schedule(coro):
        coro.close()
        raise RuntimeError("schedule failed")

    monkeypatch.setattr(preparation_service.asyncio, "create_task", fail_schedule)
    with pytest.raises(RuntimeError, match="schedule failed"):
        preparation_service.start_process_all(tmp_path, confirm=True)
    assert finished == ["op"]
    assert gate.status()["active_operation_scopes"] == 0

    monkeypatch.setattr(
        preparation_service.preparation_operations_service, "finish_operation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("terminal failed")),
    )
    with pytest.raises(RuntimeError, match="terminal failed"):
        preparation_service.start_process_all(tmp_path, confirm=True)
    assert gate.status()["active_operation_scopes"] == 0
    gate.begin_draining()


def test_process_all_scheduling_failure_terminalizes_durable_parent(monkeypatch, tmp_path):
    gate = OperationAdmissionGate()
    created: list[str] = []
    original_start = preparation_service.preparation_operations_service.start_operation
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)
    monkeypatch.setattr(preparation_service, "_inbox_track_ids", lambda *_: [1])
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()

    def record_start(*args, **kwargs):
        operation = original_start(*args, **kwargs)
        created.append(operation["id"])
        return operation

    def fail_schedule(coro):
        coro.close()
        raise RuntimeError("schedule failed")

    monkeypatch.setattr(preparation_service.preparation_operations_service, "start_operation", record_start)
    monkeypatch.setattr(preparation_service.asyncio, "create_task", fail_schedule)
    with pytest.raises(RuntimeError, match="schedule failed"):
        preparation_service.start_process_all(tmp_path, confirm=True)
    parent = preparation_service.preparation_operations_service.get_operation(created[0])
    assert parent is not None and parent["status"] == "failed"
    assert gate.status()["active_operation_scopes"] == 0


def test_process_all_reservation_rejects_before_parent_row_when_already_draining(monkeypatch, tmp_path):
    gate = OperationAdmissionGate()
    rows: list[object] = []
    gate.begin_draining()
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)
    monkeypatch.setattr(preparation_service, "_inbox_track_ids", lambda *_: [1])
    monkeypatch.setattr(
        preparation_service.preparation_operations_service, "start_operation",
        lambda *_args, **_kwargs: rows.append(object()) or {"id": "never"},
    )
    with pytest.raises(LibraryOperationDrainingError):
        preparation_service.start_process_all(tmp_path, confirm=True)
    assert rows == []


@async_test
async def test_process_all_execution_exception_releases_reserved_scope(monkeypatch, tmp_path):
    gate = OperationAdmissionGate()
    scope = gate.reserve_operation_scope()
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)
    monkeypatch.setattr(
        preparation_service, "clean_tracks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("clean failed")),
    )
    monkeypatch.setattr(preparation_service.preparation_operations_service, "finish_operation", lambda *_args, **_kwargs: None)
    await preparation_service.run_process_all("op", tmp_path, [1], durable_scope=scope)
    assert gate.status()["active_operation_scopes"] == 0
    await gate.begin_draining_async()


@async_test
async def test_process_all_real_analysis_descendant_finishes_before_concurrent_drain(monkeypatch, managed_root, tmp_path):
    """The real Process All adapter retains scope through a real BPM row create."""
    _seed_inbox_track(managed_root, filename="drain-boundary.mp3")
    gate = OperationAdmissionGate()
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)
    monkeypatch.setattr(analysis_jobs_service, "operation_admission_gate", gate)
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()
    monkeypatch.setattr(preparation_service, "clean_tracks", lambda *_args: {"cleaned_count": 0})
    monkeypatch.setattr(
        preparation_service, "enrich_tracks", lambda *_args: {"enriched_count": 0, "warnings": []},
    )
    monkeypatch.setattr(
        preparation_service, "write_tracks", lambda *_args: {"written_count": 0, "failed_count": 0, "warnings": []},
    )
    from backend.app.services import metadata_repair_queue_service
    monkeypatch.setattr(metadata_repair_queue_service, "refresh", lambda: None)
    monkeypatch.setattr(analysis_jobs_service, "_resolve_aubio_binary", lambda: "/safe/aubio")
    monkeypatch.setattr(analysis_jobs_service, "_bpm_candidates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(analysis_jobs_service, "_raw_missing_bpm_count", lambda *_args, **_kwargs: 0)

    child_create_entered = threading.Event()
    allow_child_create = threading.Event()
    drain_started = threading.Event()
    drain_returned = threading.Event()
    controller_errors: list[BaseException] = []
    original_start = analysis_jobs_service.analysis_operations_service.start_operation
    captured_scope = []
    original_run = analysis_jobs_service.run

    def capture_scope(job_type, **kwargs):
        if job_type == "bpm_analysis":
            captured_scope.append(kwargs["durable_scope"])
        return original_run(job_type, **kwargs)

    def pause_before_real_child_row(*args, **kwargs):
        child_create_entered.set()
        if not allow_child_create.wait(2):
            raise RuntimeError("test did not release BPM child creation")
        return original_start(*args, **kwargs)

    def drain_controller() -> None:
        try:
            assert child_create_entered.wait(2)
            drainer = threading.Thread(target=lambda: (gate.begin_draining(), drain_returned.set()))
            drainer.start()
            deadline = time.monotonic() + 2
            while gate.state.value != "DRAINING_FOR_LIBRARY_SWITCH" and time.monotonic() < deadline:
                time.sleep(0.001)
            assert gate.state.value == "DRAINING_FOR_LIBRARY_SWITCH"
            drain_started.set()
            # The real Process All scope remains active while the real BPM
            # operation-row helper is paused, so this concurrent drain cannot
            # return until the child create is allowed and the adapter exits.
            assert gate.status()["active_operation_scopes"] == 1
            assert not drain_returned.is_set()
            allow_child_create.set()
            drainer.join(2)
            assert drain_returned.is_set()
        except BaseException as exc:  # surfaced on the test task below
            controller_errors.append(exc)

    monkeypatch.setattr(analysis_jobs_service, "run", capture_scope)
    monkeypatch.setattr(analysis_jobs_service.analysis_operations_service, "start_operation", pause_before_real_child_row)
    controller = threading.Thread(target=drain_controller)
    controller.start()

    started = preparation_service.start_process_all(managed_root, confirm=True)
    assert started["operation_id"]
    assert await asyncio.to_thread(drain_started.wait, 2)

    # The real adapter task is the only Process All task in this test loop.
    while gate.status()["active_operation_scopes"]:
        await asyncio.sleep(0)
    controller.join(2)
    assert not controller_errors
    assert drain_returned.is_set()
    assert len(captured_scope) == 1

    with backend_db.get_conn() as conn:
        before = conn.execute("SELECT COUNT(*) FROM analysis_operations").fetchone()[0]
        bpm_before = conn.execute(
            "SELECT COUNT(*) FROM analysis_operations WHERE job_type = 'bpm_analysis'"
        ).fetchone()[0]
    with pytest.raises(LibraryOperationDrainingError):
        analysis_jobs_service.run(
            "bpm_analysis", confirm=True, limit=25, track_ids=[1], max_track_ids=None,
            durable_scope=captured_scope[0],
        )
    with backend_db.get_conn() as conn:
        after = conn.execute("SELECT COUNT(*) FROM analysis_operations").fetchone()[0]
        bpm_after = conn.execute(
            "SELECT COUNT(*) FROM analysis_operations WHERE job_type = 'bpm_analysis'"
        ).fetchone()[0]
    assert bpm_before >= 1
    assert after == before and bpm_after == bpm_before


# ---------------------------------------------------------------------------
# 1B.2B-1: Process All cancellation after execution starts with A→B context switch
# ---------------------------------------------------------------------------

@async_test
async def test_process_all_cancelled_after_start_with_library_switch_releases_scope_and_terminalizes_origin(
    tmp_path, monkeypatch
):
    """
    Real A→B regression test for 1B.2B-1 cancellation defect.

    1. Create/select Library A.
    2. Start real Process All through its actual service entrypoint.
    3. Wait until the Process All coroutine has definitely begun execution.
    4. Change surrounding/current selected-library context to Library B.
    5. Cancel the running Process All task via explicit asyncio.Task.cancel().
    6. Await the task and assert asyncio.CancelledError is propagated.
    7. Assert:
       - A parent operation is terminal/cancelled;
       - A parent is NOT RUNNING;
       - B has not been modified;
       - operation scope is released;
       - begin_draining() can complete afterward.
    """
    import sqlite3
    from backend.app.services import operation_admission_gate as gate_module
    from backend.app.core import db as backend_db
    from backend.app.core.library_key import current_library_key

    # --- Setup Library A ---
    root_a = tmp_path / "A"
    root_a.mkdir(parents=True)
    from backend.app.services import workspace_service as svc
    svc.configure_workspace(root_a)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root_a))
    monkeypatch.delenv("CRATEIQ_BACKEND_LIBRARY_KEY", raising=False)
    key_a = current_library_key()

    # Seed Inbox tracks in Library A so Process All has work to do
    def _seed_inbox_track(root: Path, filename: str) -> int:
        inbox_file = root / "Inbox" / filename
        inbox_file.parent.mkdir(parents=True, exist_ok=True)
        inbox_file.write_bytes(b"fake-audio")
        with sqlite3.connect(root / "logs" / "processed.db") as conn:
            conn.execute(
                """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                        processed_at, pipeline_ver, storage_zone)
                       VALUES (?, ?, '', '', '', 'pending', '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
                (str(inbox_file), filename),
            )
            return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    _seed_inbox_track(root_a, "track1.mp3")
    _seed_inbox_track(root_a, "track2.mp3")

    # Use a fresh admission gate for this test
    gate = gate_module.OperationAdmissionGate()
    monkeypatch.setattr(gate_module, "operation_admission_gate", gate)
    monkeypatch.setattr(preparation_service, "operation_admission_gate", gate)

    # Initialize jobs DB
    monkeypatch.setattr(backend_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_db.init_db()

    # Track when the coroutine actually starts executing
    execution_started = asyncio.Event()
    # Capture the actual asyncio.Task created for the running Process All coroutine
    captured_task: list[asyncio.Task] = []
    real_create_task = asyncio.create_task
    original_run_process_all = preparation_service.run_process_all

    def capture_create_task(coro):
        task = real_create_task(coro)
        captured_task.append(task)
        return task

    monkeypatch.setattr(preparation_service.asyncio, "create_task", capture_create_task)

    async def tracking_run_process_all(operation_id, root, track_ids, *, durable_scope, on_started):
        if on_started:
            on_started()
        execution_started.set()
        await original_run_process_all(operation_id, root, track_ids, durable_scope=durable_scope, on_started=None)

    monkeypatch.setattr(preparation_service, "run_process_all", tracking_run_process_all)

    # --- Start Process All on Library A ---
    started = preparation_service.start_process_all(root_a, confirm=True)
    operation_id = started["operation_id"]

    # Wait until the coroutine has definitely begun execution
    await asyncio.wait_for(execution_started.wait(), timeout=2.0)

    # Verify parent operation exists and is RUNNING under Library A
    parent_a = preparation_service.preparation_operations_service.get_operation(operation_id)
    assert parent_a is not None, "Parent operation must exist"
    assert parent_a["status"] == "running", "Parent must be RUNNING before cancellation"

    # Verify scope is active
    assert gate.status()["active_operation_scopes"] == 1, "Scope must be active during execution"

    # --- Switch context to Library B ---
    root_b = tmp_path / "B"
    root_b.mkdir(parents=True)
    svc.configure_workspace(root_b)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root_b))
    monkeypatch.delenv("CRATEIQ_BACKEND_LIBRARY_KEY", raising=False)
    key_b = current_library_key()
    assert key_b != key_a, "Library keys must be distinct"

    # Verify Library B has no preparation operations yet
    ops_b = preparation_service.preparation_operations_service.list_recent()
    assert ops_b == [], "Library B must have no operations initially"

    # --- Cancel the running Process All task via explicit asyncio.Task.cancel() ---
    assert captured_task, "Task must have been captured"
    task = captured_task[0]
    task.cancel()

    # Await the task and assert asyncio.CancelledError is propagated
    try:
        await task
        raise AssertionError("Expected asyncio.CancelledError to be raised")
    except asyncio.CancelledError:
        pass  # Expected

    # --- Assertions ---
    # 1. A parent operation is terminal/cancelled (query with Library A's key)
    with backend_db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM preparation_operations WHERE id = ? AND library_key = ?",
            (operation_id, key_a)
        ).fetchone()
    assert row is not None, "Parent operation must still be retrievable under its originating library_key"
    assert row["status"] == "cancelled", f"Parent must be cancelled, got {row['status']}"

    # 2. A parent is NOT RUNNING
    assert row["status"] != "running", "Parent must not remain RUNNING"

    # 3. B has not been modified (no operations created in B)
    ops_b = preparation_service.preparation_operations_service.list_recent()
    assert ops_b == [], "Library B must remain untouched"

    # 4. Operation scope is released
    assert gate.status()["active_operation_scopes"] == 0, "Scope must be released after cancellation"

    # 5. begin_draining() can complete afterward
    await gate.begin_draining_async()
