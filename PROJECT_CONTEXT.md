# CrateIQ Project Context

**Updated:** 2026-08-05

**Purpose:** Canonical low-token engineering memory for future AI sessions.

## Latest Milestone

- 2026-08-05: Added `/listening`, a DB-only DJ listening review queue with
  status, rating, notes, and keyboard shortcuts. No track tags or media files
  are changed.

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
- Supported frontend routes: `/`, `/quality`, `/issues`, `/enrichment`,
  `/metadata-repair`, `/metadata-sanitation`, `/bpm-review`, `/audit`,
  `/folders`, `/jobs`, `/crates`, `/smart-crates`, `/set-builder`, `/exports`, `/sync`, and
  `/reconciliation`.
- Legacy `Dashboard`, `Collection`, and `Tracks` pages and placeholder
  `Settings` remain in source but redirect to `/`; `/export` and `/ssd-sync`
  are compatibility redirects.
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
