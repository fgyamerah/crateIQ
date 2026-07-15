# Path Reconciliation Spec

**Status:** design only  
**Date:** 2026-05-03  
**Runtime changes:** none

## Inspected Files

- `db.py`
- `modules/run_logger.py`
- `modules/filename_normalize.py`
- `modules/library_organize.py`
- `modules/artist_merge.py`
- `modules/artist_folder_clean.py`

## 1. Problem

CrateIQ uses absolute file paths as operational identity across the primary DB, processed-state cache, history tables, cue suggestions, duplicate records, set playlists, review queues, and logs. Current path mutation behavior is not centralized.

Verified current behavior:

- `modules/run_logger.rename_path()` calls `db.rename_processed_path()`.
- `db.rename_processed_path()` updates only `processed_state.filepath`.
- `filename-normalize` calls `_proc.rename_path(src, dst)` after `src.rename(dst)`.
- `library-organize` calls `_proc.rename_path(src, dst)` after `shutil.move`.
- `artist-merge` moves files, upserts a destination `tracks` row, then deletes the old `tracks` row.
- `artist-folder-clean` uses the same move/upsert/delete pattern for `tracks`.

Risks:

- `tracks.filepath` can stale after renames/moves that only update `processed_state`.
- `processed_state` can be updated while other path-bearing tables are not.
- `track_history`, `duplicate_groups`, `cue_points`, and set playlist rows can keep old paths.
- Review queues can point to missing files after moves.
- `DELETE FROM tracks` after moves can erase the old DB row without a durable tombstone.
- Partial filesystem moves can leave DB state only partly updated.

## 2. Proposed Helper

Introduce a central path reconciliation helper conceptually named:

```python
update_track_path_references(old_path, new_path, operation_context)
```

This spec does not require the helper to live in a specific module, but the likely home is `db.py` or a new small reconciliation module used by `modules/run_logger.py`.

### Responsibilities

- Normalize `old_path` and `new_path` consistently.
- Validate that `old_path != new_path`.
- Update all DB path references for one moved/renamed track.
- Update or mark stale known JSON queue references.
- Record an audit ledger row/event.
- Return a structured reconciliation result for logs and caller decisions.

### Conceptual Signature

```python
update_track_path_references(
    old_path: str | Path,
    new_path: str | Path,
    operation_context: PathOperationContext,
) -> PathReconciliationResult
```

### `operation_context`

Required fields:

- `operation_id`
- `command`
- `operation_type`: `rename`, `move`, `quarantine`, `merge`, `folder_clean`, `manual_repair`
- `dry_run`
- `apply`
- `source_module`
- `reason`
- `operator_note`
- `started_at`

Optional fields:

- `expected_old_size`
- `expected_old_mtime`
- `expected_new_size`
- `expected_new_mtime`
- `collision_strategy`
- `quarantine_root`
- `batch_id`

## 3. Required References To Update

### SQLite: `processed.db`

| Reference | Required behavior |
|---|---|
| `tracks.filepath` | Update old path to new path without deleting the old logical record. If collision exists, do not overwrite silently. |
| `tracks.filename` | Update to `Path(new_path).name`. |
| `processed_state.filepath` | Update all stage rows from old path to new path. |
| `track_history.filepath` | Update rows for old path to new path. |
| `track_history.original_path` | Do not rewrite unless the old path is the recorded original path and operation is explicitly a source-path correction. |
| `duplicate_groups.original` | Update old path to new path. |
| `duplicate_groups.duplicate` | Update old path to new path. |
| `cue_points.filepath` | Update old path to new path. |
| `set_playlist_tracks.filepath` | Update old path to new path. |

### SQLite: `jobs.db`

| Reference | Required behavior |
|---|---|
| `bpm_anomalies.filepath` | Update old path to new path when track identity is known. |
| `bpm_anomalies.track_id` | Do not rewrite unless a reliable `tracks.id` mapping exists. Cross-DB FK remains unenforceable. |

### JSON / JSONL Queues

| File/queue | Required behavior |
|---|---|
| `data/intelligence/enrichment_review_queue.json` | Update entries where `file_path == old_path`; preserve existing recommendation and timestamp; add reconciliation metadata. |
| `data/intelligence/artist_repair_queue.json` | Update entries where `file == old_path`; preserve approved/rejected/applied flags. |
| `data/intelligence/artist_review_queue.json` | Update path fields if schema is confirmed; otherwise report as `UNVERIFIED_QUEUE_SCHEMA`. |
| `data/review/artist_review_queue.jsonl` | Prefer append a superseding path event instead of editing JSONL in place; schema must be verified before mutation. |
| AI/enrichment accepted/rejected JSONL datasets | Do not rewrite historical training/audit data. Optionally include path history in reports. |

### Logs

Run logs should generally not be mutated. They are historical records.

Required behavior:

- Append a new reconciliation event to the audit ledger.
- Do not edit old log lines.
- Link operation IDs from reconciliation reports to original command logs where available.

## 4. Transaction Model

### Preferred Move Flow

1. Build move plan.
2. Validate source exists and destination does not conflict, or collision strategy is selected.
3. Open DB transaction.
4. Capture before-state references for all DB tables.
5. Perform filesystem move/rename.
6. If filesystem move succeeds, update DB references in the same control flow.
7. Write audit ledger success event.
8. Commit DB transaction.
9. Update JSON queues after DB commit, using atomic temp-file replace.
10. If queue update fails, keep DB commit but mark queue repair needed in ledger/report.

### Failure Flow

- If filesystem move fails before DB updates, roll back DB transaction.
- If DB update fails after filesystem move, attempt compensating move back only if safe and collision-free.
- If compensating move is unsafe or fails, write a `PARTIAL_FAILURE` ledger event and report immediate manual recovery steps.

### Deletion Policy

- Never delete old `tracks` rows as the primary move cleanup.
- Replace delete+upsert with one of:
  - transactional path update, or
  - tombstone/audit entry that preserves old row identity and moved-to path.
- If a future cleanup deletes rows, it must require a separate explicit maintenance command and preserve an audit record.

## 5. Audit Ledger

Create a durable operation ledger before adopting central reconciliation.

### Required Fields

| Field | Description |
|---|---|
| `operation_id` | Stable UUID for one path operation. |
| `timestamp` | UTC ISO timestamp. |
| `command` | CLI command or backend job command. |
| `source_module` | Module/function initiating the operation. |
| `old_path` | Absolute normalized original path. |
| `new_path` | Absolute normalized destination path. |
| `operation_type` | `rename`, `move`, `quarantine`, `merge`, `folder_clean`, etc. |
| `success` | Boolean final status. |
| `failure_stage` | `precheck`, `filesystem`, `db`, `queue`, `report`, or empty. |
| `before_db_refs` | Counts/IDs of DB rows referencing old path before update. |
| `after_db_refs` | Counts/IDs of DB rows referencing new path after update. |
| `queue_refs_updated` | Queue files and entry counts updated. |
| `rollback_possible` | Boolean plus reason. |
| `notes` | Human-readable details, collision suffix, warnings. |

### Storage Options

Preferred minimal approach:

- SQLite table in `processed.db`: `path_operation_history`
- Optional JSONL mirror under logs for easy inspection

Do not rely only on console output.

## 6. Recovery Behavior

### `path-audit`

Read-only.

Must detect:

- DB paths that do not exist.
- Files under library roots not represented in `tracks`.
- Old paths in `processed_state`, `cue_points`, `duplicate_groups`, and `set_playlist_tracks`.
- Queue entries pointing to missing files.
- Duplicate filename ambiguity where a missing path basename exists in multiple locations.
- Mismatch between `tracks.filepath` and `processed_state.filepath`.

Output:

- Summary counts.
- Per-path findings.
- Suggested repair action.
- Risk level.
- JSON report option.

### `path-reconcile --dry-run`

Read-only repair planner.

Must:

- Propose old/new path mappings.
- Flag ambiguous mappings instead of guessing.
- Show DB tables and queue entries that would change.
- Show whether rollback would be possible.
- Refuse to plan destructive deletes.

### `path-reconcile --apply`

Write mode.

Must:

- Apply only unambiguous mappings.
- Use transaction model above.
- Write audit ledger events.
- Generate report.
- Leave ambiguous cases unresolved.

### `path-history`

Read-only.

Must show:

- Operation history for a path.
- Old/new path chain.
- Command/source module.
- Success/failure state.
- Rollback feasibility.

## 7. CLI Proposal

| Command | Mode | Behavior |
|---|---|---|
| `path-audit` | read-only | Scan DB/queues/filesystem for stale path references. |
| `path-reconcile --dry-run` | read-only | Generate proposed repairs without writing. |
| `path-reconcile --apply` | write | Apply unambiguous repairs with ledger and transactions. |
| `path-history` | read-only | Show path operation ledger for one path or operation ID. |

Recommended flags:

- `--path ROOT`
- `--queue-scan`
- `--include-jobs-db`
- `--json REPORT`
- `--operation-id ID`
- `--strict`
- `--limit N`

## 8. Adoption Plan

### Phase 1: Ledger and Audit Only

- Add `path_operation_history`.
- Add `path-audit`.
- Do not change existing move behavior.
- Use reports to validate assumptions.

### Phase 2: Central Helper for Simple Renames

Integrate into `filename-normalize` first.

Why:

- Single-file rename.
- Existing move point is clear: after `src.rename(dst)`.
- Lower complexity than folder merges.

Required adjustment:

- Replace `_proc.rename_path(src,dst)` with future central helper after successful filesystem rename.
- Preserve preview behavior.

### Phase 3: Library Organize Moves

Integrate into `library-organize`.

Why:

- Similar single-file moves.
- Existing `_proc.rename_path(src,dst)` call sites are clear.

Requirements:

- Handle CHKARTISTNAMES moves separately as quarantine operation type.
- Record collision suffixes.

### Phase 4: Artist Merge

Integrate after simple moves are stable.

Why:

- Higher risk: folder merge, collision suffixes, `DELETE FROM tracks`.

Requirements:

- Remove direct `DELETE FROM tracks` move cleanup.
- Use transaction/tombstone model.
- Record group-level batch ID plus per-file operation IDs.

### Phase 5: Artist Folder Clean

Integrate last among inspected modules.

Why:

- Similar risk to artist merge.
- Includes rename/merge/recovery paths.

Requirements:

- Use helper for every per-file move.
- Preserve folder-level report output.
- Replace delete+upsert with path update/tombstone.

## 9. Risks

| Risk | Handling |
|---|---|
| Duplicate file ambiguity | Do not auto-repair when basename exists in multiple locations; require manual selection. |
| Same filename collisions | Preserve existing collision suffix strategy; record final destination in ledger. |
| Moved files outside pipeline | `path-audit` can detect, but `path-reconcile --apply` should only repair unambiguous matches. |
| Queues referencing deleted files | Mark stale and report; do not drop queue entries silently. |
| Partial move failures | Use transaction/ledger failure stages; attempt safe compensation only when collision-free. |
| Cross-DB references | Update `jobs.db` only when enabled and track identity is reliable. |
| Historical JSONL data | Do not mutate training/audit history; append superseding events instead. |
| Case-only renames | Treat as special operation on case-insensitive filesystems; Linux target is likely safe but portable exports may care. |

## 10. Non-Goals

- No automatic audio-file deletion.
- No broad content-hash dedupe in first implementation.
- No rewrite of historical logs.
- No attempt to repair ambiguous paths automatically.
- No changes to tag metadata ownership.

## Safest Implementation Order

1. Add read-only `path-audit`.
2. Add ledger table and JSON report format.
3. Add dry-run reconciliation planner.
4. Integrate helper into `filename-normalize`.
5. Integrate helper into `library-organize`.
6. Replace `artist-merge` delete+upsert path handling.
7. Replace `artist-folder-clean` delete+upsert path handling.

## Unresolved Design Questions

- Should `path_operation_history` live in `processed.db` only, or also mirror JSONL logs?
- Should queue repair happen in the same command as DB repair, or require `--queue-apply`?
- How should cross-DB `jobs.db.bpm_anomalies.track_id` be reconciled when `tracks.id` changes?
- Should historical `track_history.original_path` ever be rewritten?
- Should reconciliation compute content hashes or rely on size/mtime/basename for the first version?

