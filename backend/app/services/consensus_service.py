"""
Evidence consensus engine (Cycle 11).

Aggregates ProviderCandidate evidence from however many providers were
actually queried for one track into an explainable, deterministic
HIGH/MEDIUM/LOW/CONFLICT verdict per field -- never a fabricated
pseudo-probability. A track-level identity verdict does not imply every
field is equally confident; confidence is computed field-by-field, per
the product's explicit rule.

Strong identity signals (in order of strength):
  - exact ISRC agreement across >=2 independent providers
  - a real AcoustID recording-ID match (fingerprint-based, not text search)
  - >=2 independent providers agreeing on normalized artist+title
  - a single provider's own 'high' confidence tier, alone

Genre gets special handling: raw provider genre/style strings are mapped
through the existing genre_mappings table (same one Genre Taxonomy uses,
see backend/app/api/routes/genres.py) rather than used verbatim, and
Beatport/Discogs (electronic/DJ-catalogue authorities) are weighted above
generic sources once track identity itself is already strong -- never
forcing every electronic subgenre into one bucket.

HIGH auto-apply requires: strong identity evidence AND no conflicting
value for that specific field AND the field-level agreement rule above.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from .providers.base import ProviderCandidate

Confidence = str  # "HIGH" | "MEDIUM" | "LOW" | "CONFLICT"

_GENRE_AUTHORITY_ORDER = ("beatport", "discogs", "lastfm")
_IDENTITY_FIELDS = ("artist", "title")


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


@dataclass
class FieldConsensus:
    field: str
    value: str | None
    confidence: Confidence
    reason_code: str
    evidence: list[str] = dc_field(default_factory=list)


@dataclass
class TrackConsensus:
    track_id: int
    identity_confidence: Confidence
    identity_reason: str
    fields: dict[str, FieldConsensus]


def normalize_genre(conn: sqlite3.Connection, raw_genre: str | None) -> str | None:
    """Map a raw provider genre/style string through the existing genre_mappings table."""
    if not raw_genre or not raw_genre.strip():
        return None
    key = raw_genre.strip().casefold()
    try:
        row = conn.execute(
            "SELECT normalized_genre FROM genre_mappings WHERE raw_genre = ? AND enabled = 1", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # genre_mappings table not yet initialized -- no mapping available
    return row[0] if row else None


def _identity_verdict(candidates: list[ProviderCandidate]) -> tuple[Confidence, str]:
    isrcs = {c.isrc for c in candidates if c.isrc}
    if len(isrcs) == 1 and sum(1 for c in candidates if c.isrc) >= 2:
        return "HIGH", "exact_isrc_agreement"

    acoustid_matches = [c for c in candidates if c.provider == "acoustid" and c.provider_id]
    if acoustid_matches:
        return "HIGH", "acoustid_fingerprint_match"

    artist_title_pairs = [
        (_normalize(c.artist), _normalize(c.title)) for c in candidates if c.artist and c.title
    ]
    if len(artist_title_pairs) >= 2:
        distinct_pairs = set(artist_title_pairs)
        if len(distinct_pairs) > 1:
            return "CONFLICT", "providers_disagree_on_artist_title"
        return "HIGH", "multi_provider_artist_title_agreement"

    high_tier = [c for c in candidates if c.raw_confidence and "high" in c.raw_confidence.lower()]
    if high_tier:
        return "MEDIUM", "single_source_high_confidence"

    if candidates:
        return "LOW", "single_low_confidence_or_partial_match"

    return "LOW", "no_candidates"


def _field_verdict(field_name: str, candidates: list[ProviderCandidate], identity: Confidence) -> FieldConsensus:
    values = [(c.provider, getattr(c, field_name)) for c in candidates if getattr(c, field_name)]
    if not values:
        return FieldConsensus(field=field_name, value=None, confidence="LOW", reason_code="no_evidence")

    normalized_groups: dict[str, list[str]] = {}
    for provider, value in values:
        normalized_groups.setdefault(_normalize(value), []).append(provider)

    if len(normalized_groups) > 1:
        evidence = [f"{p}: {v}" for p, v in values]
        return FieldConsensus(field=field_name, value=None, confidence="CONFLICT", reason_code="provider_disagreement", evidence=evidence)

    (_, providers_agreeing), = normalized_groups.items()
    original_value = next(v for p, v in values if p in providers_agreeing)
    evidence = [f"{p}: {v}" for p, v in values]

    if identity == "HIGH" and (len(providers_agreeing) >= 2 or field_name not in _IDENTITY_FIELDS):
        confidence = "HIGH" if len(providers_agreeing) >= 2 else "MEDIUM"
        reason = "agrees_with_strong_identity" if len(providers_agreeing) >= 2 else "single_source_with_strong_identity"
    elif len(providers_agreeing) >= 2:
        confidence, reason = "MEDIUM", "multi_source_agreement_weak_identity"
    else:
        confidence, reason = "LOW", "single_source_only"

    return FieldConsensus(field=field_name, value=original_value, confidence=confidence, reason_code=reason, evidence=evidence)


def build_track_consensus(
    track_id: int,
    candidates_by_provider: dict[str, list[ProviderCandidate]],
    *,
    conn: sqlite3.Connection | None = None,
) -> TrackConsensus:
    """
    candidates_by_provider: {"beets": [...], "musicbrainz": [...], "discogs": [...], ...}
    Empty/missing providers are simply not evidence -- this never queries
    a provider itself; callers decide which providers to gather first.
    """
    all_candidates = [c for candidates in candidates_by_provider.values() for c in candidates]
    identity_confidence, identity_reason = _identity_verdict(all_candidates)

    fields: dict[str, FieldConsensus] = {}
    for field_name in ("artist", "title"):
        fields[field_name] = _field_verdict(field_name, all_candidates, identity_confidence)

    fields["genre"] = _genre_verdict(all_candidates, identity_confidence, conn=conn)

    return TrackConsensus(
        track_id=track_id, identity_confidence=identity_confidence,
        identity_reason=identity_reason, fields=fields,
    )


def _genre_verdict(
    candidates: list[ProviderCandidate], identity: Confidence, *, conn: sqlite3.Connection | None,
) -> FieldConsensus:
    genre_candidates = [c for c in candidates if c.genre or c.style]
    if not genre_candidates:
        return FieldConsensus(field="genre", value=None, confidence="LOW", reason_code="no_evidence")

    normalized_by_provider: dict[str, str] = {}
    evidence: list[str] = []
    for c in genre_candidates:
        raw = c.genre or c.style
        normalized = normalize_genre(conn, raw) if conn is not None else raw
        if normalized:
            normalized_by_provider[c.provider] = normalized
            evidence.append(f"{c.provider}: {raw} -> {normalized}")

    if not normalized_by_provider:
        return FieldConsensus(field="genre", value=None, confidence="LOW", reason_code="unrecognized_genre_terms", evidence=[f"{c.provider}: {c.genre or c.style}" for c in genre_candidates])

    # Electronic/DJ-catalogue authorities are trusted above generic sources
    # once identity is already strong -- but never override a disagreeing
    # authority with a lower-priority source.
    for authority in _GENRE_AUTHORITY_ORDER:
        if authority in normalized_by_provider:
            authoritative_value = normalized_by_provider[authority]
            others = {v for p, v in normalized_by_provider.items() if p != authority}
            if others and others != {authoritative_value}:
                return FieldConsensus(field="genre", value=None, confidence="CONFLICT", reason_code="genre_authority_disagreement", evidence=evidence)
            confidence = "HIGH" if identity == "HIGH" else "MEDIUM"
            return FieldConsensus(field="genre", value=authoritative_value, confidence=confidence, reason_code=f"{authority}_genre_authority", evidence=evidence)

    distinct_values = set(normalized_by_provider.values())
    if len(distinct_values) > 1:
        return FieldConsensus(field="genre", value=None, confidence="CONFLICT", reason_code="genre_provider_disagreement", evidence=evidence)
    value = next(iter(distinct_values))
    confidence = "MEDIUM" if len(normalized_by_provider) >= 2 else "LOW"
    return FieldConsensus(field="genre", value=value, confidence=confidence, reason_code="non_authority_genre_evidence", evidence=evidence)
