"""Focused contracts for server-owned per-batch enrichment source selection."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.api.routes.workspace import EnrichSelectedRequest
from backend.app.services import settings_service
from backend.app.services.providers.base import ProviderCapability


def _source(
    source_id: str,
    *,
    role: str = "track_enrichment",
    enabled: bool = True,
    configured: bool = True,
    connection_status: str = "ready",
    selectable: bool = True,
    priority: int = 10,
) -> dict:
    return {
        "id": source_id,
        "role": role,
        "enabled": enabled,
        "configured": configured,
        "connection_status": connection_status,
        "selectable_for_enrichment": selectable,
        "priority": priority,
    }


def test_omitted_source_ids_resolve_to_enabled_ready_routable_defaults(monkeypatch):
    monkeypatch.setattr(settings_service, "get_metadata_sources", lambda: {"sources": [
        _source("deezer", priority=80),
        _source("discogs", priority=50),
        _source("musicbrainz", enabled=False, selectable=False),
    ]})

    assert settings_service.validate_enrichment_source_ids() == ["discogs", "deezer"]


@pytest.mark.parametrize(
    ("source_id", "message"),
    [
        ("unknown", "Unknown metadata source"),
        ("mixed_in_key", "not a track-enrichment source"),
        ("local_tags", "not a track-enrichment source"),
        ("discogs", "disabled in Settings"),
        ("spotify", "not ready"),
    ],
)
def test_explicit_source_ids_reject_unknown_ineligible_and_non_enrichment_sources(monkeypatch, source_id, message):
    monkeypatch.setattr(settings_service, "get_metadata_sources", lambda: {"sources": [
        _source("mixed_in_key", role="analysis_only", selectable=False),
        _source("local_tags", role="local_input", selectable=False),
        _source("discogs", enabled=False, selectable=False),
        _source("spotify", configured=False, connection_status="needs_setup", selectable=False),
    ]})

    with pytest.raises(ValueError, match=message):
        settings_service.validate_enrichment_source_ids([source_id])


def test_empty_explicit_selection_and_duplicate_ids_are_rejected(monkeypatch):
    monkeypatch.setattr(settings_service, "get_metadata_sources", lambda: {"sources": [_source("deezer")]})

    with pytest.raises(ValueError, match="at least one"):
        settings_service.validate_enrichment_source_ids([])
    with pytest.raises(ValueError, match="duplicates"):
        settings_service.validate_enrichment_source_ids(["deezer", "deezer"])


def test_source_roles_are_server_owned_and_musicbrainz_is_not_cli_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")
    monkeypatch.setattr(settings_service, "_musicbrainz_python_api_available", lambda: False)

    sources = {source["id"]: source for source in settings_service.get_metadata_sources()["sources"]}
    assert sources["local_tags"]["role"] == "local_input"
    assert sources["filename_hints"]["role"] == "local_input"
    assert sources["mixed_in_key"]["role"] == "analysis_only"
    assert sources["musicbrainz"]["role"] == "track_enrichment"
    assert sources["musicbrainz"]["connection_status"] == "unavailable"
    assert sources["musicbrainz"]["selectable_for_enrichment"] is False


def test_api_source_contract_marks_ready_beets_and_discogs_selectable(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_service, "METADATA_SOURCES_PATH", tmp_path / "metadata_sources.json")
    monkeypatch.setattr(settings_service, "_musicbrainz_python_api_available", lambda: True)
    monkeypatch.setattr(settings_service.discogs_client, "capability", lambda credentials: ProviderCapability(status="ready", message="ok"))
    settings_service.update_metadata_sources([{
        "id": "discogs",
        "enabled": True,
        "credentials": {"personal_access_token": "test-token"},
    }])

    sources = {source["id"]: source for source in settings_service.get_metadata_sources()["sources"]}
    assert len(sources) == 12
    assert sources["beets"]["label"] == "Beets"
    assert sources["beets"]["role"] == "track_enrichment"
    assert sources["beets"]["selectable_for_enrichment"] is True
    assert sources["discogs"]["selectable_for_enrichment"] is True
    assert all("test-token" not in str(source) for source in sources.values())


def test_enrich_request_preserves_the_200_track_limit_and_has_optional_sources():
    request = EnrichSelectedRequest(track_ids=[1], source_ids=["deezer"])
    assert request.source_ids == ["deezer"]
    assert EnrichSelectedRequest(track_ids=list(range(200))).track_ids[-1] == 199
    with pytest.raises(ValidationError):
        EnrichSelectedRequest(track_ids=list(range(201)))
