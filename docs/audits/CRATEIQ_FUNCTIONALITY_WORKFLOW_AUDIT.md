# CrateIQ Functionality, Workflow, and Dependency Audit

**Audit date:** 2026-08-05  
**Mode:** read-only product/code audit; no library, audio, metadata, or DJ-application data was changed  
**Primary question:** can CrateIQ operate as a useful standalone local-first library, crate, preview, and export app while optional tools remain isolated to the workflows that need them?

## Executive summary

CrateIQ already has a credible standalone core. A DJ can select and initialize a library root, explicitly preview and import filenames into CrateIQ's index, browse and filter tracks, review DB-only metadata proposals, build Manual Crates, generate Smart Crate suggestions from existing metadata, preview playable local audio, and stage portable, Serato, and Rekordbox exports. None of those core operations inherently requires Mixed In Key, `ffprobe`, `ffmpeg`, `keyfinder-cli`, `aubio`, `beet`, or `rmlint`.

The main product gap is not the absence of another analyzer. It is the absence of a workflow-capability layer between global readiness and individual actions. Settings detects seven optional binaries, but pages and the generic Jobs form do not consume that information to explain or disable only the affected operation. Missing optional tools therefore make the whole runtime look “degraded,” while actions such as “Analyze Library” do not say which analyzer will run, which fields will be touched, or why a tool is unavailable.

Mixed In Key (MIK) is respected in intent and partially in implementation, but is not yet a product workflow. The analyzer can read existing BPM and key/Camelot-compatible file tags and preserves existing DB/tag values. The data model does not record analysis provenance, the first-pass Settings import reads filenames only, cue tags are not imported, and no UI reports MIK or compatible-tag coverage. CrateIQ therefore cannot currently prove that a value came from MIK, distinguish MIK from another tag writer, or show the requested BPM/key/Camelot/cue coverage.

Three immediate findings should shape the next work:

1. **Add per-workflow capability and preference gating.** Core readiness must be separate from optional capabilities. Import without analysis must remain the default. BPM and key analysis must be independent opt-ins.
2. **Make analysis semantics accurate and DB-first.** The Library action queues `analyze-missing` without checking tools or showing criteria. The BPM Review UI defaults to `force=true` and `dry_run=false`, but the dispatched command does not pass `--apply`, and the `analyze-missing` implementation still selects missing values only. Conversely, an explicit CLI apply can write BPM/key tags and can move failed files to a corrupt folder. The UI, API, and CLI contracts are not aligned.
3. **Add provenance before fallback analysis expands.** The system needs a safe, explicit metadata-read/coverage pass that records `existing_db`, `compatible_tag`, `mik_confirmed` (only when genuinely knowable), or analyzer provenance without rewriting files. Fallback analysis should target only fields without trusted existing values.

There is also a security boundary that should remain visible: the backend has no authentication, yet the service helper offers LAN binding and makes LAN the first interactive choice. That is appropriate only for a deliberately trusted network and should not be the default first-run posture.

### Overall verdict

| Area | Verdict |
|---|---|
| Standalone browse/crate/export use | **Usable foundation** |
| First-time setup/import | **Implemented but fragmented** |
| Optional-tool isolation | **Partial; detection exists, workflow gating does not** |
| MIK preservation | **Partial; values are preserved but provenance/coverage is missing** |
| BPM/key opt-in workflow | **Unclear/broken product contract** |
| Review-before-apply | **Strong in dedicated review pages; inconsistent in generic Jobs/legacy pipeline surfaces** |
| DJ-app safety | **Strong staged exports; no live Serato/Rekordbox writers** |
| Non-technical DJ usability | **Core concepts are present, navigation and action naming remain module-oriented** |

## Scope and evidence

The audit inspected the current working tree, including:

- product and operating context: `README.md`, `PROJECT_CONTEXT.md`, `NEXT_TASKS.txt`, `CHANGELOG.txt`, `PRODUCT.md`, `docs/strategy/*`, and `docs/operations/*`;
- frontend routes, navigation, mounted pages, Library components, shared UI, API clients, and TypeScript types;
- FastAPI application wiring, all route modules, services, core path/readiness/config code, and schemas;
- `pipeline.py`, `db.py`, analyzer/import/cleaning/export modules, the local service helper, and demo seeding;
- backend route tests and the supported-route contract;
- the Impeccable static detector across `frontend/src` and a source-level UX review.

This is a snapshot of the code as it exists on 2026-08-05. Several strategy/product passages are stale relative to the implementation: they still describe Settings, Manual Crates, audio preview, or Serato staging as missing. Those documents should be reconciled after the workflow decisions in this audit are accepted.

## Status vocabulary

- **Implemented:** usable end-to-end for its stated, bounded scope.
- **Partial:** meaningful behavior exists, but an important product, safety, or integration layer is absent.
- **Missing:** no supported product implementation was found.
- **Unclear:** code and UI/docs disagree enough that the behavior should not be advertised as reliable.
- **Broken:** a visible action or state does not fulfill its stated contract.

## Current functionality inventory

### A. Library setup and import

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Active/configured library root | Implemented | `/settings` | `GET /api/settings`; `settings_service` | None | Yes | Reads process config | Active versus pending is understandable only after reading restart copy | Retain separate active/pending state and make it the first onboarding step |
| Pending library root | Implemented | `/settings` | validate + `PATCH /api/settings/library` | None | Yes | Ignored `.run/local/crateiq.env` only | No folder chooser; absolute path input is technical | Add guided path entry, validation summary, and restart state in wizard |
| Library initialization | Implemented | `/settings` | `POST /api/settings/library/initialize`; `library_setup_service` | None | Yes | Creates `logs/`, `exports/`, and `processed.db` | Initializes only the minimum tracks schema; not a full workflow checkpoint | Return schema/version/capability summary and keep initialization idempotent |
| Scan preview | Implemented | `/settings` | `POST /api/library/scan-preview` | None | Yes | None | Re-recurses the tree on import; preview is not an immutable confirmation snapshot | Add preview ID/fingerprint and explicit rescan messaging |
| Import | Partial | `/settings` | `POST /api/library/import` | None | Yes | CrateIQ `processed.db` only | Filename/path only; no safe tag read, provenance, added date, or preview token | Add a staged import summary and safe metadata-read phase without analysis |
| Library readiness | Implemented | global strip, Library strip, Settings | `/api/runtime/readiness` | None for required checks | Yes | None | Optional tools collapse into one global “degraded” state | Split `core_status` from workflow capabilities |
| Demo library | Implemented | service helper profile | `seed_demo_library.py` | None | Yes | `.run/demo-library` only | Fake absolute paths intentionally cannot play; demo does not demonstrate MIK provenance | Keep fake files; seed source/provenance coverage once schema exists |
| Configured-library startup | Implemented/strict | terminal helper | `crateiq-local-services.sh` | None | Yes | Process/runtime files only | Uninitialized roots cannot start; user must bootstrap through demo/current root Settings | Add explicit onboarding docs/command and keep auto-scan disabled |

### B. Library browsing and review

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Library workspace | Implemented | `/` | `/api/tracks*`, `/api/library/overview` | None | Yes | None | Empty library says “No tracks match” instead of guiding setup/import | Link zero-track state to Settings → Scan preview |
| Search/filter/sort/paging | Implemented | `/` | `track_service` | None | Yes | UI state only | Dense desktop toolbar becomes crowded; no saved filters | Preserve core behavior; improve mobile ordering and saved views later |
| Track inspector and harmonic matches | Implemented | `/` | `/api/tracks/{id}`, `/compatible` | Existing key metadata only | Yes | None | Missing key state lacks direct optional-analysis/setup route | Link to MIK coverage or Analyze Missing Key when available |
| Quality dashboard | Implemented | `/quality` | `/api/library/quality` | None for DB summary | Yes | None | Recommended actions are separate from capability/setup state | Route recommendations to exact safe workflow |
| Issues view | Implemented | `/issues` | `/api/tracks/issues`, track APIs | None | Yes | UI filters only | Shares legacy CrateMind shell and has a stale “playback not implemented” inspector | Use the reusable preview player and first-party empty guidance |
| Folders | Implemented | `/folders` | `/api/library/folders` | None | Yes | None | Useful secondary view but promoted as a top-level workflow | Move beneath Library or Advanced |
| Audit report | Partial | `/audit` | `/api/audit/latest` | Depends on prior CLI audit run; quality run may need `ffprobe` | Yes to view | None | Cannot launch/understand the producing workflow from the page | Show source run, requirements, and safe launch path |

### C. Analysis and enrichment

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| BPM anomaly classification | Implemented | `/bpm-review` | `/api/analysis/bpm-check`, `bpm_analysis` | None | Yes | Backend anomaly DB | Label “Detect and fix” conflates a DB heuristic scan with audio analysis | Rename first action “Find BPM anomalies” and explain it is DB-only |
| Analyze missing BPM | Partial | Library/Jobs/BPM Review | `analyze-missing`, `modules/analyzer.py` | `aubio` preferred; bundled `librosa` fallback | Optional workflow | Dry-run by default; explicit apply writes DB and tags and may move corrupt files | Separate BPM-only request, capability check, DB-only default, no quarantine in UI path |
| Analyze missing key/Camelot | Partial | Library/Jobs/BPM Review | same combined command | `keyfinder-cli`; `ffmpeg` only for decode retry | Optional workflow | Same as above | Cannot choose key independently from BPM; no precise preflight result | Add key-only job with explicit eligibility count and tool gate |
| Force reanalysis | Broken/unclear | `/bpm-review` | `POST /api/analysis/reanalyze` | Analyzer tools | No | Current job remains dry-run unless `--apply`; selector remains missing-only | UI/API claim force overwrite while pipeline path ignores that contract | Remove force default; resolve semantics before exposing again |
| Existing tag preservation | Partial | Not visible | `modules/analyzer._read_existing_analysis` | Mutagen/Python dependency | Yes | Promotes missing DB values only | Reads BPM/key tags but records no provenance and no cue coverage | Move to explicit metadata-read service with provenance |
| MIK detection/import/coverage | Missing as product workflow | Settings only has locked policy text | No dedicated route/service | MIK is an optional input source, not an executable dependency | Yes without it | Should write CrateIQ DB only | Cannot identify MIK source, report coverage, or import cues | Implement coverage/import preview before expanding fallback analysis |
| `ffprobe`/`ffmpeg` probing | Partial | Quality/Jobs indirectly | `audit-quality`, conversion, cue/key retry modules | `ffprobe` and/or `ffmpeg` | Optional | Audit can write DB; conversion can create/move artifacts | No workflow-specific gate or plain-language capability state | Add dedicated capability cards and hide conversion from basic Jobs |
| Beets workflow | Partial/legacy | Generic Jobs or CLI | organizer/pipeline | `beet` for the Beets path | Optional | Beets organizer can move/rename/tag during explicit pipeline apply | “Enrichment” UI is not the Beets workflow; fallback organizer blurs naming | Make Beets a clearly advanced, explicit import/enrichment operation |
| Online enrichment review | Implemented for existing queue | `/enrichment` | `/api/enrichment/*` | Existing queue/input providers generated elsewhere | Yes to review | Approved apply updates CrateIQ tracks DB | No in-app queue generation/source explanation | Add provenance/source and a guided “create proposals” step later |
| Skip analysis | Partial | CLI `--skip-analysis`; Settings policy copy | main pipeline | None | Yes | Depends on other selected stages | Import already skips analysis, but no persisted frontend preference/control | Add explicit default-off BPM/key preferences and onboarding choice |

### D. Cleaning and metadata

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Filename parsing | Partial | Import result, issues/repair | first-pass `_filename_metadata`; richer pipeline parsers | None | Yes | Import writes parsed DB fields | First-pass parser only recognizes `Artist - Title`; no previewed confidence detail | Show proposed artist/title/confidence before import |
| Deterministic metadata repair | Implemented | `/metadata-repair` | dedicated queue/review/apply APIs | None | Yes | Approved apply changes artist/title in CrateIQ DB only | Queue generation and empty-state origin are not part of onboarding | Link issue counts to generate/review flow |
| Metadata sanitation | Implemented | `/metadata-sanitation` | dedicated queue/review/apply APIs | None | Yes | Approved apply changes artist/title in CrateIQ DB only | Naming distinction from “repair” is subtle to DJs | Combine under “Fix Metadata” with Repair/Cleanup tabs |
| Generic metadata clean/tag normalize | Partial/advanced | Generic Jobs/CLI | pipeline modules | None or Mutagen | Optional | Explicit apply can write audio tags | Raw CLI names are exposed without impact-specific confirmation | Remove write-capable commands from basic generic launcher |
| Genre taxonomy | Missing | Genre filter only | No editable taxonomy service | None | Yes | Future DB-only proposals | Existing genres are free-form and Smart Crates need exact text matches | Add canonical taxonomy plus reviewable mapping |
| Duplicate detection | Partial/CLI | Generic Jobs only | `dedupe`, `rmlint` | `rmlint` required | Optional | Dry-run reports; apply quarantines/moves duplicates | No dedicated UI, capability gate, or group review | Build preview-only duplicate groups UI before any quarantine action |
| Issue resolution | Partial | Issues, repair, sanitation, BPM review | multiple stores/routes | None for review | Yes | Mostly review DBs; some applies update tracks DB | Fragmented queues and status models | Create one Fix & Review entry point with typed queues |
| Review-before-apply | Implemented in dedicated queues | enrichment/repair/sanitation | dry-run/apply pairs | None | Yes | Explicit DB apply | Generic Jobs bypasses product-level review semantics | Restrict Jobs to advanced/admin and require risk-specific forms |
| Audio tag write-back | Implemented only in CLI paths | Generic Jobs/CLI | analyzer/tagger/clean modules | Mutagen, sometimes analyzers | Not core | Audio tags | Safety policy says no automatic writes, but product does not distinguish DB save from tag write clearly | Keep tag write-back deferred behind a separate audited workflow |

### E. Crates and playlists

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Manual Crates CRUD | Implemented | `/crates` | `/api/crates*`, `crate_service` | None | Yes | `manual_crates.db` only | No bulk-add or duplicate-resolution UX; otherwise solid | Treat as core product surface |
| Ordered crate membership | Implemented | `/crates` | add/remove/reorder routes | None | Yes | `manual_crates.db` only | Up/down controls are safe but slow for long crates | Add move-to-position/keyboard support later |
| Smart Crate preview | Implemented | `/smart-crates` | presets/preview | Existing metadata only | Yes | None | Energy/vibe/date-added unavailable; exact genre input is brittle | Keep deterministic; add trusted fields only when schema supports them |
| Smart-to-Manual save | Implemented | `/smart-crates` | `POST /api/smart-crates/save` | None | Yes | Normal Manual Crate only | No saved criteria/rerun model, intentionally snapshot-only | Preserve snapshot semantics until smart rule persistence is designed |
| Generated Set Builder | Implemented/advanced | `/set-builder` | `/api/playlists/set-builder`, pipeline set builder | Existing BPM/key/energy metadata | Optional | Job/output files and set tables | Called “Set Builder” beside Smart/Manual Crates without explaining differences | Rename/group as “Generated Sets” and show missing-metadata eligibility |

### F. Audio preview/player

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Safe audio endpoint | Implemented | Indirect | `GET /api/tracks/{id}/preview-audio` | Browser-decodable source | Yes | None | MIME/native support varies; no transcode fallback by design | Keep native-only for core preview |
| Range requests/seeking | Implemented | Player seek control | track route byte-range parser | Browser-native playback | Yes | None | Single-range only, sufficient for player | Retain tests and avoid arbitrary path endpoints |
| Reusable player | Implemented | Library, Manual Crates, Smart Crates | `AudioPreviewPlayer` | Browser-native playback | Yes | None | Player is per-page rather than persistent; no keyboard shortcuts | Add persistent queue/player later, not a DJ deck |
| Unavailable states | Implemented | Player StatusStrip | HTTP 400/404/416 handling | None | Yes | None | Cannot distinguish missing file from unsupported codec in copy | Add safe reason codes without exposing paths |
| Review-page integration | Partial | Main Library/crate pages only | Existing endpoint | None | Yes | None | Issues/Enrichment CrateMind inspector still says playback is not implemented | Reuse the player in review surfaces |

### G. Exports

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| CSV/JSON/M3U/M3U8 Manual Crate exports | Implemented | `/exports` | crate export service/routes | None | Yes | New files under export root | Export action is enabled before preview; preview-first is copy, not enforced state | Require a current preview fingerprint before write |
| Path modes | Implemented | `/exports`, Settings default | export services | None | Yes | Export artifact only | Absolute mode can expose local paths by explicit choice, as designed | Keep filename default and show portability trade-off |
| Serato staged handoff | Implemented within stated scope | `/exports` | Serato preview/write service | None | Yes | M3U8 + manifest under exports | Not an exact binary `.crate`; no live write | Keep explicit staged/importable wording |
| Rekordbox XML | Implemented within stated scope | `/exports` | Rekordbox XML service | None | Yes | New XML under exports | Filename/relative locations may need manual path mapping | Add post-generation validation/import guidance |
| Legacy full-library export | Partial/advanced | top of `/exports` | validate/run background routes | Existing metadata; optional recovery can invoke analyzers | Optional | Export artifacts; recovery can update analysis data in legacy pipeline | Separate from safe Manual Crate exports and gate recovery tools explicitly |
| Export validation | Implemented for legacy full-library flow | `/exports` | `export_validation` | None for validation | Yes | None | Treats missing BPM/key as export-invalid even though portable exports do not require them | Scope validation rules by destination/profile |

### H. Settings and diagnostics

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Settings page | Implemented | `/settings` | settings routes/service | None | Yes | Safe preference/local env only | Long single page mixes onboarding, tools, policies, diagnostics | Make Setup the first section and move diagnostics to Advanced accordion/tab |
| Tool detection | Implemented | `/settings` | preflight | Executable presence only | Yes | None | Detection is not a capability contract and does not validate tool invocation/version | Add capability API with required/alternative inputs and action IDs |
| MIK status/coverage | Missing | Locked policy text only | None | Optional MIK metadata source | Yes without it | None today | “Authoritative” is asserted but not measurable | Add source-aware coverage section |
| Safety policies | Implemented as read-only copy | `/settings` | settings response | None | Yes | None | One claim (“files and tags are never automatically modified”) coexists with generic explicit CLI apply paths | Clarify “never automatic” versus explicit advanced writes and DB-only defaults |
| Default export path mode | Implemented | `/settings` | `PATCH /api/settings` | None | Yes | ignored app settings JSON | Only preference currently supported | Keep it; add analysis preferences separately |
| Readiness | Implemented | global, Library, Settings | runtime/preflight | None for core | Yes | None | Optional warnings make overall runtime “degraded” | Return core status plus capabilities, not one aggregate severity |
| Analysis preferences | Missing | None | None | N/A | Needed for safe opt-in | Future settings JSON | Cannot choose BPM/key independently or persist opt-out | Implement next |

### I. Jobs and background workflows

| Feature | Status | UI exposure | Backend/service | External requirement | Standalone? | Writes | Main gap | Recommended next action |
|---|---|---|---|---|---|---|---|---|
| Job submit/list/detail/logs | Implemented | `/jobs` | jobs routes/service/runner | Per command | Core app does not need it | Jobs DB/logs; command-dependent | Generic raw command list is not understandable or risk-ranked | Make Jobs primarily a monitor; launch workflows from dedicated pages |
| Allowlisted subprocess execution | Implemented technically | `/jobs` and workflow pages | `toolkit_runner` | Per command | Optional | Command-dependent | Allowlist prevents injection, not inappropriate workflow launch | Add command capability and risk metadata |
| Progress | Partial | Jobs and some workflow panels | polling/job rows | None | Yes | None | Percent/message is populated mainly for sync; most jobs are opaque | Standardize staged progress events |
| Cancel | Implemented | `/jobs`, some workflow pages | SIGTERM registry | None | Yes | Process state | Cancellation recovery semantics vary by command | Document atomicity/partial-output behavior per job |
| Retry | Missing | None | None | Per command | Optional | Future | User must reconstruct raw command | Add safe retry only for idempotent/dry-run jobs |
| Analysis job orchestration | Missing as product workflow | Library/BPM Review use generic job | combined `analyze-missing` | Analyzer tools | Optional | See analysis row | Add BPM-only/key-only eligibility, preview, confirmation, and provenance |

### J. Safety model

| Safety behavior | Status | Current evidence | Main gap | Recommended action |
|---|---|---|---|---|
| No file mutation by default | Mostly implemented | import/crates/player/exports are read/index-only; many CLI commands default dry-run | Generic Jobs exposes write-capable command names and flags without product-specific context | Keep basic UI DB/artifact-only and isolate Advanced operations |
| No live DJ database mutation | Implemented | staged Serato and Rekordbox exports only | Legacy docs can imply direct deployment workflows | Use “staged export” consistently and archive stale guidance |
| MIK authoritative | Partial | analyzer preserves existing DB/tag BPM/key; cue suggestion disabled in main pipeline by default | No provenance/coverage; compatible tags are labeled MIK without proof | Add explicit source taxonomy and never infer MIK from value shape alone |
| Preview-before-apply | Strong but inconsistent | dedicated queues and exports expose previews | portable export write not technically dependent on preview; Jobs can launch raw commands | Enforce preview fingerprints for meaningful applies/writes |
| Path safety | Strong in new APIs | selected-root containment, normalized export destinations, redacted Settings paths | Older pipeline commands have broader path/config semantics | Keep legacy operations Advanced and test each path boundary |
| Missing-data-only analysis | Partial/implemented in analyzer selection | analyzer and `analyze_missing` select missing fields | Reanalysis API/UI claims force behavior that implementation does not consistently support | Remove ambiguous force action and formalize eligibility rules |
| External workflows opt-in | Partial | tools only warn; import does not analyze | no persisted controls or per-action gating | Add workflow capability/preference model |
| Trusted-local security | Partial/high risk | no auth; CORS scoped; helper supports localhost and LAN | interactive helper offers LAN first/default without auth | Default to localhost; require explicit warning/confirmation for LAN |

## External-tool and input-source dependency matrix

The live readiness endpoint reported all seven executable checks as available on the audit host. That is an environment fact, not a product dependency: the application must continue to behave correctly when any or all optional binaries are absent.

| Tool/input | Workflow | Required or optional | Current missing behavior | Settings presentation | Action to disable | User-facing explanation | Fallback | Skippable? | Detected today? |
|---|---|---|---|---|---|---|---|---|---|
| Mixed In Key | Existing BPM/key/Camelot/cue metadata source | Optional preferred input | App still works; no coverage/import workflow exists | “Optional metadata source,” coverage by field, provenance confidence, never a binary pass/fail | Only an explicit MIK import action when no supported source is supplied | “CrateIQ works without MIK. When compatible MIK metadata is present, it is preserved and preferred.” | Existing compatible tags or leave fields missing; fallback analysis only by opt-in | Yes | **No** dedicated detection; analyzer reads compatible BPM/key tags only |
| `ffprobe` | Quality/codec/duration/bitrate probing and conversion validation | Required for those probing workflows only | Runtime warns; affected Jobs can still be submitted and fail later | Capability card: available/version/source/purpose | “Probe audio quality” and conversion validation | “Install/configure ffprobe to inspect codecs and quality. Browsing, crates, playback, and exports remain available.” | Existing DB metadata for non-probing views; no fabricated probe result | Yes | **Yes**, executable presence only |
| `ffmpeg` | Audio conversion, full cue energy decode, keyfinder decode retry | Required for conversion/full decode; optional retry for key analysis | Runtime warns; no action-level block | Separate capability from ffprobe | Convert audio; full cue analysis; only retry-dependent key cases | “ffmpeg is needed only to decode/convert audio for this advanced workflow.” | Native source decode where keyfinder/browser handles it; no conversion fallback | Yes | **Yes**, executable presence only |
| `keyfinder-cli` | Missing key/Camelot fallback | Required for supported fallback key analysis | Runtime warns; combined Analyze action is still available | “Key analysis” capability with eligible missing-key count | “Analyze Missing Key/Camelot” | “Keyfinder is not needed to import or use existing key data. Install it only to analyze tracks missing a trusted key.” | None currently; preserve missing value | Yes | **Yes**, executable presence only |
| `aubio` | Missing BPM fallback | Preferred external BPM analyzer | Analyzer falls back to bundled Python `librosa`; readiness still warns | Show “aubio preferred; librosa fallback available/unavailable” as one BPM capability | Disable only if neither aubio nor supported librosa is available | “BPM analysis is optional. Aubio is preferred; this install can/cannot use the local librosa fallback.” | `librosa` is declared in Python requirements | Yes | **Yes** for executable; **No** capability check for librosa |
| `beet` | Explicit Beets import/enrichment/organizer path | Required for the Beets-labelled workflow, not core import | Legacy organizer falls back to Python, but UI has no explicit Beets action | Advanced tool card | “Run Beets enrichment/import” | “Beets is optional. Filename import, manual cleanup, crates, and exports do not need it.” | Built-in filename/tag organizer is a different workflow and must be labeled as such | Yes | **Yes**, executable presence only |
| `rmlint` | Byte-identical duplicate detection | Required for current duplicate engine | Command logs an error/continues; no dedicated UI gate | “Duplicate detection” capability | “Scan for duplicates” | “rmlint is needed only for duplicate scanning. No files are removed automatically.” | None currently; checksum engine could be future work but should not be invented silently | Yes | **Yes**, executable presence only |
| Browser-native audio playback | In-app preview | Required only to hear a file | Clean unavailable state; core app continues | Browser capability/help state, not server readiness | Play control for unsupported/missing file only | “This browser cannot play this file directly. Crates and metadata remain available.” | No transcode in core preview; optional ffmpeg preview proxy could be later | Yes | **No** preflight; detected at playback time |

### Capability contract recommendation

Add a backend-owned capability response rather than making each page interpret raw binaries. A suitable shape is:

```json
{
  "core_status": "ready",
  "capabilities": {
    "import_tracks": {"available": true, "requires": []},
    "preview_audio": {"available": true, "requires": ["browser-native playback"]},
    "analyze_missing_bpm": {
      "available": true,
      "requires_any": ["aubio", "librosa"],
      "selected_provider": "aubio"
    },
    "analyze_missing_key": {
      "available": true,
      "requires_all": ["keyfinder-cli"]
    },
    "duplicate_scan": {"available": true, "requires_all": ["rmlint"]},
    "audio_quality_probe": {"available": true, "requires_all": ["ffprobe"]},
    "beets_import": {"available": true, "requires_all": ["beet"]}
  }
}
```

`core_status` should not become degraded solely because an optional capability is unavailable. The capability row should carry setup guidance, safe purpose, whether it can be skipped, and an action identifier. Frontend buttons should consume those action capabilities directly.

## Standalone usability audit

### Works without any optional executable

- configure, validate, initialize, and explicitly scan a library root;
- import paths and filename-derived metadata into CrateIQ's own SQLite index;
- browse, search, filter, sort, inspect, and review existing metadata;
- run DB-only issue classification and deterministic repair/sanitation review;
- create/edit/delete/reorder Manual Crates;
- generate Smart Crate suggestions from metadata that already exists;
- use native browser audio preview when the file format is supported;
- export Manual Crates as CSV, JSON, M3U, M3U8, staged Serato M3U8/manifest, and staged Rekordbox XML;
- inspect Settings and core runtime diagnostics.

### Currently feels dependent even when it is not

- Any missing optional binary changes the global readiness result to `degraded`, producing a warning across most routes.
- The Library's primary right-side action is “Analyze Library,” even though analysis is optional and import/browse/crate/export are valid next steps.
- Empty states usually describe an empty table rather than directing a new user to Settings → Initialize → Scan → Import.
- Settings leads with diagnostics and path terminology rather than a persistent setup checklist.
- The sidebar gives equal weight to core workflows, maintenance queues, low-level jobs, SSD sync, and reconciliation.

### Standalone conclusion

The architecture already supports standalone use, but the information architecture does not communicate it. The next release should make “Import without analysis” the visible default path, report optional capabilities separately, and replace “Analyze Library” with explicit BPM/key actions located after import—not as the Library's primary call to action.

## Mixed In Key workflow audit

### What exists

- `modules/analyzer.py` reads existing BPM and key-compatible tags from MP3, FLAC, and M4A containers when the DB field is missing.
- Existing non-empty DB BPM/key values take precedence.
- Existing compatible file-tag values are promoted to missing DB fields.
- Fresh fallback analysis only runs for missing BPM/key values in the normal analyzer path.
- Main-pipeline cue suggestion is disabled by default unless explicitly forced.
- Settings and documentation state that MIK is authoritative for BPM, key, and cues.

### What does not exist

- no `metadata_source`, `bpm_source`, `key_source`, or source-confidence field for tracks;
- no way to prove that a compatible `TBPM`, `TKEY`, `INITIALKEY`, `tmpo`, or `©key` value came from Mixed In Key rather than another tagger;
- no MIK application/database/file integration;
- no dedicated MIK metadata import endpoint or preview;
- no cue-tag reader or MIK cue coverage import;
- no UI coverage totals for BPM, key, Camelot, or cues;
- no per-track source badge;
- no explicit user declaration “I use MIK” / “I do not use MIK”;
- no conflict queue for DB value versus file-tag value;
- no source-aware rule preventing a future analyzer from replacing a trusted value beyond the current non-empty check.

### Recommended source model

Do not label every existing compatible tag as MIK. Use an additive provenance model:

- `existing_db_unknown` — value existed before provenance tracking;
- `compatible_file_tag` — read from a supported BPM/key tag, producer unknown;
- `mik_confirmed` — only when a reliable MIK-specific indicator or explicit user-confirmed import source exists;
- `crateiq_aubio`, `crateiq_librosa`, `crateiq_keyfinder` — generated locally by a named analyzer;
- `manual` — user-entered/reviewed value;
- `unknown` — no defensible source.

Store observed values and provenance in an additive table or schema migration with `observed_at`, source, confidence, and conflict status. Keep the canonical `tracks.bpm` and `tracks.key_camelot` fields for compatibility. Cue observations should remain distinct from CrateIQ-generated cue suggestions.

### Coverage UX

Settings or a dedicated Metadata Sources panel should show:

- total imported tracks;
- BPM present / trusted / missing;
- musical key present / Camelot present / trusted / missing;
- cue data present / trusted / missing;
- compatible tags found, MIK-confirmed values, CrateIQ-analyzed values, and unknown-source values separately;
- conflicts requiring review;
- a clear “I do not use Mixed In Key” choice that hides MIK-specific setup without disabling any core workflow.

The explicit “Import existing analysis metadata” workflow must be preview-first and DB-only. It may read tags, must not write tags, and must never run aubio/keyfinder/ffmpeg automatically.

## Optional BPM/key analysis recommendation

### Required controls

| Control | Default | Lock/state | Recommended home |
|---|---|---|---|
| Import without analysis | On | Always available | Import Wizard |
| Analyze BPM | Off | Editable opt-in | Import completion and Analyze Missing page |
| Analyze key/Camelot | Off | Editable opt-in | Import completion and Analyze Missing page |
| Use MIK metadata when present | On | Locked, with “skip MIK-specific workflow” option | Settings + import metadata step |
| Preserve existing BPM/key/cues | On | Locked | Settings safety policies and confirmation summary |
| Missing-data-only analysis | On | Locked | Settings and analysis confirmation |
| Use external tools if available | Off globally or explicit per action | Editable; never means auto-run | Settings analysis preferences |
| Allow clean/enrich without BPM/key | On | Locked | Import and Fix & Review |
| BPM fallback only when trusted BPM missing | On | Locked | Analysis job request |
| Key fallback only when trusted key missing | On | Locked | Analysis job request |

### Behavior

1. Import paths and filename metadata without analysis.
2. Optionally read existing file metadata into a preview, preserving provenance and conflicts.
3. Explicitly import accepted existing metadata to CrateIQ DB only.
4. Show eligible counts: missing trusted BPM, missing trusted key/Camelot, and conflicts.
5. Let the user run BPM and key analysis independently.
6. Require `aubio` or supported `librosa` capability for BPM; require `keyfinder-cli` for key. Use `ffmpeg` only when an explicitly documented decoder fallback is selected.
7. Default analysis output to CrateIQ DB plus run provenance only. Treat audio-tag write-back as a separate future review/apply workflow.
8. Do not quarantine/move a file from the in-app analysis workflow. Report unreadable files to a review queue.
9. Never overwrite trusted DB/tag/MIK values. A user-disputed value should go through a dedicated correction workflow, not a “force” checkbox.

### Placement

- **Settings:** defaults, locked safety policies, capabilities, and MIK usage preference.
- **Import Wizard:** “Import without analysis” and existing-metadata read/coverage preview. Do not expose analyzer toggles until after the import summary.
- **Analyze Missing page/modal:** eligible counts, BPM/key independent choices, provider availability, dry-run/DB-write summary, and explicit start.
- **Jobs:** monitoring, logs, cancel, and retry for safe job types; not the primary launcher.
- **Library home:** coverage cards and contextual links, not a vague one-click Analyze button.

## Recommended first-time workflow

```text
Choose library root
  → Validate (no scan)
  → Save pending root / restart if needed
  → Initialize CrateIQ index (no scan)
  → Scan preview (read-only)
  → Confirm import (CrateIQ DB only)
  → Review import summary and existing-metadata coverage
  → Choose a next action
       ├─ Browse only
       ├─ Build Manual or Smart Crates
       ├─ Export a saved crate
       ├─ Fix filenames/metadata in review queues
       ├─ Review/import MIK-compatible metadata
       ├─ Analyze missing BPM (aubio or supported librosa)
       ├─ Analyze missing key/Camelot (keyfinder-cli)
       ├─ Run Beets enrichment (beet)
       ├─ Detect duplicates (rmlint)
       └─ Probe audio quality (ffprobe/ffmpeg)
```

### Wizard state rules

- Never scan on startup, root validation, save, restart, or initialization.
- Import must remain enabled without MIK or analyzers.
- “Import previewed tracks” should refer to a specific preview snapshot or disclose that the directory will be rescanned.
- If no supported audio is found, show the extensions checked and a safe “Scan again” action.
- After import, show “Your tracks are ready to browse” before suggesting optional cleanup/analysis.
- Each optional action must state its input, tool, output, and mutation boundary before it can start.

## UI and sidebar audit

### Route-by-route review

| Route | Current purpose | Sidebar decision | Simpler label/group | Empty/setup guidance | Imported tracks? | Optional tool state | First-time vs advanced |
|---|---|---|---|---|---|---|---|
| `/` | Library browse, filters, inspector, player, compatible tracks | Keep, first | **Library** | Link zero-track state to Library Setup | Yes | Replace generic Analyze with explicit capability links | First-time/core |
| `/settings` | Root setup, import, preferences, tools, safety, diagnostics | Keep utility; surface in onboarding | **Settings & Setup** during onboarding, Settings later | Already contains setup; use checklist/progress | No | Capability cards and per-workflow guidance | First-time/core utility |
| `/crates` | Manual ordered playlists | Keep | **Crates** (manual understood in page copy) | If library empty, link Import; if no crates, create | Tracks needed to add | None | Core |
| `/smart-crates` | Metadata-based suggestion preview/save | Keep near Crates | **Smart Crates** | Link import and explain missing metadata | Yes | No external tool; show missing-field coverage | Core after import |
| `/exports` | Portable + staged Serato/Rekordbox + legacy full-library export | Keep | **Export Crates**; move legacy export under Advanced | Link to create a crate | Crates/tracks | Recovery analysis must gate aubio/keyfinder; base formats need none | Core plus Advanced subsection |
| `/quality` | DB quality overview/action links | Keep under Review | **Library Health** | Link setup if zero | Yes | Probing actions gate ffprobe; summary itself does not | Core after import |
| `/issues` | Track issue filter/review | Keep under Review | **Review Issues** | Link setup/import and explain issue generation | Yes | None for DB issues | Core after import |
| `/enrichment` | Review existing enrichment proposals | Move under Fix & Review/Advanced | **Metadata Matches** | Explain how proposals are generated | Yes plus queue | Gate only proposal source workflow; review needs none | Advanced |
| `/metadata-repair` | Deterministic artist/title repair queue | Combine under Fix Metadata | **Fix Metadata** → Repair tab | Generate/link queue when empty | Yes | None | Core review |
| `/metadata-sanitation` | Deterministic cleanup queue | Combine under Fix Metadata | **Fix Metadata** → Cleanup tab | Explain difference from repair | Yes | None | Core review |
| `/bpm-review` | DB anomaly review + attempted reanalysis launch | Move under Analyze/Advanced | **BPM Check** | Link MIK coverage and missing-BPM eligibility | Yes | DB check: none; audio analysis: aubio/librosa | Advanced optional |
| `/jobs` | Raw allowlisted command launcher and monitor | Move under Advanced; monitor-first | **Activity** or **Jobs** | Explain jobs start from workflows | No to monitor | Show tool/impact badges; do not offer unavailable command | Advanced |
| `/folders` | Folder aggregates | Nest under Library | **Folders** | Link setup/import | Yes | None | Secondary core |
| `/audit` | Latest generated audit artifact | Move under Advanced/Diagnostics | **Audit Report** | Explain no report and how to safely generate one | Depends on prior run | Quality audit may require ffprobe | Advanced |
| `/set-builder` | Algorithmic energy/harmonic set generation | Keep near Crates but label distinction | **Generated Sets** | Show eligibility/missing BPM-key guidance | Yes | Existing metadata; no analyzer should auto-run | Optional after import |
| `/sync` | rsync working library to SSD | Move under Publish/Advanced | **SSD Transfer** | Require configured destination and preview | Files/paths | Gate `rsync` | Advanced/high-risk |
| `/reconciliation` | Read-only path-reconciliation ledger/plan validation | Advanced only | **Path Reconciliation** | Explain why/when records exist | Usually | None for ledger | Advanced/maintenance |

### Recommended sidebar information architecture

```text
Library
  Library
  Library Health
  Review Issues

Crates
  Crates
  Smart Crates
  Generated Sets

Publish
  Export Crates

Advanced
  Fix Metadata
  Metadata Matches
  BPM Check / Analyze Missing
  Folders
  Jobs / Activity
  Audit Report
  SSD Transfer
  Path Reconciliation

Settings
```

The first-run shell should temporarily promote **Settings & Setup** and show one next step. After import, Library becomes the default home. Optional tools should never determine whether the core navigation renders.

### Impeccable UI review

Static Impeccable detection across `frontend/src` reported no blocking findings and four existing layout-animation warnings in `index.css` (three width transitions and one height transition). A live URL scan was attempted at desktop width but could not run because Puppeteer is not installed; no dependency was added for this documentation audit.

| Dimension | Score (0–4) | Audit notes |
|---|---:|---|
| Accessibility | 2 | Good labels exist in the player, Camelot wheel, and many controls. Global `:focus-visible` treatment and reduced-motion handling were not found; several icon/small controls rely on `title` or have compact hit targets; modal focus behavior needs a dedicated pass. |
| Performance | 3 | Library tables are paged/virtualized and search is debounced. Route components are eagerly imported, the sidebar makes several independent requests on mount, and layout-property transitions can cause avoidable reflow. |
| Responsive behavior | 3 | The app has meaningful breakpoints, stacked crate/settings/export grids, and a scrollable sticky sidebar. Dense operational tables and the 15-item navigation remain difficult at narrow widths. |
| Theming/consistency | 3 | Shared dark tokens and StatusStrip/KpiCard/EmptyState/Badge primitives are established. Some legacy CrateMind/BPM/Jobs treatments and inline values remain inconsistent. |
| Implementation integrity | 2 | The UI is product-specific and real-data-driven, but labels expose implementation modules, Settings and Export are very long surfaces, and old inspector/docs copy contradicts shipped player/crate/settings functionality. |
| **Audit health** | **13/20** | **Functional and coherent foundation; workflow clarity and accessibility need focused product work before more feature breadth.** |

## Missing or incomplete functionality

Ranked by impact:

1. **Workflow capability registry and gating** for optional tools/input sources.
2. **Persisted optional-analysis preferences** with BPM/key independent, default-off controls.
3. **Accurate analysis job contract**: eligibility preview, DB-only default, tool selection, no implicit file move/tag write.
4. **MIK/compatible-tag provenance and coverage** for BPM, key, Camelot, and cues.
5. **Polished import wizard** with a stable preview/confirmation boundary and safe metadata-read phase.
6. **Onboarding/empty-library dashboard** that offers browse-only before advanced analysis.
7. **Unified Fix & Review center** for repair, sanitation, enrichment, BPM anomalies, and conflicts.
8. **Dedicated duplicate detection UI** gated on `rmlint`, with group review and no automatic removal.
9. **Genre taxonomy and mapping review** so Smart Crates do not depend on exact free-form strings.
10. **Analysis job progress/cancel/retry model** with per-stage and per-track summaries.
11. **Metadata source conflict review** when DB and compatible file tags disagree.
12. **Safe tag write-back design**, if ever approved, separate from DB analysis/import and with backup/diff/apply confirmation.
13. **Export preview enforcement and destination-specific validation** rather than one Rekordbox-centric validity model.
14. **MIK-aware export validation** that reports preserved source/coverage without requiring analysis for portable playlists.
15. **Review-surface audio player integration** for Issues/Enrichment and a future persistent player.
16. **Waveform, cue preview, beat-grid visualization, and shortcuts**—useful later, not required for core value.
17. **Backup/restore for settings, index DB, review state, and crate DB** before higher-risk applies.
18. **Authentication or stricter local-network posture** before LAN is treated as routine/default.
19. **Documentation reconciliation** across README, PRODUCT, strategy, legacy command docs, and current routes.

## Recommended roadmap

### Immediate blockers

1. Stop presenting optional-tool warnings as whole-app degradation; introduce core readiness plus workflow capabilities.
2. Remove or correct misleading “force reanalysis” and generic “Analyze Library” behavior before expanding analysis UI.
3. Make localhost the normal default posture or require a conspicuous trusted-LAN confirmation while auth is absent.
4. Keep new in-app analysis DB-only and missing-data-only; do not inherit tag-write/quarantine behavior from the legacy combined command.

### Next three phases

#### Phase 1 — Capability gating and analysis preferences

- backend capability contract;
- BPM/key independent default-off settings;
- MIK preservation and missing-only policies locked;
- button-level availability/setup guidance;
- Jobs becomes monitor-first;
- exact analysis action copy and tests.

#### Phase 2 — Import Wizard and onboarding

- guided root → initialize → preview → import state machine;
- preview fingerprint/token and import confirmation;
- safe existing-metadata read preview;
- empty-library links across routes;
- browse/build/export next actions before analysis suggestions.

#### Phase 3 — MIK/metadata-source coverage

- additive provenance model;
- compatible-tag versus MIK-confirmed distinction;
- BPM/key/Camelot/cue coverage API and UI;
- conflict review;
- explicit DB-only import;
- fallback eligibility derived from trusted missing fields.

### Next ten phases

1. Capability registry and optional analysis preferences.
2. Import Wizard/onboarding and stable preview confirmation.
3. MIK-compatible metadata coverage, provenance, and import.
4. BPM-only and key-only Analysis Jobs with DB-only output and eligibility preview.
5. Goal-oriented sidebar and unified Fix & Review entry point.
6. Genre taxonomy plus deterministic mapping review.
7. Dedicated duplicate scan/review using `rmlint`.
8. Standard job progress, cancellation semantics, safe retry, and run manifests.
9. Destination-specific export validation, preview fingerprints, and export history/diff.
10. Player continuity, waveform/cue visualization, backup/restore, and local security hardening.

### Priority classes

**Must-have**

- core versus optional capability separation;
- default-off BPM/key analysis;
- MIK/source provenance and non-overwrite rules;
- accurate action semantics;
- stable, explicit import confirmation;
- empty-library onboarding;
- safe local-only default posture.

**Should-have**

- unified Fix & Review;
- genre taxonomy;
- duplicate group review;
- analysis progress and run manifests;
- export preview enforcement and validation profiles;
- documentation cleanup.

**Later/advanced**

- tag write-back with backup/diff confirmation;
- waveform/cue/beat-grid tooling;
- exact Serato binary `.crate` support, only if independently verified;
- live DJ-database writers (not recommended without a separate security/backup design);
- AI recommendations after deterministic workflows are complete;
- multi-user/authenticated remote deployment.

## Top three implementation prompts

### Prompt 1 — Optional analysis settings and workflow gating

```text
Recommended model: gpt-5.6-sol
Reasoning/effort: High

Repository:
~/code/gewcc/crateIQ

Before making changes, read and follow AGENTS.md. Review README.md,
PROJECT_CONTEXT.md, NEXT_TASKS.txt, CHANGELOG.txt, and
docs/audits/CRATEIQ_FUNCTIONALITY_WORKFLOW_AUDIT.md.

Do not touch LedgerIQ or opsIQ. Use only crateIQ service commands if needed:
scripts/crateiq-local-services.sh status --short
scripts/crateiq-local-services.sh stop
scripts/crateiq-local-services.sh start

Task:
Implement optional analysis settings and per-workflow capability gating.

Product requirements:
- CrateIQ core workflows must remain usable with no optional executables.
- Import without analysis is enabled and remains the default.
- Analyze BPM and Analyze Key/Camelot are independent, default-off opt-ins.
- Use existing MIK/compatible metadata when present, preserve existing
  BPM/key/cues, and missing-data-only analysis are locked safety policies.
- Missing tools disable only the action that requires them and show setup
  guidance; they must not make the whole app unusable.
- Do not run analysis automatically.

Backend:
- Add a backend-owned capability contract, either within /api/settings or a
  dedicated /api/runtime/capabilities endpoint.
- Model at least: import, browser preview, analyze_missing_bpm,
  analyze_missing_key, beets_import, duplicate_scan, audio_quality_probe,
  audio_conversion, SSD sync, and portable exports.
- BPM capability is available when aubio is available or the declared
  supported librosa fallback is importable; report which provider would run.
- Key capability requires keyfinder-cli. ffmpeg is a separate optional decode
  retry capability and must not be silently conflated with keyfinder.
- Persist only safe preferences in the existing ignored local settings file:
  analyze_bpm=false, analyze_key=false, use_external_tools=false or explicit
  per-tool choices. Do not store secrets or user library metadata.
- Keep core readiness separate from optional capability warnings.
- Do not invoke any external tool during detection.

Frontend:
- Update Settings with clear Analysis preferences and capability rows.
- Replace the vague Library “Analyze Library” action with explicit links/actions
  for Analyze Missing BPM and Analyze Missing Key/Camelot.
- Gate BPM Review reanalysis and any affected Export recovery controls using
  capabilities.
- Remove or correct the current force-reanalysis default. Do not imply values
  will be overwritten; trusted existing/MIK values must remain protected.
- Keep Jobs primarily a monitor. Hide or clearly mark raw advanced commands;
  unavailable commands must not be launchable from the basic UI.
- Show why an action is disabled and link to Settings/tool guidance.
- Use Impeccable for the UI review loop without redesigning unrelated pages.

Safety:
- Do not modify audio files or tags.
- Do not move/quarantine/delete tracks.
- Do not modify BPM/key/cues/MIK data while implementing or testing.
- Do not write Serato/Rekordbox live databases.
- Any future analysis action designed here must default to CrateIQ DB-only,
  missing-data-only behavior; audio-tag write-back is out of scope.

Tests/checks:
- Add focused backend tests for core readiness with every optional tool missing,
  capability alternatives, preference validation/persistence, and action gating.
- Add route-contract coverage if a route is added.
- Run focused pytest, frontend typecheck/build, git diff --check,
  git status --short, and crateIQ service status.
- Do not run the full Python suite under host I/O pressure.

Update README.md, PROJECT_CONTEXT.md, NEXT_TASKS.txt, and CHANGELOG.txt.
Do not commit. Do not use git add .
```

### Prompt 2 — Polish the Library Import Wizard

```text
Recommended model: gpt-5.6-sol
Reasoning/effort: High

Repository:
~/code/gewcc/crateIQ

Before making changes, read and follow AGENTS.md. Review the Settings/library
root implementation, library_setup_service, processed.db schema, Library empty
states, tests, and docs/audits/CRATEIQ_FUNCTIONALITY_WORKFLOW_AUDIT.md.

Do not touch LedgerIQ or opsIQ. Use only crateIQ service helper commands if
service checks are needed. Do not commit.

Task:
Turn the existing Settings Library setup/import panel into a polished explicit
wizard: Choose root → Initialize → Scan preview → Confirm import → Next actions.

Requirements:
- Preserve current safe root validation and restart-required behavior.
- Never scan on startup, validation, save, restart, or initialization.
- Scan only after an explicit Preview action.
- Bind confirmation to a preview ID/fingerprint or clearly detect/report when
  the filesystem changed before import. “Import previewed tracks” must not
  silently mean a different scan.
- Import writes only CrateIQ's local processed.db index.
- Import without BPM/key analysis is enabled and is the default.
- Do not run aubio, librosa, keyfinder-cli, ffmpeg, ffprobe, beet, rmlint, or
  any MIK workflow during normal import.
- Show sample supported files, unsupported/skipped counts, warnings, proposed
  filename-derived artist/title, and parse confidence before confirmation.
- Preserve existing records idempotently and report added/updated/unchanged.
- Add a clean no-audio state and a safe rescan action.
- After import, offer: Browse Library, Fix Metadata, Review MIK/metadata
  coverage, Analyze Missing BPM, Analyze Missing Key, Build Crates, Export.
  Analysis options must be visibly optional and capability-gated.
- Update Library, Crates, Smart Crates, and quality/review empty states to link
  to the correct setup/import step when the library has zero tracks.
- Keep the page scrollable and responsive. Use existing shared UI primitives
  and Impeccable for the review loop; do not broadly redesign the app.

Mixed In Key handling:
- Preserve all existing BPM/key/cue values.
- This task may preview safe existing metadata only if provenance can be
  represented honestly. Do not call a compatible BPM/key tag “MIK” without a
  reliable source signal.
- Do not write file tags or import cue data unless an explicit, tested,
  read-only-to-files metadata-source design is included.

Tests/checks:
- Focused tests for wizard ordering, idempotent initialization, no preview
  writes, stable preview confirmation/change detection, explicit import only,
  supported/unsupported paths, and no analyzer invocation.
- Route-contract smoke test if routes change.
- Frontend typecheck/build, focused pytest, bash -n if helper changes,
  git diff --check, git status --short, and service status.
- Do not use real music fixtures; use temp directories and tiny empty/dummy
  filenames where sufficient.

Update README.md, PROJECT_CONTEXT.md, NEXT_TASKS.txt, and CHANGELOG.txt.
Do not commit. Do not use git add .
```

### Prompt 3 — Mixed In Key and compatible-metadata coverage/import

```text
Recommended model: gpt-5.6-sol
Reasoning/effort: High

Repository:
~/code/gewcc/crateIQ

Before making changes, read and follow AGENTS.md. Review db.py,
modules/analyzer.py, cue_points, import/setup services, Settings/Library UI,
export validation, tests, LOCAL_TOOLING.md, and
docs/audits/CRATEIQ_FUNCTIONALITY_WORKFLOW_AUDIT.md.

Do not touch LedgerIQ or opsIQ. Do not commit.

Task:
Implement a preview-first, DB-only Mixed In Key / compatible analysis metadata
coverage and import foundation.

Product rules:
- CrateIQ works fully without Mixed In Key.
- Existing trusted BPM, key, Camelot, and cue data are preserved.
- Never overwrite MIK values automatically.
- Do not claim a tag was written by MIK unless a reliable source indicator or
  explicit user-provided MIK source proves it. Use “compatible file tag” when
  the producer is unknown.
- Fallback BPM/key analysis remains a separate, later opt-in workflow and may
  target only fields missing trusted values.

Backend/data model:
- Design the smallest additive migration for metadata observations/provenance,
  covering field, value, source category, confidence, observed_at, and conflict
  state while retaining canonical tracks.bpm/key fields for compatibility.
- Source categories should include existing_db_unknown, compatible_file_tag,
  mik_confirmed (only when defensible), crateiq_aubio, crateiq_librosa,
  crateiq_keyfinder, manual, and unknown.
- Keep imported/observed cue data separate from CrateIQ cue suggestions.
- Add read-only-to-files scan/preview and explicit DB-import endpoints.
- Read only supported metadata blocks; do not scan automatically, analyze
  audio, or invoke external tools.
- Report BPM/key/Camelot/cue present, trusted, missing, source, and conflicts.
- Preserve canonical values on conflict and send conflicts to review.
- Do not require a MIK database export unless a separately documented format
  is genuinely supported; otherwise mark dedicated MIK file import deferred.

Frontend:
- Add MIK/metadata source coverage in Settings or a focused Library panel.
- Provide “I use Mixed In Key” / “I do not use Mixed In Key” guidance without
  disabling core workflows.
- Show coverage by field and source, conflict counts, preview, and an explicit
  “Import existing metadata to CrateIQ index” action.
- Clearly state that files/tags/MIK and DJ application databases are not
  modified.
- Link missing trusted BPM/key counts to future capability-gated analysis
  actions, but do not run those actions in this task.
- Use Impeccable for the UI review loop and preserve the established visual
  system.

Safety:
- No audio or tag writes, moves, deletes, renames, transcoding, or analysis.
- No MIK application/database mutation.
- No Serato/Rekordbox live database writes.
- Use temp fixture audio containers or mocked tag readers in tests; never use
  real music files.

Tests/checks:
- Add migration/idempotency tests; supported tag parsing tests; unknown-source
  labeling; MIK-confirmed evidence rules; canonical-value preservation;
  conflict behavior; cue separation; empty/missing/unsafe path handling; and
  preview-no-write/import-DB-only behavior.
- Run focused pytest, route contracts, frontend typecheck/build,
  git diff --check, git status --short, and service status.
- Do not run the full suite under host I/O pressure.

Update README.md, PROJECT_CONTEXT.md, NEXT_TASKS.txt, CHANGELOG.txt, and tooling
docs if the supported source contract changes. Do not use git add . Do not
commit.
```

## Safety model review

### Strengths to preserve

- New API file access is constrained to the selected library or export roots.
- Library scan/import is explicit and does not modify audio files.
- Manual/Smart Crates use library-scoped local databases and preserve order.
- Portable and DJ-app exports create new staged artifacts and do not overwrite live DJ libraries.
- Dedicated metadata review queues distinguish dry-run from apply and update CrateIQ DB fields only.
- The subprocess runner avoids shell interpolation and validates commands/arguments.
- Missing optional binaries do not prevent backend startup.

### Risks to resolve

1. The generic Jobs launcher treats commands with radically different side effects as peer options and offers only generic dry-run/verbose controls.
2. The combined legacy `analyze-missing` apply path writes audio tags and can move decode failures; that behavior is unsuitable as the default backend for a friendly in-app analysis action.
3. UI/API force-reanalysis language does not match the missing-only implementation or dry-run command construction.
4. No provenance field makes MIK authority a convention rather than an auditable invariant.
5. Optional tool warnings are global rather than scoped, which weakens both clarity and trust.
6. LAN startup is offered without authentication. The app must remain trusted-local-only and localhost-first.
7. Some legacy docs describe write-heavy full-pipeline flows more prominently than the newer safe library/crate workflow.

## Open questions

1. What reliable evidence, if any, is available in the owner's MIK-written files to distinguish MIK from generic BPM/key tags?
2. Which cue containers/frames must be supported for read-only MIK coverage, and can they be identified without guessing?
3. Should CrateIQ's in-app analyzer ever write audio tags, or should results remain DB-only with a separate future export/write-back workflow?
4. Is `librosa` an intentionally supported production BPM fallback or only a convenience dependency? The capability contract should make this explicit.
5. Should the legacy full-library pipeline remain launchable from the web, or be CLI-only until command-specific risk forms exist?
6. Is LAN access required for the target user workflow? If so, what minimum confirmation/auth boundary is acceptable?
7. Should scan/import confirmation snapshot paths only, or also file size/mtime to detect changes between preview and import?
8. How should imported tracks be marked when they later disappear or move, without turning an explicit import into automatic background scanning?
9. Should Manual Crates remain in a separate DB, or should backup/restore bundle processed index, crates, preferences, provenance, and review state together?
10. Which user-facing term is clearest: “Smart Crates,” “Suggested Crates,” or “Crate Suggestions”? The current implementation is a saved snapshot, not a continuously updating rule.

## Recommended next task

**Follow-up (2026-08-05):** the optional-analysis settings and per-workflow
capability contract is now implemented. Core workflows remain independently
available; Settings exposes default-off BPM/key preferences and locked MIK,
existing-value, and missing-data-only policies. Cards honestly label unavailable
or not-yet-runnable advanced workflows rather than launching a generic analyzer.

The next task is a **DB-only, missing-data-only analysis jobs workflow** with
explicit BPM/key eligibility previews and source-aware MIK-compatible metadata
coverage before any fallback analysis is expanded.
