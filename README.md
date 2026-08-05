# crateIQ

**Local-first DJ music library intelligence for safer crates, metadata review, harmonic planning, and export prep.**

crateIQ is a local app for DJs who want to inspect a music library, make
deliberate crates, review existing metadata, and prepare portable exports
without handing control of their collection to a cloud service or automatic
file-writing workflow.

![crateIQ Library workspace](docs/screenshots/crateiq-library.webp)

*The Library workspace: local index health, filtering, track review, harmonic context, and browser-native preview.*

## What crateIQ does

- Initializes and imports a local music folder into CrateIQ's own SQLite index.
- Reviews library quality, issues, folders, and existing metadata.
- Builds ordered Manual Crates and saves Smart Crate suggestions as editable
  Manual Crates.
- Uses browser-native playback to preview files that are safely available under
  the selected library root.
- Creates portable CSV, JSON, M3U, and UTF-8 M3U8 crate exports.
- Stages Serato handoffs and Rekordbox-importable XML; it does not write either
  application's live database.
- Reviews and imports Mixed In Key-compatible BPM/key metadata into CrateIQ's
  local index only.
- Offers optional, explicitly requested BPM and key/Camelot analysis for tracks
  still missing trusted values.

## Local-first safety model

- **Your source music files and tags are not modified** by library setup,
  import, crate building, preview, or the portable/staged export workflows.
- **Import does not run BPM or key analysis.** Analysis is optional,
  preview-first, and only starts after explicit confirmation.
- **Mixed In Key-compatible values are trusted and preserved.** crateIQ fills
  only missing local-index values and never overwrites trusted BPM, key, or cue
  information.
- **Optional tools are scoped to their own jobs.** Their absence does not block
  import, browsing, crates, preview, or portable exports.
- **Serato and Rekordbox live databases are never written.** Exports are staged
  artifacts for review or manual import.
- **No cloud account is required.** crateIQ is intended for trusted local use;
  it currently has no authentication for remote or multi-user deployment.

## Feature status

| Feature | Status | Notes |
| --- | --- | --- |
| Library setup and import | Implemented | Explicit initialize → scan preview → import flow; writes CrateIQ's local index only. |
| Manual Crates | Implemented | Create, edit, reorder, and save local DJ working lists. |
| Smart Crates | Implemented | Deterministic suggestions from existing local metadata; save as Manual Crates. |
| Audio preview | Implemented | Browser-native playback for safely resolvable local files. |
| Portable exports | Implemented | CSV, JSON, M3U, and M3U8, with safe staged output paths. |
| Staged Serato export | Implemented | M3U8 plus manifest handoff; exact binary `.crate` writing is deferred. |
| Staged Rekordbox XML export | Implemented | Importable XML file only; no live Rekordbox database writer. |
| MIK coverage/import | Implemented foundation | Explicit read-only metadata preview and DB-only import; cue-tag parsing is deferred. |
| BPM analysis with `aubio` | Implemented safe runner | Preview and confirmation required; only missing BPM is eligible. |
| Key/Camelot with `keyfinder-cli` | Implemented safe runner | Preview and confirmation required; only missing key/Camelot is eligible. |
| Beets enrichment | Preview/review foundation | Shows incomplete non-critical metadata; no `beet` invocation or apply flow. |
| Duplicate detection with `rmlint` | Preview + DB-only review | Bounded JSON scan plus local keep/ignore/review-later notes; no delete, move, rename, or quarantine action. |
| Audio quality probe with `ffprobe` | Preview-only foundation | Bounded JSON metadata checks; no transcode, file, tag, or DB writes. |
| Live Serato/Rekordbox DB writes | Not supported by design | crateIQ stages artifacts only. |

## Screenshots

### Library

![crateIQ Library](docs/screenshots/crateiq-library.webp)

### Analysis Jobs

![crateIQ Analysis Jobs](docs/screenshots/crateiq-jobs.webp)

### Manual Crates

![crateIQ Manual Crates](docs/screenshots/crateiq-crates.webp)

### Exports

![crateIQ Exports](docs/screenshots/crateiq-exports.webp)

The live Settings screenshot is deliberately not published because it displays
the active local library location. The existing
[`settings.webp`](docs/mockups/settings.webp) remains a visual mockup/reference,
not a product screenshot.

## Quick start

Requirements:

- Python 3.10 or newer
- Node.js and npm
- SQLite (normally provided by Python)

```bash
cd ~/code/gewcc/crateIQ

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm --prefix frontend install

# Starts only crateIQ on backend :8020 and frontend :5175.
scripts/crateiq-local-services.sh start
```

The `start` command asks you to choose a library profile and access mode. For
the safest first run, choose **Demo library** and **Local only**. Then open
<http://127.0.0.1:5175>.

For a non-interactive demo launch:

```bash
.venv/bin/python scripts/seed_demo_library.py --reset
scripts/crateiq-local-services.sh start-demo-local
```

For a configured library, first open **Settings** to choose a folder,
initialize CrateIQ's local `logs/processed.db`, scan it explicitly, and import
the previewed audio paths. Restart with the configured profile after saving the
root:

```bash
scripts/crateiq-local-services.sh start-library-local
```

The configured root is stored locally in ignored `.run/local/crateiq.env`.
Initialization creates CrateIQ's `logs/` and `exports/` folders only; it does
not scan or alter music files. Scanning is always an explicit later action.

### Service commands

```bash
scripts/crateiq-local-services.sh status --short
scripts/crateiq-local-services.sh stop
scripts/crateiq-local-services.sh start
scripts/crateiq-local-services.sh logs
```

The helper manages only crateIQ's ports (`8020` and `5175`). It does not stop
or alter LedgerIQ or opsIQ.

## Optional analysis tools

Core crateIQ workflows do not require external analysis tools. Settings and
Analysis Jobs show each capability independently:

| Optional tool/input | Current workflow |
| --- | --- |
| Mixed In Key-compatible metadata | Explicit coverage preview and local-index-only import. |
| `aubio` | Preview-first, confirmed missing-BPM analysis. |
| `keyfinder-cli` | Preview-first, confirmed missing key/Camelot analysis. |
| `beet` | Preview-limited enrichment planning only. |
| `rmlint` | Preview-only duplicate candidates only. |
| `ffprobe` | Bounded preview-only audio metadata checks; no transcode or writes. |
| `ffmpeg` | Reserved for separate, explicitly scoped decode/conversion workflows. |

See [local tooling guidance](docs/operations/LOCAL_TOOLING.md) for setup notes
and exact safety boundaries.

## Development

```bash
# Focused backend coverage for the current safe analysis workflows
.venv/bin/python -m pytest -q tests/test_backend_api.py -k "analysis or jobs"
.venv/bin/python -m pytest -q tests/test_supported_route_contracts.py

# Frontend checks
npm --prefix frontend run typecheck
npm --prefix frontend run build

# Repository hygiene
git diff --check
git status --short
```

Useful local endpoints:

- Frontend: <http://127.0.0.1:5175>
- Backend health: <http://127.0.0.1:8020/api/health>
- Runtime readiness: <http://127.0.0.1:8020/api/runtime/readiness>

## Project map

- `backend/app/` — FastAPI API, safe local services, schemas, and route logic.
- `frontend/` — React/Vite interface.
- `scripts/` — local service helper and demo-library seeding.
- `docs/operations/LOCAL_TOOLING.md` — optional-tool setup and workflow scope.
- `docs/audits/CRATEIQ_FUNCTIONALITY_WORKFLOW_AUDIT.md` — current product,
  workflow, and dependency audit.

## Contributing

Keep changes local-first and reviewable. Do not introduce automatic file/tag
writes, overwrite Mixed In Key values, or write live Serato/Rekordbox
databases. Run focused checks for the surface you change, avoid committing local
databases and `.run/` data, and document any behavior change.
