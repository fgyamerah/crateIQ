# crateIQ Project Context

**Updated:** 2026-08-08

**Purpose:** Read this to understand what crateIQ is NOW — a concise,
low-token current-state engineering context. It is not a chronological log.
Completed-cycle narrative lives in `docs/history/`; superseded/legacy raw
documents live in `docs/archive/` (see `docs/archive/README.md`). For
exhaustive rules and safety policy, `AGENTS.md` is authoritative — this file
summarizes and links rather than duplicating it.

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
* React 18 / Vite / TypeScript frontend
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
rather than living in the primary sidebar.

## Backend Architecture

Service map (`backend/app/services/`), current primary surfaces:

* `workspace_service` — Inbox/Library/Quarantine state, import, safe
  rename, inline/bulk metadata edit, promotion
* `preparation_service` — Process All orchestration (clean -> enrich ->
  write-back), background operation tracking
* `needs_review_service` — read-only aggregation across enrichment,
  metadata-repair, and quality review queues
* `provider_routing_service` / `consensus_service` — evidence gathering and
  field-level HIGH/MEDIUM/LOW/CONFLICT consensus
* `tag_write_service` — plan/backup/write/re-read/verify controlled tag
  writes, with restore on failure
* `analysis_jobs_service`, `waveform_*` services — BPM/key analysis and
  waveform generation/cache/lifecycle
* `publish_export_service`, `publish_sync_service` — guarded crate export
  and SSD sync (validate -> preview -> confirm -> execute -> verify)
* reconciliation services — duplicate/orphan/quarantine detection, plan
  propose/validate (apply remains unimplemented — see Known Issues)
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

## Data Stores

* Managed workspace (music files) under the configured root:
  `Inbox/`, `Library/`, `Quarantine/`
* Pipeline/index DB (compatibility): `<root>/logs/processed.db`
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
  load-bearing for maintenance and reconciliation compatibility (e.g.
  `db-prune-stale`, some reporting flows). `modules/`, `ai/`,
  `intelligence/`, `utils/`, `config.py`, `db.py` still back it.
* "Legacy Direct Library" mode remains supported under Settings ->
  Advanced, behind a collapsed disclosure, secondary to the managed
  workspace.
* Do not make broad `pipeline.py`/`config.py` architecture changes unless
  explicitly asked.

## Current Known Issues

Architecture-level gaps, current as of this writing (see `AGENTS.md`
Section 17 for the authoritative list; see `NEXT_TASKS.txt` for the
task-level backlog):

1. **Trusted-local, no-auth security model** — no login, sessions, user
   model, roles, or route guards. Do not expose the backend remotely
   without addressing this first.
2. **Publish/Sync configuration portability** — hardcoded local paths;
   pending a fix.
3. **Legacy `pipeline.py`/`config.py` coexistence** — remains partially
   load-bearing alongside the current FastAPI/React application, a source
   of confusion for anyone reading old code first.
4. **Legacy scripts under `scripts/`** — some are still present and
   potentially dangerous; not yet cleaned up. Verify before running.
5. **"Legacy Direct Library" compatibility mode** remains alongside the
   managed-workspace workflow.
6. **Credential-dependent providers** (Discogs, Beatport, Spotify, Deezer,
   Last.fm) need live-credential verification before their real matching
   value can be confirmed in practice.
7. **Reconciliation apply is unimplemented** — DETECT/PROPOSE/REVIEW/
   VALIDATE exist; a real APPLY workflow (backups, per-action confirmation,
   restore path) is still only planned (see
   `docs/architecture/FULL_RECONCILIATION_APPLY_SPEC.md`).

## Development Priorities

Unless the user states otherwise, prefer this order (from `AGENTS.md`
Section 18): refine the core managed-library workflow based on real usage;
safely remove legacy architecture confusion; fix Publish/Sync path
portability; improve provider matching using real-world evidence; address
authentication/security before any remote/multi-user deployment; packaging/
production readiness after the above.
