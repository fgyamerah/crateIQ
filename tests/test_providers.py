"""
Targeted tests for Cycle 11's provider adapters (backend/app/services/providers/).

Covers: truthful capability detection (needs_setup/ready/unavailable),
response parsing against realistic mocked HTTP responses, and graceful
handling of 401/403/429 and network errors -- never raising for ordinary
provider failure modes. Deezer's real live-request test lives separately
(it needs no credentials) to keep this file network-free and fast.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from backend.app.services.providers import (
    acoustid_client, beatport_client, deezer_client, discogs_client,
    lastfm_client, spotify_client, youtube_client,
)


def _fake_response(status_code=200, json_data=None):
    return SimpleNamespace(status_code=status_code, json=lambda: json_data if json_data is not None else {})


# ---------------------------------------------------------------------------
# Capability detection -- every provider requiring credentials must report
# needs_setup with none configured, ready with them configured.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter,creds", [
    (lastfm_client, {"api_key": "x"}),
    (discogs_client, {"personal_access_token": "x"}),
    (spotify_client, {"client_id": "x", "client_secret": "y"}),
    (beatport_client, {"access_token": "x"}),
    (youtube_client, {"api_key": "x"}),
])
def test_needs_setup_without_credentials(adapter, creds):
    assert adapter.capability({}).status == "needs_setup"
    assert adapter.capability(creds).status == "ready"


def test_deezer_never_needs_setup():
    assert deezer_client.capability({}).status == "ready"


def test_acoustid_needs_setup_without_client_key():
    assert acoustid_client.capability({}).status == "needs_setup"


def test_acoustid_reports_unavailable_when_fpcalc_missing(monkeypatch):
    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: None)
    cap = acoustid_client.capability({"client_key": "x"})
    assert cap.status == "unavailable"


def test_acoustid_ready_when_key_and_fpcalc_present(monkeypatch):
    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: "/usr/bin/fpcalc")
    cap = acoustid_client.capability({"client_key": "x"})
    assert cap.status == "ready"


# ---------------------------------------------------------------------------
# search_track requires credentials before attempting any request
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adapter", [lastfm_client, discogs_client, spotify_client, beatport_client, youtube_client])
def test_search_without_credentials_makes_no_request(adapter, monkeypatch):
    called = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: called.append(1) or _fake_response())
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1) or _fake_response())
    result = adapter.search_track("Artist", "Title", credentials={})
    assert result.error
    assert not called


# ---------------------------------------------------------------------------
# Response parsing -- realistic mocked payloads
# ---------------------------------------------------------------------------

def test_lastfm_parses_search_results(monkeypatch):
    payload = {"results": {"trackmatches": {"track": [
        {"name": "Pick Up", "artist": "DJ Koze", "url": "https://last.fm/x", "listeners": "1000"},
    ]}}}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, payload))
    result = lastfm_client.search_track("DJ Koze", "Pick Up", credentials={"api_key": "k"})
    assert not result.error
    assert result.candidates[0].artist == "DJ Koze"
    assert result.candidates[0].title == "Pick Up"
    assert result.network_used


def test_lastfm_handles_rate_limit(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(429))
    result = lastfm_client.search_track("A", "B", credentials={"api_key": "k"})
    assert result.error and result.status_code == 429


def test_lastfm_handles_network_error(monkeypatch):
    def raise_conn_error(*a, **k):
        raise requests.RequestException("boom")
    monkeypatch.setattr(requests, "get", raise_conn_error)
    result = lastfm_client.search_track("A", "B", credentials={"api_key": "k"})
    assert result.error and "boom" in result.error


def test_discogs_parses_search_results(monkeypatch):
    payload = {"results": [{
        "title": "DJ Koze - Pick Up", "label": ["Pampa Records"], "genre": ["Electronic"],
        "style": ["Deep House"], "year": 2018, "catno": "PAMPA050", "id": 12345,
        "uri": "/release/12345-DJ-Koze-Pick-Up",
    }]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, payload))
    result = discogs_client.search_track("DJ Koze", "Pick Up", credentials={"personal_access_token": "t"})
    assert not result.error
    cand = result.candidates[0]
    assert cand.artist == "DJ Koze"
    assert cand.title == "Pick Up"
    assert cand.label == "Pampa Records"
    assert cand.style == "Deep House"
    assert cand.provider_url.startswith("https://www.discogs.com")


def test_discogs_handles_auth_rejection(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(401))
    result = discogs_client.search_track("A", "B", credentials={"personal_access_token": "bad"})
    assert result.error and result.status_code == 401


def test_deezer_parses_search_results(monkeypatch):
    payload = {"data": [{
        "title": "One More Time", "artist": {"name": "Daft Punk"}, "album": {"title": "Discovery"},
        "isrc": "GBDUW0000053", "duration": 320, "id": 3135553, "link": "https://deezer.com/track/3135553",
    }]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, payload))
    result = deezer_client.search_track("Daft Punk", "One More Time")
    assert not result.error
    assert result.candidates[0].isrc == "GBDUW0000053"


def test_deezer_handles_api_error_payload(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, {"error": {"message": "Quota limit exceeded"}}))
    result = deezer_client.search_track("A", "B")
    assert result.error == "Quota limit exceeded"


def test_spotify_search_uses_cached_token(monkeypatch):
    calls = {"token": 0, "search": 0}

    def fake_post(url, **kwargs):
        calls["token"] += 1
        return _fake_response(200, {"access_token": "tok123", "expires_in": 3600})

    def fake_get(url, **kwargs):
        calls["search"] += 1
        return _fake_response(200, {"tracks": {"items": [{
            "name": "Pick Up", "artists": [{"name": "DJ Koze"}], "album": {"name": "Knock Knock"},
            "external_ids": {"isrc": "DEXX01800001"}, "duration_ms": 240000, "id": "abc123",
            "external_urls": {"spotify": "https://open.spotify.com/track/abc123"},
        }]}})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    spotify_client._token_cache.clear()
    creds = {"client_id": "cid", "client_secret": "csecret"}

    result1 = spotify_client.search_track("DJ Koze", "Pick Up", credentials=creds)
    result2 = spotify_client.search_track("DJ Koze", "Pick Up", credentials=creds)

    assert not result1.error and not result2.error
    assert calls["token"] == 1, "second call must reuse the cached token, not re-authenticate"
    assert calls["search"] == 2
    assert result1.candidates[0].isrc == "DEXX01800001"


def test_spotify_handles_forbidden_dev_mode(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _fake_response(403))
    spotify_client._token_cache.clear()
    result = spotify_client.search_track("A", "B", credentials={"client_id": "x", "client_secret": "y"})
    assert result.error and "Premium" in result.error


def test_beatport_parses_search_results(monkeypatch):
    payload = {"results": [{
        "name": "Pick Up", "artists": [{"name": "DJ Koze"}], "mix_name": "Original Mix",
        "genre": {"name": "Deep House"}, "release": {"label": {"name": "Pampa Records"}},
        "length_ms": 240000, "id": 999, "url": "https://beatport.com/track/pick-up/999",
    }]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, payload))
    result = beatport_client.search_track("DJ Koze", "Pick Up", credentials={"access_token": "t"})
    assert not result.error
    cand = result.candidates[0]
    assert cand.genre == "Deep House"
    assert cand.label == "Pampa Records"
    assert cand.mix_version == "Original Mix"


def test_beatport_handles_token_rejection(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(403))
    result = beatport_client.search_track("A", "B", credentials={"access_token": "expired"})
    assert result.error and result.status_code == 403


def test_youtube_parses_search_results(monkeypatch):
    payload = {"items": [{
        "id": {"videoId": "xyz789"},
        "snippet": {"title": "DJ Koze - Pick Up (Official)", "channelTitle": "Pampa Records"},
    }]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, payload))
    result = youtube_client.search_track("DJ Koze", "Pick Up", credentials={"api_key": "k"})
    assert not result.error
    assert result.candidates[0].provider_url == "https://www.youtube.com/watch?v=xyz789"
    assert result.candidates[0].raw_confidence == "low_authority_corroboration_only"


def test_youtube_handles_quota_exceeded(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(403))
    result = youtube_client.search_track("A", "B", credentials={"api_key": "k"})
    assert result.error and result.status_code == 403


# ---------------------------------------------------------------------------
# AcoustID -- local fingerprinting is exercised for real (fpcalc is a real
# local binary, no network/account needed); lookup() is mocked.
# ---------------------------------------------------------------------------

def test_acoustid_fingerprint_file_real_local_run(tmp_path):
    pytest.importorskip("subprocess")
    import shutil
    if shutil.which("fpcalc") is None:
        pytest.skip("fpcalc not installed in this environment")
    import subprocess as sp
    audio = tmp_path / "test.wav"
    # Generate a minimal real audio file locally via ffmpeg if available;
    # otherwise skip -- this test only proves real local fingerprinting,
    # it must never fabricate a result.
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed in this environment")
    # Chromaprint needs a minimum window of real audio content to produce a
    # non-empty fingerprint -- empirically ~3s for a plain sine tone; 2s
    # reproducibly yields "ERROR: Empty fingerprint" from fpcalc itself.
    sp.run([ffmpeg, "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-y", str(audio)],
           capture_output=True, timeout=20, check=True)
    fingerprint, duration, error = acoustid_client.fingerprint_file(audio)
    assert error is None
    assert fingerprint
    assert duration and duration > 0


def test_acoustid_fingerprint_missing_binary_reports_error(monkeypatch, tmp_path):
    monkeypatch.setattr(acoustid_client, "_resolve_fpcalc_binary", lambda: None)
    fingerprint, duration, error = acoustid_client.fingerprint_file(tmp_path / "x.mp3")
    assert fingerprint is None
    assert error and "fpcalc" in error


def test_acoustid_lookup_parses_recordings(monkeypatch):
    payload = {"status": "ok", "results": [{
        "score": 0.95,
        "recordings": [{"id": "mbid-123", "title": "Pick Up", "artists": [{"name": "DJ Koze"}]}],
    }]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, payload))
    result = acoustid_client.lookup("fake-fp", 240.0, credentials={"client_key": "k"})
    assert not result.error
    assert result.candidates[0].provider_id == "mbid-123"
    assert result.candidates[0].artist == "DJ Koze"


def test_acoustid_lookup_handles_error_status(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _fake_response(200, {"status": "error", "error": {"message": "invalid client key"}}))
    result = acoustid_client.lookup("fp", 100.0, credentials={"client_key": "bad"})
    assert result.error == "invalid client key"
