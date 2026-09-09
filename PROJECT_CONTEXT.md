# crateIQ Project Context

**Updated:** 2026-09-06

**Purpose:** Read this to understand what crateIQ is NOW — a concise,
low-token current-state engineering context. It is not a chronological log.
Completed-cycle narrative lives in `docs/history/`; superseded/legacy raw
documents live in `docs/archive/` (see `docs/archive/README.md`). For
exhaustive rules and safety policy, `AGENTS.md` is authoritative — this file
summarizes and links rather than duplicating it.

The shared operation/result contract across the major workflows (Process
All, analysis, waveform, tag write, reconciliation, Publish, and jobs) is
defined in `docs/architecture/OPERATION_RESULT_CONTRACT.md`. It inventories
current-state divergences, proposes a minimum common vocabulary, and
prescribes an incremental, read-adapter-first convergence strategy. No
runtime behavior or API/schema changes are authorized by that document.

## Product

CrateIQ is a local-first DJ library preparation and management application.
It takes a DJ's messy music collection through import, metadata cleanup,
provider-backed enrichment, human review, controlled tag writes, BPM/key/
waveform analysis, and promotion into a clean library, then supports
crate/set building, export, and SSD sync/publish.

## Primary Workflow

```
External source
  -> copy into managed Inbox
  -> Process All (deterministic cleanup + provider routing + consensus)
  -> HIGH-confidence fields applied automatically
  -> MEDIUM/LOW/CONFLICT fields go to Needs Review
  -> controlled tag writes to managed Inbox copies
  -> BPM/key/waveform analysis
  -> readiness
  -> explicit "Move Ready to Library" promotion
  -> Library/Genre/Artist/Artist - Title.ext
  -> Crates / Set Builder / Publish
```

## Managed Workspace

```
<root>/
  Inbox/        tracks copied in for preparation, not yet promoted
  Library/      promoted, finished music (Genre/Artist/Artist - Title.ext)
  Quarantine/   reserved; never an automatic destination
```

External source files are never modified. Importing copies files into
`Inbox/`; originals remain untouched. Promotion (`workspace_service.
promote_tracks`) explicitly moves a managed copy from `Inbox/` into
`Library/` — it is never automatic. "Legacy Direct Library" (a single
configured root with no Inbox/Library/Quarantine separation) remains
supported under Settings -> Advanced as a secondary compatibility mode.

## Current Stack

* FastAPI backend (Pydantic, Uvicorn)
* React 18 / Vite / TypeScript frontend, with a Vitest + React Testing
  Library/jsdom component and route-contract test harness
* Python service layer under `backend/app/services/`
* SQLite for tracks, jobs, and operational state
* Local filesystem for the managed music workspace

**Unified waveform.** All waveform surfaces render one canonical
`UnifiedWaveform` (a single mirrored, frequency-tinted canvas — never three
Low/Mid/High rows). The backend decodes mono at 22050 Hz and stores signed
min/max peaks plus optional per-bucket low/mid/high band-energy fractions
(`color_bands`) used only to color each slice of the one waveform; artifact
algorithm `mono-minmax-band-s16-v2` supersedes `mono-minmax-s16-v1`.
Generation is demand-driven via `POST /api/tracks/{id}/waveform/generate`
(dedup'd, cancellable, atomic cache) and now auto-starts from the
`useTrackWaveform` hook the first time a track is opened when no valid
waveform exists (`not_generated`/`stale`/`cancelled`); `failed`/`unsupported`
never auto-retry. The GET endpoint stays read-only. A module-level in-flight
set in the hook guarantees one generation POST per track per tab; additional
same-track consumers observe the in-flight request and attach to the visible
job, with backend dedup as the cross-tab safety net.

## Runtime

* Repo path: this repository root
* Backend: port 8020
* Frontend: port 5175
* Launch/status: `scripts/crateiq-local-services.sh {start|stop|restart|status|logs}`.
  Normal interactive `start` now defaults to the rootless Library Launcher,
  followed by the existing LAN/local-only access choice; it starts the
  long-lived supervisor, rootless backend on 8020, and frontend on 5175 without
  requiring a configured root or `logs/processed.db`. Explicit
  `start-demo-local`, `start-library-local`, and `start-launcher-local`
  variants remain available. PID files/logs live under `.run/` (gitignored).
  For configured-library starts, the
  Settings-managed `.run/local/crateiq.env` root is authoritative at every
  configured-library selection (including sourced aliases and restart); an
  inherited `CRATEIQ_LIBRARY_ROOT` is only a fallback when that file has no
  saved root.
* Safe local supervisor foundation (Checkpoint 1B.2A): the helper starts a
  dedicated, non-reload `backend.app.supervisor` process which owns its
  backend child; the Vite frontend remains independently owned. Supervisor
  IPC is only `.run/local/crateiq-supervisor.sock` (owner-only Unix socket,
  no TCP control listener). A process-lifetime flock at
  `.run/local/crateiq-supervisor.lock` is acquired and the IPC socket is bound
  before an active child is spawned. Runtime directories and lock files are
  descriptor-validated and reject symlinks, non-regular paths, and
  multiply-linked aliases before lock metadata can mutate an inode; accepted
  IPC handlers have byte/time-bounded reads, are actively interrupted during
  shutdown, and quiesce before children or lifetime ownership are released.
  Backend subprocess creation is pinned to long-lived supervisor ownership:
  main startup uses the supervisor main thread, while worker/IPC requests use
  one supervisor-lifetime launch-owner thread. This preserves Linux
  `PR_SET_PDEATHSIG` protection without tying a promoted backend to the
  short-lived registered-activation worker thread; successful worker return
  therefore cannot terminate the committed active child while an uncommitted
  child remains subject to rollback cleanup.
  Normal cooperative socket cleanup atomically withdraws the published public
  link through unique instance-private entries, leaving replacement files or
  symlinks untouched; ambiguous crash/stale artifacts fail closed rather than
  unlink a pathname whose ownership cannot be proven. Process All and bulk waveform reserve
  gate-owned descendant scopes before their durable parent rows, so a future
  drain waits for their deferred durable work. It uses
  fixed allowlisted operations and no shell command input. A candidate is
  always started on a supervisor-reserved
  loopback-only temporary port (never 8020 or the active port). Public health
  is generic; private candidate identity is available only through a
  loopback-only, supervisor-token-protected endpoint and must match the
  generated instance, supervisor instance, canonical root, role, port,
  deterministic root key, verification token, and explicit post-lifespan
  readiness. Candidate startup has a bounded 15-second verification window;
  promoted/rootless startup has a bounded 30-second window for normal DB/cache,
  tool-readiness, and scheduler initialization. Both use repeated short probes.
  The supervisor and its fail-closed parent-death protection are Linux-only.
  `.run/local/library_activation_state.json` and the adjacent OS-level lock
  are restrictive and atomic; malformed or impossible state fails closed.
  Checkpoint 1B.2B-2A adds the internal-only handoff engine: it classifies a
  canonical requested root, starts/verifies a fresh loopback candidate, drains
  the active backend through its token-protected loopback admission bridge,
  inspects persisted exact-key blockers, then retires/reaps the old child and
  launches/verifies a fresh root-bound active child on the original stable
  port. Candidate and promoted identities are verified independently. Saved
  root compatibility data is atomically written only after active verification;
  the exact prior file (including rootless absence) is held in memory and a
  post-replace/fsync error is reconciled against disk before any rollback is
  reported. A child reference is cleared only after confirmed reaping; any
  ambiguous candidate, promoted backend, original-child retirement, or config
  restoration retains ownership and a diagnosable durable `fail_closed` state.
  The engine is exposed only through a registry-ID launcher activation
  contract. The API accepts an opaque ID rather than a path, revalidates the
  saved entry, and submits canonical root/key/classification data through the
  owner-only supervisor IPC. Because successful handoff replaces the serving
  backend, activation uses start-and-status semantics; the supervisor retains
  the safe outcome for the replacement backend to report. It updates registry
  `last_opened_at` only after a verified activation or same-library no-op.
  Recency failure is a bounded warning and never rolls back a verified active
  backend. LAN clients may open a known safe registry ID, but arbitrary path
  inspection, browse, register, and create administration remain local-only.
  Status IPC is handled independently of the handoff-wide ownership lock and
  backend status calls run outside the FastAPI event loop, so polling reports
  `activating` without starving the promoted backend's identity response. A
  clean terminal `idle` transition requires a still-live verified promoted or
  rollback child; promoted B is transferred into the sole in-memory `active`
  slot before success and removed from disposable handoff ownership only after
  the durable idle write. Otherwise the activation rolls back or remains
  `fail_closed`.
  Local-operator launcher administration is now implemented through a bounded,
  one-level directory browser plus explicit register/create endpoints. Browse
  is rooted in environment-derived home/Music and standard mount locations,
  plus registered-library parents; it filters hidden entries, never follows
  symlinks, scans at most 512 entries, and returns at most 100 per request.
  Registration canonicalizes, reclassifies, deduplicates by the existing
  library key, writes under a restrictive cross-process registry lock, and
  leaves `last_opened_at` null until verified activation. Create accepts a
  validated parent and one safe name segment, exclusively creates the target,
  reuses `workspace_service.configure_workspace()`, validates the result with
  the normal launcher classifier, then registers it. Provably operation-owned
  partial initialization is rolled back; ambiguous content is left in place
  and reported. A registry failure after valid initialization leaves the valid
  workspace intact for deterministic registration retry. Create never starts
  recovery/schedulers or creates the rootless runtime jobs database.
  The frontend `/libraries` route is an installation-level chooser outside the
  workspace shell. Rootless workspace routes redirect there; active users can
  reopen it from the sidebar or Settings. It renders at most four recent
  registered libraries, submits only `library_id`, and uses bounded status and
  current-library polling that tolerates the backend replacement gap without
  inferring success from elapsed time. In local mode, Browse Libraries now uses
  the bounded backend directory contract to register only selectable managed or
  strict Legacy Direct libraries, while Create New Library chooses a returned
  parent directory and submits the validated parent plus one name segment. Both
  immediately reuse the same opaque-registry-ID activation flow. A create that
  succeeds on disk but fails registry persistence is reported as partial
  success and routes the operator back to Browse for registration. In LAN mode,
  registered-ID activation remains available while Browse/Create and host paths
  remain unavailable.
  The central process-local operation-admission gate atomically drains the
  bounded durable-create sections for Process All, single/bulk waveform,
  BPM/key analysis, and exact BPM retry only. Checkpoint 1B.2B-1 adds the
  data-side prerequisite: one canonical SHA-256 library key (the established
  waveform identity; `waveform_*.library_id` is a compatibility column name),
  keyed jobs/operation history and recovery, conservative legacy NULL-row
  handling, and persisted switch-blocker inspection. Waveform scheduler
  shutdown, startup/cache maintenance, worker source resolution, generic
  background job updates, and bulk waveform polling retain their immutable
  originating key (and root where source resolution requires it). Reference
  findings filter BPM/tag-write operational rows by that exact key, and active
  queued/processing `waveform_track_state` rows participate in switch blockers.
  Terminal NULL rows are
  excluded from ordinary views; active NULL and known active rows for another
  key fail closed. Global tag backups/job logs are namespaced by key and
  publish destinations are v2 per-key records; a legacy global destination is
  preserved but never inferred. The internal handoff uses this persisted
  blocker only after the drained gate and preserves key/root-bound startup,
  shutdown, recovery, artifacts, and publish settings. The launcher API now
  exposes activation start/status, active registry identity, four recent
  successfully opened entries, and the local-only browse/register/create
  contracts to the `/libraries` frontend launcher. Registered-but-never-opened
  entries remain activation-resolvable but do not masquerade as recents.
* Launcher foundation (Checkpoint 1B.1): installation-scoped recents live at
  `.run/local/library_registry.json` (schema v1, maximum 16 canonical roots;
  launcher returns the latest four). Its classifier is strictly read-only and
  distinguishes `managed_workspace`, `legacy_direct_library`, `empty_folder`,
  `external_music_folder`, `malformed_or_unsafe`, and `missing`. Legacy
  detection uses immutable SQLite inspection and requires the supported
  historical CrateIQ schema core (`tracks`, `track_history`, `pipeline_runs`,
  and `duplicate_groups` with their characteristic pipeline columns), not a
  generic `tracks` table. Rootless
  startup exposes only launcher/health/version/readiness endpoints, does not
  open library indexes or the jobs database, and does not run library recovery
  or schedulers. The frontend gate redirects rootless workspace requests to
  `/libraries`; verified registry-ID activation performs the existing
  supervisor-owned switch into the normal workspace.
  Rootless compatibility seeding uses only the Settings-managed saved root;
  the launcher clears inherited root state before booting rootless, while
  inherited `CRATEIQ_LIBRARY_ROOT` remains a configured-library startup
  fallback. Candidate host-path inspection is enabled only by local-only
  server startup state and is disabled for all LAN-mode requests (including
  loopback Vite proxy traffic); unauthenticated LAN clients must not browse
  server filesystem paths.
  Persisted `fail_closed` activation state is never cleared on startup. The
  explicit `recover-launcher` operator command can reset it to rootless idle
  only while holding both installation locks and after proving no owned
  supervisor/backend survives, atomically withdrawing any proven-stale socket,
  validating registry/saved-root consistency, and archiving the failed state.
  The same command also repairs an `idle` state left beside a crash-stale
  supervisor socket: it holds both locks, validates prior activation-lock PID
  metadata, scans installation-local supervisor/backend ownership, and uses
  the same fresh socket probe plus atomic withdrawal. `idle` with no socket is
  a clean no-op; live, malformed, or ambiguous ownership leaves the pathname
  untouched. Recovery preserves registry recency and compatibility-root bytes
  and never activates the requested library.
* Frontend: <http://127.0.0.1:5175>; backend health:
  <http://127.0.0.1:8020/api/health>; runtime readiness:
  <http://127.0.0.1:8020/api/runtime/readiness>

## Frontend

Current primary navigation (`frontend/src/components/Sidebar.tsx`):

* **LIBRARY** — Inbox, Library, Needs Review
* **DJ** — Crates, Set Builder, Publish
* **TOOLS** — Jobs, Maintenance (hub linking to Quality, Duplicates,
  Reconciliation, Folders, Audit)
* **SYSTEM** — Settings

Legacy/placeholder routes (`Dashboard`, `Collection`, `Tracks`,
`/library-prep`) redirect to their current equivalents rather than staying
independently mounted; specialist pages (Beets Review, Enrichment Review,
Metadata Repair, Metadata Sanitation, BPM Review, Genre Taxonomy, Quality
Review) remain reachable as deep links from Needs Review / Maintenance
rather than living in the primary sidebar. `/duplicate-resolution-plan`
(read-only "Plan only — no files changed.") is a deep link from
`/duplicates` and is not in the primary sidebar either.

## Backend Architecture

Service map (`backend/app/services/`), current primary surfaces:

* `workspace_service` — Inbox/Library/Quarantine state, import, safe
  rename, inline/bulk metadata edit, promotion, and the authoritative
  read-only Inbox preparation-state projection. The projection batches
  track/tag-plan/review/tag-write-history/destination reads, performs no
  network or mutation, and exposes `preparation_state` on
  `GET /api/workspace/inbox/tracks`; promotion preview/apply reuse the same
  result rather than maintaining a second readiness interpretation.
* `preparation_service` — Process All orchestration (clean -> enrich ->
  write-back), background operation tracking
* `needs_review_service` — read-only aggregation across enrichment,
  metadata-repair, and quality review queues
* `quality_review_service` — safe ffprobe preview persisted as replaceable
  snapshots (`quality_review_snapshots`/`quality_review_decisions`), merged
  with durable, per-event findings from `quality_findings_service` (e.g. a
  BPM-analysis decode finding) so both survive a `refresh_preview()`
  snapshot replace in one unified `get_review()` response. Items carry a
  stable `finding_key` (`ffprobe:<track_id>` or `durable:<finding_id>`) so
  a track with more than one open finding can have each decision
  (`reviewed`/`ignore`/`review_later`/`unresolved`) addressed
  unambiguously; `update_decision()` accepts an optional `finding_key` and
  stays backward compatible with track_id-only requests.
  `quality_findings_service` owns the durable `quality_review_findings`
  table (upsert on `track_id`/`reason_code`/`source`, no duplicate rows on
  repeat events) and has no import on `quality_review_service` or
  `analysis_jobs_service`, so both of those import it without a cycle. A
  nullable `resolved_at` gives findings a small active/resolved lifecycle:
  `record_finding()` always clears a prior `resolved_at` on a repeat event
  (reactivating the same row rather than duplicating it), and
  `resolve_finding()` sets it without deleting history; `_durable_items()`
  filters to unresolved rows only, so a track whose finding was resolved by
  a later successful retry drops out of the active Quality/Needs Review
  view while its history row is retained.
* `provider_routing_service` / `consensus_service` — evidence gathering and
  field-level HIGH/MEDIUM/LOW/CONFLICT consensus
* `tag_write_service` — plan/backup/write/re-read/verify controlled tag
  writes, with restore on failure
* `analysis_jobs_service`, `waveform_*` services — BPM/key analysis and
  waveform generation/cache/lifecycle. BPM/key candidate selection and the
  `run()`/`preview()` entry points accept an optional `track_ids` scope:
  omitted (`None`) preserves the existing global missing-value queue
  unchanged; an explicit list -- including an empty one -- restricts
  candidate selection to exactly those track IDs and can only narrow, never
  widen, the candidate universe (nonexistent/ineligible IDs are simply
  absent from the result, never substituted with an unrelated global
  candidate). Scoped runs report `eligible_total`/`considered`/
  `remaining_missing_*` truthfully within the requested scope and persist
  analysis-operation history with `mode='apply_scoped'` (vs. `'apply'` for
  global runs). `preparation_service`'s Process All ANALYZE stage passes its
  own captured Inbox `track_ids` into both bpm_analysis and key_analysis, so
  it can never analyze a track outside its own batch. An external,
  user-supplied `track_ids` scope (HTTP request bodies/query params) is
  bounded to 2000 entries (`_normalize_track_ids()`'s `max_track_ids`
  default) -- this is an API-boundary limit, not a SQL constraint; SQL
  safety comes from chunked `id IN (...)` queries (500 IDs/chunk) that have
  no dependency on that number. A trusted internal caller -- only Process
  All today -- passes `max_track_ids=None` to opt out, since an Inbox can
  legitimately accumulate more tracks than any single import operation
  (`workspace_service._MAX_IMPORT_FILES`) once multiple imports land. Both
  `GET` and `POST /api/analysis/jobs/{job_type}/preview` exist: `GET` takes
  repeated `track_ids` query params (fine for a global or small explicit
  preview); `POST` takes a typed `AnalysisJobPreviewRequest` body and is the
  preferred contract once a scope is large enough that a query string would
  be unwieldy (e.g. a future "Analyze Selected" workflow). Both call the
  identical `analysis_jobs_service.preview()` candidate-selection code as
  `POST .../run`, so preview and run never disagree on the candidate
  universe. BPM analysis tries
  direct aubio decode first; on failure or an unusable result it falls back
  to an FFmpeg decode into a secure temporary WAV outside the managed workspace
  (never rewriting the source), retries aubio against that WAV, and
  records distinct provenance (`aubio` vs `aubio_ffmpeg_decode`) plus a
  non-blocking recovery warning on success. Persisted analysis operations
  expose a derived `outcome` (`complete` / `completed_with_warnings` /
  `completed_with_errors` / `cancelled` / `failed`) alongside `status`, so
  a run with unrecovered track failures cannot render as a plain
  "Complete". Direct-aubio failure is classified as `no_tempo` (exit 0, no
  BPM) / `tool_error` (timeout or process could not start) / `decode_error`
  (non-zero exit PLUS an explicit, small, conservative set of known
  decoder/media-error stderr signals, e.g. "Header missing" or
  "source_avcodec") / `process_error` (non-zero exit with no such evidence
  -- a non-zero aubio exit alone is never treated as proof the audio is
  malformed). A durable, non-blocking `recoverable_audio_decode_warning`
  Quality finding is recorded only when direct aubio showed `decode_error`
  evidence and FFmpeg recovery succeeded, and a durable, high-severity
  `audio_decode_failed` finding only when both direct aubio showed
  `decode_error` evidence and FFmpeg genuinely failed to decode (never for
  a missing tool, a timeout, a cancellation, or an unevidenced
  `process_error`/benign "no tempo found"). That same strict two-stage
  evidence -- and only once the visible finding write itself succeeds -- also
  durably pauses the track from future automatic BPM retries, via a small
  neutral `bpm_retry_policy_service` (`bpm_retry_pauses` table in the
  selected library's `processed.db`, deliberately separate from Quality
  Review's `reviewed`/`ignore`/`review_later`/`unresolved` decision; neither
  ever implicitly drives the other). `_bpm_candidates()` excludes paused
  tracks after missing-BPM eligibility and before `limit` (global or
  scoped), reporting a bounded `suppressed_count`/warning rather than
  substituting an unrelated candidate; every read path and a plain
  successful analysis run never create the pause/finding tables unless a
  pause/finding genuinely exists. Two narrow exact-track endpoints cover
  user control: `POST /api/analysis/jobs/bpm_analysis/tracks/{id}/retry`
  runs the identical blocking analysis with `track_ids=[id]`, `limit=1`,
  bypassing only that one track's own pause (never a global/None scope);
  `POST .../tracks/{id}/resume` clears only the pause -- no analysis, no
  BPM/tag write. A successful retry (direct or FFmpeg-recovered) clears the
  pause and resolves the finding; a repeated genuine failure reactivates the
  same finding row and refreshes the same pause idempotently; a transient
  retry failure (timeout/tool/OSError/cancellation) leaves the prior proven
  pause and finding untouched. `QualityReview.tsx`'s durable finding detail
  exposes "Retry BPM now" and, only while paused, "Resume automatic
  retries" -- separate from `BpmReview.tsx`'s unrelated anomaly-review
  `Queue` action.
* `WaveformGenerationCard` observes an active bulk waveform operation through
  one adaptive chained-timeout stream (1 second during startup, 2.5 seconds
  through two minutes, then 5 seconds steady-state). The timeout ref represents
  only a callback that has not fired, while a separate abortable request slot
  represents the sole in-flight status read. Visibility resume polls
  immediately only when that request slot is idle; terminal states and request
  errors stop the stream, and a monotonically invalidated session prevents old
  callbacks from rescheduling or updating state after supersession or unmount.
* `publish_export_service`, `publish_sync_service` — guarded crate export
  and SSD sync (validate -> preview -> confirm -> execute -> verify)
* `sync_destination_service` — Publish/SSD Sync source and destination
  resolution: source always derives from the active workspace (managed
  `<root>/Library`, or the legacy root itself in Legacy Direct Library
  compatibility mode) — never Inbox/Quarantine, never a hardcoded personal
  path. Destination is an explicit, user-configured absolute path (Settings
  -> Publish / SSD Sync) with no default; execution is blocked until it is
  configured and validated safe.
* reconciliation services — duplicate/orphan/quarantine detection; plan
  propose/validate; and a narrow reviewed DB-only apply/rollback surface.
  Apply reloads and revalidates an exact saved plan, accepts exactly one
  selected `update_path_reference` or eligible
  `mark_stale_processed_state_path` action, then holds SQLite's write
  reservation while it creates and verifies a unique logical SQLite backup
  (including committed WAL state) before mutation. Exact before/after state
  plus verified operation provenance is retained in the existing append-only
  ledger; SQLite read-only URIs safely encode selected-root path characters;
  rollback accepts only those current DB-only operations and rejects
  outside-root restoration. It never moves, renames,
  deletes, or tags a music file and never rewrites queue artifacts. The
  detection/planning engine
  lives in the neutral `utils/path_reconciliation.py` module (no FastAPI or
  `pipeline.py` import); current backend services do not import private
  `pipeline.py` helpers. The read-only Stage 1 reference-artifact detector
  (`GET /api/reconciliation/reference-findings`) scans bounded Categories A,
  B, C, D, and E surfaces from
  `docs/architecture/RECONCILIATION_REFERENCE_ARTIFACT_DESIGN.md`. Stage 2
  adds additive `POST /api/reconciliation/reference-plan/propose` and
  `/validate` endpoints that persist and validate only a distinct,
  root-contained reference-artifact plan JSON. Stage 3 adds
  `POST /api/reconciliation/reference-apply/preview`, a one-action,
  read-only revalidation against an exact plan byte snapshot (including its
  SHA-256), the fresh bounded detector, artifact pre-state, canonical target,
  and applicable collision checks. Completed Stage 4A/B adds confirmed,
  one-action `cue_points.filepath` and `set_playlist_tracks.filepath` writes.
  Apply binds the exact plan path/ID/Stage-3 SHA-256, repeats eligibility and
  row checks under SQLite's writer transaction, creates a hash-verified
  root-contained SQLite backup, verifies the complete row postcondition, and
  appends to `reference_artifact_ledger`. For these legacy path-only tables,
  Stage 1 proposes a correction only when immutable history maps the exact
  stale path to one extant root-contained canonical track; ambiguous
  candidates remain in manual review. The ledger retains the exact stored filepath pre-state, so
  a root-relative reference rolls back exactly as it was stored. Failed apply
  attempts remove their unledgered backups. Its dedicated rollback verifies the
  original backup/hash and exact live after-state before restoring only
  `filepath`, then appends a child ledger row. Completed Stage 4C/D extends
  that reviewed surface to current `field_provenance.track_id` and
  `manual_crate_tracks.track_id`: exact row/non-track-column pre-state,
  orphaned old-ID and canonical replacement checks are required; provenance
  collisions and crate membership collisions fail closed. Stage 1 creates a
  candidate only for a unique safe canonical track with an exact stored local
  fingerprint, duration, and algorithm match to the orphaned ID; rollback
  also refuses an old ID reclaimed by a canonical track. Manual-crate actions
  reject a missing processed DB before recovery opens a writer connection and
  use a verified `manual_crates.db`
  backup and durable prepared ledger state before its separate-DB write, so a
  writer-locked retry can prove and finalize the committed state, or record
  that the crate transaction never committed, rather than silently reporting
  a partial success. Recovery is also bound to the physical crate-row
  transition across regenerated plan artifacts; a different plan cannot claim
  its successful mutation. Rollback writers lazily add reference-ledger
  backup provenance columns before appending rollback history, preserving
  legacy Stage 4A/B records. Completed Stages 5/6 add derived-only Category-C
  regeneration/unresolvable notifications (with surviving review decisions
  preserved) and bounded, root-contained, symlink-safe Category-D stale-path
  detection for the currently emitted M3U/M3U8/JSON/CSV/XML exports. Both produce
  non-executable regeneration actions only; malformed, oversized, unsafe, and
  unsupported export inputs fail closed with warnings. Queue JSON/JSONL mutation remains explicitly unauthorized and
  deferred. Neither reference writer mutates tracks, media, tags, BPM, key,
  cue content, review state, caches, exports, or the DB-only reconciliation
  ledger. Filesystem
  move/rename/quarantine remains a separate, later, explicitly high-risk
  milestone.
* `duplicate_review_service` / `duplicate_resolution_plan_service` — the
  former owns the sole authoritative DB-only human review state
  (`keep`/`ignore`/`review_later`/`unresolved`) against a saved rmlint
  preview snapshot; never deletes, moves, renames, quarantines, or writes a
  tag/file. The latter is a separate, read-only plan-first layer
  (`GET /api/duplicates/resolution-plan`) that derives a deterministic plan
  from the latest snapshot plus its decisions -- `keep` /
  `candidate_for_reversible_resolution` / `no_action` / `review_required`
  per track, never `delete`. A group is plan-eligible only with verified
  content-checksum grouping evidence, exactly one explicit keeper, all other
  members reviewed, and every member path/file re-verified live against the
  selected root; any ambiguity or drift blocks the whole group. Candidate
  items carry an `execution_requirements` object (truthfully labeled
  identity evidence, current stat, backup/collision/restore/ledger
  requirements) describing what a future apply phase must prove -- no
  apply/execute endpoint exists yet. The future execution design is
  deliberately Inbox-only at first: one reviewed action binds to a persisted
  SHA-256 preview, revalidates full hashes, root containment, non-symlink
  paths, and Inbox zone under a per-root lock, then uses a verified backup and
  atomic operational hold outside normal scan roots. It uses its own
  append-only duplicate-resolution ledger and confirmed, drift-checked
  restore/recovery; it does not use the reserved `Quarantine/` folder or
  reference-artifact reconciliation, and Library candidates remain blocked
  pending a separate impact design. See
  `docs/architecture/DUPLICATE_RESOLUTION_SPEC.md`.
* `pipeline.py` compatibility — see Legacy Compatibility below

Route groups: `/api/workspace*`, `/api/tracks*`, `/api/library*`,
`/api/needs-review*`, `/api/jobs*`, `/api/analysis*`, `/api/waveforms*`,
`/api/tag-write*`, `/api/beets-review*`, `/api/enrichment-review*`,
`/api/metadata-repair*`, `/api/metadata-sanitation*`,
`/api/quality-review*`, `/api/duplicates*`, `/api/crates*`,
`/api/smart-crates*`, `/api/playlists*`, `/api/exports*`, `/api/sync*`,
`/api/publish*`, `/api/reconciliation*`. See `AGENTS.md` Section 4.2 for
the full current list.

## Metadata Providers

Evidence sources, in routing order:

1. Embedded tags and filename hints (always available, no network)
2. AcoustID / Chromaprint fingerprinting
3. Beets Python API + MusicBrainz (always tried first)
4. Discogs / Beatport (release/genre/DJ-catalogue evidence)
5. Spotify / Deezer (catalogue corroboration)
6. Last.fm (tag/genre evidence)
7. YouTube (last-resort, low-authority corroboration)

Provider calls are bounded and config/credential-aware. Discogs, Beatport,
Spotify, Last.fm, and YouTube require credentials to provide real
verification value; without credentials they are truthfully reported as
"needs setup" rather than silently skipped. Deezer needs no credentials for
basic search. **Beets Python API is allowed; the `beet` CLI binary is
forbidden** — this is enforced by a static AST regression guard
(`tests/test_no_beet_cli_invocation.py`).

Provider adapters remain synchronous and preserve their existing timeout,
matching, cache, and fallback semantics, but every FastAPI/Process All entry
point that can reach them dispatches the complete synchronous provider
workflow to a worker thread. No external provider network wait runs on the
uvloop event-loop thread. Process All joins a bounded in-flight provider
worker before propagating cancellation so its durable library scope is not
released while that worker can still update review/cache state. The shared
beets MusicBrainz client serializes access to its singleton rate limiter and
closes its pooled session after every lookup attempt, including errors.

Traxsource is legacy: it exists only in old `pipeline.py`-era code and is
not part of the current provider set — do not treat it as active.

The Settings metadata-source response is the source of truth for source roles
(`local_input`, `analysis_only`, or `track_enrichment`) and readiness. An
explicit Inbox **Enrich Selected** action may select only sources marked
`selectable_for_enrichment`: globally enabled, configured, ready, and usable
by the current provider router. `source_ids` is optional for backward
compatibility on `POST /api/workspace/prepare/enrich`; when omitted, the
server resolves the globally enabled + ready track-enrichment defaults. When
provided, every ID must be validated and the selected sources are eligible to
be queried, not guaranteed to run, because staged routing may stop early after
strong consensus. Process All continues to use the global defaults and does
not open the per-batch selector. Credentials never enter the Inbox request.

Beets and MusicBrainz readiness for this routing path reflects the shared
Beets Python API used by `musicbrainz_client`; the forbidden `beet` CLI is not
used. Local tags and filename hints remain automatic local-input evidence, and
Mixed In Key remains an analysis-only trusted input rather than a selectable
track-enrichment provider.

## Confidence / Review Model

Consensus is field-level and explainable, one verdict per field:

* **HIGH** — strong identity evidence, no conflicts; eligible for
  auto-apply during Process All. A HIGH track identity never implies every
  field is HIGH (e.g. genre can independently land on CONFLICT).
* **MEDIUM** — plausible but not strongly corroborated.
* **LOW** — weak or single-source evidence.
* **CONFLICT** — providers disagree.

MEDIUM, LOW, and CONFLICT fields go to Needs Review rather than being
auto-applied. Existing non-empty metadata is never silently overwritten,
except an explicit junk/placeholder-value exception paired with a HIGH
replacement.

## Genre Intelligence (Strategy Phase 3)

`genre_taxonomy_service` is the single deterministic genre taxonomy/mapping
resolver; `backend/app/api/routes/genres.py` (`/api/genres/*`) is a thin
typed adapter over it, and `consensus_service.normalize_genre` delegates to
the same service-layer resolver adapter (`resolve_consensus_genre`) -- one
shared mapping contract, not a second hardcoded table.

* **Repository config**: `config/genre_taxonomy.json` (preferred genre
  list) and `config/genre_mappings.json` (default raw-genre -> preferred-
  genre mappings), both schema-validated and cached at load time
  (`repo_taxonomy()` / `repo_mappings()`), in deterministic file order.
  Never written to by the app -- edits always land in the local index.
* **Local index overrides**: the existing `genre_taxonomy` / `genre_mappings`
  tables in the selected library's `processed.db` hold only user
  customizations (additions, edits, disables). A DB row for a given
  name/raw-genre fully overrides the matching repository default at
  resolution time; disabling only affects future resolution, never
  already-stored track values.
* **Resolution precedence** (`resolve_genre()`), per raw genre string:
  1. an explicit enabled user mapping;
  2. an exact match against an enabled preferred canonical genre name
     (identity match, not a guess);
  3. an enabled repository default mapping;
  4. no hit -> Needs Review. Ambiguous/unmapped raw genres (e.g. generic
     "afro", "dance") are never guessed -- their repository default entries
     explicitly mark `needs_review: true`; only an explicit user mapping may
     collapse them to a specific genre.
* **Normalization**: deterministic casefold + whitespace/hyphen/underscore
  collapse + punctuation strip (keeping `&`), pinned by tests. The same
  `normalize_key()` contract is used for resolver lookups and write-time
  duplicate checks, so punctuation/spacing variants resolve consistently.
* **Preview/apply contract**: `GET /api/genres/review` is read-only.
  `POST /api/genres/review/preview-refresh` computes a fresh resolution per
  track (never touches track columns) and saves a review snapshot.
  `POST /api/genres/review/apply` is explicit, selected-track-scoped,
  requires `confirm: true`, preserves the raw `genre` column, writes only
  `normalized_genre`/genre provenance columns, and records provenance via
  `field_provenance_service` (`origin="system"`, no confidence value --
  deterministic app logic never masquerades as provider confidence).
  Needs-Review items and disabled/invalid-target mappings are always
  skipped, never auto-applied. Repeated apply of an identical mapping is
  idempotent (no duplicate provenance rows, matching `field_provenance_
  service`'s existing repeat-event contract).
* **Needs Review integration**: unchanged and already consolidated --
  `metadata_repair_queue_service`'s `missing_genre`/`missing_normalized_genre`
  issues and `needs_review_service`'s GENRE category read the same
  `tracks.normalized_genre` column this service writes, so an applied
  normalized genre clears its own pending Needs Review entry.

## Field Provenance / Track Identity (Metadata Model Phase 2)

Additive foundation living in the same selected-library index DB as
`quality_review_findings`/`bpm_retry_pauses` (`<root>/logs/processed.db`),
created lazily on first write, never on a read.

* `field_provenance_service` owns a `field_provenance` table recording, per
  `(track_id, field_name)`: the observed/applied value, `origin`
  (`provider` | `user` | `system`), `source`, an optional provider
  `confidence` verdict (HIGH/MEDIUM/LOW/CONFLICT), a bounded reason/evidence
  reference, and current-vs-history status via `is_current` (enforced by a
  partial unique index — at most one current row per field). A `confidence`
  value may only be paired with `origin='provider'`; `record()` rejects any
  attempt to pair it with `user`/`system` origin, so a manual edit can never
  masquerade as provider HIGH confidence. Recording an identical repeat
  event refreshes the current row instead of duplicating it (Process All
  reruns are idempotent); a genuinely different value closes the previous
  row into history and inserts a new current one. Wired into the two
  authoritative write paths: `enrichment_review_service.apply_selected()`
  (HIGH-confidence Process All auto-apply and explicit enrichment-review
  apply both funnel through it — `origin='provider'`) and
  `workspace_service.edit_inbox_track_metadata()` /
  `bulk_edit_apply()` (manual Inbox edits — `origin='user'`, no
  confidence). It only records local-index decisions; it never writes tags
  itself — tag writes still go through `tag_write_service`.
* `track_identity_service` owns a `track_fingerprints` table caching an
  optional, local-only Chromaprint fingerprint per track, reusing the
  existing `fpcalc`/`acoustid_client.fingerprint_file` capability (no new
  dependency, no network call, no `beet` CLI). Explicit per-track action
  only (`POST /api/tracks/{id}/fingerprint`), never automatic and never
  required to open/use the library; a missing `fpcalc` tool yields a
  truthful `unavailable` status rather than a failure. Track numeric `id`
  remains the sole stable local identity — a fingerprint is optional
  corroborating evidence only, never a primary key, and is never used to
  auto-deduplicate tracks (duplicate resolution stays a separate, explicit
  workflow).
* `GET /api/tracks/{id}` exposes both as additive, backward-compatible
  `identity`/`provenance` fields; a plain GET never creates either table.
* No structured artist/title/version columns and no backfill of provenance
  for pre-existing tracks were added — both are deferred until a concrete
  consumer needs them (see `NEXT_TASKS.txt`).

## Metadata Write Safety

All controlled tag writes go through `tag_write_service`'s exact contract:
build a diff/plan -> validate the file hasn't gone stale since the plan was
built -> take a hash-verified backup outside the scanned tree -> write only
the approved diffed fields -> re-read and verify every changed field ->
preserve the backup for restore on failure. Writes only ever touch managed
Inbox copies; external originals are never modified. Promotion to `Library/`
is always an explicit, separately confirmed action. Mixed In Key is
authoritative for BPM/key/cue points and is never overwritten (see
`AGENTS.md` Section 5 for the full rule).

## Readiness

Inbox Redesign Checkpoint 1 defines five user-facing preparation states in
strict precedence order:

1. **Write Blocked** — the managed file cannot currently be safely written or
   verified (unsupported format, missing/out-of-scope source, current planner
   blocker, or a latest failed write whose DB/file difference is still open).
2. **Needs Attention** — required Artist/Title/Genre is missing, current
   suspicious metadata or a serious processing issue exists, or the intended
   Library destination already exists.
3. **Review** — a pending suggestion/conflict in the latest Inbox-scoped
   enrichment snapshot still needs a decision. Applied, ignored, review-later,
   and superseded history do not count.
4. **Unsaved** — approved `tracks` metadata differs from live managed-file
   tags and no higher-priority state applies.
5. **Ready** — the complete promotion contract passes.

The projection is read-only: `tracks` remains approved working metadata,
`tag_write_service` remains the only tag writer, and external originals are
never inspected as writable managed sources or modified. Required for Ready:

* Artist, Title, Genre present
* Metadata write verified (if any writes were pending)
* Zero serious current error or actionable provider review
* Managed source present and safely contained in Inbox
* No existing destination (identical content is also fail-closed because
  promotion apply never overwrites or silently removes the Inbox copy)

Warnings only (do not block promotion):

* BPM
* Key
* Waveform

Quality Review (ffprobe snapshot findings and durable findings alike,
including `recoverable_audio_decode_warning` and `audio_decode_failed`) is
not currently consulted by promotion readiness at all -- it is purely
informational, matching its pre-existing status.

Inbox Redesign Checkpoint 2 builds the read-only workspace layer on that same
contract. `GET /api/workspace/inbox/tracks` accepts `preparation_status`,
searches filename/Artist/Title/Genre, and returns search-scoped status counts,
the full filtered total, and current Inbox IDs for safe selection validation.
The service performs one candidate preparation projection, then reuses it for
counts, status filtering, authoritative readiness sorting, pagination, and
response rendering. The companion
`GET /api/workspace/inbox/tracks/{track_id}/inspection` is a single-track,
read-only projection for URL-backed (`/inbox?track=<id>`) inspector restore.

Inbox selection is ID-based and persists across sorting, refetch, filtering,
and pages. The UI distinguishes total selected from visible selected, exposes
clear-hidden and clear-all actions, labels the header checkbox as Select
Visible, and supports shift-click ranges only within rendered rows. The Track
Inspector shows Metadata, authoritative Status/reasons/warnings/write/
promotion state, Analysis/waveform state, and managed File context. Its
DB-first metadata editing remains separate from the explicit Save to File
action, which is enabled only for pending writable changes and keeps the
Inspector open while the verified result is shown.

Checkpoint 3A is complete: Inbox single-track and bulk metadata editing for
Artist/Title/Genre/Album is DB-first, records existing manual provenance, and
refreshes the authoritative preparation state without writing file tags.
Checkpoint 3B is complete: the frontend provides inline Artist/Title/Genre
editing in the dense Inbox table, Album editing in the Track Inspector and
Bulk Edit panel, four-field opt-in bulk preview/confirmation, local validation,
targeted refreshes, selection/filter/sort preservation, and clear Unsaved
pending-field presentation. Bulk preview reports selected, eligible,
changeable, already-matching, skipped, and missing tracks explicitly. Unsaved
filter counts are primary-status counts; a higher-precedence state may still
have `pending_fields` and `write.has_unsaved_changes`. These controls still
update approved working metadata only; they never call tag-write APIs or imply
that file tags changed. The Save to File checkpoint is complete: the frontend
uses the existing tag_write_service plan/apply contract with 50-track request
chunking, a concise exact-diff preview, managed-copy confirmation wording,
verified per-track result reporting, stale-plan rejection, no-op/blocked
handling, and authoritative Inbox refresh after apply. It does not add a new
writer or a permanent SAVED preparation state. Per-batch provider-source
selection is complete for explicit Enrich Selected. Inline enrichment review
is complete: the Inbox Track Inspector now exposes a Review section that reads
the shared enrichment_review_service decision queue (no new review store) and
supports field/proposal "Use Suggested" (DB-only apply) and "Keep Current"
(ignored) decisions that refresh the authoritative preparation state.
`GET /api/workspace/inbox/tracks/{track_id}/enrichment-review` is a thin
read-only track-scoped aggregation of actionable (pending) suggestions.
Needs Review merge/demotion, Process All demotion, the full mobile redesign,
and the final focused Impeccable pass remain deferred.

## Data Stores

* Managed workspace (music files) under the configured root:
  `Inbox/`, `Library/`, `Quarantine/`
* Pipeline/index DB (compatibility): `<root>/logs/processed.db` — also
  hosts the additive `field_provenance` and `track_fingerprints` tables
  (see Field Provenance / Track Identity above), created lazily on first
  write
* Backend jobs/operations DB: `backend/data/jobs.db` (job history, analysis/
  waveform/publish/preparation operations, tag-write history — separate
  from the music index)
* Tag-write backups: hash-verified, outside the scanned tree, under
  `backend/data/`
* Waveform cache: bounded, LRU-pruned, under `backend/data/`
* Provider cache and runtime config: `backend/data/`, `.run/local/` (both
  gitignored where they may hold credentials)

## Legacy Compatibility

* `pipeline.py` predates the FastAPI/React managed-workspace application.
  It is not the primary product architecture but remains partly
  load-bearing as a maintenance CLI (e.g. `db-prune-stale`,
  `rekordbox-export`, `set-builder`, and other `toolkit_runner`-allowlisted
  subcommands — see `docs/architecture/TOOLKIT_COMMAND_CLASSIFICATION.md`).
  `modules/`, `ai/`, `intelligence/`, `config.py`, `db.py` still back it.
  `utils/` additionally hosts `path_reconciliation.py`, the neutral
  path-audit/path-reconcile engine shared by `pipeline.py`'s CLI wrappers
  and the current backend reconciliation route/services — the current
  reconciliation path does not import private `pipeline.py` helpers.
* "Legacy Direct Library" mode remains supported under Settings ->
  Advanced, behind a collapsed disclosure, secondary to the managed
  workspace. See
  `docs/architecture/LEGACY_DIRECT_LIBRARY_BOUNDARY.md` (Phase 7) for the
  explicit boundary between it and the current workspace-selected model:
  every surviving legacy entry point, why each remains, and what a future
  removal phase would require.
* Do not make broad `pipeline.py`/`config.py` architecture changes unless
  explicitly asked.

## Current Known Issues

Architecture-level gaps, current as of this writing (see `AGENTS.md`
Section 17 for the authoritative list; see `NEXT_TASKS.txt` for the
task-level backlog):

1. **Trusted-local, no-auth security model** — no login, sessions, user
   model, roles, or route guards. Do not expose the backend remotely
   without addressing this first.
2. **Legacy `pipeline.py`/`config.py` coexistence** — remains partially
   load-bearing alongside the current FastAPI/React application, a source
   of confusion for anyone reading old code first.
3. **"Legacy Direct Library" compatibility mode** remains alongside the
   managed-workspace workflow.
4. **Credential-dependent providers** (Discogs, Beatport, Spotify, Deezer,
   Last.fm) need live-credential verification before their real matching
   value can be confirmed in practice.
5. **Reconciliation filesystem and queue repair remain future work** —
   reviewed DB-only apply and DB-only rollback now support allowlisted,
   sufficiently proven current path-reference operations. Filesystem
   move/rename/delete/quarantine, queue/reference-file rewriting, filesystem
   rollback, weak/ambiguous automated repair, and `processed_state` relinks
   without sufficient source-row proof remain unsupported (see
   `docs/architecture/FULL_RECONCILIATION_APPLY_SPEC.md`).
6. **Duplicate resolution apply remains future work** — the current backend
   supports only a read-only, plan-first `/duplicates/resolution-plan`
   surface derived from the latest saved Duplicate Review snapshot and its
   human decisions; it has no apply/execute endpoint and performs zero file,
   tag, or track-metadata writes. The reversible, confirmation-gated Inbox
   hold/backup/ledger/restore design is complete, but implementation must be
   separately approved and follow real managed-workspace use plus
   disposable-root acceptance tests (see
   `docs/architecture/DUPLICATE_RESOLUTION_SPEC.md`).

Publish/Sync configuration portability (hardcoded local paths) was fixed in
a prior cycle — see Backend Architecture below. The dangerous pre-managed-
workspace runtime scripts and systemd units (unattended pipeline timer/
watcher, `setup.sh`, `beet` CLI bootstrap, `rsync --delete` transfer script)
were retired and archived to `docs/archive/legacy-runtime/` in this cycle;
current local service management is `scripts/crateiq-local-services.sh`.

## Development Priorities

Unless the user states otherwise, prefer this order (from `AGENTS.md`
Section 18): refine the core managed-library workflow based on real usage;
safely remove legacy architecture confusion; fix Publish/Sync path
portability; improve provider matching using real-world evidence; address
authentication/security before any remote/multi-user deployment; packaging/
production readiness after the above.
