# CrateIQ Project Context

**Updated:** 2026-07-15

**Purpose:** Canonical low-token engineering memory for future AI sessions.

## Latest Milestone

- 2026-07-24: Supported-route smoke-test contract added
  (`tests/test_supported_route_contracts.py`): all 14 supported frontend
  routes mapped to primary read-only backend endpoints, router-drift
  detection against `App.tsx`, missing-DB degradation checks, and a
  read-only/no-subprocess guard. Mutating endpoints are deliberately
  deferred (see `DEFERRED_ENDPOINTS` in the test file and NEXT_TASKS.txt).
- 2026-07-24: Local-runtime preflight and readiness contract implemented.
  `backend/app/core/preflight.py` runs read-only checks (library-root safety
  including unsafe-broad-root rejection with `CRATEIQ_ALLOW_UNSAFE_ROOT=1`
  override, pipeline DB presence/containment, `pipeline.py` entrypoint,
  backend data dir, and 7 optional external binaries) and
  `GET /api/runtime/readiness` reports `ready` / `degraded` / `not_ready`.
  `/api/health` is unchanged. `.env.example` expanded (non-secret template
  with no-auth warning). Tests: `tests/test_preflight.py`. Frontend
  readiness banner deferred to a follow-up task.
- 2026-07-24: crateIQ's target product vision is now documented in
  `docs/strategy/CRATEIQ_PRODUCT_VISION_AND_ROADMAP.md` — current-state vs
  target gap analysis, metadata/genre/harmonic/player/playlist/export
  strategy, and a 9-phase capability roadmap mapped to
  `docs/CRATEIQ_ROADMAP.md`. Documentation only; no runtime behavior changed.
  Recommended next implementation task: the local-runtime preflight and
  readiness contract (paste-ready prompt in that document's section 16).
- README updated for the Phase 1-8 CrateIQ platform milestone.
- Fork foundation commit: local CrateIQ branch `feat/crateiq-foundation-audit`.
- Backend test-suite hang diagnosis is complete; Phase 1 remains deferred until
  review of `docs/CRATEIQ_PRODUCT_AUDIT.md` and `docs/CRATEIQ_ROADMAP.md`.
- Warning: back up `<root>/logs/processed.db` before any reconciliation apply work.
- Repository audit completed on 2026-07-02; see `AUDIT_REPORT.md` for the current technical/product state and follow-up priorities.
- First post-audit stabilization completed on 2026-07-02: the frontend router
  and sidebar now expose only supported workflows.
- Supported frontend routes: `/`, `/quality`, `/issues`, `/enrichment`,
  `/metadata-repair`, `/metadata-sanitation`, `/bpm-review`, `/audit`,
  `/folders`, `/jobs`, `/set-builder`, `/exports`, `/sync`, and
  `/reconciliation`.
- Legacy `Dashboard`, `Collection`, and `Tracks` pages and placeholder
  `Settings` remain in source but redirect to `/`; `/export` and `/ssd-sync`
  are compatibility redirects.
- Frontend install, typecheck, and production build pass. Production npm
  dependencies audit clean after non-breaking updates; clearing the remaining
  development-tool advisories requires a separately tested Vite major upgrade.
- Backend development setup now uses `requirements-dev.txt` from a Python 3.10+
  virtual environment. It includes pipeline/backend dependencies, pytest,
  TestClient support, and a wheel-backed numba constraint.
- The original baseline collected 857 tests under Python 3.12, then stalled at
  `tests/test_backend_api.py::test_health_endpoint_reports_selected_root_and_db`.
  Diagnosis on 2026-07-15 proved that the restricted execution sandbox failed
  to wake the cross-thread asyncio event loop used by AnyIO/Starlette
  TestClient. A bare FastAPI reproduction and the identical CrateIQ stack pass
  outside the sandbox; the issue was pre-existing environment behavior, not the
  CrateIQ rename. The current suite collects 860 tests and passes twice in the
  normal host environment.
  Shared pytest setup isolates `DJ_MUSIC_ROOT` and the preferred
  `CRATEIQ_LIBRARY_ROOT` in temporary directories; the deprecated
  `CRATEMINDAI_LIBRARY_ROOT` fallback remains supported.
- `COMMANDS.md` is the canonical command reference. The former lowercase
  `commands.md` was moved to `docs/operations/LEGACY_DJ_TOOLKIT_COMMANDS.md`
  to remove the macOS case-insensitive checkout conflict while preserving its
  distinct historical content.
- `.env.example`, CrateIQ naming updates and compatibility tests are now
  present. Next recommended task: review and approve the roadmap, then begin
  Phase 1 only after that approval.

## Phase 7 Planning

- Phase 7 started as planning only.
- Full reconciliation apply spec created at `docs/architecture/FULL_RECONCILIATION_APPLY_SPEC.md`.
- No runtime behavior changed.
- No reconciliation apply behavior has been added.

## Overview

CrateIQ is a local-first DJ library automation toolkit. It processes audio files into a cleaner, Rekordbox-ready library through deterministic cleanup, local AI-assisted normalization, artist intelligence, online enrichment, label tooling, organization, dedupe, exports, and backend/UI workflows.

Detailed safety docs:

- `docs/audits/SAFETY_GAP_AUDIT.md`
- `docs/audits/COMMAND_RISK_MATRIX.md`
- `docs/audits/FILESYSTEM_DB_CONSISTENCY_AUDIT.md`
- `docs/safety/SAFETY_MODEL.md`
- `docs/safety/ROLLBACK_AND_RECOVERY.md`
- `docs/operations/OPERATOR_SAFETY_PLAYBOOK.md`
- `docs/architecture/METADATA_OWNERSHIP_MATRIX.md`
- `docs/architecture/STABILITY_MATRIX.md`

## Architecture Summary

- `pipeline.py` is the main CLI entry point.
- `modules/` contains core pipeline operations: metadata cleanup, analysis, organizer, dedupe, cue suggestions, playlists, exports, conversion, and audits.
- `ai/` contains local Ollama AI normalization and dataset capture.
- `intelligence/artist/` handles deterministic artist normalization and aliases.
- `intelligence/enrichment/` handles online metadata enrichment and review queues.
- `intelligence/label/` handles label parsing/enrichment/reporting.
- `backend/` is the FastAPI web backend with its own `jobs.db`.
- `frontend/` is the web UI; detailed safety was not inspected in this pass.

## Safety Doctrine

Prefer no change over unsafe change.

- Preview first.
- `--apply` should gate destructive changes, but this is not universal.
- Mixed In Key owns BPM, key, and cue data.
- AI must not write BPM, key, cues, filenames, or folder structure.
- File moves and renames require path reconciliation.
- Quarantine means review later, not delete.

## Verified Safety Facts

- `ai-normalize` default confidence constant is `MIN_AI_CONFIDENCE = 0.80`.
- Enrichment matcher uses `THRESHOLD_APPLY = 0.90` and `THRESHOLD_REVIEW = 0.75`.
- Enrichment artist/version hard blocks cap blocked confidence at `min(top_conf, 0.74)`.
- Enrichment exact ISRC match bypasses gates and returns confidence 0.98.
- Enrichment review queue dedupes by exact `file_path`.
- `config.IGNORED_DIR` is `.BIN/IGNORED`; enrichment preserves paths relative to `.BIN` parent and adds `_dupN` collisions.
- `rename_processed_path()` updates `processed_state` only.

## Major Operational Risks

- Some older write-capable commands still do not require `--yes` or `--force` confirmation.
- Legacy `organizer.py` still moves files and deletes old `tracks` rows through a pre-Phase-3 path mutation pattern.
- `artist-merge`, `artist-folder-clean`, and `library-organize` move files, but their Phase 3 paths now call `update_track_path_references()`.
- Most tag writes and file moves lack universal rollback.
- Review queues and DB tables are path-based and can go stale after renames/moves.

## Subsystem State

Use `docs/architecture/STABILITY_MATRIX.md` as the current authoritative subsystem status table.

## Documentation Maintenance

Any new command, destructive behavior, metadata mutation, queue change, schema change, or rollback change must update `docs/MAINTENANCE_POLICY.md` requirements and the relevant audit docs.

## Current System State (Post Phase 3)

Phase 3 is stable enough to proceed to Phase 4 planning and implementation. Phase 4 has started as a documentation and ownership-hardening phase; runtime behavior should remain conservative until the remaining legacy path and metadata mutation risks are explicitly migrated.

### Architecture summary

CrateIQ is now organized around a safer current-state model:

- `pipeline.py` remains the main CLI and command router.
- `processed_state` records stage/file processing history.
- `tracks` is being promoted to the canonical current-state track table.
- Path-audit and path-reconcile operate against an explicitly selected library root.
- Centralized DB path updates live in `db.update_track_path_references()`.
- `modules/organizer.py` is legacy/deprecated. Prefer `modules/library_organize.py` for Phase 3-safe organization paths.

### Canonical data flow

The intended current-state flow is:

1. Files exist under a selected library root.
2. Pipeline stages record processing outcomes in `processed_state`.
3. `build-tracks --root <root>` derives one canonical `tracks` row per valid, existing, non-stale processed-state path.
4. `path-audit --root <root>` uses `tracks.filepath` as canonical when `tracks` is populated, falling back to active non-stale `processed_state` when tracks is empty.
5. `path-reconcile --root <root>` plans repairs from audit findings. Full `--apply` remains intentionally unimplemented.

### Safety model

Current verified safety guarantees:

- `metadata-clean`, `tag-normalize`, `analyze-missing`, `convert-audio`, `cue-suggest`, `db-prune-stale`, and `review-queue` default to dry-run.
- Those commands require `--apply` plus `--yes` or `--force` before write behavior.
- `path-audit` is read-only except report/log files.
- `path-reconcile` planning is read-only except plan/log files.
- `path-reconcile --apply-auto-safe-only` updates only `processed_state.filepath` for auto-safe candidates.
- `path-reconcile --mark-stale-pstate` marks only eligible processed-state rows stale and does not change file paths.
- `update_track_path_references()` updates `tracks` and non-stale `processed_state` in one transaction and never modifies stale processed-state rows.
- Root-scoped Phase 3 commands use `<root>/logs/processed.db` and `<root>/logs/`.

### Command behavior

Command mode policy:

- Default is preview/dry-run for hardened write-capable commands.
- Apply mode must be explicit.
- Destructive or write-capable Phase 3-hardened commands require confirmation.
- `review-queue` defaults to list-only dry-run behavior; interactive queue mutation requires `--apply --yes`.
- `path-reconcile --apply` is not implemented; only narrowly scoped apply helpers exist.

### Known limitations

- `modules/organizer.py` still contains a legacy `tracks` upsert plus `DELETE FROM tracks` path-mutation pattern.
- Not every older write-capable command requires `--yes` or `--force` yet.
- Root isolation is stable for `path-audit`, `path-reconcile`, and `build-tracks`, but older commands still use global config-derived paths in places.
- Queue, cue, set, and historical log references remain path-based and can become stale after external moves.
- Reconciliation does not move files, update queues, update cue references, or implement full apply mode.
- Frontend and backend write behavior were not fully re-audited in Phase 3.
