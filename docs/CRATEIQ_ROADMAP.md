# CrateIQ Product and Engineering Roadmap

Status: proposed after Phase 0 audit
Important: Phases 1–10 are not implemented by this commit.

## Roadmap rules

- Preserve local-first, review-first, dry-run-first behavior.
- Never silently write tags, rename/move files, delete audio, change a real Rekordbox DB, or change BPM/key/cues/beatgrids/performance metadata.
- Every mutation must identify scope, current value, proposed value, safety level, confirmation requirement, operation ID, and verification result.
- A proposal is not an approval. An approval is not an apply. An apply is not a verification.
- Use repository-controlled temporary fixtures only for development, tests and screenshots.
- Do not describe planned behavior as current behavior in UI or documentation.
- Cheaper model = routine implementation, mechanical wiring, test expansion and doc updates after contracts are decided. Stronger reasoning model = state-machine design, safety boundaries, data migrations, performance decisions, complex UX and release review.

## Cross-phase product contracts

### Shared lifecycle

```text
Detected → Classified → Proposed → Reviewed → Approved → Applied → Verified
```

Side states: `rejected`, `deferred`, `blocked`, `skipped`, `failed`, `cancelled`. Review status and operation status must be separate fields.

### Shared operation result

Every future operation result should expose: `operation_id`, `operation_type`, `mode` (`read`, `preview`, `dry_run`, `apply`), `scope`, `counts`, `warnings`, `blockers`, `artifacts`, `affected_item_ids`, `confirmation_required`, `verification_status`, and `error_code`/`reason_code` where applicable.

### Shared safety levels

- Safe read: no writes to library, tags, pipeline DB or queues except bounded cache/report output.
- Review write: records a human decision or proposal edit only.
- Controlled apply: narrow, explicitly confirmed mutation with an allowlist and postcondition check.
- High-risk operation: file move, tag write, export overwrite, external SSD sync or path update; requires a dedicated preview and confirmation.
- Unsupported: no control, no fake progress, no implied capability.

## Phase 0 — Clone, rename, baseline, audit, and roadmap

Status: completed in this foundation commit.

- Goal: create a safe CrateIQ fork and establish evidence for future work.
- User value: a trustworthy starting point with clear boundaries and no accidental upstream push.
- Reused: upstream code, tests, docs, existing safety model, pipeline DB, backend and frontend.
- Backend: prefer `CRATEIQ_LIBRARY_ROOT`; retain `CRATEMINDAI_LIBRARY_ROOT` fallback.
- Frontend: update active branding and package metadata only; no redesign.
- Database: no schema migration; preserve DB paths and serialized formats.
- API: update FastAPI title/description only; preserve paths.
- Safety constraints: no real library access, no apply pipelines, no major feature changes.
- Tests: run documented Python suite; add fixture-based env alias tests.
- Documentation: audit, IA, design direction, roadmap, README/context/task/changelog/commands updates.
- Acceptance: clone exists, upstream is safe, branch exists, baseline is recorded, docs distinguish current/planned, local commit exists, tree is clean.
- Dependencies: upstream clone and repository instruction review.
- Risks: baseline backend health test hangs; npm dev advisories remain documented.
- Out of scope: all redesign and roadmap implementation.
- Suggested commits: this commit only: `chore: establish CrateIQ foundation and product roadmap`.
- Model: cheaper model is sufficient for bounded rename/doc work; stronger review is recommended for verifying safety claims.

## Phase 1 — Foundation and design system

- Goal: establish shared contracts, tokens, shell primitives and runtime readiness.
- User value: consistent terminology, predictable actions, and an honest readiness state before users operate on a library.
- Existing functionality reused: current `Layout`, `Sidebar`, `PageHeader`, `StatusBadge`, `ErrorBanner`, API client, health/version endpoints and existing CSS tokens.
- Backend changes: add a versioned readiness/preflight service; normalize error responses; expose operation/result/status vocabulary without changing mutation semantics.
- Frontend changes: split shell primitives, typed API client, status/operation components, confirmation/preview components, standard loading/error/empty/degraded states.
- Database changes: none initially; add only additive operation correlation fields if evidence requires them.
- API changes: define `/api/runtime/readiness` or extend health with backward-compatible fields; keep `/api/health` shape stable.
- Safety constraints: readiness must not scan a real library expensively or auto-fix config; controls remain disabled when prerequisites are unknown.
- Tests: response contract tests, schema tests, error mapping, path fixture tests, confirmation state tests, visual/token snapshots if adopted.
- Documentation: runtime setup, env precedence, status glossary, operation safety guide.
- Acceptance criteria: no active page invents a status term; every mutation has preview/apply/verify labels; readiness distinguishes ready, degraded, blocked and unknown; old API clients still work.
- Dependencies: Phase 0 audit; no major route migration required.
- Risks: expanding health into an expensive scan; accidental breaking API changes; token rewrite churn.
- Out of scope: new library capabilities, auth, remote deployment, broad CSS redesign.
- Suggested commits: `feat: add runtime readiness contract`; `refactor: add shared operation states`; `style: establish CrateIQ design tokens`.
- Model: cheaper model for mechanical component extraction; stronger reasoning model for result/status contracts and safety review.

## Phase 2 — Navigation and command-center Home

- Goal: replace module-oriented entry navigation with the six user-goal sections and a useful Home.
- User value: users can see what is healthy, what needs attention, and what to do next without knowing pipeline internals.
- Existing functionality reused: library overview/quality, issue counts, enrichment summary, jobs, audit, export validation and sync config endpoints.
- Backend changes: compose a bounded command-center snapshot from existing read-only services; return readiness, counts, blockers, recent jobs and recommended next action with source timestamps.
- Frontend changes: mount Home, consolidate sidebar into Home/Library/Fix & Review/Sets/Publish/Operations, add actionable health cards and recent activity; keep legacy redirects.
- Database changes: none unless recent-activity queries need additive indexed fields; do not migrate existing stores speculatively.
- API changes: add a read-only dashboard summary endpoint with bounded query work and explicit freshness metadata.
- Safety constraints: cards are not action authorization; a blocked/unknown state must not become a green success; recommendations cannot auto-run commands.
- Tests: endpoint composition, empty/degraded states, redirect tests, keyboard nav, Home action-link tests, fixture snapshots.
- Documentation: supported route table, Home data freshness, recommended-action semantics.
- Acceptance criteria: Home shows selected library, readiness, health, pending reviews, missing/untracked/duplicates, export/sync readiness, recent jobs/changes/failures and one honest next action; no unsupported button appears.
- Dependencies: Phase 1 contracts; existing read endpoints.
- Risks: dashboard endpoint causing expensive scans; misleading stale metrics; overly dense cards.
- Out of scope: fixing underlying data quality, new automation, mobile redesign beyond basic shell behavior.
- Suggested commits: `feat: add command-center summary`; `feat: mount CrateIQ Home`; `refactor: consolidate navigation and redirects`.
- Model: cheaper model for route wiring and card composition; stronger model for snapshot freshness, next-action rules and UX review.

## Phase 3 — Library workspace refactor

- Goal: turn the current track workspace into a scalable Library with reusable data and inspector components.
- User value: fast, understandable browsing of large libraries with evidence for every track decision.
- Existing functionality reused: `/api/tracks`, filters, sorting, pagination, virtualized rows, selected track detail, folders and persisted UI state.
- Backend changes: audit indexes and query plans; add server-side filter/sort contracts, cursor pagination only if measured necessary, provenance/history read services and bounded folder context.
- Frontend changes: split `CrateMind.tsx` into data hooks, table, toolbar, saved views, inspector drawer, issue badges and history/provenance panels; URL-back all filters; add column controls and keyboard model.
- Database changes: additive indexes only after measurements; no path identity migration in this phase.
- API changes: typed pagination/filter/sort envelopes, provenance/history endpoints if available from existing artifacts, stable track detail schema.
- Safety constraints: library browsing remains read-only; no page render may run a broad filesystem scan; selected-track actions link to review, never silently apply.
- Tests: pagination/filter/sort, cancellation/debounce, stale selection, large fixture rendering, keyboard table navigation, inspector states, API error/empty/degraded states.
- Documentation: Library route contract, saved-view semantics, large-library performance notes.
- Acceptance criteria: server-side search/filter/sort works; URL can reproduce a view; table remains usable with large fixtures; inspector shows current/proposed/provenance/history separately; no unsupported inline edit is presented.
- Dependencies: Phase 1 typed client; Phase 2 route shell.
- Risks: offset performance, state duplication during extraction, inaccessible virtualization, overloading the inspector.
- Out of scope: applying metadata, duplicate resolution, new scans, waveform/energy generation.
- Suggested commits: `refactor: split library workspace`; `feat: add URL-backed library views`; `feat: add track provenance inspector`.
- Model: stronger reasoning model for state extraction, query contracts and accessibility; cheaper model for repetitive component/test migration after contracts are fixed.

## Phase 4 — Unified Fix & Review center

- Goal: provide one review center for metadata repair, sanitation, enrichment, BPM anomaly review and supported AI proposals.
- User value: users learn one safe review flow and can see exactly what is proposed, why, from which source, and what applying it would change.
- Existing functionality reused: current queues, approve/reject/defer endpoints, proposal edits, dry-run/apply-approved services and confidence thresholds.
- Backend changes: introduce an adapter/common queue model over existing queue stores; normalize reason codes, source, confidence, safety level and lifecycle; preserve current storage formats.
- Frontend changes: queue tabs/filters, difference viewer, provenance panel, confidence/reason/safety display, bulk review, preview apply summary, apply eligibility and verification results.
- Database changes: none unless an additive cross-queue decision index is required; preserve JSON/JSONL compatibility.
- API changes: add a common read model and operation preview envelope; keep existing queue endpoints for compatibility.
- Safety constraints: approval only records intent; apply requires explicit scope and confirmation; BPM/key/cue fields stay protected; AI/provider output remains non-authoritative.
- Tests: adapter parity, lifecycle transitions, field allowlists, apply confirmation, dry-run/apply idempotency, verification failure, bulk selection, queue isolation.
- Documentation: review lifecycle, ownership matrix, provider/privacy behavior, apply eligibility rules.
- Acceptance criteria: all supported queues share terminology; every row can show current/proposed/source/reason/confidence; bulk review cannot bypass eligibility; apply preview lists exact fields/items; verification is visible.
- Dependencies: Phases 1–3; existing queue APIs and tests.
- Risks: common model flattening meaningful queue differences; accidental apply widening; provider data leakage.
- Out of scope: automatic approval, automatic tag writes, changing BPM/key/cues, new external providers.
- Suggested commits: `feat: add unified review read model`; `feat: add review difference viewer`; `feat: add guarded apply verification`.
- Model: stronger reasoning model required for lifecycle and safety normalization; cheaper model for adapters and repetitive UI once contract is approved.

## Phase 5 — Duplicate, orphan, path, and quarantine workflows

- Goal: make path and duplicate findings reviewable and planning-first, with a visible quarantine browser.
- User value: users can understand file/path conflicts and choose safe next actions without permanent deletion or blind reconciliation.
- Existing functionality reused: path audit reports, candidate scoring, reconciliation plans/ledger validation, duplicate groups, quarantine directories and root-containment tests.
- Backend changes: expose normalized finding types, side-by-side file properties, match reasons, candidate keeper recommendation, plan artifacts and quarantine records; add safe restore preview only where supported.
- Frontend changes: review tabs for duplicates/orphans/quarantine; side-by-side comparison; missing/untracked/stale/path candidate views; plan/preview/confirm/verify surfaces; restore eligibility.
- Database changes: additive read indexes or quarantine metadata only when backed by an existing artifact; do not invent permanent deletion state.
- API changes: read-only finding endpoints and plan/preview endpoints; no broad apply endpoint until separately specified and tested.
- Safety constraints: no delete audio button; no silent move; no automatic path match; every proposed keeper/match has confidence and reason; restore must remain bounded under root.
- Tests: duplicate comparison, no-delete invariant, containment, collision handling, stale path isolation, plan validation, idempotency and fixture-only quarantine.
- Documentation: quarantine contract, restore limitations, reconciliation plan semantics, “unsupported” guidance.
- Acceptance criteria: every finding links to evidence; proposals are planning-only by default; no UI implies permanent deletion; quarantine records show source, destination, reason, operation and time; invalid plans are blocked.
- Dependencies: Phases 1–4; existing audit/reconciliation artifacts.
- Risks: false matches, path drift, collision data loss, stale audit reports.
- Out of scope: broad automatic reconciliation, permanent deletion, general file manager behavior.
- Suggested commits: `feat: expose duplicate and orphan findings`; `feat: add quarantine browser`; `feat: add plan-first path review`.
- Model: stronger reasoning model required for path/duplicate safety; cheaper model for tables and fixture tests after invariants are set.

## Phase 6 — Set Builder improvements

- Goal: make Sets a real draft/planning workspace using only real, owned data.
- User value: users can assemble, order and annotate a set while seeing duration, BPM progression and known warnings without invented compatibility.
- Existing functionality reused: `set_playlists`/`set_playlist_tracks`, playlist service, pipeline set-builder, track API and available BPM/key/duration fields.
- Backend changes: add draft/save/reorder/notes/readiness services around existing set records; calculate only values supported by current data; expose missing-data warnings.
- Frontend changes: saved sets/drafts, track picker, ordering, duration, BPM/key display where real, genre balance, transition notes, readiness and honest warnings.
- Database changes: additive draft/status/notes fields only with a migration plan; preserve existing saved set rows and positions.
- API changes: typed set CRUD/reorder/readiness endpoints; export only if an existing supported output is used.
- Safety constraints: do not fabricate energy, waveform, compatibility or readiness; do not modify Rekordbox performance data; set planning does not imply export success.
- Tests: ordering/duration, missing BPM/key behavior, duplicate track handling, draft persistence, readiness warnings, API idempotency and keyboard interaction.
- Documentation: set data ownership and unsupported signals; export limitations.
- Acceptance criteria: drafts save safely; order and notes persist; duration is deterministic; missing/unknown values are explicit; no unsupported compatibility score is shown.
- Dependencies: Phases 1–3; existing playlist tables/services.
- Risks: schema drift, overclaiming DJ intelligence, confusing generated set with user-approved set.
- Out of scope: waveform rendering, new audio analysis, AI set selection, automatic Rekordbox cue/grid edits.
- Suggested commits: `feat: add set drafts and ordering`; `feat: add set readiness`; `refactor: expose honest set warnings`.
- Model: stronger reasoning model for data ownership and readiness semantics; cheaper model for CRUD/UI wiring.

## Phase 7 — Publish: export and SSD synchronization

- Goal: provide one guided publish flow from validation to verified export and sync.
- User value: users know what is blocked, what will be written, where it will go, and whether the result was verified.
- Existing functionality reused: export validation, Rekordbox export jobs, sync config, rsync preview/run, job logs and MIK-first flags.
- Backend changes: compose publish readiness, blockers, dry-run artifacts, destination/conflict summaries and verification results; ensure named source/destination validation remains strict.
- Frontend changes: wizard steps Validate → Blockers → Dry run → Preview → Confirm → Export → Verify → Sync preview → Confirm sync → Verify destination; show XML default/force semantics and conflicts.
- Database changes: use existing jobs/audit artifacts; add only operation linkage fields if necessary and additive.
- API changes: add publish orchestration read/preview endpoints over existing export/sync APIs; preserve existing endpoints and flags.
- Safety constraints: no default apply, no implicit XML force, no destructive rsync delete behavior, no real SSD in fixtures, explicit destination and confirmation every time.
- Tests: validation blockers, preview determinism, confirmation gates, conflict lists, destination containment, job linkage, failed verification and fixture end-to-end flow.
- Documentation: publish safety checklist, export ownership, sync conflict/verification behavior, no-auth warning.
- Acceptance criteria: a publish cannot skip blockers or preview; export and sync are visibly separate operations; every write has confirmation and job/audit link; verification can fail visibly; unsupported outcomes are not green.
- Dependencies: Phases 1, 2, 4 and 8 job/audit contracts; existing export/sync services.
- Risks: external-media mutation, rsync semantics, stale validation, user confusion between export and sync.
- Out of scope: remote publishing, Rekordbox DB mutation, silent overwrite/delete, automatic conflict resolution.
- Suggested commits: `feat: add publish readiness`; `feat: add export preview and verification`; `feat: add sync preview and confirmation flow`.
- Model: stronger reasoning model required for safety-critical orchestration and confirmation flows; cheaper model for step UI and API adapters after contracts are approved.

## Phase 8 — Operations, jobs, audit history, and reconciliation

- Goal: make every operation observable and connect jobs, audit artifacts, plans, decisions and verification.
- User value: users can answer what ran, when, against which scope, with what result, and what changed.
- Existing functionality reused: jobs DB, logs, process registry, audit reports, reconciliation ledger, run summaries, preflight and provider/config status.
- Backend changes: add operation correlation and structured result summaries; improve reliable progress/cancel semantics; expose audit timeline, logs, storage/DB/provider health and plan status.
- Frontend changes: Operations dashboard with Jobs, Audit, Reconciliation, Runtime, Providers, Logs, Storage and Database; affected-item links and failure next actions.
- Database changes: additive operation metadata/correlation tables only after a migration design; preserve jobs DB and pipeline DB ownership boundaries.
- API changes: structured operation detail and audit timeline endpoints; stable status/result schemas; avoid returning false success on missing artifacts.
- Safety constraints: logs must avoid secrets/private prompts by default; cancellation must remain best-effort; no fabricated progress; auth remains a prerequisite for remote use.
- Tests: job state transitions, cancellation, structured result parsing, audit correlation, failed/missing log behavior, ledger validation, secret redaction and preflight isolation.
- Documentation: operations model, log retention/privacy, reconciliation ledger, trusted-local-only deployment posture.
- Acceptance criteria: every queued operation has stable ID/status/mode/scope/result; failures are visible with next action; audit timeline distinguishes review/apply/verify; structured data links to artifacts without exposing secrets.
- Dependencies: Phase 1 operation contract and Phase 7 publish linkage.
- Risks: cross-database consistency, log privacy, progress claims, accidental remote exposure.
- Out of scope: authentication implementation unless separately approved, distributed workers, cloud telemetry.
- Suggested commits: `feat: add structured operation results`; `feat: add operations audit timeline`; `refactor: harden job progress and cancellation`.
- Model: stronger reasoning model for cross-store operation identity and failure semantics; cheaper model for view wiring and tests.

## Phase 9 — Accessibility, responsive behavior, and performance

- Goal: make supported workflows usable with keyboard, assistive technology, tablet/mobile layouts and large libraries.
- User value: fewer interaction failures, faster browsing, and reliable operation on different screens and input methods.
- Existing functionality reused: current responsive CSS, semantic controls, virtualization, debounce, SQL paging, loading/error patterns.
- Backend changes: measure slow queries, audit indexes, add cancellation/timeouts, bound tree/scans, record timings and cache only safe immutable/read data.
- Frontend changes: mobile navigation, track cards, inspector drawer, responsive dialogs/tables, focus management, live announcements, reduced motion, minimum touch targets and accessible status components.
- Database changes: index changes only from measured query plans; no speculative denormalization.
- API changes: request cancellation and bounded query parameters; include freshness/timing where useful.
- Safety constraints: performance work must not bypass confirmation or move scans into render; caching must not hide state changes or stale blockers.
- Tests: keyboard/a11y automated checks, focus/dialog behavior, contrast, reduced-motion, responsive viewport tests, slow-query measurements, large-fixture performance and request cancellation.
- Documentation: WCAG 2.2 AA checklist, supported breakpoints, performance budgets and measurement method.
- Acceptance criteria: all core workflows pass keyboard and screen-reader smoke tests; statuses do not rely on color; tablet/mobile critical paths work; large fixtures meet agreed budgets; no expensive scan occurs during render.
- Dependencies: stable route/component architecture from Phases 2–8.
- Risks: accessibility regressions from virtualization/dialogs, over-optimization, unreliable synthetic budgets.
- Out of scope: visual trends that reduce clarity, decorative animation, replacing evidence with charts.
- Suggested commits: `feat: add accessible interaction primitives`; `feat: add responsive workflow layouts`; `perf: measure and harden large-library reads`.
- Model: stronger reasoning model for a11y/performance tradeoffs and validation; cheaper model for mechanical fixes guided by test output.

## Phase 10 — End-to-end hardening and release readiness

- Goal: prove the deterministic fixture flow and make release behavior reproducible and safe.
- User value: confidence that inspection, review, apply, export and sync boundaries behave as documented.
- Existing functionality reused: all safety tests, pipeline fixtures, backend route tests, frontend contracts, operation/audit model and setup docs.
- Backend changes: resolve the baseline health-test hang, harden startup/preflight, add release diagnostics, verify config/env compatibility and mutation allowlists.
- Frontend changes: route smoke tests, review-flow tests, publish wizard tests, degraded-state tests, visual/a11y regression checks and supported-route inventory.
- Database changes: migration dry-run/backup guidance if additive schema changes were approved; no destructive migrations.
- API changes: freeze/version stable contracts, deprecation headers/messages for legacy names/routes where useful.
- Safety constraints: run only against temporary/repository-controlled fixtures; no real library/SSD/Rekordbox DB; no `npm audit fix --force`; no auth claims before auth exists.
- Tests: full Python suite, backend route suite, frontend typecheck/build/test, deterministic end-to-end fixture flow, apply/no-apply file hashes, path containment, idempotency, export/sync preview, audit verification and clean-install checks.
- Documentation: release checklist, support matrix, known limitations, migration/deprecation notes and exact verification evidence.
- Acceptance criteria: clean setup is reproducible; all supported routes are tested; no unsupported controls; safety invariants pass; no unresolved critical/high release blocker; clean tree and reviewable changelog.
- Dependencies: Phases 1–9 and resolved backend baseline blocker.
- Risks: hidden environment coupling, flaky end-to-end tests, false confidence from mocks, dependency advisories.
- Out of scope: public deployment, remote multi-user support, speculative AI/RAG, broad data migrations.
- Suggested commits: `test: add deterministic CrateIQ fixture flow`; `chore: harden release diagnostics`; `docs: publish release readiness checklist`.
- Model: strongest reasoning model recommended for final release audit, safety review and go/no-go decision; cheaper model can execute mechanical test/doc updates once acceptance criteria are fixed.

## Dependency sequence

```text
Phase 0
  ↓
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5
                       ↘       ↘       ↘
                        Phase 6  Phase 7 → Phase 8
                                           ↓
                                      Phase 9 → Phase 10
```

Phase 6 can proceed after the Library contracts are stable. Phase 7 needs the shared operation/confirmation model and should not begin as a collection of isolated export/sync buttons. Phase 8 consumes publish/job linkage. Phase 9 should validate the composed product, not polish unstable page architecture.

## Model allocation summary

Use a cheaper Codex model for mechanical renames, typed-client boilerplate, component extraction after contracts are decided, repetitive test fixtures, route wiring, documentation formatting and routine verification. Use a stronger reasoning model for operation/status contracts, path and duplicate safety, review lifecycle unification, publish/export/sync orchestration, DB migrations, performance budgets, accessibility architecture, baseline hang diagnosis and final release readiness.
