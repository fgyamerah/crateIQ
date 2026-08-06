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
- Uses a persistent browser-native bottom player to preview safely available
  files across Library, Music Review, and route changes.
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
| Persistent audio preview | Implemented | App-shell bottom player with Library/Music Review queues, route persistence, seek, volume, and safe unavailable states. |
| Portable exports | Implemented | CSV, JSON, M3U, and M3U8, with safe staged output paths. |
| Staged Serato export | Implemented | M3U8 plus manifest handoff; exact binary `.crate` writing is deferred. |
| Staged Rekordbox XML export | Implemented | Importable XML file only; no live Rekordbox database writer. |
| MIK coverage/import | Implemented foundation | Explicit read-only metadata preview and DB-only import; cue-tag parsing is deferred. |
| BPM analysis with `aubio` | Implemented safe runner | Preview and confirmation required; only missing BPM is eligible. |
| Key/Camelot with `keyfinder-cli` | Implemented safe runner | Preview and confirmation required; only missing key/Camelot is eligible. |
| Beets enrichment | Selected-field DB-only review | Local missing-field candidates; explicit saved/confirmed artist/title/genre apply, no `beet` invocation or tag/file writes. |
| Metadata Sources | Settings foundation | Local tags, MIK, Beets, and future APIs are modeled; external APIs are disabled by default and do not yet perform lookup. |
| Multi-source enrichment review | Implemented foundation | Compares conservative local suggestions and source status; selected empty local-index fields only. No provider API calls. |
| Genre Taxonomy | Implemented foundation | Review-first Ghana/Africa and DJ-friendly genre normalization in the local index; raw values stay preserved. |
| Duplicate detection with `rmlint` | Preview + DB-only review | Bounded JSON scan plus local keep/ignore/review-later notes; no delete, move, rename, or quarantine action. |
| Audio quality probe with `ffprobe` | Probe + DB-only review | Bounded JSON checks plus local review notes; no transcode, remediation, file, or tag writes. |
| Waveform foundation | W1 cache/config/state foundation + W2 extraction engine | W1: safe cache/config/state/readiness contracts. W2: an internal, unwired extraction engine (bounded ffprobe + FFmpeg decode + min/max peak accumulator) verified only against mocked processes and synthetic PCM. No API, worker, cache artifact, source hashing, or frontend rendering yet. |
| Live Serato/Rekordbox DB writes | Not supported by design | crateIQ stages artifacts only. |

### Browser playback notes

The persistent player streams only through the existing DB-backed
`/api/tracks/{track_id}/preview-audio` endpoint. Playback availability depends
on the selected file existing under the configured library root and on the
browser supporting its audio format. Missing, out-of-root, or unsupported
files show an unavailable state; crateIQ does not transcode them.

Library queues the currently visible filtered page. Music Review queues its
current review list, keeps next/previous selection synchronized with the bottom
player, and does not save a review merely because a track was selected or
played. The low/mid/high display is a deterministic visual placeholder—not an
extracted waveform or audio analysis. Player use never writes tags, audio
files, BPM, key, Camelot, cues, MIK data, crate order, or DJ databases.

The 2026-08-05 playback pass verified MP3 and FLAC in Chrome using the explicit
`crateiq-test-library`; this is evidence for those files in that browser, not a
claim that every codec/container works everywhere. Queue boundaries do not
wrap. A natural track end advances and continues when another queue item exists,
while the final item stops safely. The player labels the source as Library or
Music Review and does not display the indexed absolute local filepath.

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

### Waveform W1 configuration

Waveform Phase W1 adds only an optional backend foundation. The default cache
root is `backend/data/cache/waveforms/`, inside the already ignored backend
runtime-data tree. An override must be absolute and is rejected if it equals,
contains, or sits below the selected music-library root, including after
symlink resolution.

| Environment variable | Default |
| --- | --- |
| `CRATEIQ_WAVEFORMS_ENABLED` | `1` |
| `CRATEIQ_WAVEFORM_CACHE_DIR` | backend-owned cache root |
| `CRATEIQ_WAVEFORM_MAX_CONCURRENCY` | `1` (valid 1–2) |
| `CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE` | `32` |
| `CRATEIQ_WAVEFORM_MAX_CACHE_BYTES` | `2147483648` (2 GiB) |

Runtime readiness reports `disabled`, `misconfigured`, `cache_unavailable`,
`extractor_unavailable`, or `detected`. In W1, `detected` means FFmpeg and
ffprobe were found passively; neither executable was run and versions remain
unverified. `ready` is reserved for a future verified extractor contract.
Waveform operational state is stored only in backend `jobs.db`; the trusted
library `processed.db` is not extended or written by this foundation.

Phase W2 adds an internal extraction engine
(`backend/app/services/waveform_extractor.py` and supporting
`waveform_probe.py`/`waveform_peaks.py`/`waveform_process.py` modules) that a
future phase can call: a bounded, read-only ffprobe policy check, a fixed
argument-vector FFmpeg decode command with no shell and no output file, and a
bounded streaming min/max peak accumulator with extrema-preserving
downsampling. It is not reachable from any API route, job worker, or
application startup path, and every W2 test runs against fake process objects
and synthetic PCM — no real audio tool ever decodes a file in this repository
as part of W2.

## Development

```bash
# Focused backend coverage for the current safe analysis workflows
.venv/bin/python -m pytest -q tests/test_backend_api.py -k "analysis or jobs"
.venv/bin/python -m pytest -q tests/test_waveform_foundation.py tests/test_preflight.py
.venv/bin/python -m pytest -q tests/test_waveform_peaks.py tests/test_waveform_process.py tests/test_waveform_probe.py tests/test_waveform_extractor.py
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
