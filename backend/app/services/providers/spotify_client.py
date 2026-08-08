"""
Spotify provider adapter (Cycle 11).

Official Web API only: Client Credentials flow
(https://accounts.spotify.com/api/token) against your own Spotify
Developer app, then https://api.spotify.com/v1/search.

As of the February 2026 Web API changes, Development Mode requires the
app owner to hold an active Spotify Premium subscription (the app stops
working otherwise), is capped at 5 users, and several endpoint families
were removed; Extended Quota Mode requires a registered organization with
250k+ monthly active users -- not attainable for local-first software like
CrateIQ. This is surfaced in Settings' configuration_note, not hidden.

Strong evidence when available: ISRC, exact artist/title/album, duration.
Spotify's track-level genre field is unreliable/absent in practice, so
genre is never inferred here from artist-level genre tags.
"""
from __future__ import annotations

import base64
import logging
import time

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, missing_credentials, timeout

log = logging.getLogger(__name__)

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SEARCH_URL = "https://api.spotify.com/v1/search"
_REQUIRED = ("client_id", "client_secret")

# In-process token cache: {(client_id): (token, expires_at_monotonic)}. Not
# persisted -- a fresh backend process re-authenticates once on first use.
_token_cache: dict[str, tuple[str, float]] = {}


def capability(credentials: dict[str, str]) -> ProviderCapability:
    missing = missing_credentials(_REQUIRED, credentials)
    if missing:
        return ProviderCapability(
            status="needs_setup",
            message="Add your Spotify Developer app's Client ID and Secret in Settings. Requires an active Premium subscription on the app owner's account under 2026 Development Mode rules.",
            required_credentials=_REQUIRED,
        )
    return ProviderCapability(status="ready", message="Client credentials configured.", required_credentials=_REQUIRED)


def _get_token(credentials: dict[str, str]) -> tuple[str | None, ProviderResult | None]:
    client_id = credentials["client_id"]
    cached = _token_cache.get(client_id)
    if cached and cached[1] > time.monotonic():
        return cached[0], None

    basic = base64.b64encode(f"{client_id}:{credentials['client_secret']}".encode()).decode()
    try:
        resp = requests.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {basic}"},
            timeout=timeout(),
        )
    except requests.RequestException as exc:
        return None, ProviderResult(error=f"Spotify token request failed: {exc}", network_used=True)

    if resp.status_code in (400, 401):
        return None, ProviderResult(error="Spotify rejected the client credentials.", status_code=resp.status_code, network_used=True)
    if resp.status_code == 403:
        return None, ProviderResult(
            error="Spotify Development Mode requires an active Premium subscription on the app owner's account; access forbidden.",
            status_code=403, network_used=True,
        )
    if resp.status_code >= 400:
        return None, ProviderResult(error=f"Spotify returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        payload = resp.json()
        token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
    except (ValueError, KeyError):
        return None, ProviderResult(error="Spotify token response had an unexpected shape.", network_used=True)

    _token_cache[client_id] = (token, time.monotonic() + expires_in - 30)
    return token, None


def search_track(artist: str | None, title: str | None, *, credentials: dict[str, str]) -> ProviderResult:
    if not title:
        return ProviderResult(error="Title is required for a Spotify search.")
    cap = capability(credentials)
    if cap.status != "ready":
        return ProviderResult(error=cap.message)

    token, error_result = _get_token(credentials)
    if token is None:
        return error_result

    query = f"track:{title}" + (f" artist:{artist}" if artist else "")
    try:
        resp = requests.get(
            _SEARCH_URL, params={"q": query, "type": "track", "limit": 5},
            headers={"Authorization": f"Bearer {token}"}, timeout=timeout(),
        )
    except requests.RequestException as exc:
        return ProviderResult(error=f"Spotify search failed: {exc}", network_used=True)

    if resp.status_code == 429:
        return ProviderResult(error="Spotify quota exceeded.", status_code=429, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"Spotify returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        items = resp.json().get("tracks", {}).get("items", [])
    except ValueError:
        return ProviderResult(error="Spotify search response had an unexpected shape.", network_used=True)

    candidates = [
        ProviderCandidate(
            provider="spotify",
            artist=", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name")) or None,
            title=item.get("name"),
            album=item.get("album", {}).get("name"),
            isrc=item.get("external_ids", {}).get("isrc"),
            duration_sec=(item.get("duration_ms") or 0) / 1000 or None,
            provider_id=item.get("id"),
            provider_url=item.get("external_urls", {}).get("spotify"),
        )
        for item in items
        if isinstance(item, dict)
    ]
    return ProviderResult(candidates=candidates, network_used=True)
