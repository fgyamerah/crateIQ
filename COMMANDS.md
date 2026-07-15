# CrateIQ Commands

A reference for the CrateIQ intelligence pipeline CLI.

Version 2.0.0 &nbsp;·&nbsp; Updated 2026-04-20

---

## Table of Contents

- [Core Pipeline](#core-pipeline)
- [Operational States](#operational-states)
- [Safety Guarantees](#safety-guarantees)
- [metadata-sanitize](#metadata-sanitize)
- [artist-repair](#artist-repair)
- [ai-normalize](#ai-normalize)
- [artist-intelligence](#artist-intelligence)
- [metadata-enrich-online](#metadata-enrich-online)
- [review-queue](#review-queue)
- [filename-normalize](#filename-normalize)
- [library-organize](#library-organize)

---

## Core Pipeline

`metadata-sanitize` → `artist-repair` → `artist-intelligence` → `ai-normalize` → `metadata-enrich-online` → `filename-normalize` → `library-organize`

Each stage is standalone. Run one, or compose the full pipeline.  
Preview by default — nothing writes without `--apply`.

---

## Operational States

Applies to `metadata-enrich-online` results:

| State | Condition | Action |
|---|---|---|
| **APPLY** | conf ≥ 0.80, all safety rules pass | Written with `--apply` |
| **REVIEW** | 0.70 ≤ conf < 0.80 | Added to review queue |
| **SKIP** | Hard safety block fires | Moved to IGNORED with `--move-ignored` |

---

## Safety Guarantees

- Artist field: **never proposed or modified**
- BPM, key, and cues: **never modified** — Mixed In Key owns these
- Version mismatch: conflicting version tokens → confidence capped at 0.74
- Low artist similarity (< 0.90, no ISRC anchor): confidence capped at 0.74
- ISRC exact match: overrides all formula limits → confidence 0.98
- Preview by default on every command — nothing writes without `--apply`

---

## Incremental Processing

All processing stages (`metadata-sanitize`, `artist-repair`, `ai-normalize`, `artist-intelligence`, `metadata-enrich-online`, `filename-normalize`, `library-organize`) track processed state in the SQLite database.

On repeated runs, files whose path, size, and modification time haven't changed are automatically skipped — no reprocessing, no API calls, no prompts.

**Skipped statuses** (auto-skipped on re-run):

| Status | Meaning |
|---|---|
| `success` | Changes were applied or file was renamed |
| `no_change` | Analysed; nothing needed changing |
| `skipped` | Skipped for a deterministic reason (missing tags, hard reject) |
| `ignored` | File moved to IGNORED quarantine |

**Always reprocessed** (never auto-skipped): `error` (retried each run), `review` (re-evaluated in case alias store changed).

### Flags (available on all five stages)

| Flag | Description |
|---|---|
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear all processed-state records for this stage before running. |

### Recommended workflow

```bash
# First run — processes everything
python3 pipeline.py metadata-sanitize --input ~/Music/sorted --apply

# Subsequent runs — unchanged files skipped automatically
python3 pipeline.py metadata-sanitize --input ~/Music/sorted --apply

# Force full reprocessing after a rule update
python3 pipeline.py metadata-sanitize --input ~/Music/sorted --apply --force

# Reset tracking and reprocess from scratch
python3 pipeline.py metadata-sanitize --input ~/Music/sorted --apply --reset-stage
```

---

## metadata-sanitize

Deterministic offline cleaning of all metadata fields. Removes URL watermarks, promo artifacts, DJ pool tags, malformed ISRCs, and BPM/key comment noise.

> Idempotent — re-running a clean file produces no further changes.
> Safe to run before any AI or enrichment step.

### Purpose

- Strips URL watermarks, promo tags, and DJ pool artifacts from every metadata field
- Removes malformed ISRCs and BPM/key noise embedded in comment fields
- Runs fully offline — no network, no AI, no external dependencies
- Safe to repeat — already-clean files produce no further changes

### Common usage

```bash
python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to process. |
| `--apply` | Write changes to files. Without this flag, preview only. |
| `--verbose` | Enable debug logging. |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |

### Examples

```bash
python3 pipeline.py metadata-sanitize --input ~/Music/inbox
python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply
```

---

## artist-repair

Detects merged/concatenated artist names before artist-intelligence runs. Uses a [a-z][A-Z] mid-word boundary scan with country-suffix and prefix guards. Confidence-gated: HIGH (both split halves found in the known-artist set) is write-eligible; MEDIUM/LOW go to review queue only.

> Safe to run before artist-intelligence — operates on raw artist tags, no alias store dependency.
> Preview by default — nothing writes or moves without explicit flags.

### Purpose

- Detects "African RootsLebo" → proposes "African Roots, Lebo" before AI stages see the bad data
- Builds a known-artist dict from folder hierarchy, sampled audio tags, and the alias store
- Queues LOW/MEDIUM confidence splits for human review; auto-applies only HIGH confidence with `--apply`
- Country suffix guard: (IT), (De), (UK), (ZA) stripped before analysis, restored in output
- Prefix guard: boundaries in first 3 chars of a word skipped (Mc, De, La, mOat-at-word-start)

### Common usage

```bash
python3 pipeline.py artist-repair --input ~/Music/sorted
python3 pipeline.py artist-repair --input ~/Music/sorted --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to scan. |
| `--apply` | Write HIGH-confidence repairs to files. |
| `--move-artist-review` | Move LOW/MEDIUM-confidence flagged files to `.BIN/CHKARTISTNAMES/`. |
| `--limit N` | Cap files processed (useful for spot checks). |
| `--verbose` | Enable debug logging. |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |
| `--log-dir DIR` | Write run log/summary JSON to this directory. |

### Review queue

Flagged files are written to `data/intelligence/artist_repair_queue.json`. Each entry includes:
- `original` — the raw artist tag value
- `proposed` — the comma-separated split
- `confidence` — 0.45 / 0.65 / 0.85
- `country_suffix` — any stripped suffix (e.g. `(UK)`)
- `apply_blocked` — `true` for LOW/MEDIUM (never auto-applied)

### Examples

```bash
# Preview all potential merges
python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted/A

# Preview a small sample
python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted --limit 50

# Apply HIGH confidence only
python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted --apply

# Quarantine LOW/MEDIUM for manual review
python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted --move-artist-review
```

---

## ai-normalize

Local AI (Ollama) metadata proposals for artist, title, version, label, remixers, and featured artists. Preview by default; --apply to write. BPM, key, and cues are never touched.

> Min confidence: 0.75 — proposals below threshold are skipped, not applied.
> --pre-sanitize: runs metadata-sanitize before inference (recommended).

### Purpose

- Proposes improved artist, title, version, label, and remixer values via a local LLM
- Uses Ollama — all inference runs on your machine, no data sent externally
- Skips proposals below 0.75 confidence; BPM, key, and cues are never touched
- Use `--pre-sanitize` to clean fields before inference in a single pass

### Common usage

```bash
python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to process. |
| `--apply` | Write accepted proposals to files. |
| `--pre-sanitize` | Run metadata-sanitize before AI inference. |
| `--min-confidence 0.75` | Minimum confidence to accept a proposal. |
| `--model MODEL` | Ollama model to use. Default: OLLAMA_DEFAULT_MODEL env. |
| `--verbose` | Enable debug logging. |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |

### Examples

```bash
python3 pipeline.py ai-normalize --input ~/Music/inbox
python3 pipeline.py ai-normalize --input ~/Music/inbox --apply
python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply
python3 pipeline.py ai-normalize --input ~/Music/inbox --min-confidence 0.80 --apply
```

---

## artist-intelligence

Deterministic artist normalization, alias resolution, and identity consistency across the library. Builds an alias store for consistent downstream processing.

> Package: intelligence/artist/

### Purpose

- Resolves artist name variants to a single canonical form across the library
- Stores aliases persistently for consistent cross-run identity resolution
- Handles collab/feat suffixes without corrupting the primary artist name
- Deterministic — same input always produces the same output

### Common usage

```bash
python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory or library path to process. |
| `--apply` | Write normalized artist tags to files. |
| `--verbose` | Enable debug logging. |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |

### Examples

```bash
python3 pipeline.py artist-intelligence --input ~/Music/inbox
python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply
```

---

## metadata-enrich-online

Fill missing album, label, and ISRC via Spotify + Deezer matching with confidence scoring. Preview by default; --apply to write. Artist field is never proposed.

> Operational states per track:
>   APPLY   conf >= 0.80; all safety rules pass -> written with --apply
>   REVIEW  0.70 <= conf < 0.80 -> added to review queue
>   SKIP    hard safety block fires -> moved to IGNORED with --move-ignored
> 
> IGNORED path: /home/koolkatdj/Music/music/IGNORED/

### Purpose

- Queries Spotify, Deezer, and Traxsource to fill missing album, label, and ISRC
- Routes each result to APPLY, REVIEW, or SKIP based on confidence and safety rules
- Artist field is never proposed; version mismatches block auto-apply
- Use `--move-ignored` to quarantine unresolvable files automatically

### Common usage

```bash
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to enrich. |
| `--apply` | Write APPLY-state changes to files. |
| `--min-confidence 0.80` | Minimum confidence to apply. Default: 0.80. |
| `--move-ignored` | Move all hard-rejected files to the IGNORED quarantine directory. |
| `--verbose` | Enable debug logging. |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |

### Examples

```bash
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --min-confidence 0.85
```

---

## review-queue

Review and resolve medium-confidence enrichment results interactively. Reads entries populated by metadata-enrich-online (REVIEW state: 0.70 <= conf < 0.80).

> Queue file: data/intelligence/enrichment_review_queue.json
> Actions: a=apply  s=skip  d=delete  n=next  q=quit

### Purpose

- Opens an interactive session to resolve REVIEW-state enrichment results
- Each entry shows proposed changes with before/after field values
- Accepted entries are written immediately; skipped entries stay in the queue
- Use `--list-only` to audit the queue without making any changes

### Common usage

```bash
python3 pipeline.py review-queue
```

### Flags

| Flag | Description |
|---|---|
| `--list-only` | Print all pending entries without entering interactive mode. |

### Examples

```bash
python3 pipeline.py review-queue
python3 pipeline.py review-queue --list-only
```

---

## filename-normalize

Deterministic filename normalization using embedded tags. Renames audio files to `{artist} - {title} ({version}).ext`. Preview by default; `--apply` to commit.

> Version deduplication: if version is already in the title, it is not appended again.
> No overwrite: collisions get a safe suffix ` (1)`, ` (2)`, …
> Tags are never modified. BPM, key, and cues are untouched.

### Purpose

- Renames files whose filename encodes genre/BPM/key rather than artist/title
- Reads artist, title, and version from embedded tags (ID3, Vorbis, MP4)
- Skips files missing artist or title tags — never guesses
- Preserves directory structure — only renames, never moves folders

### Common usage

```bash
python3 pipeline.py filename-normalize --input ~/Music/inbox --apply
```

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to process. Required. |
| `--apply` | Commit renames. Without this flag, preview only. |
| `--verbose` | Show skipped and no-change files; enable debug logging. |
| `--limit N` | Process at most N files (useful for spot-checking). |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |
| `--move-artist-review` | Move unsafe-artist files to `.BIN/ARTIST_REVIEW/` (requires `--apply`). |

### Unsafe Artist Review

Files whose artist tag looks like concatenated names (e.g. `Prince KaybeeShimzaBlack`) are **never renamed** — the tag must be corrected manually first. Every such file is:

1. Logged to `data/review/artist_review_queue.jsonl` (appended each run).
2. Optionally moved to `.BIN/ARTIST_REVIEW/` when `--move-artist-review --apply` is passed (relative directory structure preserved).

After correcting the tag, rerun with `--force` to re-evaluate the file.

```bash
# Preview — see which files are flagged
python3 pipeline.py filename-normalize --input ~/Music/inbox

# Apply renames and move review files in one pass
python3 pipeline.py filename-normalize --input ~/Music/inbox --apply --move-artist-review

# Re-evaluate a previously skipped file after tag correction
python3 pipeline.py filename-normalize --input ~/Music/inbox --apply --force
```

**Review queue path:** `data/review/artist_review_queue.jsonl`
**Quarantine dir:** `.BIN/ARTIST_REVIEW/` (auto-excluded from all scans)

### Examples

```bash
python3 pipeline.py filename-normalize --input ~/Music/inbox
python3 pipeline.py filename-normalize --input ~/Music/inbox --apply
python3 pipeline.py filename-normalize --input ~/Music/inbox --limit 50
python3 pipeline.py filename-normalize --input /mnt/music_ssd/KKDJ/sorted --apply
python3 pipeline.py filename-normalize --input ~/Music/inbox --apply --move-artist-review
```

---

## library-organize

Deterministic folder reorganization using embedded artist tags. Moves each audio file into `<sorted_root>/<first-letter>/<primary-artist>/<filename>`. Preview by default; `--apply` to commit.

> Primary artist = the part of the artist tag before the first collaboration separator (`feat.`, `ft.`, `&`, `,`, `;`, `x`, `vs.`, `with`, `pres.`).
> Tags are never modified. BPM, key, and cues are untouched.
> No overwrite: collisions get a safe suffix ` (1)`, ` (2)`, …

### Purpose

- Merges fragmented artist folders (`Papik`, `Papik & Bengi`, `Papik feat. X`) under a single `Papik/` directory
- Determines the sorted root automatically — point `--input` at `sorted/` or any sub-folder
- Skips files missing an artist tag or whose artist looks like concatenated names without a separator

### Primary artist extraction

| Artist tag | Primary artist | Target folder |
|---|---|---|
| `Papik feat. Michele Ranieri` | `Papik` | `sorted/P/Papik/` |
| `Papik & Bengi` | `Papik` | `sorted/P/Papik/` |
| `Black Coffee, Bucie` | `Black Coffee` | `sorted/B/Black Coffee/` |
| `Black Motion` | `Black Motion` | `sorted/B/Black Motion/` |
| `&ME` | `&ME` | `sorted/#/&ME/` |
| `2Point1` | `2Point1` | `sorted/#/2Point1/` |

### Unsafe artist skip

If the artist tag has no recognized separator AND contains 2+ CamelCase transitions (e.g. `KaybeeShimzaBlack`), the file is skipped with `unsafe_primary_artist`. Fix the tag manually, then rerun with `--force`.

### Flags

| Flag | Description |
|---|---|
| `--input DIR` | Directory of audio files to organize. Required. |
| `--apply` | Commit moves. Without this flag, preview only. |
| `--verbose` | Show already-correct files; enable debug logging. |
| `--limit N` | Process at most N files (useful for spot-checking). |
| `--force` | Reprocess all files, ignoring processed-state tracking. |
| `--reset-stage` | Clear processed-state tracking for this stage before running. |
| `--move-unsafe-artists` | Move unsafe concatenated-name files to `.BIN/CHKARTISTNAMES/` for manual review. Requires `--apply` to execute; preview shows `WOULD MOVE TO CHKARTISTNAMES`. |
| `--flatten-collab-folders` | Repair mode: collapse nested collaborator folders back to the primary artist level. Does not read tags. |

### Unsafe Artist Review

If the artist tag has no recognized separator **and** contains 2 or more CamelCase transitions (e.g. `Prince KaybeeLaSoulMatesTNS`), the organizer cannot safely determine the primary artist.

Without `--move-unsafe-artists` these files are left in place and counted as **Left in place** in the summary.  
With `--move-unsafe-artists --apply` they are moved to `.BIN/CHKARTISTNAMES/`, preserving the relative directory structure from `--input`.

```
FROM: /mnt/music_ssd/KKDJ/sorted/_compilations/Zakes BantwiniKasango - Osama.mp3
TO  : /mnt/music_ssd/KKDJ/.BIN/CHKARTISTNAMES/_compilations/Zakes BantwiniKasango - Osama.mp3
```

`.BIN/CHKARTISTNAMES/` is automatically excluded from all future scans (dot-prefix rule). Fix the artist tag manually, then re-run with `--force` to re-evaluate.

**Review queue path:** `.BIN/CHKARTISTNAMES/` (relative to sorted-root parent)

### Rebuild order

Run `library-organize` **after** filename-normalize and metadata-sanitize so artist tags and filenames are already clean when folder structure is decided.

```
1. filename-normalize    — clean filenames from tags
2. metadata-sanitize     — clean tags (remove watermarks, promo noise)
3. dedupe                — remove exact/quality duplicates
4. library-organize      — sort into letter/artist folders   ← this stage
5. ai-normalize          — AI-assisted tag proposals
6. artist-intelligence   — deterministic artist normalization
7. metadata-enrich-online — online label/ISRC enrichment
8. analyze-missing       — fill missing BPM / key
9. rekordbox-export      — export XML + M3U for Rekordbox
```

### Examples

```bash
# Preview — see what would move (no files touched)
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted

# Spot-check first 50 files in P/ subfolder
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted/P --limit 50

# Apply to full library
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --apply

# Re-evaluate after fixing a tag
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --apply --force

# Reset tracking and reprocess from scratch
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --apply --reset-stage

# Preview which files have unsafe artist names (no files touched)
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --move-unsafe-artists

# Move unsafe-artist files to .BIN/CHKARTISTNAMES for manual review
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --apply --move-unsafe-artists

# Repair existing nested collaborator folders (preview)
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --flatten-collab-folders

# Repair existing nested collaborator folders (apply)
python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --flatten-collab-folders --apply
```

---
