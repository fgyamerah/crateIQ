# Legacy DJ Toolkit Command Reference

> Historical archive preserved from the former lowercase `commands.md`.
> Commands and safety semantics may be outdated. Use the canonical
> [crateIQ command reference](../../COMMANDS.md) for current operations.

All commands run from the project root:


# Quick Start

## First-time cleanup (existing library)
python3 pipeline.py artist-folder-clean
python3 pipeline.py artist-merge
python3 pipeline.py metadata-clean
python3 pipeline.py label-clean
python3 pipeline.py dedupe

## Daily usage (new music)
python3 pipeline.py

## Build a DJ set
python3 pipeline.py set-builder --dry-run

## Get harmonic suggestions
python3 pipeline.py harmonic-suggest --key 8A --bpm 124

## Export to Rekordbox (Windows M:)
python3 pipeline.py rekordbox-export

## Safety Levels

Safe (no file modification):
- --dry-run
- harmonic-suggest
- cue-suggest --dry-run

Modifies metadata:
- metadata-clean
- label-clean

Moves files:
- dedupe (moves to quarantine)

Reorganizes structure:
- artist-folder-clean
- artist-merge

## Typical Workflow

1. Add new files to inbox
2. Run main pipeline
   python3 pipeline.py

3. Clean periodically
   python3 pipeline.py metadata-clean
   python3 pipeline.py label-clean

4. Remove duplicates
   python3 pipeline.py dedupe

5. Generate sets / playlists
   python3 pipeline.py set-builder

6. Export to Rekordbox
   python3 pipeline.py rekordbox-export

```bash
python3 pipeline.py [COMMAND] [FLAGS]
```

Use `--help` on any command for the full flag list:

```bash
python3 pipeline.py --help
python3 pipeline.py cue-suggest --help
```

---

## Contents

1. [Main Pipeline](#1-main-pipeline)
2. [Cleanup / Repair](#2-cleanup--repair)
3. [Metadata / Labels](#3-metadata--labels)
4. [Duplicate Detection](#4-duplicate-detection)
5. [Cue Analysis](#5-cue-analysis)
6. [Set Builder](#6-set-builder)
7. [Harmonic Suggestions](#7-harmonic-suggestions)
8. [Playlist / Export](#8-playlist--export)
9. [Rekordbox Export](#9-rekordbox-export)
10. [Utilities / Maintenance](#10-utilities--maintenance)

---

## 1. Main Pipeline

Processes everything in the inbox folder end-to-end: QC → organize → sanitize → analyze (BPM + key) → tag → cue-suggest → playlists + XML.

```bash
# Dry run — scan everything, no file changes
python3 pipeline.py --dry-run

# Full run — process all inbox files
python3 pipeline.py

# Full run on a custom directory instead of the configured inbox
python3 pipeline.py --path /mnt/music_ssd/KKDJ/

# Skip beets import (use built-in organizer only)
python3 pipeline.py --skip-beets

# Skip BPM + key analysis (useful for re-tagging only)
python3 pipeline.py --skip-analysis

# Re-run BPM + key on sorted tracks that are missing those values
python3 pipeline.py --reanalyze

# Skip cue point suggestion (speeds up re-runs)
python3 pipeline.py --skip-cue-suggest

# Verbose debug logging
python3 pipeline.py --verbose
```

### Pipeline steps (in order)

| Step | Description |
|------|-------------|
| 1. QC | Reject files below minimum bitrate / duration thresholds |
| 2. Beets | Auto-tag from MusicBrainz (skip with `--skip-beets`) |
| 3. Organize | Route files into sorted library folders by artist |
| 4. Sanitize | Strip URL / promo junk from all embedded tags |
| 5. Analyze | Detect BPM (aubio) and musical key (keyfinder-cli) |
| 6. Tag | Write cleaned + detected values back to audio file tags |
| 7. Cue suggest | Auto-detect intro / drop / outro positions |
| 8. Playlists | Generate M3U playlists + Rekordbox XML |

### Key pipeline flags

| Flag | Effect |
|------|--------|
| `--dry-run` | No file changes; full analysis only |
| `--skip-beets` | Skip MusicBrainz auto-tagging |
| `--skip-analysis` | Skip BPM + key detection |
| `--reanalyze` | Re-analyze sorted tracks missing BPM/key |
| `--skip-cue-suggest` | Skip cue point detection step |
| `--path DIR` | Override the music root directory |
| `--verbose` / `-v` | Enable debug logging |

---

## 2. Cleanup / Repair

### artist-folder-clean

Retroactively fix artist folder names that were created with bad values (Camelot key prefixes, bracket junk, URL noise, symbol-heavy names).

```bash
# Scan and report only — no file moves
python3 pipeline.py artist-folder-clean --dry-run

# Apply all recoverable renames and merges
python3 pipeline.py artist-folder-clean --apply

# Scan a custom directory
python3 pipeline.py artist-folder-clean --apply --path /mnt/music_ssd/KKDJ/
```

**Detection rules:**

| Rule | Example |
|------|---------|
| `pure_camelot` | `10B`, `1A` |
| `camelot_prefix` | `1A - Afrikan Roots` → rename/merge |
| `bracket_junk` | `[HouseGrooveSA]` |
| `url_junk` | `djcity.com` |
| `symbol_heavy` | < 40% alphanumeric characters |

**Outcomes:** `rename` (target does not exist) · `merge` (target already exists) · `review` (unrecoverable, report only)

| Flag | Effect |
|------|--------|
| `--dry-run` | Report only, no moves |
| `--apply` | Apply recoverable renames/merges |
| `--path DIR` | Scan this directory instead of the sorted library |
| `--verbose` / `-v` | Debug logging |

---

### artist-merge

Find artist folders that represent the same artist (different capitalisation, featuring suffixes, collaborator order) and merge them into a single canonical folder.

```bash
# Scan and report only
python3 pipeline.py artist-merge --dry-run

# Apply safe merges (case / feat / collab differences only)
python3 pipeline.py artist-merge --apply

# Scan a custom directory
python3 pipeline.py artist-merge --apply --path /mnt/music_ssd/KKDJ/
```

Safe merges (only capitalisation / feat / collaborator differences) are applied automatically. Uncertain merges (primary artist differs) are written to a review report.

| Flag | Effect |
|------|--------|
| `--dry-run` | Report only, no moves |
| `--apply` | Apply safe merges |
| `--path DIR` | Scan this directory instead |
| `--verbose` / `-v` | Debug logging |

---

## 3. Metadata / Labels

### metadata-clean

Scan all processed tracks for URL / promo junk embedded in metadata fields and optionally write cleaned values back.

```bash
# Preview all field changes — no writes
python3 pipeline.py metadata-clean --dry-run

# Apply changes across the library
python3 pipeline.py metadata-clean

# Apply on a custom directory
python3 pipeline.py metadata-clean --path /mnt/music_ssd/KKDJ/
```

**Fields cleaned:** `title`, `artist`, `album`, `albumartist`, `genre`, `comment`, `label` (organization/TPUB), `grouping` (TIT1), catalog number.

**What is removed:** URLs/domains · DJ pool phrases (djcity, zipdj, musicafresca) · promo phrases (official audio, free download) · Camelot/BPM noise in comment fields.

| Flag | Effect |
|------|--------|
| `--dry-run` | Preview changes, no writes |
| `--path DIR` | Scan this directory instead |
| `--verbose` / `-v` | Debug logging |

---

### label-clean

Detect, normalize, and optionally write back record label metadata from embedded tags and filename patterns.

```bash
# Scan and report — no writes (default)
python3 pipeline.py label-clean

# Write high-confidence labels (≥ 0.85) back to TPUB tag
python3 pipeline.py label-clean --write-tags

# Export only tracks that need manual review
python3 pipeline.py label-clean --review-only

# Broaden write threshold (accepts more results)
python3 pipeline.py label-clean --write-tags --confidence-threshold 0.75

# Scan a custom directory
python3 pipeline.py label-clean --path /mnt/music_ssd/KKDJ/
```

**Detection order (with confidence):**

| Source | Confidence |
|--------|-----------|
| organization/TPUB embedded tag | 0.95 |
| grouping tag fallback | 0.75 |
| comment tag fallback | 0.60 |
| filename pattern parsing | 0.55–0.70 |
| unresolved | 0.00 |

| Flag | Effect |
|------|--------|
| `--dry-run` | Explicit scan-only mode |
| `--write-tags` | Write labels above confidence threshold to TPUB |
| `--review-only` | Export only unresolved / low-confidence tracks |
| `--confidence-threshold FLOAT` | Minimum confidence for write-back (default: 0.85) |
| `--path DIR` | Scan this directory instead |
| `--verbose` / `-v` | Debug logging |

---

### label-intel

Scrape label metadata from Beatport and/or Traxsource using a seeds file and export results.

```bash
# Scrape from default seeds file and both sources
python3 pipeline.py label-intel

# Custom seeds file
python3 pipeline.py label-intel --label-seeds /music/data/labels/seeds.txt

# Beatport only, slower request rate
python3 pipeline.py label-intel --label-sources beatport --label-delay 3.0

# Skip enrichment step (faster, search results only)
python3 pipeline.py label-intel --label-skip-enrich
```

| Flag | Default | Effect |
|------|---------|--------|
| `--label-seeds FILE` | `config.LABEL_INTEL_SEEDS` | One label name per line |
| `--label-output DIR` | `config.LABEL_INTEL_OUTPUT` | Output directory |
| `--label-cache DIR` | `config.LABEL_INTEL_CACHE` | HTTP cache directory |
| `--label-sources SOURCE…` | `beatport traxsource` | Sources to scrape |
| `--label-delay SECS` | `2.0` | Per-host request delay |
| `--label-skip-enrich` | — | Skip label page enrichment |
| `--verbose` / `-v` | — | Debug logging |

---

### Label enrichment from library

Build / update the local label database using the organization/TPUB tags already present in your sorted library (no scraping, no internet).

```bash
python3 pipeline.py --label-enrich-from-library
```

Reads the label tag from every `ok`-status track in the DB. Run this after `label-clean --write-tags` to keep the label database in sync.

---

## 4. Duplicate Detection

### dedupe

Detect and quarantine duplicate audio files across the library. Files are **moved, never deleted**.

```bash
# Preview duplicate groups — move no files
python3 pipeline.py dedupe --dry-run

# Quarantine duplicates (moves to _duplicates/)
python3 pipeline.py dedupe

# Scan a custom directory
python3 pipeline.py dedupe --path /mnt/music_ssd/KKDJ/

# Quarantine to a custom directory
python3 pipeline.py dedupe --quarantine-dir /music/review/
```

**Detection cases:**

| Case | Description | Action |
|------|-------------|--------|
| A — Exact duplicate | Same SHA-256 hash | Keep one, quarantine rest |
| B — Quality duplicate | Same track, different format/bitrate | Keep best quality, quarantine rest |
| C — Different versions | Extended Mix vs Radio Edit etc. | Keep all, report only |

**Quality priority (highest first):** WAV/AIFF > FLAC > MP3 320 > MP3 256 > M4A > MP3 192 > OGG/OPUS > MP3 128 > MP3 <128

| Flag | Effect |
|------|--------|
| `--dry-run` | Preview groups, move no files |
| `--path DIR` | Scan this directory instead of the library DB |
| `--quarantine-dir DIR` | Move duplicates here (default: `library/sorted/_duplicates/`) |
| `--verbose` / `-v` | Debug logging |

---

## 5. Cue Analysis

### cue-suggest

Automatically detect cue point positions for tracks in the library and store them in the database.

> **Note:** These are *suggested* positions only. Native Rekordbox hot-cues are **not** written by this tool. Always review cues in Rekordbox before a live set.

```bash
# Analyse tracks, print results — no DB writes
python3 pipeline.py cue-suggest --dry-run

# Analyse all library tracks and store cues in DB
python3 pipeline.py cue-suggest

# Analyse files directly from a folder (no DB record required)
python3 pipeline.py cue-suggest --path /mnt/music_ssd/KKDJ/

# Limit to 20 tracks for testing
python3 pipeline.py cue-suggest --limit 20

# Analyse only tracks matching an artist/title substring
python3 pipeline.py cue-suggest --track 'Black Coffee'

# Export only JSON master output
python3 pipeline.py cue-suggest --export-format json
```

**Cue types detected:**

| Cue type | Description |
|----------|-------------|
| `intro_start` | Bar 1 — always present, confidence 1.0 |
| `mix_in` | First stable DJ entry point |
| `groove_start` | First full-arrangement section |
| `drop` | Main energy arrival / impact |
| `breakdown` | Significant energy/density reduction after peak |
| `outro_start` | Beginning of the mix-out section |

**Analysis modes:** Full mode uses RMS energy + low-frequency energy (< 250 Hz, bass/kick proxy) + spectral flux — all bar-grid aligned via BPM. Falls back to BPM-only heuristics when audio decode is unavailable.

**Output files:**

```
logs/cue_suggest/cue_suggestions.json      master, all tracks
logs/cue_suggest/cue_suggestions.csv       wide format, 1 row/track
logs/cue_suggest/runs/cues_TIMESTAMP.csv   per-run detail log
```

| Flag | Effect |
|------|--------|
| `--dry-run` | Analyse and print, no DB writes |
| `--path DIR` | Analyse files in this directory (no DB required) |
| `--limit N` | Process at most N tracks |
| `--track NAME` | Filter by artist/title/filename substring |
| `--min-confidence FLOAT` | Minimum confidence to store (default: 0.4) |
| `--export-format FMT` | Output formats: `json`, `csv`, or `json,csv` |
| `--verbose` / `-v` | Debug logging |

---

## 6. Set Builder

### set-builder

Automatically build a DJ set from the library database, arranging tracks across energy phases with harmonically compatible transitions.

```bash
# Preview the set — no files written
python3 pipeline.py set-builder --dry-run

# Build a 60-minute peak-energy set (default)
python3 pipeline.py set-builder --vibe peak

# 90-minute warm set
python3 pipeline.py set-builder --vibe warm --duration 90

# Deep afro house set, energy_lift transitions
python3 pipeline.py set-builder --vibe deep --genre 'afro house' --strategy energy_lift

# Named set (controls output filenames)
python3 pipeline.py set-builder --vibe peak --duration 60 --name saturday_night
```

**Phases (always in this order):**

| Phase | Energy | BPM range |
|-------|--------|-----------|
| warmup | Chill / Mid | 100–125 |
| build | Mid / Peak | 118–130 |
| peak | Peak | 124–150 |
| release | Mid / Chill | 110–128 |
| outro | Chill / Mid | 95–125 |

**Vibe presets (time allocation per phase):**

| Vibe | warmup | build | peak | release | outro |
|------|--------|-------|------|---------|-------|
| `warm` | 30% | 30% | 15% | 15% | 10% |
| `peak` | 12% | 20% | 40% | 18% | 10% |
| `deep` | 25% | 30% | 15% | 20% | 10% |
| `driving` | 15% | 25% | 35% | 15% | 10% |

**Transition strategies:**

| Strategy | Description |
|----------|-------------|
| `safest` | Highest Camelot × BPM composite score |
| `energy_lift` | Prefer tracks with higher energy or BPM (harmonic-gated) |
| `smooth_blend` | Very close BPM + Camelot (minimal pitch correction) |
| `best_warmup` | Chill/Mid energy, relaxed BPM |
| `best_late_set` | Peak energy, high BPM, strong Camelot |

**Output:**

```
logs/set_builder/<name>.m3u8   playable playlist
logs/set_builder/<name>.csv    full metadata + transition notes
```

| Flag | Default | Effect |
|------|---------|--------|
| `--dry-run` | — | Preview only, no files |
| `--vibe VIBE` | `peak` | Phase-weight preset (warm / peak / deep / driving) |
| `--duration MINS` | `60` | Target set duration in minutes |
| `--genre GENRE` | — | Restrict selection to this genre (substring) |
| `--strategy STRATEGY` | `safest` | Transition ranking strategy |
| `--start-energy TIER` | — | Preferred energy for the first track (Chill / Mid / Peak) |
| `--end-energy TIER` | — | Preferred energy for the last track |
| `--name NAME` | auto | Base filename for output files |
| `--verbose` / `-v` | — | Debug logging |

---

## 7. Harmonic Suggestions

### harmonic-suggest

Given a track (or a manual key + BPM), rank every library track by harmonic compatibility and print the top suggestions.

```bash
# Suggest from a specific track file
python3 pipeline.py harmonic-suggest --track '/mnt/music_ssd/KKDJ/library/sorted/A/ATFC/track.mp3'

# Manual mode (no file needed)
python3 pipeline.py harmonic-suggest --key 8A --bpm 128

# Change strategy and show top 20
python3 pipeline.py harmonic-suggest --key 8A --bpm 128 --strategy energy_lift --top-n 20

# Include energy and genre context for better scoring
python3 pipeline.py harmonic-suggest --key 5B --bpm 124 --energy Peak --genre 'afro house'

# Save results to JSON
python3 pipeline.py harmonic-suggest --key 8A --bpm 128 --json

# Print only — do not write JSON
python3 pipeline.py harmonic-suggest --key 8A --bpm 128 --json --dry-run
```

**Scoring factors:**

| Factor | Weight | Description |
|--------|--------|-------------|
| Camelot compatibility | 35% | Camelot wheel distance (same key = 1.0, clash = 0.10) |
| BPM compatibility | 30% | Tempo delta, halftime/doubletime aware |
| Energy compatibility | 20% | Peak / Mid / Chill tier match |
| Genre compatibility | 15% | Exact / related / different |

**BPM step constraint:** Transitions > ±6 BPM receive a hard 0.10× penalty. Transitions > ±3 BPM are linearly penalised. This prevents 122→150 BPM jumps from ranking highly.

| Flag | Default | Effect |
|------|---------|--------|
| `--track PATH` | — | Suggest from a library track (mutually exclusive with `--key`) |
| `--key KEY` | — | Camelot key (e.g. `8A`, `5B`) — use with `--bpm` |
| `--bpm BPM` | — | BPM of the current track — use with `--key` |
| `--strategy STRATEGY` | `safest` | Ranking strategy |
| `--top-n N` | `10` | Number of results |
| `--energy TIER` | — | Energy tier context (Chill / Mid / Peak) |
| `--genre GENRE` | — | Genre context for scoring |
| `--json` | — | Write results to JSON file |
| `--dry-run` | — | Print only, skip JSON write |
| `--verbose` / `-v` | — | Debug logging |

---

## 8. Playlist / Export

### playlists

Generate all M3U playlists and the internal Rekordbox XML from the current library database. Runs automatically at the end of the main pipeline; use this command to regenerate without re-processing the inbox.

```bash
# Preview — no files written
python3 pipeline.py playlists --dry-run

# Generate all M3U playlists + Rekordbox XML
python3 pipeline.py playlists

# Skip XML (M3U playlists only)
python3 pipeline.py playlists --no-xml

# Skip Key and Route playlists
python3 pipeline.py playlists --no-key --no-route

# Override music root
python3 pipeline.py playlists --path /mnt/music_ssd/
```

**Output structure:**

```
playlists/m3u/                    letter playlists (A.m3u8 … Z.m3u8)
playlists/m3u/_all_tracks.m3u8   master "all tracks" playlist
playlists/m3u/Genre/              Afro House.m3u8, Amapiano.m3u8 …
playlists/m3u/Energy/             Peak.m3u8, Mid.m3u8, Chill.m3u8
playlists/m3u/Combined/           Peak Afro House.m3u8, Chill Deep House.m3u8 …
playlists/m3u/Key/                1A.m3u8, 1B.m3u8 … 12A.m3u8, 12B.m3u8
playlists/m3u/Route/              Acapella.m3u8, Tool.m3u8, Vocal.m3u8
playlists/xml/rekordbox_library.xml
```

> **Note:** M3U paths in this command use **relative** paths (portable, Linux-native). For Windows-absolute paths use `rekordbox-export` instead.

| Flag | Effect |
|------|--------|
| `--dry-run` | Show what would be written, create no files |
| `--no-genre` | Skip Genre/ playlists |
| `--no-energy` | Skip Energy/ playlists |
| `--no-combined` | Skip Combined/ playlists |
| `--no-key` | Skip Key/ (Camelot) playlists |
| `--no-route` | Skip Route/ playlists (Acapella, Tool, Vocal) |
| `--no-xml` | Skip Rekordbox XML export |
| `--path DIR` | Override the music root directory |
| `--verbose` / `-v` | Debug logging |

---

## 9. Rekordbox Export

### rekordbox-export

Export the full library as a plug-and-play Rekordbox package for Windows. Converts all Linux paths to Windows M: drive paths and generates both XML and M3U playlists with Windows-absolute paths.

```bash
# Preview — no files written, tag validation warnings shown
python3 pipeline.py rekordbox-export --dry-run

# Full export (XML + all M3U playlists)
python3 pipeline.py rekordbox-export

# XML only (skip M3U)
python3 pipeline.py rekordbox-export --no-m3u

# M3U only (skip XML)
python3 pipeline.py rekordbox-export --no-xml

# Override drive mapping at runtime
python3 pipeline.py rekordbox-export --win-drive M --linux-root /mnt/music_ssd
```

**Path mapping:**

| Linux | Windows |
|-------|---------|
| `/mnt/music_ssd/KKDJ/library/sorted/A/ATFC/track.mp3` | `M:/KKDJ/library/sorted/A/ATFC/track.mp3` |

Default: `RB_LINUX_ROOT = /mnt/music_ssd`, `RB_WINDOWS_DRIVE = M`

Override permanently in `config_local.py`:

```python
from pathlib import Path
RB_LINUX_ROOT    = Path("/mnt/music_ssd")
RB_WINDOWS_DRIVE = "M"
```

Or via environment variables:

```bash
export RB_LINUX_ROOT=/mnt/music_ssd
export RB_WIN_DRIVE=M
```

**Output structure:**

```
_REKORDBOX_XML_EXPORT/
    rekordbox_library.xml       import into Rekordbox (File → Import)
    export_report.txt           per-track tag validation warnings

_PLAYLISTS_M3U_EXPORT/
    Genre/<genre>.m3u8
    Energy/<level>.m3u8
    Combined/<name>.m3u8
    Key/<camelot>.m3u8
    Route/<route>.m3u8
```

**Tag validation** runs automatically before export and flags:

- Missing or raw-filename title
- Missing / Unknown artist
- Missing or out-of-range BPM (50–220)
- Missing or junk genre
- Missing or invalid Camelot key
- Junk characters in filename

Issues are warnings only — they do not block the export.

**Importing into Rekordbox (Windows):**

1. Copy the SSD / sync via rsync
2. Open Rekordbox → File → Import Collection in rekordbox XML format
3. Navigate to `M:\KKDJ\_REKORDBOX_XML_EXPORT\rekordbox_library.xml`
4. Import → Rekordbox will read BPM, key, genre, label from the XML
5. Let Rekordbox analyse waveforms and beatgrids
6. Export to USB as normal

| Flag | Effect |
|------|--------|
| `--dry-run` | Preview, no files written (tag warnings still shown) |
| `--no-xml` | Skip Rekordbox XML generation |
| `--no-m3u` | Skip M3U playlist generation |
| `--win-drive LETTER` | Windows drive letter (default: `M`) |
| `--linux-root PATH` | Linux path = root of the Windows drive (default: `/mnt/music_ssd`) |
| `--verbose` / `-v` | Debug logging |

---

## 10. Utilities / Maintenance

### Re-analyze missing BPM / key

Re-run BPM and key detection on sorted library tracks that are already in the DB but are missing those values (useful after adding new analysis tools).

```bash
python3 pipeline.py --reanalyze
```

### Enrich label DB from library

Update the local label database by reading the organization/TPUB tag from all `ok`-status library tracks. No internet required.

```bash
python3 pipeline.py --label-enrich-from-library
```

### Check what the pipeline would do

```bash
# Full pipeline dry run — see every decision without touching a file
python3 pipeline.py --dry-run --verbose
```

### Run a targeted subset

```bash
# Only metadata cleaning + fresh playlist generation
python3 pipeline.py metadata-clean && python3 pipeline.py playlists

# Full repair sequence after importing a new batch
python3 pipeline.py dedupe --dry-run
python3 pipeline.py dedupe
python3 pipeline.py metadata-clean
python3 pipeline.py artist-merge --dry-run
python3 pipeline.py artist-merge --apply
python3 pipeline.py playlists
python3 pipeline.py rekordbox-export
```

---

## Configuration

Settings live in `config.py`. Override without touching that file by creating `config_local.py` in the same directory (it is git-ignored):

```python
# config_local.py — local overrides, not committed to git
from pathlib import Path

DJ_MUSIC_ROOT    = "/mnt/music_ssd/KKDJ"
RB_LINUX_ROOT    = Path("/mnt/music_ssd")
RB_WINDOWS_DRIVE = "M"

PLAYLIST_MIN_TRACKS = 3          # minimum tracks to write a playlist
CUE_SUGGEST_MIN_CONFIDENCE = 0.5 # raise cue confidence floor
```

Key environment variables:

| Variable | Default | Effect |
|----------|---------|--------|
| `DJ_MUSIC_ROOT` | `/music` | Music library root |
| `DJ_WIN_DRIVE` | `E` | Windows drive letter for legacy XML |
| `RB_LINUX_ROOT` | `/mnt/music_ssd` | Linux mount = root of Windows drive |
| `RB_WIN_DRIVE` | `M` | Windows drive letter for rekordbox-export |
| `RMLINT_BIN` | `rmlint` | Path to rmlint binary |
| `AUBIO_BIN` | auto | Path to aubio binary |
| `KEYFINDER_BIN` | `keyfinder-cli` | Path to keyfinder-cli |
| `FFPROBE_BIN` | `ffprobe` | Path to ffprobe |
| `BEET_BIN` | `beet` | Path to beet |
