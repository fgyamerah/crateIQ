# CrateIQ Shared Operation/Result Contract

Architecture inventory and minimum-common-vocabulary proposal.
No runtime/API/schema implementation — this is a documentation and planning
artifact only.

This document was produced by inspecting current source code across all
seven major workflow families. It records what exists rather than what
should exist. The "Proposed minimum shared contract" section (3) is additive
guidance for future convergence; nothing in this document authorizes
immediate implementation work.

---

## 1. Current-State Inventory

Every workflow ships a distinct operation model with its own status
vocabulary and its own count fields.  Most workflows persist their
operation rows inside the backend's `jobs.db`.  Reconciliation is the
explicit exception: its append-only reconciliation ledger
(`reconciliation_ledger` table) is stored in the selected library's
`<root>/logs/processed.db`, not in `jobs.db`.

### 1.1 Inbox Process All / Preparation Operations

**Source:** `backend/app/services/preparation_operations_service.py`,
`backend/app/core/db.py` (table `preparation_operations`),
`backend/app/services/preparation_service.py`

**Operation table:** `preparation_operations`

| Field                | Meaning                                                   | Present |
|----------------------|-----------------------------------------------------------|---------|
| `id`                 | uuid4 hex; operation identifier                           | YES     |
| `operation_type`     | `process_all` / `clean_selected` / `enrich_selected`      | YES     |
| `status`             | `running` / `completed` / `failed` / `cancelled`          | YES     |
| `mode`               | implicit: always `apply` (only confirmed runs persist)    | IMPLICIT |
| `track_count`        | total inbox tracks in the batch                           | YES     |
| `cleaned_count`      | deterministic cleanup stage                               | YES     |
| `enriched_count`     | provider routing + consensus stage                        | YES     |
| `written_count`      | tag write-back stage (controlled writes only)             | YES     |
| `needs_review_count` | tracks/fields requiring human review                      | YES     |
| `ready_count`        | tracks passing promotion readiness                        | YES     |
| `failed_count`       | unrecoverable failures across any stage                   | YES     |
| `cancel_requested`   | boolean flag set by `request_cancel`; honored by caller   | YES     |
| `warnings_json`      | JSON array, max 50 entries                                | YES     |
| `error_reason`       | max 200 chars                                             | YES     |
| `created_at`         | ISO-8601 UTC                                              | YES     |
| `started_at`         | ISO-8601 UTC (same as created_at for confirmed runs)      | YES     |
| `finished_at`        | ISO-8601 UTC                                              | YES     |

**Absent fields:** NO `outcome` (derived column — `status` alone must
distinguish clean completion from partial-failure), NO
`verification_status`, NO `confirmation_required` (enforced at API boundary
via confirm flag, not persisted), NO `affected_item_ids` (only aggregate
counts), NO `artifacts`, NO `scope` as separate from `track_count`, NO
`outcome`, NO `blockers`, NO `remaining_*` counter.

**Lifecycle:**
- A row is created only for a confirmed run about to begin work — previews
  are never persisted.
- Status transitions: `running` → `completed` / `failed` / `cancelled`.
- Interrupted `running` rows are closed to `failed` with
  `error_reason='backend_restarted'` on startup (never silently resumed).
- Cancellation is *cooperative*: the flag is set, `preparation_service.py`
  checks it between stages and calls `finish_operation` with
  `status='cancelled'`.

**Endpoint shape (API route `GET /api/workspace/preparation/operations`):**
returns the row as a flat dict — no typed Pydantic model exists for the GET
response.

---

### 1.2 BPM and Key Analysis Operations

**Source:** `backend/app/services/analysis_operations_service.py`,
`backend/app/services/analysis_jobs_service.py`,
`backend/app/schemas/analysis_jobs.py`,
`backend/app/core/db.py` (table `analysis_operations`)

**Operation table:** `analysis_operations`

| Field                | Meaning                                                   | Present |
|----------------------|-----------------------------------------------------------|---------|
| `id`                 | uuid4 hex; operation identifier                           | YES     |
| `job_type`           | `bpm_analysis` / `key_analysis`                           | YES     |
| `mode`               | `apply` (global) / `apply_scoped` (track_ids-restricted)  | YES     |
| `status`             | `running` / `completed` / `failed` / `cancelled`          | YES     |
| `outcome`            | **DERIVED** from status + counts (via `derive_outcome()`) | DERIVED |
| `scope_limit`        | explicit limit passed at confirmation time                | YES     |
| `eligible_total`     | in-scope missing-value candidates before limit            | YES     |
| `considered`         | candidates selected for processing (≤ scope_limit)        | YES     |
| `processed`          | tracks that entered the analysis loop                     | YES     |
| `succeeded`          | tracks with successful, accurate results                  | YES     |
| `skipped`            | ineligible / already-has-value tracks                     | YES     |
| `failed`             | unrecoverable track-level failures                        | YES     |
| `recovered`          | tracks that only succeeded via the FFmpeg BPM fallback    | YES     |
| `remaining_missing`  | tracks still missing the value after this run             | YES     |
| `cancel_requested`   | boolean flag; honored by caller's loop                    | YES     |
| `error_reason`       | max 200 chars                                             | YES     |
| `warnings_json`      | JSON array, max 50 entries                                | YES     |
| `created_at`         | ISO-8601 UTC                                              | YES     |
| `started_at`         | ISO-8601 UTC                                              | YES     |
| `finished_at`        | ISO-8601 UTC                                              | YES     |

**Outcome vocabulary (derived, not persisted):**
- `running` — still in progress
- `complete` — status=`completed`, zero failed, zero recovered
- `completed_with_warnings` — status=`completed`, zero failed, some recovered
- `completed_with_errors` — status=`completed`, some failed
- `cancelled` / `failed` — passed through from status

This derivation (`analysis_operations_service.derive_outcome`) ensures a
run with unrecovered track failures cannot render as a plain "Complete" in
the UI.

**Absent fields:** NO `verification_status`, NO `confirmation_required`
(enforced at API boundary), NO `blockers` in the operational row (blockers
block the run from starting and never create a row), NO `artifacts`, NO
`warnings` as a dedicated column outside `warnings_json`.

**Endpoint shape:** `POST /api/analysis/jobs/{job_type}/run` returns a
`BpmAnalysisRunResult` or `KeyAnalysisRunResult` with an `operation_id`
field. The GET history endpoint returns `AnalysisOperation` typed models.

---

### 1.3 Waveform Generation / Bulk Waveform Operations

This workflow family has two layers:
1. Per-track waveform jobs (operational state in `waveform_jobs` table)
2. Bulk operation history (operational state in `waveform_operations` table)

#### 1.3a Bulk Waveform Operations

**Source:** `backend/app/services/waveform_operations_service.py`,
`backend/app/schemas/waveform.py`,
`backend/app/core/db.py` (table `waveform_operations`)

| Field                | Meaning                                                   | Present |
|----------------------|-----------------------------------------------------------|---------|
| `id`                 | uuid4 hex; operation identifier                           | YES     |
| `operation_type`     | always `generate_missing`                                 | YES     |
| `status`             | `running` / `completed` / `failed` / `cancelled`          | YES     |
| `total_tracks`       | total tracks in the library (truthful, for context)       | YES     |
| `eligible_total`     | tracks eligible for generation (missing waveform)         | YES     |
| `processed`          | tracks that entered the generation loop                   | YES     |
| `generated`          | tracks that successfully generated a waveform             | YES     |
| `skipped`            | ineligible / already-has-waveform tracks                  | YES     |
| `failed`             | unrecoverable per-track failures                          | YES     |
| `remaining_missing`  | tracks still missing a waveform after this run            | YES     |
| `cancel_requested`   | boolean flag                                              | YES     |
| `error_reason`       | max 200 chars                                             | YES     |
| `created_at`         | ISO-8601 UTC                                              | YES     |
| `started_at`         | ISO-8601 UTC                                              | YES     |
| `finished_at`        | ISO-8601 UTC                                              | YES     |

**Absent fields:** NO `outcome` (no derived column — `status='completed'`
with tracked failures renders misleadingly as plain success), NO
`mode`, NO `warnings`, NO `verification_status`, NO `confirmation_required`,
NO `artifacts`, NO `blockers`.

**Endpoint shape:** `GET /api/waveform-bulk/operations/{id}` returns a
`WaveformBulkOperation` typed model. Bulk start returns a
`WaveformBulkStartResponse` with `id`, `total_tracks`, `eligible_total`.

#### 1.3b Per-Track Waveform Jobs

**Source:** `backend/app/services/waveform_job_service.py`,
`backend/app/models/waveform.py`

These are single-track lifecycle rows (`waveform_jobs` table), not a
batched operation. They carry:

| Field              | Meaning                                             |
|--------------------|-----------------------------------------------------|
| `id`               | uuid; job identifier                                |
| `library_id`       | library identifier                                  |
| `track_id`         | target track                                        |
| `status`           | `queued` / `processing` / `succeeded` / `failed` / `cancelled` |
| `generation_key`   | identity key distinguishing source content versions |
| `cancel_requested` | boolean flag                                        |
| `error_code`       | structured error code (not free-text)               |
| `created_at` / `started_at` / `finished_at` | timestamps               |

Track-level waveform state lives in a separate `waveform_track_state`
table with `status`: `not_generated`, `queued`, `processing`, `ready`,
`failed`, `cancelled`, `unsupported`, `stale`.

**Notable:** The `waveform_operations` bulk table has no outbound link to
the per-track `waveform_jobs` table — only the top-level operation ID is
exposed; per-track progress is visible only through the bulk operation's
incremental count fields.

---

### 1.4 Controlled Tag-Write Operations

**Source:** `backend/app/services/tag_write_service.py`,
`backend/app/schemas/tag_write.py`,
`backend/app/core/db.py` (table `tag_write_operations`)

**Operation table:** `tag_write_operations`

| Field                  | Meaning                                                  | Present |
|------------------------|----------------------------------------------------------|---------|
| `id`                   | uuid4 hex; operation identifier                          | YES     |
| `status`               | `previewed` / `running` / `completed` / `failed` / `partially_failed` / `restored` | YES |
| `track_count`          | number of tracks in the apply request                    | YES     |
| `applied_count`        | tracks where tags were written and verified              | YES     |
| `skipped_count`        | blocked / no-op tracks                                   | YES     |
| `failed_count`         | tracks where write or verification failed                | YES     |
| `plan_json`            | the full plan items (blocked/reason, diff fields)        | YES     |
| `backup_manifest_json` | per-track backup records (sha256, original stat)         | YES     |
| `result_json`          | per-track apply results (applied/skipped/failed/verified)| YES     |
| `warnings_json`        | JSON array                                               | YES     |
| `error_reason`         | max 200 chars                                            | YES     |
| `created_at`           | ISO-8601 UTC                                             | YES     |
| `started_at`           | ISO-8601 UTC                                             | YES     |
| `finished_at`          | ISO-8601 UTC                                             | YES     |
| `restored_at`          | ISO-8601 UTC — set when the operation is restored        | YES     |

**Unique status vocabulary:**
- `running` — in progress
- `completed` — all non-blocked tracks succeeded (applied + verified)
- `failed` — every non-blocked track failed
- `partially_failed` — some applied, some failed (not all failed, not all
  succeeded)
- `restored` — the operation was rolled back via hash-verified backup
  restore; `restored_at` is set
- `previewed` — reserved for future use, never set by current code

**Confirmation model:** `confirm=true` required at apply time; `expected`
(stat echo-back from the plan) enforces staleness detection at apply time
so a file that changed after plan preview is blocked.

**Verification model:** Every written field is postcondition-verified
(re-read and compare). Field-level mismatches become per-track failures
with a preserved backup for restore. The restore endpoint
(`POST .../restore/{track_id}`) accepts `confirm=true` and returns
`restored`/`verified`/`relative_path`.

**Absent fields:** NO `operation_type` (the table only stores
tag-write operations, so no discriminator exists), NO
`mode`, NO `affected_item_ids` as a separate column (the track list
lives inside `plan_json`), NO `verification_status` as a separate column
(verification is per-track in `result_json`), NO `blockers` column (blocked
tracks are recorded in `plan_json` with `blocker` reasons), NO `outcome`
(status vocabulary covers it directly).

---

### 1.5 Reconciliation Plan/Apply/Rollback and Ledger Evidence

This workflow family spans multiple stages with distinct models:

#### 1.5a Findings and Plan Phase

**Source:** `backend/app/services/reconciliation_findings_service.py`,
`backend/app/services/reconciliation_plan_service.py`,
`utils/path_reconciliation.py`

Findings are read-only discovery: `indexed_missing_file`, `untracked_file`,
`stale_path`, `path_candidate`. Plan proposal (`POST .../plans/propose`)
produces a plan artifact with:
- `generated_at`, `plan_artifact` (path)
- `apply_supported` (always false for filesystem actions — only DB-only
  actions are supported)
- `planned_action_summary` (counts by action type)
- `planned_actions` (list of action dicts with `action`, `old_path`,
  `new_path`, `review_tier`, `score`, `evidence`, `source_rows`)
- `audit_summary`, `limitations`
- `message`

Plan validation (`validate-plan`) adds per-action `status` (`valid` /
`invalid` / `skipped`), `issues`, and `warnings`.

**Absent:** NO operation row in any persistence table at this stage —
findings and proposals are read-only or produce saved JSON artifacts on
disk.

#### 1.5b Apply Phase

**Source:** `backend/app/services/reconciliation_apply_service.py`,
`backend/app/schemas/reconciliation.py`

Apply preview (`POST .../apply/preview`):
- `plan_path`, `plan_id`, `root`
- `db_only` (always true — filesystem mutations are unsupported)
- Per-action: `action_id`, `eligible` (boolean), `blockers` (array of
  string codes), `operation_type` (`update_path_reference` /
  `mark_stale_processed_state_path`)
- `message`

Apply (`POST .../apply`, `confirm=true`):
- `plan_id`, `db_only`
- Results: `action_id`, `ledger_id`, `status` (`applied`), `backup_path`,
  `backup_sha256`, `verification_status` (`verified`)
- `message`

Each apply produces an append-only ledger entry in
`reconciliation_ledger` table (`<root>/logs/processed.db`):
- `ledger_id` (`recon-{uuid}`)
- `created_at`, `root`
- `operation_type` (same as action type)
- `old_path`, `new_path`
- `affected_tables` (JSON array)
- `before_values_json`, `after_values_json` (with `apply_contract`,
  `verification_status`, `plan_id`, `action_id`, `backup_path`,
  `backup_sha256`, `rows`)
- `status` (`applied`)
- `error`

**Operation ID model:** The reconciliation operation carries a `ledger_id`
(not a unified `operation_id`), recorded in the selected library's
`processed.db`, NOT in the backend's `jobs.db`. This is the only operation
type that lives in `processed.db`.

**Confirmation model:** `confirm=true` required.

**Verification model:** Backup (`_sqlite_backup`) created under writer lock,
backup sha256 verified, pre-state rows checked in backup, write applied
with exact WHERE clause, postcondition verified against expected state,
ledger entry inserted with full before/after provenance. Every field is
required for rollback validity.

**Cancellation:** No `cancel_requested` field — reconciliation applies are
synchronous, narrow (exactly one action), and validated before the
mutation begins. If an error occurs inside the writer transaction, it
rolls back the entire transaction and no ledger entry is written.

#### 1.5c Rollback Phase

**Source:** `reconciliation_apply_service.py`, function `rollback()`

Rollback (`POST /ledger/{ledger_id}/rollback`, `confirm=true`):
- `ledger_id` (new rollback ledger entry ID)
- `rollback_of_ledger_id` (references the original apply ledger entry)
- `status` (`rolled_back`)
- `backup_path`, `backup_sha256`
- `verification_status` (`verified`)

Rollback creates a new ledger entry with `operation_type` prefixed
`rollback:` and `status` set to `rolled_back`. It reads the original apply provenance, verifies the current
DB state matches the applied after-state, creates a backup of the
current-state, restores the before-state with postcondition checks, and
writes a new ledger row. Idempotent — a second rollback of the same ledger
entry is rejected.

---

### 1.6 Publish / Export / Sync Operations

**Source:** `backend/app/services/publish_operations_service.py`,
`backend/app/services/publish_export_service.py`,
`backend/app/services/publish_sync_service.py`,
`backend/app/schemas/publish.py`,
`backend/app/core/db.py` (table `publish_operations`)

**Operation table:** `publish_operations`

| Field                      | Meaning                                              | Present |
|----------------------------|------------------------------------------------------|---------|
| `id`                       | uuid4 hex; operation identifier                      | YES     |
| `operation_type`           | `export` / `sync`                                    | YES     |
| `export_target`            | crate annotation where export metadata is stored     | YES     |
| `sync_source`              | `library` (only supported source)                    | YES     |
| `job_id`                   | for sync, links to the backing rsync `jobs` table row| YES     |
| `mode`                     | always `apply`                                       | YES     |
| `status`                   | `running` / `completed` / `failed` / `cancelled`     | YES     |
| `crate_id` / `crate_name`  | for export only                                      | YES     |
| `scope`                    | descriptive string (e.g. library directory path)     | YES     |
| `track_count`              | tracks in the crate/export                           | YES     |
| `destination_relative`     | root-relative description of export file destination | YES     |
| `result`                   | human-readable summary                               | YES     |
| `verification_status`      | `verified` / `failed` / `skipped`                    | YES     |
| `verification_details_json`| JSON array, max 50 entries                           | YES     |
| `warnings_json`            | JSON array, max 50 entries                           | YES     |
| `error_reason`             | max 200 chars                                        | YES     |
| `created_at`               | ISO-8601 UTC                                         | YES     |
| `started_at`               | ISO-8601 UTC                                         | YES     |
| `finished_at`              | ISO-8601 UTC                                         | YES     |

**Lifecycle phases (both export and sync):**

1. **Readiness** (`GET /publish/readiness/{crate_id}`): read-only snapshot.
   Returns `blockers`, `warnings`, `conflicts`, `confirmation_required`,
   `next_operation`.

2. **Preview** (`GET /publish/export/{crate_id}/preview` or
   `POST /publish/sync/preview`): read-only. Returns `blockers`,
   `warnings`, `confirmation_required`, plus operation-specific details
   (e.g. `target_path`, `no_overwrite`, `ssd_mounted`, `files`).
   Preview is never persisted as an operation.

3. **Confirm+Execute** (`POST /publish/export/{crate_id}` or
   `POST /publish/sync/confirm`, `confirm=true`): creates a `publish_operations`
   row. Export is synchronous; sync dispatches a background rsync job and
   returns a `job_id`.

4. **Verify:** Export reads back and verifies the written file. Sync
   reports through per-track change lists.

5. **History:** `GET /publish/operations` returns
   `PublishOperationSummary[]`.

**Absent fields:** NO `cancel_requested` (export is synchronous; sync links
to a `jobs` row that supports cancellation), NO `outcome` (derived), NO
`blockers` in the operational row (blockers prevent the operation from
starting), NO `affected_item_ids`.

---

### 1.7 Generic Background Jobs / Toolkit Runner Jobs

**Source:** `backend/app/services/job_service.py`,
`backend/app/services/toolkit_runner.py`,
`backend/app/models/job.py`, `backend/app/schemas/job.py`

**Operation table:** `jobs`

| Field              | Meaning                                              | Present |
|--------------------|------------------------------------------------------|---------|
| `id`               | uuid4; job identifier                                | YES     |
| `command`          | allowlisted `pipeline.py` subcommand                 | YES     |
| `args`             | JSON array of CLI flags                              | YES     |
| `status`           | `pending` / `running` / `succeeded` / `failed` / `cancelled` | YES |
| `created_at`       | ISO-8601 UTC                                         | YES     |
| `started_at`       | ISO-8601 UTC (set when subprocess launches)          | YES     |
| `finished_at`      | ISO-8601 UTC                                         | YES     |
| `exit_code`        | OS exit code (null until finished)                   | YES     |
| `log_path`         | path to the per-job log file                         | YES     |
| `pid`              | OS process ID (set after launch, cleared on exit)    | YES     |
| `progress_current` | per-file progress counter (rsync-sync jobs only)     | YES     |
| `progress_total`   | total files (rsync-sync jobs only)                   | YES     |
| `progress_percent` | computed percent (rsync-sync jobs only)              | YES     |
| `progress_message` | human-readable (rsync-sync jobs only)                | YES     |

**Status vocabulary:** `pending` (created, not started), `running`
(subprocess active), `succeeded` (exit code 0), `failed` (non-zero exit),
`cancelled` (SIGTERM sent, process exited).

**Absent fields:** NO `operation_type` (the `command` column serves as a
type discriminator), NO `mode` (always CLI execution), NO
`verification_status`, NO `warnings`, NO `error_reason` (the `exit_code` +
`log_path` serve this role), NO `confirmation_required` (submission is
implicitly confirmed), NO `blockers`, NO `outcome`, NO `cancel_requested`
(cancellation is external via POST and SIGTERM delivery).

---

## 2. Divergence Map

### 2.1 Status Vocabularies

| Workflow          | Status vocabulary                                                                   |
|-------------------|-------------------------------------------------------------------------------------|
| Preparation       | `running`, `completed`, `failed`, `cancelled`                                       |
| Analysis          | `running`, `completed`, `failed`, `cancelled` + **derived** `outcome`               |
| Waveform Bulk     | `running`, `completed`, `failed`, `cancelled`                                       |
| Tag Write         | `previewed`, `running`, `completed`, `failed`, `partially_failed`, `restored`       |
| Reconciliation    | `valid`/`invalid`/`skipped` (plan), `applied` / `rolled_back` (ledger), `verified` (postcondition)  |
| Publish           | `running`, `completed`, `failed`, `cancelled`                                       |
| Jobs              | `pending`, `running`, `succeeded`, `failed`, `cancelled`                            |

**Divergences:**

1. **`succeeded` vs `completed`.** Jobs uses `succeeded`; every other
   workflow uses `completed`. This is a meaningful difference: a
   pipeline.py subprocess with exit 0 may or may not have actually
   "completed" useful work (it depends on the subcommand), whereas
   CrateIQ-owned operations declare `completed` only after their own
   postcondition checks pass.

2. **`partially_failed`.** Only tag writes expose this status. It means
   "not all tracks failed, not all succeeded." Analysis achieves the same
   information via the derived `completed_with_errors` outcome without
   changing the persisted `status`.

3. **`restored`.** Only tag writes have this terminal status, because tag
   writes are the only operation class that supports undo (restore from
   hash-verified backup).

4. **`pending`.** Only Jobs uses this. CrateIQ-owned operations skip
   `pending` and start directly in `running` — there is no queue/approval
   stage within the operation persistence layer.

5. **Reconciliation is not a unified `status`.** It splits across three
   vocabularies: plan validation `status` (`valid`/`invalid`/`skipped`),
   apply eligibility (`eligible` boolean + `blockers`), and ledger
   `status` (`applied` / `rolled_back`).

### 2.2 `status` vs Derived `outcome`

Only analysis operations expose a computed `outcome` (`complete`,
`completed_with_warnings`, `completed_with_errors`). Every other workflow
conflates completion status and operational success in a single `status`
field:

- Preparation: `completed` can hide failed tracks (the `failed_count`
  column is > 0 but the status remains `completed`).
- Waveform Bulk: same — `completed` can hide per-track failures.
- Tag Write: `partially_failed` is the only partial-success vocabulary.
- Jobs: `succeeded` means exit code 0, with no track-level semantics.

### 2.3 Synchronous Result vs Background Job

| Workflow          | Execution model                                              |
|-------------------|--------------------------------------------------------------|
| Preparation       | Background (single operation row, cooperative cancellation)  |
| Analysis (BPM)    | Background (single operation row, cooperative cancellation)  |
| Waveform Bulk     | Background (single operation row, cooperative cancellation)  |
| Tag Write         | Synchronous (operation row created and finished in one call) |
| Reconciliation    | Synchronous (exactly one DB-only action, within a transaction)|
| Export            | Synchronous                                                  |
| Sync              | Background (linked to a `jobs` table row for rsync progress) |
| Jobs              | Background (subprocess dispatch, signal-based cancellation)  |

This has implications:
- Background operations need a `cancel_requested` flag and cooperative
  cancellation semantics (preparation, analysis, waveform bulk do this;
  Jobs uses external SIGTERM).
- Synchronous operations need no `cancel_requested` field.
- Sync is special: it dispatches a background rsync but tracks it through
  the `jobs` table rather than a dedicated operation mechanism.

### 2.4 Review Decision vs Execution State

- **Needs Review** (the human decision queue): entirely separate from
  operation models. It has its own vocabulary (`keep`/`ignore`/
  `review_later`/`unresolved` for duplicates; `reviewed`/`ignore`/
  `review_later`/`unresolved` for quality; enrichment review has its own
  apply/discard model).
- **Process All** auto-applies HIGH-confidence fields and others go to
  review — the preparation operation row records `needs_review_count` as
  an aggregate but has no per-track review decision mapping.

The roadmap (`docs/CRATEIQ_ROADMAP.md`, Cross-Phase Product Contracts)
makes this distinction explicit:
```text
Detected -> Classified -> Proposed -> Reviewed -> Approved -> Applied -> Verified
```

### 2.5 Preview Token/State vs Apply State

| Workflow     | Preview mechanism                                                     |
|--------------|-----------------------------------------------------------------------|
| Tag Write    | `build_plan()` returns `expected_size` + `expected_mtime_ns` to echo back at apply time |
| Reconciliation| Plan artifact (JSON file), `plan_id` echoed back at apply time       |
| Analysis     | `GET/POST preview` returns candidate list (never persisted)          |
| Waveform Bulk| `GET preview` returns counts (never persisted)                       |
| Publish      | `GET preview` returns `target_path`, `no_overwrite` details          |
| Preparation  | No preview — confirmed run creates the operation row immediately     |
| Jobs         | No preview — submission creates the job immediately                  |

Only tag writes and reconciliation encode a staleness check: tag writes
verify file stat hasn't changed since preview; reconciliation verifies
`plan_id` matches the reloaded plan sha256.

### 2.6 Verification: Inline vs Operation History

- **Tag Write**: verification is per-track, inline, and stored in
  `result_json`. The operation's overall status reflects it.
- **Publish**: `verification_status` is a dedicated column (`verified`
  / `failed` / `skipped`) with per-item `verification_details`.
- **Reconciliation**: verification is embedded in `before_values_json`
  and `after_values_json` with `verification_status: "verified"`, plus a
  backup sha256 verification step.
- **Analysis**: no verification column — the `outcome` derivation and
  per-track `succeeded`/`failed` counts serve this role.
- **Preparation / Waveform Bulk / Jobs**: no verification model at all.

### 2.7 ID Generation Sources

| Workflow          | ID source                     | Storage DB          |
|-------------------|-------------------------------|---------------------|
| Preparation       | `uuid.uuid4().hex` in service | `jobs.db`           |
| Analysis          | `uuid.uuid4().hex` in service | `jobs.db`           |
| Waveform Bulk     | `uuid.uuid4().hex` in service | `jobs.db`           |
| Tag Write         | `uuid.uuid4().hex` in service | `jobs.db`           |
| Publish           | `uuid.uuid4().hex` in service | `jobs.db`           |
| Reconciliation    | `recon-{uuid.uuid4().hex}`    | `processed.db`      |
| Jobs              | `uuid.uuid4()` (with dashes)  | `jobs.db`           |

Reconciliation is the only workflow that uses a different ID format and a
different database. Jobs uses full UUID format (with dashes) while all
CrateIQ-owned operations use hex format (no dashes).

### 2.8 Cancellation Support

| Workflow          | Cancellation                          |
|-------------------|---------------------------------------|
| Preparation       | `cancel_requested` flag + cooperative |
| Analysis          | `cancel_requested` flag + cooperative |
| Waveform Bulk     | `cancel_requested` flag + cooperative |
| Tag Write         | N/A (synchronous)                     |
| Reconciliation    | N/A (synchronous, single-action)      |
| Export            | N/A (synchronous)                     |
| Sync              | Via linked `jobs` table row           |
| Jobs              | SIGTERM to subprocess PID             |

### 2.9 Harmless vs Problematic Divergences

**Harmless (domain-specific, preserved):**

- Tag write `partially_failed`/`restored` states — tag writes are the only
  reversible operation; the vocabulary is justified.
- Jobs `pending` — pipeline subprocess dispatch genuinely has a queue
  phase.
- Reconciliation's separate DB storage — it acts on `processed.db`, not
  operational state, so it naturally lives there.

**Inconsistent (impedes shared UI/API handling):**

1. **No `outcome` on preparation and waveform bulk.** `status='completed'`
   with `failed_count > 0` renders as plain success. Analysis already
   solves this with a derived `outcome` column; the other workflows could
   benefit from the same pattern without changing their persisted `status`
   vocabulary.

2. **Count field naming differences.** `succeeded` (analysis) vs
   `generated` (waveform) vs `written_count` (preparation) vs
   `applied_count` (tag write) — they all mean "successfully processed
   items" but use different names. A shared `succeeded_count` / `failed_count`
   convention would simplify list rendering.

3. **Warnings storage varies.** Analysis uses `warnings_json`,
   preparation uses `warnings_json`, but waveform bulk has no warnings
   column at all.

4. **Job status `succeeded` vs operational `completed`.** The frontend
   already has separate renderers for these, but a shared adapter
   vocabulary could collapse them without changing schemas.

5. **No verification column on most workflows.** Only publish and
   reconciliation have a `verification_status`. Analysis encodes
   verification in the `outcome` + `succeeded` counts. Preparation and
   waveform bulk have no verification at all.

---

## 3. Proposed Minimum Shared Contract

This vocabulary is derived from what the current workflows already encode,
either explicitly or implicitly. None of it is new schema — every field
maps to something that already exists, or can be derived from existing data
by a read adapter.

### 3.1 Shared Fields

| Field                  | Meaning                                                              | Required | Current Reality                                        |
|------------------------|----------------------------------------------------------------------|----------|--------------------------------------------------------|
| `operation_id`         | unique identifier for the operation                                  | required | `id` column on every table; already universal          |
| `operation_type`       | discriminator for the workflow family                               | required | `operation_type` (prep/publish) / `job_type` (analysis) / `command` (jobs); missing on tag write |
| `mode`                 | `read`, `preview`, `dry_run`, `apply`, `apply_scoped`               | optional | `mode` column only on analysis; implicit on others     |
| `scope`                | what the operation targeted (track count, crate ID, plan artifact)   | optional | varies: `track_count`, `crate_id`, `scope`, `plan_path`|
| `status`               | coarse lifecycle: `pending` > `running` > terminal                  | required | universal, but vocabularies differ                     |
| `status_terminal` types| `completed`, `failed`, `cancelled` (`succeeded` in jobs)            | N/A      | shared across most workflows                           |
| `outcome`              | derived, user-facing: `complete` / `completed_with_warnings` / `completed_with_errors` | recommended | exists only for analysis; should be derived for others |
| `succeeded_count`      | items that successfully completed the operation                     | recommended| `succeeded`/`generated`/`written_count`/`applied_count` |
| `failed_count`         | items that failed unrecoverably                                      | recommended| `failed` column on every applicable workflow           |
| `skipped_count`        | items that were ineligible or already satisfied                     | optional   | `skipped` column on most workflows                     |
| `remaining_count`      | items still needing work after this operation                       | optional   | `remaining_missing` (analysis, waveform)               |
| `warnings`             | bounded list of non-blocking warning strings                        | recommended| `warnings_json` on analysis, prep; missing on waveform |
| `blockers`             | reasons the operation cannot proceed / is ineligible                | optional   | exists for reconciliation and publish (preview phase)  |
| `affected_item_ids`    | list of item IDs that were operated on                              | optional   | not stored (except in JSON fields like plan_json)      |
| `confirmation_required`| whether an explicit confirmation gate must be passed                | optional   | not persisted; enforced at API boundary                |
| `verification_status`  | `verified` / `failed` / `skipped`                                   | optional   | exists for publish and reconciliation only             |
| `error_code`           | structured error code (not free-text)                               | optional   | `error_code` on waveform jobs; `error_reason` elsewhere|
| `error_reason`         | free-text error description (max 200 chars)                         | optional   | `error_reason` on most operations                      |
| `artifacts`            | output artifacts (plan paths, backup paths, ledger IDs)             | optional   | scattered across different columns; reconciliation has the richest model |
| `created_at`           | when the operation row was first persisted                          | required  | universal                                              |
| `started_at`           | when execution began                                                | optional   | universal                                              |
| `finished_at`          | when execution reached a terminal state                             | optional   | universal                                              |
| `cancel_requested`     | whether the user asked to stop (background operations)              | optional   | preparation, analysis, waveform bulk, waveform jobs    |

### 3.2 Shared Status Vocabulary (Recommended Convergence)

For **CrateIQ-owned operations** (preparation, analysis, waveform bulk,
tag write, publish):

```
running → completed | failed | cancelled
```

Plus domain-extended terminal states:
- `partially_failed` (tag write)
- `restored` (tag write)
- `completed_with_errors` / `completed_with_warnings` (derived `outcome`,
  not a change to `status`)

For **pipeline jobs**:
```
pending → running → succeeded | failed | cancelled
```

`pending` and `succeeded` remain separate from `running` and `completed`
because they describe a fundamentally different execution model (CLI
subprocess dispatch vs. service-owned background work).

### 3.3 Outcome Derivation (Recommended)

Every background operation with per-item counts should derive an `outcome`
(from existing `status` + counts):

```
if status in ('cancelled', 'failed', 'running'):
    outcome = status
else:  # status == 'completed'
    if failed_count > 0:
        outcome = 'completed_with_errors'
    elif recovered_count > 0:  # where applicable
        outcome = 'completed_with_warnings'
    else:
        outcome = 'complete'
```

This derivation is zero-cost at the schema level — it's a computed field
in the service layer or a read adapter, never a new column. Analysis
already implements this pattern exactly.

---

## 4. Lifecycle Rules

These distinctions are already maintained in the product contract
(`docs/CRATEIQ_ROADMAP.md`, AGENTS.md). This section restates them in
the context of operation/result modeling.

### 4.1 Product Distinctions (Preserved)

1. **Detection/proposal is not review.** Reconciliation plan proposal is a
   read-only suggestion. Review decisions are separate from plan contents.

2. **Review/approval is not apply.** Human decisions sit in review tables.
   Apply requires `confirm=true` and revalidates the plan/scope at
   execution time.

3. **Apply is not verification.** Apply writes data or files.
   Verification re-reads and proves the write was correct. These are
   separate phases with separate result fields (e.g. publish
   `verification_status`; reconciliation `before_values_json` /
   `after_values_json` `verification_status`).

4. **Successful dispatch/start is not successful completion.** A
   `running` status must never be rendered as a terminal state. Startup
   recovery closes interrupted `running` rows to `failed`.

5. **Completed-with-warnings/errors must not render as plain success.**
   The `outcome` derivation (Section 3.3) is the recommended mechanism.

6. **Cancellation is distinct from failure.** `status='cancelled'` must
   never be confused with `status='failed'`. The `cancel_requested` flag
   exists separately from `error_reason`.

7. **Read/preview paths remain non-mutating.** Previews and readiness
   checks never create operation rows, never mutate data, and never spawn
   background work. Only bounded operational/cache/report writes are
   permitted (e.g. saving a plan artifact to disk; writing a readiness
   snapshot).

### 4.2 Shared Lifecycle (Existing)

The roadmap's cross-phase lifecycle is already referenced by several
workflows:

```text
Detected -> Classified -> Proposed -> Reviewed -> Approved -> Applied -> Verified
```

Side states: `rejected`, `deferred`, `blocked`, `skipped`, `failed`,
`cancelled`.

Future operation result models should use this vocabulary instead of
inventing new state names where the same concept already exists.

---

## 5. Compatibility / Migration Strategy

### 5.1 Staged Convergence (Recommended Order)

**Stage 1 — Read adapters only (no schema changes):**
Create a shared Python module (`backend/app/services/shared_operation.py`
or similar) that reads any operation row from any table and normalizes it
to a minimum common shape for list rendering. This abstracts away count
field naming differences (`succeeded` vs `generated` vs `written_count`)
and derives `outcome` where missing. No column is added, no migration
runs, no route changes.

**Stage 2 — Type definitions shared at the frontend:**
Define a single `BaseOperation` TypeScript interface in a new
`frontend/src/types/operations.ts` that maps onto the shared vocabulary
from Section 3.1. Each workflow's existing type can extend it or be
mapped via an adapter function at the API boundary. The frontend already
has a consistent `StatusStrip` / `Badge` pattern from Phase 1 that can
receive normalized status/outcome values.

**Stage 3 — Additive backend fields only when needed:**
When a concrete consumer (e.g. a command-center Home "recent operations"
widget or a unified jobs/operations list) genuinely needs a field that is
missing (e.g. `warnings` on waveform bulk, `verification_status` on
analysis), add only that field, with a backward-compatible migration
(default NULL), and wire it incrementally.

**Stage 4 — Workflow-by-workflow adoption:**
Once shared types exist, adopt them in one workflow at a time. Start
with analysis (already has the richest model with `outcome` derivation),
then waveform bulk, then preparation, then publish.

**Stage 5 — Deprecation only after coverage:**
`job.status = 'succeeded'` vs. `operation.status = 'completed'` is a
genuine semantic difference (CLI exit code vs. postcondition-checked
completion). Do not rename them. Provide a read-adapter normalization
that maps both to a shared rendering vocabulary.

### 5.2 What Must Remain Workflow-Specific

1. **Tag write `restored` / `partially_failed` status.** These describe
   behavior no other workflow has (reversible mutation + partial
   success).

2. **Jobs `pending` state and `exit_code`.** CLI dispatch is fundamentally
   different from service-owned background work.

3. **Reconciliation ledger `ledger_id` format.** The `recon-{uuid}`
   prefix and `processed.db` storage are deliberate — reconciliation acts
   on the library DB, not operational state.

4. **Per-track waveform job state.** `queued` / `processing` /
   `succeeded` / `failed` / `cancelled` with `error_code` and
   `generation_key` is a fundamentally different granularity from
   batched operations. It should not be forced into a batch operation
   model.

5. **Plan/preview-specific response shapes.** The tag write plan (with
   per-field diffs and expected stats), the reconciliation apply
   preview (with per-action eligibility and blocker codes), and the
   publish preview (with target paths and destination checks) are
   intentionally rich. They should remain domain-specific while the
   *operation rows they eventually produce* converge toward the shared
   vocabulary.

---

## 6. Non-Goals

This document does NOT authorize:

- Database migration or schema changes to any existing table
- API breaking changes or route contract changes
- Cross-database operation-ID rewrites (e.g. reconciliation ledger IDs
  are not becoming `jobs.db` operation IDs)
- Filesystem mutation of any kind
- Duplicate execution or background-worker framework changes
- Auth, cloud, or telemetry work
- Broad frontend refactoring
- Any runtime behavior or application source code changes

---

## Appendix A: Table Summary

| Table                    | Database       | Primary ID Format     | Status Column               | Outcome?  | Verify? | Cancel? |
|--------------------------|---------------|------------------------|----------------------------|-----------|---------|---------|
| `preparation_operations` | `jobs.db`     | `{uuid_hex}`           | `running/completed/failed/cancelled` | NO | NO  | YES     |
| `analysis_operations`    | `jobs.db`     | `{uuid_hex}`           | `running/completed/failed/cancelled` | DERIVED | NO  | YES     |
| `waveform_operations`    | `jobs.db`     | `{uuid_hex}`           | `running/completed/failed/cancelled` | NO | NO  | YES     |
| `tag_write_operations`   | `jobs.db`     | `{uuid_hex}`           | `previewed/running/completed/failed/partially_failed/restored` | NO | INLINE | NO |
| `publish_operations`     | `jobs.db`     | `{uuid_hex}`           | `running/completed/failed/cancelled` | NO | YES | NO (export) / via jobs (sync) |
| `jobs`                   | `jobs.db`     | `{uuid_dashed}`        | `pending/running/succeeded/failed/cancelled` | NO | NO  | YES (SIGTERM) |
| `reconciliation_ledger`  | `processed.db`| `recon-{uuid_hex}`     | `applied` / `rolled_back`   | NO        | YES     | NO (synchronous) |
| `waveform_jobs`          | `jobs.db`     | `{uuid_dashed}`        | `queued/processing/succeeded/failed/cancelled` | NO | NO  | YES     |

---

## Appendix B: File Inventory

Files inspected and relied upon for this inventory:

**Backend services:**
- `backend/app/services/preparation_operations_service.py`
- `backend/app/services/preparation_service.py`
- `backend/app/services/analysis_operations_service.py`
- `backend/app/services/analysis_jobs_service.py`
- `backend/app/services/waveform_operations_service.py`
- `backend/app/services/waveform_job_service.py`
- `backend/app/services/waveform_bulk_service.py`
- `backend/app/services/tag_write_service.py`
- `backend/app/services/reconciliation_apply_service.py`
- `backend/app/services/reconciliation_plan_service.py`
- `backend/app/services/publish_operations_service.py`
- `backend/app/services/job_service.py`

**Backend schemas:**
- `backend/app/schemas/job.py`
- `backend/app/schemas/analysis_jobs.py`
- `backend/app/schemas/tag_write.py`
- `backend/app/schemas/publish.py`
- `backend/app/schemas/waveform.py`
- `backend/app/schemas/reconciliation.py`
- `backend/app/schemas/reconciliation_plan.py`

**Backend infrastructure:**
- `backend/app/core/db.py` (all operation table schemas)
- `backend/app/models/job.py`

**Frontend types:**
- `frontend/src/types/publish.ts`
- `frontend/src/types/job.ts`
- `frontend/src/types/tagWrite.ts`
- `frontend/src/types/waveformBulk.ts`
- `frontend/src/types/analysis.ts`
- `frontend/src/types/reconciliation.ts`

**Documentation:**
- `docs/CRATEIQ_ROADMAP.md`
- `NEXT_TASKS.txt`
- `PROJECT_CONTEXT.md`
