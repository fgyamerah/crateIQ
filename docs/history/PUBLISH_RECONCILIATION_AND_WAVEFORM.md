# Publish, Reconciliation, Waveform, and Foundation Work

Earlier cycles that shaped the current architecture, predating the Managed
Library program (`docs/history/MANAGED_LIBRARY_CYCLES_9_13.md`). None
merged to main from their feature branches unless noted.

## Foundation (2026-07-14 to 2026-07-24)

CrateIQ fork foundation, identity rename, and baseline technical/product
audit (`AUDIT_REPORT.md`, now `docs/archive/AUDIT_REPORT.md`) landed on
`feat/crateiq-foundation-audit`. Diagnosed and resolved a baseline FastAPI
health-test hang: a restricted execution sandbox failed to wake a
cross-thread asyncio event loop required by AnyIO/Starlette TestClient —
host verification passed; no application fix was needed. Backend test
environment stabilized (isolated pipeline/backend library root per test
run, 860 tests passing twice under Python 3.12). Implemented a local-
runtime preflight (`backend/app/core/preflight.py`) and
`GET /api/runtime/readiness`, plus a dismissible frontend readiness
banner. `docs/strategy/CRATEIQ_PRODUCT_VISION_AND_ROADMAP.md` documented
the target 9-phase capability roadmap.

## Cycle 2 — Persisted Analysis Jobs History (2026-08-07)

Three stages on `main`-adjacent work. New app-owned `analysis_operations`
table in the backend's own `jobs.db` (never `processed.db`) records every
explicit, confirmed BPM/key analysis run with a real running/completed/
failed/cancelled lifecycle and genuine mid-batch cancellation via a
polled flag. A backend restart closes out any stranded 'running' row as
failed/backend_restarted. Fixed a concurrency bug found by the
cancellation tests: the run route awaited the blocking runner directly,
holding the event loop for the whole batch — dispatched via
`run_in_threadpool` instead. Stage 3 built the Jobs page's "Analysis
history" table + detail rail. 1347 backend tests pass by the end of Cycle 2.

## Cycle 3 — Guided Publish, Export, and SSD Sync (roadmap Phase 7) (2026-08-07)

Branch `feat/crateiq-publish`, 5 stages. `GET /api/publish/readiness/
{crate_id}` composes existing crate export services and SSD sync config
into one read-only readiness snapshot. `publish_export_service.py` unifies
the portable/Rekordbox-XML/Serato exporters behind
validate → preview → confirm → execute → verify. `publish_sync_service.py`
does the same for SSD sync on the existing `rsync_runner`; the guarded
confirm request schema has no `allow_delete` field at all. Found and fixed
a real pre-existing bug: the dry-run parser never recognized rsync's
`--no-inc-recursive` header, so every preview (old and new) silently
reported zero pending files. New `/publish` guided workspace ties it all
together. Final safety audit: 22 files changed vs main (+2,830/-9), all 88
sanctioned audio files byte-identical before/after, 1378 backend tests
pass, `validate-docs --strict` and `git diff --check` both clean.

## Cycle 4 — Duplicate, Orphan, Quarantine, and Plan-First Reconciliation (2026-08-07)

Branch `feat/crateiq-library-reconciliation`, 5 stages. Duplicate groups
gained safe evidence (genre/bpm/key/duration/format, missing-metadata,
copy-marker) and an advisory-only keeper recommendation (deterministic
only when a filename copy-marker unambiguously identifies one canonical
file). New read-only findings (`indexed_missing_file`, `untracked_file`,
`stale_path`, `path_candidate`) and a quarantine listing, reshaping the
existing `pipeline.py` path-audit engine rather than reimplementing
detection. `POST /reconciliation/plans/propose` performs DETECT → PROPOSE
by reusing `pipeline._path_reconcile_plan()` unchanged; `validate-plan`
gained plan-wide `target_path_collision`/`ambiguous_candidate_for_old_path`
checks. Found and fixed a real pre-existing bug:
`_path_reconcile_validate_action` flagged every `update_path_reference`
action as invalid via `old_path_missing_on_disk`, even though that action
type exists specifically because old_path is missing — no generated plan
could ever have validated as valid before this fix. `/reconciliation`
became one tabbed "Library Reconciliation" workspace.

**Apply remains fully deferred by design** — no apply/execute endpoint
exists anywhere in this cycle; `_path_reconcile_apply_auto_safe`/
`_path_reconcile_mark_stale_pstate` stay unreachable from the API. Full
branch: 22 files, +2,170/-262, 1401 backend tests pass, live DETECT→
PROPOSE→VALIDATE pass against the real sanctioned library confirmed a
truthful 0-finding empty state. Designing and testing a real reconciliation
APPLY workflow (backups, per-action confirmation, restore path) stays a
separate future task — see `docs/architecture/FULL_RECONCILIATION_APPLY_SPEC.md`
and `NEXT_TASKS.txt`.

## Waveform Phases W1–W8 (2026-08-05 to 2026-08-06)

Branch `feat/crateiq-waveform-jobs`. Designed (`docs/architecture/
WAVEFORM_ARCHITECTURE.md`) and built real backend waveform extraction and
frontend rendering end to end, phase by phase:

* **W1** — state/capability foundation, conservative config, `waveform_
  track_state`/`waveform_jobs` tables. No audio tool executed yet.
* **W2** — safe extractor wrapper: fixed read-only FFmpeg descriptor/
  probe/decode path, bounded peak accumulator, argv-only no-shell
  subprocess supervisor with cancellation/timeout. No cache/API yet.
* **W3** — cache and API: explicit generation POST, side-effect-free GET,
  job status/cancel, atomic gzip-JSON cache publication, ETag/304 support,
  bounded single-worker scheduler, restart recovery. Fixed a pre-existing
  jobs.db fsync cost (~372ms → ~15ms per commit).
* **W4** — frontend real waveform presentation: DPR-aware canvas, explicit-
  only generation (verified zero automatic POSTs), stale-response abort on
  track change.
* **W5** — waveform seeking and accessibility: native range input overlaid
  directly on the waveform visual, one accessible seek slider (replaced,
  not duplicated), 5s keyboard step.
* **W6** — lifecycle/cleanup/resource controls: tiered cleanup, 2 GiB → 80%
  LRU pruning, ordered idempotent scheduler shutdown, 30-day retention for
  terminal job rows.
* **W7** — controlled browser/performance verification against 7 of 88
  real library files: RTF 108x–178x, cached GETs 11–30ms, real cancellation
  killed the decoder in 46ms with no partial artifact, all 7 sources
  byte-identical afterward.
* **W8** — documentation and safety audit (source-level, non-generative):
  zero blockers found across a 16-commit/62-file arc. GO for merge after
  normal human review.

Follow-up work reused the same pipeline: waveform generation UX (empty-
state `EmptyWaveform` component replacing the decorative `ThreeBandWaveform`
placeholder in Track Inspector and the Persistent Player) and bulk
"Generate missing waveforms" jobs (`waveform_bulk_service`, one track
submitted at a time through the existing bounded scheduler, restart
recovery) landed 2026-08-07, plus a Jobs-page Waveform Generation card.

## Visual system rollout and UX polish (2026-07-25 to 2026-08-07)

The Library view's emerald/teal/cyan/violet dark-dashboard visual system
(`docs/mockups/library.webp`) was extended app-wide via shared CSS tokens/
primitives (`StatusStrip`, `KpiCard`, `EmptyState`, `Badge`) rather than
per-page redesigns, reaching Settings, Jobs, Quality Review, Beets Review,
Manual/Smart Crates, Metadata Repair, Genre Taxonomy, SetBuilder, and
Reconciliation over several sessions. A persistent Spotify-style bottom
player, real Camelot wheel graphic, and a per-track "compatible tracks"
harmonic-match API were added in the same arc. Crate/set-building gained a
deterministic harmonic+BPM transition read between consecutive Manual
Crate tracks and a Smart Crates filter funnel, both read-only annotations
that never change eligibility or order.

## Earlier pipeline-era work (2026-04 and prior)

Before the FastAPI/React application existed, `pipeline.py` and its
`modules/`/`ai/`/`intelligence/` packages were built up through dated,
now-completed work: metadata sanitation and artist-repair rules, the
online enrichment package (Spotify/Deezer/Traxsource matching with
multi-factor validation gates), the artist-intelligence alias/normalization
package, local Ollama AI normalization with hard guardrails, SSD sync/job
cancellation/progress infrastructure, crate export foundations (portable/
Rekordbox XML/Serato), and the doc-registry-driven `generate-docs`/
`validate-docs` system. Full detail for this era lives in `CHANGELOG.txt`;
no currently-open work depends on the specifics being reproduced here.
