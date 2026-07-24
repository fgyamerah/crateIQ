# crateIQ Product Vision and Roadmap

Status: strategy document — no code changes accompany this file.
Date: 2026-07-24
Related documents: `AGENTS.md`, `AUDIT_REPORT.md`, `docs/CRATEIQ_ROADMAP.md`,
`docs/CRATEIQ_PRODUCT_AUDIT.md`, `PROJECT_CONTEXT.md`, `README.md`, `COMMANDS.md`.

This document defines what crateIQ currently is, what it should become, the gap
between the two, and a phased path to close that gap. It complements — and does
not replace — the existing UX/platform roadmap in `docs/CRATEIQ_ROADMAP.md`;
section 15 maps the two together.

---

## 1. Executive Summary

crateIQ today is a **local-first DJ library operations toolkit**: a large,
safety-obsessed Python CLI pipeline (40+ subcommands), a read-first FastAPI
backend, and a React operational dashboard, all built around one principle —
*never blindly mutate a DJ library*. Its strongest muscles are deterministic
metadata sanitation, artist intelligence, enrichment scoring with human review,
path auditing, and Rekordbox-oriented export. Mixed In Key is treated as the
authoritative owner of BPM, key, and cue data.

The owner's goal is broader: a **master system** for a DJ library — cleaning,
understanding (genre + harmonic intelligence), listening, playlisting, and
exporting to Rekordbox/Serato — while never destroying data.

The gap is real but smaller than it looks. Roughly 60% of the target already
exists in some form: cleaning, enrichment, review queues, Camelot scoring,
set building, and Rekordbox export are all implemented. What is missing is:

1. **A first-class metadata model** — canonical track identity, structured
   artist/title/version/remix fields, per-field provenance and confidence.
2. **A genre intelligence layer** — today genre is a single free-text tag;
   there is no taxonomy, no normalization, no region/mood dimensions.
3. **Listening** — there is no audio player anywhere in the product.
4. **Manual playlists** — set-builder generates sets, but there is no
   hand-curated playlist workflow in the UI.
5. **Serato export** — only Rekordbox XML/M3U exists.
6. **Free zero-credential metadata sources** — MusicBrainz/Deezer are the
   right backbone; MusicBrainz is still only a TODO.
7. **Runtime readiness** — setup is fragile (hardcoded `/music`,
   `/mnt/music_ssd/KKDJ` defaults; no preflight; no auth).

The recommended strategy: **keep the review-first safety doctrine exactly as
it is**, promote the `tracks` table into a real metadata model, add a genre
taxonomy and a Camelot engine as *pure, deterministic, testable libraries*,
and only then build the player, playlists, and export bridges on top.

The single best next task (section 16): implement the **local-runtime
preflight and readiness contract** — it is already the top recommendation of
`AUDIT_REPORT.md`, `NEXT_TASKS.txt`, and Phase 1 of the existing roadmap, and
everything else in this document builds on a reliably bootable app.

---

## 2. What crateIQ Currently Is

Based on the repository as of this audit, crateIQ is three cooperating layers
over one SQLite database per library root (`<root>/logs/processed.db`):

### 2.1 The CLI pipeline (`pipeline.py`, ~8,000 lines, 40+ subcommands)

The core intelligence chain (all preview-by-default, write with
`--apply --yes`):

`metadata-sanitize` → `artist-repair` → `artist-intelligence` →
`ai-normalize` (local Ollama) → `metadata-enrich-online`
(Spotify/Deezer/Traxsource) → `filename-normalize` → `library-organize`

Plus operational commands: `path-audit`, `path-reconcile`, `build-tracks`,
`extract-track-metadata`, `dedupe`, `audit-quality`, `convert-audio`,
`cue-suggest`, `set-builder`, `harmonic-suggest`, `playlists`,
`rekordbox-export`, `enrichment-review`, `enrichment-apply-approved`,
`review-queue`, and more.

### 2.2 The FastAPI backend (`backend/app/`)

Read-first API over the selected library root plus a jobs subsystem
(`backend/data/jobs.db`) that executes an **allowlisted** subset of pipeline
commands as subprocesses (argument lists, never shell strings). Route groups:
health/stats, tracks, library, jobs, enrichment/insights, analysis (BPM
anomalies), metadata-repair, metadata-sanitation, playlists (set builder),
exports (validation + Rekordbox export jobs), sync (rsync SSD sync with
preview), and reconciliation (read-only ledger). No authentication of any
kind; trusted-local-only by policy.

### 2.3 The React/Vite dashboard (`frontend/`)

Fourteen supported routes (`/`, `/quality`, `/issues`, `/enrichment`,
`/metadata-repair`, `/metadata-sanitation`, `/bpm-review`, `/audit`,
`/folders`, `/jobs`, `/set-builder`, `/exports`, `/sync`,
`/reconciliation`), with legacy pages redirected. It is an operational
console: track browsing with virtualized tables, issue queues,
approve/reject/defer moderation, job monitoring, export validation, sync
preview. It cannot write tags or move files directly.

### 2.4 The philosophy (as practiced, not just documented)

- **Review-first**: proposal ≠ approval ≠ apply ≠ verification.
- **Dry-run by default**; writes need `--apply --yes` or `confirm=true`.
- **Deterministic before AI, local before online.**
- **MIK/Rekordbox own BPM, key, beatgrid, cues** — crateIQ only fills gaps.
- **Confidence-gated automation**: enrichment apply ≥ 0.90, review 0.75–0.89;
  AI normalization gate 0.80; hard caps (0.74) on artist/version conflicts;
  ISRC exact match scores 0.98.
- **Quarantine, never delete**: `.BIN/QUARANTINE`, `.BIN/IGNORED`, etc.
- **Everything logged**: JSONL run logs, paged detail files, audit artifacts.

### 2.5 What already exists that the owner may not realize

- A working **Camelot engine** (`modules/harmonic.py`): wheel parsing,
  distance/mode scoring, BPM tolerance bands, energy tiers, genre relations,
  transition strategies (`safest`, `energy_lift`, `smooth_blend`,
  `best_warmup`, `best_late_set`) — used by `harmonic-suggest` and
  `set_builder.py`.
- **Key/genre/energy M3U playlists** (`modules/playlists.py`, config
  toggles for Genre/Energy/Combined/Key/Route playlist folders).
- **Junk cleaning** across `sanitizer.py`, `metadata_sanitize.py`,
  `junk_patterns.py`, `metadata_clean.py`: URL junk, promo/DJ-pool junk,
  bare-number strips with protected-word guards, label-suffix stripping,
  trailing-BPM removal, feat-casing normalization.
- **Filename parsing with confidence levels** (HIGH/MEDIUM/LOW) that rejects
  weak parses rather than inventing metadata.
- 860 passing tests on a normal host.

### 2.6 Known weak points (from `AUDIT_REPORT.md` and verified here)

- No auth; permissive local CORS; destructive toggles reachable via API.
- Hardcoded path defaults in `config.py` (`/music`, `/mnt/music_ssd/KKDJ`,
  Windows drive letters) — portability is fragile.
- **No preflight module exists** (`backend/app/core/preflight.py` and
  `tests/test_preflight.py` are recommended in docs but not yet written).
- Path-keyed state (queues, review files) drifts after moves/renames;
  reconciliation apply is intentionally unimplemented (Phase 7 spec only).
- `modules/organizer.py` is legacy/deprecated but still present.
- Genre is a single free-text column; no taxonomy.
- No audio playback, no manual playlists, no Serato support, no MusicBrainz.
- Naming still mixes CrateIQ / CrateMindAI / DJ Toolkit / TrackIQ / KKDJ.

---

## 3. What crateIQ Should Become

The target product is one coherent system with seven roles, layered from
foundation to intelligence:

1. **Local-first safe library operations tool** *(foundation — largely
   exists)*: scan/ingest any library root, audit DB↔filesystem consistency,
   preview every change, quarantine instead of delete, roll back where
   possible, run entirely offline.

2. **Music library cleaner** *(exists, needs unification)*: one review
   workflow that cleans junk tags, bad filenames, URL/promo/DJ-pool junk,
   duplicate metadata, and messy artist/title/version fields — with per-field
   confidence and provenance, not seven separate command queues.

3. **DJ metadata intelligence system** *(partial)*: canonical track identity
   (audio fingerprint + ISRC + normalized artist/title/version), enrichment
   from free sources (MusicBrainz + Deezer backbone), remix/edit detection,
   label intelligence — always review-before-apply.

4. **Genre intelligence system** *(missing)*: raw genre preserved forever;
   canonical DJ-useful genre + subgenre + region/style + mood/energy tags
   derived through a versioned taxonomy with user-editable mappings and
   confidence scores. First-class support for Afro House, Amapiano, Afrobeats,
   Highlife, Hiplife, Gospel, House, Deep House, Hip Hop, R&B, Dancehall,
   Reggae, and related African/diaspora genres that mainstream taxonomies
   handle badly.

5. **Harmonic mixing assistant** *(engine exists; product missing)*: Camelot
   wheel (1A–12B) ↔ traditional key conversion, compatible-key rules,
   BPM/half-time/double-time awareness, energy curves, transition scoring,
   set-structure guidance (warmup → peak → outro), and "what mixes out of
   this?" answers surfaced in the UI — reading MIK data, never writing it.

6. **Playlist and set builder** *(auto-sets exist; manual missing)*: manual
   crates/playlists with drag-ordering, plus intelligent playlists defined by
   saved rules (genre/BPM/key/energy/quality filters) that stay live as the
   library changes, plus the existing harmonic set generator.

7. **DJ software export bridge** *(Rekordbox exists; Serato missing)*:
   validated, non-destructive export of library + playlists to Rekordbox
   (XML/M3U, path-mapped for Windows), Serato (crates), and generic M3U8 —
   always writing *new* export artifacts, never editing the DJ software's own
   database in place.

8. **In-app listening** *(missing)*: stream local files through the backend
   into a browser audio player so every review decision ("is this really the
   Extended Mix?") can be verified by ear without leaving the app.

The philosophy stays exactly as it is: crateIQ is the **operational layer
around** Rekordbox/Serato/MIK, not their replacement. It becomes the master
system for *metadata truth and library operations*, while performance data
(BPM/key/cues/beatgrids) remains owned by MIK and the DJ software.

---

## 4. Current vs Target Gap Analysis

| Area | Current state | Target state | Gap | Priority |
|---|---|---|---|---|
| Ingestion / scanning | Root-aware scan via `build-tracks`, `path-audit`, `extract-track-metadata`; skip-dirs; incremental `processed_state` | One "ingest" flow: scan → fingerprint → extract → identify → report, incremental, UI-visible | No fingerprinting; flow split across commands; no UI ingest wizard | P1 |
| Tag cleaning | Strong: `metadata-sanitize`, `metadata-clean`, junk patterns, artist repair/intelligence, 100s of tests | Same engines, unified review center, per-field provenance | Unify queues; add provenance | P1 |
| Filename cleaning | `filename-normalize` renames to `{artist} - {title} ({version})`; parser with confidence levels | Same + structured version field driving renames; rename-safe state (identity keys) | Path-keyed state drifts after renames | P1 |
| Metadata enrichment | Spotify/Deezer/Traxsource scoring + review queue + controlled DB-only apply | Add MusicBrainz (+ AcoustID) as zero-credential backbone; Last.fm tags; caching; per-field merge | MusicBrainz/AcoustID absent; no response cache; whole-candidate (not per-field) apply | P1 |
| Genre normalization | Free-text `genre` column; genre playlists match raw strings; Traxsource genre captured | Versioned taxonomy: raw preserved + canonical/subgenre/region/mood + confidence + user mappings | Entire taxonomy layer missing | P1 |
| Harmonic / Camelot intelligence | `harmonic.py`: wheel scoring, BPM bands, energy tiers, strategies; used by set-builder CLI + page | Extracted pure engine; key notation conversion; half/double-time; per-track "compatible tracks" in UI; transition explanations | Engine buried in CLI; no traditional-key conversion; no browse-time surfacing | P2 |
| Music player / listening | None | Backend range-request audio streaming + waveform-lite player; play from any track row/queue | Entirely missing | P2 |
| Manual playlists | None (only generated M3Us + auto sets) | Crates/playlists tables; add/remove/reorder in UI; export to M3U8/Rekordbox/Serato | Entirely missing | P2 |
| Intelligent playlists | Generated genre/energy/key/combined M3Us; harmonic set-builder | Saved rule-based smart playlists evaluated live against `tracks`; harmonic-aware ordering option | Rule engine + persistence missing | P2 |
| Rekordbox export | `rekordbox-export` XML/M3U, path mapping to Windows drive, validation endpoint + UI, M3U-safe default | Same + playlist-level export from new playlist system; round-trip validation | Solid; needs playlist integration | P3 |
| Serato export | None | Serato crate writing (`_Serato_/Subcrates/*.crate`) to a *copy/preview*, never in-place by default | Entirely missing; format has no official spec | P3 |
| Sync / export safety | rsync preview + confirm, `allow_delete` opt-in, job cancel, allowlists | Same + rollback manifests for applies; export never touches live DJ DBs | Rollback manifests incomplete | P2 |
| AI use | Local Ollama normalize (0.80 gate), schema validation, review datasets, optional Anthropic wrapper; prompt logging risk | Same doctrine; AI only proposes (genre hints, junk explanation, set narration); deterministic engines decide; opt-in prompt logging | Prompt-log privacy; AI/deterministic boundary must be documented per feature | P2 |
| Frontend UX | Dense operational console, 14 routes, module-oriented sidebar; no player, no playlist UI | Goal-oriented IA (Home / Library / Fix & Review / Sets / Publish / Operations per existing roadmap), player bar, playlist panel | Navigation redesign planned; new surfaces needed | P2 |
| Backend / API | Read-first, allowlisted jobs, narrow writes; silent empty fallbacks in places; no readiness endpoint | Readiness/preflight contract, honest errors, audio streaming, playlist/genre/harmonic endpoints | Preflight missing; fallback cleanup | P1 |
| Auth / local security | None (trusted-local-only); permissive dev CORS; destructive toggles API-reachable | Local auth (single-operator password/token), protected mutating routes, audit log of approve/apply | Entirely missing (accepted for now, required before any exposure) | P3 |
| Test coverage | 860 tests pass (host); pipeline/backend logic well covered; no frontend unit tests; no preflight/player/playlist tests | Contract tests for readiness, streaming, playlists, taxonomy, Camelot engine, Serato writer; keep safety-behavior tests as regression net | New features need tests-first; frontend testing strategy | P1 |

---

## 5. Recommended Product Architecture

Keep the three-layer shape (CLI pipeline / FastAPI / React). Change *what the
layers share*: today they share a database; they should share **engines**.

```text
                    ┌────────────────────────────┐
                    │  React dashboard (frontend) │
                    │  + player bar + playlists   │
                    └──────────────┬─────────────┘
                                   │ HTTP (local)
                    ┌──────────────▼─────────────┐
                    │  FastAPI backend            │
                    │  readiness · streaming ·    │
                    │  review · playlists · jobs  │
                    └──────┬───────────────┬─────┘
                           │               │ subprocess (allowlisted)
              ┌────────────▼───┐      ┌────▼────────────┐
              │  Shared engines │      │  pipeline.py CLI │
              │  (pure Python)  │◄─────┤  (thin command   │
              │                 │      │   wrappers)      │
              │ · camelot/      │      └─────────────────┘
              │   harmonic      │
              │ · genre         │
              │   taxonomy      │
              │ · identity/     │
              │   fingerprint   │
              │ · junk/clean    │
              │ · export        │
              │   writers       │
              └────────┬────────┘
                       │
        ┌──────────────▼───────────────┐
        │  SQLite per library root      │
        │  tracks (canonical) ·         │
        │  processed_state (history) ·  │
        │  field_provenance · playlists │
        │  · genre_map · review queues  │
        └──────────────────────────────┘
```

Guiding decisions:

1. **Extract engines, don't rewrite them.** `harmonic.py` scoring, junk
   patterns, and filename parsing are proven; move them toward pure functions
   with no `config`/`db` imports so backend, CLI, and tests use the same code.
2. **`tracks` remains the single canonical store**, extended additively
   (never destructive migrations): identity fields, structured
   artist/title/version, canonical genre fields, energy.
3. **Per-field provenance table** (`field_provenance`: track_id, field,
   value, source, confidence, applied_at, operation_id) instead of trying to
   encode provenance into `tracks` columns. This powers "why does this track
   say Amapiano?" and per-field rollback.
4. **Everything mutating goes through the existing lifecycle**
   (Detected → Proposed → Reviewed → Approved → Applied → Verified) already
   specified in `docs/CRATEIQ_ROADMAP.md` cross-phase contracts.
5. **The frontend never computes intelligence**; it renders engine output and
   collects decisions.

---

## 6. Metadata Intelligence Strategy

### 6.1 Better metadata model (additive schema evolution)

Extend `tracks` (all nullable, no destructive migration):

- Identity: `acoustid_fingerprint`, `fingerprint_short`, `musicbrainz_recording_id`, `isrc` (exists in apply allowlist already).
- Structure: `artist_primary`, `artists_featured` (JSON), `remixers` (JSON), `title_base`, `version` (e.g. "Extended Mix"), `edit_type` (original/remix/edit/bootleg/acapella/instrumental — the route folders already imply this vocabulary).
- Genre: `genre_raw` (immutable snapshot at ingest), `genre_canonical`, `subgenre`, `region_tags` (JSON), `mood_tags` (JSON), `genre_confidence`, `genre_source`.
- Quality/energy: existing `quality_tier`; add `energy_tier` (Peak/Mid/Chill already computed in `playlists.py` — persist it).

### 6.2 Canonical track identity

Today identity = file path, which is why renames cause state drift (known
risk #8 in AGENTS.md). Target: identity = **audio fingerprint first, path
second**.

- Compute a Chromaprint/AcoustID fingerprint at ingest (`fpcalc` binary,
  same optional-external-tool pattern as `ffprobe`/`aubio`).
- Key review queues and provenance on `track_id`/fingerprint, not path.
- `path-reconcile` then becomes dramatically safer: a moved file is *the same
  track* by fingerprint, so path repair is provable, and the long-deferred
  Phase 7 reconciliation apply gets a solid foundation.
- Dedupe upgrades from size/tag heuristics to fingerprint-verified duplicates.

### 6.3 Artist/title/version separation and remix detection

- Extend the existing parser (`filename_parse.py`, `parser.py`) to emit
  structured components rather than only cleaned strings: primary artist,
  featured artists (feat-handling logic already exists in artist
  intelligence), remixer ("(Xtetiqsoul Remix)" → remixer=Xtetiqsoul,
  edit_type=remix), version label (`_extract_version_label()` already exists
  in `metadata_matcher.py` — promote it to the shared model).
- Deterministic regex/vocabulary first (Original Mix, Extended, Radio Edit,
  Club Mix, Dub, VIP, Amapiano vocab like "Vocal Mix"/"Main Mix"); AI only
  proposes for the residue, gated at the existing 0.80 confidence.

### 6.4 Confidence scoring and review-before-apply

Keep the existing numeric doctrine and generalize it product-wide:

- ≥ 0.90 (or ISRC/fingerprint exact): eligible for auto-apply *when the user
  runs apply* — still never silent.
- 0.75–0.89: review queue.
- < 0.75 or any hard-block (artist mismatch, version conflict): skip/cap 0.74.
- Every applied field writes a `field_provenance` row → per-field rollback
  ("revert all genre changes from operation X") becomes trivial.

### 6.5 Unified Fix & Review center

Replace seven parallel queues (enrichment, metadata-repair, sanitation,
artist review, BPM anomalies, filename, dedupe) with one review surface
grouped by proposal type — this is exactly Phase 4 of the existing
`docs/CRATEIQ_ROADMAP.md` and this document endorses it.

---

## 7. Free Online Metadata Source Strategy

Priority order for a DJ library with heavy African/diaspora content:

| Source | Good for | Limitations | DJ-genre value | Mode |
|---|---|---|---|---|
| **Local file tags** (exists) | Ground truth the user already curated; MIK data lives here | Messy — that's the point of the product | High | Automatic (read) |
| **MIK / Rekordbox / Serato data** (partially read) | Authoritative BPM/key/cues; existing crates/playlists as import | Read-only by doctrine; Serato format undocumented | Very high | Automatic (read-only, never write back) |
| **MusicBrainz** (not yet integrated; TODO in NEXT_TASKS) | Canonical recording/release/artist IDs, ISRC lookup, relationships, zero credentials | 1 req/sec rate limit; weaker coverage of promo/DJ-pool edits and very new African releases | Medium-high (backbone for identity, not genre) | Automatic for ISRC/fingerprint exact match; review otherwise |
| **AcoustID** (companion to MusicBrainz) | Audio-fingerprint → recording ID even with garbage tags | Needs `fpcalc`; free API key (registration, no payment); coverage gaps for unreleased edits | High (identity for untagged promos) | Automatic lookup, review-only apply |
| **Deezer** (integrated) | Zero-credential search, good international/African catalog coverage, duration/artist/album checks | No genre per track (album-level only); no ISRC search on public API | Medium | Automatic candidate scoring (as today), review-gated apply |
| **Spotify** (integrated, optional credentials) | Broad catalog, ISRC, release dates, popularity | Requires client credentials; genre only at artist level; audio-features API deprecated for new apps | Medium | Optional; candidate scoring as today |
| **Last.fm** (not integrated) | Crowd-sourced tags — the best free signal for *genre/mood* vocabulary (users tag "amapiano", "gqom", "afro house") | Free API key required; tags are noisy and need taxonomy mapping; no strict schema | **High for genre intelligence** | Review-only; tags feed the genre mapper as *hints*, never applied directly |
| **Discogs** (provider stub exists in label_intel) | Label/catalog/release data, electronic-music genre+style fields, vinyl-era depth | Free token, 60 req/min; style vocabulary is collector-oriented; weak on digital-only DJ-pool content | Medium-high (labels + house/electronic styles) | Review-only |
| **Traxsource** (integrated, scraper) | Best-in-class Afro House / Soulful / Deep genre labels; label + remixer data | Scraping, selectors fragile (open TODO); no official API; rate-limit ethics | **Very high for house genres** | Review-only (as today); keep genre-aware trigger |
| **AcousticBrainz-style features** | BPM/key/energy estimates | Project discontinued (frozen dataset); would tempt violation of MIK rule | Low — **do not adopt** for BPM/key; frozen mood data optionally as hints | Review-only if ever used |
| **Beatport** (scraper exists for labels) | Electronic genre taxonomy, remixer credits | No free public API; scraping fragility | High for house/EDM taxonomy reference (as vocabulary, not live source) | Reference/review-only |

Strategy rules:

1. **Zero-credential backbone**: MusicBrainz + AcoustID + Deezer must make the
   product useful with no signup at all. Spotify/Last.fm/Discogs are optional
   enhancers behind env keys (pattern already exists for Spotify).
2. **Cache every response** (open P3 task — promote to P1): SQLite cache table
   keyed by (provider, query-hash) with TTL. Respect rate limits; make re-runs
   free and offline-replayable.
3. **Per-field merging**: a Deezer candidate may win `title`, Traxsource win
   `genre`, MusicBrainz win `isrc` — merge by field with per-field confidence
   rather than accepting one provider's whole record.
4. **Genre sources are hints, never direct writes**: all external genre
   signals flow into the genre mapper (section 8), which produces the
   reviewable proposal.
5. Paid services (Beatport API partnerships, etc.) remain optional and out of
   core requirements.

---

## 8. Genre Intelligence and Genre Conversion

### 8.1 Data model

Per track: `genre_raw` (preserved forever, never edited), `genre_canonical`,
`subgenre`, `region_tags[]`, `mood_tags[]`, `genre_confidence`,
`genre_source` (tag-exact / mapping-rule / provider-hint / user / ai).

Taxonomy store (versioned JSON under `data/intelligence/`, same pattern as
`artist_aliases.json`):

- `genre_taxonomy.json` — canonical genres, subgenres, parents, related-genre
  weights (this also replaces the hardcoded `_GENRE_RELATIONS` in
  `harmonic.py`), typical BPM ranges per genre (useful for half/double-time
  disambiguation and anomaly checks).
- `genre_mappings.json` — raw-string → canonical rules, shipped defaults +
  user-added mappings (mirrors the alias-store pattern with
  exact/normalized/case-insensitive lookup and confidence caps).

### 8.2 Resolution pipeline (deterministic first, AI last)

1. Exact/normalized match of `genre_raw` against mappings → confidence 0.95+.
2. Token rules ("afro" + "house" → Afro House; "soulful house" → House/Soulful).
3. Provider hints (Traxsource genre, Last.fm tags, Discogs style) → weighted
   vote, capped at review-level confidence.
4. Heuristic priors (BPM range + label intelligence + artist history:
   "artist's other 12 tracks are Amapiano") → hint only.
5. Optional local AI proposal for the residue → capped at 0.74 (always review).
6. User decision in review UI → confidence 1.0, and *optionally saves a new
   mapping rule* so the same raw string never asks again.

Unmapped raw strings are **left unmapped and queued** — never guessed.

### 8.3 Seed canonical taxonomy (target genres, examples)

| Raw tag examples | Canonical | Subgenre | Region/style | Typical BPM |
|---|---|---|---|---|
| "afrohouse", "Afro House / Afro Tech", "AFRO" | Afro House | Afro Tech / Afro Deep / 3-Step | South Africa / Angola | 118–125 |
| "amapiano", "piano", "Private School Piano", "sgija" | Amapiano | Private School / Sgija / Bacardi | South Africa | 108–118 |
| "afrobeats", "afro pop", "afrobeat" (modern pop context) | Afrobeats | Afro-fusion / Alté / Afroswing | Nigeria / Ghana / UK | 95–115 |
| "afrobeat" (Fela-style band context) | Afrobeat | — | Nigeria (classic) | 100–130 |
| "highlife", "hi-life" | Highlife | Classic / Burger Highlife | Ghana | 100–130 |
| "hiplife", "hip life" | Hiplife | — | Ghana | 85–110 |
| "gospel", "praise", "worship", "gospel house" | Gospel | Praise / Worship / Gospel House* | Ghana / Nigeria / US / SA | varies |
| "house", "club", "dance", "soulful house", "gqom"† | House | Soulful / Vocal / Tribal / Gqom† | global / Durban | 120–126 |
| "deep house", "deep", "deep tech" | Deep House | Deep Tech / Lo-fi Deep | global | 118–124 |
| "hip hop", "rap", "trap", "drill" | Hip Hop | Trap / Drill / Boom Bap | US / UK / global | 65–90 (140 double) |
| "rnb", "R&B", "r-n-b", "slow jams", "afro r&b" | R&B | Contemporary / Slow Jam / Afro-R&B | US / global | 60–95 |
| "dancehall", "bashment", "afro dancehall" | Dancehall | Classic / Modern / Afro-Dancehall | Jamaica / global | 95–105 |
| "reggae", "roots", "dub", "lovers rock" | Reggae | Roots / Dub / Lovers Rock | Jamaica | 70–90 |

\* Gospel House maps canonical=House, mood=Gospel — mood tags exist precisely
so one axis doesn't have to lose. † Gqom shown under House for wheel
adjacency; the taxonomy supports promoting it to a top-level canonical genre
via user mapping — user rules always outrank shipped defaults.

Note the two hard cases this design handles explicitly: **"afrobeat" vs
"afrobeats"** (disambiguated by artist era/provider hints, and queued for
review when ambiguous — never auto-resolved) and **"piano"** (Amapiano vs
classical piano — BPM prior + label/artist context, review on doubt).

### 8.4 Product behavior

- Genre normalization is a standard proposal type in the Fix & Review center.
- Bulk operations: "review all 214 tracks whose raw genre is 'Afro'".
- Genre M3U playlists and harmonic genre-compatibility switch to
  `genre_canonical` (falling back to raw) — instantly making the existing
  playlist generator and set builder smarter with no changes to them.

---

## 9. Harmonic Mixing and Camelot Intelligence

### 9.1 What exists (keep it)

`modules/harmonic.py` already implements the core correctly: Camelot parsing
(1A–12B), circular wheel distance with mode-switch handling, scored rules
(same key 1.00, ±1 0.90, relative A↔B 0.85, diagonal 0.80, ±2 0.55, clash
0.15), BPM tolerance bands (≤2% / ≤5% / ≤8% / ≤12%), energy-tier
compatibility, genre compatibility, and five ranking strategies including
warmup/late-set. `set_builder.py` consumes it for full set generation.

### 9.2 Target engine (`intelligence/harmonic/` as a pure library)

1. **Extract** the wheel/scoring code into a dependency-free module (no
   `config`/`db` imports) so backend endpoints, CLI, and tests share it.
2. **Notation conversion**: bidirectional Camelot ↔ traditional
   (8A = A minor, 8B = C major, …) plus Open Key (1m/1d) and common tag
   spellings ("Amin", "F#m", "Abm"). This also future-proofs reading key tags
   written by different tools. MIK-written Camelot values remain untouched —
   conversion is a *display/matching* layer only.
3. **Half-time/double-time awareness**: compare BPM at ×0.5/×1/×2 and pick
   the best interpretation (a 124 Afro House track vs an 85 Hip Hop track has
   a valid 124↔85×1.5 relationship only via genre-aware rules; 70 vs 140 is a
   clean double-time blend). Genre BPM priors from the taxonomy (section 8)
   feed this.
4. **Transition scoring with explanations**: composite score plus
   machine-readable reasons ("−1 on wheel = energy drop; +2.1% BPM; energy
   Mid→Peak") so the UI can *teach* harmonic mixing, per the assistant goal.
5. **Energy curve model**: a set is a target curve (warmup ramp, plateau,
   peak, cooldown); the builder scores candidate orderings against the curve
   using the persisted `energy_tier` plus BPM trajectory. Expose the curve
   visually in Set Builder.
6. **Genre movement**: genre-compatibility weights come from the taxonomy's
   related-genre graph (Afro House ↔ Afro Tech ↔ Deep House ↔ Amapiano
   adjacency), replacing the hardcoded `_GENRE_RELATIONS`.

### 9.3 Product surfaces

- Track inspector: "Compatible next tracks" panel (backend endpoint over the
  engine), filterable by strategy — this is the highest-value, lowest-risk
  harmonic feature since it's pure read.
- Set Builder: keep generation; add energy-curve visualization and
  per-transition explanation badges (the `.badge--info` Camelot badges
  already exist in the UI).
- Player integration (later): "audition this transition" queues the outro of
  A and intro of B.
- Assistant framing: every recommendation shows *why* in music-theory terms
  (relative minor, dominant, mode switch) — deterministic text from the
  engine's reason codes; AI may optionally rephrase, never decide.

Hard rule preserved: the engine **reads** BPM/key from MIK-owned tags/DB and
never writes or "corrects" them. Tracks with missing/invalid keys are
excluded from harmonic pools and surfaced as an issue count, not guessed.

---

## 10. Playlist and Intelligent Playlist Strategy

### 10.1 Manual playlists (new)

- Additive tables in the pipeline DB: `playlists` (id, name, kind
  [manual/smart], created_at, updated_at) and `playlist_tracks` (playlist_id,
  track_id, position). Track-id keyed, so file renames don't break crates
  (unlike M3U files).
- Backend CRUD endpoints + reorder; frontend playlist panel with add-from-any
  track table and drag ordering.
- The existing `set_playlists` tables (set-builder output in jobs.db) stay as
  generation history; a generated set can be "saved as playlist".

### 10.2 Intelligent (smart) playlists

- A smart playlist stores a **rule document**, not a track list:
  `{all/any: [ {field, op, value} ]}` over canonical fields (genre_canonical,
  subgenre, mood, bpm range, key/Camelot set, energy, quality_tier, year,
  added-date, label, missing-metadata flags).
- Evaluated live against `tracks` (SQL-translatable rules; the allowlisted
  sort/filter pattern in `track_service.py` extends naturally).
- Optional ordering modes: manual field sort, or "harmonic order" via the
  Camelot engine.
- Examples that fall out immediately: "Amapiano 110–115 BPM energy Peak",
  "8A/8B/9A warmup under 120", "added last 30 days, missing genre" (a smart
  playlist doubling as a workflow queue).

### 10.3 Export

Every playlist (manual or smart snapshot) exports through the existing bridge:
M3U8 always-safe, Rekordbox XML playlist nodes, Serato crate (section 12).
Generated M3U folders (Genre/Energy/Key/Combined) remain, driven by canonical
genre once section 8 lands.

---

## 11. Listening / Music Player Strategy

- **Backend**: `GET /api/tracks/{id}/audio` streaming endpoint with HTTP Range
  support (FastAPI `FileResponse`/`StreamingResponse`), strictly
  root-contained (reuse the existing path-containment validation), read-only,
  no transcoding in v1 (browsers handle MP3/AAC/FLAC/WAV natively in
  Chromium; log-and-skip for exotic formats). Optional later: on-the-fly
  ffmpeg transcode for AIFF/ALAC edge cases, using the existing `FFMPEG_BIN`
  pattern.
- **Frontend**: a persistent player bar (HTML5 `<audio>`; no heavy library
  needed — waveform via `wavesurfer.js` is a later, approval-gated
  dependency). Play from any track row, review queue item, or playlist. A
  small queue (play next / play selection).
- **Why it matters beyond listening**: audible verification is a *safety
  feature* — approving "this is the Extended Mix, not the Radio Edit" or
  "these two are true duplicates" by ear closes the biggest gap in the review
  workflow. Player placement should therefore prioritize the review center
  and dedupe queues, not just the library table.
- Non-goals: no DJ deck emulation, no cue-point editing (MIK/Rekordbox own
  cues), no library-wide waveform pre-rendering in v1.

---

## 12. Rekordbox, Serato, and DJ Software Export Strategy

### 12.1 Rekordbox (exists — refine)

- Keep `rekordbox-export` XML + M3U with Linux→Windows path mapping and the
  M3U-safe / `--force-xml`-explicit doctrine.
- Add playlist-tree export from the new playlist system (XML playlist nodes).
- Export validation (already a UI page) gains round-trip checks: every
  referenced path exists on the export target, every track has required tags,
  and a diff against the previous export (added/removed/changed).
- Never write to a live Rekordbox database/device directly; always produce
  import artifacts.

### 12.2 Serato (new)

- Serato crates are `.crate` binary files under `_Serato_/Subcrates/` next to
  the music (or on the drive root). The format is undocumented but stable and
  well reverse-engineered by open-source projects; implement a small pure
  writer/reader in `modules/serato_crates.py` (no new dependency required —
  the format is length-prefixed tag/value records).
- Safety model, in line with the sync doctrine: default output is a **staged
  export directory** (`_SERATO_EXPORT/`) plus a preview diff; writing into a
  real `_Serato_/` folder requires `--apply --yes` and backs up existing
  crate files first (timestamped copy) for trivial rollback.
- v1 scope: crates (playlists) only. Do **not** attempt to write Serato's
  beatgrid/cue tags (`Serato Markers2` etc.) — performance data ownership
  stays with Serato/MIK, mirroring the Rekordbox rule.
- Read side (valuable and zero-risk): import existing Serato crates as
  crateIQ playlists.

### 12.3 Generic bridge

- M3U8 remains the universal, always-safe interchange (Engine DJ, VirtualDJ,
  Traktor all import it). Relative-vs-absolute path mode per target.
- Export profiles: named target configs (root mapping, drive letter, format
  set) stored in config/DB instead of today's hardcoded
  `SSD_KKDJ_ROOT`-style constants — this folds into the preflight/config
  cleanup.

---

## 13. AI Role and Safety Boundaries

The doctrine from AGENTS.md stands: **deterministic decides, AI proposes.**

| Concern | Deterministic engine | AI (local-first) may… | AI must never… |
|---|---|---|---|
| Junk/tag cleaning | regex/vocabulary rules, sanitize modules | propose fixes for residue rows (existing ai-normalize, 0.80 gate) | write without review; touch artist field (existing rule) |
| Identity | fingerprint, ISRC, exact matching | suggest fuzzy matches for review | confirm identity |
| Genre | taxonomy + mapping rules + provider votes | propose canonical genre for unmapped residue, capped 0.74 → always review | apply genre directly; invent new canonical genres |
| Harmonic | Camelot engine (pure math) | rephrase reason codes into friendly explanations; narrate set structure | pick keys/BPM; override scores |
| Playlists | rule engine over canonical fields | suggest rule sets ("make me a warmup crate") that the user sees as an editable rule doc | silently materialize tracks |
| BPM/key/cues | MIK-owned; read-only everywhere | — | ever touch (absolute) |

Operational guardrails to add as AI use grows:

- Prompt logging becomes **opt-in** (today `last-prompts/` capture is a
  documented privacy risk), with a documented retention/cleanup story.
- Batch/cost governor for any non-local provider: max items per run, max
  runs per day, and an explicit `--provider` flag; the optional Anthropic
  wrapper stays off the default path.
- Model-output schema validation and confidence clamping stay mandatory
  (pattern already exists in `ai/metadata_schema.py`).
- Every AI proposal records model, prompt version, and confidence in
  provenance, so bad batches are identifiable and revertible as a group.

---

## 14. Local-First Safety Model

Everything in section 3–12 inherits the existing model, restated as the
product's constitution:

1. **Local-first**: fully functional offline except explicit enrichment runs;
   no telemetry; external calls only on user-initiated operations.
2. **Dry-run first**: every write-capable operation previews by default;
   apply requires `--apply --yes` / `confirm=true`.
3. **MIK/Rekordbox/Serato own performance data** — BPM, key, beatgrids, cues
   are read, never written (the audit found one legacy noisy-tagger rewrite
   path in `NEXT_TASKS`; closing it belongs in Phase 1 hardening).
4. **Raw data is never destroyed**: `genre_raw` and original tag snapshots
   are preserved; quarantine instead of delete; exports create artifacts
   rather than editing DJ databases in place.
5. **Provenance + rollback**: per-field provenance rows and per-operation
   manifests make "undo operation X" a query, not an archaeology dig.
6. **Identity over paths**: fingerprint-keyed state ends the rename-drift
   class of bugs rather than patching it.
7. **Honest state**: preflight/readiness tells the truth (ready / degraded /
   blocked / unknown); silent empty API fallbacks are replaced with explicit
   errors (audit finding #9).
8. **Trusted-local-only until auth exists** (Phase 9): no remote exposure
   guidance, mutating endpoints protected before any multi-user story.

---

## 15. Phased Roadmap

Relationship to `docs/CRATEIQ_ROADMAP.md` (Phases 0–10, UX/platform): that
roadmap remains valid for shell/navigation/review-UX work. The phases below
are the *product-capability* roadmap; the mapping is noted per phase. Where
both apply, the existing roadmap's cross-phase contracts (lifecycle, operation
result, safety levels) govern all new work.

### Phase 1 — Stabilize current foundation
*(aligns with existing roadmap Phases 1–2)*

- **Goal**: reliably bootable, honest-about-readiness app on any machine; no
  more hardcoded-path fragility; close known legacy write risks.
- **Scope**: implement `backend/app/core/preflight.py` + `/api/runtime/readiness`
  (library root, DB, external tools `ffprobe`/`ffmpeg`/`fpcalc`/`aubio`,
  env config, writable log dirs — each ready/degraded/blocked/unknown);
  `.env`-driven config for the remaining hardcoded roots
  (`SSD_KKDJ_ROOT`, `SET_BUILDER_OUTPUT_DIR`, RB paths); remove silent empty
  API fallbacks; retire `modules/organizer.py` from any callable path; fix
  the tagger BPM/key re-write noise (NEXT_TASKS P1); smoke tests for the 14
  supported routes against their API contracts.
- **Files/modules**: `backend/app/core/preflight.py` (new),
  `backend/app/api/routes/health.py`, `config.py`, `backend/app/core/config.py`,
  `modules/tagger.py`, `frontend` readiness banner, `tests/test_preflight.py`
  (new), `.env.example`.
- **Risks**: readiness accidentally doing expensive scans; breaking existing
  health consumers (extend, don't change `/api/health` shape).
- **Tests**: preflight contract tests (missing root / missing tool / ok),
  route smoke tests, config precedence tests.
- **User-visible outcome**: the app starts anywhere, says exactly what's
  ready/missing, and never pretends.

### Phase 2 — Metadata cleaning and review workflow
*(aligns with existing roadmap Phases 3–4)*

- **Goal**: one metadata model, one review center.
- **Scope**: additive `tracks` columns (identity, structured
  artist/title/version/edit_type), `field_provenance` table, fingerprinting
  at ingest (optional `fpcalc`), unified Fix & Review UI over the existing
  queues, per-field apply + rollback manifest, enrichment response cache.
- **Files/modules**: `db.py`, `modules/filename_parse.py`, `modules/parser.py`,
  new `intelligence/identity/`, `backend/app/api/routes/` review composition,
  `frontend/src/pages/` review center, existing queue modules unchanged
  underneath.
- **Risks**: schema migration mistakes (additive only, migration tests
  against fixture DBs); review-center scope creep (compose existing queues,
  don't rewrite their logic).
- **Tests**: migration idempotency, provenance write/rollback, fingerprint
  ingest with missing binary, review lifecycle state machine.
- **User-visible outcome**: one place to see every proposed change, why, with
  what confidence — and undo by operation.

### Phase 3 — Genre intelligence

- **Goal**: messy genre tags become clean DJ-useful genres, reviewably.
- **Scope**: `genre_taxonomy.json` + `genre_mappings.json` seeded with the
  section 8.3 taxonomy; `intelligence/genre/` resolver (deterministic chain);
  `genre-normalize` CLI (preview/apply pattern); genre proposals in the
  review center; Last.fm (optional key) + Traxsource + Discogs hints wired as
  votes; user mapping capture from review decisions; playlists switch to
  `genre_canonical` with raw fallback.
- **Files/modules**: new `intelligence/genre/`, `data/intelligence/genre_*.json`,
  `pipeline.py` subcommand, `modules/playlists.py` (read-side only),
  review-center extension.
- **Risks**: over-eager mappings (mitigate: shipped rules conservative, user
  rules explicit, everything reviewable); afrobeat/afrobeats and "piano"
  ambiguity (always queue on doubt).
- **Tests**: mapping resolution table tests (every 8.3 example), user-rule
  precedence, confidence caps, raw-preservation invariant.
- **User-visible outcome**: "Afro House (Afro Tech), South Africa, 121 BPM"
  instead of "AFRO"; genre playlists that finally mean something.

### Phase 4 — Harmonic/Camelot engine

- **Goal**: the existing scoring becomes a first-class assistant.
- **Scope**: extract `intelligence/harmonic/` pure engine from
  `modules/harmonic.py`; add notation conversion (Camelot ↔ traditional ↔
  Open Key), half/double-time logic with genre BPM priors, reason codes;
  `GET /api/tracks/{id}/compatible` endpoint; "Compatible tracks" panel in
  the track inspector; persist `energy_tier`; taxonomy-driven genre
  compatibility replacing `_GENRE_RELATIONS`.
- **Files/modules**: `modules/harmonic.py` → `intelligence/harmonic/`,
  `modules/set_builder.py` (import path update), new backend route, track
  inspector component.
- **Risks**: breaking set-builder behavior during extraction (golden-file
  tests on current scoring before refactor); key-notation edge cases.
- **Tests**: wheel math property tests, notation round-trip for all 24 keys,
  half/double-time cases, snapshot tests pinning current set-builder output.
- **User-visible outcome**: click any track → ranked compatible next tracks
  with human-readable music-theory reasons.

### Phase 5 — Music player and listening workflow

- **Goal**: hear anything, anywhere in the app.
- **Scope**: range-request streaming endpoint (root-contained, read-only);
  player bar component with queue; play buttons in track table, review
  center, dedupe queue, set builder.
- **Files/modules**: `backend/app/api/routes/tracks.py` (or new `audio.py`),
  `frontend/src/components/PlayerBar.tsx` (new), integration touches in
  existing pages.
- **Risks**: path traversal (reuse containment validation + tests); large
  FLAC/WAV seeking (Range support is mandatory, test it); codec gaps
  (explicit "can't preview this format" state, no silent failure).
- **Tests**: containment/traversal tests, Range semantics (206, partial
  reads), unsupported-format handling.
- **User-visible outcome**: press play on any row; verify before approving.

### Phase 6 — Playlists and intelligent playlists
*(aligns with existing roadmap Phase 6)*

- **Goal**: manual crates + live rule-based smart playlists.
- **Scope**: `playlists`/`playlist_tracks` tables (track-id keyed), CRUD +
  reorder API, playlist UI panel, smart-playlist rule engine (SQL-translated,
  allowlisted fields/ops), harmonic-order option, "save generated set as
  playlist".
- **Files/modules**: `db.py`, new `backend/app/api/routes/user_playlists.py`
  (existing `playlists.py` route serves set-builder — don't overload),
  `frontend` playlist components, `intelligence/harmonic` for ordering.
- **Risks**: rule-engine injection (strict allowlist, parameterized SQL, the
  pattern already exists in `track_service.py`); confusing overlap with
  set-builder history (clear naming: Crates vs Generated Sets).
- **Tests**: rule→SQL translation table tests, reorder semantics, smart
  playlist liveness (track edited → membership updates).
- **User-visible outcome**: build crates by hand or by rule; they survive
  file renames.

### Phase 7 — Rekordbox/Serato/export bridge
*(aligns with existing roadmap Phase 7 — Publish)*

- **Goal**: everything curated in crateIQ lands in the DJ software, safely.
- **Scope**: playlist-tree export into Rekordbox XML; `modules/serato_crates.py`
  reader+writer with staged-export default, backup-before-write, and
  `--apply --yes` gating; Serato crate *import* as playlists; export
  profiles replacing hardcoded export roots; export diff reports.
- **Files/modules**: `modules/rekordbox_export.py`,
  `modules/serato_crates.py` (new), `pipeline.py` subcommands, exports
  page/API, config/profile storage.
- **Risks**: Serato binary-format mistakes corrupting crates (staged +
  backup + round-trip read-verify after every write; never touch
  `Serato Markers2`); path mapping errors across OSes (profile tests).
- **Tests**: crate round-trip (write→read→equal), backup creation, staged vs
  in-place gating, Rekordbox XML playlist-node validation, path-mapping
  matrix.
- **User-visible outcome**: one Publish flow: pick playlists → preview diff →
  export to Rekordbox and/or Serato.

### Phase 8 — AI assistant and advanced recommendations

- **Goal**: conversational/assistive layer over the deterministic engines.
- **Scope**: AI-suggested smart-playlist rules; set narration and transition
  explanations (rephrasing engine reason codes); "explain this proposal"
  in review center; enrichment residue triage; batch/cost governor; opt-in
  prompt logging with retention policy.
- **Files/modules**: `ai/`, `utils/llm_client.py`, `utils/prompt_logger.py`,
  review-center and set-builder UI touches.
- **Risks**: boundary erosion (section 13 table becomes enforced policy:
  AI outputs are always proposals with capped confidence); privacy (opt-in
  logging shipped in the same phase, not after).
- **Tests**: schema-validation of every AI output path, confidence-cap
  enforcement, governor limits, logging opt-in default-off.
- **User-visible outcome**: "build me a 90-minute Amapiano warmup into Afro
  House peak" produces an editable rule set and an explained set draft — with
  the user approving every step.

### Phase 9 — Production/local security hardening
*(aligns with existing roadmap Phases 9–10 and AGENTS.md Phase 3/5)*

- **Goal**: safe beyond a single trusted terminal.
- **Scope**: local auth (single-operator password → token/session), route
  guards on all mutating endpoints, audit log of approve/apply actions
  (who/what/when — the provenance table already carries most of it),
  CORS tightening, packaging (one-command start), environment validation in
  CI, no-auth warnings removed only when true.
- **Files/modules**: `backend/app/` auth middleware + routes, frontend login,
  `systemd/`, packaging scripts, docs.
- **Risks**: breaking the local dev loop (auth must be trivially satisfiable
  locally); scope creep into multi-user (explicit non-goal until wanted).
- **Tests**: authz matrix (every mutating route rejects unauthenticated),
  session lifecycle, audit-log completeness.
- **User-visible outcome**: the app can sit on a home network without being
  a foot-gun.

---

## 16. Best Next Implementation Task

**Implement the local-runtime preflight and readiness contract (Phase 1,
step 1).** It is already the #1 recommendation of `AUDIT_REPORT.md` §15, an
open P1 in `NEXT_TASKS.txt`, and the entry requirement of existing-roadmap
Phase 1 — and every feature in this document assumes an app that can honestly
report whether it is ready.

Paste-ready implementation prompt:

> Implement the CrateIQ local-runtime preflight and readiness contract.
> Follow AGENTS.md (Modify Mode). Do not change pipeline behavior, existing
> route shapes, or any write semantics.
>
> 1. Create `backend/app/core/preflight.py` with a pure function
>    `run_preflight() -> PreflightReport` that checks, without expensive
>    library scans: (a) selected library root resolution
>    (`CRATEIQ_LIBRARY_ROOT`, deprecated `CRATEMINDAI_LIBRARY_ROOT` fallback,
>    `DJ_MUSIC_ROOT`) — exists, is a directory, is readable; (b) pipeline DB
>    presence at `<root>/logs/processed.db` (missing = degraded, not error);
>    (c) backend jobs DB directory writability; (d) availability of optional
>    external tools via `shutil.which`: ffprobe, ffmpeg, aubio, keyfinder-cli,
>    rmlint (each reported individually, missing = degraded); (e) whether
>    Spotify credentials are configured (never echo values). Each check
>    returns `{name, status: ready|degraded|blocked|unknown, detail}` and the
>    report includes an overall status (blocked if root unusable, else
>    degraded if any degraded, else ready).
> 2. Expose it as `GET /api/runtime/readiness` in a new
>    `backend/app/api/routes/runtime.py`, mounted under the existing `/api`
>    prefix. Do not change the `/api/health` response shape.
> 3. Add `tests/test_preflight.py` covering: missing root → blocked; root
>    without DB → degraded with db check degraded; all-good tmp fixture →
>    ready; missing external tool → degraded; secrets never appear in the
>    response. Use the existing conftest temp-root isolation.
> 4. Add a small readiness banner to the frontend Layout that calls the new
>    endpoint once on load and shows a dismissible warning when status is not
>    ready (no new dependencies, follow the existing ErrorBanner pattern).
> 5. Update README.md (runtime readiness section), COMMANDS.md if any CLI
>    surface changed (it should not), NEXT_TASKS.txt, PROJECT_CONTEXT.md, and
>    CHANGELOG.txt per AGENTS.md documentation rules.
> 6. Verify with `python -m pytest -q tests/test_preflight.py` then the full
>    `python -m pytest -q`, plus `npm --prefix frontend run typecheck` and
>    `npm --prefix frontend run build`. Report exact commands and results;
>    do not commit unless asked.

---

*End of document.*
