"""
YouTube provider adapter (Cycle 11).

Official YouTube Data API v3 only: https://www.googleapis.com/youtube/v3/search,
authenticated with a self-serve Google Cloud API key. Default quota is
10,000 units/day and a single search.list call costs 100 units (~100
searches/day) -- this adapter makes exactly one search call per lookup
and never a follow-up videos.list call (which would double the quota
cost) to stay conservative, since duration corroboration is optional here.

LOW-AUTHORITY evidence only, per the product's consensus rules: video
title/channel/description are discovery/corroboration signal, never
sufficient alone for a HIGH-confidence automatic match. Never used to
parse arbitrary page HTML -- API responses only. Intended as a
late-stage fallback after stronger providers have already been tried.
"""
from __future__ import annotations

import logging

import requests

from .base import ProviderCandidate, ProviderCapability, ProviderResult, missing_credentials, timeout

log = logging.getLogger(__name__)

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_REQUIRED = ("api_key",)


def capability(credentials: dict[str, str]) -> ProviderCapability:
    missing = missing_credentials(_REQUIRED, credentials)
    if missing:
        return ProviderCapability(
            status="needs_setup",
            message="Add a Google Cloud API key with the YouTube Data API v3 enabled in Settings. Default quota is 10,000 units/day; search costs 100 units per call.",
            required_credentials=_REQUIRED,
        )
    return ProviderCapability(status="ready", message="API key configured.", required_credentials=_REQUIRED)


def search_track(artist: str | None, title: str | None, *, credentials: dict[str, str]) -> ProviderResult:
    if not title:
        return ProviderResult(error="Title is required for a YouTube search.")
    cap = capability(credentials)
    if cap.status != "ready":
        return ProviderResult(error=cap.message)

    query = f"{artist} {title}".strip() if artist else title
    try:
        resp = requests.get(
            _SEARCH_URL,
            params={"part": "snippet", "q": query, "type": "video", "maxResults": 5, "key": credentials["api_key"]},
            timeout=timeout(),
        )
    except requests.RequestException as exc:
        return ProviderResult(error=f"YouTube request failed: {exc}", network_used=True)

    if resp.status_code == 403:
        return ProviderResult(error="YouTube quota exceeded or API key invalid/restricted.", status_code=403, network_used=True)
    if resp.status_code == 429:
        return ProviderResult(error="YouTube rate limit exceeded.", status_code=429, network_used=True)
    if resp.status_code >= 400:
        return ProviderResult(error=f"YouTube returned HTTP {resp.status_code}.", status_code=resp.status_code, network_used=True)

    try:
        items = resp.json().get("items", [])
    except ValueError:
        return ProviderResult(error="YouTube returned an unexpected response shape.", network_used=True)

    candidates: list[ProviderCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId")
        candidates.append(ProviderCandidate(
            provider="youtube",
            title=snippet.get("title"),
            artist=snippet.get("channelTitle"),
            provider_id=video_id,
            provider_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
            raw_confidence="low_authority_corroboration_only",
        ))
    return ProviderResult(candidates=candidates, network_used=True)
