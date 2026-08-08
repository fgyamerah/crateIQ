# intelligence/enrichment/ — Online metadata enrichment from external APIs.
#
#   spotify_lookup.py     — Spotify Web API (primary source, requires credentials)
#   deezer_lookup.py      — Deezer API (second source, no credentials required)
#   traxsource_lookup.py  — Traxsource HTML scraper (dance-music specialist fallback)
#   metadata_matcher.py   — Deterministic scoring, safeguards, label alias boost,
#                           and change-policy enforcement
#   enrichment_schema.py  — EnrichmentCandidate and EnrichmentMatch dataclasses
#   runner.py             — Entry point for the metadata-enrich-online subcommand
#
# Source priority order (runner._search_with_fallback):
#   1. Spotify ISRC lookup  — most precise; skipped if file has no ISRC
#   2. Spotify artist+title — up to 5 candidates, scored by metadata_matcher
#   3. Deezer               — fallback when Spotify confidence < 0.70
#   4. Traxsource           — dance-music specialist fallback; triggered when
#                             combined confidence < 0.80 OR file genre is house/
#                             Afro/deep/soulful.  Never the first source.
#
# To add another source (e.g. MusicBrainz):
#   1. Add <source>_lookup.py with search_by_artist_title() → List[EnrichmentCandidate]
#   2. Wire into runner._search_with_fallback() following the Traxsource pattern
#   3. Update this file
