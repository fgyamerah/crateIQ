# Managed Library & Batch Preparation Program — Cycles 9–13

Branch: `feat/crateiq-managed-library` (base `feat/crateiq-core-usability`
`78e0dfe`). Not merged to main. Goal: replace the single-root "legacy
direct library" model with a managed Inbox/Library/Quarantine workspace,
batch preparation ("Process All"), a unified review surface, real
multi-provider enrichment, and product navigation that a DJ can use by
task, not by internal subsystem name.

## Cycle 9 — Managed Music Workspace (2026-08-08)

New `Inbox/`, `Library/`, `Quarantine/` managed workspace, additive to the
existing legacy direct-library model (never auto-restructured — a root
with an existing `processed.db`/audio files and no workspace marker is
classified `legacy_direct_library` and left untouched). New
`workspace_service.py`: state classification, idempotent configure,
copy-based import (verified, deterministic `(2)`/`(3)` collisions,
symlink-safe, external original never touched or indexed), and
`promote_tracks()` ("Move Ready to Library": artist/title/genre required,
metadata-write verified via `tag_write_service`, BPM/key/waveform are
warnings only, destination `Library/<Genre>/<Artist>/<Artist> -
<Title>.<ext>`, collision blocks rather than auto-numbering). New
`tracks.storage_zone` column (idempotent migration for pre-Cycle-9 DBs).
`GET /api/tracks` now defaults to `zone=library`; `ApplyToFiles.tsx` and
`CrateMind.tsx` updated to `zone=all` so Inbox tracks stay reachable for
write-back/issue triage. New `/inbox` page. 1500 backend tests pass (+36).

Live-verified against disposable fixtures: nested-folder import, byte-
identical original verification, real promotion, final Library path
confirmed. Restarting dev services against the sanctioned library
triggered the `storage_zone` migration live — confirmed additive-only (all
88 rows backfilled to `LIBRARY`, no other value changed, no files touched).

**Known gaps carried forward:** Library overview/quality KPIs not yet
storage_zone-aware; Inbox had no inline metadata editor yet (closed in
Cycle 15).

## Cycle 10 — Batch Preparation & Unified Review (2026-08-08)

"Process All": one explicitly confirmed, cancellable, restart-safe
background operation (new `preparation_operations` table, mirrors
`analysis_operations_service`) chaining deterministic clean →
bounded HIGH-confidence enrichment (Beets+MusicBrainz agreement rule) →
verified write-back → best-effort BPM/key analysis. New unified
`/needs-review`: read-only aggregation across metadata-repair, enrichment
review, and quality review — zero new review/decision state.

**Two real bugs found and fixed:** (1) promotion preview/apply crashed or
silently created a broken empty DB against an uninitialized root — now
fails closed via a shared `_require_initialized_db()` helper; (2) Needs
Review's first draft wrote to the DB from a GET, breaking the app's
read-only smoke-test contract — moved that refresh into Process All
instead (an already write-permitted operation). 1530 backend tests pass
(+30). Live-verified end to end with real ffmpeg-generated fixtures: junk-
token cleanup, real Beets/MusicBrainz enrichment, verified ID3 write-back,
genre still correctly blocking, Needs Review surfacing remaining
exceptions with working deep links.

## Cycle 11 — Multi-Provider Identification & Enrichment (2026-08-08)

Real adapters for AcoustID, Discogs, Beatport, Spotify, Deezer, Last.fm,
and YouTube (new `backend/app/services/providers/` package), each
researched against current official docs before writing code — corrected
two wrong assumptions in the existing Settings registry: Deezer needs no
credentials for basic search (verified live), and Beatport uses OAuth 2.0
authorization-code grant, not a simple key (and has no public self-serve
signup at all — partner-brokered access only). New `consensus_service.py`
(explainable HIGH/MEDIUM/LOW/CONFLICT, field-by-field, never inherited
blindly from track-level identity; genre mapped through the existing
`genre_mappings` table with Beatport/Discogs authority weighting) and
`provider_routing_service.py` (staged evidence gathering in a fixed order,
stops early on HIGH confidence, skips unconfigured providers). Settings →
Metadata Sources needed zero frontend changes (already generic/data-
driven). None of the 6 credential-requiring providers had real credentials
in this environment; Settings truthfully reports "Needs Setup" for those.
1590 backend tests pass (+60).

**Deliberately deferred:** wiring the new consensus engine into Process
All's automatic write-back path — `preparation_service.enrich_tracks()`
still used Cycle 10's simpler Beets+MB-only rule for actual writes (closed
in Cycle 13).

## Cycle 12 — Product Navigation + Final End-to-End Workflow (2026-08-08)

Final cycle of the program. Sidebar reorganized into LIBRARY (Inbox,
Library, Needs Review) / DJ (Crates, Set Builder, Publish) / TOOLS (Jobs,
Maintenance) / SYSTEM (Settings) — one unified "Needs Review" badge
replaces four scattered ones. New `Maintenance.tsx` hub (route
`/maintenance`) links to Quality/Duplicates/Reconciliation/Folders/Audit
without rewriting any of them. `/library-prep` now redirects to `/inbox`
(materially superseded); the old page stays in source, unrouted, matching
the existing Dashboard/Collection/Tracks precedent. Settings gained a
Workspace card reusing the existing Cycle 9 status endpoint — zero new
backend code.

Impeccable's own review caught and fixed a real issue in this cycle's
first Maintenance.tsx draft: a redundant duplicate-link tabs row misusing
ARIA `role="tab"` on plain navigation links. Re-ran the full real journey
(import → Process All → promote → Library → visible to Crates' track
picker) end to end to confirm zero regression from the reorg. Final safety
audit passed in full (88 sanctioned files unchanged, no-beet-CLI-invocation
guard, credentials still gitignored, no secret-shaped strings in the
diff). 1590 backend tests pass (unchanged — no backend logic touched).

## Cycle 13 — Provider Consensus Wired into Process All (2026-08-08)

Closes Cycle 11's deferred gap. `preparation_service.enrich_tracks()` now
calls `provider_routing_service.gather_evidence()` +
`consensus_service.build_track_consensus()` instead of the Beets+MB-only
rule; HIGH field verdicts auto-apply, MEDIUM/LOW/CONFLICT never do, a HIGH
track identity never forces genre to HIGH, and existing valid metadata is
never silently replaced (only the approved junk/placeholder exception, and
only when the replacement is HIGH). Provenance flows through
`enrichment_review_service.queue_consensus_suggestions()` into the same
snapshot/decision queue `online_lookup()` already populated — no new
review table. 14 new mocked-provider tests; 1604 backend tests pass total.

Live-verified through the real running UI against a disposable workspace:
real Deezer + Beets/MusicBrainz calls, credential-requiring providers
skipped cleanly. Four real live tracks (including well-known catalog
tracks) all landed on CONFLICT rather than a clean HIGH — real catalogs
are noisy (alt versions, tribute recordings, same-titled unrelated songs);
each correctly stayed unapplied and surfaced in Needs Review with full
evidence. The HIGH-auto-apply path itself is proven deterministically by
the mocked test suite rather than forced from non-deterministic live data.
