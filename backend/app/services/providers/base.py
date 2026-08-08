"""
Shared provider adapter contract (Cycle 11).

Every provider module in this package exposes the same small surface:

    capability(credentials: dict[str, str]) -> ProviderCapability
    search_track(artist, title, *, credentials, **hints) -> ProviderResult

capability() is a pure, local, no-network check: does this provider have
what it needs to make a real request (credentials present, and for
platform-gated providers, is self-service access even possible at all).
It must never claim "ready" without a real request having succeeded at
least once in production use -- see each adapter's own docstring for its
specific truthful-status rules.

search_track() makes at most one bounded HTTP request (or a small,
documented, bounded set for multi-step auth flows like OAuth client
credentials). It never raises for ordinary provider failure modes (401,
403, 429, timeout, no match) -- those come back as a ProviderResult with
candidates=[] and a populated error/warning, so a batch caller can
continue past one provider's failure without a try/except at every call
site. Only programmer errors (unsupported field, bad input) raise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CapabilityStatus = Literal["ready", "needs_setup", "unavailable", "misconfigured"]


@dataclass
class ProviderCapability:
    status: CapabilityStatus
    message: str
    required_credentials: tuple[str, ...] = ()


@dataclass
class ProviderCandidate:
    """Normalized evidence from one provider for one track query."""
    provider: str
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    album_artist: str | None = None
    genre: str | None = None
    style: str | None = None
    label: str | None = None
    year: str | None = None
    catalog_number: str | None = None
    isrc: str | None = None
    duration_sec: float | None = None
    mix_version: str | None = None
    provider_id: str | None = None
    provider_url: str | None = None
    raw_confidence: str | None = None  # provider's own tier/score, human-readable
    tags: list[str] = field(default_factory=list)  # e.g. Last.fm community tags


@dataclass
class ProviderResult:
    candidates: list[ProviderCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    status_code: int | None = None
    network_used: bool = False


_TIMEOUT_SECONDS = 8


def timeout() -> int:
    return _TIMEOUT_SECONDS


def missing_credentials(required: tuple[str, ...], credentials: dict[str, str]) -> tuple[str, ...]:
    return tuple(field for field in required if not credentials.get(field, "").strip())
