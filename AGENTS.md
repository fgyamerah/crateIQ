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

CrateIQ is a local-first DJ library preparation and management application.

Primary stack:

* FastAPI backend;
* React/Vite/TypeScript frontend;
* a Python service layer under `backend/app/services/`;
* SQLite for tracks, jobs, and operational state;
* the local filesystem for the managed music workspace.

## Managed Workspace

CrateIQ operates on a managed root directory containing:

* `Inbox/` — tracks copied in for preparation, not yet promoted;
* `Library/` — promoted, finished music (`Genre/Artist/Artist - Title.ext`);
* `Quarantine/` — reserved; never an automatic destination.

External source files are never modified. Importing copies files into
`Inbox/`; originals remain untouched.

## Primary Workflow

```
External source
  -> copy into managed Inbox
  -> Process All (deterministic cleanup + provider routing + consensus)
  -> HIGH-confidence fields applied automatically
  -> MEDIUM/LOW/CONFLICT fields go to Needs Review
  -> controlled tag writes to managed Inbox copies
  -> BPM/key/waveform analysis
  -> readiness
  -> explicit "Move Ready to Library" promotion
  -> Library/Genre/Artist/Artist - Title.ext
  -> Crates / Set Builder / Publish
```

`pipeline.py` is a legacy CLI that remains partly load-bearing for
maintenance and reconciliation compatibility. It is not the primary
application architecture — the FastAPI backend and managed-workspace
workflow above are current and authoritative.

---

# 4. Architecture Map

## 4.1 Main Areas

| Path                            | Purpose                                                        |
| -------------------------------- | --------------------------------------------------------------- |
| `backend/app/`                   | FastAPI backend -- current primary application                 |
| `backend/app/services/`          | Core service layer (workspace, preparation, needs-review, provider routing, consensus, tag write, publish/sync, analysis/waveform jobs) |
| `backend/data/`                  | Operational data: jobs DB, tag-write backups, provider cache, waveform cache, runtime config |
| `frontend/`                      | React/Vite/TypeScript app -- current primary UI                |
| `pipeline.py`                    | Legacy/maintenance CLI; partly load-bearing for reconciliation and compatibility, not the primary entry point |
| `config.py` / `config_local.py`  | Legacy pipeline configuration and local overrides               |
| `db.py`                          | Legacy pipeline SQLite schema/helpers                            |
| `modules/`, `ai/`, `intelligence/`, `utils/` | Legacy pipeline modules and helpers, still used by `pipeline.py` |
| `tests/`                         | Python tests                                                    |
| `docs/`                          | Safety docs, audits, generated docs, project notes               |
| `scripts/`                       | Maintenance and helper scripts (some legacy/dangerous -- verify before running) |
| `systemd/`                       | Optional service/timer files                                    |
| `logs/`                          | Runtime logs and legacy pipeline artifacts                       |

## 4.1.1 Storage Separation

Do not confuse managed music storage with operational data storage.

* **Managed music** (the user's DJ library): `Inbox/`, `Library/`,
  `Quarantine/` under the configured workspace root. See Section 3.
* **Operational data** (app internals): jobs DB, backups, provider cache,
  waveform cache, and runtime config, mostly under `backend/data/`. This
  supports the app but is not itself music library content.

## 4.2 Backend

Backend framework:

* FastAPI
* Pydantic
* Uvicorn

Important route groups:

* `/api/health`, `/api/version`, `/api/genres`, `/api/settings`, `/api/runtime`
* `/api/workspace*` — managed Inbox/Library/Quarantine, import, promotion
* `/api/tracks*`, `/api/library*`
* `/api/needs-review*` — field-level consensus review queue
* `/api/jobs*` — background operation tracking
* `/api/analysis*`, `/api/waveforms*`
* `/api/tag-write*` — plan/apply/restore controlled tag writes
* `/api/beets-review*`, `/api/enrichment-review*`
* `/api/metadata-repair*`, `/api/metadata-sanitation*`
* `/api/quality-review*`, `/api/duplicates*`
* `/api/crates*`, `/api/smart-crates*`, `/api/playlists*`
* `/api/exports*`, `/api/sync*`, `/api/publish*`
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

## 4.4 Legacy Pipeline (`pipeline.py`)

`pipeline.py` predates the FastAPI/React managed-workspace application. It is
**not** the primary product architecture, but it remains partly load-bearing
for maintenance and reconciliation compatibility (e.g. `db-prune-stale`,
some reconciliation and reporting flows). Treat it as legacy/maintenance
tooling, not as the current entry point.

Do not make broad pipeline architecture changes unless the user explicitly asks.

## 4.5 Providers and Consensus

Evidence sources, in routing order:

* Embedded tags and filename hints (always available, no network);
* AcoustID / Chromaprint fingerprinting;
* Beets Python API + MusicBrainz (existing, always tried first);
* Discogs / Beatport (release/genre/DJ-catalogue evidence);
* Spotify / Deezer (catalogue corroboration);
* Last.fm (tag/genre evidence);
* YouTube (last-resort, low-authority corroboration only).

Provider calls are bounded and config/credential-aware (e.g. AcoustID only
runs if a client key is configured and `fpcalc` is available).

Consensus is field-level and explainable, with one verdict per field:

* **HIGH** — strong identity evidence, no conflicts; eligible for
  auto-apply during Process All.
* **MEDIUM** — plausible but not strongly corroborated.
* **LOW** — weak or single-source evidence.
* **CONFLICT** — providers disagree.

MEDIUM, LOW, and CONFLICT fields go to Needs Review rather than being
auto-applied.

Traxsource is legacy: it exists only in the old `pipeline.py`-era code
(`traxsource_lookup.py`, `NEXT_TASKS.txt` history) and is not part of the
current `provider_routing_service.py` provider set. Do not treat it as an
active provider.

Beets: the Beets **Python API** is allowed. The `beet` **CLI binary** is
forbidden.

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

## Managed Write Model

CrateIQ is not universally read-only. It supports controlled writes within
well-defined boundaries:

* External imported originals remain untouched — CrateIQ only ever creates
  managed copies in `Inbox/`.
* Controlled metadata (tag) writes to managed Inbox copies are supported via
  `tag_write_service`: build an exact diff/plan, validate the file has not
  gone stale since the plan was built, take a hash-verified backup outside
  the scanned tree, write only the approved diffed fields, re-read the file
  and verify every changed field, and preserve the backup for restore on any
  failure.
* Promotion (`workspace_service.promote_tracks`) explicitly moves a managed
  copy from `Inbox/` into `Library/`; it is never automatic.
* `Quarantine/` is reserved and is not an automatic destination during
  normal preparation.
* Publish/Sync mutations remain explicitly guarded and require confirmation.

Preserve Mixed In Key protections (Section 5) wherever they currently apply
to writes.

Beets: the Python API is allowed; the `beet` CLI binary is forbidden.

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

# 9. Product Surface and Frontend Routing

Current top-level navigation (`frontend/src/components/Sidebar.tsx`):

* **Library** — Inbox, Library, Needs Review
* **DJ** — Crates, Set Builder, Publish
* **Tools** — Jobs, Maintenance
* **System** — Settings

Specialist pages (e.g. quality review, metadata repair/sanitation, BPM
review, beets/enrichment review, reconciliation) remain reachable beneath
Needs Review/Maintenance or via deep links, not as top-level nav items.
Older URLs (`/dashboard`, `/collection`, `/tracks`, `/export`, `/ssd-sync`,
`/library-prep`, etc.) redirect to their current equivalents — see
`frontend/src/App.tsx`. Do not maintain an exhaustive fragile route list
here; treat `App.tsx` and `Sidebar.tsx` as the source of truth for current
routes.

When changing frontend routes:

* inspect `frontend/src/App.tsx`;
* inspect the sidebar/navigation component;
* inspect relevant files under `frontend/src/pages/`;
* ensure every visible nav item has a mounted route;
* ensure every mounted route represents a supported workflow;
* hide or redirect unsupported legacy routes rather than deleting them outright;
* do not present placeholder pages as complete features.

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

## Source-of-Truth Rule

Current-state sources, in order of authority: current source code, then
`AGENTS.md`, `README.md`, `PROJECT_CONTEXT.md`, `NEXT_TASKS.txt`, and
`CHANGELOG.txt`. Historical or archived documents under `docs/archive/`
(including `docs/archive/DJToolkit_CONTEXT.txt`) are not authoritative for
current architecture and are not required reading for active project
memory.

---

# 11. Naming Rules

Use **CrateIQ** as the product name.

Legacy names may exist in the repo:

* DJ Toolkit;
* TrackIQ;
* CrateMindAI;
* KKDJ references.

They may remain where historically accurate or required for compatibility
(e.g. the `CrateMind` component backing several deep-linked routes). Do not
infer current architecture merely from old filenames or component names.

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

Current architecture-level gaps supported by current docs/source:

1. Trusted-local, no-auth security model — no login, sessions, user model,
   roles, or route guards. Do not expose the backend remotely without
   addressing this first.
2. The legacy `pipeline.py` / `config.py` architecture remains partially
   load-bearing and coexists with the current FastAPI/React application,
   which is a source of confusion for anyone reading old code first.
3. Some legacy scripts under `scripts/` are still present and potentially
   dangerous; they have not yet been cleaned up.
4. "Legacy Direct Library" remains as a compatibility mode alongside the
   managed-workspace workflow.
5. Several metadata providers (e.g. Discogs, Beatport, Spotify, Deezer,
   Last.fm) require credentials to be configured before they provide real
   verification value; without credentials they are effectively inert.

For a full task-level backlog, see `NEXT_TASKS.txt` — do not duplicate it here.

---

# 18. Development Priorities

Unless the user states a different priority, prefer this order:

1. Refine the core managed-library workflow based on real usage.
2. Safely remove legacy architecture confusion (pipeline.py vs. current app).
3. Fix Publish/Sync configuration portability (hardcoded paths).
4. Improve provider matching using real-world library evidence.
5. Address authentication/security before any remote or multi-user deployment.
6. Packaging/production readiness, after the above.

Explicit user priorities always override this suggested order.

---

# 19. Final Safety Rule

If unsure, choose the safer option:

* ask before destructive actions;
* preserve data;
* keep changes small;
* report uncertainty;
* do not fake successful verification.
