"""
Deezer provider adapter (Cycle 11).

Verified live during this cycle's research (a real bounded GET against
https://api.deezer.com/search succeeded, see PROJECT_CONTEXT.md): Deezer's
public search endpoint requires no authentication at all for basic track
search -- unlike OAuth-gated user actions (playlists, favorites), which do
require an app_id/app_secret that, as of this writing, Deezer for
Developers is not issuing to new applications. CrateIQ only needs
read-only search, so this source is usable out of the box with no
credential setup, subject to developers.deezer.com/termsofuse (reasonable,
non-commercial use).

Useful for: alternate track/artist/album matching and ISRC corroboration,
similar evidence class to Spotify.
"""
from __future__ import annotations

import logging

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, timeout

log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.deezer.com/search"


def capability(credentials: dict[str, str]) -> ProviderCapability:  # noqa: ARG001 - no credentials needed
    return ProviderCapability(status="ready", message="Deezer's public search endpoint requires no credentials.")


def search_track(artist: str | None, title: str | None, *, credentials: dict[str, str] | None = None) -> ProviderResult:
    if not title:
        return ProviderResult(error="Title is required for a Deezer search.")

    query = f"{artist} {title}".strip() if artist else title
    try:
        resp = requests.get(_SEARCH_URL, params={"q": query, "limit": 5}, timeout=timeout())
    except requests.RequestException as exc:
        return ProviderResult(error=f"Deezer request failed: {exc}", network_used=True)

    if resp.status_code == 429:
        return ProviderResult(error="Deezer rate limit exceeded.", status_code=429, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"Deezer returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        payload = resp.json()
    except ValueError:
        return ProviderResult(error="Deezer returned an unexpected response shape.", network_used=True)

    if isinstance(payload, dict) and "error" in payload:
        return ProviderResult(error=str(payload["error"].get("message", "Deezer API error.")), network_used=True)

    items = payload.get("data", []) if isinstance(payload, dict) else []
    candidates = [
        ProviderCandidate(
            provider="deezer",
            artist=(item.get("artist") or {}).get("name"),
            title=item.get("title"),
            album=(item.get("album") or {}).get("title"),
            isrc=item.get("isrc"),
            duration_sec=item.get("duration"),
            provider_id=str(item.get("id")) if item.get("id") else None,
            provider_url=item.get("link"),
        )
        for item in items
        if isinstance(item, dict)
    ]
    return ProviderResult(candidates=candidates, network_used=True)
