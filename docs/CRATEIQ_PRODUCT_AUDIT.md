# CrateIQ Product Audit

Audit date: 2026-07-14
Source: local fork of `fgyamerah/CrateMindAI` at commit `fc8e863`
Scope: repository, pipeline, database, FastAPI backend, React frontend, tests, documentation, configuration, Git history, and current UI surfaces.

## Executive summary

CrateIQ has a substantial safety-oriented backend and pipeline foundation. The strongest current capabilities are deterministic library inspection, canonical track storage, path auditing, review queues, controlled metadata apply, job allowlisting, export validation, sync preview, reconciliation planning, and extensive Python safety tests. The product is not yet a cohesive DJ operations console: the frontend exposes many implementation modules as top-level navigation, the command center is not yet a true Home surface, review and apply semantics vary by page, and the set/export/sync workflows need clearer readiness and verification boundaries.

The fork is safe to continue from because the upstream remote is retained as `upstream`, no `origin` exists, and work is isolated on `feat/crateiq-foundation-audit`. This audit/roadmap commit does not implement the proposed redesign phases.

CrateIQ should remain local-first, review-first, dry-run-first, deterministic where possible, conservative with uncertain metadata, and explicit about every operation. Rekordbox and Mixed In Key remain authoritative for BPM, key, beatgrids, hot cues, and memory cues. A safe skip is preferable to a confident-looking incorrect change.

## Product definition

CrateIQ is a local-first DJ music-library intelligence and operations platform. It helps DJs inspect libraries, detect quality and metadata problems, review repair/enrichment proposals, audit paths, prepare exports, plan sets, and preview SSD synchronization. It is an operational layer around Rekordbox and Mixed In Key, not a replacement for either product.

Current ownership boundaries:

| Data | Current owner | Audit finding |
|---|---|---|
| Current library record | `tracks` in `<root>/logs/processed.db` | Implemented and used by backend browsing/quality/review views |
| Processing history | `processed_state` | Implemented as stage/path/mtime/size history; not current state |
| Pipeline run history | `pipeline_runs`, `track_history` | Implemented, with uneven frontend exposure |
| Duplicate candidates | `duplicate_groups` and pipeline reports | Detection/quarantine concepts exist; review UX is incomplete |
| Set records | `set_playlists`, `set_playlist_tracks` | Backend and pipeline support exist; set UX is early |
| Reconciliation history | `reconciliation_ledger` plus plans | Planning and validation exist; broad apply is explicitly unsupported |
| BPM/key/cues/beatgrids | Rekordbox/Mixed In Key | Must remain authoritative; CrateIQ must not overwrite them |
| Backend jobs | `backend/data/jobs.db` | Separate jobs DB with statuses, logs, PID and progress fields |

## Current architecture

```text
Selected library root
  ├─ audio files and folders
  ├─ logs/processed.db        ← pipeline current/history state
  ├─ data/intelligence/*      ← review queues and decisions
  └─ logs/*                   ← reports, run artifacts, audit output
          │
          ├─ pipeline.py / modules / intelligence / ai
          │       └─ deterministic and optional AI/provider workflows
          │
          └─ backend/app (FastAPI)
                  ├─ reads the selected root and pipeline DB
                  ├─ owns backend/data/jobs.db for job tracking
                  └─ exposes /api/* to the React/Vite frontend
                              └─ frontend/src pages, API modules, hooks
```

Important implementation boundaries:

- `config.py` resolves pipeline roots and operational output paths, with optional `config_local.py` overrides.
- `db.py` owns the pipeline SQLite schema and connection lifecycle.
- `backend/app/core/db.py` owns the separate backend jobs/BPM review database.
- `backend/app/core/library_root.py` now prefers `CRATEIQ_LIBRARY_ROOT`; `CRATEMINDAI_LIBRARY_ROOT` remains a deprecated fallback, then configured `MUSIC_ROOT` is used.
- `backend/app/services/toolkit_runner.py` and `process_registry.py` provide allowlisted subprocess execution and cancellation tracking.
- The frontend calls the backend through `frontend/src/api/client.ts`; it does not directly write audio tags or files.
- There is no authentication, authorization, session system, or route guard. The app is trusted-local-only software.

## Existing feature maturity matrix

“Current” describes code verified in this audit. “Planned” is not implemented by this commit.

| Capability | Current maturity | Current mode | Notes / next boundary |
|---|---|---|---|
| Root-aware library selection | Fully implemented | Read-only by default | Absolute paths and containment helpers exist; configuration still has legacy path assumptions |
| Track browsing | Fully implemented | Read-only | SQL pagination, search, filters, sorting, issue badges, inspector and virtualized table exist in `CrateMind.tsx` |
| Library overview/quality | Implemented | Read-only | Stats, folders, quality and run summaries exist; no command-center action model |
| Filename parsing | Fully implemented | Preview/read-only unless pipeline apply path | Confidence-gated deterministic fallback with tests |
| Metadata extraction | Implemented | Dry-run/apply DB-only | Does not write tags; ownership rules are conservative |
| Metadata repair | Partially implemented | Review + dry-run/apply approved DB changes | Queue and per-field review exist; lifecycle is not shared with other queues |
| Metadata sanitation | Partially implemented | Review + dry-run/apply approved DB changes | Similar but separate API/UI/state model |
| Online enrichment | Implemented | Candidate scoring/review; controlled DB apply | Provider modes and review state exist; not authoritative and not blind apply |
| AI normalization | Partially implemented | Local proposal/review/apply pipeline | Prompt logging can contain private metadata; confidence and provenance need stronger product presentation |
| BPM anomaly review | Implemented | Review/read-only plus reanalysis job dispatch | Reanalysis must retain Mixed In Key ownership; mutation semantics need stronger guardrails |
| Quality audit | Implemented | Read-only report, optional explicit tag/move operations | UI is summary-oriented; quarantine and operation provenance are not unified |
| Duplicate detection | Partially implemented | Candidate detection and quarantine-oriented pipeline | No complete side-by-side duplicate review or permanent-delete prohibition UX |
| Missing/untracked/path audit | Fully implemented | Read-only | Reports missing, untracked, stale and candidate paths; expensive scoring is opt-in |
| Reconciliation | Partially implemented | Planning/validation; narrow safe apply in pipeline | Broad automatic reconciliation is unsupported; frontend is ledger/plan oriented |
| Jobs | Implemented | Allowlisted async subprocesses | Status, logs, cancel and progress fields exist; “real progress” varies by command |
| Rekordbox export | Partially implemented | Validation, dry-run/apply job | XML is disabled by default; verification and blocker UX need consolidation |
| SSD sync | Partially implemented | Preview and explicit rsync job | Config uses fixed named source/destination paths; conflict/verification flow needs productization |
| Set Builder | Partially implemented | Pipeline/backend set generation and saved records | Existing UI is not yet a dependable planning workspace; do not fabricate energy/waveform/compatibility data |
| Audit history | Partially implemented | JSON/report/ledger artifacts and job logs | No unified user-facing audit timeline across all operations |
| Authentication | Unsupported | Trusted-local-only | Must precede remote or multi-user deployment |

## Backend audit

### Route surface

The active FastAPI surface is mounted under `/api`:

| Group | Routes | Current assessment |
|---|---|---|
| Health | `/health`, `/stats`, `/version` | Read-only readiness and snapshot data; startup currently initializes backend DB |
| Tracks | `/tracks`, `/tracks/{id}`, `/tracks/stats`, `/tracks/issues` | Read-only, paginated/filtered current-state browsing |
| Library | `/library/stats`, `/folders`, `/overview`, `/quality`, `/runs`, `/runs/{command}/{prefix}/summary`, `/tree` | Read-only views; filesystem/tree work must stay bounded |
| Jobs | POST/GET `/jobs`, job detail/logs/cancel | Allowlisted async subprocess model; no auth |
| Enrichment | queue/state/export/summary plus approve/reject/defer and dry-run/apply-approved | Review state writes are explicit; apply requires confirmation |
| Metadata repair | queue/summary, per-track/field review, proposal edit, generate and apply endpoints | Functionally useful but separate from sanitation and enrichment models |
| Metadata sanitation | queue/summary, per-track/field review, proposal edit, generate and apply endpoints | Similar duplicate lifecycle with inconsistent terminology risk |
| BPM analysis | check, anomaly summary/list, anomaly patch, reanalyze job | Review surface exists; must not imply CrateIQ owns BPM truth |
| Exports | validate, run, list, detail | Validation is read-only; export is queued and flag-driven |
| Sync | config, preview, run, list, detail | Preview and explicit run exist; destination safety is configuration-based |
| Playlists | set-builder POST, list, detail | Backend persistence exists; product workflow is not yet complete |
| Reconciliation | ledger list/detail, plan validation | Planning-first; broad apply is not supported |

### Runtime preflight and job model

- Startup logs the CrateIQ version, project root, pipeline path, selected library root, and DB presence, then calls `init_db()` for backend job state.
- Jobs are inserted as `pending`, launched as background subprocesses, tracked in a separate SQLite DB, and updated to `running`, `succeeded`, `failed`, or `cancelled`.
- Cancellation sends `SIGTERM` through an in-process registry; it is best-effort and should remain labeled as such.
- Job logs are plain text files. Progress fields exist, but reliable progress depends on the runner/command and should not be shown as precise unless emitted by the process.
- The process allowlist and argument validation are important safety boundaries and must be preserved.
- There is no auth, no per-user ownership, and no remote exposure boundary. CORS is limited to local development origins but this is not an authentication control.

### Path containment and data safety

`assert_path_under_root` resolves relative paths against the selected root and rejects traversal outside it. Route/service code also validates named sync sources and destinations. Tests cover root isolation, traversal, missing files, mixed roots, and reconciliation separation. Risks remain around path identity drift after moves, fixed operational paths in `config.py`, and the number of modules that still carry their own path assumptions.

### Database and state ownership

The pipeline DB schema contains current tracks, processing state, run history, duplicate groups, cue suggestions, set records, and reconciliation ledger rows. The backend jobs DB contains jobs and BPM anomaly review state. JSON/JSONL queues are separate from both DBs. This is a valid local-first model but creates multiple state authorities and path-based joins. A future unified operation/result envelope should link DB rows, queue decisions, plan IDs, job IDs, and output artifacts without moving existing stores speculatively.

## Pipeline audit

`pipeline.py` is a large CLI router with legacy and current commands in one file. The command inventory includes quality control, dedupe, organize, sanitize, analyze, tag, cue suggestion, artist repair/intelligence, enrichment, metadata repair/sanitation, path audit/reconcile, playlists, export, sync-related tooling, reports, and maintenance commands. Commands vary in safety semantics:

- Newer root-aware commands use `--root` and default to read/preview behavior.
- Older commands use `--path`/`--input` and retain historical compatibility.
- Several commands can write tags or move files only under explicit flags; tests cover `--apply`, `--yes`, `--dry-run`, idempotency and containment.
- Rekordbox export and sync are operationally high risk even when wrapped in a job; their UI must expose dry-run, confirmation, blockers, conflict behavior, destination and verification.
- `modules/organizer.py` is documented as legacy/deprecated and must not be the foundation of new organization behavior.
- Generated command docs and historical scripts still contain DJ Toolkit/TrackIQ language; they are naming debt, not evidence of new product capabilities.

## Frontend audit

### Active route surface

The mounted routes are `/`, `/issues`, `/enrichment`, `/audit`, `/folders`, `/quality`, `/metadata-repair`, `/metadata-sanitation`, `/bpm-review`, `/jobs`, `/set-builder`, `/exports`, `/sync`, and `/reconciliation`. Legacy aliases `/dashboard`, `/collection`, `/tracks`, `/settings`, `/export`, and `/ssd-sync` redirect or are hidden. `Dashboard.tsx`, `Collection.tsx`, `Tracks.tsx`, and `Settings.tsx` remain in the repository but are not the canonical active surfaces.

### Current strengths

- Root library browsing has search, server-side pagination, sorting, issue filters, selection, an inspector, persisted UI state, debounced search and virtualized rows.
- Review pages expose approve/reject/defer actions and dry-run/apply actions in at least some workflows.
- Jobs expose status, logs and cancellation; export and sync have dedicated pages.
- Error banners, loading states, empty states and an error boundary exist in several places.

### Current product and UI/UX problems

- Navigation is grouped as Browse, Operations and Reconciliation, which mirrors code modules more than the user goals Home, Library, Fix & Review, Sets, Publish and Operations.
- There is no true command-center Home with next actions, readiness, failed operations, export blockers and sync readiness.
- `CrateMind.tsx` is a large monolith containing track browsing, queue state, persistence, filters, inspector and multiple review actions. Other pages duplicate state and API patterns.
- Metadata repair, sanitation, enrichment and BPM review do not share one visible lifecycle or common difference/provenance vocabulary.
- Dense tables often expose technical paths and fields without enough progressive disclosure, responsive alternatives, or column control.
- Some controls and text can make a preview, approval, apply, reanalysis, export, or sync look more equivalent than it is.
- Jobs have reliable status fields but not always reliable measurable progress; UI must not imply percentage precision without command evidence.
- Existing pages include technical and legacy terms such as “DJ Toolkit”, “TrackIQ”, “CrateMind”, “Ledger”, and “SSD Sync” without a consistent product language model.
- Backend capabilities such as reconciliation plan validation, audit artifacts, provenance, source information and processing history are not consistently exposed at the point of decision.
- Responsive behavior is limited by table density and desktop assumptions. Mobile-friendly cards/drawers are not yet a coherent system.

## Accessibility audit

Current positives include some semantic buttons, `aria-label` usage, `role="alert"`, dialog semantics in `LogModal`, and a progressbar in sync. Gaps to address:

- Table selection, virtualized rows, filters and pagination need a documented keyboard model and focus retention.
- Some interactive-looking elements are `div` elements with click handlers; keyboard parity and visible focus must be verified.
- Status is frequently communicated through color and small muted text; every status needs text/icon/label support and contrast validation.
- Dialog focus trapping, initial focus, escape behavior, return focus and screen-reader descriptions need consistent implementation.
- Long technical paths and dense data need accessible labels and responsive summaries.
- Async job completion/failure needs polite/live announcements.
- Reduced-motion behavior and minimum touch targets are not yet a shared requirement.

The roadmap treats WCAG 2.2 AA as a release gate, not a visual polish item.

## Responsive audit

The current information density is optimized for desktop. The library table, inspector, jobs table, export validation and sync preview will need tablet layouts, mobile card/list representations, a drawer-based inspector, responsive dialogs, and touch-sized controls. The target is desktop-first with tablet usable and mobile task-completion paths, not a shrunken desktop table.

## Safety audit

Safety guarantees verified in code/tests:

- Dry-run/preview is the default for many write-capable commands.
- Apply flows require explicit confirmation in the newer CLI/backend paths.
- Path containment and root isolation are tested.
- Metadata ownership preserves BPM/key/cues from automatic mutation.
- Path audit is read-only and reports before reconciliation.
- Enrichment is candidate/review-first and controlled DB-only apply.
- Duplicate workflows are quarantine-oriented rather than permanent deletion by default.

Safety gaps and risks:

- The number of commands and old flag variants makes the safety contract difficult to understand.
- A generic Jobs page can make heterogeneous commands look equally safe.
- Export and sync are high-risk operations that need one shared confirmation and verification vocabulary.
- No authentication or authorization means any local client that can reach the API can request supported mutations.
- Prompt logs may contain private library metadata.
- Some API error paths and fallback behavior need review so operational failures do not become silent empty states.
- Filesystem/database/queue references can drift after moves; the reconciliation ledger is not yet the universal source of operation history.

## Performance audit

Existing protections include DB indexes, SQL `LIMIT/OFFSET`, capped track listing, SQL filters, request timing headers/logs, cached enrichment queue reads, debounced search, persisted UI state, virtualized track rows, and loading/error states. Remaining risks are expensive scans during page rendering, offset pagination at very large scale, repeated API requests from duplicated page logic, unmeasured slow queries, filesystem tree expansion, and provider/network work leaking into interactive reads. The roadmap requires measurements before optimization claims.

## Testing audit

The Python suite collected 857 tests in this environment and contains strong coverage for metadata transforms, path containment, dry-run/apply gates, idempotency, reconciliation, enrichment, root isolation, and backend routes. The baseline run reached the first backend health test and stalled; an isolated 30-second run of that test exited with code 124 without reaching an assertion. This is documented as a pre-existing baseline blocker, not hidden.

Frontend baseline has no unit-test script. TypeScript typecheck and Vite production build pass. There are no committed frontend route, review-flow, accessibility, responsive, or deterministic end-to-end tests comparable to the backend suite.

Recommended test additions are specified in the roadmap; no major test framework or dependency is added in Phase 0.

## Technical debt and legacy code

- Product language remains in CrateMindAI, DJ Toolkit, TrackIQ, and KKDJ forms across active comments, generated docs, and history.
- `CrateMind.tsx` and several large pages need decomposition.
- `Dashboard.tsx`, `Collection.tsx`, `Tracks.tsx`, and `Settings.tsx` are retained but orphaned/redirected.
- `pipeline.py` is a large command router with mixed generations of command semantics.
- `modules/organizer.py` is explicitly legacy/deprecated.
- `COMMANDS.md`, `COMMANDS.txt`, `COMMANDS.html`, and generators can drift from one another.
- Fixed SSD and source path defaults in backend configuration increase environment coupling.
- The jobs DB and pipeline DB are intentionally separate, but cross-links and lifecycle history are incomplete.

## Product risks ranked

1. Critical: mutating operations remain exposed in a trusted-local-only API with no authentication or authorization.
2. High: inconsistent preview/approval/apply/verify language can cause users to misunderstand what a control will change.
3. High: export and SSD sync can affect external operational media; conflict, destination, and verification visibility are not yet unified.
4. High: path-based identity drift can produce stale records or unsafe reconciliation proposals after moves.
5. Medium: frontend architecture and navigation make core workflows hard to discover and maintain.
6. Medium: backend baseline health test hangs in the current environment and must be diagnosed before release hardening.
7. Medium: development dependency advisories exist in Vite/esbuild; the available automated fix is a major Vite upgrade and was intentionally not applied.
8. Medium: private library metadata may enter prompt logs; logging and retention need explicit controls.
9. Low: naming and generated documentation drift reduce trust and increase operator confusion.

## Recommended information architecture

The target top-level sections are:

1. Home — readiness, library health, attention queue, recent operations and next action.
2. Library — scalable browsing, filters, saved views, track inspector, provenance and history.
3. Fix & Review — one review center for metadata, sanitation, enrichment, BPM anomalies, duplicates and paths.
4. Sets — saved/draft set planning with only real data and explicit warnings.
5. Publish — validate, dry-run, preview, confirm, export, verify, sync preview, confirm, verify.
6. Operations — jobs, audit, reconciliation, preflight, providers, logs, storage and DB status.

Legacy routes should redirect to the new destinations with a visible compatibility notice until migration is complete. The detailed route plan is in `CRATEIQ_INFORMATION_ARCHITECTURE.md`.

## Recommended status lifecycle

Use one visible lifecycle for proposed changes:

```text
Detected → Classified → Proposed → Reviewed → Approved → Applied → Verified
```

Review status and operation status must remain distinct. Approval is a human decision about a proposal; apply is an explicit mutation; verified means the expected postcondition was checked. `Rejected`, `Deferred`, `Blocked`, `Skipped`, `Failed`, and `Cancelled` are terminal or side states with reason codes.

## Naming migration findings

- Product-facing branding is now `CrateIQ` in the app title, sidebar, backend API metadata, package metadata, active comments and selected setup/runtime messages.
- `CRATEIQ_LIBRARY_ROOT` is preferred.
- `CRATEMINDAI_LIBRARY_ROOT` remains supported as a deprecated fallback and is documented in `.env.example`.
- `DJ_MUSIC_ROOT`, database paths, API paths, CLI command names, serialized queue formats and historical artifacts were not renamed because they are compatibility-sensitive contracts.
- Historical changelog entries are intentionally unchanged.
- Internal legacy filenames/symbols such as `CrateMind.tsx` remain identified as migration debt rather than being renamed speculatively.
- Remaining generated and historical references are catalogued for later, source-aware cleanup; they do not represent new CrateIQ product promises.

## Priority recommendations

1. Resolve and test the backend startup/health hang before declaring the foundation stable.
2. Establish shared API/result/status types and a product shell before changing page layouts.
3. Build Home and route consolidation around user goals, not module names.
4. Refactor Library and review flows with server-side state, provenance and explicit apply verification.
5. Gate duplicate/path/quarantine workflows with planning-first semantics and no permanent-delete UI.
6. Make Set Builder and Publish honest about what data exists and what is unsupported.
7. Consolidate Jobs, Audit and Operations into a clear operation history.
8. Add accessibility, responsive, performance and end-to-end gates before release.

## Baseline evidence

- Python: `Python 3.12.3` in `.venv`.
- Node: `v20.20.2`.
- npm: `10.8.2`.
- Backend collection: 857 tests collected; full run stalled at `tests/test_backend_api.py::test_health_endpoint_reports_selected_root_and_db`; isolated timeout result `124` after 30 seconds.
- Frontend typecheck: passed.
- Frontend production build: passed; Vite transformed 1,777 modules and emitted `frontend/dist`.
- npm audit: production dependencies 0 advisories; full tree 2 advisories (moderate `esbuild`, high `vite`).
