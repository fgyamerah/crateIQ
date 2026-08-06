# CrateIQ Waveform Architecture and Safety Design

**Status:** accepted design; Phase W1 foundation and Phase W2 safe extraction
wrapper implemented

**Date:** 2026-08-05

**Scope:** real audio waveform extraction, derived-cache lifecycle, API, and
frontend presentation

**Safety class:** read-only source access; application-owned derived writes only

> The waveform is a derived, disposable visualization cache. Source audio is
> authoritative and read-only. Deleting every waveform artifact must have no
> effect on playback, tags, metadata, crates, reviews, exports, or DJ software.

## 1. Decision summary

**Recommended extraction location: CrateIQ backend, with the frontend rendering
backend-generated canonical peak data.** This is a hybrid delivery model, not
browser extraction: decoding and caching are backend responsibilities; drawing,
responsive downsampling, progress overlay, and pointer presentation are frontend
responsibilities.

**Recommended waveform engine: the local FFmpeg/ffprobe toolchain, used only to
read/decode source audio to a bounded mono PCM stdout stream, followed by a
CrateIQ-owned deterministic min/max peak accumulator.** No output media file,
transcode, normalized file, tag, or sidecar is produced. `audiowaveform` is the
fallback engine if implementation measurement shows the FFmpeg wrapper is not
acceptable; it is not an automatic per-track fallback in version 1.

The version-1 design uses:

- explicit, demand-driven generation;
- no expensive side effects on `GET`;
- one waveform worker by default;
- mono full-band signed min/max pairs, not invented frequency bands;
- three bounded levels of detail: 256, 1,024, and an adaptive maximum of 32,768
  pairs;
- gzip-compressed, versioned JSON artifacts under
  `backend/data/cache/waveforms/` by default;
- source identity that starts with cheap library/track/stat data while full
  content SHA-256 remains nullable and deferred;
- waveform job and linkage state in the backend-owned `jobs.db`, never in the
  trusted pipeline `processed.db` track metadata;
- canvas rendering that consumes persistent-player state and calls its existing
  `seek(seconds)` action;
- the current deterministic three-band signal as a clearly labeled fallback.

### 1.1 Approved amendments incorporated by W1

Two details of the original design were amended before extraction work:

1. A full source SHA-256 is not a mandatory pass before decoding. W1 stores a
   cheap validated source-stat snapshot independently and keeps
   `source_sha256` nullable. W1 never reads source contents for hashing. W2/W3
   must choose a content-identity strategy that preserves cache correctness
   without forcing two immediate full-file reads for large sources.
2. `not_generated` is a normal lifecycle state for an existing track. A future
   read-only waveform endpoint should return a structured `200` state response;
   `404` remains reserved for a track that does not exist. Routine lifecycle
   states are not HTTP conflicts merely because no artifact exists yet.

## 2. Inspection findings and constraints

### 2.1 Scope inspected

The design was based on direct inspection of:

- `PersistentPlayerProvider.tsx`, `PersistentBottomPlayer.tsx`,
  `usePersistentPlayer.ts`, `AudioPreviewPlayer.tsx`, and
  `ThreeBandWaveform.tsx`;
- Library queue/selection integration in `LibraryView.tsx` and
  `TrackInspector.tsx`;
- Music Review queue/selection integration in `pages/Listening.tsx`;
- `frontend/src/api/audio.ts`, track types, player CSS, responsive behavior,
  focus labels, and reduced-motion handling;
- `backend/app/api/routes/tracks.py`, `track_service.py`, the track model and
  schemas, `pipeline_db.py`, `library_root.py`, backend configuration,
  readiness/capability code, job DB conventions, and safe subprocess examples;
- track schema/import/path-update behavior in `db.py` and
  `library_setup_service.py`;
- preview-audio range/MIME/path tests in `tests/test_backend_api.py`;
- commits `83bc009`, `66d1b27`, `1698e44`, `e37f693`, `91cbc6c`, `b53479c`,
  `988ac08`, and `7d0e791`;
- project context, current tasks, README, changelog, stability matrix, safety
  model, local tooling guidance, and the Night Deck design context.

`CURRENT_WORK.md` was not present.

### 2.2 Current playback architecture

`PersistentPlayerProvider` wraps the router, so its one `<audio>` element and
its current track, queue, time, duration, volume, play state, error state, and
minimized state survive route transitions. Library contributes the current
visible filtered page as a queue. Music Review contributes its current review
list and synchronizes player movement back to the selected row. The bottom
player is a state consumer, not an audio owner.

Manual and Smart Crates still use the separate page-local `AudioPreviewPlayer`.
Waveform version 1 should integrate first with the proven persistent Library and
Music Review path. Replacing the crate-local preview players is a separate task
and is not required for safe waveform delivery.

The persistent player already owns the authoritative seek operation:
`seek(seconds)` clamps and writes `HTMLMediaElement.currentTime`. Waveform code
must call that action; it must not create another `<audio>` element or maintain
an independent playback clock.

### 2.3 Current waveform presentation

`ThreeBandWaveform.tsx` generates 22 or 34 deterministic low/mid/high bar heights
from the integer track ID. It explicitly says no waveform analysis is performed,
reads no media, and is currently used by the persistent deck, Library inspector,
and Music Review.

Real version-1 peak data will be mono full-band amplitude. The UI must therefore
render an honest single waveform with the established teal/cyan/violet Night
Deck palette. It must not label one mono envelope as low/mid/high frequency
content. The existing component remains the decorative fallback until a real
artifact is ready or if generation fails.

### 2.4 Track identity and source audio

Frontend playback uses the integer `tracks.id`. The preview URL contains only
that ID. Backend code opens `processed.db` read-only, resolves the ID to the
indexed `tracks.filepath`, canonicalizes the path beneath the selected library
root, requires a regular existing file, and then streams it.

Track ID is a linkage key, not content identity:

- import uses unique filepath identity;
- a path update can retain an ID;
- deleting and re-importing can allocate a new ID;
- two tracks can contain identical bytes;
- a file can change in place without its ID changing;
- different configured libraries can reuse the same integer IDs.

Waveform state must therefore be keyed by `(library_id, track_id)` while cache
artifacts are keyed by source content plus waveform schema/algorithm versions.

### 2.5 Existing safe preview serving

`GET /api/tracks/{track_id}/preview-audio`:

1. accepts no client-supplied filesystem path;
2. looks up the track in the selected library's read-only DB;
3. applies `assert_path_under_root()` to the DB path and selected root;
4. rejects outside-root paths and missing/non-file sources;
5. streams fixed-size chunks;
6. supports one RFC-style byte range, suffix ranges, and `416` responses;
7. derives MIME type from the safe filename.

Waveform generation must reuse this identity and root boundary, and should
strengthen the file-open step against symlink races as described in section 17.

### 2.6 Information available without audio analysis

Without decoding source audio, CrateIQ already has:

- track ID and safe display metadata;
- backend-only indexed path and filename;
- indexed file size and optional duration/bitrate;
- filesystem size, timestamps, device, and inode from `stat`;
- browser-reported duration/current time once media metadata loads;
- the selected library root and a read-only pipeline DB connection;
- optional-engine availability and application-owned cache writability checks.

No existing value provides waveform peaks. BPM, key, Camelot, cues, review
state, and quality metadata must not be used to fabricate them.

## 3. Candidate architecture decision matrix

Scores use 1 (poor) through 5 (strong). Deployment burden is scored with 5
meaning least burden.

| Criterion | Browser / Web Audio | Backend FFmpeg + CrateIQ peaks | Backend `audiowaveform` | Backend Python `librosa`/`soundfile` |
| --- | ---: | ---: | ---: | ---: |
| MP3/FLAC/WAV compatibility | 3 | 5 | 5 | 4 |
| AAC/M4A compatibility | 2 | 5, build-dependent | 1 | 2, decoder-dependent |
| CPU efficiency | 2 | 4 | 5 | 2 |
| Memory efficiency | 1 | 5 | 5 | 3 with streaming only |
| Central cache support | 1 | 5 | 5 | 5 |
| Frontend simplicity | 1 | 5 | 5 | 5 |
| Backend simplicity | 5 | 3 | 4 | 3 |
| Local privacy | 5 | 5 | 5 | 5 |
| Large-file suitability | 1 | 5 | 5 | 3 |
| Deployment burden | 5 | 3 | 2 | 4 (already a dependency) |
| Maintainability | 2 | 4 | 4 | 2 |
| DJ waveform suitability | 2 | 4 | 5 | 3 |
| **Total / 60** | **30** | **53** | **51** | **41** |

### 3.1 Option A: frontend/browser extraction

Rejected as the default. `decodeAudioData()` generally needs the complete
encoded file and a complete decoded buffer. A five-minute compressed track can
expand to tens or hundreds of megabytes; a long lossless set can exhaust a tab.
Range support helps browser playback but does not make full Web Audio decoding
incremental or persistent. Each client/tab can repeat work, LAN clients download
the full source, mobile clients pay the CPU/memory cost, codec support varies by
browser, and durable cache ownership is awkward. Privacy remains local, but the
resource and consistency costs are unacceptable.

Browser extraction remains a possible development-only fallback for tiny files,
not a supported CrateIQ architecture.

### 3.2 Option B: backend extraction

Accepted. The backend already has authoritative track-to-path resolution,
selected-root enforcement, background job patterns, optional-tool detection,
and application-owned writable storage. One generation can serve every route,
tab, and LAN client. Decoded PCM can be streamed rather than loaded in memory,
and source access is isolated from React lifecycles.

The cost is an optional local native decoder and careful subprocess/resource
management. The feature must remain optional so a missing engine never prevents
CrateIQ startup or basic playback.

### 3.3 Option C: hybrid

Accepted as the complete system: backend extraction plus frontend rendering.
The backend produces reusable amplitude data, not a rendered PNG. The frontend
chooses canvas dimensions, colors, progress overlay, and responsive density.
This keeps extraction centralized and the Night Deck presentation flexible.

## 4. Extraction engine decision

### 4.1 Primary: FFmpeg decoder plus CrateIQ peak accumulator

FFmpeg is selected because CrateIQ already models `FFMPEG_BIN` and
`FFPROBE_BIN`, its normal builds cover the project's relevant MP3, FLAC, WAV,
AAC/M4A, AIFF, Ogg, and Opus inputs, and it can decode a chosen audio stream to
stdout without creating media output. Exact codecs remain build-dependent and
are handled per job.

The future wrapper should:

1. use ffprobe only for bounded read-only stream metadata needed for duration,
   channel/sample-rate reporting, and policy checks;
2. invoke FFmpeg with a fixed argument array selecting the first audio stream;
3. disable stdin and non-audio streams;
4. decode to 8 kHz, mono, signed 16-bit little-endian PCM on stdout;
5. consume stdout incrementally in fixed chunks;
6. compute signed min/max pairs without retaining full PCM;
7. write only an application-owned temporary cache artifact;
8. atomically publish that artifact after validation and a final source `stat`.

The conceptual decode shape is fixed by code, never supplied by an API client:

```text
ffmpeg -nostdin -hide_banner -loglevel error -threads 1 \
  -i <read-only-source> -map 0:a:0 -vn -sn -dn -ac 1 -ar 8000 \
  -f s16le -nostats -progress pipe:2 pipe:1
```

This command shape is documentation, not authorization to run it in this
design stage. In implementation it must be an argument list with `shell=False`.

This is decoding/read-only analysis. It is not an in-place transcode and must
not contain any output-media path, metadata mapping, overwrite flag, ReplayGain,
normalization, loudness, BPM, key, cue, artwork, or tag option.

FFmpeg is normally LGPL 2.1+ but builds that enable GPL components are GPL; an
installer or container must document the exact package/build it distributes.
CrateIQ should invoke the separately installed executable rather than link its
libraries. See [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html) and
[FFmpeg licensing guidance](https://ffmpeg.org/legal.html).

### 4.2 Fallback: `audiowaveform`

`audiowaveform` is a strong specialized fallback. It directly produces mono
min/max waveform data, has bounded 8/16-bit representations, and is suitable
for large MP3, WAV, FLAC, Ogg Vorbis, and Opus files. It does not natively cover
AAC/M4A in its documented input set, adds a less common system package, and its
ongoing development has moved from the BBC repository to Codeberg. It is GPL-3.0
and should remain a separately installed optional executable.

If selected after measurement, it must use JSON or binary waveform output in an
application-owned temporary directory—not PNG and never a source-directory
sidecar. CrateIQ must still normalize and validate its output into the canonical
schema below. See the upstream
[`audiowaveform` documentation](https://github.com/bbc/audiowaveform).

### 4.3 Rejected primary: Python-native loading

CrateIQ already depends on NumPy/librosa, but `librosa.load()` materializes a
time series and is unsafe for long sets. `librosa.stream()` is bounded but only
accepts codecs supported by `soundfile`; the broader `audioread` path is
deprecated. A Python peak implementation would inherit more decoder variability,
GIL/NumPy/process concerns, and maintenance burden. It remains useful for pure
unit tests of downsampling, not as the source decoder. See
[`librosa.stream`](https://librosa.org/doc/latest/generated/librosa.stream.html)
and [`librosa.load`](https://librosa.org/doc/latest/generated/librosa.load.html).

## 5. Canonical waveform data

### 5.1 Meaning

Version 1 stores one full-band mono envelope. Every point is a signed minimum
and maximum of decoded PCM in an equal-duration time bin. Source channels are
mixed to mono by the decoder. Values are normalized against signed 16-bit full
scale, not auto-normalized against the loudest sample in each track.

Version 1 deliberately does not store:

- rendered PNG/SVG images;
- stereo envelopes;
- invented low/mid/high bands;
- RMS/loudness/ReplayGain;
- spectral data, beat grids, BPM, key, cues, or markers;
- absolute or relative source paths.

Those omissions keep the artifact small, honest, and presentation-independent.

### 5.2 Artifact format

The disk artifact is UTF-8 JSON compressed with gzip and named `.json.gz`.
Gzip and JSON require no new Python or browser dependency. Integer peaks gzip
well; the frontend receives normal JSON after HTTP content decoding. A custom
binary format would be smaller and faster to parse, but adds versioning and
DataView complexity before the real payload profile is measured. Binary can be
reconsidered if version-1 performance thresholds are missed.

Conceptual schema:

```json
{
  "schema_version": 1,
  "algorithm_version": "mono-minmax-s16-v1",
  "cache_key": "sha256-hex",
  "source": {
    "sha256": null,
    "size_bytes": 28499123,
    "mtime_ns_at_generation": 1785950000000000000
  },
  "engine": {
    "name": "ffmpeg",
    "version": "normalized-version-string"
  },
  "audio": {
    "duration_ms": 247381,
    "source_channels": 2,
    "source_sample_rate_hz": 44100,
    "analysis_sample_rate_hz": 8000
  },
  "encoding": {
    "type": "int16_min_max_interleaved",
    "scale": 32767,
    "rendered_channels": 1
  },
  "resolutions": {
    "compact": {"pair_count": 256, "peaks": [-102, 311, -88, 290]},
    "player": {"pair_count": 1024, "peaks": [-42, 91, -51, 104]},
    "detail": {"pair_count": 4948, "peaks": [-8, 22, -9, 27]}
  }
}
```

Each `peaks` array has exactly `pair_count * 2` integers ordered
`[min0, max0, min1, max1, ...]`, with `-32768 <= min <= max <= 32767`.
Examples above are intentionally abbreviated.

The source fingerprint and engine block stay backend/cache internal. The
content hash may be populated by a future generation strategy but is not
required merely to represent state. Waveform
API responses omit source fingerprint fields. Engine availability/version is
reported through the local readiness capability, not repeated in every payload.

### 5.3 Validation limits

Before publish and on cache read, enforce:

- exact known schema and algorithm versions;
- compressed artifact size no greater than 1 MiB;
- decompressed JSON no greater than 4 MiB;
- only the three known resolution names;
- total pair counts within configured constants;
- exact interleaved lengths and integer bounds;
- finite positive duration at or below the duration limit;
- cache key equal to the computed key;
- no path-like fields or unknown executable output fields.

A validation failure marks the artifact corrupt, removes only that CrateIQ-owned
artifact, and returns the normal fallback state. It never affects playback.

## 6. Resolution and long-file policy

Version 1 stores three levels derived with peak-preserving downsampling:

| Level | Pair count | Intended use |
| --- | ---: | --- |
| `compact` | up to 256 | Library inspector and narrow/mobile surfaces |
| `player` | up to 1,024 | persistent bottom player |
| `detail` | adaptive, 2,048 to 32,768 | future full track detail and source for resizing |

Detail target:

```text
detail_pairs = min(decoded_sample_count,
                   clamp(ceil(duration_seconds * 20), 2048, 32768))
```

This yields approximately:

- 5 minutes: 6,000 pairs;
- 20 minutes: 24,000 pairs;
- 60 minutes: capped at 32,768 pairs (about 9.1 pairs/second);
- 180 minutes: capped at 32,768 pairs (about 3.0 pairs/second).

Seeking accuracy does not depend on the point count: pointer fraction maps to
the persistent audio element's duration. The cap limits disk, network, parsing,
and memory for 1–3 hour sets. Version 1 does not support zoomed sub-second
editing. If a future detailed editor needs it, add separately versioned tiles;
do not silently remove this cap.

Compact/player levels are computed from detail bins by taking the minimum of
all contributing minima and maximum of all contributing maxima. This preserves
transients better than averaging. Frontend resizing can further reduce the
selected level to approximately one vertical stroke per CSS pixel using the
same rule; it must never interpolate extra precision.

## 7. Cache location, ownership, and permissions

Default root:

```text
backend/data/cache/waveforms/
  v1/
    mono-minmax-s16-v1/
      ab/
        abcdef...json.gz
```

This follows the existing backend-owned `BACKEND_DATA_DIR` convention and is
already below a git-ignored tree. It is outside every selected music root in a
valid default installation. The two-character prefix prevents a single huge
directory. Filenames contain only lowercase SHA-256 hex.

Rules:

- cache directories are process-owned mode `0700`; artifacts are `0600`;
- an override must be absolute, writable, and canonicalized;
- the cache root must not be equal to, inside, or an ancestor of the selected
  library root;
- symlinked cache roots that resolve into a library root are rejected;
- temporary files are created in the final artifact directory and published
  with `os.replace()` after validation;
- no temporary or final file is ever created beside source music;
- an incomplete `.tmp.<uuid>` is never considered a cache hit.

Recommended setting:

```text
CRATEIQ_WAVEFORM_CACHE_DIR=<absolute override>
```

Default: `BACKEND_DATA_DIR / "cache" / "waveforms"`.

## 8. Cache identity and invalidation

### 8.1 Library identity

Backend state uses:

```text
library_id = SHA256("crateiq-library-v1" || canonical selected-library-root bytes)
```

Only the digest is stored or returned by waveform internals. This prevents ID
collisions between configured libraries without exposing the root.

### 8.2 Deferred content identity and cheap source signature

Cheap validation fields are stored internally per `(library_id, track_id)`
only after canonical selected-root validation:

```text
st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns
```

W1 also stores `source_sha256` and `cache_key` as nullable fields. It does not
hash source contents and does not define a cache key that requires a content
hash before state can exist.

A future strong content identity may still use:

```text
source_sha256 = SHA256(all source bytes read through an O_RDONLY descriptor)
cache_key = SHA256(
  "crateiq-waveform" || schema_version || algorithm_version ||
  source_size_bytes || source_sha256
)
```

Full hashing costs one sequential read. It must never happen during Library
listing/import/open or across the whole collection by default. W2/W3 must
decide whether hashing can safely share the decoder read, whether a later
verification pass is needed, and how cache reuse is upgraded when a deferred
hash becomes available. A sampled hash must not silently claim the same strong
identity as a complete content hash.

### 8.3 Required behavior

- **Same filename changed in place:** fast fields change and existing waveform
  state becomes stale; future content verification determines reuse.
- **Content changed with timestamp restored:** `ctime` normally invalidates;
  if all fast fields are deliberately forged, this trusted-local design cannot
  detect it until an explicit future regenerate/strong-verification request.
- **Rename or move:** cheap identity changes can mark state stale. Future strong
  identity may safely recover reuse without making W1 depend on it.
- **Duplicate files / track ID changes:** future content identity may enable
  artifact reuse; W1 makes no reuse claim without that identity.
- **Library rescan:** no generation occurs. Existing links are lazily checked.
- **Deleted source:** state becomes `source_missing`; artifact remains cleanup-
  eligible and basic playback keeps its existing unavailable behavior.
- **Modified during generation:** compare final `fstat/stat` with the initial
  fast fingerprint. Discard the temporary artifact and return `source_changed`.

Absolute paths are never part of an artifact filename, API payload, cache key,
or INFO log.

## 9. Backend database impact

Do not alter `processed.db` or attach waveform fields to `tracks`. Waveform data
is derived operational state and belongs in backend `jobs.db`.

W1 schema implemented in backend-owned `jobs.db`:

```sql
CREATE TABLE IF NOT EXISTS waveform_track_state (
    library_id           TEXT NOT NULL,
    track_id             INTEGER NOT NULL,
    status               TEXT NOT NULL,
    cache_key            TEXT,
    schema_version       INTEGER NOT NULL,
    algorithm_version    TEXT NOT NULL,
    source_device        INTEGER,
    source_inode         INTEGER,
    source_size_bytes    INTEGER,
    source_mtime_ns      INTEGER,
    source_ctime_ns      INTEGER,
    source_sha256        TEXT,
    generated_at         TEXT,
    last_error_code      TEXT,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (library_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_waveform_track_cache_key
    ON waveform_track_state(cache_key);
CREATE TABLE IF NOT EXISTS waveform_jobs (
    id                    TEXT PRIMARY KEY,
    library_id            TEXT NOT NULL,
    track_id              INTEGER NOT NULL,
    status                TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    started_at            TEXT,
    finished_at           TEXT,
    cancel_requested      INTEGER NOT NULL DEFAULT 0,
    error_code            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_waveform_one_active_track
    ON waveform_jobs(library_id, track_id)
    WHERE status IN ('queued', 'processing');
```

Allowed track-state values:

```text
not_generated, queued, processing, ready, failed, unsupported, stale, cancelled
```

W1 job states are `queued`, `processing`, `succeeded`, `failed`, and
`cancelled`. Failure codes are sanitized categories, not raw stderr or paths.
Cross-database foreign keys are intentionally absent. W2/W3 may add bounded
progress fields additively when real work exists.

## 10. API contract

### 10.1 Read waveform state/data

```text
GET /api/tracks/{track_id}/waveform?resolution=compact|player|detail
```

This endpoint may perform a DB lookup, root validation, `stat`, and cache
validation. It never hashes or decodes audio and never queues work.

Ready response (`200`):

```json
{
  "track_id": 42,
  "status": "ready",
  "schema_version": 1,
  "algorithm_version": "mono-minmax-s16-v1",
  "resolution": "player",
  "duration_ms": 247381,
  "pair_count": 1024,
  "encoding": {
    "type": "int16_min_max_interleaved",
    "scale": 32767,
    "rendered_channels": 1
  },
  "peaks": [-102, 311, -88, 290],
  "generated_at": "2026-08-05T20:00:00Z"
}
```

The peaks example is abbreviated. Send `ETag` based on cache key and resolution,
`Cache-Control: private, no-cache`, and gzip when accepted.

Queued/processing response (`202`, `Retry-After: 1`):

```json
{
  "track_id": 42,
  "status": "processing",
  "job": {
    "id": "opaque-uuid",
    "status": "processing",
    "progress_percent": 37.4
  },
  "retry_after_ms": 1000
}
```

Normal no-data response (`200`):

```json
{
  "track_id": 42,
  "status": "not_generated"
}
```

Status mapping:

| Condition | HTTP | Code / status |
| --- | ---: | --- |
| valid cache | 200 | `ready` |
| queued or processing | 202 | matching status |
| unknown track ID | 404 | `TRACK_NOT_FOUND` |
| never generated | 200 | `not_generated` |
| stale/corrupt cache | 200 | `stale` plus safe code where useful |
| unsupported codec/container | 200 | `unsupported` |
| previous extractor failure | 200 | `failed` plus sanitized code |
| cancelled | 200 | `cancelled` |
| source missing | 200 | failure state plus `WAVEFORM_SOURCE_MISSING` |
| source outside root / symlink rejection | 403 | `WAVEFORM_PATH_REJECTED` |
| policy size/duration rejection | 413 | `WAVEFORM_POLICY_REJECTED` |
| feature disabled | 503 | `WAVEFORM_DISABLED` |
| engine/cache misconfigured | 503 | `WAVEFORM_UNAVAILABLE` |

### 10.2 Explicit generation request

```text
POST /api/tracks/{track_id}/waveform/generate
Content-Type: application/json

{"force": false}
```

There is no path, engine argument, resolution, shell option, or output location
in the request. Resolution is a read concern; one generation creates all LODs.

New job (`202`):

```json
{
  "track_id": 42,
  "status": "queued",
  "job": {"id": "opaque-uuid", "status": "queued"},
  "deduplicated": false,
  "waveform_url": "/api/tracks/42/waveform?resolution=player"
}
```

Existing valid cache returns `200 ready`. An existing active job returns `202`
with the same job ID and `deduplicated: true`. Queue saturation returns `429`
with `Retry-After`. The exact future `force=true` content-verification behavior
is deferred with the non-redundant hash decision; it never overwrites source.

### 10.3 Job observation and cancellation

```text
GET    /api/waveform-jobs/{job_id}
DELETE /api/waveform-jobs/{job_id}
```

Cancellation is best-effort. A queued job becomes `cancelled`. A running job
sets the cancellation flag, terminates the process group, and deletes only its
temporary cache artifact. A completed immutable artifact is not deleted by job
cancellation.

### 10.4 Capability

Extend existing responses rather than inventing a disconnected health system:

- `GET /api/runtime/readiness`: add optional checks for waveform cache and the
  waveform engine; missing/disabled waveform support may degrade but never make
  core startup `not_ready`;
- `GET /api/settings/capabilities`: add
  `analysis.waveform_generation` with `available`, `status`, `enabled`,
  `action_state`, engine name/version, and safe messages.

Capability states established by W1:

| State | Meaning |
| --- | --- |
| `disabled` | operator disabled waveform support |
| `misconfigured` | invalid limits or unsafe cache containment |
| `cache_unavailable` | safe cache root cannot currently be written/created |
| `extractor_unavailable` | FFmpeg or ffprobe was not passively detected |
| `detected` | both tools found without execution; version is not verified |
| `ready` | reserved for a future runtime-verified extractor contract |

No capability response returns executable paths, cache paths, source paths, or
library roots.

For the primary version-1 engine, `available` requires both FFmpeg and ffprobe;
one without the other is `unavailable`. `required_tools` should therefore be
`["ffmpeg", "ffprobe"]`, while the UI can still identify FFmpeg as the engine.

## 11. Generation lifecycle

Default generation policy is **first explicit playback request for a track**.

- Opening Library does nothing.
- Auto-selecting the first Library row does nothing.
- Listing/scrolling/filtering tracks does nothing.
- Merely rendering a fallback does nothing.
- Pressing Play can request one track if its `GET` state is not ready.
- An explicit future “Generate waveform” action may request the selected track.
- Import/rescan never generates waveforms.
- No library-wide idle or batch generation is enabled by default.

Playback starts independently; waveform generation must never delay
`audio.play()`. The frontend first reads waveform state and renders fallback.
After an explicit playback intent, it may issue the idempotent `POST` in
parallel. Ready data replaces the fallback when available.

An explicit batch operation can be designed later with a previewed count,
storage estimate, cancellation, and separate confirmation. It is not part of
version 1.

State machine:

```text
not_generated/stale/failed/cancelled
        |
        | explicit POST
        v
      queued -> processing -> ready
        |           |
        +-----------+--> failed/cancelled/unsupported
```

Every transition is persisted. Future worker recovery must decide whether
`queued` jobs are requeued; interrupted `processing` jobs become `failed` with
`BACKEND_RESTARTED`, and only their contained temporary cache artifacts are
cleanup candidates.

## 12. Concurrency and deduplication

Use an in-process bounded async queue plus persistent job rows.

1. A SQLite transaction and partial unique index deduplicate active requests for
   the same `(library_id, track_id)` across tabs/clients.
2. An in-memory per-track async lock prevents duplicate work inside one backend
   process.
3. After future strong content identity is available, a per-cache-key lock can
   prevent two different track IDs with identical bytes from decoding
   simultaneously.
4. Before work, check whether the content artifact already exists and validates;
   if so, link it and finish without decoding.
5. On publish, `os.replace()` makes races harmless.

CrateIQ is currently a local single-backend process. Multi-process Uvicorn
workers are not a supported waveform-worker topology in version 1 because
in-memory locks would not cross processes. If multi-worker deployment is later
required, claim jobs atomically in SQLite (`UPDATE ... WHERE status='queued'`)
and use an OS lock file derived only from cache-key hex.

## 13. Resource model and limits

Conservative version-1 defaults:

| Resource | Default | Rule |
| --- | ---: | --- |
| concurrent jobs | 1 | configurable 1–2; values above 2 rejected |
| queued jobs | 32 | further requests return `429` |
| decoder threads | 1 | fixed FFmpeg thread/filter limit |
| process niceness (Linux) | +10 | best effort; normal playback stays responsive |
| max source size | 8 GiB | reject generation only; playback unaffected |
| max duration | 6 hours | reject generation only; playback unaffected |
| unknown-duration timeout | 10 minutes | no unbounded process |
| known-duration timeout | `clamp(120s, duration * 0.15 + 60s, 1200s)` | hard 20-minute ceiling |
| termination grace | 5 seconds | TERM process group, then KILL |
| PCM read chunk | 64 KiB | streaming only |
| stderr retained | 64 KiB tail | sanitized category; raw text not API/logged at INFO |
| ffprobe stdout | 1 MiB | reject overflow |
| detail pairs | 32,768 | hard cap |
| decompressed artifact | 4 MiB | hard cap |
| cache size | 2 GiB | LRU cleanup to 80% |

On Linux, the worker launcher should apply a 1 GiB child address-space limit
where safely supported. The peak accumulator itself must remain bounded to
small PCM chunks plus the capped peak arrays; it must not keep decoded audio.
Containers/systemd should additionally restrict the service to one or two CPUs
and a documented memory limit. Unsupported platforms retain the application
caps and concurrency of one, and report the missing hard child-memory limit as
diagnostic metadata rather than disabling core playback.

Configuration should stay small:

| Setting | Default |
| --- | --- |
| `CRATEIQ_WAVEFORMS_ENABLED` | `1` |
| `CRATEIQ_WAVEFORM_CACHE_DIR` | backend data cache path |
| `CRATEIQ_WAVEFORM_MAX_CONCURRENCY` | `1` (valid: 1–2) |
| `CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE` | `32` |
| `CRATEIQ_WAVEFORM_MAX_CACHE_BYTES` | `2147483648` (2 GiB) |

Reuse existing `FFMPEG_BIN` and `FFPROBE_BIN`. Size/duration/peak/timeout limits
are safety constants in version 1, not routine UI settings.

## 14. Failure model

Waveform state is independent of player state. No waveform failure may set the
persistent audio status to unavailable, pause audio, change the queue, or alter
review state.

| Failure | Backend behavior | Frontend behavior |
| --- | --- | --- |
| unsupported codec/container | `unsupported`, safe message | keep current visual fallback; playback may still work in browser |
| corrupt/truncated media | `EXTRACTOR_DECODE_FAILED` | fallback; playback determines its own availability |
| missing source | `source_missing` | fallback plus unobtrusive waveform-unavailable text |
| permission denied | `SOURCE_UNREADABLE` | fallback; no path displayed |
| outside-root/symlink rejection | `path_rejected`, security warning | fallback; generic rejected-source message |
| process crash/nonzero exit | `EXTRACTOR_FAILED` | fallback and optional retry |
| timeout | terminate group, `EXTRACTOR_TIMEOUT` | fallback and retry action |
| cancellation | delete temp, `cancelled` | fallback; no error toast required |
| source changes during job | discard temp, `SOURCE_CHANGED` | fallback; a later request can retry |
| cache write/permission/full | `CACHE_WRITE_FAILED`/`CACHE_FULL` | fallback; playback continues |
| corrupt cache | quarantine/delete only cache artifact, `stale` | fallback; explicit retry can regenerate |
| backend restart | active job becomes `BACKEND_RESTARTED` | fallback; retry permitted |
| policy size/duration rejection | `policy_rejected` | fallback with concise limit message |

Retries use bounded exponential backoff for repeated automatic polling, but a
user's explicit retry can bypass backoff once. Never retry unsupported or policy-
rejected sources automatically.

## 15. Frontend integration

### 15.1 Component boundaries

Add a real `TrackWaveform` presentation component and a waveform data hook.

`TrackWaveform` receives:

```text
status, peaks, durationMs, currentTime, playbackDuration,
interactive, onSeek(seconds), onGenerate(), errorCode
```

It does not import `usePersistentPlayer`, own an `<audio>` element, poll media
time, change routes, or request source URLs. A thin container in the persistent
bottom player reads `usePersistentPlayer()` and passes data/actions down. This
keeps presentation reusable in Library and Music Review.

Recommended rendering is `<canvas>` at device-pixel-aware dimensions with a
static unplayed base and a clipped played overlay. Canvas avoids thousands of
React DOM nodes. Use a `ResizeObserver`, coalesce redraws with
`requestAnimationFrame`, and downsample the selected API LOD to display width.

Night Deck treatment is preserved through the existing dark field, restrained
border, cyan/teal unplayed peaks, a brighter progress overlay, and violet only
as a truthful visual accent—not a frequency label.

### 15.2 UI states

| State | Presentation |
| --- | --- |
| loading state request | current decorative signal with a small “Checking waveform…” label |
| not generated | decorative signal; no automatic job until explicit playback |
| queued/generating | decorative signal plus restrained indeterminate/progress status |
| cached/ready | real peak canvas plus current-time overlay |
| stale | decorative signal and background regeneration only after explicit request |
| failure/unsupported | decorative signal and concise “Waveform unavailable”; playback controls remain normal |
| no selected track | existing inactive decorative signal |

When track ID changes, abort the previous HTTP request and assign a monotonically
increasing request token. A late response is ignored unless both token and track
ID still match. Route changes do not reset waveform/player state because the
persistent provider remains above the router.

The existing plain range seek control remains visible and functional even when
real peaks are ready. Do not remove it.

## 16. Seeking and accessibility

### 16.1 Seeking ownership

The persistent player provider remains authoritative. Waveform interaction maps:

```text
fraction = clamp((pointer_x - rect.left) / rect.width, 0, 1)
seconds = fraction * persistent_player.duration
persistent_player.seek(seconds)
```

Use the browser media duration for seeking. Waveform duration is display
metadata and a consistency check. If media duration is unknown/non-finite or
player status is unavailable, waveform seek is disabled.

Pointer behavior:

- pointer down captures the pointer and starts a visual scrub preview;
- dragging updates only the preview marker;
- pointer up commits one seek;
- a click/tap commits at release;
- `Escape` cancels an active keyboard/pointer preview;
- track change, pointer cancel, or component unmount cancels without seeking;
- touch uses the same pointer-event path and a minimum 44 px interaction height.

This avoids flooding the media element with seeks and reduces accidental seeks
during a drag. Live-scrub can be reconsidered later after playback measurement.

### 16.2 Accessibility contract

- The existing labeled range input remains the primary nonvisual seek control.
- The waveform interaction surface uses `role="slider"`, an accessible track
  name, `aria-valuemin`, `aria-valuemax`, `aria-valuenow`, and a formatted
  `aria-valuetext` such as “1 minute 42 seconds of 4 minutes 7 seconds”.
- Arrow Left/Right seek by 5 seconds; Shift+Arrow by 30 seconds; Home/End seek
  start/end. These call the provider action.
- Use a visible focus ring and no keyboard trap.
- Current time and total duration remain visible text; an `aria-live="polite"`
  status announces ready/failure transitions, not every time update.
- The waveform is never the only seeking interface and is not described as
  low/mid/high when it contains mono amplitude data.
- Progress is a static clip/fill. If any loading shimmer is later added,
  `prefers-reduced-motion: reduce` disables it while preserving status text.
- Color is not the only state indicator.

## 17. Security and process execution

### 17.1 Source path and file-open security

Generation starts only from an integer track ID. The service independently
repeats the same DB lookup and canonical-root validation used for playback.
Never accept a raw path in waveform API input.

On Linux, prefer this stronger open model:

1. resolve the DB path under the canonical selected root;
2. open with `O_RDONLY | O_CLOEXEC` and `O_NOFOLLOW` where supported;
3. `fstat` and require a regular file plus policy size;
4. verify the opened descriptor's canonical `/proc/self/fd/<n>` target remains
   within the selected root;
5. hash/read through that descriptor;
6. reset the descriptor offset before each consumer;
7. pass the descriptor to the child with `pass_fds` and a `/proc/self/fd/<n>`
   input path;
8. compare final file state before publish.

On platforms without `/proc`/`pass_fds`, resolve and validate immediately before
spawn, reject unsafe symlinks, and compare pre/post `stat`. Document that this is
the weaker trusted-local fallback.

### 17.2 Subprocess contract

- resolve executables to absolute regular executable files at startup;
- never discover an executable inside the selected library or waveform cache;
- fixed argument arrays only; never `shell=True`, shell strings, globbing, or
  interpolation;
- `start_new_session=True` for process-group cancellation;
- `-nostdin`, one audio stream, no video/subtitle/data, one decoder thread;
- stdout is consumed incrementally with a hard byte/time policy;
- stderr is drained concurrently and retained only to a 64 KiB tail; parse
  fixed FFmpeg `-progress` key/value records (prefer `out_time_us`) against the
  probed duration for optional progress, while treating all other lines as
  bounded diagnostic text;
- set a minimal allowlisted environment, `LC_ALL=C`, and
  `AV_LOG_FORCE_NOCOLOR=1`; explicitly remove `FFREPORT` so FFmpeg cannot create
  diagnostic report files;
- use an application-owned temporary working directory;
- TERM the process group on cancellation/timeout, wait five seconds, then KILL;
- nonzero exit, malformed output, short sample, or overflow is a failed job;
- raw stderr and command paths are DEBUG-only with explicit local opt-in and
  must not be returned by APIs.

At startup/readiness, future implementation may run only bounded non-audio
version checks (`ffmpeg -version`, `ffprobe -version`) with a three-second
timeout. Store a normalized first-line version. Never run a source probe in
readiness.

### 17.3 Cache/API security

- derive every artifact path from validated hex and fixed directories;
- reject `..`, separators, unexpected suffixes, symlink artifacts, and files
  outside the canonical cache root;
- enforce compressed/decompressed/array bounds before returning data;
- use numeric track IDs and opaque job UUIDs only;
- never return source/cache/executable paths, device/inode values, usernames,
  home directories, or mounted-volume details;
- preserve trusted-local-only deployment warnings because the app has no auth;
- do not expose waveform endpoints publicly until authentication/authorization
  exists;
- rate/queue limits protect against local oversized-file denial of service.

## 18. Privacy and observability

Waveform processing is local. It uses no cloud processing, fingerprint database,
metadata provider, telemetry service, or third-party API.

INFO logs may include:

```text
event, library_id_prefix, track_id, job_id, state, cache_hit,
engine_name, engine_version, duration_ms, elapsed_ms, pair_count,
source_size_bucket, failure_code
```

Do not log absolute/relative source paths, filenames, artist/title, cache paths,
full source hashes, usernames, mount names, raw stderr, or PCM/peaks at INFO.
DEBUG path logging is off by default and should use existing redaction.

Useful local metrics derived from logs/state:

- cache hit/miss/stale counts;
- queued/running/succeeded/failed/cancelled jobs;
- extraction and hash elapsed time;
- decoded duration and real-time factor;
- artifact/payload size;
- cache bytes and cleanup count;
- deduplicated request count;
- failure codes by engine version.

No remote telemetry is introduced.

## 19. Cache cleanup and schema evolution

Cleanup may delete only files that pass canonical containment under the
configured CrateIQ waveform cache root.

Policy:

- startup: remove `.tmp.*` older than 24 hours and mark interrupted jobs;
- lazy: when a track's source fingerprint changes, unlink the track state from
  the old artifact; do not synchronously delete a possibly shared artifact;
- daily or on size pressure: remove unreferenced artifacts least-recently used;
- size pressure: at 2 GiB, prune to 80% (1.6 GiB);
- removed tracks: state/artifact becomes eligible after 30 days without access;
- failed/cancelled job rows: retain 30 days, then delete rows only;
- old schema/algorithm directories: immediately regeneration-eligible and
  cleanup-eligible after 7 days;
- manual “Clear waveform cache” action: preview count/bytes, require explicit
  confirmation, and affect only the validated waveform cache root.

Do not scan source directories for sidecars. Do not remove any file based on a
track path. If cache-root validation fails, cleanup stops without deleting.

`schema_version` covers JSON structure/encoding. `algorithm_version` covers
decoding rate, channel mixing, peak binning, normalization, and resolution
strategy. A mismatch is a cache miss. Regenerate rather than migrate amplitude
arrays. Old artifacts remain disposable and are cleaned by the policy above.

## 20. Deployment impact

### Linux development workstation

- Use the distribution FFmpeg/ffprobe package or an explicitly configured
  executable; do not vendor binaries in the repo.
- Existing `FFMPEG_BIN`/`FFPROBE_BIN` overrides apply.
- Backend starts normally if either executable is absent.
- The waveform capability reports unavailable/misconfigured while playback,
  Library, Music Review, crates, and exports remain available.
- Cache requires application-owned writable space outside the music root.
- One nice, single-thread job avoids monopolizing a workstation shared with
  browser sessions, LedgerIQ, opsIQ, and development tools.

### Self-hosted/container deployment

- Continue to describe CrateIQ as trusted-local-only until auth exists.
- Install an architecture-compatible FFmpeg package in the image; record its
  version, configure flags/license, and security update process.
- Mount the music library read-only in the container.
- Mount `backend/data/` or the configured waveform cache as a separate writable
  persistent volume.
- Run as a non-root UID with no write permission to the music mount.
- Apply CPU/memory/PID limits and keep waveform concurrency at one initially.
- Do not make engine readiness a container liveness/startup failure.

`audiowaveform` fallback deployment requires its separate package and license;
do not silently download it at runtime.

## 21. Test strategy

### 21.1 Backend unit tests

Use temporary library/cache/DB roots and fake/mock extractor processes. Do not
invoke a real audio tool in normal unit tests.

Cover:

- canonical cache-key and library-key derivation;
- valid cache hit without hashing or extractor invocation;
- cache miss with no generation side effect on `GET`;
- stale fast fingerprint and changed content key;
- renamed/moved content reuse;
- duplicate content reuse across track IDs;
- track ID reuse across different library keys;
- source changed during generation;
- all LOD lengths, signed bounds, and peak-preserving downsampling;
- invalid schema, oversized gzip/JSON, malformed arrays, and corrupted gzip;
- atomic temp publish and cache write failure;
- status transitions and restart recovery;
- explicit request, deduplicated request, queue full, cancellation, timeout,
  nonzero extractor exit, and bounded stderr/stdout;
- unsupported codec, corrupt/truncated result, missing source, permission denied,
  invalid ID, outside-root path, traversal, symlink escape, and non-regular file;
- spaces, apostrophe, Unicode, leading-dash filename, and very long filename;
- size/duration rejection and long-duration capped pair count;
- APIs never serialize paths/fingerprints/device/inode/raw stderr;
- playback preview range/MIME/source tests remain unchanged and passing.

Extractor wrapper tests assert the exact argument list, `shell=False`,
`-nostdin`, no output path, sanitized environment, timeout, process group, and
cancellation behavior.

### 21.2 Backend integration tests

- `GET` ready/miss/stale/processing/failure contracts and HTTP codes;
- `POST` idempotency and one active job across simultaneous clients;
- job observation/cancellation;
- capability available/unavailable/disabled/misconfigured states;
- jobs DB migration is idempotent;
- cache cleanup touches only fake files under the temporary cache root;
- invalid cache override inside/above the temporary library root is rejected.

### 21.3 Frontend tests

- loading/checking state;
- real peak canvas render from a valid response;
- decorative fallback for not-generated, disabled, unsupported, failed, and
  missing-source states;
- generating progress and eventual ready replacement;
- no generation request merely from Library render/auto-selection;
- explicit playback triggers at most one generation request;
- current-time progress overlay;
- click/tap seek, drag-preview/commit, Escape cancel, duration-unavailable guard;
- Arrow/Shift+Arrow/Home/End keyboard seeking;
- existing range control remains usable;
- track change aborts/ignores stale responses;
- route changes retain current track and ready waveform;
- resize selects/downsamples the appropriate LOD without DOM bar explosion;
- waveform failure never disables play/pause/previous/next or changes audio
  error state;
- reduced-motion and accessible slider/value text.

### 21.4 Browser verification

After implementation, use an explicitly selected test library and existing
read-only source files; do not create or alter user audio. Verify:

- representative MP3 and FLAC;
- WAV and AAC/M4A if safely available;
- one safely available long file/set;
- spaces, apostrophe, parentheses, Unicode, and leading-dash filename if
  available;
- missing/unavailable and browser-unsupported source behavior;
- cached reload, route persistence, rapid switching, duplicate requests,
  cancellation, and restart recovery;
- existing playback range/seek/end/queue behavior is unchanged.

Do not mutate a source file to test staleness. Use a temporary test fixture copy
outside the user library in automated integration tests, or validate stale state
with mocked metadata.

## 22. Performance measurement plan and acceptance thresholds

Do not benchmark during architecture/design. The implementation verification
stage must measure on a named reference workstation and record engine/build,
codec, duration, source size, cold/warm filesystem state, and concurrency.

Measure:

- hashing time and extraction time separately;
- extraction real-time factor by codec/duration;
- average/peak CPU and child/backend peak RSS;
- artifact compressed/uncompressed size;
- compact/player/detail API payload sizes;
- cache-hit response latency and uncached time to first waveform;
- frontend fetch/parse/downsample/render time;
- playback responsiveness during one job;
- multiple requests, queueing, and deduplication.

Version-1 acceptance gates:

| Metric | Gate |
| --- | --- |
| 5-minute MP3/FLAC first generation | <= 15 seconds each |
| 20-minute extended mix | <= 60 seconds |
| 180-minute set | <= 9 minutes (<= 0.05x real time) |
| cache hit, local p95 | <= 250 ms to usable player LOD |
| player response, compressed | <= 32 KiB |
| detail response, compressed | <= 256 KiB |
| typical 5-minute disk artifact | <= 128 KiB |
| backend RSS increase/job | <= 96 MiB |
| child peak RSS | <= 512 MiB |
| frontend parse + first draw | <= 50 ms player LOD |
| resize redraw | <= 16 ms target, <= 50 ms hard gate |
| cache-hit extractor invocations | exactly zero |
| default simultaneously running jobs | exactly one |
| playback during generation | no pause/error/queue change attributable to waveform work |

If FFmpeg misses large-file/resource gates, measure `audiowaveform` with the
same sources and canonical output validation before changing the ADR.

## 23. Recommended phased implementation

Each phase is intended as a separate reviewable commit.

### Phase W1 — backend state and capability foundation (implemented 2026-08-05)

**Files/components:** backend config, `core/db.py`, preflight/settings capability,
new waveform schemas/state service, focused backend tests, docs.

**Scope:** constants/settings, jobs DB tables, state machine, safe library key,
cache-root validation, optional engine/cache capability. No source decoding.

**Safety gate:** pipeline DB remains read-only; cache override cannot overlap a
library root; missing feature does not block startup.

**Result:** idempotent jobs DB tables, explicit artifact/job/capability enums,
deferred nullable content hash, source-stat snapshots behind preview-strength
path validation, canonical cache overlap/cleanup guards, passive privacy-safe
FFmpeg/ffprobe detection, and optional readiness states are implemented. No
extractor, artifact, worker, automatic queue producer, or waveform endpoint was
added.

### Phase W2 — safe extraction wrapper (implemented 2026-08-06)

**Files/components:** `backend/app/core/waveform_limits.py` (policy constants
and timeout/detail-pair formulas), `backend/app/core/waveform_process.py`
(subprocess supervisor), `backend/app/models/waveform_extraction.py` (error
taxonomy, `ProbeResult`, `WaveformExtractionResult`, `CancellationToken`),
`backend/app/services/waveform_probe.py` (ffprobe wrapper + executable
resolution + unwired version-check primitive), `backend/app/services/
waveform_peaks.py` (PCM framing + bounded accumulator + downsampling),
`backend/app/services/waveform_extractor.py` (orchestrator), plus
`tests/test_waveform_peaks.py`, `tests/test_waveform_process.py`,
`tests/test_waveform_probe.py`, `tests/test_waveform_extractor.py`, and a
small `async_test` helper added to `tests/conftest.py` (avoids adding a
pytest-asyncio dependency for this handful of `async`/`await` tests).

**Scope:** read-only descriptor handling (reuses W1's `track_source_service`
unchanged), a bounded ffprobe validation contract, a fixed-argv FFmpeg stdout
decode command, a bounded doubling-merge min/max accumulator that serves both
known- and unknown-duration input from one algorithm, extrema-preserving
compact/player downsampling, cooperative cancellation, duration-aware
timeouts, and pre/post source-change detection. Strong content identity
(full-file SHA-256 / cache key) remains explicitly deferred to W3+, as W1
already decided — W2 does not read source contents for hashing anywhere in
this pipeline. No API, job-queue, or cache-artifact wiring was added.

**Safety gate:** exact command-array tests prove no shell, no output-media
path, no tag/metadata option, and bounded stdout/stderr; the accumulator has
dedicated tests proving peak storage never grows with simulated stream length.

**Complete when:** mocked ffprobe/FFmpeg metadata and PCM paths, and every
failure mode (probe/decode failure, invalid probe, unsupported codec, policy
rejection, source-changed, timeout, cancellation, launch failure), produce a
validated internal `WaveformExtractionResult` or a narrow typed
`WaveformExtractionError` — never a raw exception or a fabricated peak. Met:
92 new focused tests pass alongside the unchanged 960-test W1 baseline (1052
total).

### Phase W3 — cache and API

**Files/components:** waveform cache/state service, new waveform routes,
`main.py` router registration, backend integration tests.

**Scope:** explicit generation POST, read-only GET, job status/cancel,
deduplication, atomic publish, gzip JSON, ETag, privacy-safe errors.

**Safety gate:** GET cannot invoke hash/extractor; API contains no paths; preview-
audio regression tests pass unchanged.

**Complete when:** cache hit/miss/stale/restart/dedup/cancel contracts pass.

### Phase W4 — frontend real waveform presentation

**Files/components:** `frontend/src/api/waveforms.ts`, waveform types/hook,
`TrackWaveform.tsx`, scoped CSS, persistent bottom player, Library inspector,
Music Review tests.

**Scope:** canvas render, all loading/ready/fallback states, stale response guard,
responsive LOD, Night Deck styling. No seeking changes yet.

**Safety gate:** playback provider and audio element are unchanged; failure does
not disable playback.

**Complete when:** ready peaks render and every failure retains the current
decorative fallback honestly labeled.

### Phase W5 — waveform seeking and accessibility

**Files/components:** `TrackWaveform`, thin persistent-player container, existing
bottom-player timeline tests/CSS.

**Scope:** provider-owned seek callback, pointer preview/commit, keyboard
controls, roles/value text/focus, reduced-motion behavior, preserved range input.

**Safety gate:** no new audio element or playback clock; unknown duration cannot
seek; route/queue behavior unchanged.

**Complete when:** pointer, touch-equivalent pointer events, keyboard, screen-
reader labels, and range fallback tests pass.

### Phase W6 — lifecycle, cleanup, and resource controls

**Files/components:** worker lifecycle, startup recovery, cleanup service/manual
preview, observability, tests.

**Scope:** queue bound, one worker, process group cancellation, temp cleanup,
LRU size cleanup, safe local logs, restart recovery.

**Safety gate:** cleanup containment tests prove no source/library deletion is
possible.

**Complete when:** interruption/storage-pressure/multiple-tab scenarios are
deterministic and bounded.

### Phase W7 — controlled browser and performance verification

**Scope:** representative existing read-only test-library files, all baseline
playback behaviors, measurements from section 22, failure/recovery/restart.

**Safety gate:** explicit test-library selection; no source mutation, generated
audio, metadata write, or DJ-database write.

**Complete when:** acceptance gates pass or the engine ADR is reopened with
recorded evidence.

### Phase W8 — documentation and safety audit

**Files/components:** README, local tooling, project context, changelog, next
tasks, this ADR, safety documentation as applicable.

**Scope:** installation/readiness/cache management, optional-feature behavior,
measured limits, browser compatibility, exact non-write guarantees.

**Safety gate:** docs never imply cloud upload, source ownership, broad automatic
analysis, or support beyond measured codecs/platforms.

**Complete when:** docs and implementation agree and the waveform cache can be
deleted/rebuilt without any source or trusted metadata change.

## 24. Architecture Decision Record

### Decision

CrateIQ will use backend-local, demand-driven waveform extraction. A fixed,
read-only FFmpeg/ffprobe wrapper decodes one validated DB-backed source to
streamed mono PCM; CrateIQ computes versioned min/max peak data, stores it in a
backend-owned disposable cache, and serves bounded LOD JSON for frontend canvas
rendering. Generation requires an explicit POST and is optional.

### Context

The existing persistent browser player is proven and must remain the sole
playback/seek owner. Its current waveform is decorative. Real waveform data
requires full-file decoding, but large FLAC/MP3 files and DJ sets make browser
decoding/repetition unsafe. CrateIQ already has the source identity, root guard,
background patterns, app-owned storage, and local readiness system needed for a
central derived cache.

### Chosen architecture

- backend decode/cache; frontend render;
- FFmpeg/ffprobe read-only decoder toolchain;
- mono signed min/max peak pairs;
- gzip JSON with compact/player/adaptive-detail LODs;
- content-addressed artifacts and library-scoped track linkage;
- backend jobs DB state only;
- explicit, idempotent generation API;
- one bounded cancellable worker;
- canvas presentation consuming persistent-player seek/time state;
- decorative fallback on every unavailable/failure path.

### Alternatives considered

- **Web Audio:** rejected for full downloads, decoded-buffer memory, repeated
  client work, codec differences, and weak durable caching.
- **`audiowaveform` primary:** close second; deferred because AAC/M4A coverage and
  package availability are weaker, while CrateIQ already detects FFmpeg.
- **Python/librosa:** rejected as primary because full loading is unsafe and the
  streaming decoder surface is narrower/deprecating.
- **Rendered PNG cache:** rejected because it fixes dimensions/colors, prevents
  responsive progress/seek rendering, and duplicates variants.
- **Waveform columns on `tracks`:** rejected because peaks are disposable derived
  operational state, not trusted music metadata.

### Consequences

Positive:

- one extraction serves all routes/tabs/clients;
- source paths remain backend-only;
- large files have bounded app memory and peak payloads;
- cache is reusable, versioned, private, and fully disposable;
- playback and waveform failure domains remain separate;
- no source-side artifacts or metadata ownership conflicts.

Negative:

- FFmpeg/ffprobe are optional system dependencies;
- strong content identity remains a W2/W3 design/measurement decision;
- implementation needs a careful worker/subprocess/cache state machine;
- version-1 mono amplitude is less rich than frequency-colored DJ waveforms;
- exact codec behavior depends on the installed FFmpeg build;
- multi-process backend workers need additional cross-process claiming/locking.

### Safety guarantees

The implementation must guarantee:

- source descriptors are opened read-only;
- no output-media path is passed to the decoder;
- no source directory receives a cache, sidecar, temp file, report, or log;
- no source audio is modified, rewritten, normalized, transcoded in place,
  renamed, moved, copied into management, deleted, or quarantined;
- no tags, ReplayGain, BPM, key, Camelot, cue, marker, artwork, or comment is
  written;
- no crate order, Smart Crate rule, Music Review state, Serato/Rekordbox DB, or
  Mixed In Key value is changed by waveform activity;
- no cloud/external metadata/fingerprint service is called;
- every derived write is confined to validated CrateIQ backend DB/cache storage;
- clearing the cache affects only disposable CrateIQ artifacts;
- waveform failure never makes otherwise working playback fail.

### Deferred decisions

- measured FFmpeg versus `audiowaveform` performance on long lossless sets;
- whether gzip JSON meets detailed-view parse/payload gates or a version-2
  binary format is warranted;
- whether a future zoomed editor needs tiled data;
- whether true frequency-band color data is valuable enough to justify a new
  algorithm/schema version;
- cross-platform descriptor passing and hard child-memory limits outside Linux;
- explicit library-wide batch generation UX;
- the safest non-redundant content hash/fingerprint strategy and cache-key
  upgrade path;
- authentication/authorization before any non-local deployment.

## 25. Explicit W1 non-actions

W1 adds configuration, containment, jobs DB state, cheap validated stat
snapshots, and passive executable discovery only. It performs no engine
invocation, source content hash, source probe, audio decoding, peak generation,
artifact generation, benchmark, source media operation, tag/metadata operation,
DJ database operation, or external metadata lookup. W1 creates no cache during
ordinary startup/readiness; its initialization primitive may create only a
validated empty application cache directory when explicitly called.

## 26. Explicit W2 non-actions

W2 adds a callable internal extraction engine only: a bounded ffprobe
wrapper, a fixed-argv FFmpeg decode command builder, a subprocess supervisor,
and a bounded PCM/peak accumulator with extrema-preserving downsampling. It
adds no waveform generation API, no job-queue consumer, no background worker,
no application-startup subprocess call, and no cache artifact writer. It never
decodes, probes, or analyzes any track in the user's actual configured
library — every test uses fake process objects, synthetic in-memory PCM, or
temporary fixture files that are never opened by a real audio tool. It never
reads source file contents (no hashing, no content-based cache key); strong
content identity remains deferred to W3+ exactly as W1 already decided. It
never reads or writes `waveform_track_state` or `waveform_jobs` — the
extractor module does not import `waveform_state_service` or
`waveform_cache`. Runtime FFmpeg/ffprobe version verification exists only as
an unwired primitive (`waveform_probe.verify_extractor_versions`); readiness
stays at `detected`, deliberately not upgraded to `ready` in this phase.
