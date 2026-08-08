"""
Targeted tests for backend.app.services.consensus_service (Cycle 11).

Covers: identity verdict rules (ISRC agreement, AcoustID match, multi-
provider artist/title agreement, conflict, single-source), field-by-field
confidence (never derived from track-level identity alone), and genre
resolution (authority weighting for Beatport/Discogs, conflict detection,
never forcing all electronic styles into one bucket).
"""
from __future__ import annotations

import sqlite3

import pytest

from backend.app.services import consensus_service as cs
from backend.app.services.providers.base import ProviderCandidate


def _candidate(provider, **kwargs):
    return ProviderCandidate(provider=provider, **kwargs)


# ---------------------------------------------------------------------------
# Identity verdict
# ---------------------------------------------------------------------------

def test_identity_high_on_isrc_agreement():
    candidates = {
        "spotify": [_candidate("spotify", isrc="GBDUW0000053", artist="Daft Punk", title="One More Time")],
        "deezer": [_candidate("deezer", isrc="GBDUW0000053", artist="Daft Punk", title="One More Time")],
    }
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.identity_confidence == "HIGH"
    assert consensus.identity_reason == "exact_isrc_agreement"


def test_identity_high_on_acoustid_match():
    candidates = {"acoustid": [_candidate("acoustid", provider_id="mbid-123", artist="DJ Koze", title="Pick Up")]}
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.identity_confidence == "HIGH"
    assert consensus.identity_reason == "acoustid_fingerprint_match"


def test_identity_high_on_multi_provider_agreement():
    candidates = {
        "beets": [_candidate("beets", artist="DJ Koze", title="Pick Up")],
        "musicbrainz": [_candidate("musicbrainz", artist="DJ Koze", title="Pick Up")],
    }
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.identity_confidence == "HIGH"
    assert consensus.identity_reason == "multi_provider_artist_title_agreement"


def test_identity_conflict_on_disagreement():
    candidates = {
        "beets": [_candidate("beets", artist="DJ Koze", title="Pick Up")],
        "musicbrainz": [_candidate("musicbrainz", artist="Different Artist", title="Different Title")],
    }
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.identity_confidence == "CONFLICT"


def test_identity_medium_on_single_high_confidence_source():
    candidates = {"beets": [_candidate("beets", artist="DJ Koze", title="Pick Up", raw_confidence="high")]}
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.identity_confidence == "MEDIUM"
    assert consensus.identity_reason == "single_source_high_confidence"


def test_identity_low_with_no_candidates():
    consensus = cs.build_track_consensus(1, {})
    assert consensus.identity_confidence == "LOW"
    assert consensus.identity_reason == "no_candidates"


# ---------------------------------------------------------------------------
# Field-level confidence -- never blindly inherits track-level identity
# ---------------------------------------------------------------------------

def test_field_high_requires_multi_provider_agreement_even_with_strong_identity():
    candidates = {
        "beets": [_candidate("beets", artist="DJ Koze", title="Pick Up")],
        "musicbrainz": [_candidate("musicbrainz", artist="DJ Koze", title="Pick Up")],
    }
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.fields["artist"].confidence == "HIGH"
    assert consensus.fields["title"].confidence == "HIGH"


def test_field_conflict_when_providers_disagree_on_one_field():
    candidates = {
        "beets": [_candidate("beets", artist="DJ Koze", title="Pick Up")],
        "discogs": [_candidate("discogs", artist="DJ Koze", title="Different Title")],
    }
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.fields["title"].confidence == "CONFLICT"


def test_field_no_evidence_when_field_absent():
    candidates = {"beets": [_candidate("beets", artist="DJ Koze", title=None)]}
    consensus = cs.build_track_consensus(1, candidates)
    assert consensus.fields["title"].confidence == "LOW"
    assert consensus.fields["title"].reason_code == "no_evidence"


# ---------------------------------------------------------------------------
# Genre resolution -- authority weighting, mapped through genre_mappings
# ---------------------------------------------------------------------------

@pytest.fixture()
def genre_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE genre_mappings(raw_genre TEXT, normalized_genre TEXT, enabled INTEGER DEFAULT 1)")
    conn.executemany(
        "INSERT INTO genre_mappings (raw_genre, normalized_genre, enabled) VALUES (?, ?, 1)",
        [("deep house", "Deep House"), ("afro house", "Afro House"), ("amapiano", "Amapiano")],
    )
    conn.commit()
    yield conn
    conn.close()


def test_normalize_genre_maps_through_taxonomy(genre_conn):
    assert cs.normalize_genre(genre_conn, "Deep House") == "Deep House"
    assert cs.normalize_genre(genre_conn, "deep house") == "Deep House"
    assert cs.normalize_genre(genre_conn, "totally-unknown-genre-xyz") is None


def test_normalize_genre_missing_table_returns_none():
    conn = sqlite3.connect(":memory:")
    assert cs.normalize_genre(conn, "Deep House") is None
    conn.close()


def test_genre_beatport_authority_wins_over_generic_source(genre_conn):
    candidates = {
        "beatport": [_candidate("beatport", genre="Deep House")],
        "lastfm": [_candidate("lastfm", genre="Deep House")],
    }
    consensus = cs.build_track_consensus(1, candidates, conn=genre_conn)
    assert consensus.fields["genre"].value == "Deep House"
    assert consensus.fields["genre"].reason_code == "beatport_genre_authority"


def test_genre_authority_conflict_detected(genre_conn):
    candidates = {
        "beatport": [_candidate("beatport", genre="Deep House")],
        "discogs": [_candidate("discogs", genre="Amapiano")],
    }
    consensus = cs.build_track_consensus(1, candidates, conn=genre_conn)
    assert consensus.fields["genre"].confidence == "CONFLICT"


def test_genre_never_forces_all_electronic_styles_into_one_bucket(genre_conn):
    """Afro House and Amapiano must remain distinct, never collapsed to a single genre."""
    afro_house = {"beatport": [_candidate("beatport", genre="Afro House")]}
    amapiano = {"beatport": [_candidate("beatport", genre="Amapiano")]}
    result1 = cs.build_track_consensus(1, afro_house, conn=genre_conn)
    result2 = cs.build_track_consensus(2, amapiano, conn=genre_conn)
    assert result1.fields["genre"].value == "Afro House"
    assert result2.fields["genre"].value == "Amapiano"
    assert result1.fields["genre"].value != result2.fields["genre"].value


def test_genre_unrecognized_terms_stay_low_confidence_not_fabricated(genre_conn):
    candidates = {"lastfm": [_candidate("lastfm", genre="some-random-unmapped-tag")]}
    consensus = cs.build_track_consensus(1, candidates, conn=genre_conn)
    assert consensus.fields["genre"].value is None
    assert consensus.fields["genre"].reason_code == "unrecognized_genre_terms"


def test_genre_no_evidence(genre_conn):
    consensus = cs.build_track_consensus(1, {"beets": [_candidate("beets", artist="X")]}, conn=genre_conn)
    assert consensus.fields["genre"].confidence == "LOW"
    assert consensus.fields["genre"].reason_code == "no_evidence"
