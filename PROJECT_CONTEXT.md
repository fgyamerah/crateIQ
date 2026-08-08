# CrateIQ Project Context

**Updated:** 2026-08-08

**Purpose:** Canonical low-token engineering memory for future AI sessions.

## Latest Milestone

- 2026-08-08: Cycle 12 (Product Navigation + Final End-to-End Workflow),
  the final cycle of the crateIQ Managed Library & Batch Preparation
  Program, on `feat/crateiq-managed-library` (base Cycle 11, this file's
  previous entry), no merge to main. Turns four cycles of real backend
  and workflow capability into a sidebar an ordinary DJ can navigate by
  task rather than by crateIQ's internal subsystem names.

  **Sidebar** (`frontend/src/components/Sidebar.tsx`) rebuilt from the
  old flat `Browse`/`Operations`/`Reconciliation` grouping (17 + 10 + 1
  items, undifferentiated) into the product's target shape: `LIBRARY`
  (Inbox, Library, Needs Review) / `DJ` (Crates, Set Builder, Publish) /
  `TOOLS` (Jobs, Maintenance) / `SYSTEM` (Settings). Every route from
  Cycles 1-11 remains fully mounted -- nothing was deleted, only
  un-listed from the permanent sidebar, matching the explicit "reuse
  specialist pages underneath the simplified IA rather than deleting
  blindly" instruction. The four previously-scattered sidebar badge
  counts (issues, enrichment, repair, bpm -- each from a different
  endpoint, each double-counting overlapping concerns) are replaced by
  one `Needs Review` badge sourced from Cycle 10's own unified
  aggregator (`GET /api/needs-review`), which is a more honest signal
  than four partially-redundant ones ever were.

  **New `frontend/src/pages/Maintenance.tsx`** (route `/maintenance`):
  a hub linking to Quality, Duplicates, Reconciliation, Folders, and
  Audit. Deliberately does not embed these pages inline as true tab
  panels -- `CrateMind.tsx` (which owns Folders and Audit) derives its
  active section from `window.location.pathname` via `sectionFromPath()`
  rather than a prop, so genuine in-page tab-panel embedding would have
  required either faking a pathname or refactoring CrateMind's own
  routing coupling -- out of scope for a navigation-consolidation cycle
  per the explicit "reuse, do not rewrite" instruction. Simple
  navigational link-cards, each carrying real descriptive text, was the
  correct-scoped choice instead.

  **A real bug found and fixed by this cycle's own Impeccable pass**:
  the first draft of `Maintenance.tsx` had both a compact "tabs" row
  (styled with the existing `.reconciliation-tabs`/`.reconciliation-tab`
  classes) *and* a card grid below it, both linking to the identical 5
  destinations -- a redundant, confusing duplication. Worse, the tabs
  row used `role="tab"`/`aria-selected={false}` on what were actually
  plain navigation `<Link>`s: those ARIA roles are a contract that
  implies in-page panel switching and keyboard arrow-key navigation
  (per WAI-ARIA authoring practices), which never happens here --
  clicking one just navigates to a different page. Fixed by removing
  the redundant tabs row entirely and keeping one honest
  `<nav aria-label="Maintenance areas">` of link-cards.

  **Route consolidation** (`frontend/src/App.tsx`): `/library-prep` now
  redirects to `/inbox` instead of staying independently mounted --
  Inbox (built across Cycles 9-10: Process All, Clean/Enrich Selected,
  explicit promotion) materially supersedes Library Prep's older
  single-track step wizard, and leaving both live would have been
  exactly the "duplicate competing main workflow" the product
  explicitly said not to leave. `LibraryPrep.tsx` itself stays in
  source, unrouted -- the same precedent this repo already established
  for the legacy `Dashboard`/`Collection`/`Tracks` pages (documented
  earlier in this file), not a new pattern invented for this cycle.
  New `/maintenance` route contract entry added to
  `tests/test_supported_route_contracts.py`; the old `/library-prep`
  entry removed since it is now a `Navigate` redirect, which the
  contract-sync test correctly excludes from "routes needing a
  contract" (same treatment as `/dashboard`, `/collection`, etc.).

  **Settings** (`frontend/src/pages/Settings.tsx`) gained a `Workspace`
  card (new `#workspace` section plus a quick-jump tab link) showing
  the managed root, Inbox path, Library path, and Quarantine path.
  Reuses Cycle 9's existing `GET /api/workspace/status` endpoint end to
  end -- zero new backend code was needed. Renders the existing state
  message (not broken/empty fields) for a `not_configured` or
  `legacy_direct_library` root.

  **Live end-to-end re-verification**, against a fresh disposable
  workspace (never the sanctioned library, real `ffmpeg`-generated
  MP3): confirmed the reorganized sidebar renders exactly the target
  4-section grouping; confirmed navigating to `/library-prep` redirects
  to `/inbox` (tab URL updates automatically); confirmed the Settings
  Workspace card shows the real managed-root/Inbox/Library/Quarantine
  paths; confirmed a Maintenance card navigates to its real target page
  (`/duplicates`); then re-ran the complete real journey through the
  *new* navigation end to end to prove the reorg introduced zero
  regression: import -> both KPI/preflight counts update -> Process All
  is not needed since the fixture already had matching tags/genre ->
  "Move Ready to Library" two-step confirm -> the file physically landed
  at `Library/House/Final Artist/Final Artist - Final Track.mp3` ->
  Inbox directory confirmed empty -> the external original confirmed
  SHA-256 byte-identical before and after -> `GET /api/tracks` (the
  exact query `Crates.tsx`'s track picker uses) confirmed to return the
  promoted track with `storage_zone: "LIBRARY"`, proving the promised
  "Crates workflow can see promoted Library track" outcome is real, not
  assumed.

  **Final safety audit**, run in full and passed: `compileall`, the
  complete pytest suite (1590, unchanged from Cycle 11 since no backend
  logic was touched this cycle), frontend `typecheck`/`build`,
  `pipeline.py validate-docs --strict`, `git diff --check` (clean), the
  88 sanctioned audio files confirmed still present (file-count check),
  the existing static no-`beet`-CLI-invocation guard test (still
  passing), confirmation that `.run/local/metadata_sources.json` (all
  Cycle 11 provider credentials) remains gitignored, and a diff-wide
  grep across the whole 4-cycle branch for hardcoded-secret-shaped
  strings, which found none. `LedgerIQ` and `opsIQ` (sibling
  directories outside this repo) were never touched or referenced this
  session.

  **Documentation**: added a "Managed Library workflow" section to
  `README.md` explaining the Managed Root/Inbox/Library/Quarantine
  layout, the copy-never-move import contract, Process All, Needs
  Review, explicit promotion, and provider configuration in user-facing
  terms; corrected the "Metadata Sources ... external APIs ... do not
  yet perform lookup" feature-status row, which was accurate before
  Cycle 11 and false afterward, and added rows for the managed
  workspace and the multi-provider consensus engine.

  **Known open follow-up work**, carried forward truthfully rather than
  silently dropped (see `NEXT_TASKS.txt` for full detail): wiring the
  Cycle 11 consensus engine into Process All's automatic write-back
  path (currently still uses Cycle 10's simpler Beets+MB-only rule for
  actual writes); a cross-category "Accept N high-confidence
  recommendations" bulk action on Needs Review; re-verifying live
  requests for the 6 providers that need credentials, if/when real
  credentials become available; `/api/library/overview` and
  `/api/library/quality` are not yet `storage_zone`-aware; Inbox has no
  inline metadata editor yet (routes to existing specialist pages
  instead); batch analysis reuses the existing global missing-BPM/key
  queue rather than one scoped to just the current batch.

- 2026-08-08: Cycle 11 (Multi-Provider Identification & Enrichment) of
  the crateIQ Managed Library & Batch Preparation Program, on
  `feat/crateiq-managed-library` (base Cycle 10, this file's previous
  entry), no merge to main. Adds real adapters for 7 metadata providers
  and an explainable multi-provider consensus engine, replacing
  Cycle 10's implicit assumption that only Beets + MusicBrainz would
  ever supply enrichment evidence.

  **Provider research findings** (each checked against current official
  documentation before any adapter code was written, not assumed from
  training data or old blog posts):
  - AcoustID: self-serve application client key at acoustid.org, 3
    req/sec published rate limit, lookup-only (fingerprints are never
    submitted). `fpcalc` (Chromaprint) is installed in this dev
    environment, so local fingerprinting was verified for real: a real
    `ffmpeg`-generated audio file produced a real, non-empty Chromaprint
    fingerprint via a real `fpcalc -json` subprocess call -- no network,
    no account. (Also discovered empirically: a plain sine tone under
    ~3 seconds reliably yields "ERROR: Empty fingerprint" from `fpcalc`
    itself -- not a bug, a real Chromaprint characteristic that the
    test suite now accounts for.)
  - Discogs: self-serve personal access token (simpler than the
    consumer-key/secret OAuth alternative), 60 req/min authenticated /
    25 req/min unauthenticated, attribution/link-back required by
    Discogs' terms -- every Discogs candidate carries a `provider_url`.
  - Beatport: **no public self-service developer signup exists at
    all** -- access is brokered case-by-case through Beatport's Partner
    Portal / business-development team. The v4 API
    (`api.beatport.com/v4/catalog/search/`) uses OAuth 2.0
    authorization-code grant (a user-consent redirect flow), not a
    simple API key, which the pre-existing Settings registry entry had
    wrong (`requires_credentials: False`, no credential fields at all).
    Corrected to `requires_credentials: True` with a single
    `access_token` field: a partner-approved user completes Beatport's
    OAuth flow externally and pastes the resulting bearer token: this
    app does not implement the interactive OAuth consent redirect
    itself (no callback-URL infrastructure, and moot without partner
    approval regardless).
  - Spotify: self-serve Client ID/Secret, but the **February 2026 Web
    API Development Mode changes** are materially restrictive: the app
    owner must hold an active Spotify Premium subscription or the app
    stops working, new apps are capped at one client ID and 5 users,
    several endpoint families were removed, and search result limits
    dropped from 50 to 10. Extended Quota Mode requires a registered
    organization with 250k+ monthly active users -- structurally
    unreachable for local-first software like CrateIQ. All of this is
    surfaced verbatim in the Settings `configuration_note`, not
    glossed over. Spotify's track-level genre field is unreliable/
    absent in practice, so genre is never inferred from it.
  - Deezer: **verified live during this cycle's own research** that
    `https://api.deezer.com/search` requires no authentication
    whatsoever for basic track search (a real unauthenticated `curl`
    request returned real track data) -- contradicting the pre-existing
    Settings registry entry, which required `app_id`/`app_secret`.
    Corrected to `requires_credentials: False`. Separately confirmed
    that Deezer for Developers is not currently issuing new OAuth app
    credentials to new applications at all (needed only for user-level
    actions like playlists/favorites, which this adapter does not use).
    Re-verified a second and third time through this app's own code: a
    direct Python call to `settings_service.test_metadata_source
    ('deezer')`, and a real click on "Test" in the actual running
    Settings UI, both succeeded live (`"Live test search succeeded (5
    candidate(s) returned)."`).
  - Last.fm: free self-serve API key, non-commercial use only per
    Last.fm's API ToS (CrateIQ qualifies as local-first, non-commercial
    software). Community tags (`track.getTopTags`) are evidence, never
    an unquestionable canonical genre -- mapped through the existing
    Genre Taxonomy like every other provider's genre evidence.
  - YouTube: official Data API v3, self-serve Google Cloud API key,
    10,000 units/day default quota with `search.list` costing 100
    units (~100 searches/day) -- the adapter makes exactly one search
    call per lookup and deliberately never a follow-up `videos.list`
    call (which would double the cost), consistent with the product's
    explicit "low-authority, last-resort, never scraped" rule; every
    YouTube candidate is tagged `low_authority_corroboration_only`.

  **New `backend/app/services/providers/` package** (`base.py` shared
  contract + one module per provider): `capability(credentials)` is a
  pure, local, no-network truthfulness check
  (`ready`/`needs_setup`/`unavailable`/`misconfigured`);
  `search_track(...)` makes at most one bounded HTTP request (Spotify:
  plus an in-process-cached OAuth token request) and never raises for
  ordinary failure modes (401/403/429/timeout/no-match) -- those come
  back as a `ProviderResult` with `candidates=[]` and a populated
  `error`, so a batch caller can continue past one provider's failure
  without a `try`/`except` at every call site.

  **New `backend/app/services/consensus_service.py`**: explainable
  HIGH/MEDIUM/LOW/CONFLICT verdicts, never a fabricated
  pseudo-probability, per the product's explicit rule. Track-level
  identity reaches HIGH on exact ISRC agreement across >=2 providers, a
  real AcoustID fingerprint match, or >=2 providers agreeing on
  normalized artist+title; disagreement reaches CONFLICT; a single
  high-tier source alone reaches MEDIUM. **Field-by-field confidence is
  computed independently per field** -- a HIGH track-level identity
  does not blindly promote every field to HIGH (caught and fixed a
  real bug here: the original identity-conflict detection only fired
  when the disagreeing pairs also had internal ties, missing the
  simpler "two providers, two different single-occurrence answers"
  case -- found by a dedicated test, not manual inspection). Genre gets
  its own resolution: raw provider genre/style strings are mapped
  through the existing `genre_mappings` table (the same one Genre
  Taxonomy already owns in `backend/app/api/routes/genres.py`) rather
  than used verbatim; Beatport/Discogs are weighted as genre
  authorities once identity is already strong, but an authority's value
  that conflicts with another source surfaces as CONFLICT rather than
  silently overriding it, and distinct electronic subgenres (verified
  with a test asserting Afro House and Amapiano resolve to different
  values from the same evidence shape) are never collapsed into one
  bucket.

  **New `backend/app/services/provider_routing_service.py`**: stages
  evidence gathering exactly per the product's specified order (Beets/
  MusicBrainz first since they need no extra credentials, then AcoustID
  fingerprint, then Discogs/Beatport, then Spotify/Deezer, then
  Last.fm, then YouTube last) and stops early once the consensus engine
  already reports HIGH identity confidence -- verified by a test that
  configures a mocked HIGH-confidence Beets+MusicBrainz agreement and
  asserts Discogs' `search_track` is never called. Each stage silently
  skips any provider whose `capability()` isn't `"ready"`.

  **New `POST /api/workspace/enrichment/consensus/{track_id}`**:
  gathers evidence from every currently-configured provider for one
  track and returns the full explainable verdict. Deliberately a POST,
  not a GET -- this cycle's own first draft framed it as a read-only
  GET, which was wrong: gathering evidence reuses
  `enrichment_review_service.online_lookup()` for the Beets/MusicBrainz
  stage, which makes real network calls and persists them to the
  existing `enrichment_review_service` decision queue, exactly like the
  existing single-track online-lookup action already does. A dedicated
  regression test (`test_consensus_preview_*`) caught the mismatch
  before it shipped by asserting the endpoint's actual DB-write
  behavior rather than trusting the docstring.

  **Settings integration**: `settings_service.py`'s metadata-source
  registry already had stubbed entries for discogs/spotify/deezer/
  beatport/lastfm from an earlier cycle (`current_behavior:
  "settings_only"`/`"planned"`) -- this cycle implemented them for real
  and added the two missing providers (acoustid, youtube). `
  get_metadata_sources()`'s `connection_status` now calls each
  adapter's real `capability()`; `test_metadata_source()` now makes one
  genuine bounded live request for a `ready` provider instead of
  returning a canned `"not_implemented"`. New
  `get_metadata_source_credentials()` accessor is documented and
  enforced as server-side-only -- never called from an API response
  path, verified by a test that saves a real-looking credential value
  and asserts it never appears anywhere in a subsequent API response.
  The existing `MetadataSourcesPanel.tsx` is fully generic/data-driven
  (masks any credential field whose name contains "key"/"token"/
  "secret", groups by category, renders `configuration_note` verbatim)
  and needed **zero frontend changes** to correctly display and mask
  all 7 new providers -- confirmed live in the browser, including a
  real click on Deezer's "Test" button succeeding.

  **Deliberately deferred, not shipped half-safe under time pressure**:
  wiring the consensus engine's multi-provider results into Process
  All's automatic write-back path. `preparation_service.enrich_tracks()`
  still uses Cycle 10's simpler Beets+MB-only agreement rule for actual
  writes; the fuller consensus engine is real, tested, and reachable
  via the new preview endpoint, but integrating it into auto-write
  requires extending `enrichment_review_service`'s snapshot/decision
  model to accept externally-sourced (non-Beets/MB) candidates safely --
  a deeper change to an existing, well-tested write path that deserves
  its own focused review rather than a hasty change under this cycle's
  time budget, especially since, with zero real provider credentials
  configured anywhere in this environment, the change would have been
  unobservable/untestable live in this session regardless. See
  `NEXT_TASKS.txt`.

  1590 backend tests pass (60 new: `tests/test_providers.py`,
  `tests/test_consensus_service.py`,
  `tests/test_provider_routing_and_settings.py`, plus consensus-preview
  route tests and one pre-existing test corrected for the new real
  provider count/behavior); frontend typecheck/build pass (no frontend
  changes); `git diff --check` clean. No secrets committed -- all
  credential fields live only in the existing gitignored
  `.run/local/metadata_sources.json`.

- 2026-08-08: Cycle 10 (Batch Preparation & Unified Review) of the
  crateIQ Managed Library & Batch Preparation Program, on
  `feat/crateiq-managed-library` (base Cycle 9, this file's previous
  entry), no merge to main. Makes processing hundreds of Inbox tracks
  practical: "Process All" is one explicitly confirmed, cancellable,
  restart-safe background operation, and a new unified Needs Review
  page aggregates open exceptions across five categories into one
  read-only view.
  New `preparation_operations` table (`backend/app/core/db.py`) and
  `backend/app/services/preparation_operations_service.py` mirror
  `analysis_operations_service`'s exact lifecycle contract byte-for-byte
  (start/update_progress/finish/request_cancel/
  recover_interrupted_operations, wired into `main.py`'s startup
  recovery same as every other operation table) -- truthful subset
  counts only, never a fabricated percent.
  `backend/app/services/preparation_service.py` is a thin orchestrator,
  not a new engine: clean reuses `modules.sanitizer.sanitize_metadata()`
  (pure, deterministic -- safe to auto-apply by construction); identify/
  enrich reuses `enrichment_review_service.online_lookup()` (Cycle 6's
  real Beets + MusicBrainz calls) bounded to 25 lookups/run, with a
  HIGH-confidence auto-apply rule requiring either independent
  Beets/MusicBrainz agreement on the same normalized artist+title or
  either source's own 'high' tier -- anything less stays exactly as-is
  and surfaces as a pending item in `enrichment_review_service`'s
  existing decision queue (which `online_lookup` itself already
  populates, so no new persistence was needed); write reuses
  `tag_write_service.build_plan()`/`apply_plan()` exactly as Cycle 7/9
  do, chunked to its existing 50-track cap; analyze reuses
  `analysis_jobs_service.run()` bounded and best-effort. Process All
  runs as a real background `asyncio.create_task()` (the same
  fire-and-forget pattern `rsync_runner`/`toolkit_runner` already use
  for subprocess jobs, applied here to in-process batch work instead of
  inventing a scheduler) so a separate cancel request can genuinely
  interrupt it between tracks; the route returns an operation id
  immediately. A known, disclosed scoping caveat: the analyze stage
  reuses the existing *global* missing-BPM/key queue (same as the Jobs
  page), not one scoped to only the current run's track_ids -- CrateIQ
  has no per-track-id-scoped analysis contract yet.
  New `backend/app/services/needs_review_service.py` /
  `GET /api/needs-review` / `frontend/src/pages/NeedsReview.tsx` at
  `/needs-review`: read-only aggregation across
  `metadata_repair_queue_service` (METADATA/GENRE/ANALYSIS --
  missing artist/title/genre, suspicious filenames, duplicate-looking
  title/artist, unknown BPM/key), `enrichment_review_service`'s pending
  decision queue (IDENTITY_ENRICHMENT), and `quality_review_service`'s
  unresolved findings (QUALITY) -- zero new review/decision state, per
  the explicit "do not duplicate all review implementations"
  requirement. Each item carries track_id/category/severity/
  reason_code/summary/current+recommended value/confidence/provenance/
  a deep-link action to the owning specialist page. A cross-category
  bulk "Accept N high-confidence recommendations" action was
  deliberately deferred (see `NEXT_TASKS.txt`) rather than shipped
  half-safe under time pressure -- resolution currently routes through
  each item's specialist page.
  **Two real bugs found and fixed during this cycle, both while wiring
  genuinely new code paths rather than pre-existing ones that happened
  to go unexercised until now:**
  1. `workspace_service.promotion_preview()`/`promote_tracks()`
     (Cycle 9) called `sqlite3.connect()` directly against
     `processed.db` with no existence check -- against an uninitialized
     root this either crashed with an unhandled
     `sqlite3.OperationalError` or silently auto-created an empty,
     schema-less DB file (sqlite3's own connect() behavior). Fixed with
     a shared `_require_initialized_db()` helper (fails closed with a
     clear `ValueError`, mirroring `tag_write_service`'s existing
     `_db_path()` precedent); `preparation_service.preflight_preview()`
     additionally degrades to a genuine 200-with-zero-counts response
     rather than propagating, since it is a GET in the smoke-tested
     read-only surface (`"A fresh root without processed.db must
     degrade safely, not crash"`). Also fixed in the same pass:
     `promotion_preview(root, track_ids=[])` was silently treated
     identically to `track_ids=None` (Python's `if track_ids:`
     truthiness swallowed the empty-list case), previewing *all* Inbox
     tracks instead of none -- now `[]` and `None` are handled
     explicitly and mean what they say. Six new regression tests lock
     all three fixes in, not just the one the broad contract test
     happened to already catch.
  2. This cycle's own first draft of `needs_review_service` called
     `metadata_repair_queue_service.refresh()` (a real write --
     creates/repopulates CrateIQ's own `repair_queue` bookkeeping
     table) directly from the `GET /api/needs-review` path, to keep the
     aggregator always current. That broke
     `test_smoke_surface_is_read_only_and_spawns_no_subprocess`'s
     existing guarantee that the whole smoke-tested GET surface never
     mutates `processed.db` -- caught immediately by the full test gate,
     not by manual inspection. Root-caused via a targeted instrumented
     reproduction that called every smoke endpoint one at a time and
     diffed DB bytes after each. Fixed by reverting Needs Review to
     pure read (`metadata_repair_queue_service.get()`, whatever was
     last computed) and instead calling `refresh()` from inside
     `run_process_all()`, which is already an explicitly confirmed,
     write-permitted operation -- so Needs Review still ends up current
     after a real batch run, without any GET ever writing. Locked in
     with a dedicated before/after DB-bytes regression test in addition
     to the pre-existing broad contract test.
  Inbox (`frontend/src/pages/Inbox.tsx`) gained: a pipeline-stage KPI
  row (Imported/Cleaned/Enriched from the latest persisted Process All
  operation, Ready/Needs work from live readiness), a Process All card
  with preflight counts and a two-step confirm using the exact
  authorization language from the product spec ("does not authorize...
  promotion to final Library"), live progress polling with a Cancel
  control while an operation runs, and Clean Selected/Enrich Selected
  batch actions over per-track selection checkboxes. Impeccable review
  (`polish` playbook) on Inbox + the new Needs Review page found and
  fixed: a batch-selection table with no way to select all (added a
  header select-all checkbox with indeterminate state), and Needs
  Review's category tabs initially using the wrong existing tab
  component (`.settings-tabs`, styled for anchor in-page-jump links)
  instead of the correct one (`.reconciliation-tabs`, the actual
  button-based tab-switcher pattern Reconciliation.tsx already
  establishes) -- switched to the correct component rather than
  patching the wrong one.
  Frontend zone-default audit, extending Cycle 9's `GET /api/tracks`
  `zone=library` default change to its other callers for the first
  time: `ApplyToFiles.tsx` and `CrateMind.tsx` (Issues/Audit/
  Enrichment/Folders) now explicitly request `zone=all`, since
  write-back verification and issue triage must still reach Inbox
  tracks -- exactly where they matter most before promotion.
  `Crates.tsx` was deliberately left on the new `zone=library` default,
  matching the product decision that crates are built from promoted
  Library tracks only.
  1530 backend tests pass (30 new: `tests/test_preparation_service.py`,
  `tests/test_workspace_prepare_routes.py`, 6 new regression tests in
  `tests/test_workspace_service.py`); frontend typecheck/build pass;
  `git diff --check` clean.
  **Live end-to-end verification**, against disposable fixtures only,
  using real `ffmpeg`-generated silent MP3s (not fake byte content)
  specifically so `mutagen`'s full audio-format parsing genuinely
  exercises `tag_write_service`'s real code path rather than short-
  circuiting on an unparseable file: imported a junk-token-filename
  track (`"... [djcity.com]"`) and a filename with no parseable
  artist/title separator through the real running UI, ran Process All
  through its real two-step confirmation, watched it live-clean the
  junk token from the title, enrich the missing-artist track via a
  real Beets distance-scored lookup and a real MusicBrainz search
  (confirmed via the operation's persisted counts and the Needs Review
  page showing the *other*, non-applied source's lower-confidence
  candidate still pending), and verified the write-back landed in the
  actual Inbox copy's ID3 tags via direct `mutagen` re-read on disk --
  while both external originals stayed SHA-256 byte-identical
  throughout. Confirmed genre remained correctly required and blocking
  promotion (Process All never invents a genre value), and confirmed
  Needs Review's category tab counts and deep links worked correctly
  against real aggregated data (8 items across IDENTITY_ENRICHMENT/
  GENRE/ANALYSIS after the run).

- 2026-08-08: Cycle 9 (Managed Music Workspace) of the crateIQ Managed
  Library & Batch Preparation Program, on `feat/crateiq-managed-library`
  (base `feat/crateiq-core-usability` at `78e0dfe`), no merge to main.
  Introduces a physically separated managed workspace --
  `<root>/Inbox/`, `<root>/Library/`, `<root>/Quarantine/` -- additive to
  the existing "legacy direct library" model (a root scanned in place via
  `library_setup_service.import_previewed_library`, unchanged). A root is
  classified read-only as `managed_workspace` (a `.crateiq-workspace.json`
  marker is present), `legacy_direct_library` (audio files or a
  `processed.db` already exist directly under the root with no marker), or
  `not_configured`. `configure_workspace()` refuses to touch a
  `legacy_direct_library` root -- per product decision, existing
  installations are never silently restructured; the message directs the
  user to choose a new dedicated root.
  New `backend/app/services/workspace_service.py` owns this: state
  classification, idempotent configure (creates the three zone dirs +
  marker, reuses `library_setup_service.initialize_library()` for the
  local index rather than a parallel schema), `import_sources()` (copies
  external files/folders into Inbox -- read-only source validation,
  `os.walk(followlinks=False)` with symlinked dirs/files skipped and
  reported as warnings rather than silently included or excluded,
  deterministic `" (2)"`/`" (3)"` collision suffixes so an existing Inbox
  file is never overwritten, a separate identical-content-duplicate path
  that skips the copy rather than doubling it, and a re-hash-after-copy
  verification before the local index is ever updated -- the external
  original is never indexed, only the verified Inbox copy), and
  `promotion_preview()`/`promote_tracks()` (the "Move Ready to Library"
  flow: artist/title/genre required; metadata-write verification reuses
  `tag_write_service.build_plan()` rather than a new check -- if the
  file's current tags don't yet match the approved index values, that's a
  blocker, not silently ignored; BPM/key/waveform absence are warnings
  only, matching the product decision; destination is
  `Library/<Genre>/<Artist>/<Artist> - <Title>.<ext>` via a new
  `safe_path_segment()` sanitizer -- blocks path separators, `..`
  traversal, NUL/control characters, and Windows-reserved trailing
  dots/spaces while preserving Unicode, since the library is ultimately
  synced to a Windows-compatible DJ drive; prefers atomic `Path.rename()`
  since Inbox and Library share one filesystem root, falls back to
  `shutil.move`; a destination collision blocks that track with the Inbox
  copy left in place -- never silently numbered or overwritten for a
  final Library file, matching the product decision that only Inbox
  import collisions get the "(2)" treatment).
  Schema: `tracks.storage_zone` (`INBOX`/`LIBRARY`/`QUARANTINE`, default
  `'LIBRARY'`) added to `library_setup_service._TRACKS_SCHEMA` for new
  DBs, plus `ensure_storage_zone_column()` -- an idempotent `ALTER TABLE`
  migration (mirrors the existing `_add_column_safe` pattern in
  `backend/app/core/db.py`) called at backend startup whenever a library
  root is already configured, so a pre-Cycle-9 `processed.db` backfills
  the column with all existing rows defaulting to `'LIBRARY'` --
  `visibility for a legacy install is unchanged`. This migration is
  intentionally lazy/defensive rather than a one-time forced upgrade:
  `track_service.list_tracks()`/`get_stats()` also call it before applying
  a zone filter, so an un-migrated DB never fails closed to an empty
  result.
  `track_service.list_tracks()`/`get_stats()` gained an optional
  `storage_zone` filter, `None` by default (fully backward compatible for
  any direct server-side caller that doesn't ask for it). The
  `GET /api/tracks` and `GET /api/tracks/stats` HTTP routes now default
  their `zone` query param to `library` (only promoted tracks) --
  matching "the Main Library page shows only promoted tracks by default."
  Because this changes a *shared* HTTP endpoint's default, every existing
  frontend caller was audited: `ApplyToFiles.tsx` and `CrateMind.tsx`
  (Issues/Audit/Enrichment/Folders) now explicitly request `zone=all`
  since write-back verification and issue triage must still reach Inbox
  tracks -- that's exactly where those workflows matter most before
  promotion. `Crates.tsx` was deliberately left on the new `zone=library`
  default: manual crates should only be built from promoted Library
  tracks, matching the target product experience ("Crates workflow can
  see promoted Library track").
  New `frontend/src/pages/Inbox.tsx` (route `/inbox`, added to the
  sidebar's Browse section -- the full Library/DJ/Tools/System nav
  reorganization is Cycle 12 scope, not this cycle) reuses existing shared
  components only (`PageHeader`/`KpiCard`/`EmptyState`/`Badge`/
  `StatusStrip`, the `.card.settings-card.table-scroll` table pattern) --
  no new component patterns. Reviewed with the Impeccable skill
  (`polish` playbook) for Night Deck consistency; fixed a textarea missing
  an associated `<label>` and untruncated long readiness-blocker text in
  the table (new `.inbox-page .badge` truncation rule mirroring the
  existing `.quality-review-option .badge` pattern, plus a `title`
  tooltip with the full blocker list).
  New API (`backend/app/api/routes/workspace.py`):
  `GET /api/workspace/status`, `POST /api/workspace/configure`,
  `POST /api/workspace/import` (`confirm=true` required),
  `GET /api/workspace/inbox/tracks`,
  `POST /api/workspace/promotion/preview`,
  `POST /api/workspace/promotion/apply` (`confirm=true` required).
  1500 backend tests pass (36 new: `tests/test_workspace_service.py`,
  `tests/test_track_service_storage_zone.py`, plus a route-contract
  addition in `tests/test_supported_route_contracts.py`); frontend
  typecheck/build pass; `git diff --check` clean.
  **Live end-to-end verification**, against disposable fixtures only
  (external source + managed root both under `/tmp`, never the sanctioned
  library): configured a managed workspace through
  `workspace_service.configure_workspace()`, imported a nested external
  folder (one top-level file + one file in a nested subfolder) through
  the real running UI, confirmed both external originals were SHA-256
  byte-identical before and after import, watched the real readiness gate
  correctly block first on missing genre and then -- after setting genre
  but before the file's actual ID3 tags matched -- on "approved metadata
  has not been written back to the file yet" (using a real
  `ffmpeg`-generated silent MP3 fixture and real `mutagen` ID3 tag
  writes, not mocks), then promoted both tracks through the real "Move
  Ready to Library" two-step confirmation and confirmed the physical
  result: `Library/House/<Artist>/<Artist> - <Title>.mp3` on disk, Inbox
  directory empty, the DB `storage_zone` column updated to `LIBRARY`, the
  main Library page (default zone) showing both tracks with the correct
  path in the Track Inspector, and `GET /api/tracks?zone=inbox`
  correctly empty afterward.
  **Noted side effect, disclosed rather than hidden**: this verification
  required briefly restarting the local dev services against the
  disposable root, then restarting them back against the sanctioned
  `~/Music/crateiq-test-library` to match the pre-session state. That
  restart triggered the startup `ensure_storage_zone_column()` migration
  against the sanctioned library's real `processed.db` -- confirmed via
  direct inspection: all 88 existing rows now have
  `storage_zone='LIBRARY'` (no visibility change), no row's other values
  changed, and all 88 sanctioned audio files were confirmed present
  (file-count check) both before and after with no `Inbox`/`Library`/
  `Quarantine` directories or workspace marker created in that root
  (it remains a `legacy_direct_library`, exactly as designed).
  **Known gaps carried to later cycles** (see `NEXT_TASKS.txt`):
  `/api/library/overview` and `/api/library/quality` (the Library page's
  KPI cards) are not yet `storage_zone`-aware and still aggregate across
  all zones -- pre-existing behavior, not a regression, but worth
  reconciling once Inbox tracks are common. Inbox has no inline metadata
  editor yet; fixing genre/artist/title on an Inbox track currently
  requires routing to Metadata Repair/Sanitation/Apply to Files (all of
  which now see Inbox tracks via `zone=all`).

- 2026-08-08: Pre-merge hardening pass on `feat/crateiq-core-usability`
  (base Cycle 8, this file's previous entry), no merge to main. Three
  fixes, the first two closing gaps this file already flagged:
  1. **Closed the Cycle 7 near-miss documented below.**
     `library_setup_service._target_root()` no longer falls back to
     `settings_service._pending_library_root()` when a mutating call
     (`initialize_library`, `scan_preview`, `import_previewed_library`)
     omits an explicit `library_root` -- it now always resolves to
     `selected_library_root()` (the same canonical source
     `tag_write_service` and most other services already use) and raises
     if none is configured, rather than silently substituting whatever
     root happens to be staged in `.run/local/crateiq.env`. Regression
     tests in `tests/test_library_setup_service.py` reproduce the exact
     scenario: pending root -> library A, env-selected root -> library B,
     omitted-argument call must act on B only.
  2. **Closed the `beet` CLI risk this file's Cycle 6 entry flagged as a
     hard rule.** `modules/organizer.py`'s `_run_beets()` was still
     shelling out to the real `beet` binary via `subprocess.run()` by
     default (only `--skip-beets` avoided it) -- a real violation of
     "never invoke the `beet` CLI binary from CrateIQ code" hiding in the
     legacy pipeline organizer, distinct from the sanctioned Beets Python
     API usage in `musicbrainz_client.py`. It now always uses the
     existing pure-Python fallback organizer; `--skip-beets` is accepted
     for CLI compatibility but is a no-op. Added
     `tests/test_no_beet_cli_invocation.py`, a static AST-based regression
     guard scanning all production Python for any subprocess/os.system/
     shell call referencing "beet", verified against a planted bad
     snippet so the detector itself is tested.
  3. **Found live during a real disposable-library browser walkthrough**
     (Library Prep: Import -> Clean metadata -> Enrich & review, with
     real Beets and real MusicBrainz per-track lookups -> Apply to Files
     -> Analyze -> Ready -> Manual/Smart Crates continuation, at
     `http://127.0.0.1:5175` against a 6-track disposable copy under
     `/tmp`, never the sanctioned library): every real UI-driven
     Apply-to-Files write failed as a false "File changed since preview
     -- stale plan blocked", even immediately after a fresh preview.
     Root cause: `expected_mtime_ns` is a nanosecond epoch timestamp
     (~1.8x10^18), which exceeds JavaScript's `Number.MAX_SAFE_INTEGER`
     (2^53); round-tripping it as a JSON *number* through the browser
     silently corrupts the value (confirmed via `Number.isSafeInteger`
     and a direct `curl` vs. browser comparison), so the apply-time
     staleness check in `tag_write_service.apply_plan()` always failed --
     a genuine defect blocking the single highest-risk step in the whole
     app from ever working through the real UI. Fixed by serializing
     `expected_mtime_ns` as a JSON string end-to-end (Pydantic coerces it
     back to a full-precision int on the way in; `apply_plan()` now
     `int()`-coerces defensively regardless of caller type, so direct
     Python callers in tests are unaffected). Verified against the live
     disposable fixture afterward: preview -> confirm -> write -> restore
     all succeeded, byte-for-byte SHA-256-identical to the pre-write
     backup. Also fixed a confirmed-live, non-blocking UX gap found in
     the same walkthrough: Enrichment Review's confirm checkbox was
     silently disabled with no explanation until "Save selection" was
     clicked first (caused three failed attempts firsthand); added an
     inline hint.
  Real Beets and MusicBrainz lookups both confirmed live (network calls
  measured at 6.4s and 0.8s respectively via backend request logs, not
  mocked). No original sanctioned file or its `processed.db` was
  touched (confirmed via mtime check spanning the whole session). 1474
  backend tests pass (6 new); frontend typecheck/build pass;
  `pipeline.py validate-docs --strict` passes. True responsive
  verification at 1440/760/390px could not run in this session -- the
  sandboxed browser's display is fixed at 1280x800 and `resize_window`
  silently no-ops beyond it (confirmed via `window.innerWidth`/
  `screen.width` after the call); the rest of the walkthrough ran at the
  environment's native 1280px.

- 2026-08-08: Cycle 8 (DJ Preparation) of the crateIQ Core Usability
  Program, on `feat/crateiq-core-usability` (base Cycle 7 `580b0e7`), no
  merge to main. Final cycle: connects the already-built BPM/key analysis
  and waveform generation systems into Library Prep and adds a conservative
  readiness contract -- no new analysis engine, no new job scheduler,
  everything routes through the existing `analysis_jobs_service`/
  `waveform_bulk_service` contracts already used by the Jobs/BpmReview
  pages. New `backend/app/services/library_readiness_service.py`:
  `build_readiness()` composes existing signals only (library overview,
  sanitation/repair pending counts, waveform coverage preview,
  tag_write_operations failure history) into BLOCKER / WARNING / OPTIONAL
  reason codes, never recomputing anything itself. Blockers: no tracks
  imported, missing required artist/title, any failed/partially-failed
  write-back operation. Warnings: unresolved sanitation/repair review,
  missing BPM/key coverage, missing waveform coverage. Optional: missing
  genre. `ready = (blockers.length === 0)` -- warnings and optional items
  never block progress, matching the orchestrator's "not every item has to
  be a blocker" guidance. New `GET /api/library/readiness` route (in
  `library.py`, alongside the existing `/library/quality` it reuses
  pieces of). Frontend: `LibraryPrep.tsx` steps 5 (Analyze) and 6 (Ready,
  renumbered after a step-3/4 merge, see below) are now genuinely
  functional instead of "unavailable" placeholders -- Analyze has three
  inline buttons (Analyze missing BPM/key, Generate missing waveforms)
  each showing a live "(N pending)" count and calling the same
  preview/run contracts the dedicated Jobs/BpmReview pages already use;
  Ready fetches the real readiness contract and shows blocker/warning
  StatusStrips (capped at 4 each, "+N more" beyond that) plus "Continue
  to Manual Crates"/"Continue to Smart Crates" links.
  **Also fixed in this cycle** (found during the final full-workflow
  Impeccable pass, not new scope): steps 3 ("Enrich") and 4 ("Review
  candidates") were stale leftovers from Cycle 5 still claiming
  Beets/MusicBrainz "land in the next cycle" -- false since Cycle 6
  shipped them. Merged into one step ("Enrich & review candidates",
  since step 4 had no distinct action of its own, just a description of
  step 3's same page) and updated the copy to describe the real
  Enrichment Review online-lookup buttons. This also resolved a
  leftover design inconsistency: the `unavailable`/locked step state
  (dimmed chrome + Lock icon) is no longer reachable from any step now
  that every step is genuinely functional, so the state value, its two
  CSS rules, and the unused `Lock` import were removed rather than left
  as dead code.
  **Final end-to-end acceptance**, chaining the full journey in one
  script against a fresh disposable copy library (never the originals):
  configure/import (2 tracks) -> tracks appear in local index ->
  sanitation detects a real seeded junk-artist token
  (`source_token_removed`) -> cleanup approved and applied to the local
  index -> real Beets lookup (5 distance-scored candidates) -> real
  MusicBrainz lookup (5 search candidates) -> field-by-field comparison
  via `enrichment_review_service.online_lookup` -> exact write plan (1
  replacement) -> byte-for-byte backup -> confirmed write -> re-read
  verification -> restore proven byte-identical -> real BPM analysis
  launched and completed (`runner_implemented=True`, 2/2 analyzed) ->
  key analysis and waveform generation previewed (launch path
  exercised; not run to completion in this bounded script, since both
  already have dedicated full acceptance elsewhere: Cycle 8's own
  compact run above and the pre-existing waveform-jobs branch history)
  -> readiness correctly reported `ready=true` with two truthful
  warnings (missing key coverage, missing waveform coverage) -> the
  same crates links Ready already exposes. All temporary artifacts (the
  disposable library, the real backup directory this run created, and
  its `tag_write_operations` row) cleaned up afterward. All 88 original
  sanctioned audio files confirmed byte-unchanged (count + mtime check)
  before and after this entire cycle, and after the whole 4-cycle
  program.
  1468 backend tests pass (3 new in `tests/test_backend_api.py` for the
  readiness endpoint); frontend typecheck/build pass;
  `pipeline.py validate-docs --strict` passes (24/24 registry commands
  present). One Impeccable pass (see "also fixed" above) plus a
  standard count-display-consistency fix (Analyze step buttons now use
  the same "(N pending)" convention as the Clean metadata step) and a
  cap on the Ready step's rendered blocker/warning list (4 each, "+N
  more" beyond that, to avoid a wall of stacked strips on a messy
  library). Live browser verification at 1440/760/390px could not run
  in this session (no Chrome extension connected, consistent with
  Cycles 5-7) -- verified via code/CSS review, the detector-equivalent
  Impeccable pass, and the real end-to-end acceptance script instead.

- 2026-08-08: Cycle 7 (Controlled Metadata Write-Back) of the crateIQ Core
  Usability Program, on `feat/crateiq-core-usability` (base Cycle 6
  `c48dd5e`), no merge to main. The highest-risk cycle: real writes to
  actual audio file tags, guarded end to end. New
  `backend/app/services/tag_write_service.py`: `build_plan()` (read-only
  exact diff between the local index's approved artist/title/album/genre
  and the file's current embedded tags -- ADD when the file field is
  empty, REPLACE when it differs, nothing when they already match),
  `apply_plan()` (revalidate fresh size+mtime_ns against what the client's
  own prior preview reported -- blocks as stale if the file changed
  underneath the plan, never silently rebases -- then byte-for-byte
  hash-verified backup, then mutagen easy-tag write of *only* the diffed
  fields, then re-read and verify), `restore_file()` (atomic
  copy-to-temp-then-rename restore of one file from its recorded backup,
  hash-verified both before and after). Write surface is deliberately
  four fields only -- artist, title, album, genre, exactly what the local
  index already models reliably through Cycles 5-6 -- never BPM, key,
  Camelot, cues, artwork, or any other tag; only MP3/FLAC are supported,
  every other format is an explicit blocker in the preview, never a
  silent skip. New `tag_write_operations` jobs.db table (mirrors
  analysis_operations/publish_operations/waveform_operations, extended
  with `restored` as a distinct terminal status) plus restart recovery
  (`tag_write_service.recover_interrupted_operations()`, wired in
  `main.py` alongside the others). New `TAG_WRITE_BACKUP_DIR =
  backend/data/tag_write_backups/` (backend/app/core/config.py) -- outside
  any possible scanned library root by construction, since
  `settings_service._forbidden_library_roots()` already blocks the whole
  repo tree from ever being selected as a library root. New routes: `POST
  /api/tag-write/plan`, `POST /api/tag-write/apply`, `GET
  /api/tag-write/operations[/{id}]`, `POST /api/tag-write/operations/{id}
  /restore/{track_id}`. New frontend page
  `frontend/src/pages/ApplyToFiles.tsx` (route `/apply-to-files`, sidebar
  entry, and Library Prep step 5 now links to it instead of showing
  "unavailable"): select tracks -> preview exact plan (FIELD/CURRENT
  FILE VALUE/APPROVED VALUE/ACTION table, blocked-track warnings shown
  before the confirm gate) -> explicit confirm checkbox -> backup/write/
  verify -> operations history with a per-track restore action.
  **Incident-free this cycle in the write-back code itself, but a real
  near-miss during manual acceptance scripting**: a hand-written
  acceptance script called `library_setup_service.initialize_library()`/
  `import_previewed_library()` without an explicit `library_root`
  argument; those two Cycle-5 functions fall back to
  `settings_service._pending_library_root()` (a *file*,
  `.run/local/crateiq.env`) before the `CRATEIQ_LIBRARY_ROOT` env var when
  no explicit root is passed, and that file already pointed at the real
  sanctioned library -- so the script's first run silently
  scanned/imported against the real `crateiq-test-library/logs/
  processed.db` instead of the intended disposable temp copy. Confirmed
  harmless (the only write was a no-op `UPDATE ... SET filename =
  excluded.filename, filesize_bytes = excluded.filesize_bytes` on values
  that were already correct; artist/title/genre are never touched by that
  conflict clause; all 88 sanctioned audio files confirmed byte-unchanged
  throughout). Fixed by passing `library_root` explicitly in the script
  and re-running clean. Documented here because the same trap exists for
  any future script/tooling that calls those two functions with no
  explicit root -- `backend.app.core.library_root.selected_library_root()`
  (used by `tag_write_service` and most other services) has no such
  pending-root fallback and only reads the env var, so this is specific
  to `library_setup_service`'s two entry points.
  **Real acceptance, disposable copies only**: two real files copied
  (never moved) out of the sanctioned library into a temp directory,
  given deliberately messy embedded tags, then imported, "approved"
  (simulating a completed sanitation/enrichment review), planned, backed
  up, written, re-read-verified, and one restored -- through the actual
  `library_setup_service`/`tag_write_service` functions, backups landing
  in the real `backend/data/tag_write_backups/` path (proving the real
  backup store, not a mock), all cleaned up afterward. All 88 original
  sanctioned audio files confirmed byte-unchanged (mtime + count check)
  before and after. 1465 backend tests pass (10 new: 9 in
  `tests/test_tag_write_service.py` using real ffmpeg-generated MP3/FLAC
  fixtures -- real mutagen read/write/backup/restore round trips, not
  mocks -- plus 1 new HTTP-level round-trip test in
  `tests/test_backend_api.py`); frontend typecheck/build pass. One
  Impeccable pass on `ApplyToFiles.tsx` (the only file-mutating page in
  the app) found and fixed: the real "Backup, write, and verify" button
  was styled identically to the harmless "Preview write plan" button
  (now uses the app's existing `.btn--danger` treatment, matching how
  other destructive actions like crate/job deletion are styled elsewhere
  -- DESIGN.md: "don't obscure write boundaries behind friendly labels or
  visual polish"), the error banner was missing `role="alert"` present on
  equivalent banners in Publish.tsx/Reconciliation.tsx, and
  preview/apply/restore lacked an in-function re-entrancy guard beyond
  the disabled prop (added `if (busy !== null) return`).

- 2026-08-08: Cycle 6 (Real Enrichment) of the crateIQ Core Usability
  Program, on `feat/crateiq-core-usability` (base Cycle 5 `ae6dae7`), no
  merge to main. Added real, verified-live Beets and MusicBrainz metadata
  enrichment, built on the existing multi-source Enrichment Review
  foundation rather than a parallel tool. New `beets>=2.13.0` runtime
  dependency (`requirements.txt`); no system-wide install, project `.venv`
  only. New `backend/app/services/musicbrainz_client.py` wraps beets'
  own upstream-tested MusicBrainz HTTP client (compliant User-Agent, 10s
  timeout, bounded retries on transient 5xx/429 only, MusicBrainz's
  required 1 req/sec rate limit) rather than reimplementing an HTTP
  client -- exposes `search_recordings()` (raw MB search, the
  "MusicBrainz" source) and `match_track_candidates()` (beets' own
  distance-scored matching against MB candidates using beets'
  authoritative `strong_rec_thresh`/`medium_rec_thresh` = 0.04/0.25, the
  "Beets" source). Both return a `MusicBrainzError` value instead of
  raising on network/HTTP failure so a lookup failure degrades to a
  warning, never a 500. New `POST /api/enrichment/review/tracks/{id}
  /online-lookup` (body `{"source": "beets"|"musicbrainz"}`) in
  `enrichment_review_service.py`: explicit, single-track, bounded --
  never triggered automatically or library-wide; skips the network call
  entirely (with a truthful warning) when the track has no missing
  allowed field; results are cached in a new `metadata_lookup_cache`
  sqlite table (30-day TTL) so a repeat lookup for the same track/source
  is free; only proposes values for currently-missing fields, matching
  the existing "never overwrite non-empty metadata" invariant unchanged.
  Frontend: `EnrichmentReview.tsx` gained "Look up on Beets"/"Look up on
  MusicBrainz" buttons (distinct cyan-signal `.btn--online-lookup` style,
  spinner during the request) feeding the page's existing generic N-source
  field-comparison table -- no new page, no parallel review tool.
  **Isolation (critical, learned the hard way this cycle):** a real
  `~/.config/beets/library.db` can exist on the machine CrateIQ runs on.
  `musicbrainz_client.py` only ever constructs in-memory `beets.library
  .Item` objects and calls `beets.config.read(user=False, defaults=True)`
  -- it never opens a real beets `Library` and never reads a real user
  config. During adapter development an ad-hoc `beet --version` shell
  invocation (outside the isolated adapter code, a mistake in manual
  testing) opened the real library and ran several schema migrations
  against it; it was restored from beets' own earliest pre-migration
  `.bak` snapshot (integrity-checked, empty tables confirmed matching the
  pre-existing empty library) and the debris backup files were removed.
  **Hard rule going forward: never invoke the `beet` CLI binary from
  CrateIQ code or tooling** -- only the isolated Python API path above.
  Real, live acceptance verified against the running dev backend (not
  just mocked tests): both a Beets distance-matched candidate (distance
  0.4, confidence low, `Jorn - Traveller`) and a MusicBrainz search match
  (score 100, confidence high, `Stripper's Union - Traveller Traveller`)
  were produced for the same real sanctioned-library track (id 88,
  `Traveller.mp3`, missing artist), coexisting for field-by-field
  comparison, cached correctly, proposing only the missing `artist` field
  -- title was left alone. All 88 sanctioned test-library audio files
  confirmed unmodified (mtime check) after this cycle's work; this
  cycle's writes are local-index-only (new snapshot items + cache table),
  identical to Cycle 5's write boundary. Automated tests use mocks only
  (`tests/test_musicbrainz_client.py`, 7 tests; 4 new tests in
  `tests/test_backend_api.py` for the online-lookup route) -- no live
  network call runs in the pytest suite. 1459 backend tests pass; frontend
  typecheck/build pass. One Impeccable critique pass on the new UI found
  and fixed: stale safety copy that flatly denied external API calls
  (now false -- copy corrected to describe the bounded, explicit
  behavior), no visual differentiation between the new network-triggering
  buttons and purely-local buttons (added the cyan-signal
  `.btn--online-lookup` style per DESIGN.md's Signal Color Rule), and a
  static "Looking up…" busy state inconsistent with the app's existing
  `Loader2`/`.spin` convention used everywhere else (fixed). Settings'
  metadata-sources catalog updated: `beets` and `musicbrainz` now report
  `current_behavior: "implemented"` (were `preview_only`/`settings_only`)
  and accurate `connection_status`, reflecting that online-lookup is real,
  not a placeholder -- Spotify/Deezer/Discogs/Beatport/Last.fm remain
  untouched, deferred placeholders as planned (not attempted this cycle,
  per scope).

- 2026-08-08: Cycle 5 (Core Library Workflow) of the crateIQ Core Usability
  Program, on `feat/crateiq-core-usability` (base `main` `4119013`), no
  merge to main. Added a new unified **Library Prep** workspace
  (`frontend/src/pages/LibraryPrep.tsx`, route `/library-prep`, sidebar
  entry between Library and Quality) presenting the full target workflow as
  seven steps (Import, Clean metadata, Enrich, Review candidates, Apply to
  files, Analyze, Ready). Steps 1-2 are fully functional today and reuse
  existing backend contracts unchanged (`/library/scan-preview`,
  `/library/import`, `/api/metadata-sanitation/summary`,
  `/api/metadata-repair/summary`); steps 3-7 are explicit, truthfully-labeled
  "not available yet" placeholders (locked-state styling dims only the step's
  own header chrome, not its body links, so genuinely working pointers to
  `/jobs`, `/crates`, `/smart-crates`, `/beets-review`, `/enrichment-review`
  stay visually live) with no functionality claimed ahead of later cycles.
  Closed a real gap versus the CLI pipeline: the backend's lightweight web
  import (`backend/app/services/library_setup_service.py`) previously indexed
  filename-parsed artist/title only; it now performs a read-only embedded-tag
  lookup via `mutagen.File(..., easy=True)` first (artist/title/album/genre),
  falling back to filename parsing only when tags are absent, and reports a
  new `tags_read_count` in scan/import results. Never writes to source
  files -- read-only `mutagen` lookup only, same dependency already used by
  the CLI pipeline's `modules/tagger.py`/`modules/sanitizer.py`. New test
  file `tests/test_library_setup_service.py` (8 tests: initialization is
  scan-free, scan preview is read-only, embedded tags win over filename
  parsing, filename fallback when tags are absent, idempotent rescans, no
  source-file byte mutation, unreadable-file handling). Added the new route
  to the supported-route smoke contract
  (`tests/test_supported_route_contracts.py`). One Impeccable critique pass
  (dual sub-agent; browser evidence degraded -- no Chrome extension connected
  in this environment, detector scan clean) caught and fixed one real layout
  bug (page wrapper used a nonexistent `page-content` CSS class instead of
  the shared `.page` class -- would have rendered with zero padding), the
  locked-link affordance issue above, a silently-swallowed initial-load API
  failure (now surfaced via a `StatusStrip tone="warn"`), and a duplicate
  Lock icon on the "Apply to files" step. Known, accepted tradeoff (not
  fixed this cycle): Settings.tsx keeps its own separate import
  scan/import UI calling the same underlying API functions --
  intentional reuse of business logic, not a second implementation, but two
  UI entry points exist; consider consolidating in a later cycle. 1444
  backend tests pass; frontend typecheck/build pass. Live browser
  verification at 1440/760/390px could not run -- no Chrome extension was
  connected in this sandboxed session; verified via code/CSS review,
  detector scan, and full type/build checks instead.

- 2026-08-07: Waveform generation UX + bulk waveform jobs, on
  `feat/crateiq-waveform-jobs` (base `main` `9799e04`). Three stages, no
  merge to main. **Stage 1** replaced the decorative LOW/MID/HIGH
  `ThreeBandWaveform` placeholder in Track Inspector and the Persistent
  Player with a new `EmptyWaveform` component (a thin muted center line
  matching the real waveform's own center-line color and the real
  waveform's 46px box height, so swapping never shifts layout) --
  `frontend/src/components/player/EmptyWaveform.tsx`. Lifecycle copy/
  actions unified across both surfaces via `presentWaveformState()`
  ("Waveform not generated", "Waveform generation failed" + Retry).
  `ThreeBandWaveform.tsx` itself is untouched and remains in its one
  other use on the unrelated Music Review (Listening) page -- a noted
  follow-up in `NEXT_TASKS.txt`, deliberately out of scope here.
  **Stage 2** added an explicit bulk "Generate missing waveforms"
  contract that reuses the existing per-track pipeline end to end, no
  second generation system: `GET /api/waveform-bulk/preview` is a
  read-only, truthful count
  (ready/missing/generating/failed/unsupported/eligible_to_generate);
  `POST /api/waveform-bulk/generate-missing` creates a persisted
  `waveform_operations` jobs.db row (new table, mirrors
  `analysis_operations`/`publish_operations`) and fires an async
  fire-and-forget feeder that submits one eligible track at a time
  through `waveform_job_service.submit_generation_job` +
  `WaveformScheduler.enqueue`, polling for that job's terminal state
  before submitting the next -- the existing bounded queue
  (`max_queue_size`) and concurrency (`max_concurrent_jobs`, default 1)
  stay fully authoritative; bulk generation can never enqueue more than
  one job at a time on its own behalf. A track that races to ready or
  already-active between scoping and being reached is re-checked and
  skipped, never resubmitted. Cancellation
  (`POST /api/waveform-bulk/operations/{id}/cancel`) stops scheduling
  new tracks but lets any in-flight job finish so its counts stay
  truthful; final status is always completed/failed/cancelled. A run
  left `running` by a backend restart is reconciled to
  `failed`/`backend_restarted` at startup
  (`waveform_operations_service.recover_interrupted_operations`, wired
  into `main.py` lifespan next to the existing analysis/publish
  recovery calls). New:
  `backend/app/services/waveform_bulk_service.py`,
  `backend/app/services/waveform_operations_service.py`,
  `backend/app/api/routes/waveform_bulk.py`, a `waveform_operations`
  table/migration in `backend/app/core/db.py`, corresponding Pydantic
  schemas, and 35 new tests in `tests/test_waveform_bulk.py` (preview
  truthfulness, bounded scheduling -- never more than one outstanding
  job --, concurrent-skip, cancellation semantics, restart recovery,
  source/processed.db untouched, HTTP contract). **Stage 3** added a
  Waveform Generation card to the Jobs page
  (`frontend/src/components/waveform/WaveformGenerationCard.tsx`,
  mounted in `frontend/src/pages/Jobs.tsx`) reusing existing Jobs/
  Analysis visual patterns (`KpiCard`, `.job-progress-*`, `Badge`,
  `StatusStrip`) rather than a parallel job UI: truthful KPI counts, a
  running-progress state, and a completion/cancellation summary read
  from persisted history so a page reload mid-run still shows real
  state. Ran a focused Impeccable design review (dual sub-agent:
  design-review + detector/screenshot-evidence) afterward and fixed the
  one real finding worth fixing now -- a completed-with-failures run
  showed the same green "Complete" badge as a fully clean run, the same
  anti-pattern `AnalysisOperationsHistory.tsx` already guards against for
  BPM/key runs -- plus a KPI tone/message polish (coral now marks the
  actual Failed state per `DESIGN.md`'s reserved risk color, and the
  Failed tile shows both unsupported-format and retryable counts when
  both are nonzero). `detect.mjs` returned zero findings on all three
  touched files. Verification: `python -m compileall backend/app`
  clean; `python -m pytest -q` -- 1436 passed; `npm run typecheck` and
  `npm run build` both clean; `pipeline.py validate-docs --strict` --
  OK, 24/24 registry commands present; `git diff --check` clean.
  Chrome-in-browser extension was not connected this session; UI
  verification used headless Chrome + a small CDP automation script
  (search/click via `Runtime.evaluate`, screenshots via
  `Page.captureScreenshot`) against the real, sanctioned
  `crateiq-test-library` (88 tracks) instead, disclosed at each stage:
  confirmed the empty state renders with no LOW/MID/HIGH placeholder
  anywhere in Track Inspector or the Persistent Player; explicit
  single-track "Generate waveform" completed and the real
  amplitude-colored waveform replaced the empty state without a reload;
  bulk "Generate missing waveforms" showed real 0/78 progress and
  Cancel stopped scheduling new tracks while letting the in-flight one
  finish (1 generated, 77 remaining, status cancelled); preview counts
  matched the backend exactly at every step. Source-integrity check:
  mtime of all 88 real audio files predates this session (none touched
  today); the two intentional waveform generations performed during
  manual verification wrote only to the app-owned waveform cache and
  jobs.db, never to source audio, tags, or `processed.db`. No files
  changed outside `backend/app/`, `frontend/src/`, and `tests/`.
  LedgerIQ and opsIQ were never referenced. Not merged to main.

- 2026-08-07: Cycle 4 Stage 5 -- final reconciliation safety audit,
  completing roadmap Cycle 4 (Duplicate, Orphan, Quarantine, and
  Plan-First Library Reconciliation) on
  `feat/crateiq-library-reconciliation` (base `main` `b78943b`).
  Full-branch diff vs `main`: 22 files changed, +2,170/-262 across all 4
  stages. Verification: `python -m compileall backend/app` clean;
  `python -m pytest -q` -- 1401 passed; `npm run typecheck` and `npm run
  build` both clean; `pipeline.py validate-docs --strict` -- OK, 24/24
  registry commands present; `git diff --check` clean on the full branch
  diff. Ran a live end-to-end DETECT -> PROPOSE -> VALIDATE pass against
  the real, sanctioned `crateiq-test-library` (88 tracks, 88 disk files,
  library already in sync -- 0 findings, 0 planned actions, 0/0/0
  validation, confirming truthful empty states rather than fabricated
  activity) and confirmed `POST /api/reconciliation/plans/apply` and
  `/api/reconciliation/apply` both 404 -- no apply endpoint exists
  anywhere. Source-integrity check: sha256 of all 88 audio files
  byte-identical before/after the full session (including this stage's
  live propose/validate calls and Stage 4's duplicate-preview refresh);
  `tracks` table row count and bpm/key_camelot/key_musical values
  unchanged; the only writes to the real test library across the whole
  session were the intended, contract-safe ones -- one row in
  `duplicate_review_snapshots` (Stage 1's DB-only review contract) and
  one `logs/path_reconcile/20260807_path_reconcile_plan.json` artifact
  (Stage 3's designed plan-proposal output) -- both are the feature
  working as designed, not incidental mutation. No file was moved,
  renamed, or deleted; no tag, BPM, key, Camelot, or cue value changed;
  no other real music library was touched; LedgerIQ and opsIQ were never
  referenced. Chrome MCP extension was unavailable all session; UI
  verification throughout used headless Chrome screenshots and direct
  API calls instead, disclosed at each stage.
  Cycle 4 summary across all 5 stages: duplicate groups now carry safe
  evidence (genre/bpm/key/duration/format, missing-metadata,
  copy-marker) and an advisory-only keeper recommendation
  (deterministic only when a filename copy-marker unambiguously
  identifies one canonical file, else `insufficient_evidence`); orphan/
  stale-path findings (`indexed_missing_file`, `untracked_file`,
  `stale_path`, `path_candidate`) and a quarantine listing are exposed
  read-only with root-relative paths and symlink-escape rejection;
  `POST /reconciliation/plans/propose` performs DETECT -> PROPOSE by
  reusing the unmodified `pipeline._path_reconcile_plan()` and persists
  to the same artifact location the CLI already used, while
  `validate-plan` gained plan-wide `target_path_collision` /
  `ambiguous_candidate_for_old_path` checks; one real pre-existing bug
  (`old_path_missing_on_disk` wrongly failing every generated
  `update_path_reference` action) was found and fixed while wiring
  propose -> validate together for the first time. `/reconciliation` is
  now one tabbed "Library Reconciliation" workspace over all of it. At
  no point was a filesystem-mutation, auto-apply, auto-keeper-removal,
  or auto-relink capability added -- apply remains fully deferred by
  design, exactly as scoped.

- 2026-08-07: Cycle 4 Stage 4 -- Unified Library Reconciliation UI, on
  `feat/crateiq-library-reconciliation`. `/reconciliation` (sidebar label
  changed from "Ledger" to "Library Reconciliation") became one tabbed
  workspace: Duplicates (a summary card linking to the existing
  `/duplicates` page rather than re-implementing its group browser),
  Missing / Orphaned (`indexed_missing_file` + `stale_path` findings),
  Untracked (`untracked_file` findings), Quarantine (Stage 2's read-only
  listing), and Plans (the pre-existing ledger + validate-plan UI, now
  joined by a "Propose plan" button wired to Stage 3's `POST
  /reconciliation/plans/propose`). No tab has a delete/apply/resolve-all
  control. Also completed Stage 1's deferred UI: `/duplicates` now
  renders the evidence fields (genre/bpm/key/duration/format,
  missing-metadata chips, copy-marker flag) and the advisory keeper
  recommendation, kept visually distinct from the human review decision.
  Implementation extended existing pages/components rather than adding a
  parallel workspace: `frontend/src/pages/Reconciliation.tsx` (tabbed
  restructure), `frontend/src/pages/Duplicates.tsx` (evidence display),
  `frontend/src/types/reconciliation.ts` + `frontend/src/types/
  duplicates.ts` (new response shapes), `frontend/src/api/
  reconciliation.ts` (3 new fetchers), `frontend/src/components/
  Sidebar.tsx` (1 label change), `frontend/src/index.css` (new
  workspace/tab/finding/evidence-chip CSS, following the existing
  per-page-prefixed class convention). `npm run typecheck` and `npm run
  build` both pass. Ran the Impeccable `audit` skill (a11y/performance/
  theming/responsive/implementation-integrity) against the changed
  files -- 0 issues attributable to this stage; its static detector's 4
  findings are all pre-existing, in unrelated Jobs/Collection
  progress-bar CSS, left untouched. The Chrome MCP extension was not
  connected this session, so final visual verification used headless
  Chrome screenshots at ~1440/~760/~390px against the dev server bound
  to the configured `crateiq-test-library`, plus a stderr console-error
  scan (clean apart from pre-existing React Router v7 future-flag
  warnings). Confirmed the persistent sidebar not collapsing below
  ~1440px is a pre-existing, app-wide issue -- reproduced identically on
  the untouched `/jobs` page -- not a regression from this stage, and
  left unfixed as out of scope. No backend change in this stage; no
  file, tag, DB-schema, or destructive action was added.

- 2026-08-07: Cycle 4 Stage 3 -- Plan-first library reconciliation, on
  `feat/crateiq-library-reconciliation`. `POST /api/reconciliation/
  plans/propose` performs DETECT -> PROPOSE by calling the existing,
  unmodified `pipeline._path_audit_report()` and `pipeline.
  _path_reconcile_plan()`, then persists the plan to the exact artifact
  location/filename pattern the CLI already writes
  (`<root>/logs/path_reconcile/{YYYYMMDD}_path_reconcile_plan.json`),
  so the pre-existing `POST /api/reconciliation/validate-plan
  {"latest": true}` keeps working against it unchanged. The API
  response is root-relative-path-only; the on-disk artifact stays in
  the CLI's native absolute-path format since the unmodified validator
  depends on that. `validate-plan` now also runs two plan-wide checks
  the per-action CLI validator structurally can't perform alone:
  `target_path_collision` (two actions proposing the same new_path) and
  `ambiguous_candidate_for_old_path` (one old_path with more than one
  proposed new_path) -- additive-only (can move an action from valid to
  invalid, never the reverse) in a new `reconciliation_plan_service.
  augment_with_cross_action_checks()`. `apply_supported` is `false`
  throughout; no apply/execute endpoint exists; `_path_reconcile_apply_
  auto_safe`/`_path_reconcile_mark_stale_pstate` remain unreachable
  from the API, unmodified. Found and fixed one real pre-existing bug
  while wiring propose -> validate together end-to-end for the first
  time: `_path_reconcile_validate_action` flagged every
  `update_path_reference` action invalid via `old_path_missing_on_disk`
  -- but that action type exists specifically because old_path is
  confirmed missing (that's the whole premise of a rename/relocation
  candidate); no plan the planner itself generates could ever have
  validated as valid before this one-line fix, and no existing test
  asserted on the removed check. New: `backend/app/services/
  reconciliation_plan_service.py`, `backend/app/schemas/
  reconciliation_plan.py`; extended: `backend/app/api/routes/
  reconciliation.py` (one new POST route; existing validate-plan route
  now layers the cross-action check). 12 new targeted tests in
  `tests/test_reconciliation_plan.py`; full backend suite (1401 tests)
  passes. No file, tag, DB-schema, or apply/destructive action was
  added; no new dependency.

- 2026-08-07: Cycle 4 Stage 2 -- Orphan/stale-path/quarantine findings, on
  `feat/crateiq-library-reconciliation`. Two new read-only GET endpoints.
  `/api/reconciliation/findings` reshapes the existing `pipeline.
  _path_audit_report()` CLI engine (disk-vs-DB scan, rename/relocation
  matching) into a bounded, relative-path-only finding contract with four
  types: `indexed_missing_file`, `untracked_file`, `stale_path` (a
  processed-state row superseded by an existing current path), and
  `path_candidate` (an untracked file that may be the renamed/relocated
  version of a missing indexed track). `/api/reconciliation/quarantine`
  lists files under `<library_root>/.BIN/QUARANTINE` read-only; since
  legacy quarantine moves never persisted original location, reason,
  operation id, or timestamp, every item truthfully reports
  `restore_supported: false` and null provenance rather than fabricating
  it. Every exposed path is root-relative and independently re-checked
  with `assert_path_under_root` (rejecting symlink escapes) regardless of
  what the underlying pipeline report already filtered. Finding ids are
  content-derived (sha256 of finding type + path), so ids stay stable
  across repeated calls but a finding legitimately disappears if the
  underlying DB/disk state changes -- the property Stage 3's plan
  staleness check will depend on. New:
  `backend/app/services/reconciliation_findings_service.py`,
  `backend/app/schemas/reconciliation_findings.py`; extended:
  `backend/app/api/routes/reconciliation.py` (two GET routes on the
  existing router, no new router). 8 new targeted tests in
  `tests/test_reconciliation_findings.py`; full backend suite (1389
  tests) passes. No detection logic was reimplemented -- 100% reused from
  `pipeline.py`. No file, tag, DB-schema, or destructive action was
  added; no new dependency.

- 2026-08-07: Cycle 4 Stage 1 -- Duplicate evidence + keeper recommendation,
  on `feat/crateiq-library-reconciliation` (branched from `main` `b78943b`).
  Extends the existing safe, DB-only `/api/duplicates/review` contract
  (unchanged routes/decision states) with richer per-item evidence pulled
  only from already-indexed track data: `genre`, `bpm`, `key_camelot`/
  `key_musical`, `duration_sec`, an extension-derived `format`, a
  `missing_metadata` list, and a `copy_marker` flag. Groups now also carry
  `match_basis` ("content_checksum", since rmlint groups by full-file
  checksum) and a `checksum_prefix`. A new advisory-only `recommendation`
  (`track_id`/`reason_code`/`evidence`) is deterministic only when exactly
  one item's filename lacks a copy-style marker (e.g. `"(1)"`, `"copy"`,
  `"duplicate"`); otherwise it returns `reason_code: insufficient_evidence`
  and `track_id: null`. A recommendation is never authorization to remove
  another item -- it is separate from, and does not gate, the human
  keep/ignore/review_later/unresolved decision. Legacy snapshots saved
  before these fields existed still decode safely to `match_basis:
  "unknown"` and an empty recommendation. Implementation:
  `backend/app/services/analysis_jobs_service.py` (`_track_rows` now also
  selects `duration_sec`; new `_infer_format`, `_missing_metadata_fields`,
  `_looks_like_copy_filename`, `_recommend_keeper` helpers feed
  `_preview_duplicate_detection`), `backend/app/schemas/duplicate_review.py`
  (new `DuplicateKeeperRecommendation` model), `backend/app/services/
  duplicate_review_service.py` (`_safe_groups` sanitizes/passes through the
  new fields with safe defaults for old snapshots). 4 new targeted tests in
  `tests/test_backend_api.py`; full backend suite (1381 tests) passes. No
  file, tag, DB-schema, or destructive action was added; no new dependency.

- 2026-08-07: Cycle 3 Stage 5 -- final publish safety audit, completing
  roadmap Phase 7 (Publish: export and SSD synchronization) on
  `feat/crateiq-publish`. Full-branch diff vs `main` (`bd3a5f6`): 22 files,
  +2,830/-9, scope held tightly to publish/export/sync plus two small
  surgical fixes to pre-existing code (`crate_export_service.
  next_output_path()` extraction, the rsync dry-run header parser bug).
  Found and closed one real test gap: `publish_operations_service.
  recover_interrupted_operations()` (restart safety for a stranded
  'running' export/sync row) had no test, unlike its Cycle 2
  `analysis_operations` counterpart -- added a mirrored test. Re-verified
  source safety: all 88 audio files under `crateiq-test-library/music`
  are byte-identical before/after the entire Cycle 3 session (including
  Stage 4's live browser verification, which produced two real confirmed
  exports); `processed.db`'s mtime predates this session's activity since
  every export/readiness code path opens it read-only. The only real
  writes this session produced are two staged `.m3u8` files under
  `crateiq-test-library/exports/` -- the feature's own intended output
  location. `validate-docs --strict` passes (24/24), `git diff --check`
  is clean, 1378 backend tests pass (1377 + 1 new), frontend typecheck/
  build pass. LedgerIQ/opsIQ untouched; no stray artifacts; other
  Cycle 2/foundation-audit branches and `main` untouched. Not merged to
  `main` per instruction.
  Cycle 3 summary across all 5 stages: `GET /api/publish/readiness/
  {crate_id}` is a read-only contract composing existing crate export
  services and SSD sync config (export_ready/sync_ready, tagged
  blockers/warnings, informational conflicts, confirmation_required,
  next_operation). `publish_export_service.py` unifies the portable/
  Rekordbox-XML/Serato exporters behind validate -> preview -> confirm ->
  execute -> verify without reimplementing any renderer. `publish_sync_
  service.py` does the same for SSD sync on top of the unmodified
  `rsync_runner`, with a request schema that has no `allow_delete` field
  at all and lazy post-job verification via a second safe dry-run.
  Confirmed operations of both kinds persist to a new `publish_
  operations` jobs.db table (Cycle 2-style: running -> terminal status,
  restart recovery, root-relative destinations only). `/publish` is one
  guided frontend workspace over all of it, reusing the Night Deck
  primitives verbatim.

- 2026-08-07: Cycle 3 Stage 4 -- Guided Publish workspace. New `/publish`
  route (`frontend/src/pages/Publish.tsx`) composes Stages 1-3's backend
  contracts into one guided flow: crate + export-format + sync-source
  selection -> readiness (blockers/warnings/conflicts, export_ready/
  sync_ready badges) -> Export preview -> confirm -> verify -> SSD Sync
  preview -> confirm -> live status polling -> verify, plus a "Recent
  publish operations" table. Export and Sync are two separate cards; a
  user can act on Export alone and stop. Every Confirm button is disabled
  until a fresh preview for the *current* selection exists with zero
  blockers -- changing any selector clears stale preview/result state.
  Reuses the Night Deck primitives verbatim (PageHeader, StatusStrip,
  Badge, EmptyState, `.card`, `.lib-defs`, `.table--jobs`,
  `.job-progress-*`); only layout-only `.publish-*` CSS was added.
  Impeccable's focused review (detector clean; inline design pass) fixed
  two real issues: "blocked" readiness badges used a neutral tone
  (changed to `failed`), and the running-sync state didn't reuse the
  existing job-progress-bar pattern (now added). Live verification
  (Chrome extension unavailable -- used a scripted headless-Chrome +
  DevTools-Protocol session against the real `crateiq-test-library`
  backend/frontend) found and fixed a real bug the unit suite structurally
  could not catch: any `jobs.db` created before this session's Stage 3
  change (including the long-running dev backend used for verification)
  had `publish_operations` without the new `job_id` column, since `CREATE
  TABLE IF NOT EXISTS` never alters an existing table -- every confirmed
  export hit a raw 500. Fixed with the same `_add_column_safe()` additive
  migration pattern already used for every other jobs.db column, plus a
  regression test reproducing the exact pre-migration table shape. After
  the fix, a real confirmed M3U8 export against the "test" crate wrote and
  verified successfully end-to-end through the actual browser UI at
  1440/760/390px with no console errors; SSD Sync correctly showed
  BLOCKED (no real SSD mounted on this machine) and Preview sync completed
  in ~2ms, confirming rsync was never spawned against a blocked
  destination. `/publish` was also registered in
  `tests/test_supported_route_contracts.py` (read-only `/api/publish/
  operations` in the smoke contract; the three mutating endpoints in
  DEFERRED_ENDPOINTS; ID-scoped GETs untracked, consistent with existing
  precedent). Files: `frontend/src/pages/Publish.tsx`, `frontend/src/api/
  publish.ts`, `frontend/src/types/publish.ts`, `frontend/src/App.tsx`,
  `frontend/src/components/Sidebar.tsx`, `frontend/src/index.css`,
  `backend/app/core/db.py`, `tests/test_backend_api.py`, `tests/
  test_supported_route_contracts.py`. 1377 backend tests pass (1376 + 1
  new), frontend typecheck/build/`git diff --check` pass. No source
  audio/tag/BPM/key/cue/MIK data touched; no live Rekordbox/Serato/SSD
  write; the only real writes were verification exports into
  `crateiq-test-library`'s own `exports/` directory. Stage 5 (final
  safety audit) is next.

- 2026-08-07: Cycle 3 Stage 3 -- Guarded SSD sync workflow.
  `publish_sync_service.py` layers validate -> preview (dry-run) ->
  confirm -> execute -> verify on the existing, unmodified
  `rsync_runner.preview_sync`/`start_sync_job`. `POST
  /api/publish/sync/preview` never spawns rsync when the destination
  already fails structural safety (same-path, nested/ancestor,
  protected system path, unmounted); `POST /api/publish/sync/confirm`
  requires `confirm: true` and its request schema has no `allow_delete`
  field at all, so the guided flow cannot request destructive rsync
  `--delete` semantics even in principle -- it always calls
  `start_sync_job(..., allow_delete=False)`. `GET
  /api/publish/sync/{operation_id}` lazily verifies exactly once after
  the underlying job reaches a terminal state, by re-running the same
  safe dry-run preview: zero pending transfers means verified, anything
  else is a verification failure reported separately from job execution
  failure. New `publish_safety.evaluate_sync_paths()` merges the
  mount/existence checks with Stage 1's `describe_sync_destination_
  safety()`; `publish_readiness_service.py` now calls it instead of
  duplicating the same checks. Found and fixed a real pre-existing bug:
  `rsync_runner._parse_dry_run_output()` only matched rsync's default
  header, never the "building file list ... done" header rsync actually
  prints when `--no-inc-recursive` is passed (which `preview_sync()`
  always does) -- so every dry-run preview, old and new, silently
  reported zero pending files regardless of reality. Confirmed sync
  operations share Stage 2's `publish_operations` table via a new
  `job_id` linkage column; destination is stored only as
  `external_ssd:<source>`, never an absolute path. Files:
  `backend/app/services/publish_sync_service.py`, `backend/app/
  services/publish_safety.py`, `backend/app/services/
  publish_readiness_service.py`, `backend/app/services/
  publish_operations_service.py`, `backend/app/services/rsync_runner.py`,
  `backend/app/core/db.py`, `backend/app/schemas/publish.py`,
  `backend/app/api/routes/publish.py`, `tests/test_backend_api.py`. 1376
  backend tests pass (1365 baseline + 11 new). No real external SSD used
  in any test; no `--delete` ever issued by the guarded flow; fixture
  source files confirmed byte-identical after every sync. Stage 4
  (Guided Publish UI) is next.

- 2026-08-07: Cycle 3 Stage 2 -- Guarded publish export flow.
  `publish_export_service.py` orchestrates the existing crate exporters
  (portable CSV/JSON/M3U/M3U8 via `crate_export_service`, staged
  Rekordbox XML, staged Serato handoff) behind one explicit validate ->
  preview -> confirm -> execute -> verify lifecycle, reusing every
  existing renderer/no-overwrite-naming implementation unchanged.
  `GET /api/publish/export/{crate_id}/preview` never creates the exports
  directory; `POST /api/publish/export/{crate_id}` requires
  `confirm: true` (else 409) and re-checks blockers immediately before
  writing (empty crate -> 409). Confirmed writes are recorded in a new
  `publish_operations` jobs.db table (mirrors Cycle 2's
  `analysis_operations`: running -> terminal status, restart-recovery,
  root-relative destination only, never an absolute path) and verified
  afterward -- portable formats check file existence/shape, Rekordbox
  checks `COLLECTION Entries` against track_count, Serato checks both
  staged files and the manifest's track_count. Verification failure is
  reported separately from execution failure, never silently upgraded to
  success. `crate_export_service.write()`'s inline naming loop was
  factored into a pure `next_output_path()` (identical behavior; all
  pre-existing crate/serato/rekordbox export tests still pass unchanged)
  so preview can disclose the same path write() would use. Files:
  `backend/app/services/publish_export_service.py`,
  `backend/app/services/publish_operations_service.py`,
  `backend/app/services/crate_export_service.py`, `backend/app/core/
  db.py`, `backend/app/schemas/publish.py`, `backend/app/api/routes/
  publish.py`, `backend/app/main.py`, `tests/test_backend_api.py`. 1365
  backend tests pass (1355 baseline + 10 new). No source audio, tag,
  BPM/key/cue, or MIK data touched; no live Rekordbox/Serato database
  written. Stage 3 (guarded SSD sync) is next.

- 2026-08-07: Cycle 3 Stage 1 -- Publish readiness contract (roadmap
  Phase 7 begins). New `GET /api/publish/readiness/{crate_id}` is a
  read-only snapshot composing the existing crate export services
  (portable CSV/JSON/M3U/M3U8, staged Rekordbox XML, staged Serato
  handoff) and the existing SSD sync config (`SYNC_SOURCE_MAP`,
  `SYNC_DEST_SSD`) into one truthful contract: `export_ready`,
  `sync_ready`, tagged `[export]`/`[sync]` blockers/warnings,
  informational `conflicts` (pre-existing export artifacts -- never
  blocking, since crate export writers already use no-overwrite
  timestamped naming), `confirmation_required`, and `next_operation`.
  It never exports or syncs anything itself -- existing preview/write
  endpoints are untouched. New `publish_safety.describe_sync_destination_
  safety()` is a pure helper (same path, nested/ancestor, protected
  system path) shared with the later guarded sync lifecycle. Files:
  `backend/app/schemas/publish.py`, `backend/app/services/
  publish_safety.py`, `backend/app/services/publish_readiness_service.py`,
  `backend/app/api/routes/publish.py`, `backend/app/main.py`,
  `tests/test_backend_api.py`. 1355 backend tests pass (1347 baseline +
  8 new: ready, missing source, invalid/missing sync destination,
  destination outside allowed scope, existing output conflict,
  unsupported format, missing crate, no side effects). Stage 2 (guarded
  export preview/execute/verify) is next.

- 2026-08-07: Cycle 2 Stage 3 -- Operations UI. `/jobs`'s "Analysis
  history" section (previously always an empty-state placeholder) now
  renders Stage 1/2's persisted operations: a keyboard-selectable table +
  detail rail with truthful counts, a real (not fabricated) progress bar
  only while genuinely running, and Cancel only while running. A
  "Completed" run with any per-track failure now gets an amber badge
  instead of plain green so it can't visually read as fully clean. Reuses
  existing components/CSS (`.table--jobs`, `Badge`, `.lib-defs`,
  `.job-progress-*`) rather than inventing new ones; collapses to one
  column below 1180px like the Library page. Verified via headless-Chrome
  screenshots at 1440/760/390px (Chrome extension unavailable); the
  click-to-select detail panel was verified by code review + direct API
  testing rather than a live click, disclosed as a real gap. Cycle 2's
  three stages (persist -> API -> UI) are now complete for BPM/key
  analysis operations.

- 2026-08-07: Cycle 2 Stage 2 -- structured Analysis Jobs API. `GET
  /api/analysis/jobs/history/{operation_id}` (detail) and `POST
  .../history/{operation_id}/cancel` (idempotent: unknown id -> 404,
  already-terminal/already-flagged -> current record, never an error) sit
  on top of Stage 1's persistence. Confirmed run responses now carry
  `operation_id`/`cancelled`. Fixed a real concurrency bug the
  cancellation tests exposed: the run route awaited the blocking BPM/key
  runner directly, holding the event loop for the whole batch so a
  concurrent cancel genuinely could not be serviced; it now dispatches via
  `run_in_threadpool`. 1347 backend tests pass (1343 baseline + 4 new).
  Stage 3 (Operations UI) is next.

- 2026-08-07: Cycle 2 Stage 1 -- persisted Analysis Jobs history. New
  app-owned `analysis_operations` table in the backend's own jobs.db
  (processed.db untouched) records every explicit, confirmed BPM/key
  analysis run with a real running/completed/failed/cancelled lifecycle,
  truthful incremental progress counts, and genuine mid-batch cancellation
  via a polled `cancel_requested` flag (partial progress preserved, never
  fabricated). A backend restart closes out any stranded 'running' row as
  failed/backend_restarted, mirroring the existing waveform-job recovery
  pattern. Candidate previews remain intentionally unpersisted. Along the
  way, fixed a real pre-existing gap: the shared backend-API test fixture
  never isolated jobs.db, so tests were silently sharing (and could
  pollute) a developer's real `backend/data/jobs.db`; it now isolates a
  per-test path and initializes schema directly. `GET
  /api/analysis/jobs/history` returns real data instead of an always-empty
  stub. 1343 backend tests pass (1331 baseline + 12 new). Stage 2 (HTTP
  operation-detail + cancel endpoints) and Stage 3 (Operations UI) follow
  in the same cycle.

- 2026-08-07: Crate/set-building improvements (Stage 4). Manual Crates now
  show a deterministic, explainable harmonic+BPM read between each
  consecutive track pair (`CrateTrack.transition_to_next`: label
  smooth/workable/clash/unknown, camelot/bpm sub-scores, signed BPM delta,
  and a short explanation) computed in `crate_service.get_crate()` from the
  existing public `modules/harmonic.py` helpers (`camelot_score`,
  `bpm_score`, `camelot_distance`, `bpm_delta_pct`); missing key/BPM data on
  either side degrades to "unknown" rather than a fabricated score. This is
  read-only annotation — it never reorders a crate or changes eligibility.
  Smart Crates now return a `funnel` (ordered `[{label, remaining}]` stages:
  Library → BPM range → Genre → Issue-free → Harmonic match → Shown) so a
  DJ can see which filter narrowed the candidate pool and by how much;
  `preview()` was restructured from one combined filter loop into
  sequential passes to compute this without changing which tracks match
  (same AND-composed filters, same final ranking/scoring). Frontend:
  `Crates.tsx` renders a small connector row between track rows with a tone
  badge + explanation; `SmartCrates.tsx` renders the funnel as a compact
  pill chain above the preview table. An unrelated, functionally-inert
  uncommitted edit to `usePersistentPlayer.ts` found at Stage 4 start
  (inlining `AudioPreviewTrack`'s fields instead of extending it — same
  resulting shape, confirmed via typecheck with/without) was reverted since
  it had no concrete Stage 4 requirement and touched persistent-player
  behavior outside this task's scope.
  Files: `backend/app/schemas/crate.py`, `backend/app/services/
  crate_service.py`, `backend/app/schemas/smart_crate.py`, `backend/app/
  services/smart_crate_service.py`, `frontend/src/types/crate.ts`,
  `frontend/src/types/smartCrate.ts`, `frontend/src/pages/Crates.tsx`,
  `frontend/src/pages/SmartCrates.tsx`, `frontend/src/index.css`,
  `tests/test_backend_api.py`. 1331 backend tests pass (1329 baseline + 2
  new targeted tests covering the transition/funnel contract), frontend
  typecheck/build pass, `git diff --check` clean. Verified against the real
  `crateiq-test-library` backend (live API calls, not just unit fixtures) —
  a real 9B→9A/122→123 BPM pair correctly scored "smooth" and a real BPM
  funnel correctly narrowed 88 → 88 → 5 (shown). Live Chrome UI
  verification not performed — extension unavailable this session. No
  source audio, tags, BPM/key/cue data, or Music Review state changed.

- 2026-08-06: Advanced the local-only enrichment review foundation into a
  genuine multi-source comparison. Added a second candidate source,
  `local_tags` (reads a track's own embedded ID3/Vorbis/MP4 tags read-only
  via the existing `modules.metadata_clean._read_tags`; no new dependency,
  no tag writes), alongside the existing `filename_hints` source, so a
  track can surface two independently-sourced, potentially-differing
  candidates for the same field. No real external provider was called —
  Spotify/Deezer/Discogs/MusicBrainz/etc. remain settings-only/planned.
  `apply_selected`'s existing per-field never-overwrite check already made
  same-field conflicts between sources safe without any change. Frontend
  `EnrichmentReview.tsx` detail panel is now a Current/Source-A/Source-B
  comparison table (CSS `display:table` for a variable column count); only
  the selected suggestion's column is editable, reusing the existing
  single-suggestion PATCH/apply flow unchanged. Files:
  `enrichment_review_service.py`, `EnrichmentReview.tsx`, `index.css`,
  `test_backend_api.py`. 1329 backend tests pass (1327 baseline + 2 new
  targeted tests: provenance/comparison/conflict-safety/persistence/
  missing-file degrade), typecheck/build pass, `git diff --check` clean.
  Live Chrome verification not performed — extension unavailable.

- 2026-08-06: Library/Track Inspector polish pass (code+CSS audit; Chrome
  MCP unavailable this session). Fixed a real player-overlap bug (sticky
  `.lib-inspector` max-height now subtracts the fixed persistent player's
  height above 1180px width), reduced duplicate-looking status UI (Library
  status strip vs. runtime readiness strip no longer share the same
  AlertOctagon+rose treatment for their worst state), removed the dead
  always-disabled "Save filter" pill, and added aria-label/aria-expanded to
  two icon-only buttons that only had `title`. Files: `LibraryView.tsx`,
  `LibraryToolbar.tsx`, `LibraryFilters.tsx`, `index.css`. typecheck/build/
  `git diff --check` pass; live Chrome verification not performed.

- 2026-08-06: Replaced the Track Inspector's decorative "Three-band signal
  preview" with the real-waveform lifecycle (`useTrackWaveform` +
  `TrackWaveform`, same as the persistent player), keeping `ThreeBandWaveform`
  only as the non-ready fallback. Generation stays explicit-only (inherited
  from the existing hook, no new POST paths). Display-only in the inspector —
  no new `<audio>` element or seek control; the persistent player remains
  playback authority. Files: `TrackInspector.tsx`, `index.css`. typecheck/
  build/`git diff --check` pass; live Chrome verification not run this
  session (extension unavailable).

- 2026-08-06: Completed Waveform Phase W8, the final documentation/safety/
  merge-readiness audit. **No production code changed.** Source-level,
  non-generative audit of the full persistent-player + waveform arc
  (`988ac08..254c688`, 16 commits, 62 files, +15,011/-106) — this repository
  has no `main` branch; `feat/crateiq-foundation-audit` is itself the
  project's single/main branch per its own git remote state. Verified from
  source: every cache delete traces through one containment gate
  (`assert_waveform_cleanup_candidate`) fed only by a walk of the cache root
  itself, never a track filename; FFmpeg/ffprobe execution has exactly one
  entry point (`waveform_process.py`) with zero `shell=True` anywhere in
  `backend/app`; path validation is one symlink-resolving implementation
  (`track_source_service.validated_track_source`) shared by every consumer;
  `synchronous=NORMAL` is hardcoded to `jobs.db` only, `processed.db` opens
  `mode=ro` elsewhere and has zero diff in the arc; `generation_key` vs
  `source_sha256` naming is unambiguous and the latter is never written;
  every waveform API route takes only `track_id`/`job_id`, never a path;
  frontend `generate()` is wired to exactly one `onClick`, exactly one seek
  slider and one app-wide `<audio>` element exist; no waveform log line
  carries a path; no dependency file changed anywhere in the arc; no secret,
  binary, media file, or hardcoded `/home/paak` path exists in the diff.
  `validate-docs --strict` fails on 6 stale `COMMANDS.txt` entries, confirmed
  **pre-existing** by reproducing it in an isolated worktree at the arc's
  base commit — neither `COMMANDS.txt` nor `pipeline.py` has any diff in the
  arc — classified as an unrelated repository issue, not a branch blocker.
  1327 backend tests (402 waveform) pass, frontend typecheck and build pass,
  `git diff --check` clean. **Decision: GO — ready to merge after normal
  human review.** No blockers; six items classified NON-BLOCKING FOLLOW-UP
  or OPTIONAL (see NEXT_TASKS.txt). Not merged; branch left as-is per
  instruction.

- 2026-08-06: Completed Waveform Phase W7, the first controlled real-media
  end-to-end verification. Verification only — **no production code changed**;
  the 1327-test W6 baseline still passes and the engine ADR was not reopened.
  Ran the full pipeline against 7 of the 88 files in the explicitly configured
  isolated test library (5 MP3 + 2 FLAC: shortest, longest, Unicode filename,
  apostrophe filename, both FLACs); the other 81 were never opened. FFmpeg /
  ffprobe 6.1.1. Real-time factor 108x-178x; artifacts 389x-1256x smaller than
  source (25-62 KB gzip); detail peaked at 10,420 pairs against the 32,768
  ceiling; cached ready GETs 11-30 ms with zero subprocesses. Bounded memory
  confirmed against real media: backend RSS delta flat at ~32 KB regardless of
  duration versus 3.8-10.2 MB if PCM were buffered. CPU ~59% of one core of
  four; `-threads 1` and `workers=1` confirmed at runtime. Subprocess contract
  captured from /proc: argv-only, no shell, no output media path, PCM to
  stdout, mono/8kHz/s16le, child in its own process group (PGID == SID). In
  the browser, rendered canvas pixels correlated against the API's own peaks
  at r=0.9978 (MP3) and r=0.9982 (FLAC) with 0.2-0.6 px mean error; pointer,
  keyboard, and — newly, closing W5's open item — **touch** seeking verified,
  including `touch-action: pan-y` letting vertical drags scroll instead of
  seek. Request discipline: 0 waveform calls for 24 Library rows, exactly 1
  POST + 2 polls per explicit generate, polling stops, 0 requests from
  seeking, 0 console errors. Real cancellation terminated the decoder in 46 ms
  inside the 5s SIGTERM grace with no partial artifact; restart resumed 0
  jobs; LRU pruned to the 80% target evicting exactly the least-recently-used
  entries; the confirmation-gated clear rejected unconfirmed requests and then
  removed all 7 artifacts with 0 regeneration. Source integrity: all 7 files
  identical afterwards by size, mtime_ns, ctime, device, inode and full
  SHA-256; 88-file tree digest unchanged; no sidecars; BPM/key/Camelot/cue/
  review/quality unchanged; `processed.db` untouched and waveform-table-free.
  The cache was deliberately cleared at the end, restoring its pre-W7 empty
  state and proving disposability.

- 2026-08-06: Implemented Waveform Phase W6, lifecycle/cleanup/resource
  controls. Added `backend/app/services/waveform_cache_service.py`: bounded
  cache accounting, tiered cleanup (abandoned temps >24h, superseded
  schema/algorithm layouts >7d, orphans, non-ready artifacts, then LRU ready
  artifacts), 2 GiB -> 80% pruning that never evicts the artifact just
  published, startup reconciliation of tracks claiming a missing file, and a
  manual preview/clear action. Deletion is structurally contained: candidates
  are derived internally from the validated cache root and CrateIQ's own
  artifact/temp naming, the walk never follows symlinks, and each candidate
  must still pass W1's containment assertion. LRU ordering uses a new
  application-owned `waveform_track_state.last_accessed_at` column instead of
  filesystem atime (`relatime`/`noatime` defer or disable it), written at most
  once per track per hour so multi-tab polling cannot amplify into a write per
  request. Scheduler shutdown is ordered and idempotent — cancel tokens so
  W2's supervisor can TERM/KILL and reap the child process group, bounded
  drain, then task cancel — and records `BACKEND_SHUTDOWN`, distinct from a
  user cancellation; a crashed runner now fails its job instead of stranding
  it in `processing` and permanently blocking that track. Added 30-day
  retention for failed/cancelled job rows and quiet artifact-less track states
  (rows only). Completed W3's deferred `detected` -> `ready` readiness
  transition with a startup-only cached `ffmpeg/ffprobe -version` check, so
  `GET /api/runtime/readiness` stays a pure read that spawns nothing. Added
  `GET /api/waveform-cache` and a confirmation-gated
  `POST /api/waveform-cache/clear`. Fixed a latent import-time `asyncio.Lock`
  loop-binding hazard in the shared maintenance lock. 1327 tests pass twice;
  frontend typecheck unchanged; no frontend file changed. No waveform was
  generated from the real music library and no real cache directory was
  cleared.

- 2026-08-06: Implemented Waveform Phase W5, interactive waveform seeking.
  The existing native `<input type="range">` seek control moved from a
  separate row below the waveform into a transparent, full-box overlay
  directly on the waveform visual (real canvas or the `ThreeBandWaveform`
  fallback), so the whole ~46px waveform becomes the seek target and pointer,
  drag, touch, and keyboard behavior all come from the browser's native range
  implementation — no hand-rolled pointer-capture or ARIA-slider code. This
  replaces the old separate control rather than adding a second one, avoiding
  duplicate accessible sliders. The thumb is restyled into a thin cyan
  playhead needle visible over both real and fallback waveforms; `step` rose
  from 0.1s to 5s for usable arrow-key seeking (this does not affect the live
  position display, which is driven by the controlled `value` prop, not
  `step`); `aria-valuetext` now reports `"m:ss of m:ss"`. Every seek still
  goes through the unchanged `usePersistentPlayer().seek(seconds)` — no new
  audio element, no competing playback clock. Only
  `PersistentBottomPlayer.tsx` and `index.css` changed; `TrackWaveform.tsx`,
  `waveformGeometry.ts`, `useTrackWaveform.ts`, and the entire backend were
  untouched. Verified in headless Chrome with real CDP-dispatched mouse and
  keyboard events (not synthetic DOM events) against a browser-level stubbed
  waveform: click/drag/boundary seeks land correctly; play/pause intent is
  preserved across a seek and resume continues from the sought position, not
  zero; real Tab navigation reaches the control and shows the custom
  `2px solid #22d3ee` focus ring (confirmed via computed style); Arrow
  Left/Right/Home/End/PageUp all work natively; seeking works identically
  with a `not_generated` fallback and triggers zero generation requests; a
  drag interrupted by switching tracks mid-gesture lands cleanly on the new
  track with no stale contamination; a full sequence of seeks produced zero
  waveform GET/generate POST/job-status traffic; 1440x900/760x900/390x844 all
  show no overflow; clicking Generate/Cancel does not also seek; and Music
  Review's single-letter shortcuts correctly no-op while the seek control is
  focused. No backend change, no new real waveform generation, and no music
  file, tag, or trusted metadata was touched. W6 (cleanup/resource/restart
  hardening) is next.

- 2026-08-06: Implemented Waveform Phase W4, the frontend real-waveform
  surface. Added `frontend/src/api/waveforms.ts` (typed client whose
  discriminated union exposes peaks/duration only on the `ready` variant),
  `hooks/useTrackWaveform.ts` (retrieval, race protection, explicit
  generation, bounded job polling, cancellation),
  `components/player/TrackWaveform.tsx` (canvas renderer), and
  `components/player/waveformGeometry.ts` (pure peak/progress/state helpers).
  `api/client.ts` gained an additive optional `AbortSignal`.
  The persistent bottom player now draws real min/max peaks on a
  device-pixel-ratio-aware canvas with a played/unplayed progress overlay
  driven by the existing player clock, and keeps `ThreeBandWaveform` as the
  fallback for loading, not_generated, queued, processing, failed,
  unsupported, stale, and cancelled. Generation is reachable only from an
  explicit control: browser verification recorded zero generation requests
  across mount, route change, playback, track change, and every non-ready
  state, and six rapid activations produced exactly one POST. Track changes
  bump a monotonic request token and abort the previous controller so a late
  response cannot overwrite the current track; job polling is a self-cancelling
  1.5s chain that stops on terminal states, track change, and unmount.
  The canvas is `role="img"` and non-focusable — the existing labelled range
  input remains the accessible seek control, and waveform seeking stays W5.
  Verified in headless Chrome via the DevTools Protocol (no new dependency;
  the already-present `websockets` package plus the system Chrome) at 1440x900
  and 760x900 with no horizontal overflow and no W4-attributable console
  errors. No waveform was generated from the real music library and no audio
  was decoded, probed, or analyzed; the ready/queued/processing/failed/
  unsupported/stale/cancelled paths were exercised with browser-level stubbed
  responses. Real end-to-end media verification remains W7. Backend code was
  unchanged. Audited the W3 `synchronous=NORMAL` change and confirmed it is
  correctly scoped to backend-owned `jobs.db` only.

- 2026-08-06: Implemented Waveform Phase W3, the explicit generation
  lifecycle connecting W1's state foundation to W2's extractor. Added
  `waveform_identity.py` (a `generation_key` = SHA-256 of a small canonical
  structure of library-identity digest + track ID + source stat identity +
  schema/algorithm/analysis parameters — never audio content),
  `waveform_artifact_service.py` (versioned gzip-JSON artifacts under
  `<cache>/v1/<algorithm>/<ab>/<key>.json.gz`, built/validated/atomically
  published via a temp file plus `os.replace()`, with a 4 MiB decompressed
  bound and full schema/pair-count/int16-range validation on every read),
  `waveform_job_service.py` (BEGIN IMMEDIATE submission with dedup,
  supersede-on-changed-source, queue-full, claim, publish-complete, fail,
  cancel, and restart recovery), `waveform_scheduler.py` (one bounded
  asyncio queue, 1 worker by default and 2 maximum, cancellation-token
  registry, injectable runner, plus the production runner), and
  `api/routes/waveforms.py` (four endpoints). Added a `generation_key`
  column with migration to `waveform_jobs` and an additive `cache_key`
  parameter to `transition_track_state`.
  Generation is only ever started by an explicit
  `POST /api/tracks/{id}/waveform/generate`; the waveform `GET` is strictly
  side-effect free and never enqueues, extracts, runs FFmpeg/ffprobe, hashes
  a source, or writes cache. Repeated or concurrent POSTs — including
  `force=true` — reuse the single active job, guarded transactionally by
  W1's partial unique index. A forced regeneration keeps the previous ready
  artifact readable until the replacement is atomically published, and the
  cancellation cutoff is that publication. Backend restart marks interrupted
  jobs terminal and never resumes analysis. Ready responses carry a
  `"<generation_key>-<resolution>"` ETag honoring `If-None-Match` with 304.
  Reading cached waveforms stays available even when FFmpeg disappears.
  Also fixed a pre-existing `jobs.db` performance defect: WAL was paired with
  `synchronous=FULL`, costing ~372 ms of fsync per commit; it now uses the
  documented WAL pairing `synchronous=NORMAL` (~15 ms), which cannot corrupt
  the database and only risks the most recent transactions on power loss.
  139 new tests; the full suite is 1191 passed (1052 W2 baseline + 139), zero
  regressions. No waveform was generated from the user's real music library,
  no audio path reached an external executable, and full-content SHA-256
  remains deferred. W4 (frontend rendering) is next.

- 2026-08-06: Implemented Waveform Phase W2, a safe internal extraction
  engine only. Added `backend/app/core/waveform_limits.py` (size/duration/
  timeout/resolution policy constants and formulas), `waveform_process.py`
  (an argv-only, no-shell subprocess supervisor with bounded streaming
  stdout, a bounded stderr tail, cooperative cancellation, duration-aware
  timeout, and TERM-then-grace-then-KILL process-group termination),
  `backend/app/models/waveform_extraction.py` (a narrow internal error
  taxonomy plus `ProbeResult`/`WaveformExtractionResult`/
  `CancellationToken`), `backend/app/services/waveform_probe.py` (a bounded,
  read-only ffprobe wrapper with full validation of duration/channels/sample
  rate/codec, plus an executable-resolution helper and an unwired
  `-version`-only verification primitive), `waveform_peaks.py` (arbitrary-
  chunk-boundary PCM framing and one bounded doubling-merge min/max
  accumulator that serves both known- and unknown-duration streams from a
  single algorithm, plus extrema-preserving compact/player downsampling),
  and `waveform_extractor.py` (the orchestrator: reuses W1's
  `track_source_service` unchanged, enforces the 8 GiB source-size policy
  before any subprocess is spawned, and detects source changes via a
  pre/post stat comparison — never a content hash). The extractor never
  imports `waveform_state_service` or `waveform_cache`, so it cannot write
  `jobs.db` state or a cache artifact even by accident. 92 new tests run
  entirely against fake process objects and synthetic in-memory PCM; the
  full suite is 1052 passed (960 W1 baseline + 92 new), with zero
  regressions. No real audio file was decoded, probed, or hashed; no API,
  job worker, or frontend change was added. W3 (cache + API) is next.

- 2026-08-05: Implemented Waveform Phase W1 backend foundation only. Added
  environment-backed conservative limits, a canonical CrateIQ-owned cache-root
  guard that rejects equality/ancestor/descendant/symlink overlap with the
  selected library, and a cleanup-candidate containment primitive. Added
  privacy-safe library identity plus cheap source stat snapshots behind the
  same DB-backed canonical validation used by preview audio. Added explicit
  artifact/job/capability enums and idempotent `waveform_track_state` /
  `waveform_jobs` tables to backend `jobs.db`; trusted `processed.db` was not
  extended. `source_sha256` and `cache_key` remain nullable/deferred. Runtime
  readiness and Settings capabilities now distinguish disabled,
  misconfigured, cache unavailable, extractor unavailable, and passively
  detected-but-unverified states without returning cache/library/executable
  paths. Normal future `not_generated` state is documented as `200`, not 409.
  No extractor, worker, route, waveform artifact, source content hash, audio
  decode/probe, FFmpeg/ffprobe execution, dependency, or frontend change was
  added. W2 is the next separate phase.

- 2026-08-05: Accepted the design-only real waveform architecture in
  `docs/architecture/WAVEFORM_ARCHITECTURE.md`. The selected architecture uses
  explicit demand-driven backend extraction with FFmpeg/ffprobe strictly as a
  read-only decoder/probe toolchain, a CrateIQ min/max peak accumulator, a
  versioned content-addressed cache under backend-owned storage, and frontend
  canvas rendering driven by the existing persistent player's time/seek state.
  Generation is never started by ordinary GET, Library open, import, or scan;
  default concurrency is one and peak count is capped for long DJ sets. The
  current deterministic three-band display remains the graceful fallback.
  This milestone changed documentation only: no extractor, API, DB schema,
  cache, dependency, or runtime behavior was implemented, and no audio or
  trusted metadata was read by an analysis tool or modified.

- 2026-08-05: Completed browser-driven persistent-player verification against
  the explicitly selected `crateiq-test-library`. Chrome decoded representative
  MP3 and FLAC files through `/api/tracks/{track_id}/preview-audio`, including
  filenames with spaces, parentheses, an apostrophe, and Unicode. Play/pause/
  resume, forward/back seeking, range requests, non-wrapping first/last queue
  boundaries, next-track autoplay on `ended`, safe final-track stop, route
  persistence, both Library/Music Review synchronization directions, rapid
  switching, and unavailable recovery were verified with one `<audio>` element
  and no runtime exceptions. The bottom player no longer renders the absolute
  indexed filepath; Library tracks show a safe source label instead. Browser
  compatibility remains format/browser-specific, unsupported files keep the
  recoverable unavailable state, and real waveform extraction remains future
  work. No audio, tag, metadata, crate, MIK, or DJ-database data changed.

- 2026-08-05: Added a persistent browser-only audio provider above the router
  and a Spotify-style bottom player inside the app shell. Current track, safe
  preview URL, queue/index, play state, timing, seek, volume, errors, and
  minimize state survive route changes. Library supplies its visible filtered
  page as the queue; Music Review supplies its current queue and synchronizes
  player next/previous back to the selected review row. Deep-link selection is
  read-only, and review shortcuts still ignore form/editable controls.
- Playback uses only `GET /api/tracks/{track_id}/preview-audio` and browser
  decoding. Unsupported, missing, or out-of-root files surface a recoverable
  unavailable state. No tag, music/audio file, crate order, Smart Crate rule,
  BPM/key/Camelot/cue/MIK, external-provider, or DJ-database writes were added.
- The persistent deck reuses `ThreeBandWaveform.tsx`; its deterministic bars
  remain visual-only, with no waveform extraction or analysis. Metadata Repair
  and Genre Taxonomy gained dense shared tables/forms, while Manual and Smart
  Crate panels were aligned to the existing Night Deck surfaces. Settings,
  Jobs, Quality Review, and Beets Review were inspected and retained their
  already-aligned layouts.
- Remaining visual/audio work: verify full play/seek/end/error behavior with
  representative browser-playable files, design real waveform extraction only
  as a separate explicitly safe feature, and continue deeper page-level polish
  only where observed usability needs justify it.

- 2026-08-05: Manual Crate order/finder rows and Smart Crate preview rows now
  show bounded read-only Music Review status/rating badges linking to the exact
  track. Summary requests batch at 200 IDs. Manual order and Smart Crate rules,
  ranking, eligibility, and save behavior are unchanged; rejected tracks are
  context only and are not automatically excluded.

- 2026-08-05: Music Review now uses the Library's dense dark DJ-dashboard
  direction: real review metrics, a full-width track queue, a sticky selected-
  track player/review rail, the three-band visual, and explicit previous/next
  controls. `/music-review?track_id=…` selection and the query-preserving
  `/listening` redirect remain intact; selecting alone performs no DB write.
  Shortcuts ignore inputs, textareas, selects, buttons, and editable elements.

- 2026-08-05: Added reusable `ThreeBandWaveform.tsx` to the Library inspector
  and Music Review. Its low/mid/high bars are deterministic presentation from
  a track id, not analyzed waveform data; it reads and writes no audio or tags.

- 2026-08-05: Refined the Library route against
  `docs/mockups/library.webp`: denser status/metric surfaces, a dark DJ-table
  treatment with keyboard-selectable rows, and a 380px inspector rail that
  groups native browser preview with the preserved accessible Camelot wheel
  and compatible-track context. This is frontend presentation only; Music
  Review links and all library safety boundaries are unchanged.

- 2026-08-05: Library rows now show read-only Music Review status/rating
  badges backed by the bounded review-summary endpoint. Badge links open the
  matching track through `/music-review?track_id=…`; no tags or files change.

- 2026-08-05: Renamed the user-facing DB-only workflow to Music Review at
  `/music-review`. `/listening` redirects compatibly and preserves its query
  string; the `/api/reviews/*` backend contract remains unchanged. Review
  status, rating, notes, and keyboard shortcuts never change tags or files.

- 2026-08-05: Added unified `/metadata-repair` DB-only review queue. It
  snapshots conservative local-index metadata issues and routes users to safe
  specialist workflows; it never applies fixes itself.

- 2026-08-05: Genre Taxonomy now supports DB-only preferred genre and mapping
  create/update/disable operations. Only enabled mappings are used for new
  review previews; normalized apply remains explicit and preserves raw genre.

- 2026-08-05: Added `/genres`, a review-first local genre taxonomy workflow.
  Default Ghana/Africa and DJ-friendly genres are seeded safely; raw genre is
  retained while selected normalized values are stored separately in the index.

- 2026-08-05: Added `/enrichment-review`, a multi-source comparison foundation.
  It generates only conservative local filename-hint suggestions today and
  displays source state; no external API or Beets subprocess runs. Selected
  empty artist/title/genre fields can be reviewed and applied only to the local
  index, never to tags or media files.

- 2026-08-05: Added Settings → Metadata Sources: a safe local-only registry
  for local tags, filename hints, MIK, Beets, and future external sources.
  External APIs are disabled by default; credentials live only in ignored
  `.run/local/metadata_sources.json`, are masked by omission in all API/UI
  responses, and are not used for lookup in this foundation.

- 2026-08-05: Added `/beets-review`, a selected-field local enrichment
  workspace. It snapshots only candidates missing artist/title/genre, then
  requires each user-entered value to be explicitly selected, saved, and
  confirmed before applying it to `processed.db`. Beets subprocess/config/
  library access remains deferred; existing fields, BPM/key/Camelot/cues/MIK,
  tags, media files, and DJ databases are never changed.
- 2026-08-05: Added `/quality-review`, a safe Audio Quality Review workspace
  over the bounded ffprobe preview. Refresh stores only safe probe findings in
  the selected library's `processed.db`; reviewed/ignore/review-later/
  unresolved decisions and notes are DB-only and scoped to each snapshot.
  It uses a neutral low-bitrate candidate flag for lossy codecs below 192 kbps
  and has no transcode, remediation, file/tag, or DJ-database action.
- 2026-08-05: Added `/duplicates`, a safe Duplicate Review workspace layered
  over the bounded rmlint preview. Refresh stores only a safe relative-path
  preview snapshot in the selected library's `processed.db`; keep, ignore,
  review-later, and unresolved decisions with optional notes are DB-only and
  scoped to that snapshot. No delete/move/rename/quarantine/tag/file action or
  automatic resolution exists, and `/jobs` links to the review page.
- 2026-08-05: Standardized the shared document-style `.page` shell to use the
  full workspace after the sidebar rather than a narrow capped column. This
  aligns Settings, Jobs, Exports, Set Builder, BPM Review, Reconciliation,
  sync, and related pages with the existing full-width Library/Quality and
  workspace routes while preserving their internal responsive grids/tables.
- 2026-08-05: Polished `/jobs` into the same compact dark dashboard language
  as Settings. It now exposes safe workflow modes, candidate/status summaries,
  filters, policy chips, clearer preview contracts, and confirmation/result
  summaries without changing any backend runner, preview-only boundary, or
  data behavior.
- 2026-08-05: Aligned `/settings` with the dark local-first dashboard visual
  system. It now uses compact anchor navigation, visible Library & Paths,
  Analysis & Tools, Safety & Behavior, Job Defaults, planned Backup & Restore,
  and Diagnostics sections, while retaining every library setup/import action,
  capability link, and locked policy. This is frontend-only: no backend or
  safety behavior changed.
- 2026-08-05: Added a bounded, preview-only ffprobe Audio Quality Probe to
  `/jobs`. It calls `ffprobe -v error -show_format -show_streams -of json`
  for at most ten validated imported tracks, returns only safe relative paths
  plus container, codec, duration, bitrate, sample rate, channels, size, and
  neutral probe states. Audio Quality Review can separately store DB-only
  human review choices; it has no run/apply action and never transcodes or
  writes media, tags, local DB fields, MIK data, or DJ-app databases; missing
  ffprobe gates only this workflow.
- 2026-08-05: Added optional, preview-only rmlint duplicate detection to
  `/jobs`. It scans at most 100 validated imported paths using JSON stdout,
  returns grouped relative-path candidates only, and has no resolution action.
  It never creates/executes rmlint scripts or writes files, tags, or DB
  decisions. Duplicate Review can separately store DB-only human review
  choices; missing rmlint disables only preview refresh, not existing reviews.
- 2026-08-05: Added a safe Beets enrichment preview. It uses only the local
  index to identify missing artist/title/genre fields and never invokes beet,
  writes tags, moves files, or writes suggestions. `/beets-review` now offers
  an explicit selected-field DB-only acceptance workflow; broad enrichment and
  all file/tag operations remain deferred.
- 2026-08-05: Added the safe key/Camelot Analysis Jobs runner. After preview
  and explicit confirmation, it invokes `keyfinder-cli <file>` only for rows
  with both key fields null, maps only recognized musical/Camelot values using
  the existing harmonic map, and writes lower-authority local provenance. It
  never writes tags/media or changes MIK/trusted/existing key values.
- 2026-08-05: Added the first safe Analysis Jobs runner: a preview-first,
  explicitly confirmed `aubio tempo <file>` BPM pass. It selects only tracks
  with null BPM, accepts 40–250 BPM, and writes only `bpm`, `bpm_source=aubio`,
  `bpm_trusted=0`, and `bpm_analyzed_at` to `processed.db`. It never calls a
  fallback analyzer, writes tags/media, or changes MIK/trusted/existing BPM;
  all other analysis jobs remain preview-only/pending.
- 2026-08-05: Reworked `/jobs` into an optional Analysis Jobs catalog. It
  exposes MIK coverage plus BPM, key/Camelot, Beets, duplicate, and quality
  candidate previews from the local index, with per-tool gating. MIK import
  remains an explicit Settings-only DB write; every non-BPM runner is
  accurately preview-only/pending. Core import, browse,
  crates, preview, and exports remain available without the optional tools.
- 2026-08-05: Added an explicit Mixed In Key-compatible metadata coverage and
  import foundation. `GET /api/analysis/mik/coverage` reports the local-index
  state without reading files; explicit preview reads existing compatible BPM/
  key tags; explicit import fills only absent `processed.db` values with
  `mik_compatible_tag` provenance and trusted status. It never invokes MIK,
  aubio, or keyfinder, never writes audio/tags, and never overwrites existing
  BPM/key data. Cue-tag extraction is intentionally unavailable rather than
  inferred. Settings now exposes this optional input-source workflow and its
  fallback BPM/key candidate counts without blocking core use.
- 2026-08-05: Polished the Settings Library Setup & Import flow into a
  numbered, review-first wizard: select root, initialize the local index,
  explicitly scan, review counts/samples/warnings, then import. Scan previews
  now report files/folders and supported/unsupported/skipped counts plus cheap
  duplicate-name and long-path hints; imports report new/existing/total indexed
  counts and avoid duplicate DB records by path. This remains local-index-only:
  it does not decode audio, run analysis, change files/tags/BPM/key/cues/MIK,
  or write DJ application databases. The Library empty state links back to the
  setup flow rather than suggesting analysis before import.
- 2026-08-05: Added per-workflow optional-analysis capability gating and
  persisted safe analysis preferences. `GET /api/settings` and
  `GET /api/settings/capabilities` distinguish always-available core work from
  optional MIK coverage, BPM, key/Camelot, Beets, duplicate, and audio-quality
  workflows. BPM/key preferences are default-off; MIK use, preservation of
  existing BPM/key/cues, and missing-data-only behavior are locked on. Settings
  never starts analysis, and import remains analysis-free. Existing broad
  browser re-analysis UI now directs users to Settings until an explicit
  DB-only missing-data analysis workflow exists.
- 2026-08-05: Completed the full functionality, workflow, external-tool, and
  product-surface audit in
  `docs/audits/CRATEIQ_FUNCTIONALITY_WORKFLOW_AUDIT.md`. The audit confirms
  that import/browse/review, Manual and Smart Crates, native preview, and
  portable/staged exports form a standalone core without optional binaries.
  The highest-priority gap is per-workflow capability gating: current
  readiness detects optional tools globally, but actions do not yet consume a
  capability contract. MIK-compatible BPM/key tags are preserved by the
  analyzer, but provenance, cue import, and coverage UI are missing. The next
  recommended task is default-off, independent BPM/key settings plus scoped
  tool gating; Library Import Wizard polish and MIK/source coverage follow.
- 2026-08-05: Library Initialization / Import foundation lets Settings create
  only `logs/`, `exports/`, and an empty `logs/processed.db` schema for a
  pending configured root. Scan preview is explicit and read-only; confirmed
  import adds filename/path records to that local index only. No audio files,
  tags, BPM/key/cues, MIK values, or Serato/Rekordbox databases are changed.
  The service helper still refuses to start an uninitialized configured root
  and directs the user back to Settings.
- 2026-08-05: Settings is now a supported local-first route with current
  library/tool/readiness diagnostics, locked safety policies, and one
  library-scoped preference: the default export path mode. The preference
  persists in ignored `<library-root>/logs/app_settings.json` and initializes
  export forms on their next load. Settings can also validate and save a
  pending absolute library root in ignored `<repo>/.run/local/crateiq.env`;
  the helper reads it on the next configured-library start and the running
  process remains unchanged until then. Binary overrides remain read-only
  process-start environment settings with restart guidance; no
  folders are scanned and no audio/tag/MIK/DJ database data is changed.
- 2026-08-04: Audio Preview Player foundation adds browser-native, preview-only
  playback to the Library inspector plus Manual and Smart Crate track rows.
  `GET /api/tracks/{track_id}/preview-audio` resolves only DB-backed files
  under the selected root and supports byte ranges for seeking. It neither
  scans nor transcodes audio, and never changes audio/tags/BPM/key/cues, MIK,
  Serato, or Rekordbox data. Missing demo placeholder files show unavailable
  states; waveform/cue/beat-grid/keyboard preview work is deferred.
- 2026-08-04: Rekordbox XML Export Foundation adds a preview-first Manual Crate
  XML handoff to `/exports`. It stages a unique UTF-8 XML file with ordered
  collection and playlist nodes below `<library-root>/exports/rekordbox/`.
  This is for manual Rekordbox XML import only; it never writes a live
  Rekordbox database, device, USB, or application folder. No music files,
  tags, BPM/key/cues, MIK data, or Serato state is modified.
- 2026-08-04: Serato Export Foundation adds a preview-first staged handoff to
  `/exports`. A saved Manual Crate can produce an M3U8 and JSON manifest under
  `<library-root>/exports/serato/`; each export uses a new safe folder and
  never touches a live `_Serato_` folder. Exact Serato binary `.crate` writing
  and custom destinations are intentionally deferred. No audio files, tags,
  BPM/key/cues, MIK data, or DJ-app databases are modified.
- 2026-08-04: Crate Export Foundation added to the existing `/exports` page
  without changing Rekordbox job workflows. Manual Crates can now be previewed
  and explicitly written as CSV, JSON, M3U, or UTF-8 M3U8 under the selected
  library's `exports/` directory. Filename paths are the default; relative
  paths are root-relative when safe and absolute paths require an explicit
  choice. Export files are unique, no-overwrite, portable artifacts only—no
  audio files, tags, MIK data, or Serato/Rekordbox databases are modified.

- 2026-08-04: Smart Crates added at `/smart-crates`. The new local-only API
  exposes deterministic presets and preview/save endpoints. Previews remain
  ephemeral; an explicit save creates an ordered normal Manual Crate through
  the existing library-scoped crate DB. Supported criteria are BPM range,
  Camelot exact/compatible/energy-direction matching, genres, issue-free
  state, and limit. Energy/vibe and date-added are explicitly unavailable
  because `tracks` has no trusted values for them. No audio files, tags,
  BPM/key/cues, Mixed In Key data, or exports are changed.

- 2026-08-04: Manual Crates shipped as a local-first, review-before-apply
  working-list surface. `GET/POST/PATCH/DELETE /api/crates` plus scoped track
  add/remove/reorder endpoints persist only `<selected-library-root>/logs/
  manual_crates.db`; `processed.db` remains read-only and no music files,
  tags, BPM/key, cues, or Mixed In Key data are modified. The new `/crates`
  route and sidebar item provide crate creation, notes, delete confirmation,
  ordered up/down controls, library search/add, and loading/error/empty
  states. Demo seeding now creates four deterministic example crates. Focused
  API tests and the supported-route contract cover the feature.

- 2026-08-02 (later same day): SetBuilder and Reconciliation completed
  the visual rollout's deferred JSX-level pass, closing the last two
  bespoke-markup pages. SetBuilder.tsx: both ErrorBanner sites now render
  `<StatusStrip tone="danger" role="alert" onDismiss>` (identical
  content/behavior), the no-saved-sets hint renders `<EmptyState>`, and
  the job-status/Camelot-key/vibe chips render `<Badge>` (vibe keeps its
  capitalize override via an inner span). Reconciliation.tsx: LedgerBadge
  now renders `<Badge>` via a `statusTone()` mapper (identical markup),
  both ErrorBanner sites render StatusStrip, and the three standalone
  empty states render `<EmptyState>`; the validation-records table's
  index-based key (`${action_type}-${index}`) was replaced with a stable
  content-derived key (`action_type:reason:JSON.stringify(action)`) since
  records carry no real ID. Deliberately left bespoke (documented in
  CHANGELOG): phase-badge's 5-color taxonomy, set-stat-card/recon-stat
  detail tiles (KpiCard not forced — not page-level overview rows),
  live-indicator chips, loading text, inline table-cell fallbacks. No
  set-building/scoring/reconciliation/backend behavior changed.
  Verification: typecheck/build clean, Impeccable detector unchanged at
  the 4 documented pre-existing findings (both changed files scan
  clean), headless-Chrome render of both pages against the demo library
  verified the new EmptyStates live with no key warnings and no new
  console errors. See CHANGELOG.txt for detail.

- 2026-08-02: Jobs page adopted the visual rollout's shared primitives
  where they naturally fit and had its pre-existing React "missing key
  prop" warning fixed. `frontend/src/pages/Jobs.tsx` now renders the
  jobs-fetch error via `<StatusStrip tone="danger" role="alert">` (same
  content/semantics as the old `.error-banner`, which remains in use on
  other pages) and the no-jobs state via `<EmptyState>` (identical copy).
  Badge/KpiCard were deliberately not forced: `StatusBadge` remains the
  preferred job-status badge per Badge.tsx's own guidance, the "N active"
  live-indicator chip stays (pattern shared with BpmReview/Export/
  SetBuilder/SsdSync), and Jobs has no metric-card grid. Key-warning root
  cause: `JobsTable`'s `jobs.map()` returned a `<>` fragment with keys on
  the inner `<tr>` elements; it now returns `<Fragment key={job.id}>`
  (stable real job ID). `JobProgress` markup and its width-transition
  fill, job execution/cancellation/polling, and all backend behavior are
  unchanged. Frontend-only: typecheck/build clean, Impeccable detector
  unchanged at the 4 documented pre-existing findings, headless-Chrome
  render against the demo library verified no key warning and no new
  console errors. See CHANGELOG.txt for detail.

- 2026-07-29: Added a local tooling setup layer for the optional
  `keyfinder-cli`, `aubio`, and `beet` executables. `requirements.txt` and
  `requirements-dev.txt` remain Python-only: CrateIQ invokes Beets only via
  the `beet` CLI and does not import its Python package. New
  `docs/operations/LOCAL_TOOLING.md` provides Linux Mint/Ubuntu installation,
  verification, override, and safety guidance; `.env.example` now documents
  blank `KEYFINDER_BIN`, `AUBIO_BIN`, and `BEET_BIN` values as PATH search.
  `config.py` treats blank keyfinder/Beets overrides the same way. Readiness
  remains read-only and non-fatal: missing optional tools report a degraded
  warning with the unavailable workflow, and focused tests cover overrides.
  No music scanning, analysis, export, or pipeline behavior changed.

- 2026-08-02: Expanded `docs/operations/LOCAL_TOOLING.md` with a safe,
  source-only fallback for Linux Mint/Ubuntu systems where apt does not package
  `keyfinder-cli`: pinned upstream `keyfinder-cli` v1.2.0 plus
  Mixxx-maintained `libkeyfinder` 2.2.8, built with CMake into a user-local
  prefix. The documented static-libkeyfinder build avoids a local shared
  library search-path requirement. It remains an opt-in fallback for tracks
  without MIK key data, and `KEYFINDER_BIN` may point to the resulting binary.
  No script, automatic package installation, binary artifact, or application
  behavior was added or changed.

- 2026-07-26: Adopted the visual rollout's three previously-unused shared
  primitives (KpiCard, EmptyState, Badge — see prior milestone entry below)
  into `Quality.tsx` and `BpmReview.tsx`, the two lowest-risk pages, rather
  than leaving them as dead code. `Quality.tsx`'s top metric row now uses
  `KpiCard` (ring-progress for percentage metrics, icon-based for the rest),
  its confidence chips now use `Badge` (which also fixed a real bug — the
  old `conf-chip--high/medium/low` classes had no CSS, so HIGH/MEDIUM/LOW
  all rendered identically uncolored), and its "Recommended Next Actions"
  section now uses `EmptyState` instead of silently rendering blank when
  empty. `BpmReview.tsx`'s two "no anomalies" messages now use `EmptyState`
  with identical copy. `KpiCard`/`Badge` were deliberately not forced into
  `BpmReview.tsx` — no metric-card grid exists there, and its anomaly
  reason/status labels already have a working, more granular color
  taxonomy than `Badge`'s six tones support. All four shared primitives
  (`StatusStrip`, `KpiCard`, `EmptyState`, `Badge`) now have real
  consumers. No backend/pipeline/route/API/auth/AI/sync/export/
  reconciliation behavior changed; Library's Camelot wheel and
  compatible-tracks verified unaffected. See CHANGELOG.txt for detail.
- 2026-07-26: App-wide visual system rollout extending the Library view's
  approved visual system (Inter, dark background, emerald/teal/cyan/violet/
  coral palette) to the rest of the frontend, as a shared CSS-token/primitive
  pass rather than a per-page redesign. New reusable components:
  `frontend/src/components/ui/StatusStrip.tsx` (good/warn/danger/info compact
  strip — full 1px border + tint, never a colored side border), `KpiCard.tsx`
  (reuses the Library's own `.lib-overview-card`/`RingProgress` directly, no
  duplicated CSS), `EmptyState.tsx`, `Badge.tsx`. `ReadinessBanner.tsx` now
  renders `<StatusStrip>` internally (same three states, same suppression on
  `/` via `Layout.tsx`, unchanged). Fixed both Impeccable-flagged side-tab-
  border findings (`Export.tsx`'s `WarningItem`, `SsdSync.tsx`'s
  `MountWarning`/preview-warnings/cancel-error) by swapping to `StatusStrip`.
  Global `index.css` changes (all app-wide, one shared stylesheet): `.app-main`
  gained the same subtle radial-gradient wash as `.lib-workspace`/
  `.crate-workspace` (every non-Library/CrateMind page now shares that
  atmosphere instead of a flat background); `.card`/`.stat-card` radius+surface
  unified with `.lib-card`; removed a pervasive leftover pre-teal-swap blue
  accent (`rgba(74,126,255,*)`, 21 occurrences) used for selected/hover/focus
  states across Metadata Repair, Metadata Sanitation, Quality, Set Builder,
  and the CrateMind workspace — repainted to the teal `--accent`; a matching
  light-blue text color (`#9fb7ff`, 5 occurrences) repainted to
  `var(--brand-cyan)`; `.btn--primary:hover`, `.badge--info`,
  `.quality-badge--high`, `.bpm-suggestion`, `.metadata-repair-primary`/
  `.metadata-repair-apply-preview`, and `.crate-workspace`/`.crate-meter`'s
  gradients all had the same leftover blue, now cyan/teal. Left untouched:
  the two legacy-Collection.tsx-only occurrences of that blue (unreachable
  page), Jobs/Export/SsdSync's width-transition progress bars (Jobs.tsx not
  touched this pass), and `--status-running` blue / `.lib-btn--primary`'s own
  indigo-blue-cyan gradient (both intentional, not leftovers). Verification:
  frontend typecheck/build clean, `python -m pytest -q` 888 passed (no backend
  changes), `pip check` clean, `git diff --check` clean. Impeccable detector
  findings went from 6 to 4 (both side-tab findings fixed; the remaining 4 are
  the pre-existing/deferred width/height-transition progress bars). Manually
  verified Library/compatible-tracks/Camelot-wheel/demo-seed still work
  unchanged, and clicked through Quality, Issues, Enrichment Queue, Metadata
  Repair, BPM Review, Jobs, Set Builder, Export, SSD Sync, and Reconciliation
  against a freshly reset demo library — no broken/blank pages, no new console
  errors (one pre-existing React "key" prop warning in Jobs.tsx's JobsTable
  was observed but is unrelated — Jobs.tsx has zero diff this pass). No
  backend/pipeline/route/API/auth/AI/sync/export/reconciliation behavior
  changed.
- 2026-07-26: Real compatible-tracks API + Camelot wheel for the Library
  inspector, replacing the two remaining honest placeholders from the prior
  redesign pass. New `GET /api/tracks/{id}/compatible` (backend/app/api/
  routes/tracks.py, backend/app/services/track_service.py, backend/app/
  schemas/track.py) reuses `modules/harmonic.py`'s existing Camelot/BPM/
  genre scoring — no duplicate harmonic algorithm — restricted to the three
  standard mixable relations (same key, adjacent wheel position, relative
  major/minor); BPM tolerance and genre only affect ranking/inclusion, not
  separate match routes. 9 new tests in `tests/test_backend_api.py`.
  `frontend/src/components/library/CamelotWheel.tsx` (new) is a real SVG
  Camelot wheel with an accessible aria-label/aria-describedby text
  alternative; `TrackInspector.tsx`'s "Compatible tracks coming soon" note
  is now a live list (loading/empty/error/no-key states, request-sequence
  guard against stale responses). `scripts/seed_demo_library.py` gained 14
  fixed "Compatibility Demo" tracks (two anchors: 8A Afro House, 3A
  Amapiano spanning the required Ghana/Africa genre set) so the new feature
  always has real clusters to demonstrate; still `.run/demo-library`-only,
  still idempotent under `--reset`. No pipeline, auth, sync/export/
  reconciliation, or MIK-data behavior changed. See CHANGELOG.txt for full
  detail.
- 2026-07-26: Library view split out of `CrateMind.tsx` entirely and given a
  mockup-faithful visual pass (the 2026-07-25 pass below still read as a
  re-theme once viewed with populated data). `CrateMind.tsx` now only serves
  `/issues`, `/enrichment`, `/audit`, `/folders`; the `/` route renders
  `frontend/src/components/library/LibraryView.tsx`, which composes
  `LibraryToolbar`, `LibraryRuntimeStrip`, `LibraryOverview`, `LibraryFilters`,
  `TrackTable`, `TrackInspector`, and `libraryUtils.ts` (camelot-hue color
  coding, UI-state persistence, virtualization constants) — all under
  `frontend/src/components/library/`. `frontend/src/pages/LibraryView.tsx`
  (the prior monolith) was removed.
  Key changes: the global `ReadinessBanner` (see below) is now suppressed on
  `/` — `Layout.tsx` checks the route and skips it there — replaced by
  `LibraryRuntimeStrip.tsx`, a single-line compact amber/coral strip (top
  issue + "+N more" + Recheck) instead of the full-width banner. Overview
  cards now match the mockup's 6-card set exactly (Total Tracks, Analyzed,
  Missing Key, BPM Coverage, Key Compatibility, Duplicates) in one row on
  desktop; `GET /api/library/overview` gained an additive `tracks_analyzed`
  field (tracks with both BPM and key present) to back "Analyzed" honestly.
  The track table gained a Quality column (compact 4-bar meter from the real
  `quality_tier` field — there is no "energy" field, so this is the honest
  substitute per user instruction) and auto-selects the first track on load.
  The inspector always renders its full structure (hero/stat tiles/metadata/
  waveform/compatible-tracks note) in a dimmed placeholder state rather than
  collapsing to a bare "select a track" box when nothing is selected.
  App-wide font switched from IBM Plex Sans to Inter via `--font-ui` in
  `index.css` (Google Fonts link in `frontend/index.html`, system-ui/
  -apple-system/Segoe UI fallback). Added `scripts/seed_demo_library.py`:
  idempotent, seeds 52 realistic demo tracks across 12 genres (House, Tech
  House, Deep House, Melodic House, Afro House, Amapiano, Afrobeats,
  Highlife, Hiplife, Gospel, Techno, Progressive House) into
  `<repo>/.run/demo-library/logs/processed.db` only — never a real
  `DJ_MUSIC_ROOT`, never touches real audio. See CHANGELOG.txt 2026-07-26.
- 2026-07-25: Library view (`/`, `CrateMind.tsx` "library" section) and the
  global sidebar were redesigned to a dark emerald/teal/cyan/violet theme
  (`frontend/src/index.css` design tokens: `--brand-teal`, `--brand-cyan`,
  `--brand-violet`, `--brand-coral`; `--accent` repointed from blue to teal).
  Additions: a data-quality "Library status" strip (distinct from the
  runtime `ReadinessBanner`), functional genre/BPM/has-key filter chips
  (mapped to existing `GET /api/tracks` params), restyled overview cards
  (+ Missing Key, + Duplicates "Not available" placeholder), a `#`/Key/
  Quality-badge table columns, and an inspector with BPM/Key/Camelot stat
  tiles, a disabled play-button placeholder, a decorative waveform
  placeholder, and a "Compatible tracks coming soon" deferred note. Sidebar
  gained real nav badges (Issues/Enrichment/Metadata Repair/BPM Review
  pending counts) and a Library Health mini-card from
  `GET /api/library/quality`. Fixed a pre-existing CSS bug where
  `.sidebar-sections` had no overflow set (nav bled into the footer on
  short viewports) and reconciled the responsive breakpoints so the
  inspector now stacks below the table under 860px instead of being
  hidden. No backend/pipeline/routing/auth change. See CHANGELOG.txt for
  the mockup-feature classification (implemented / placeholder / deferred).
- 2026-07-25: Local service helper added: `scripts/crateiq-local-services.sh`
  (subcommands start/stop/restart/status/logs/back-logs/front-logs; sourcing
  with `--aliases` installs `crate_*` shell functions). CrateIQ's assigned
  local dev ports are backend 8020 / frontend 5175 (LedgerIQ owns 5173/8000,
  OpsIQ owns 5174/8010). PID files and logs live in `.run/` (gitignored).
  The Vite dev proxy target is overridable via `CRATEIQ_API_PROXY_TARGET`
  (default `http://localhost:8000` unchanged). Known issue: tracked
  `frontend/vite.config.js` shadows `vite.config.ts` (Vite loads `.js`
  first); both are kept identical for now — removal of the duplicate is an
  open NEXT_TASKS item.
- 2026-07-25: Repo hygiene — generated TypeScript build metadata
  (`frontend/tsconfig.*.tsbuildinfo`) untracked and ignored via
  `*.tsbuildinfo` in `.gitignore`, so `npm run typecheck`/`build` no longer
  dirty the working tree. No frontend/backend behavior change.
- 2026-07-25: Frontend readiness banner added. `frontend/src/api/runtime.ts`
  calls `GET /api/runtime/readiness` once on `Layout` mount (no polling);
  `frontend/src/hooks/useReadiness.ts` wraps it with a manual `refresh`;
  `frontend/src/components/ReadinessBanner.tsx` renders nothing when
  `status: "ready"`, a warning-styled banner for `degraded`, an error-styled
  banner for `not_ready`, and a small neutral notice if the fetch itself
  fails. The banner shows up to 3 failing/warning check messages (never raw
  JSON or metadata paths), is dismissible for the current session, and the
  degraded/not_ready banner carries a fixed "local diagnostic only — no
  authentication added" note.
  Purely additive to `Layout.tsx`; no routing, backend, or pipeline changes.
  Superseded in part 2026-07-26: `Layout.tsx` now skips rendering this
  banner on the `/` (Library) route only, since `LibraryRuntimeStrip.tsx`
  shows the same `GET /api/runtime/readiness` data in a compact form there;
  every other route is unaffected.
- 2026-07-24: Supported-route smoke-test contract added
  (`tests/test_supported_route_contracts.py`): all 14 supported frontend
  routes mapped to primary read-only backend endpoints, router-drift
  detection against `App.tsx`, missing-DB degradation checks, and a
  read-only/no-subprocess guard. Mutating endpoints are deliberately
  deferred (see `DEFERRED_ENDPOINTS` in the test file and NEXT_TASKS.txt).
- 2026-07-24: Local-runtime preflight and readiness contract implemented.
  `backend/app/core/preflight.py` runs read-only checks (library-root safety
  including unsafe-broad-root rejection with `CRATEIQ_ALLOW_UNSAFE_ROOT=1`
  override, pipeline DB presence/containment, `pipeline.py` entrypoint,
  backend data dir, and 7 optional external binaries) and
  `GET /api/runtime/readiness` reports `ready` / `degraded` / `not_ready`.
  `/api/health` is unchanged. `.env.example` expanded (non-secret template
  with no-auth warning). Tests: `tests/test_preflight.py`. Frontend
  readiness banner implemented 2026-07-25 (see above).
- 2026-07-24: crateIQ's target product vision is now documented in
  `docs/strategy/CRATEIQ_PRODUCT_VISION_AND_ROADMAP.md` — current-state vs
  target gap analysis, metadata/genre/harmonic/player/playlist/export
  strategy, and a 9-phase capability roadmap mapped to
  `docs/CRATEIQ_ROADMAP.md`. Documentation only; no runtime behavior changed.
  Recommended next implementation task: the local-runtime preflight and
  readiness contract (paste-ready prompt in that document's section 16).
- README updated for the Phase 1-8 CrateIQ platform milestone.
- Fork foundation commit: local CrateIQ branch `feat/crateiq-foundation-audit`.
- Backend test-suite hang diagnosis is complete; Phase 1 remains deferred until
  review of `docs/CRATEIQ_PRODUCT_AUDIT.md` and `docs/CRATEIQ_ROADMAP.md`.
- Warning: back up `<root>/logs/processed.db` before any reconciliation apply work.
- Repository audit completed on 2026-07-02; see `AUDIT_REPORT.md` for the current technical/product state and follow-up priorities.
- First post-audit stabilization completed on 2026-07-02: the frontend router
  and sidebar now expose only supported workflows.
- Supported frontend routes (as of the 2026-07-02 audit; see the
  2026-08-08 Latest Milestone entry above for `/library-prep`, added
  since): `/`, `/quality`, `/issues`, `/enrichment`,
  `/metadata-repair`, `/metadata-sanitation`, `/bpm-review`, `/audit`,
  `/folders`, `/jobs`, `/crates`, `/smart-crates`, `/music-review`,
  `/set-builder`, `/exports`, `/sync`, and `/reconciliation`. This list has
  since drifted further from `frontend/src/App.tsx` (e.g. `/beets-review`,
  `/enrichment-review`, `/genres`, `/publish`, `/settings`,
  `/quality-review` also exist) -- `tests/test_supported_route_contracts.py`
  is the enforced source of truth, not this prose list.
- Legacy `Dashboard`, `Collection`, and `Tracks` pages and placeholder
  `Settings` remain in source but redirect to `/`; `/export` and `/ssd-sync`
  are compatibility redirects. `/listening` redirects to `/music-review` and
  preserves the query string for legacy review links.
- Frontend install, typecheck, and production build pass. Production npm
  dependencies audit clean after non-breaking updates; clearing the remaining
  development-tool advisories requires a separately tested Vite major upgrade.
- Backend development setup now uses `requirements-dev.txt` from a Python 3.10+
  virtual environment. It includes pipeline/backend dependencies, pytest,
  TestClient support, and a wheel-backed numba constraint.
- The original baseline collected 857 tests under Python 3.12, then stalled at
  `tests/test_backend_api.py::test_health_endpoint_reports_selected_root_and_db`.
  Diagnosis on 2026-07-15 proved that the restricted execution sandbox failed
  to wake the cross-thread asyncio event loop used by AnyIO/Starlette
  TestClient. A bare FastAPI reproduction and the identical CrateIQ stack pass
  outside the sandbox; the issue was pre-existing environment behavior, not the
  CrateIQ rename. The current suite collects 860 tests and passes twice in the
  normal host environment.
  Shared pytest setup isolates `DJ_MUSIC_ROOT` and the preferred
  `CRATEIQ_LIBRARY_ROOT` in temporary directories; the deprecated
  `CRATEMINDAI_LIBRARY_ROOT` fallback remains supported.
- `COMMANDS.md` is the canonical command reference. The former lowercase
  `commands.md` was moved to `docs/operations/LEGACY_DJ_TOOLKIT_COMMANDS.md`
  to remove the macOS case-insensitive checkout conflict while preserving its
  distinct historical content.
- `.env.example`, CrateIQ naming updates and compatibility tests are now
  present. Next recommended task: review and approve the roadmap, then begin
  Phase 1 only after that approval.

## Phase 7 Planning

- Phase 7 started as planning only.
- Full reconciliation apply spec created at `docs/architecture/FULL_RECONCILIATION_APPLY_SPEC.md`.
- No runtime behavior changed.
- No reconciliation apply behavior has been added.

## Overview

CrateIQ is a local-first DJ library automation toolkit. It processes audio files into a cleaner, Rekordbox-ready library through deterministic cleanup, local AI-assisted normalization, artist intelligence, online enrichment, label tooling, organization, dedupe, exports, and backend/UI workflows.

Detailed safety docs:

- `docs/audits/SAFETY_GAP_AUDIT.md`
- `docs/audits/COMMAND_RISK_MATRIX.md`
- `docs/audits/FILESYSTEM_DB_CONSISTENCY_AUDIT.md`
- `docs/safety/SAFETY_MODEL.md`
- `docs/safety/ROLLBACK_AND_RECOVERY.md`
- `docs/operations/OPERATOR_SAFETY_PLAYBOOK.md`
- `docs/architecture/METADATA_OWNERSHIP_MATRIX.md`
- `docs/architecture/STABILITY_MATRIX.md`

## Architecture Summary

- `pipeline.py` is the main CLI entry point.
- `modules/` contains core pipeline operations: metadata cleanup, analysis, organizer, dedupe, cue suggestions, playlists, exports, conversion, and audits.
- `ai/` contains local Ollama AI normalization and dataset capture.
- `intelligence/artist/` handles deterministic artist normalization and aliases.
- `intelligence/enrichment/` handles online metadata enrichment and review queues.
- `intelligence/label/` handles label parsing/enrichment/reporting.
- `backend/` is the FastAPI web backend with its own `jobs.db`.
- `frontend/` is the web UI; detailed safety was not inspected in this pass.

## Safety Doctrine

Prefer no change over unsafe change.

- Preview first.
- `--apply` should gate destructive changes, but this is not universal.
- Mixed In Key owns BPM, key, and cue data.
- AI must not write BPM, key, cues, filenames, or folder structure.
- File moves and renames require path reconciliation.
- Quarantine means review later, not delete.

## Verified Safety Facts

- `ai-normalize` default confidence constant is `MIN_AI_CONFIDENCE = 0.80`.
- Enrichment matcher uses `THRESHOLD_APPLY = 0.90` and `THRESHOLD_REVIEW = 0.75`.
- Enrichment artist/version hard blocks cap blocked confidence at `min(top_conf, 0.74)`.
- Enrichment exact ISRC match bypasses gates and returns confidence 0.98.
- Enrichment review queue dedupes by exact `file_path`.
- `config.IGNORED_DIR` is `.BIN/IGNORED`; enrichment preserves paths relative to `.BIN` parent and adds `_dupN` collisions.
- `rename_processed_path()` updates `processed_state` only.

## Major Operational Risks

- Some older write-capable commands still do not require `--yes` or `--force` confirmation.
- Legacy `organizer.py` still moves files and deletes old `tracks` rows through a pre-Phase-3 path mutation pattern.
- `artist-merge`, `artist-folder-clean`, and `library-organize` move files, but their Phase 3 paths now call `update_track_path_references()`.
- Most tag writes and file moves lack universal rollback.
- Review queues and DB tables are path-based and can go stale after renames/moves.

## Subsystem State

Use `docs/architecture/STABILITY_MATRIX.md` as the current authoritative subsystem status table.

## Documentation Maintenance

Any new command, destructive behavior, metadata mutation, queue change, schema change, or rollback change must update `docs/MAINTENANCE_POLICY.md` requirements and the relevant audit docs.

## Current System State (Post Phase 3)

Phase 3 is stable enough to proceed to Phase 4 planning and implementation. Phase 4 has started as a documentation and ownership-hardening phase; runtime behavior should remain conservative until the remaining legacy path and metadata mutation risks are explicitly migrated.

### Architecture summary

CrateIQ is now organized around a safer current-state model:

- `pipeline.py` remains the main CLI and command router.
- `processed_state` records stage/file processing history.
- `tracks` is being promoted to the canonical current-state track table.
- Path-audit and path-reconcile operate against an explicitly selected library root.
- Centralized DB path updates live in `db.update_track_path_references()`.
- `modules/organizer.py` is legacy/deprecated. Prefer `modules/library_organize.py` for Phase 3-safe organization paths.

### Canonical data flow

The intended current-state flow is:

1. Files exist under a selected library root.
2. Pipeline stages record processing outcomes in `processed_state`.
3. `build-tracks --root <root>` derives one canonical `tracks` row per valid, existing, non-stale processed-state path.
4. `path-audit --root <root>` uses `tracks.filepath` as canonical when `tracks` is populated, falling back to active non-stale `processed_state` when tracks is empty.
5. `path-reconcile --root <root>` plans repairs from audit findings. Full `--apply` remains intentionally unimplemented.

### Safety model

Current verified safety guarantees:

- `metadata-clean`, `tag-normalize`, `analyze-missing`, `convert-audio`, `cue-suggest`, `db-prune-stale`, and `review-queue` default to dry-run.
- Those commands require `--apply` plus `--yes` or `--force` before write behavior.
- `path-audit` is read-only except report/log files.
- `path-reconcile` planning is read-only except plan/log files.
- `path-reconcile --apply-auto-safe-only` updates only `processed_state.filepath` for auto-safe candidates.
- `path-reconcile --mark-stale-pstate` marks only eligible processed-state rows stale and does not change file paths.
- `update_track_path_references()` updates `tracks` and non-stale `processed_state` in one transaction and never modifies stale processed-state rows.
- Root-scoped Phase 3 commands use `<root>/logs/processed.db` and `<root>/logs/`.

### Command behavior

Command mode policy:

- Default is preview/dry-run for hardened write-capable commands.
- Apply mode must be explicit.
- Destructive or write-capable Phase 3-hardened commands require confirmation.
- `review-queue` defaults to list-only dry-run behavior; interactive queue mutation requires `--apply --yes`.
- `path-reconcile --apply` is not implemented; only narrowly scoped apply helpers exist.

### Known limitations

- `modules/organizer.py` still contains a legacy `tracks` upsert plus `DELETE FROM tracks` path-mutation pattern.
- Not every older write-capable command requires `--yes` or `--force` yet.
- Root isolation is stable for `path-audit`, `path-reconcile`, and `build-tracks`, but older commands still use global config-derived paths in places.
- Queue, cue, set, and historical log references remain path-based and can become stale after external moves.
- Reconciliation does not move files, update queues, update cue references, or implement full apply mode.
- Frontend and backend write behavior were not fully re-audited in Phase 3.
