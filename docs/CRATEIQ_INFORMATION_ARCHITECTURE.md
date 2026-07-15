# CrateIQ Information Architecture

Status: recommended target; not implemented in Phase 0.

## Primary navigation

| Section | User question | Primary destination |
|---|---|---|
| Home | What needs my attention and what is safe to do next? | `/` |
| Library | What is in my library and what do I know about each track? | `/library` |
| Fix & Review | Which proposals or findings need a human decision? | `/fix-review` |
| Sets | How am I building and preparing a DJ set? | `/sets` |
| Publish | Is the library ready to export or sync, and what will change? | `/publish` |
| Operations | What ran, what changed, and is the runtime healthy? | `/operations` |

Top-level navigation should stay at six sections. Technical tools appear inside Operations, and issue type appears as filters/tabs inside Fix & Review rather than as separate top-level destinations.

## Route plan

| Target route | Responsibility | Existing implementation reused |
|---|---|---|
| `/` | Command-center Home | `Dashboard.tsx` concepts, library overview/quality, jobs, audit, export validation and sync config |
| `/library` | Search/filter/sort/browse, saved views, track inspector | `CrateMind.tsx`, `/api/tracks`, folders, track detail |
| `/library/:trackId` | Deep link to track inspector/history | `/api/tracks/{id}` and existing inspector |
| `/fix-review` | Unified review inbox | metadata repair, sanitation, enrichment and BPM pages/queues |
| `/fix-review/:kind` | Filtered review kind: metadata, sanitation, enrichment, BPM, duplicates, paths | existing queue APIs and path audit artifacts |
| `/sets` | Saved/draft set planning | `SetBuilder.tsx`, playlist service and set tables |
| `/sets/:setId` | Set editor/readiness | set playlist rows and track API |
| `/publish` | Export/sync guided workflow | `Export.tsx`, `SsdSync.tsx`, validation/jobs/sync APIs |
| `/operations` | Operations overview and runtime readiness | `Jobs.tsx`, `Reconciliation.tsx`, audit/health APIs |
| `/operations/jobs` | Job queue/status/logs | jobs API and `LogModal` |
| `/operations/audit` | Audit timeline and artifacts | audit endpoints, run summaries, ledger |
| `/operations/reconciliation` | Plans, ledger and validation | reconciliation API |
| `/operations/runtime` | Preflight, provider, storage and DB health | health/config/read-only services |

The exact route names can be adjusted during Phase 1, but the ownership boundaries should remain stable.

## Redirect and migration plan

Keep compatibility redirects while the target routes are introduced:

| Legacy route | Target | Migration note |
|---|---|---|
| `/dashboard` | `/` | Existing redirect already exists |
| `/collection` | `/library` | Existing page is legacy/reference only |
| `/tracks` | `/library` | Existing redirect already exists |
| `/quality` | `/?view=quality` or `/library?view=quality` | Preserve deep-link context in a query parameter |
| `/issues` | `/fix-review?kind=issues` | Preserve issue filter |
| `/enrichment` | `/fix-review/enrichment` | Preserve queue filters |
| `/metadata-repair` | `/fix-review/metadata` | Preserve selected track/field when possible |
| `/metadata-sanitation` | `/fix-review/sanitation` | Preserve selected track/field when possible |
| `/bpm-review` | `/fix-review/bpm` | Label BPM as review of anomalies, not authoring |
| `/audit` | `/operations/audit` | Preserve latest audit view |
| `/folders` | `/library?view=folders` | Folders become a Library view |
| `/jobs` | `/operations/jobs` | Preserve job query/detail |
| `/reconciliation` | `/operations/reconciliation` | Preserve ledger/plan ID |
| `/set-builder` | `/sets` | Preserve set ID/draft if present |
| `/exports` and `/export` | `/publish?step=export` | Preserve validation/export context |
| `/sync` and `/ssd-sync` | `/publish?step=sync` | Preserve preview/job context |
| `/settings` | `/operations/runtime` | Do not expose an empty settings page; show runtime/config summary |

Redirects should be measured and eventually marked deprecated. Do not delete legacy routes until deep links and documentation have migrated.

## Page responsibilities

### Home

Owns summary and next action, not detailed editing. It may link to a review or operation with a scoped filter. It must show freshness and degraded/unknown status.

### Library

Owns current track browsing and context: search, filters, sort, saved views, columns, selection, inspector, provenance, current versus proposed values and processing history. It must not become a generic file manager.

### Fix & Review

Owns human decisions on proposals/findings. Every item must show evidence, source, reason, confidence, safety level, current/proposed values, review status, apply eligibility and verification. It must distinguish a review decision from a write operation.

### Sets

Owns drafts, ordering, notes, duration, known BPM/key data and readiness warnings. It must not fabricate energy, waveform, compatibility or performance metadata.

### Publish

Owns the sequential validate/preview/confirm/apply/verify workflow for export and sync. Export and sync remain separate steps and separate confirmations.

### Operations

Owns technical state: jobs, logs, audit history, reconciliation plans/ledger, runtime preflight, provider status, storage and DB health. It is the place for implementation detail.

## Workflow relationships

```text
Home health card
   ├─→ Library scoped view
   ├─→ Fix & Review queue
   ├─→ Sets readiness
   ├─→ Publish validation
   └─→ Operations job/audit detail

Library track
   ├─→ Fix & Review proposal
   ├─→ Sets picker
   └─→ Publish readiness (never direct silent apply)

Fix & Review
   └─ review decision → dry-run → explicit apply → verification → Operations audit

Publish
   └─ validate → preview → confirm → job → verify → audit
```

## User journeys

### Find and fix a metadata issue

1. Home shows “tracks needing attention” with a count and freshness.
2. User opens Fix & Review with a filtered queue.
3. User inspects current value, proposed value, source, confidence and reason.
4. User approves/rejects/defers the proposal; this is only a review decision.
5. User opens a dry-run summary showing exact fields/items.
6. User explicitly confirms an eligible apply.
7. CrateIQ verifies the DB postcondition and records the operation.

### Investigate a missing file

1. Home or Library shows missing-path count.
2. Fix & Review → Paths shows the missing path and evidence.
3. Candidate matches are presented as proposals with confidence, never applied automatically.
4. User validates a reconciliation plan.
5. Any future apply, if separately supported, requires a new confirmation and verification.

### Prepare and publish a library

1. User opens Publish.
2. CrateIQ validates export requirements and lists blockers.
3. User generates a dry run and reviews exact outputs.
4. User confirms export; a job runs and is visible in Operations.
5. CrateIQ verifies artifacts.
6. User previews SSD sync separately, reviews conflicts, confirms, then verifies destination.

### Build a set

1. User opens Sets and creates or resumes a draft.
2. User selects tracks from Library.
3. User orders tracks, adds notes and sees deterministic duration/BPM/key values where present.
4. Unknown or missing values appear as warnings, not invented scores.
5. User reviews readiness and only then chooses a supported publish path.

## Status terminology

Use these terms consistently:

| Term | Meaning |
|---|---|
| Detected | A rule or scan found a possible issue |
| Classified | The issue has a type, reason and safety level |
| Proposed | A concrete current → proposed change exists |
| Reviewed | A human inspected it |
| Approved | A human authorized this proposal for a later apply |
| Applied | The explicit mutation completed |
| Verified | The expected postcondition was checked |
| Rejected | Human declined the proposal |
| Deferred | Human postponed a decision |
| Blocked | Safety or prerequisite prevents progress |
| Skipped | Safely not changed, with a reason |
| Failed | Operation attempted but did not complete |
| Cancelled | User requested cancellation and the operation stopped or was marked cancelled |
| Preview/Dry run | No target mutation; reports intended results |

Avoid using “fixed” for approved or previewed work. Avoid “complete” when a job is only queued.

## Legacy migration principles

- Keep API paths and CLI commands stable unless a compatibility plan exists.
- Treat old route redirects as temporary compatibility behavior, not duplicate product surfaces.
- Keep `CRATEMINDAI_LIBRARY_ROOT` as a deprecated environment fallback while documenting `CRATEIQ_LIBRARY_ROOT` as preferred.
- Preserve database paths, queue file names, serialized formats and historical changelog text.
- Do not rename internal legacy files merely for visual consistency until import and migration impact is measured.
