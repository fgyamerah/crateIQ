"""
Shared, safe access to MusicBrainz via beets' bundled, upstream-tested API
client (beetsplug.musicbrainz / beetsplug._utils.musicbrainz).

Reused rather than reimplemented: beets' client already provides a
compliant User-Agent, a 10s request timeout, bounded retries on transient
5xx/429 responses only, and MusicBrainz's own required 1 request/second
rate limit -- building a second HTTP client here would duplicate all of
that and risk getting the rate limit wrong.

Isolation (required -- a real ~/.config/beets/library.db can exist on this
machine):
- beets.config is read with user=False so CrateIQ never loads a real
  user's beets config file.
- Only in-memory beets.library.Item objects are constructed; no Library
  is opened, so no real beets library.db is ever touched.
- Only read-only MusicBrainz search/lookup calls are made. No import,
  write, move, or tagging stage of beets is invoked.

This module makes network calls. It must only be invoked from an
explicit, user-triggered, single-track lookup -- never from a GET/page
load and never looped over an entire library.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_configured = False


def _ensure_isolated_config() -> None:
    """Materialize beets.config once, from bundled defaults only."""
    global _configured
    if _configured:
        return
    import beets

    beets.config.clear()
    beets.config.read(user=False, defaults=True)
    _configured = True


def _plugin():
    from beetsplug.musicbrainz import MusicBrainzPlugin

    _ensure_isolated_config()
    return MusicBrainzPlugin()


@dataclass
class MusicBrainzError:
    message: str


def search_recordings(artist: str, title: str, limit: int = 5) -> list[dict[str, Any]] | MusicBrainzError:
    """Raw MusicBrainz recording search. One bounded HTTP call."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        return []
    limit = max(1, min(limit, 10))
    try:
        plugin = _plugin()
        filters: dict[str, str] = {"recording": title}
        if artist:
            filters["artist"] = artist
        results = plugin.mb_api.search("recording", filters, limit=limit)
    except Exception as exc:  # network/HTTP/parsing failure -- never crash the request
        log.warning("MusicBrainz search failed: %s", exc.__class__.__name__)
        return MusicBrainzError(f"MusicBrainz lookup failed ({exc.__class__.__name__}).")
    candidates: list[dict[str, Any]] = []
    for row in results[:limit]:
        artist_credit = row.get("artist_credit") or []
        artist_name = " & ".join(
            str(credit.get("artist", {}).get("name") or credit.get("name") or "").strip()
            for credit in artist_credit
            if credit.get("artist", {}).get("name") or credit.get("name")
        ) or None
        releases = row.get("releases") or []
        album = str(releases[0].get("title")) if releases and releases[0].get("title") else None
        date = str(releases[0].get("date")) if releases and releases[0].get("date") else None
        score = row.get("score")
        candidates.append({
            "mb_recording_id": row.get("id"),
            "artist": artist_name,
            "title": row.get("title"),
            "album": album,
            "date": date,
            "score": int(score) if isinstance(score, (int, float)) else None,
        })
    return candidates


def match_track_candidates(artist: str, title: str, limit: int = 5) -> list[dict[str, Any]] | MusicBrainzError:
    """Beets' own distance-scored track matching against MusicBrainz candidates."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        return []
    limit = max(1, min(limit, 10))
    try:
        from beets.autotag import track_distance
        from beets.library import Item

        plugin = _plugin()
        item = Item(artist=artist, title=title)
        candidates = list(plugin.item_candidates(item, artist, title))
        scored = sorted(
            (
                (track_distance(item, info, incl_artist=bool(artist)).distance, info)
                for info in candidates
            ),
            key=lambda pair: pair[0],
        )
    except Exception as exc:
        log.warning("Beets/MusicBrainz matching failed: %s", exc.__class__.__name__)
        return MusicBrainzError(f"Beets lookup failed ({exc.__class__.__name__}).")
    results: list[dict[str, Any]] = []
    for distance, info in scored[:limit]:
        if distance < 0.04:
            confidence = "HIGH"
        elif distance <= 0.25:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        results.append({
            "mb_recording_id": getattr(info, "track_id", None),
            "artist": getattr(info, "artist", None),
            "title": getattr(info, "title", None),
            "album": getattr(info, "album", None),
            "distance": round(float(distance), 4),
            "confidence": confidence,
        })
    return results
