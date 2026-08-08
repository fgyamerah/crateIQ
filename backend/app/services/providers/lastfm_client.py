"""
Last.fm provider adapter (Cycle 11).

Official API only: https://ws.audioscrobbler.com/2.0/ with a free,
self-serve API key (last.fm/api/account/create). Free tier is for
non-commercial use only, per Last.fm's API ToS -- CrateIQ is local-first,
non-commercial software, so this is within terms for the intended use.

Useful for: track/artist correction (track.search) and community tag
evidence (track.getTopTags) mapped through the existing Genre Taxonomy,
never treated as an unquestionable canonical genre. Community tags are
free-text and often noisy; only used as corroborating evidence.
"""
from __future__ import annotations

import logging

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, missing_credentials, timeout

log = logging.getLogger(__name__)

_BASE_URL = "https://ws.audioscrobbler.com/2.0/"
_REQUIRED = ("api_key",)


def capability(credentials: dict[str, str]) -> ProviderCapability:
    missing = missing_credentials(_REQUIRED, credentials)
    if missing:
        return ProviderCapability(
            status="needs_setup",
            message="Add a free Last.fm API key (last.fm/api/account/create) in Settings to enable this source.",
            required_credentials=_REQUIRED,
        )
    return ProviderCapability(status="ready", message="API key configured.", required_credentials=_REQUIRED)


def search_track(artist: str | None, title: str | None, *, credentials: dict[str, str]) -> ProviderResult:
    if not title:
        return ProviderResult(error="Title is required for a Last.fm search.")
    cap = capability(credentials)
    if cap.status != "ready":
        return ProviderResult(error=cap.message)

    params = {
        "method": "track.search",
        "track": title,
        "api_key": credentials["api_key"],
        "format": "json",
        "limit": 5,
    }
    if artist:
        params["artist"] = artist
    try:
        resp = requests.get(_BASE_URL, params=params, timeout=timeout())
    except requests.RequestException as exc:
        return ProviderResult(error=f"Last.fm request failed: {exc}", network_used=True)

    if resp.status_code == 429:
        return ProviderResult(error="Last.fm rate limit exceeded.", status_code=429, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"Last.fm returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        payload = resp.json()
        tracks = payload.get("results", {}).get("trackmatches", {}).get("track", [])
    except (ValueError, AttributeError):
        return ProviderResult(error="Last.fm returned an unexpected response shape.", network_used=True)

    if isinstance(tracks, dict):
        tracks = [tracks]

    candidates = [
        ProviderCandidate(
            provider="lastfm",
            artist=str(item.get("artist") or "").strip() or None,
            title=str(item.get("name") or "").strip() or None,
            provider_url=item.get("url"),
            provider_id=item.get("mbid") or None,
            raw_confidence=f"listeners={item.get('listeners')}" if item.get("listeners") else None,
        )
        for item in tracks
        if isinstance(item, dict)
    ]
    return ProviderResult(candidates=candidates, network_used=True)


def top_tags(artist: str, title: str, *, credentials: dict[str, str]) -> ProviderResult:
    """Community tag evidence for genre corroboration. Never authoritative."""
    cap = capability(credentials)
    if cap.status != "ready":
        return ProviderResult(error=cap.message)

    params = {
        "method": "track.getTopTags", "artist": artist, "track": title,
        "api_key": credentials["api_key"], "format": "json",
    }
    try:
        resp = requests.get(_BASE_URL, params=params, timeout=timeout())
    except requests.RequestException as exc:
        return ProviderResult(error=f"Last.fm request failed: {exc}", network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"Last.fm returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        payload = resp.json()
        tags = payload.get("toptags", {}).get("tag", [])
    except (ValueError, AttributeError):
        return ProviderResult(error="Last.fm returned an unexpected response shape.", network_used=True)
    if isinstance(tags, dict):
        tags = [tags]

    tag_names = [str(t.get("name")) for t in tags if isinstance(t, dict) and t.get("name")][:10]
    candidate = ProviderCandidate(provider="lastfm", artist=artist, title=title, tags=tag_names)
    return ProviderResult(candidates=[candidate] if tag_names else [], network_used=True)
