"""
Beatport provider adapter (Cycle 11).

Official v4 REST API only: https://api.beatport.com/v4/catalog/search/,
Bearer-token authenticated. Beatport's v4 API uses OAuth 2.0
authorization-code grant (a user-consent redirect flow), not a simple
client-credentials API key, and -- more importantly -- there is no public
self-service developer signup at all: access is brokered case-by-case
through Beatport's Partner Portal / business-development team. This
adapter does not implement the interactive OAuth consent redirect (that
would need callback-URL infrastructure this local-first app does not
have, and is moot without partner approval regardless); it expects a
bearer access token the user obtained through Beatport's own external
partner OAuth flow and pasted into Settings.

Never scrapes beatport.com. If no token is configured, or Beatport has
not granted this token catalog-search scope, capability() truthfully
reports "needs_setup" / "unavailable" rather than claiming readiness.

High genre authority for electronic/DJ catalogue matches once a strong
identity match already exists, per the product's consensus rules --
CrateIQ's own consensus_service.py applies that weighting, not this
adapter.
"""
from __future__ import annotations

import logging

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, missing_credentials, timeout

log = logging.getLogger(__name__)

_SEARCH_URL = "https://api.beatport.com/v4/catalog/search/"
_REQUIRED = ("access_token",)


def capability(credentials: dict[str, str]) -> ProviderCapability:
    missing = missing_credentials(_REQUIRED, credentials)
    if missing:
        return ProviderCapability(
            status="needs_setup",
            message=(
                "Beatport has no public self-service signup. Access requires partner "
                "approval via Beatport's Partner Portal / business-development team; "
                "once approved, paste the resulting bearer access token in Settings."
            ),
            required_credentials=_REQUIRED,
        )
    return ProviderCapability(status="ready", message="Access token configured.", required_credentials=_REQUIRED)


def search_track(artist: str | None, title: str | None, *, credentials: dict[str, str]) -> ProviderResult:
    if not title:
        return ProviderResult(error="Title is required for a Beatport search.")
    cap = capability(credentials)
    if cap.status != "ready":
        return ProviderResult(error=cap.message)

    query = f"{artist} {title}".strip() if artist else title
    try:
        resp = requests.get(
            _SEARCH_URL, params={"q": query, "type": "tracks", "per_page": 5},
            headers={"Authorization": f"Bearer {credentials['access_token']}"}, timeout=timeout(),
        )
    except requests.RequestException as exc:
        return ProviderResult(error=f"Beatport request failed: {exc}", network_used=True)

    if resp.status_code in (401, 403):
        return ProviderResult(
            error="Beatport rejected the access token (expired, revoked, or missing catalog-search scope).",
            status_code=resp.status_code, network_used=True,
        )
    if resp.status_code == 429:
        return ProviderResult(error="Beatport rate limit exceeded.", status_code=429, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"Beatport returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        results = resp.json().get("results", [])
    except ValueError:
        return ProviderResult(error="Beatport returned an unexpected response shape.", network_used=True)

    candidates: list[ProviderCandidate] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        artists = item.get("artists") or []
        genre = item.get("genre") or {}
        label = item.get("release", {}).get("label", {}) if isinstance(item.get("release"), dict) else {}
        candidates.append(ProviderCandidate(
            provider="beatport",
            artist=", ".join(a.get("name", "") for a in artists if a.get("name")) or None,
            title=item.get("name"),
            mix_version=item.get("mix_name"),
            genre=genre.get("name") if isinstance(genre, dict) else None,
            label=label.get("name") if isinstance(label, dict) else None,
            catalog_number=item.get("catalog_number") if item.get("catalog_number") else None,
            duration_sec=(item.get("length_ms") or 0) / 1000 or None,
            provider_id=str(item.get("id")) if item.get("id") else None,
            provider_url=item.get("url") or item.get("slug"),
        ))
    return ProviderResult(candidates=candidates, network_used=True)
