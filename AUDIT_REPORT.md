# CrateIQ Audit Report

> Preflight update (2026-07-24): the "reproducible local-runtime preflight"
> recommended in §15 is now implemented: `backend/app/core/preflight.py`
> plus `GET /api/runtime/readiness` (read-only; ready/degraded/not_ready),
> an expanded non-secret `.env.example`, and `tests/test_preflight.py`.
> The supported-route smoke-test contract is also implemented
> (`tests/test_supported_route_contracts.py`): all 14 supported frontend
> routes are mapped to their primary read-only backend endpoints with
> route-drift detection against `App.tsx`. Mutating/job endpoints remain
> deliberately untested by smoke tests (see `DEFERRED_ENDPOINTS`).

> Post-audit update (2026-07-02): frontend route/navigation consolidation is
> complete. Supported operational pages are mounted, legacy/placeholder pages
> redirect safely, and `npm install`, typecheck, and the production build now
> succeed in this workspace. Production npm dependencies audit clean after
> non-breaking security updates; development tooling retains advisories whose
> automated fix requires a Vite major upgrade. Backend development dependencies
> are now reproducible through `requirements-dev.txt`; `python -m pytest -q`
> passes 857 tests in a Python 3.12 virtual environment with one TestClient
> deprecation warning. Findings below otherwise describe the point-in-time audit.

> CrateIQ fork update (2026-07-14): this fork uses `CRATEIQ_LIBRARY_ROOT` as
> the preferred backend environment variable and retains
> `CRATEMINDAI_LIBRARY_ROOT` as a deprecated fallback. A fresh baseline in the
> fork collected 857 tests but stalled at the first FastAPI health test; do not
> rely on the older “passes 857 tests” statement above until that blocker is
> diagnosed. See `docs/CRATEIQ_PRODUCT_AUDIT.md` for the current evidence and
> `docs/CRATEIQ_ROADMAP.md` for planned work.

> Backend hang diagnosis update (2026-07-15): the original stall is reproduced
> only in the restricted execution sandbox. Collection completes normally, but
> the sandbox does not wake the cross-thread asyncio event loop used by AnyIO /
> Starlette `TestClient`; the exact health test therefore blocks before client
> startup. A bare FastAPI reproduction and the identical dependency stack pass
> outside the sandbox. The CrateIQ suite now passes 860 tests twice in the normal
> host environment. No application lifecycle or dependency change was required.

## 1. Executive Summary

CrateIQ is a local-first DJ library operations app with a substantial CLI pipeline, a FastAPI backend, and a React/Vite dashboard. The core product is built around inspecting, cleaning, reconciling, enriching, and exporting a DJ library while avoiding unsafe automatic writes.

The codebase is past prototype stage in the backend and pipeline, but the product surface is uneven. The core read-only library, enrichment review, metadata repair/sanitation, BPM anomaly review, export validation, sync preview, and reconciliation ledger are real. The biggest gaps are:

- no authentication or authorization at all;
- inconsistent frontend routing, with several pages and links pointing to routes that are not mounted;
- strong environment/path assumptions baked into config;
- restricted test sandboxes may hang at the first FastAPI health test because of
  cross-thread asyncio wakeup behavior;
- some docs and naming are inconsistent across `CrateIQ`, `DJ Toolkit`, and `TrackIQ`.

## 2. Stack and Architecture

- Frontend: React 18 + Vite + TypeScript (`frontend/`)
- Backend: FastAPI + Pydantic + Uvicorn (`backend/app/`)
- Pipeline/runtime: Python CLI in `pipeline.py` with many command modules under `modules/`, `ai/`, `intelligence/`, and `utils/`
- Database:
  - pipeline DB: selected library root `logs/processed.db`
  - backend DB: `backend/data/jobs.db`
  - review state: JSON/JSONL under the selected library root
- AI services:
  - local Ollama for `ai-normalize`
  - Spotify/Deezer/Traxsource for online enrichment
  - Anthropic wrapper exists in `utils/llm_client.py`, but it is not the main active path in the backend
- Deployment/runtime:
  - backend via `uvicorn backend.app.main:app --reload --port 8000 --app-dir .`
  - frontend via `npm run dev`
  - setup helpers: `setup.sh`, `pipeline.sh`, `systemd/`
- Tooling:
  - pytest
  - TypeScript build/typecheck
  - generated docs in `docs/generated/`

This is a monorepo-like single repository with a Python backend/pipeline and a separate frontend package.

## 3. Repository Map

| Path | Purpose |
|---|---|
| `pipeline.py` | Main CLI/router for all pipeline commands |
| `config.py` | Global runtime config, path defaults, AI/provider settings |
| `db.py` | Core SQLite schema helpers for the pipeline |
| `backend/app/` | FastAPI backend, read-only API, job tracking DB |
| `frontend/` | React/Vite dashboard and API client |
| `modules/` | Main pipeline logic: sanitation, enrichment, exports, organization, analysis |
| `ai/` | Local Ollama normalization and schema code |
| `intelligence/artist/` | Deterministic artist normalization and alias handling |
| `intelligence/enrichment/` | Spotify/Deezer/Traxsource enrichment matching and review workflow |
| `intelligence/label/` | Label intelligence and label-normalization tooling |
| `utils/` | Prompt logging and LLM client helpers |
| `tests/` | Unit tests for CLI, backend logic, sanitation, AI, and path safety |
| `docs/` | Safety docs, audits, architecture specs, generated indexes |
| `scripts/` | Maintenance scripts, rollback helpers, doc generation |
| `systemd/` | User service definitions for timer/watch execution |
| `logs/` | Recorded run artifacts and summaries from pipeline stages |
| `backend/data/` | Backend-owned job DB/log storage location, created at runtime |

## 4. Product Workflows

What CrateIQ appears to be built for:

- ingest a DJ library under a single selected root;
- inspect tracks, folders, issues, and quality coverage;
- run deterministic metadata sanitation and repair workflows;
- queue and review enrichment proposals before applying them;
- detect BPM anomalies and reanalysis candidates;
- validate/export a library for Rekordbox;
- sync a library to an SSD mount;
- build sets/playlists from the library database;
- reconcile file paths against DB state.

Main intended user workflows:

1. browse the library and inspect track health;
2. review issue queues and approve/reject/defer metadata proposals;
3. validate or run exports;
4. run jobs and watch logs;
5. perform SSD sync previews and controlled sync runs;
6. inspect path reconciliation plans and ledger entries;
7. run pipeline commands from the backend job system.

The product assumption is a single operator managing one local DJ library root at a time.

## 5. Frontend Findings

What exists:

- core dashboard workspace in `frontend/src/pages/CrateMind.tsx`;
- quality dashboard in `frontend/src/pages/Quality.tsx`;
- metadata repair and sanitation review UIs;
- reconciliation ledger UI;
- reusable sidebar, page header, status badges, track panel, log modal, and error boundary components;
- API client and hooks for tracks and jobs.

What works reasonably well:

- main track browsing and filtering;
- review/approve/defer actions for enrichment and metadata queues;
- selected-track inspector;
- log polling for active jobs;
- virtualized or performance-oriented table handling in the main workspace.

Incomplete or broken:

- `Dashboard.tsx`, `Collection.tsx`, `Tracks.tsx`, and `Settings.tsx` remain legacy/orphaned source pages while the supported operational pages are mounted;
- the current sidebar is still organized by implementation modules rather than the target six user-goal sections;
- `Settings.tsx` is explicitly a placeholder;
- the product feels functional in the main review workflows but not yet fully integrated as a polished app.

UX assessment:

- core screens are practical and operational;
- route model is inconsistent;
- several affordances are dead or hidden, which makes the product feel partly consolidated and partly legacy.

## 6. Backend/API Findings

Major route groups:

- health/version: `/api/health`, `/api/stats`, `/api/version`
- tracks: `/api/tracks`, `/api/tracks/stats`, `/api/tracks/issues`, `/api/tracks/{id}`
- library: `/api/library/tree`, `/api/library/stats`, `/api/library/folders`, `/api/library/overview`, `/api/library/quality`, `/api/library/runs*`
- jobs: `/api/jobs*`
- insights/enrichment: `/api/enrichment/*`, `/api/audit/latest`
- analysis/BPM: `/api/analysis/*`
- metadata repair/sanitation: `/api/metadata-repair/*`, `/api/metadata-sanitation/*`
- export: `/api/exports/*`
- sync: `/api/sync/*`
- playlists: `/api/playlists/*`
- reconciliation: `/api/reconciliation/*`

What is solid:

- track queries use allowlisted sort columns;
- job execution is allowlisted and uses argument lists, not shell strings;
- sync source keys are validated and destination is fixed;
- reconciliation plan validation is read-only;
- read-only access to pipeline DB is enforced in backend helpers;
- logs are streamed from files rather than shell output pipes.

Weak points:

- no authentication or role checks on any API route;
- CORS is permissive for local dev origins only, but there is no auth boundary;
- some routes return broad `200`/empty fallbacks when the DB or files are missing, which hides failures;
- write-capable endpoints rely on frontend discipline and explicit `confirm=true` or allowlists, not user identity;
- some endpoints are intentionally narrow, but the backend still exposes destructive toggles like rsync `--delete` and export XML forcing.

## 7. Database and Persistence

Data stores:

- `logs/processed.db` under the selected library root: canonical pipeline DB
- `backend/data/jobs.db`: backend job queue, PID/progress state, BPM anomalies
- `data/intelligence/*.jsonl` and related JSON state files under the selected library root
- `logs/` under the selected root: pipeline run artifacts, summaries, audit reports

Main entities:

- `tracks`: canonical current-state library record
- `processed_state`: history/stage tracking cache in the pipeline DB
- `jobs`: backend job queue and subprocess tracking
- `bpm_anomalies`: review queue for anomaly detection
- `set_playlists` / `set_playlist_tracks`: playlist/set builder output
- enrichment review state and queue JSON/JSONL files

Relationship model:

- most application state is keyed by file path, not by account/workspace/user;
- the app is single-root and single-operator in practice;
- there is no tenant separation, workspace isolation, or user ownership model.

Risks:

- path-based state can drift after file moves/renames;
- backend and pipeline DBs are separate, so consistency depends on conventions;
- several destructive workflows are only guarded by CLI flags and operational discipline.

## 8. Authentication and Permissions

Current state:

- no login system;
- no sessions;
- no user model;
- no roles or permissions;
- no route guards;
- no API auth headers;
- no CSRF protection because there is no auth layer to protect.

Implication:

- the app is effectively trusted-local-only software.
- if it is ever exposed beyond a single operator on a local machine or private network, access control becomes a critical gap.

Weak access-control areas:

- job submission endpoints;
- export/sync/reconciliation apply paths;
- review state mutation endpoints;
- destructive rsync delete option;
- any endpoint that changes backend or pipeline state.

## 9. AI/LLM System Findings

Provider usage:

- local Ollama client in `ai/ollama_client.py`
- prompt/schema layer in `ai/normalizer.py` and `ai/metadata_schema.py`
- Anthropic wrapper in `utils/llm_client.py`
- Spotify/Deezer/Traxsource enrichment logic in `intelligence/enrichment/`

Prompting/memory/retrieval:

- no vector DB found;
- no embeddings or semantic memory layer found;
- review state is persisted as JSON/JSONL rather than RAG memory;
- prompt logs are written to `last-prompts/` by the shared LLM wrapper.

Risk controls that do exist:

- strict JSON-only prompt instructions for Ollama normalization;
- schema validation with confidence clamping;
- confidence threshold gate (`MIN_AI_CONFIDENCE = 0.80`);
- local Ollama timeout;
- deterministic pre-cleaning before the model sees tags;
- enrichment uses confidence gates and review state before apply.

Risk areas:

- prompt injection from file metadata or filenames can still influence outputs;
- hallucinated artist/title/version/label proposals remain possible;
- prompt logs can capture sensitive library metadata on disk;
- there is no budget cap, rate limit, or batch governor visible in the backend for LLM use;
- Anthropic wrapper reads `ANTHROPIC_API_KEY` from env, so any accidental use there becomes a data-leak path if prompt logging is left on.

## 10. Testing and Verification

| Command | Result | Notes |
|---|---|---|
| `python -m pip install -r requirements-dev.txt` | Passed | Verified after activating a repository-local Python 3.12 virtual environment |
| `python -m pytest -q` (historical restricted-sandbox baseline) | Blocked | 857 collected; stalls at `test_health_endpoint_reports_selected_root_and_db`; bounded run exits 124 before TestClient startup |
| `timeout -k 10s 300s .venv/bin/python -m pytest -q` (normal host) | Passed | 860 passed, 1 TestClient deprecation warning, 44.98s; repeated run passed 860 in 29.66s |
| `npm --prefix frontend run build` | Passed | Production Vite build completed |
| `npm --prefix frontend run typecheck` | Passed | TypeScript check completed |
| `rg --files ...` / file inspection | Passed | Repo inventory and route audit completed |
| `git status --short` | Passed | Git branch/remotes and final clean-tree state are recorded in the fork handoff |

The health-test hang was pre-existing environment behavior, not a regression
introduced by the CrateIQ rename. No backend runtime files were changed. The
existing health test provides lifecycle regression coverage; focused execution,
the complete backend API/reconciliation group, and two complete suite runs all
exited normally outside the restricted sandbox.

## 11. Security and Privacy Findings

| Severity | Finding | Evidence / impact |
|---|---|---|
| Critical | No authentication or authorization | Entire backend is exposed as trusted-local-only |
| High | Destructive operations are reachable without identity-based protection | Jobs, sync delete, export apply, review apply paths |
| High | Strong path/environment assumptions | Hardcoded music roots and SSD mount paths in `config.py` |
| High | Prompt/logging of metadata to disk | `utils/prompt_logger.py`, `last-prompts/` |
| Medium | CORS only covers local dev origins, not a real auth boundary | `backend/app/main.py` |
| Medium | Path-based state can drift after moves/renames | queue/review/log artifacts are file-path keyed |
| Medium | Some endpoints return empty fallbacks on error | Hides operational problems and can mask missing DB state |
| Low | Naming/docs inconsistency (`DJ Toolkit`, `TrackIQ`, `CrateIQ`) | Increases operator confusion, not direct runtime risk |

Immediate fixes recommended:

- add auth if there is any intention to expose the backend beyond one trusted operator;
- remove or further gate destructive job/apply paths;
- centralize and validate runtime path configuration;
- stop logging sensitive prompts by default or make the log destination opt-in.

## 12. Documentation Findings

Existing documentation is substantial and mostly useful:

- `README.md` is detailed and mostly current;
- `PROJECT_CONTEXT.md` and `NEXT_TASKS.txt` capture long-form project memory;
- `docs/safety/` and `docs/audits/` contain useful policy/audit material;
- generated indexes in `docs/generated/` help with repository navigation.

Issues:

- naming is inconsistent across docs and code;
- some docs refer to older phase labels or old command surfaces;
- the README and router do not fully match: several frontend pages in the tree are not actually routed;
- the repo contains both current and legacy workflow descriptions, which makes it harder to tell what is production-ready today.

Docs that should be updated before more development:

- `README.md` should reflect the actual mounted frontend routes and current runtime assumptions;
- `PROJECT_CONTEXT.md` should explicitly note the current audit state;
- `NEXT_TASKS.txt` should be trimmed to the next concrete implementation steps;
- any route/workflow changes should update `docs/generated/` and the relevant safety docs.

## 13. Main Risks

1. No auth/permissions layer.
2. Dead or orphaned frontend routes create false product completeness.
3. Hardcoded/local-only path assumptions make portability fragile.
4. Destructive workflows are present and rely on operator discipline.
5. Prompt logging can leak sensitive library metadata to disk.
6. Build/test reproducibility is currently broken in this workspace.
7. Path-based state can drift after file moves or renames.
8. Multiple naming systems (`CrateIQ`, `DJ Toolkit`, `TrackIQ`) increase operational ambiguity.
9. Some API error paths fall back to empty data, hiding real failures.
10. AI enrichment/normalization can still hallucinate or misclassify metadata.

## 14. Recommended Roadmap

Immediate fixes before new features:

- make the app buildable from a clean checkout;
- fix the route/nav surface so the existing pages are reachable or removed;
- normalize naming/docs to one product identity;
- tighten prompt logging and sensitive-data handling;
- audit all destructive endpoints for explicit user confirmation semantics.

Phase 1: stabilize and run reliably

- add dependency installation instructions that actually work in a fresh environment;
- make backend and frontend verification run in CI or a repeatable local script;
- surface clear errors when the selected library root or DB is missing;
- remove silent empty fallbacks where they hide runtime problems.

Phase 2: complete core workflows

- finish the main library/review/extraction/export workflows in one consistent router;
- wire jobs, export, sync, and set-builder into the UI if they remain supported;
- deprecate or hide legacy pages that are not part of the supported product.

Phase 3: auth/user/account hardening

- add authentication;
- add a user/role model;
- protect mutating endpoints;
- add audit logs for who approved/applied what.

Phase 4: AI reliability and memory/RAG improvements

- add stronger prompt/versioning controls;
- add batch limits and cost/latency tracking;
- decide whether prompt logs are opt-in;
- if memory is needed, add a real retrieval layer rather than relying on JSON review files.

Phase 5: production deployment readiness

- containerize or otherwise package the backend/frontend cleanly;
- add environment validation;
- standardize config overrides;
- add health checks that confirm the library root, DB, and external binaries.

Phase 6: future product features

- tenant/workspace support if the product moves beyond one operator;
- collaborative review workflows;
- richer analytics and reporting;
- optional remote deployment;
- safer reconciliation apply flows.

## 15. Best Next Development Step

The route consolidation task is complete.

Next, create a reproducible local-runtime preflight: add a non-secret environment
template, validate configured library paths and required external tools, and add
smoke tests that verify the supported frontend routes against their backend API
contracts. This addresses the remaining setup ambiguity without changing pipeline
execution behavior.
