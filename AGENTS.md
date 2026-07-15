# AGENTS.md

This file provides operating instructions for AI coding agents working in this repository.

Project: **CrateIQ**
Repository type: local-first DJ library operations app
Primary stack: Python pipeline + FastAPI backend + React/Vite frontend + SQLite
Primary safety principle: preserve user-controlled DJ library data and avoid unsafe automatic writes.

---

# 1. Core Operating Rule

Agents must work in **controlled, scoped mode**.

Do not behave autonomously beyond the user’s task.

## Always

* Understand the requested task before editing.
* Keep changes focused and reviewable.
* Prefer small, surgical edits over broad rewrites.
* Preserve existing behavior unless the user explicitly asks to change it.
* Protect music library data, metadata, playlists, BPM/key/cue data, and export/sync outputs.
* Update project documentation after meaningful code changes.
* Report exact commands run and whether they passed or failed.

## Never

* Delete files unless explicitly instructed.
* Run destructive commands without explicit user permission.
* Print or expose secrets, API keys, tokens, cookies, or private paths containing credentials.
* Broadly refactor unrelated areas.
* Add new dependencies unless they are required and justified.
* Change CLI compatibility without documenting it.
* Change destructive sync/export behavior casually.
* Override Mixed In Key data.
* Pretend tests passed when they failed or were not run.

---

# 2. Working Modes

Use one of these modes per task.

## 2.1 Audit / Explore Mode

Use this when the user asks to audit, inspect, understand, map, summarize, or report on the repository.

Allowed:

* Inspect files needed to understand the requested scope.
* Use `rg`, `find`, `ls`, `sed`, and similar read-only commands.
* Read relevant frontend, backend, pipeline, config, tests, and docs files.
* Produce a clear report with findings, risks, and recommended next steps.

Required:

* Avoid printing secrets.
* Do not modify app behavior.
* Only create or update audit/documentation files if requested.
* Summarize what was inspected.

## 2.2 Modify Mode

Use this when the user asks for a bug fix, feature, refactor, cleanup, or implementation.

Allowed:

* Read files needed to complete the requested change.
* Modify only files relevant to the task.
* Add or update tests when practical.
* Update docs required by this file.

Required:

* Keep the diff focused.
* Preserve existing patterns.
* Run relevant verification commands.
* Report files changed and commands run.

## 2.3 Restricted Mode

Use this when the user explicitly names specific files and asks to work only on those files.

Rules:

* Read only the specified files unless the task cannot be completed safely.
* If more context is needed, stop and ask or clearly explain the required extra files.
* Do not search the broader repository.

---

# 3. Project Overview

CrateIQ is a local-first DJ library operations app.

It helps inspect, clean, reconcile, enrich, validate, export, and manage a DJ music library while avoiding unsafe automatic writes.

The app includes:

* a Python CLI pipeline;
* a FastAPI backend;
* a React/Vite/TypeScript frontend;
* SQLite databases;
* review queues and JSON/JSONL state files;
* local-first AI-assisted metadata normalization;
* enrichment workflows using providers such as Spotify, Deezer, and Traxsource;
* Rekordbox/export support;
* SSD sync and reconciliation workflows.

The current product is strongest in backend/pipeline logic and weaker in frontend routing, setup reproducibility, authentication, and production readiness.

---

# 4. Architecture Map

## 4.1 Main Areas

| Path              | Purpose                                                   |
| ----------------- | --------------------------------------------------------- |
| `pipeline.py`     | Main CLI/router for pipeline commands                     |
| `config.py`       | Global runtime configuration and path defaults            |
| `config_local.py` | Local overrides; should not be used for committed secrets |
| `db.py`           | Core SQLite schema/helpers for pipeline state             |
| `backend/app/`    | FastAPI backend                                           |
| `backend/data/`   | Backend runtime data such as jobs DB                      |
| `frontend/`       | React/Vite/TypeScript dashboard                           |
| `modules/`        | Main pipeline modules                                     |
| `ai/`             | Local AI normalization code                               |
| `intelligence/`   | Artist, label, enrichment, and review workflows           |
| `utils/`          | Shared helpers, LLM client, prompt logging                |
| `tests/`          | Python tests                                              |
| `docs/`           | Safety docs, audits, generated docs, project notes        |
| `scripts/`        | Maintenance and helper scripts                            |
| `systemd/`        | Optional service/timer files                              |
| `logs/`           | Runtime logs and pipeline artifacts                       |

## 4.2 Backend

Backend framework:

* FastAPI
* Pydantic
* Uvicorn

Important route groups:

* `/api/health`
* `/api/stats`
* `/api/version`
* `/api/tracks*`
* `/api/library*`
* `/api/jobs*`
* `/api/enrichment*`
* `/api/audit/latest`
* `/api/analysis*`
* `/api/metadata-repair*`
* `/api/metadata-sanitation*`
* `/api/exports*`
* `/api/sync*`
* `/api/playlists*`
* `/api/reconciliation*`

Backend safety expectations:

* Preserve allowlisted job execution.
* Preserve read-only DB access patterns where already used.
* Preserve confirmation requirements for mutating operations.
* Do not loosen path validation.
* Do not expose destructive actions casually through the UI.

## 4.3 Frontend

Frontend stack:

* React 18
* Vite
* TypeScript

Important frontend areas:

* `frontend/src/App.tsx`
* `frontend/src/pages/`
* `frontend/src/components/`
* `frontend/src/api/`
* `frontend/src/hooks/`

Frontend expectations:

* Routes must match real supported workflows.
* Navigation must not point to dead routes.
* Placeholder pages should not be presented as finished features.
* UI should clearly distinguish supported, deferred, and experimental workflows.
* Product naming should consistently use **CrateIQ**.

## 4.4 Pipeline

The pipeline is local-first and must protect library data.

Typical pipeline concepts:

* quality control;
* dedupe;
* organize;
* sanitize;
* analyze;
* tag;
* cue;
* playlist/set generation;
* reporting;
* Rekordbox export;
* sync;
* reconciliation;
* enrichment review.

Do not make broad pipeline architecture changes unless the user explicitly asks.

---

# 5. Mixed In Key Rule

Mixed In Key, also called **MIK**, is authoritative for:

* BPM;
* key;
* cue points.

## Never

* Overwrite existing BPM.
* Overwrite existing key.
* Overwrite existing cue points.
* Re-analyze files that already have trusted MIK data.
* Force XML export unless explicitly requested or required.

## Always

* Check existing DB state and file tags first.
* Preserve existing analysis values.
* Only fill missing data.
* Prefer safe M3U exports where applicable.
* Treat `--force-xml` as an explicit override, not a default.

---

# 6. Data Safety Rules

CrateIQ manages real music library data. Treat all file operations as high-risk.

## Path Safety

* Do not hardcode new absolute paths.
* Prefer config-driven paths.
* Preserve support for local overrides.
* Validate user-controlled paths before use.
* Avoid path traversal risks.
* Do not assume a library root exists.
* Surface clear errors when a required root, DB, mount, or file is missing.

## Database Safety

Primary stores include:

* pipeline DB under the selected library root, usually `logs/processed.db`;
* backend jobs DB under `backend/data/jobs.db`;
* JSON/JSONL review state under the selected library root;
* logs and audit artifacts under the selected library root.

Rules:

* Do not add destructive migrations without explicit approval.
* Do not silently wipe or recreate production-like DB files.
* Avoid broad write operations where targeted updates are possible.
* Keep read-only operations read-only.
* Preserve idempotency.

## Sync / Export Safety

Destructive workflows must remain guarded.

High-risk areas:

* SSD sync;
* rsync delete options;
* export overwrite/force options;
* reconciliation apply flows;
* metadata apply flows;
* enrichment apply flows.

Rules:

* Keep explicit confirmation semantics.
* Do not make destructive options the default.
* Do not hide destructive behavior behind friendly UI labels.
* Document any changes to sync/export behavior.

---

# 7. AI / LLM Rules

CrateIQ is local-first. AI must assist the workflow, not control it blindly.

Known AI areas:

* local Ollama normalization;
* metadata schema validation;
* enrichment provider matching;
* optional Anthropic wrapper;
* prompt logging utilities.

## Always

* Prefer deterministic logic before LLM calls.
* Validate model output with schemas.
* Preserve confidence thresholds.
* Keep review-before-apply behavior.
* Handle AI/provider failures gracefully.
* Avoid uncontrolled batch costs.
* Avoid leaking private library metadata to external providers unless explicitly required.

## Never

* Send private library metadata to an external API casually.
* Add a new AI provider without approval.
* Treat AI output as authoritative.
* Bypass human review for risky metadata changes.
* Log secrets.

## Prompt Logging

Prompt logs may contain private library metadata.

Rules:

* Do not print prompt logs in final responses.
* Do not expose prompt-log contents unless explicitly requested.
* Prefer opt-in or clearly documented prompt logging.
* Never log API keys or secrets.

---

# 8. Authentication and Permissions

Current audit state:

* no login system;
* no sessions;
* no user model;
* no roles;
* no API auth protection;
* no route guards.

Until auth is implemented, treat the app as **trusted-local-only software**.

Rules:

* Do not claim the app is safe for remote exposure.
* Do not expose backend endpoints publicly.
* Do not add remote deployment guidance without warning about missing auth.
* Protect mutating endpoints before any production or multi-user deployment work.
* If implementing auth, do it as a dedicated task with tests and docs.

---

# 9. Frontend Routing Rules

The audit identified inconsistent frontend routing and orphaned pages.

When changing frontend routes:

* inspect `frontend/src/App.tsx`;
* inspect the sidebar/navigation component;
* inspect relevant files under `frontend/src/pages/`;
* ensure every visible nav item has a mounted route;
* ensure every mounted route represents a supported workflow;
* hide or redirect unsupported legacy routes;
* do not present placeholder pages as complete features;
* update README and task docs with the supported route list.

Recommended route policy:

* Core library/dashboard/review workflows should be reachable.
* Jobs/export/sync/set-builder pages should only be exposed if they are safe and functional.
* Experimental or legacy workflows should be hidden, clearly marked, or deferred.

---

# 10. Documentation Rules

After every meaningful code change, update project documentation.

Required docs to update when applicable:

* `README.md`
* `NEXT_TASKS.txt`
* `CHANGELOG.txt`
* `PROJECT_CONTEXT.md`

## CHANGELOG.txt

Add a new entry at the top:

```text
[YYYY-MM-DD] — Short title

- What changed
- Why it changed
- Files affected
- Migration notes, if any
```

## NEXT_TASKS.txt

Update task status:

* mark completed items with `[x]`;
* mark in-progress items with `[~]`;
* add new follow-up tasks where needed;
* remove or consolidate stale duplicates only when clearly safe.

## PROJECT_CONTEXT.md

Update when any of these change:

* architecture;
* CLI behavior;
* backend routes;
* frontend routes;
* DB schema;
* config keys;
* runtime setup;
* known issues;
* security posture;
* AI/provider behavior;
* product naming.

## README.md

Update when any of these change:

* install commands;
* run commands;
* test/build commands;
* supported frontend routes;
* environment variables;
* product name;
* backend/frontend startup flow;
* warnings about local-only/no-auth status.

Do not update docs for purely read-only inspection unless the user asked for a report or audit file.

---

# 11. Naming Rules

Use **CrateIQ** as the product name.

Legacy names may exist in the repo:

* DJ Toolkit;
* TrackIQ;
* KKDJ references.

Rules:

* Do not rename everything in one broad sweep unless explicitly requested.
* When touching nearby docs/UI, prefer CrateIQ naming.
* Preserve historical references only where they are clearly archival or migration context.
* Avoid introducing new naming variants.

---

# 12. Testing and Verification

Run the safest relevant checks for the task.

## Python

Common commands:

```bash
python3 -m pytest -q
python3 -m pytest tests/ -v
python3 -m unittest discover tests
```

If `pytest` is missing, report the exact failure and recommend environment setup. Do not claim tests passed.

## Frontend

Common commands:

```bash
npm --prefix frontend install
npm --prefix frontend run build
npm --prefix frontend run typecheck
```

Only run scripts that exist in `frontend/package.json`.

## General

Useful checks:

```bash
git diff --check
git status --short
```

If the workspace is not a git repo, report that clearly.

## Verification Report Format

Always report:

* command run;
* result;
* short notes;
* unresolved failures.

Use a table when practical.

---

# 13. Dependency Rules

Do not add dependencies casually.

Before adding a dependency:

* check whether existing code already solves the problem;
* justify why the dependency is necessary;
* prefer small, well-maintained packages;
* update lockfiles when applicable;
* update README/setup docs;
* run build/tests.

Never add external AI, cloud, telemetry, analytics, or tracking packages without explicit approval.

---

# 14. Security Rules

High-priority security issues in this repo:

* no auth/permissions;
* destructive local workflows;
* hardcoded/local path assumptions;
* prompt logs containing library metadata;
* potentially permissive local dev behavior;
* silent empty API fallbacks;
* path-based state drift.

Rules:

* Do not expose secrets.
* Do not weaken CORS.
* Do not add public deployment instructions without no-auth warnings.
* Do not make destructive endpoints easier to trigger.
* Validate file paths.
* Prefer explicit errors over silent empty fallbacks.
* Keep local-first assumptions clear.

If you discover committed secrets:

* do not print the secret;
* report the file path and type of issue;
* recommend rotation/removal;
* do not include the secret value in logs or output.

---

# 15. Git Rules

When the repository is a git repo, report:

```bash
git status --short
```

Do not commit unless the user explicitly asks.

When the user asks to commit:

1. run relevant tests/build checks first;
2. run `git status --short`;
3. summarize changed files;
4. create a clear commit message;
5. commit;
6. show final status.

Do not push unless the user explicitly asks.

---

# 16. Output Format

At the end of every coding task, return:

1. Files changed
2. Summary of changes
3. Verification commands and results
4. Remaining gaps or risks
5. Recommended next task
6. Git status summary, if available

For audits, return:

1. Files inspected or scope inspected
2. Main findings
3. Risks ranked by severity
4. Recommended roadmap
5. Suggested next Codex prompt

For read-only questions, keep the answer concise and do not invent implementation details.

---

# 17. Current Known Gaps

From the latest audit, the main known gaps are:

1. No authentication or authorization.
2. Frontend routes and sidebar navigation are inconsistent.
3. Several frontend pages exist but are not mounted or are effectively orphaned.
4. Setup/build/test reproducibility is broken when dependencies are not installed.
5. Runtime is tied to local path assumptions.
6. Prompt logging can capture private library metadata.
7. Some API error paths may return empty fallbacks that hide real failures.
8. Path-based state can drift after file moves or renames.
9. Naming is inconsistent across CrateIQ, DJ Toolkit, and TrackIQ.
10. AI enrichment and normalization still require review because hallucinated proposals are possible.

---

# 18. Preferred Development Order

Unless the user gives a different priority, prefer this order:

## Phase 1 — Stabilize

* make frontend/backend setup reproducible;
* fix frontend routing and navigation;
* remove dead links;
* document supported routes;
* make build/typecheck/test commands reliable.

## Phase 2 — Complete Core Workflows

* complete library dashboard;
* complete review queues;
* complete metadata repair/sanitation flows;
* complete BPM anomaly review;
* complete export validation;
* complete sync preview;
* complete reconciliation ledger.

## Phase 3 — Auth and Permissions

* add login;
* add sessions;
* add user model;
* add roles/permissions;
* protect mutating routes;
* add audit logs for apply/approve actions.

## Phase 4 — AI Reliability

* improve prompt/version controls;
* make prompt logging safer;
* add batch/cost controls;
* improve validation around AI proposals;
* consider memory/RAG only after core workflows are stable.

## Phase 5 — Production Readiness

* environment validation;
* packaging/containerization;
* health checks;
* deployment documentation;
* no-auth warning removal only after auth exists.

---

# 19. Final Safety Rule

If unsure, choose the safer option:

* ask before destructive actions;
* preserve data;
* keep changes small;
* report uncertainty;
* do not fake successful verification.
