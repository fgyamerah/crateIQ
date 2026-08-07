# CrateIQ Project Context

**Updated:** 2026-08-07

**Purpose:** Canonical low-token engineering memory for future AI sessions.

## Latest Milestone

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
- Supported frontend routes: `/`, `/quality`, `/issues`, `/enrichment`,
  `/metadata-repair`, `/metadata-sanitation`, `/bpm-review`, `/audit`,
  `/folders`, `/jobs`, `/crates`, `/smart-crates`, `/music-review`,
  `/set-builder`, `/exports`, `/sync`, and `/reconciliation`.
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
