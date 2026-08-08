# crateIQ

**Local-first DJ library preparation: managed Inbox → Library workflow, multi-provider metadata intelligence, analysis, crates, and safe publishing.**

![crateIQ Library workspace](docs/screenshots/crateiq-library.webp)

*The Library workspace: local index health, filtering, track review, harmonic context, and browser-native preview.*

## What crateIQ is

crateIQ is a local-first DJ music preparation and library-management app.
You point it at raw downloads, it copies them into a managed workspace on
your machine, cleans and identifies the metadata (deterministically first,
then via multi-provider consensus), surfaces anything uncertain for you to
resolve, and lets you explicitly promote finished tracks into a clean,
Rekordbox-ready library — all without a cloud account, and without ever
touching your original source files.

## Core workflow

```
External Sources
    │  copy (originals untouched)
    ▼
  Inbox  ───────────────►  Process All
    │                          │
    │                          ├─ deterministic cleanup
    │                          ├─ multi-provider metadata consensus
    │                          ├─ verified tag write-back (Inbox copy only)
    │                          └─ BPM / key analysis
    ▼
Needs Review  (uncertainty & conflicts)
    │  resolve
    ▼
  Ready
    │  explicit "Move Ready to Library"
    ▼
 Library/<Genre>/<Artist>/<Artist> - <Title>.<ext>
    │
    ▼
Crates → Set Builder → Publish
```

### Managed workspace

crateIQ manages a music workspace with three physically separated zones
under one **Managed Root** folder you choose (Settings → Workspace):

```
<Managed Root>/
  Inbox/        tracks being prepared, not yet finished
  Library/      promoted, finished music -- Genre/Artist/Artist - Title.ext
  Quarantine/   reserved; never an automatic destination
```

- **Import copies, never moves.** Selecting external files or folders in
  **Inbox** copies them into `Inbox/`; your original source files are never
  modified, renamed, or deleted.
- **Inbox is crateIQ's working copy.** All metadata writes happen only
  against managed Inbox copies, never against the external originals.
- **Library contains only explicitly promoted finished tracks** — nothing
  lands there automatically.
- **Quarantine is never an automatic destination.** No workflow moves files
  there on its own.
- **An existing library you already point crateIQ at directly** (files
  scanned in place, no `Inbox`/`Library`/`Quarantine` folders) is never
  silently restructured into this layout — configuring a managed workspace
  requires a new, dedicated root.
- **Workspace Root vs. Import Source are different things.** The Workspace
  Root is the dedicated folder crateIQ owns (Settings → Workspace can create
  it safely — one new folder only, never a recursive parent tree, and only
  after you confirm). An Import Source is any external folder of your
  existing music; crateIQ refuses an import whose source *is* the workspace,
  *contains* the workspace, or resolves through a symlink into it, so a
  sibling folder like `~/Music/downloads` imports cleanly into
  `~/Music/crateIQ/Inbox` without ever re-discovering its own copies.
- Operational databases, backups, and caches (crateIQ's SQLite index, job
  state, waveform cache) live under crateIQ's own runtime data directories,
  outside the managed music tree.

## Batch preparation

**Process All** is the main way tracks move through Inbox. Behind one
explicit confirmation, it runs:

1. **Deterministic metadata cleanup** — strips URL watermarks, promo junk,
   and malformed fields; no network calls.
2. **Multi-provider metadata routing and consensus** — gathers evidence from
   the providers you have configured (see below) and reduces it to a
   field-by-field verdict.
3. **Verified tag write-back** — HIGH-confidence fields are written to the
   managed Inbox copy through the existing preview/stale-check/backup/
   re-read verification path; everything else is left untouched and queued
   for review.
4. **Analysis preparation** — BPM and key/Camelot for tracks still missing
   trusted values.

**Process All does not query every provider for every track.** Evidence
gathering follows a bounded, staged order (Beets + MusicBrainz first, since
they need no credentials; AcoustID fingerprinting next if configured; then
Discogs/Beatport, Spotify/Deezer, Last.fm, and finally YouTube as a
last-resort corroboration source), stopping early once identity confidence
is already HIGH. Only providers whose credentials are configured and who
report themselves ready are queried; an unconfigured provider is silently
skipped, never an error.

Existing non-empty metadata is never silently overwritten. The one
exception: a field whose *current* value is already a known junk/placeholder
(e.g. an "Unknown Artist" stand-in) is cleared before a HIGH-confidence
replacement is applied — an explicit, logged step, not a weakening of the
general no-overwrite rule.

**Process All never promotes tracks into Library.** Promotion is always a
separate, explicit action (see "Ready and promotion" below).

You can also run **Clean Selected** or **Enrich Selected** on a chosen
subset instead of the whole Inbox.

## Metadata intelligence

crateIQ draws on multiple evidence sources per track:

| Source | Kind | Current support |
| --- | --- | --- |
| Embedded tags | local | Implemented and ready |
| Filename hints | local | Implemented and ready |
| Mixed In Key-compatible tags | local | Implemented and ready — read-only preview, DB-only import |
| Beets | installed tool | Implemented and ready (isolated Python API, no `beet` CLI invocation) |
| MusicBrainz | provider | Implemented and ready — credential-free |
| AcoustID / Chromaprint | provider | Implemented — needs a free self-serve client key from acoustid.org |
| Deezer | provider | Implemented and ready — credential-free search |
| Discogs | provider | Implemented — needs a free self-serve personal access token |
| Spotify | provider | Implemented — needs your own Spotify Developer app credentials |
| Last.fm | provider | Implemented — needs a free self-serve API key |
| YouTube | provider | Implemented — needs a Google Cloud API key |
| Beatport | provider | Implemented in code — unavailable without Beatport Partner Portal approval (no public self-service signup) |

Settings → Metadata Sources reports each provider's real status
(Ready / Needs Setup / Unavailable) from a live credential/capability check —
crateIQ never claims a provider is connected without a successful check.

### Consensus, not a score

Each metadata field gets its own explainable verdict, not a single fake
confidence percentage:

- **HIGH** — evidence agrees strongly enough to auto-apply during Process All.
- **MEDIUM / LOW** — some evidence, not enough to auto-apply; goes to Needs Review.
- **CONFLICT** — sources actively disagree; goes to Needs Review with every
  candidate shown.

Verdicts are field-by-field, not track-by-track: a HIGH-confidence
artist/title identity match does **not** mean genre is also HIGH — genre
keeps its own authority-weighted verdict (Beatport and Discogs carry more
genre authority than a generic text match), so it's normal for identity to
auto-apply while genre still lands in Needs Review with its own evidence.

## Needs Review

Needs Review is a single aggregated queue for everything that couldn't be
resolved automatically, pulled from crateIQ's existing specialist review
systems:

- **Metadata** — missing or ambiguous fields
- **Identity & Enrichment** — provider consensus MEDIUM/LOW/CONFLICT verdicts
- **Genre** — taxonomy and authority conflicts
- **Analysis** — BPM/key issues
- **Quality** — audio quality findings

The point is that day to day you work through Inbox; Needs Review exists so
you only have to look at exceptions, with a link into the relevant
specialist page to resolve each one — you don't need to understand every
internal review system to use crateIQ.

## Ready and promotion

A track becomes promotable once:

**Required (blockers):**
- Artist is present
- Title is present
- Genre is present
- Its approved metadata has been verified written back to the file
- No unresolved serious errors (e.g. the source file must still exist)

**Warnings only (do not block promotion):**
- Missing BPM
- Missing key

Promotion (**Move Ready to Library**) is always an explicit, separate
action — nothing is promoted automatically by Process All or by resolving a
review item. A promoted file moves from `Inbox/` into
`Library/<Genre>/<Artist>/<Artist> - <Title>.<ext>` and no longer appears in
Inbox.

> Waveform data is not part of the promotion gate — it plays no role in
> whether a track is ready to promote.

## DJ workflow

Once tracks are in Library:

```
Library → Crates → Set Builder → Publish
```

- **Manual Crates** — create, edit, reorder, and save ordered DJ working
  lists from Library tracks.
- **Smart Crates** — deterministic suggestions from existing local metadata,
  saved as editable Manual Crates.
- **Set Builder** — build an energy-curve DJ set from your library.
- **Publish** — guided, preview-then-confirm export and sync for one crate
  at a time:
  - Portable CSV, JSON, M3U, and UTF-8 M3U8 exports.
  - Staged Rekordbox-importable XML — a file for manual import, not a live
    database write.
  - Staged Serato handoff — M3U8 plus manifest, for manual/handoff use.
  - Optional SSD sync (rsync-based), previewed and explicitly confirmed
    before any write.

crateIQ does not write to live Serato or Rekordbox databases; all DJ-software
integration is via staged, reviewable artifacts.

## Safety model

- **External import originals are never modified.** Import always copies;
  nothing about setup, import, crate building, preview, or export/sync
  touches the source files you imported from.
- **crateIQ works on managed Inbox copies.** Controlled metadata write-back
  is a real, supported capability of Process All and Enrich Selected — it is
  *not* true that "crateIQ never modifies music tags." What's true is that
  writes are scoped to managed Inbox copies, confidence-gated, and behind
  explicit confirmation.
- Metadata writes go through the existing protections: a preview, a
  stale-check against the file on disk, a backup, an explicit confirmation,
  a post-write re-read verification, and a restore path if verification
  fails.
- **Mixed In Key-compatible BPM, key, and cue values are trusted and
  preserved.** crateIQ fills only missing values and never overwrites
  trusted MIK data.
- **Promotion moves the managed Inbox copy**, not the external original —
  the original you imported from was never touched in the first place.
- **Permanent deletion is not part of normal preparation.** Quarantine is a
  reserved zone, never an automatic destination.
- **The `beet` CLI is never invoked.** Beets integration uses its isolated
  Python API only.
- **Live Serato/Rekordbox database writes are not supported by design** —
  crateIQ stages artifacts for manual import/handoff instead.
- **No cloud account is required.** crateIQ is intended for trusted local
  use; it currently has no authentication for remote or multi-user
  deployment.

## Navigation

```
LIBRARY
  Inbox
  Library
  Needs Review

DJ
  Crates
  Set Builder
  Publish

TOOLS
  Jobs
  Maintenance

SYSTEM
  Settings
```

**Maintenance** is a hub linking to the specialist pages behind day-to-day
use — Quality, Duplicates, Reconciliation, Folders, and Audit — so they stay
reachable without each competing for a permanent sidebar slot.

## Screenshots

### Library

![crateIQ Library](docs/screenshots/crateiq-library.webp)

### Analysis Jobs

![crateIQ Analysis Jobs](docs/screenshots/crateiq-jobs.webp)

### Manual Crates

![crateIQ Manual Crates](docs/screenshots/crateiq-crates.webp)

### Exports

![crateIQ Exports](docs/screenshots/crateiq-exports.webp)

> **These four screenshots predate the Managed Library navigation** (Inbox /
> Needs Review / the reorganized LIBRARY–DJ–TOOLS–SYSTEM sidebar shipped in
> Cycles 9–12). They still accurately show Library, Jobs, Crates, and
> Exports as those pages exist today, but the app now also has an Inbox
> workspace and a Needs Review queue with no current screenshot. New
> screenshots were not captured for this documentation pass — call this out
> rather than fabricate them; capturing current Inbox/Needs Review/Settings
> screenshots against a disposable demo library is good follow-up work.

The live Settings screenshot is deliberately not published because it
displays the active local library location. The existing
[`settings.webp`](docs/mockups/settings.webp) remains a visual
mockup/reference, not a product screenshot.

## Installation / Quick start

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

**First-run flow for your own music:**

1. Start crateIQ.
2. Open **Settings** — Workspace is the first tab. Enter a new folder path
   (e.g. `~/Music/crateIQ`); Settings validates it and, once you confirm,
   creates it and saves it as the pending workspace. Restart crateIQ
   (Settings shows the exact restart command and clearly separates the
   *current* workspace you're still running on from the *new* one pending
   restart), then reload Settings and click **Create Managed Workspace**.
3. Open **Inbox** and **Import Music** — this copies files in from an
   external Import Source; your originals are untouched.
4. Run **Process All**, resolve anything in **Needs Review**, then
   **Move Ready to Library**.

Direct/legacy library setup (scan an existing folder in place, no managed
Inbox/Library/Quarantine) has moved to **Settings → Advanced → Legacy
Direct Library** — it still works, but it's no longer the default path new
users see.

```bash
scripts/crateiq-local-services.sh start-library-local
```

The configured root is stored locally in ignored `.run/local/crateiq.env`.

Pointing crateIQ directly at an existing library (no managed
Inbox/Library/Quarantine folders) remains supported, but the managed
workspace above is the recommended path for new users.

### Service commands

```bash
scripts/crateiq-local-services.sh status --short
scripts/crateiq-local-services.sh stop
scripts/crateiq-local-services.sh start
scripts/crateiq-local-services.sh logs
```

The helper manages only crateIQ's ports (`8020` and `5175`). It does not stop
or alter LedgerIQ or opsIQ.

## Provider setup

| Provider | Purpose | Credential / setup | Current support |
| --- | --- | --- | --- |
| Beets | Missing-field candidates via its own MusicBrainz-backed matcher | None (isolated Python API) | Ready |
| MusicBrainz | Official releases and albums | None | Ready |
| Deezer | Alternate track/artist/album matching, ISRC corroboration | None (public search endpoint) | Ready |
| AcoustID / Chromaprint | Fingerprint-based identity evidence | Free client key at acoustid.org | Needs setup |
| Discogs | Electronic/vinyl/remix/release metadata | Free personal access token (discogs.com → Settings → Developers) | Needs setup |
| Spotify | Mainstream track/artist/album matching via ISRC | Your own Spotify Developer app (client ID + secret) | Needs setup |
| Last.fm | Community tag/genre corroboration | Free API key (last.fm/api/account/create) | Needs setup |
| YouTube | Low-authority corroboration/discovery only | Google Cloud API key (YouTube Data API v3) | Needs setup |
| Beatport | DJ/electronic genre and style authority | OAuth access token via Beatport's Partner Portal | Unavailable without partner approval — no public self-service signup |

No credentials are ever printed by crateIQ or committed to the repository;
saved credentials live in gitignored local state. See Settings → Metadata
Sources for each provider's live status and exact setup steps.

## Feature status

| Feature | Status | Notes |
| --- | --- | --- |
| Managed workspace (Inbox/Library/Quarantine) | Implemented | Copy-based import, Process All batch preparation, unified Needs Review, explicit Move Ready to Library promotion. Additive to the existing direct-library model; an existing library is never auto-restructured. |
| Process All batch preparation | Implemented | Deterministic cleanup + staged multi-provider consensus enrichment + verified tag write-back + BPM/key analysis behind one confirmation. Never promotes to Library. |
| Multi-provider consensus | Implemented, wired into Process All | Field-by-field HIGH/MEDIUM/LOW/CONFLICT evidence aggregation with staged provider routing and genre-authority weighting. HIGH fields auto-apply during Process All/Enrich Selected; MEDIUM/LOW/CONFLICT always go to Needs Review with full provenance. |
| Direct per-track provider lookup (Enrichment Review) | Implemented | Manual, explicit Beets/MusicBrainz online lookup for a single track outside of batch consensus. |
| Local-suggestion enrichment review | Implemented foundation | Compares conservative local suggestions (filename hints, embedded tags) against selected empty local-index fields only; no provider API calls — distinct from multi-provider consensus above. |
| Library setup and import | Implemented | Explicit initialize → scan preview → import flow; writes CrateIQ's local index only. |
| Manual Crates | Implemented | Create, edit, reorder, and save local DJ working lists. |
| Smart Crates | Implemented | Deterministic suggestions from existing local metadata; save as Manual Crates. |
| Persistent audio preview | Implemented | App-shell bottom player with Library/Music Review queues, route persistence, seek, volume, and safe unavailable states. |
| Portable exports | Implemented | CSV, JSON, M3U, and M3U8, with safe staged output paths. |
| Staged Serato export | Implemented | M3U8 plus manifest handoff; exact binary `.crate` writing is deferred. |
| Staged Rekordbox XML export | Implemented | Importable XML file only; no live Rekordbox database writer. |
| SSD sync | Implemented | Preview → explicit confirm rsync-based sync from Publish, one crate at a time. |
| MIK coverage/import | Implemented foundation | Explicit read-only metadata preview and DB-only import; cue-tag parsing is deferred. |
| BPM analysis with `aubio` | Implemented safe runner | Preview and confirmation required; only missing BPM is eligible. |
| Key/Camelot with `keyfinder-cli` | Implemented safe runner | Preview and confirmation required; only missing key/Camelot is eligible. |
| Genre Taxonomy | Implemented foundation | Review-first Ghana/Africa and DJ-friendly genre normalization in the local index; raw values stay preserved. |
| Duplicate detection with `rmlint` | Preview + DB-only review | Bounded JSON scan plus local keep/ignore/review-later notes; no delete, move, rename, or quarantine action. |
| Audio quality probe with `ffprobe` | Probe + DB-only review | Bounded JSON checks plus local review notes; no transcode, remediation, file, or tag writes. |
| Waveform generation and playback | Implemented | Explicit, demand-driven backend generation with a bounded worker, dedup, cancellation, atomic cache, and a real canvas waveform seek control in the player. Never generated automatically. See [Waveform Architecture](docs/architecture/WAVEFORM_ARCHITECTURE.md) for the full design, safety audit, and measured performance. |
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
played. Player use never writes tags, audio files, BPM, key, Camelot, cues,
MIK data, crate order, or DJ databases.

## Optional analysis tools

Core crateIQ workflows do not require external analysis tools. Settings and
Analysis Jobs show each capability independently:

| Optional tool/input | Current workflow |
| --- | --- |
| Mixed In Key-compatible metadata | Explicit coverage preview and local-index-only import. |
| `aubio` | Preview-first, confirmed missing-BPM analysis. |
| `keyfinder-cli` | Preview-first, confirmed missing key/Camelot analysis. |
| `rmlint` | Preview-only duplicate candidates only. |
| `ffprobe` | Bounded preview-only audio metadata checks; no transcode or writes. |
| `ffmpeg` | Powers waveform generation; also reserved for separate, explicitly scoped decode/conversion workflows. |

See [local tooling guidance](docs/operations/LOCAL_TOOLING.md) for setup notes
and exact safety boundaries.

## Development

```bash
# Focused backend coverage for the current safe analysis workflows
.venv/bin/python -m pytest -q tests/test_backend_api.py -k "analysis or jobs"
.venv/bin/python -m pytest -q tests/test_process_all_consensus.py
.venv/bin/python -m pytest -q tests/test_waveform_foundation.py tests/test_preflight.py
.venv/bin/python -m pytest -q tests/test_supported_route_contracts.py

# Full backend suite
.venv/bin/python -m pytest -q

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
- `docs/architecture/WAVEFORM_ARCHITECTURE.md` — waveform design, safety audit,
  and measured performance.
- `docs/audits/CRATEIQ_FUNCTIONALITY_WORKFLOW_AUDIT.md` — current product,
  workflow, and dependency audit.

## Contributing

Keep changes local-first and reviewable. Do not introduce automatic file/tag
writes, overwrite Mixed In Key values, or write live Serato/Rekordbox
databases. Run focused checks for the surface you change, avoid committing local
databases and `.run/` data, and document any behavior change.

<!-- COMMANDS:START -->
## Subcommands

> Auto-generated from `modules/doc_registry.py`.  Run `python3 pipeline.py generate-docs` to refresh.

### Library Maintenance

```bash
# Detect and quarantine duplicate audio files across the library.
python3 pipeline.py dedupe --dry-run

# Strip URL watermarks and promo junk from all metadata fields across the library.
python3 pipeline.py metadata-clean --dry-run

# Standardize MP3 ID3 tag format for Rekordbox (ID3v2.4 → ID3v2.3, remove ID3v1).
python3 pipeline.py tag-normalize --dry-run

# Detect BPM and key for tracks missing that data — writes to DB and audio tags.
python3 pipeline.py analyze-missing

# Audit library for codec/bitrate quality — classify into LOSSLESS/HIGH/MEDIUM/LOW/UNKNOWN.
python3 pipeline.py audit-quality

# Fix bad artist folder names across the library (Camelot prefixes, URL junk, symbols).
python3 pipeline.py artist-folder-clean --dry-run

# Merge artist folder spelling variants into a single canonical folder.
python3 pipeline.py artist-merge --dry-run

# Mark DB rows stale when the file no longer exists on disk.
python3 pipeline.py db-prune-stale --dry-run

```

### Audio Conversion

```bash
# Convert .m4a files to .aiff with parallel ffmpeg, preserving metadata and archiving originals.
python3 pipeline.py convert-audio --src PATH --dst PATH --archive PATH [FLAGS]

```

### Playlists And Export

```bash
# Generate all M3U playlists and Rekordbox XML from the library DB.
python3 pipeline.py playlists --dry-run

# Export library as Rekordbox-ready M3U playlists for Windows (Linux→Windows path mapping).
python3 pipeline.py rekordbox-export --dry-run

```

### Cues And Sets

```bash
# Auto-detect cue points (intro / drop / outro) and store in the DB.
python3 pipeline.py cue-suggest --dry-run

# Build an energy-curve DJ set from the library database and export as M3U + CSV.
python3 pipeline.py set-builder --dry-run

# Suggest the best next tracks using harmonic + BPM + energy scoring.
python3 pipeline.py harmonic-suggest --track "/music/sorted/Artist/track.mp3"

```

### Label Intelligence

```bash
# Scrape label metadata from Beatport / Traxsource and export to JSON/CSV/TXT/SQLite.
python3 pipeline.py label-intel

# Detect, normalize, and optionally write back label metadata (Phase 1: local).
python3 pipeline.py label-clean

```

### Metadata Intelligence

```bash
# Deterministic offline cleaning of all metadata fields. Removes URL watermarks, promo artifacts, DJ pool tags, malformed ISRCs, and BPM/key comment noise.
python3 pipeline.py metadata-sanitize --input ~/Music/inbox

# Deterministic artist normalization, alias resolution, and identity consistency across the library.
python3 pipeline.py artist-intelligence --input ~/Music/inbox

# Local AI (Ollama) metadata proposals for artist, title, version, label, remixers, and featured artists. Preview by default; --apply to write. BPM, key, and cues are never touched.
python3 pipeline.py ai-normalize --input ~/Music/inbox

# Fill missing album, label, and ISRC via Spotify + Deezer matching with confidence scoring. Preview by default; --apply to write. Artist field is never proposed.
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox

# Review and resolve medium-confidence enrichment results interactively. Reads entries populated by metadata-enrich-online.
python3 pipeline.py review-queue

# Rename audio files to {artist} - {title} ({version}).ext using embedded tags. Preview by default; --apply to commit.
python3 pipeline.py filename-normalize --input ~/Music/inbox

```

<!-- COMMANDS:END -->
