# crateIQ Project Context

**Updated:** 2026-08-11

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

## Runtime

* Repo path: this repository root
* Backend: port 8020
* Frontend: port 5175
* Launch/status: `scripts/crateiq-local-services.sh {start|stop|restart|status|logs}`
  (also `start-demo-local`, `start-library-local` variants); PID files/logs
  under `.run/` (gitignored)
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
  rename, inline/bulk metadata edit, promotion
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

Traxsource is legacy: it exists only in old `pipeline.py`-era code and is
not part of the current provider set — do not treat it as active.

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

Required for promotion:

* Artist, Title, Genre present
* Metadata write verified (if any writes were pending)
* Zero serious unresolved error

Warnings only (do not block promotion):

* BPM
* Key
* Waveform

Quality Review (ffprobe snapshot findings and durable findings alike,
including `recoverable_audio_decode_warning` and `audio_decode_failed`) is
not currently consulted by promotion readiness at all -- it is purely
informational, matching its pre-existing status.

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
