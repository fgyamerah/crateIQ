# CrateIQ

[![Status](https://img.shields.io/badge/status-active-green)](#current-platform-status)
[![Backend](https://img.shields.io/badge/backend-FastAPI-009688)](#backend-api)
[![Frontend](https://img.shields.io/badge/frontend-React%20%2B%20Vite-646cff)](#frontend-dashboard)
[![Safety](https://img.shields.io/badge/safety-dry--run%20first-blue)](#safety-model)
[![Mode](https://img.shields.io/badge/mode-review--first-informational)](#core-philosophy)

CrateIQ is a local-first DJ library operations platform for building and maintaining a clean, auditable, Rekordbox-ready music library.

It is built around deterministic automation, explicit review queues, and conservative metadata ownership. It helps inspect, normalize, reconcile, and enrich a DJ library without handing control of musical analysis or performance-critical data to unstable automation.

CrateIQ is not a Rekordbox replacement. It is an operational layer around a DJ library: it prepares, audits, reviews, and organizes metadata so Rekordbox and Mixed In Key can remain the source of truth for DJ performance workflows.

## Overview

CrateIQ started as a pipeline for cleaning messy downloaded audio files and evolved into a broader library control system:

- A canonical SQLite `tracks` table representing the current library state.
- A historical `processed_state` table used for stage tracking, audit, and provenance.
- Deterministic local metadata extraction from existing audio tags.
- Conservative filename parsing with confidence scoring.
- Online enrichment candidate scoring without automatic metadata application.
- Human review state for enrichment decisions.
- A root-aware read-first backend API.
- A dense operational frontend dashboard for browsing tracks, issues, folders, audit reports, and enrichment queues.

The platform is designed for large libraries where accidental writes, metadata churn, and path drift are more dangerous than missing a single enrichment opportunity.

## Core Philosophy

CrateIQ follows a review-first operating model:

- Deterministic operations before AI or online lookup.
- Local data before external providers.
- Dry-run by default for write-capable commands.
- Apply mode requires explicit confirmation with `--apply --yes`.
- No silent tag writes.
- No silent file moves.
- No silent database mutation.
- Current-state data and historical/audit data are separated.
- Human review is required before applying enrichment metadata.

Metadata ownership is explicit:

- `tracks` owns CrateIQ's canonical current-state library record.
- `processed_state` owns historical processing and incremental stage audit.
- Mixed In Key and Rekordbox own BPM, key, beatgrid, cue, and performance preparation data.
- CrateIQ must not overwrite BPM, key, cues, beatgrids, or other performance-critical DJ data.

The project prefers a safe skip over a confident-looking wrong update.

## Architecture

CrateIQ is organized as a local pipeline plus an operational app.

```text
Audio files / DJ library root
        |
        v
pipeline.py commands
        |
        +-- path audit and path planning
        +-- tracks table build/update helpers
        +-- local metadata extraction
        +-- deterministic filename parsing
        +-- metadata scoring and enrichment review
        |
        v
logs/processed.db
        |
        +-- tracks           canonical current-state table
        +-- processed_state  stage history and audit trail
        |
        v
FastAPI backend
        |
        v
React/Vite dashboard
```

The backend reads from the selected library root. The frontend uses the backend API and does not directly mutate files or databases.

Important roots and artifacts:

- `<root>/logs/processed.db`
- `<root>/logs/path_audit/`
- `<root>/logs/path_reconcile/`
- `<root>/logs/metadata_extract/`
- `<root>/logs/enrichment/`
- `<root>/data/intelligence/enrichment_review_queue.jsonl`
- `<root>/data/intelligence/enrichment_review_state.json`

## Current Platform Status

| Phase | Status |
|---|---|
| Phase 1 | Complete |
| Phase 2 | Complete |
| Phase 3 | Stable with legacy organizer caveat |
| Phase 4 | Complete |
| Phase 5 | Complete |
| Phase 6 | Complete |
| Phase 7 | Not started |
| Phase 8 | Complete |

Phase 3 is stable for the current canonical path/database work, with one caveat: `modules/organizer.py` is legacy/deprecated and should not be treated as the forward path for new organization behavior.

## Major Features

- Root-aware pipeline and backend operation.
- Canonical `tracks` table for current library state.
- `processed_state` history for incremental stage tracking and audit.
- Read-only path audit reports.
- Dry-run path reconciliation planning.
- Local metadata extraction from existing audio tags.
- Deterministic filename fallback parsing with confidence levels.
- Enrichment candidate queue and review state.
- Controlled DB-only enrichment apply for approved high-confidence rows.
- FastAPI backend over safe library data.
- React/Vite operational dashboard.
- Track filtering, issue grouping, folder stats, audit viewer, enrichment moderation.
- Large-library performance hardening with API caps, DB indexes, request timing, queue caching, debounced search, persisted UI state, and virtualized table rendering.

## Path Audit System

The path audit system checks whether the database and filesystem still agree.

It is read-only. It does not move files, delete rows, write tags, or reconcile paths automatically.

Typical output lives under:

```text
<root>/logs/path_audit/
```

The audit system can identify:

- Files referenced by `tracks` that are missing on disk.
- Audio files present on disk but not tracked.
- Stale `processed_state` records.
- Candidate path mismatches.
- Canonical-source summary data used by the backend stats endpoint.

The backend exposes the latest audit through:

```text
GET /api/audit/latest
GET /api/stats
```

## Path Reconciliation

Path reconciliation is intentionally separate from path audit.

Audit answers: what is inconsistent?

Reconciliation answers: what would be safe to fix?

Current reconciliation behavior is planning-first. It should not be treated as a blind repair tool. Any write-capable reconciliation path must be explicit and narrowly scoped.

CrateIQ does not currently perform broad automatic path reconciliation in the frontend.

## Canonical Tracks Database

The `tracks` table is the canonical current-state table.

It represents what CrateIQ currently believes is in the active library. Backend track browsing, issue counts, folder stats, overview stats, metadata extraction, and enrichment apply all operate against `tracks`.

`processed_state` is not the canonical current-state table. It is history and audit:

- Which pipeline stages saw which paths.
- File size and mtime fingerprints.
- Stage-level processing status.
- Incremental-run skip tracking.

This distinction matters. Current UI and API views should prefer `tracks`; historical diagnosis should inspect `processed_state`.

## Metadata Extraction

Local metadata extraction populates missing `tracks` fields from metadata already present in audio files.

Command:

```bash
python3 pipeline.py extract-track-metadata --root <root>
```

Dry-run is the default. Apply mode requires:

```bash
python3 pipeline.py extract-track-metadata --root <root> --apply --yes
```

Extraction is local and deterministic:

- No online providers.
- No AI.
- No tag writes.
- No audio file changes.
- DB-only updates when applied.

Fields considered when available:

- `artist`
- `title`
- `album`
- `genre`
- `bpm`
- `key_musical`
- `duration_sec`
- `bitrate_kbps`

Existing non-empty fields are preserved. BPM and key fields are especially conservative because Mixed In Key and Rekordbox own musical analysis data.

Logs are written under:

```text
<root>/logs/metadata_extract/
```

## Deterministic Filename Parsing

Filename parsing is a fallback only. Embedded tags win when valid.

The parser handles common DJ-library filename patterns such as:

- `Artist - Title`
- featured artist text such as `feat.`
- remix/version suffixes like `(Original Mix)`
- malformed but recoverable separators
- suffix junk such as trailing `-Gold`

The parser assigns:

- `HIGH`
- `MEDIUM`
- `LOW`

Fallback extraction applies only when confidence is at least `MEDIUM`. Weak parses are rejected safely rather than inventing metadata.

Examples:

```text
C Minor - Kunapendeza feat. Alai K.mp3
artist: C Minor
title:  Kunapendeza feat. Alai K
```

```text
Javier Mio - Ampreiah (Original Mix).aif
artist: Javier Mio
title:  Ampreiah (Original Mix)
```

Malformed examples such as long descriptive strings without a reliable artist/title separator are intentionally rejected.

## Metadata Scoring And Review Workflow

CrateIQ can score enrichment candidates and place them into a review workflow.

The key principle: candidate scoring is not the same as metadata application.

Review artifacts:

```text
<root>/data/intelligence/enrichment_review_queue.jsonl
<root>/data/intelligence/enrichment_review_state.json
```

Review states:

- `pending`
- `approved`
- `rejected`
- `deferred`

Approved review rows can later be applied in a controlled DB-only step:

```bash
python3 pipeline.py enrichment-apply-approved --root <root>
```

Dry-run is the default. Apply mode requires:

```bash
python3 pipeline.py enrichment-apply-approved --root <root> --apply --yes
```

Controlled apply rules:

- Only approved review-state items.
- Only `HIGH` confidence.
- Only update `tracks`.
- Only allowed fields: `artist`, `title`, `album`, and optional `label` / `isrc` when columns exist.
- Never update BPM.
- Never update key.
- Never update cues.
- Never write tags.
- Never modify audio files.
- Never rename files.

Apply logs are written under:

```text
<root>/logs/enrichment/
```

## Backend API

The backend is a FastAPI app exposing the selected library root through controlled endpoints.

The selected root can be configured with the preferred CrateIQ variable:

```bash
export CRATEIQ_LIBRARY_ROOT=/path/to/library
```

`CRATEMINDAI_LIBRARY_ROOT` remains a deprecated fallback for existing local
setups when `CRATEIQ_LIBRARY_ROOT` is not set. Database paths, API paths,
pipeline command names, and serialized queue formats are intentionally not
renamed for compatibility. See [.env.example](.env.example).

Representative endpoints:

```text
GET  /api/health
GET  /api/stats
GET  /api/tracks
GET  /api/tracks/{id}
GET  /api/tracks/issues
GET  /api/library/folders
GET  /api/library/overview
GET  /api/enrichment/queue
GET  /api/enrichment/review/state
GET  /api/enrichment/review/export
GET  /api/enrichment/review/summary
POST /api/enrichment/review/{track_id}/approve
POST /api/enrichment/review/{track_id}/reject
POST /api/enrichment/review/{track_id}/defer
POST /api/enrichment/apply-approved/dry-run
POST /api/enrichment/apply-approved/apply?confirm=true
GET  /api/audit/latest
```

Backend safety rules:

- Root containment is enforced.
- Track browsing is read-only.
- Audit and overview endpoints do not perform expensive automatic filesystem scans.
- Enrichment review endpoints write only queue review state.
- Apply-approved endpoint writes only approved metadata fields to `tracks`.
- Apply endpoint requires explicit `confirm=true`.

## Frontend Dashboard

The frontend is a React/Vite operational dashboard for CrateIQ.

It is intentionally dense and work-focused, not a marketing UI.

Supported routes:

| Route | Workflow |
|---|---|
| `/` | Library workspace and track browsing |
| `/quality` | Library quality summary |
| `/issues` | Track issue review |
| `/enrichment` | Enrichment queue review |
| `/metadata-repair` | Deterministic metadata repair review |
| `/metadata-sanitation` | Metadata sanitation review |
| `/bpm-review` | BPM anomaly scan and review |
| `/audit` | Latest path/library audit |
| `/folders` | Folder-level library view |
| `/jobs` | Allowlisted pipeline job submission and monitoring |
| `/set-builder` | Set generation and saved set review |
| `/exports` | Export validation and Rekordbox export jobs |
| `/sync` | SSD sync preview and controlled execution |
| `/reconciliation` | Read-only reconciliation ledger and plan validation |

Legacy or incomplete page implementations remain in `frontend/src/pages/` for
reference but are intentionally hidden. `/dashboard`, `/collection`, `/tracks`,
and `/settings` redirect to `/`; the singular `/export` and `/ssd-sync` aliases
redirect to their supported routes. `Collection.tsx` includes unfinished controls,
`Settings.tsx` is a placeholder, and `Dashboard.tsx`/`Tracks.tsx` duplicate the
current library and operations surfaces.

Core dashboard capabilities:

- Track table with pagination, sorting, issue badges, search, and selection.
- Selected track inspector.
- Issue count page with clickable filters.
- Folder statistics from DB paths only.
- Overview cards for totals, BPM coverage, key coverage, missing metadata, parse confidence, and genre counts.
- Enrichment queue moderation with approve/reject/defer.
- Review summary and export.
- Apply-approved dry-run preview and controlled apply button.

### Library view (`/`)

The Library route (`frontend/src/pages/CrateMind.tsx`, `section === 'library'`)
was redesigned around a dark emerald/teal/cyan/violet palette (design tokens
in `frontend/src/index.css`, e.g. `--brand-teal`, `--brand-cyan`,
`--brand-violet`, `--brand-coral`):

- A data-quality **Library status strip** (distinct from the runtime
  `ReadinessBanner`) that appears only when open track issues exist, with a
  "Review issues" link and a "Re-scan" button that re-runs the existing
  read-only refresh (no pipeline scan is dispatched).
- A **filter chip row** for genre, BPM range, and has-key/missing-key — each
  chip maps 1:1 to a query parameter `GET /api/tracks` already accepts.
  Camelot-range, energy, mood, and source filters from early design mockups
  have no backing field on `TrackSummary` yet and are intentionally omitted
  rather than wired to a non-functional control.
- Restyled **overview cards** (Total Tracks, BPM Coverage, Key Coverage,
  Missing Key, Missing Artist/Title, Parse Confidence) plus a **Duplicates**
  card that reads "Not available" — library-wide dedupe is a CLI-only
  pipeline scan, not exposed via a read API.
- The **tracks table** gained a row number column, a separate musical Key
  column alongside Camelot, and a Quality-tier badge column.
- The **track inspector** gained BPM/Key/Camelot stat tiles, a disabled
  play-button placeholder, a deterministic decorative waveform placeholder,
  and a "Compatible tracks coming soon" deferred note — harmonic scoring
  exists for Set Builder but is not yet exposed as a per-track lookup API.
- The sidebar (`frontend/src/components/Sidebar.tsx`) gained a teal
  glow active state, real nav badges (Issues / Enrichment Queue / Metadata
  Repair / BPM Review pending counts, from existing endpoints), and a
  Library Health mini-card sourced from `GET /api/library/quality`.
- Layout collapses to a single column below 860px, with the inspector
  stacking below the table instead of disappearing.

No backend endpoint, pipeline behavior, or route was changed for this
redesign — see `CHANGELOG.txt` for the full functionality classification
(implemented / read-only placeholder / deferred).

The frontend is not allowed to write audio tags or modify files.

## Performance Features

Phase 8 added large-library hardening:

- DB indexes for `artist`, `title`, `genre`, `bpm`, and `parse_confidence`.
- `/api/tracks` SQL paging with `LIMIT` and `OFFSET`.
- Limit cap protection for track listing.
- SQL-backed filtering for common issue filters.
- Lightweight request timing logs and `X-Process-Time-Ms`.
- Safe mtime/size-based cache for enrichment queue JSONL reads.
- Debounced frontend search.
- Persisted UI state for filters, selected section, pagination, sort, queue filters, and selected track.
- Virtualized track table rendering.
- Loading skeletons/spinners and API error banners.

The goal is not to hide large-library complexity. The goal is to keep browsing and review responsive while preserving explicit operational control.

## Safety Model

CrateIQ's safety model is built around explicit intent.

Default behavior:

```bash
python3 pipeline.py some-command --root <root>
```

Write behavior:

```bash
python3 pipeline.py some-command --root <root> --apply --yes
```

Safety guarantees by design:

- Dry-run by default for write-capable commands.
- Write operations require `--apply --yes`.
- Backend write endpoints are narrow and explicit.
- No metadata/tag/file writes from read-only pages.
- No online lookup in local extraction.
- No AI in deterministic parsing.
- No automatic BPM/key/cue overwrite.
- No broad reconciliation from the dashboard.

BPM, key, beatgrid, and cues are performance data. They should be owned by Mixed In Key and Rekordbox, not overwritten by CrateIQ automation.

## Repository Structure

Representative structure:

```text
.
├── backend/
│   └── app/
│       ├── api/routes/
│       ├── core/
│       ├── models/
│       ├── schemas/
│       └── services/
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── pages/
│   │   └── types/
│   └── package.json
├── modules/
│   ├── filename_parse.py
│   ├── enrichment_apply.py
│   ├── metadata_enrich_online.py
│   └── organizer.py
├── tests/
├── db.py
├── pipeline.py
└── README.md
```

Notes:

- `pipeline.py` is the CLI entrypoint.
- [COMMANDS.md](COMMANDS.md) is the canonical CrateIQ CLI command reference.
- [Legacy DJ Toolkit commands](docs/operations/LEGACY_DJ_TOOLKIT_COMMANDS.md)
  are preserved for historical context only.
- `db.py` owns the core SQLite schema helpers.
- `backend/app/` owns the FastAPI backend.
- `frontend/src/pages/CrateMind.tsx` owns the current dashboard workspace.
- `modules/organizer.py` is legacy/deprecated and should not be used as the foundation for new canonical organization behavior.

## Installation

Requirements vary by workflow, but the common local setup is:

- Python 3.10+
- Node.js and npm for the frontend
- SQLite
- Audio tooling as needed for analysis/extraction workflows
- Optional local AI tooling only for AI-specific phases

Python setup:

```bash
python3 --version  # must be Python 3.10 or newer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r backend/requirements.txt
```

Frontend setup:

```bash
npm --prefix frontend install
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Configure the active library root:

```bash
export CRATEIQ_LIBRARY_ROOT=/path/to/library
```

## Running Backend/Frontend

Run the backend from the repository root:

```bash
source .venv/bin/activate
python -m uvicorn backend.app.main:app --reload --port 8000 --app-dir .
```

Run the frontend:

```bash
npm --prefix frontend run dev
```

Typical local URLs:

```text
Backend:  http://127.0.0.1:8000
Frontend: http://127.0.0.1:5173
```

Health check:

```bash
curl http://127.0.0.1:8000/api/health
```

## Local Service Helper (crate_start / crate_stop)

`scripts/crateiq-local-services.sh` starts and stops the backend/frontend
pair on dedicated local ports so CrateIQ can run alongside LedgerIQ
(5173/8000, untouched) and OpsIQ (5174/8010):

| Service  | Port | URL |
|---|---|---|
| Backend  | 8020 | http://127.0.0.1:8020 |
| Frontend | 5175 | http://127.0.0.1:5175 |
| Health   | —    | http://127.0.0.1:8020/api/health |
| Readiness | —   | http://127.0.0.1:8020/api/runtime/readiness |

Install the shell functions (add the `source` line to `~/.bashrc` or
`~/.zshrc` yourself if you want them permanently):

```bash
source ~/code/gewcc/crateIQ/scripts/crateiq-local-services.sh --aliases
```

Commands:

```text
crate_start       start backend (:8020) + frontend (:5175)
crate_stop        stop CrateIQ services only
crate_restart     stop, start, then show status
crate_status      process/port/URL/health/log overview
crate_logs        tail backend + frontend logs
crate_back_logs   tail backend log only
crate_front_logs  tail frontend log only
```

The same subcommands work without aliases:
`scripts/crateiq-local-services.sh start|stop|restart|status|logs|back-logs|front-logs`.

PID files and logs live under `.run/` (gitignored). No sudo is required.
The frontend dev proxy is pointed at the 8020 backend via the
`CRATEIQ_API_PROXY_TARGET` environment variable, which the Vite config reads
(default remains `http://localhost:8000` when unset).

Troubleshooting:

- "port ... is already in use" on start: run `crate_status`; if the port is
  held by a process the helper does not recognize as CrateIQ, it will never
  kill it — stop that process yourself or change
  `CRATEIQ_BACKEND_PORT`/`CRATEIQ_FRONTEND_PORT` before sourcing.
- Stop only affects processes verified as this repo's uvicorn/vite on ports
  8020/5175; LedgerIQ (5173/8000) is never touched.
- To remove the aliases from the current shell:
  `unset -f crate_start crate_stop crate_restart crate_status crate_logs crate_back_logs crate_front_logs`
  (and delete the `source` line from your shell rc if you added it).

## Demo Data (for local UI work / screenshots)

`scripts/seed_demo_library.py` seeds a small, clearly-fake SQLite library so
the frontend (Library view, Quality, etc.) can be exercised and screenshotted
with populated data. It never touches real music, never scans real audio
files, and always writes to `<repo>/.run/demo-library/` (gitignored) — the
path is hardcoded, not a CLI flag, specifically so it can't be pointed at a
real `DJ_MUSIC_ROOT` by accident.

```bash
.venv/bin/python scripts/seed_demo_library.py            # seed/update (idempotent)
.venv/bin/python scripts/seed_demo_library.py --reset     # wipe + reseed
.venv/bin/python scripts/seed_demo_library.py --count 60  # more demo tracks (1-500)
```

Then point a local run at it:

```bash
export DJ_MUSIC_ROOT="$(pwd)/.run/demo-library"
bash scripts/crateiq-local-services.sh restart
```

Unset `DJ_MUSIC_ROOT` (or start a fresh shell) to go back to your real
library configuration.

## Runtime Readiness

CrateIQ ships a read-only local-runtime preflight that reports whether the
environment is ready before you run library, metadata, export, or sync
workflows. It never mutates library data, never runs pipeline commands or
jobs, and never returns secret values.

```bash
curl http://127.0.0.1:8000/api/runtime/readiness
```

Response shape:

```json
{
  "status": "ready",
  "checks": [
    {"name": "library_root", "status": "pass", "required": true,
     "message": "...", "metadata": {"root": "~/music/library"}}
  ]
}
```

Status meanings:

| Status | Meaning |
|---|---|
| `ready` | All checks pass. |
| `degraded` | Required checks pass, but optional/workflow-specific checks warn (e.g. a missing external binary, or a pipeline DB that has not been created yet). Affected workflows will be limited. |
| `not_ready` | A required check fails: the library root is missing, unreadable, or an unsafe broad root, or `pipeline.py` cannot be found. Fix configuration before operating on a library. |

Checks performed (all read-only): library root resolution/existence/
readability, rejection of unsafe broad roots (`/`, `/home`, `/Users`,
`/System`, the home directory, and the repository itself — override
deliberately with `CRATEIQ_ALLOW_UNSAFE_ROOT=1`), pipeline DB presence and
containment under the root, `pipeline.py` entrypoint, backend data
directory, and availability of `ffprobe`, `ffmpeg`, `keyfinder-cli`,
`aubio`, `beet`, `rmlint`, and `rsync`. Missing binaries warn; they never
fail startup.

Environment configuration is documented in [.env.example](.env.example) —
copy the values you need into your shell or a local env file. Never commit
real secrets. Reminder: CrateIQ has no authentication; run it only on a
trusted local machine.

### Frontend readiness banner

The frontend shell (`frontend/src/components/Layout.tsx`) fetches
`GET /api/runtime/readiness` once on load
(`frontend/src/hooks/useReadiness.ts` — no polling) and shows a small
banner (`frontend/src/components/ReadinessBanner.tsx`) above the page
content:

- `ready` — no banner.
- `degraded` — a warning-styled banner with up to 3 failing/warning checks.
- `not_ready` — an error-styled banner with up to 3 failing checks.
- readiness check itself failed to load — a small neutral notice.

The banner is diagnostic only: it never blocks navigation, never dumps raw
JSON or check `metadata` (which can include local paths), and is
dismissible for the current browser session. The `degraded`/`not_ready`
banner always carries a fixed "local diagnostic only — no authentication
added" note, since readiness is not a security or auth signal.

## Example Workflows

Audit current path state:

```bash
python3 pipeline.py path-audit --root /path/to/library
```

Build or refresh canonical tracks:

```bash
python3 pipeline.py build-tracks --root /path/to/library
```

Extract local metadata in dry-run mode:

```bash
python3 pipeline.py extract-track-metadata --root /path/to/library
```

Apply local metadata extraction:

```bash
python3 pipeline.py extract-track-metadata --root /path/to/library --apply --yes
```

Inspect enrichment review queue:

```bash
python3 pipeline.py enrichment-review --root /path/to/library
```

Dry-run approved enrichment apply:

```bash
python3 pipeline.py enrichment-apply-approved --root /path/to/library
```

Apply approved enrichment metadata to `tracks` only:

```bash
python3 pipeline.py enrichment-apply-approved --root /path/to/library --apply --yes
```

Open operational dashboard:

```bash
export CRATEIQ_LIBRARY_ROOT=/path/to/library
source .venv/bin/activate
python -m uvicorn backend.app.main:app --reload --port 8000 --app-dir .
npm --prefix frontend run dev
```

## Testing

Install Python test dependencies in an activated virtual environment:

```bash
python3 --version  # must be Python 3.10 or newer
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Run the backend/pipeline test suite:

```bash
python -m pytest -q
```

`requirements-dev.txt` includes pipeline dependencies, backend dependencies,
pytest, FastAPI TestClient support, and a binary-wheel compatibility constraint
for librosa's numba/llvmlite chain. The test suite automatically assigns both
`DJ_MUSIC_ROOT` and `CRATEIQ_LIBRARY_ROOT` to a temporary directory; the
deprecated `CRATEMINDAI_LIBRARY_ROOT` alias remains supported. No local
music-library path is required and tests do not write under `/music`.

Run frontend verification:

```bash
npm --prefix frontend install
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Common combined check:

```bash
python -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

There is currently no frontend unit-test script; TypeScript and the production
Vite build are the frontend checks.

Route-contract smoke tests guard against drift between the supported frontend
routes and the backend APIs they depend on:

```bash
python -m pytest -q tests/test_supported_route_contracts.py
```

The contract maps each supported route (parsed from `frontend/src/App.tsx`)
to its primary read-only backend endpoints and asserts status codes and
minimal response shapes against temporary fixture roots. The smoke surface is
GET-only: it never runs pipeline jobs, spawns subprocesses, or triggers
sync/export/reconciliation/apply workflows. Mutating endpoints are listed as
deferred in the test file's `DEFERRED_ENDPOINTS` table.

In a restricted execution sandbox that blocks cross-thread asyncio wakeups,
Starlette `TestClient` can stall before entering its context. This is an
environment limitation, not a CrateIQ startup or health-route requirement. Run
the suite in the activated virtual environment on the normal host or CI runner;
the current suite passes 860 tests there. The installed stack may also emit a
Starlette deprecation warning recommending the future `httpx2` client; this is
tracked separately and does not block the suite.

## Known Limitations

- CrateIQ is not a Rekordbox replacement.
- CrateIQ does not own BPM, key, beatgrid, or cue authoring.
- Phase 7 apply implementation has not started; a planning specification exists.
- Path reconciliation is not a broad automatic repair system.
- Online enrichment is candidate scoring plus review workflow, not blind metadata overwrite.
- Some legacy modules remain in the repository for compatibility and historical context.
- `modules/organizer.py` is legacy/deprecated.
- Legacy frontend pages are retained but hidden as described in the supported route table.
- There is no authentication; run the app only in a trusted local environment.
- Runtime paths and external tool availability still depend heavily on environment configuration.
- The generic Jobs page is constrained by backend allowlists but does not explain every command's individual safety semantics.
- Production frontend dependencies audit clean; development tooling still has advisories whose npm-proposed fix requires a Vite major upgrade.
- The historical restricted-sandbox baseline collected 857 tests but stalled at
  the first FastAPI health test; the current normal-host suite collects 860 and
  passes twice.
- The frontend dashboard is operational but is not intended to replace CLI control for every pipeline operation.
- External provider data may be incomplete or wrong, which is why review state exists.

The recommended next task is to review the roadmap, then implement the CrateIQ
Phase 1 runtime/readiness contract only after approval.

## Long-Term Vision

CrateIQ's long-term direction is a full DJ library operations console:

- Canonical current-state tracking.
- Auditable history and change plans.
- Safe reconciliation workflows.
- Human-approved metadata enrichment.
- Library health dashboards.
- Provider-independent metadata scoring.
- Rekordbox/Mixed In Key respecting workflows.
- Repeatable operations for large, evolving DJ collections.

The destination is not autonomous metadata control. The destination is reliable operational confidence: every file, path, metadata field, and enrichment decision should be explainable, reviewable, and reversible where possible.
