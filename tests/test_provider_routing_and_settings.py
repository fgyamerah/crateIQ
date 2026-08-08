"""
Targeted tests for Cycle 11's provider_routing_service (staged evidence
gathering, early stop on HIGH confidence, skip unconfigured providers)
and its settings_service integration (registry entries, truthful
capability reporting, credentials never leaking into an API response).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from backend.app.services import provider_routing_service as routing
from backend.app.services import settings_service
from backend.app.services.providers.base import ProviderCapability, ProviderResult, ProviderCandidate


def _fake_response(status_code=200, json_data=None):
    from types import SimpleNamespace
    return SimpleNamespace(status_code=status_code, json=lambda: json_data if json_data is not None else {})


# ---------------------------------------------------------------------------
# gather_evidence: stops early once HIGH identity confidence is reached
# ---------------------------------------------------------------------------

def test_gather_evidence_stops_after_strong_beets_mb_agreement(monkeypatch):
    def fake_online_lookup(track_id, source):
        return {"items": [{
            "track_id": track_id, "source_id": source,
            "suggested_fields": {"artist": "DJ Koze", "title": "Pick Up"},
            "confidence": "high",
        }]}
    monkeypatch.setattr(routing.enrichment_review_service, "online_lookup", fake_online_lookup)

    called_text_search = []
    monkeypatch.setattr(
        routing.discogs_client, "capability",
        lambda creds: ProviderCapability(status="ready", message="ok"),
    )
    monkeypatch.setattr(
        routing.discogs_client, "search_track",
        lambda *a, **k: called_text_search.append(1) or ProviderResult(),
    )

    evidence = routing.gather_evidence(
        1, artist="DJ Koze", title="Pick Up", inbox_copy_path=None,
        credentials_by_source={"discogs": {"personal_access_token": "x"}},
    )
    assert "beets" in evidence and "musicbrainz" in evidence
    assert not called_text_search, "must stop before querying Discogs once beets+MB already agree with HIGH confidence"


def test_gather_evidence_skips_unconfigured_providers(monkeypatch):
    monkeypatch.setattr(routing.enrichment_review_service, "online_lookup", lambda tid, src: {"items": []})

    called = []
    monkeypatch.setattr(routing.lastfm_client, "search_track", lambda *a, **k: called.append(1) or ProviderResult())
    # capability() with no credentials genuinely returns needs_setup -- no
    # monkeypatch needed, this proves the real function gates correctly.

    evidence = routing.gather_evidence(
        1, artist="X", title="Y", inbox_copy_path=None, credentials_by_source={},
    )
    assert not called
    assert "lastfm" not in evidence


def test_gather_evidence_tries_acoustid_before_text_search_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(routing.enrichment_review_service, "online_lookup", lambda tid, src: {"items": []})
    audio_path = tmp_path / "song.mp3"
    audio_path.write_bytes(b"fake")

    monkeypatch.setattr(routing.acoustid_client, "capability", lambda creds: ProviderCapability(status="ready", message="ok"))
    monkeypatch.setattr(routing.acoustid_client, "fingerprint_file", lambda path: ("fp123", 240.0, None))
    monkeypatch.setattr(
        routing.acoustid_client, "lookup",
        lambda fp, dur, credentials: ProviderResult(candidates=[
            ProviderCandidate(provider="acoustid", provider_id="mbid-1", artist="X", title="Y"),
        ]),
    )

    evidence = routing.gather_evidence(
        1, artist="X", title="Y", inbox_copy_path=audio_path,
        credentials_by_source={"acoustid": {"client_key": "k"}},
    )
    assert "acoustid" in evidence
    assert evidence["acoustid"][0].provider_id == "mbid-1"


# ---------------------------------------------------------------------------
# settings_service integration
# ---------------------------------------------------------------------------

def test_all_seven_new_providers_registered():
    sources = {s["id"] for s in settings_service.get_metadata_sources()["sources"]}
    for expected in ("acoustid", "discogs", "beatport", "spotify", "deezer", "lastfm", "youtube"):
        assert expected in sources


def test_deezer_reports_ready_without_any_credentials():
    sources = {s["id"]: s for s in settings_service.get_metadata_sources()["sources"]}
    assert sources["deezer"]["connection_status"] == "ready"
    assert sources["deezer"]["requires_credentials"] is False


def test_credential_requiring_providers_report_needs_setup_by_default():
    sources = {s["id"]: s for s in settings_service.get_metadata_sources()["sources"]}
    for source_id in ("acoustid", "discogs", "spotify", "beatport", "lastfm", "youtube"):
        assert sources[source_id]["connection_status"] == "needs_setup", source_id


def test_get_metadata_sources_never_exposes_saved_credential_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")
    settings_service.update_metadata_sources([
        {"id": "discogs", "credentials": {"personal_access_token": "super-secret-token-value"}}
    ])
    payload = settings_service.get_metadata_sources()
    serialized = str(payload)
    assert "super-secret-token-value" not in serialized
    discogs = next(s for s in payload["sources"] if s["id"] == "discogs")
    assert discogs["credentials_status"] == "saved"
    assert discogs["connection_status"] == "ready"


def test_test_metadata_source_reports_needs_setup_without_making_a_request(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")
    called = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: called.append(1) or _fake_response())
    result = settings_service.test_metadata_source("spotify")
    assert result["connection_status"] == "needs_setup"
    assert not called


def test_test_metadata_source_deezer_makes_a_real_bounded_request(monkeypatch):
    """Proves the dispatch wiring reaches a real request for a genuinely available provider."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _fake_response(200, {"data": [{"title": "One More Time", "artist": {"name": "Daft Punk"}}]})

    monkeypatch.setattr(requests, "get", fake_get)
    result = settings_service.test_metadata_source("deezer")
    assert result["connection_status"] == "ready"
    assert result["network_used"] is True
    assert len(calls) == 1


def test_test_metadata_source_acoustid_reports_capability_without_generic_search(monkeypatch):
    called = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: called.append(1) or _fake_response())
    result = settings_service.test_metadata_source("acoustid")
    assert result["connection_status"] == "needs_setup"
    assert not called


def test_beatport_credential_field_is_access_token_not_client_secret():
    definition = settings_service._METADATA_SOURCE_BY_ID["beatport"]
    assert definition["credential_fields"] == ("access_token",)
    assert definition["requires_credentials"] is True
