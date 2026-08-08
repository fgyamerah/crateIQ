"""
Staged multi-provider evidence gathering (Cycle 11).

Does not query every provider for every track -- follows the product's
staged evidence order, stopping once build_track_consensus() already
reaches HIGH identity confidence:

  1. Beets + MusicBrainz (existing Cycle 6 implementation; always tried
     first, since it requires no additional credentials)
  2. AcoustID fingerprint (if a client key is configured and fpcalc is
     available) -- strong identity evidence, tried before text-search
     providers since a fingerprint match is stronger than any text match
  3. Discogs / Beatport (release/genre/DJ-catalogue evidence)
  4. Spotify / Deezer (additional catalogue corroboration)
  5. Last.fm (tag/genre evidence)
  6. YouTube (last-resort, low-authority corroboration only)

Each stage only runs for providers whose capability() currently reports
"ready" -- an unconfigured provider is silently skipped, never an error.
Bounded to at most one request per provider per track (AcoustID: one
fingerprint + one lookup).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import enrichment_review_service, settings_service
from ..core.library_root import assert_path_under_root
from .providers import (
    acoustid_client, beatport_client, deezer_client, discogs_client,
    lastfm_client, spotify_client, youtube_client,
)
from .providers.base import ProviderCandidate
from .consensus_service import TrackConsensus, build_track_consensus

log = logging.getLogger(__name__)

_TEXT_SEARCH_STAGES = (
    ("discogs", discogs_client),
    ("beatport", beatport_client),
    ("spotify", spotify_client),
    ("deezer", deezer_client),
    ("lastfm", lastfm_client),
    ("youtube", youtube_client),
)


def _beets_musicbrainz_candidates(track_id: int) -> dict[str, list[ProviderCandidate]]:
    """Reuses Cycle 6's online_lookup exactly; converts its shape into ProviderCandidate."""
    out: dict[str, list[ProviderCandidate]] = {"beets": [], "musicbrainz": []}
    for source in ("beets", "musicbrainz"):
        try:
            review = enrichment_review_service.online_lookup(track_id, source)
        except (ValueError, LookupError) as exc:
            log.info("provider_routing: %s lookup skipped for track %s: %s", source, track_id, exc)
            continue
        for item in review.get("items", []):
            if item.get("track_id") != track_id or item.get("source_id") != source:
                continue
            fields = item.get("suggested_fields", {})
            out[source].append(ProviderCandidate(
                provider=source, artist=fields.get("artist"), title=fields.get("title"),
                raw_confidence=item.get("confidence"),
            ))
    return out


def gather_evidence(
    track_id: int,
    *,
    artist: str | None,
    title: str | None,
    inbox_copy_path: Path | None,
    credentials_by_source: dict[str, dict[str, str]],
) -> dict[str, list[ProviderCandidate]]:
    """
    Returns {provider_id: [ProviderCandidate, ...]}. Stops early once
    identity confidence is already HIGH after a stage completes.
    """
    evidence = _beets_musicbrainz_candidates(track_id)

    def _current_confidence() -> str:
        return build_track_consensus(track_id, evidence).identity_confidence

    if _current_confidence() != "HIGH":
        acoustid_creds = credentials_by_source.get("acoustid", {})
        if inbox_copy_path is not None and acoustid_client.capability(acoustid_creds).status == "ready":
            fingerprint, duration, error = acoustid_client.fingerprint_file(inbox_copy_path)
            if fingerprint and duration:
                result = acoustid_client.lookup(fingerprint, duration, credentials=acoustid_creds)
                evidence["acoustid"] = result.candidates
            elif error:
                log.info("provider_routing: acoustid fingerprinting skipped for track %s: %s", track_id, error)

    for source_id, adapter in _TEXT_SEARCH_STAGES:
        if _current_confidence() == "HIGH":
            break
        creds = credentials_by_source.get(source_id, {})
        if adapter.capability(creds).status != "ready":
            continue
        result = adapter.search_track(artist, title, credentials=creds)
        if result.error:
            log.info("provider_routing: %s search skipped for track %s: %s", source_id, track_id, result.error)
            continue
        evidence[source_id] = result.candidates

    return evidence


_ALL_SOURCE_IDS = ("acoustid", "discogs", "beatport", "spotify", "deezer", "lastfm", "youtube")


def all_provider_credentials() -> dict[str, dict[str, str]]:
    """Saved credentials for every routable provider, keyed by source id (missing = not configured)."""
    return {source_id: settings_service.get_metadata_source_credentials(source_id) for source_id in _ALL_SOURCE_IDS}


def preview_consensus(root: Path, track_id: int) -> dict[str, Any]:
    """
    Read-only: gathers evidence from every currently-configured provider
    for one track and returns the full explainable field-by-field verdict.
    Makes no write of any kind -- a genuine preview, not an apply action.
    """
    import sqlite3
    db_path = assert_path_under_root(root / "logs" / "processed.db", root)
    if not db_path.is_file():
        raise ValueError("Configure and initialize the managed workspace first.")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, artist, title, filepath FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            raise ValueError(f"Track {track_id} was not found in the local index.")

        credentials_by_source = all_provider_credentials()
        inbox_path = Path(row["filepath"])
        evidence = gather_evidence(
            track_id, artist=row["artist"], title=row["title"],
            inbox_copy_path=inbox_path if inbox_path.is_file() else None,
            credentials_by_source=credentials_by_source,
        )
        consensus = build_track_consensus(track_id, evidence, conn=conn)

    return _consensus_to_dict(consensus, evidence)


def _consensus_to_dict(consensus: TrackConsensus, evidence: dict[str, list[ProviderCandidate]]) -> dict[str, Any]:
    return {
        "track_id": consensus.track_id,
        "identity_confidence": consensus.identity_confidence,
        "identity_reason": consensus.identity_reason,
        "fields": {
            name: {
                "value": fc.value, "confidence": fc.confidence,
                "reason_code": fc.reason_code, "evidence": fc.evidence,
            }
            for name, fc in consensus.fields.items()
        },
        "providers_queried": sorted(evidence.keys()),
        "candidates_by_provider": {
            provider: [
                {"artist": c.artist, "title": c.title, "genre": c.genre, "style": c.style,
                 "provider_url": c.provider_url, "raw_confidence": c.raw_confidence}
                for c in candidates
            ]
            for provider, candidates in evidence.items()
        },
    }
