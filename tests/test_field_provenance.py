"""
Tests for Metadata Model Phase 2: field-level provenance
(field_provenance_service) and optional local fingerprint identity
(track_identity_service), plus their wiring into the two authoritative
write paths: enrichment_review_service.apply_selected (HIGH-confidence
Process All auto-apply and explicit enrichment-review apply) and
workspace_service manual edit paths (single/bulk Inbox edit).

Section A: field_provenance_service unit tests -- bare sqlite tracks table,
           no ffmpeg required.
Section B: track_identity_service unit tests -- fpcalc fully mocked, no
           real Chromaprint binary or network required.
Section C: enrichment_review_service.apply_selected wiring -- fake provider
           evidence like test_process_all_consensus.py, no ffmpeg required
           (enrich_tracks never calls the real tag writer).
Section D: workspace_service manual edit wiring -- real tag writes, so
           ffmpeg-gated like test_workspace_inbox_edit.py.
Section E: read-only GET /api/tracks/{id} must never create schema.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.app.services import (
    enrichment_review_service,
    field_provenance_service,
    preparation_service,
    provider_routing_service as routing,
    settings_service,
    track_identity_service,
    workspace_service as svc,
)
from backend.app.services.providers.base import ProviderCandidate


# ---------------------------------------------------------------------------
# Section A: field_provenance_service -- bare sqlite, no ffmpeg
# ---------------------------------------------------------------------------

def _create_tracks_db(root: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            genre TEXT,
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )
    conn.execute(
        "INSERT INTO tracks (filepath, filename, artist, title, genre, bpm, key_musical, key_camelot) "
        "VALUES ('/x/song.mp3', 'song.mp3', 'Original Artist', 'Original Title', 'House', 128.0, 'Am', '8A')"
    )
    conn.commit()
    conn.close()
    return db_path


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _track_row(db_path: Path) -> sqlite3.Row:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM tracks WHERE id = 1").fetchone()


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = _create_tracks_db(root)
    return root, db_path


def test_migration_is_additive_and_idempotent(db_env):
    root, db_path = db_env
    field_provenance_service.record(1, "artist", "New Artist", origin="user", source="manual_edit")
    assert "field_provenance" in _tables(db_path)
    # Calling again (e.g. a second write) must not error or recreate anything destructively.
    field_provenance_service.record(1, "artist", "New Artist", origin="user", source="manual_edit")
    assert "field_provenance" in _tables(db_path)


def test_migration_never_changes_existing_track_field_values(db_env):
    root, db_path = db_env
    before = dict(_track_row(db_path))
    field_provenance_service.record(1, "artist", "Some Other Value", origin="user", source="manual_edit")
    after = dict(_track_row(db_path))
    # field_provenance_service only ever writes its own table; it must never
    # touch tracks.artist/title/genre/bpm/key itself.
    assert after == before


def test_repeated_identical_record_does_not_duplicate_rows(db_env):
    root, db_path = db_env
    first = field_provenance_service.record(
        1, "artist", "DJ Koze", origin="provider", source="consensus_apply", confidence="HIGH",
    )
    second = field_provenance_service.record(
        1, "artist", "DJ Koze", origin="provider", source="consensus_apply", confidence="HIGH",
    )
    rows = field_provenance_service.list_for_track(1)
    assert len(rows) == 1
    assert rows[0]["is_current"] is True
    assert first["id"] == second["id"] == rows[0]["id"]
    assert second["recorded_at"] == first["recorded_at"]  # original observation time preserved
    assert second["updated_at"] >= first["updated_at"]


def test_changed_value_creates_history_and_new_current_row(db_env):
    root, db_path = db_env
    field_provenance_service.record(1, "artist", "DJ Koze", origin="provider", source="consensus_apply", confidence="HIGH")
    field_provenance_service.record(1, "artist", "Black Coffee", origin="user", source="manual_edit")

    rows = field_provenance_service.list_for_track(1)
    assert len(rows) == 2
    current = [r for r in rows if r["is_current"]]
    history = [r for r in rows if not r["is_current"]]
    assert len(current) == 1 and len(history) == 1
    assert current[0]["value"] == "Black Coffee"
    assert current[0]["origin"] == "user"
    assert history[0]["value"] == "DJ Koze"
    assert history[0]["origin"] == "provider"

    only_current = field_provenance_service.list_for_track(1, include_history=False)
    assert len(only_current) == 1
    assert only_current[0]["value"] == "Black Coffee"


def test_user_origin_cannot_carry_a_confidence_verdict(db_env):
    with pytest.raises(ValueError, match="provider-origin"):
        field_provenance_service.record(1, "artist", "Manual Value", origin="user", source="manual_edit", confidence="HIGH")


def test_system_origin_cannot_carry_a_confidence_verdict(db_env):
    with pytest.raises(ValueError, match="provider-origin"):
        field_provenance_service.record(1, "artist", "Baseline", origin="system", source="existing_local_value", confidence="MEDIUM")


def test_provider_origin_rejects_invalid_confidence_value(db_env):
    with pytest.raises(ValueError, match="confidence"):
        field_provenance_service.record(1, "artist", "X", origin="provider", source="consensus_apply", confidence="SUPER_SURE")


def test_invalid_origin_rejected(db_env):
    with pytest.raises(ValueError, match="origin"):
        field_provenance_service.record(1, "artist", "X", origin="robot", source="whatever")


def test_evidence_and_reason_are_bounded(db_env):
    long_reason = "x" * 5000
    long_evidence_value = "y" * 5000
    many_items = [f"provider-{i}: value" for i in range(100)]
    row = field_provenance_service.record(
        1, "artist", "DJ Koze", origin="provider", source="consensus_apply", confidence="HIGH",
        reason=long_reason,
        evidence={"note": long_evidence_value, "providers": many_items},
    )
    assert len(row["reason"]) <= field_provenance_service._REASON_MAX_LEN
    assert len(row["evidence"]["note"]) <= field_provenance_service._EVIDENCE_VALUE_MAX_LEN
    assert len(row["evidence"]["providers"]) <= field_provenance_service._EVIDENCE_LIST_MAX_ITEMS


def test_read_paths_never_create_the_table_when_absent(db_env):
    root, db_path = db_env
    assert field_provenance_service.list_for_track(1) == []
    assert field_provenance_service.current_for_track(1) == {}
    assert "field_provenance" not in _tables(db_path)


def test_no_network_import_in_field_provenance_module():
    source = Path(field_provenance_service.__file__).read_text(encoding="utf-8")
    for token in ("import requests", "import socket", "urllib.request"):
        assert token not in source


# ---------------------------------------------------------------------------
# Section B: track_identity_service -- fpcalc mocked, no real binary/network
# ---------------------------------------------------------------------------

@pytest.fixture()
def identity_env(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    db_path = _create_tracks_db(root)
    audio_path = root / "Inbox" / "song.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"disposable-fixture-bytes-not-real-audio")
    return root, db_path, audio_path


def test_fpcalc_available_reflects_binary_resolution(identity_env, monkeypatch):
    from backend.app.services.providers import acoustid_client

    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: "/usr/bin/fpcalc")
    assert track_identity_service.fpcalc_available() is True
    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: None)
    assert track_identity_service.fpcalc_available() is False


def test_compute_and_store_reports_truthful_unavailable_state_no_schema_write(identity_env, monkeypatch):
    root, db_path, audio_path = identity_env
    from backend.app.services.providers import acoustid_client

    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: None)
    result = track_identity_service.compute_and_store(audio_path, 1)

    assert result["status"] == "unavailable"
    assert "track_fingerprints" not in _tables(db_path)


def test_compute_and_store_success_caches_bounded_fingerprint_idempotently(identity_env, monkeypatch):
    root, db_path, audio_path = identity_env
    from backend.app.services.providers import acoustid_client

    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: "/usr/bin/fpcalc")
    monkeypatch.setattr(acoustid_client, "fingerprint_file", lambda p: ("FAKE_FINGERPRINT_DATA", 183.2, None))

    first = track_identity_service.compute_and_store(audio_path, 1)
    assert first["status"] == "ok"
    assert first["algorithm"] == "chromaprint"
    assert first["fingerprint"] == "FAKE_FINGERPRINT_DATA"

    second = track_identity_service.compute_and_store(audio_path, 1)
    assert second["status"] == "ok"

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM track_fingerprints WHERE track_id = 1").fetchone()
    assert rows[0] == 1  # upsert, never a duplicate row

    stored = track_identity_service.get_for_track(1)
    assert stored["fingerprint"] == "FAKE_FINGERPRINT_DATA"


def test_compute_and_store_bounds_an_oversized_fingerprint(identity_env, monkeypatch):
    root, db_path, audio_path = identity_env
    from backend.app.services.providers import acoustid_client

    huge = "F" * 10_000
    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: "/usr/bin/fpcalc")
    monkeypatch.setattr(acoustid_client, "fingerprint_file", lambda p: (huge, 100.0, None))

    result = track_identity_service.compute_and_store(audio_path, 1)
    assert len(result["fingerprint"]) <= track_identity_service._FINGERPRINT_MAX_LEN


def test_compute_and_store_error_from_fpcalc_yields_error_status_no_row(identity_env, monkeypatch):
    root, db_path, audio_path = identity_env
    from backend.app.services.providers import acoustid_client

    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: "/usr/bin/fpcalc")
    monkeypatch.setattr(acoustid_client, "fingerprint_file", lambda p: (None, None, "fpcalc exited 1"))

    result = track_identity_service.compute_and_store(audio_path, 1)
    assert result["status"] == "error"
    assert "track_fingerprints" not in _tables(db_path)


def test_compute_and_store_never_touches_network(identity_env, monkeypatch):
    root, db_path, audio_path = identity_env
    from backend.app.services.providers import acoustid_client

    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: "/usr/bin/fpcalc")
    monkeypatch.setattr(acoustid_client, "fingerprint_file", lambda p: ("FP", 10.0, None))

    class _ForbiddenRequests:
        def get(self, *a, **k):
            raise AssertionError("compute_and_store must never make a network call")

    monkeypatch.setattr(acoustid_client, "requests", _ForbiddenRequests())
    track_identity_service.compute_and_store(audio_path, 1)  # must not raise


def test_get_for_track_never_creates_table_when_absent(identity_env):
    root, db_path, audio_path = identity_env
    assert track_identity_service.get_for_track(1) is None
    assert "track_fingerprints" not in _tables(db_path)


# ---------------------------------------------------------------------------
# Section C: enrichment_review_service.apply_selected wiring (no ffmpeg --
# enrich_tracks never invokes the real tag writer).
# ---------------------------------------------------------------------------

_HIGH_ARTIST_TITLE = {
    "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up")],
    "musicbrainz": [ProviderCandidate(provider="musicbrainz", artist="DJ Koze", title="Pick Up")],
}


def _write_fake_audio(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_inbox_track(root: Path, *, artist="", title="", genre="", filename="song.mp3") -> int:
    inbox_file = root / "Inbox" / filename
    _write_fake_audio(inbox_file)
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
    import backend.app.core.db as backend_core_db

    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_core_db.init_db()
    return root


def test_high_auto_apply_records_provider_high_confidence_provenance(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="a.mp3")
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: _HIGH_ARTIST_TITLE)

    preparation_service.enrich_tracks(managed_root, [track_id])

    current = field_provenance_service.current_for_track(track_id)
    assert current["artist"]["origin"] == "provider"
    assert current["artist"]["source"] == "consensus_apply"
    assert current["artist"]["confidence"] == "HIGH"
    assert current["artist"]["value"] == "DJ Koze"
    assert current["title"]["confidence"] == "HIGH"


def test_medium_never_auto_applies_so_no_provenance_recorded_yet(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, artist="DJ Koze", title="Pick Up", filename="b.mp3")
    evidence = {
        "beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title="Pick Up", raw_confidence="high")],
        "beatport": [ProviderCandidate(provider="beatport", genre="Deep House")],
    }
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    preparation_service.enrich_tracks(managed_root, [track_id])

    # Nothing auto-applied (MEDIUM genre only) -- no provenance row should
    # exist yet for a field that was never actually written.
    assert field_provenance_service.current_for_track(track_id) == {}


def test_explicit_review_apply_of_a_medium_suggestion_records_provider_not_user(managed_root, monkeypatch):
    track_id = _seed_inbox_track(managed_root, filename="c.mp3")
    evidence = {"beets": [ProviderCandidate(provider="beets", artist="DJ Koze", title=None)]}
    monkeypatch.setattr(routing, "gather_evidence", lambda tid, **k: evidence)

    preparation_service.enrich_tracks(managed_root, [track_id])
    review = enrichment_review_service.get_review()
    pending = next(i for i in review["items"] if i["track_id"] == track_id and i["source_id"] == "consensus_review")

    enrichment_review_service.update_suggestion(track_id, pending["suggestion_id"], "applied", "", pending["suggested_fields"])
    result = enrichment_review_service.apply_selected(
        [{"track_id": track_id, "suggestion_id": pending["suggestion_id"], "fields": pending["suggested_fields"]}],
        confirm=True,
    )
    assert result["applied"] == 1

    current = field_provenance_service.current_for_track(track_id)
    assert current["artist"]["origin"] == "provider"  # a human confirmed it, but the value itself is provider evidence
    assert current["artist"]["origin"] != "user"
    assert current["artist"]["confidence"] in ("LOW", "MEDIUM")  # never elevated to HIGH by the act of manual apply
    assert current["artist"]["source"] == "consensus_review"


# ---------------------------------------------------------------------------
# Section D: workspace_service manual edit wiring -- real tag writes, so
# ffmpeg-gated like tests/test_workspace_inbox_edit.py.
# ---------------------------------------------------------------------------

pytestmark_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available")


def _make_real_audio(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", "1", "-c:a", "libmp3lame", "-b:a", "32k", str(path)],
        check=True,
    )


def _seed_real_inbox_track(root: Path, filename: str, **fields) -> int:
    path = root / "Inbox" / filename
    _make_real_audio(path)
    defaults = {"artist": "Old Artist", "title": "Some Title", "genre": "Dance"}
    defaults.update(fields)
    with sqlite3.connect(root / "logs" / "processed.db") as conn:
        conn.execute(
            """INSERT INTO tracks (filepath, filename, artist, title, genre, status,
                                    processed_at, pipeline_ver, storage_zone)
               VALUES (?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z', 'test', 'INBOX')""",
            (str(path), filename, defaults["artist"], defaults["title"], defaults["genre"]),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytestmark_ffmpeg
def test_manual_single_edit_records_user_provenance_with_no_confidence(managed_root):
    track_id = _seed_real_inbox_track(managed_root, "song.mp3", artist="Old Artist")
    svc.edit_inbox_track_metadata(managed_root, track_id, artist="Black Coffee")

    current = field_provenance_service.current_for_track(track_id)
    assert current["artist"]["origin"] == "user"
    assert current["artist"]["source"] == "manual_edit"
    assert current["artist"]["confidence"] is None
    assert current["artist"]["value"] == "Black Coffee"


@pytestmark_ffmpeg
def test_manual_bulk_edit_records_user_provenance_per_track(managed_root):
    ids = [_seed_real_inbox_track(managed_root, f"t{i}.mp3", genre="Dance") for i in range(2)]
    svc.bulk_edit_apply(managed_root, ids, artist=None, genre="Afro House", confirm=True)

    for track_id in ids:
        current = field_provenance_service.current_for_track(track_id)
        assert current["genre"]["origin"] == "user"
        assert current["genre"]["source"] == "bulk_edit"
        assert current["genre"]["confidence"] is None
        assert current["genre"]["value"] == "Afro House"


@pytestmark_ffmpeg
def test_manual_edit_no_change_records_no_new_provenance(managed_root):
    track_id = _seed_real_inbox_track(managed_root, "song.mp3", artist="Same Artist")
    svc.edit_inbox_track_metadata(managed_root, track_id, artist="Same Artist")
    assert field_provenance_service.current_for_track(track_id) == {}


# ---------------------------------------------------------------------------
# Section E: plain read-only GET must never create schema.
# ---------------------------------------------------------------------------

def test_get_track_detail_route_does_not_create_provenance_or_fingerprint_tables(tmp_path, monkeypatch):
    import backend.app.core.db as backend_core_db
    import backend.app.main as backend_main
    from fastapi.testclient import TestClient

    root = tmp_path / "managed"
    svc.configure_workspace(root)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    backend_core_db.init_db()
    track_id = _seed_inbox_track(root, filename="song.mp3")
    db_path = root / "logs" / "processed.db"

    with TestClient(backend_main.app) as client:
        resp = client.get(f"/api/tracks/{track_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["identity"]["fingerprint_available"] is False
    assert body["provenance"] == {}
    assert "field_provenance" not in _tables(db_path)
    assert "track_fingerprints" not in _tables(db_path)


def test_edit_inbox_track_metadata_has_no_bpm_key_parameters():
    """Cheap regression guard: the manual-edit surface can never be used to
    write BPM/key -- Mixed In Key protections stay entirely out of reach of
    this feature's provenance wiring."""
    import inspect

    params = set(inspect.signature(svc.edit_inbox_track_metadata).parameters)
    assert "bpm" not in params and "key" not in params and "key_musical" not in params and "key_camelot" not in params
