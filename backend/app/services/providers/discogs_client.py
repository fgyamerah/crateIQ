"""
Discogs provider adapter (Cycle 11).

Official API only: https://api.discogs.com/database/search, authenticated
with a self-serve personal access token (discogs.com/settings/developers).
Discogs requires a descriptive User-Agent identifying the application and
rate-limits by source IP: 60 requests/minute authenticated, 25/minute
unauthenticated (headers X-Discogs-Ratelimit-* echo the current window).

Useful for release/version/label/catalog-number/genre/style evidence --
strong for electronic/vinyl/remix metadata. Discogs' terms require
attribution/link-back for displayed data, which is why every candidate
carries provider_url.
"""
from __future__ import annotations

import logging

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, missing_credentials, timeout

log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.discogs.com/database/search"
_REQUIRED = ("personal_access_token",)
_USER_AGENT = "CrateIQ/1.0 +local-first-dj-library-tool"


def capability(credentials: dict[str, str]) -> ProviderCapability:
    missing = missing_credentials(_REQUIRED, credentials)
    if missing:
        return ProviderCapability(
            status="needs_setup",
            message="Add a Discogs personal access token (discogs.com/settings/developers) in Settings to enable this source.",
            required_credentials=_REQUIRED,
        )
    return ProviderCapability(status="ready", message="Personal access token configured.", required_credentials=_REQUIRED)


def _headers(credentials: dict[str, str]) -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Authorization": f"Discogs token={credentials['personal_access_token']}",
    }


def search_track(artist: str | None, title: str | None, *, credentials: dict[str, str]) -> ProviderResult:
    if not title:
        return ProviderResult(error="Title is required for a Discogs search.")
    cap = capability(credentials)
    if cap.status != "ready":
        return ProviderResult(error=cap.message)

    query = f"{artist} {title}".strip() if artist else title
    params = {"q": query, "type": "release", "per_page": 5}
    try:
        resp = requests.get(_SEARCH_URL, params=params, headers=_headers(credentials), timeout=timeout())
    except requests.RequestException as exc:
        return ProviderResult(error=f"Discogs request failed: {exc}", network_used=True)

    if resp.status_code == 429:
        return ProviderResult(error="Discogs rate limit exceeded (60 req/min).", status_code=429, network_used=True)
    if resp.status_code in (401, 403):
        return ProviderResult(error="Discogs rejected the personal access token.", status_code=resp.status_code, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"Discogs returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        results = resp.json().get("results", [])
    except ValueError:
        return ProviderResult(error="Discogs returned an unexpected response shape.", network_used=True)

    candidates: list[ProviderCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_title = str(item.get("title") or "")
        cand_artist, _, cand_title = raw_title.partition(" - ")
        styles = item.get("style") or []
        genres = item.get("genre") or []
        candidates.append(ProviderCandidate(
            provider="discogs",
            artist=(cand_artist.strip() or None) if cand_title else None,
            title=(cand_title.strip() or raw_title.strip() or None),
            label=(item.get("label") or [None])[0] if isinstance(item.get("label"), list) else item.get("label"),
            genre=genres[0] if genres else None,
            style=styles[0] if styles else None,
            year=str(item.get("year")) if item.get("year") else None,
            catalog_number=item.get("catno"),
            provider_id=str(item.get("id")) if item.get("id") else None,
            provider_url=f"https://www.discogs.com{item['uri']}" if item.get("uri") else None,
        ))
    return ProviderResult(candidates=candidates, network_used=True)
