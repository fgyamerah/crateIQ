# CrateIQ Product and Engineering Roadmap

Re-baselined after the completed 2026-08 five-task cycle:

- BPM retry pause + explicit Retry/Resume
- Reviewed reconciliation DB-only APPLY/rollback
- Duplicate resolution safe planning
- Metadata identity + field provenance
- Deterministic Genre Intelligence Phase 3

This document is now a living roadmap anchored to the current source tree.
History belongs in `CHANGELOG.txt` and `docs/history/`, not here.

Current status vocabulary:

- `IMPLEMENTED`
- `SUBSTANTIALLY IMPLEMENTED`
- `PARTIAL`
- `PLANNED`
- `DEFERRED`

## Roadmap Rules

- Preserve local-first, review-first, dry-run-first behavior.
- Never silently write tags, rename or move files, delete audio, change a real Rekordbox DB, or change BPM/key/cues/beatgrids/performance metadata.
- Every mutation must identify scope, current value, proposed value, safety level, confirmation requirement, operation ID, and verification result.
- A proposal is not an approval. An approval is not an apply. An apply is not a verification.
- Use repository-controlled temporary fixtures only for development, tests, and screenshots.
- Do not describe planned behavior as current behavior in UI or documentation.
- Cheaper model = routine implementation, mechanical wiring, test expansion, and doc updates after contracts are decided.
- Stronger reasoning model = state-machine design, safety boundaries, data migrations, performance decisions, complex UX, and release review.

## Cross-Phase Product Contracts

### Shared lifecycle

```text
Detected -> Classified -> Proposed -> Reviewed -> Approved -> Applied -> Verified
```

Side states: `rejected`, `deferred`, `blocked`, `skipped`, `failed`, `cancelled`. Review status and operation status must remain separate fields.

### Shared operation result

Every future operation result should expose: `operation_id`, `operation_type`, `mode` (`read`, `preview`, `dry_run`, `apply`), `scope`, `counts`, `warnings`, `blockers`, `artifacts`, `affected_item_ids`, `confirmation_required`, `verification_status`, and `error_code`/`reason_code` where applicable.

### Shared safety levels

- Safe read: no writes to library, tags, pipeline DB, or queues except bounded cache/report output.
- Review write: records a human decision or proposal edit only.
- Controlled apply: narrow, explicitly confirmed mutation with an allowlist and postcondition check.
- High-risk operation: file move, tag write, export overwrite, external SSD sync, or path update; requires a dedicated preview and confirmation.
- Unsupported: no control, no fake progress, no implied capability.

## Historical Baseline

### Phase 0 — Clone, rename, baseline, audit, and roadmap

Current status: `IMPLEMENTED`

- Exists now: the CrateIQ fork, the current safety model, the route/service split, and the baseline docs that describe the current product.
- Remains: nothing in this phase is an active product gap.
- Superseded: roadmap prose that still treats the repo as a pre-baseline fork.
- Depends on: upstream clone, repository instruction review, and the current source tree.
- Safety boundaries: baseline work never becomes a back door for product behavior changes.

### Phase 1 — Foundation and design system

Current status: `PARTIAL`

- Exists now: `/api/runtime/readiness`, `ready` / `degraded` / `not_ready` runtime semantics, shared `PageHeader` / `Badge` / `EmptyState` / `StatusStrip` primitives, and a consistent read-only health/status surface in several areas.
- Remains: a shared operation/result vocabulary across workflows and fully unified status/error contracts. The frontend test foundation now covers Publish confirmation/stale-preview gates, Needs Review selection, representative redirects, and runtime degraded/error states.
- Superseded: any plan that reintroduces pipeline-specific status language or treats readiness as a write-capable or scanning endpoint.
- Depends on: runtime readiness, shared shell primitives, and route-level contracts.
- Safety boundaries: readiness stays read-only, no health scan runs during render, and no auto-fix behavior is hidden behind the contract.

### Phase 2 — Navigation and command-center Home

Current status: `PARTIAL`

- Exists now: consolidated sidebar sections, specialist deep links, and legacy redirects; the root route still mounts Library, not a command-center Home.
- Remains: a true Home snapshot, bounded recommended-next-action logic, and workspace-aware health summaries that do not pretend to be actions.
- Superseded: module-era top-level navigation and the old dashboard/collection/tracks route story as primary navigation.
- Depends on: read-only summary endpoints and stable route contracts.
- Safety boundaries: Home must not run broad scans or auto-execute commands.

### Phase 3 — Library workspace refactor

Current status: `SUBSTANTIALLY IMPLEMENTED`

- Exists now: a componentized `LibraryView`, toolbar/filter/table/inspector splits, server-side paging/filter/sort, persisted UI state, runtime status strip, and track detail/compatibility surfaces.
- Remains: URL-backed reproducible views, richer provenance/history inspector behavior, and any remaining refinement needed to make the view truly shareable and replayable.
- Superseded: the old monolithic track browser and any assumption that inline browsing state is good enough for reproducible workflow links.
- Depends on: track APIs, read-only stats/overview, and the current inspector/virtualization stack.
- Safety boundaries: browsing stays read-only; selected-track actions remain links to review or explicit workflows, never silent apply.

### Phase 4 — Unified Fix & Review center

Current status: `PARTIAL`

- Exists now: Needs Review aggregation, specialist review pages for quality, enrichment, metadata repair/sanitation, genre taxonomy, and BPM/analysis, plus shared badge/strip/empty-state UI patterns.
- Remains: a truly unified lifecycle and result contract, consistent diff/provenance presentation, and safe bulk behaviors that never blur approval with execution.
- Superseded: returning to isolated silos that imply one queue can auto-apply another queue's decisions.
- Depends on: queue adapters, provenance data, and the shared operation/result vocabulary.
- Safety boundaries: review remains review-first, apply remains separately gated, and BPM/key/cues stay protected.

### Phase 5 — Duplicate, orphan, path, and quarantine workflows

Current status: `SUBSTANTIALLY IMPLEMENTED`

- Exists now: DB-only duplicate review state, the plan-only duplicate resolution view, reconciliation findings, quarantine listing, and the reviewed DB-only reconciliation APPLY/rollback surface with ledger and backup checks.
- Remains: queue/reference artifact reconciliation follow-up, the later separately reviewed filesystem mutation phase, and the future reversible duplicate execution path.
- Superseded: permanent delete workflows, silent move/rename/quarantine behavior, and any implied filesystem mutation inside the current planning surfaces.
- Depends on: the neutral path-reconciliation engine, the ledger/backup services, and root-containment checks.
- Safety boundaries: no delete defaults, no blind mutation, and no attempt to bundle filesystem mutation into the current duplicate or reconciliation surfaces.

### Phase 6 — Set Builder improvements

Current status: `PARTIAL`

- Exists now: a functional Set Builder page, queued job execution, dry-run support, and job/log visibility.
- Remains: a richer draft/reorder/notes/readiness workspace and clearer saved-set ergonomics built from the current data model.
- Superseded: any description that reduces Set Builder to a CLI wrapper or invents compatibility claims that the data does not support.
- Depends on: playlist/set APIs, job history, and the available BPM/key/duration fields.
- Safety boundaries: no fabricated energy or compatibility score, and no Rekordbox performance-data mutation.

### Phase 7 — Publish: export and SSD synchronization

Current status: `SUBSTANTIALLY IMPLEMENTED`

- Exists now: readiness checks, export preview/confirm/verify, sync preview/confirm/verification, separate operation history, and the explicit destination model.
- Remains: workflow polish and any missing audit/reporting improvements, not a new mutation model.
- Superseded: blind export, rsync `--delete` defaults, or collapsing export and sync into one opaque action.
- Depends on: readiness, job/audit contracts, and workspace-derived source resolution.
- Safety boundaries: destination stays explicit, preview comes before confirm, and verification remains distinct from dispatch.

### Phase 8 — Operations, jobs, audit history, and reconciliation

Current status: `SUBSTANTIALLY IMPLEMENTED`

- Exists now: jobs, analysis history, publish history, reconciliation ledger, preflight/runtime checks, and other operation histories that make current work observable.
- Remains: a cross-workflow operation correlation model and a more unified result vocabulary across the major workflows.
- Superseded: treating jobs as the only record of operations or pretending a completed mutation has no audit trail.
- Depends on: the job tables, publish/reconciliation/analysis histories, and the existing preflight/read-only surfaces.
- Safety boundaries: no fabricated progress and no secret leakage through logs or prompt traces.

### Phase 9 — Accessibility, responsive behavior, and performance

Current status: `PARTIAL`

- Exists now: responsive CSS, semantic controls, virtualization, loading/error states, and keyboard-friendly interactions in the main routes.
- Remains: automated a11y/performance validation, stronger mobile and touch support, and browser-backed verification of the workflows that matter.
- Superseded: cosmetic polish that ignores evidence-based performance or accessibility work.
- Depends on: stable routes/components and the frontend test foundation.
- Safety boundaries: performance work must not bypass confirmation or move scans into render.

### Phase 10 — End-to-end hardening and release readiness

Current status: `PARTIAL`

- Exists now: strong backend regression coverage, supported-route contract guards, and a route/component architecture that can support a release gate.
- Remains: deterministic end-to-end fixture flow and a release checklist that reflects the actual current contracts; the focused frontend unit/component harness is now in place.
- Superseded: claims of release readiness based on backend coverage alone.
- Depends on: the contract/test foundation and the stable route/status model.
- Safety boundaries: only repository-controlled fixtures belong in hardening work; no real library, SSD, or Rekordbox DB should be used.

## Current Roadmap

The forward-looking work is organized around current product gaps, not the old phase numbering.

### A. Contract & Test Foundation

Current status: `PARTIAL`

- Frontend test foundation: Vitest 1.x (compatible with the current Vite 5 stack), React Testing Library, jest-dom, and jsdom. The non-interactive `npm --prefix frontend run test` check covers Publish safety gates, Needs Review selection, representative legacy redirects, and readiness degraded/error rendering with mocked frontend APIs.
- Shared operation/result contract inventory across Process All, BPM/key analysis, waveform, tag write, reconciliation, Publish/export/sync, and jobs is defined in `docs/architecture/OPERATION_RESULT_CONTRACT.md`. Implementation convergence (read adapters, shared outcome derivation, frontend type normalization) remains future work.
- Mutating-route contract coverage for confirmation gates, stale-preview invalidation, review state, and degraded/error states.

### B. Product Navigation / Command-center Home

Current status: `PLANNED`

- Real Home instead of a Library index route pretending to be the command center.
- Accurate workspace-aware metrics and recent activity, derived from bounded read-only sources.
- A bounded recommended-next-action model that can inform but never auto-run actions.

### C. Library & Review Convergence

Current status: `PARTIAL`

- URL-backed, reproducible Library views if the current local-storage state remains insufficient.
- Provenance/history inspector improvements that show current value, source, and review context without hiding the underlying data.
- Review semantics convergence where current queues can share terminology without losing their safety differences.

### D. Safe Destructive-capable Workflows

Current status: `PARTIAL`

- Queue/reference reconciliation work first, with filesystem mutation intentionally kept out of the first follow-up.
- Reversible duplicate execution only after separate safety design, revalidation, backup, collision checks, and restore-path proof.
- No permanent delete path and no automatic filesystem mutation in the current planning surfaces.

### E. DJ Intelligence

Current status: `PARTIAL`

- Set Builder refinement around drafts, ordering, notes, readiness, and honest warnings.
- Harmonic/intelligence follow-up work after the current deterministic foundation.
- No fabricated compatibility or energy model.

### F. Hardening / Release Readiness

Current status: `PARTIAL`

- Accessibility and responsive behavior validation, especially where keyboard and touch workflows differ.
- Frontend test coverage, route smoke checks, and deterministic fixture-based end-to-end coverage.
- Dependency maintenance and release-gate work that preserves the current local-first, no-auth posture.

## Dependency Shape

```text
A -> C -> D
 \    \    \
  \    -> E -> F
   \
    -> B
```

- Contract/test foundation is the main enabling layer for any broader release hardening.
- Library and review convergence can continue while the navigation/Home story is still being clarified.
- Safe destructive-capable workflows must keep queue/reference handling separate from later filesystem mutation.
- DJ intelligence remains downstream of the current library/review contracts.

## Model Allocation

- Use a cheaper Codex model for mechanical wiring, typed-client boilerplate, repetitive component extraction after contracts are fixed, fixture tests, route wiring, documentation formatting, and routine verification.
- Use a stronger reasoning model for operation/status contracts, path and duplicate safety, review lifecycle unification, publish/export/sync orchestration, DB migrations, performance budgets, accessibility architecture, baseline hang diagnosis, and final release readiness.
