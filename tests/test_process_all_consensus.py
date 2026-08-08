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

import sqlite3
from pathlib import Path

import pytest

from backend.app.services import (
    enrichment_review_service,
    needs_review_service,
    preparation_service,
    provider_routing_service as routing,
    settings_service,
    workspace_service as svc,
)
from backend.app.services.providers.base import ProviderCandidate

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
