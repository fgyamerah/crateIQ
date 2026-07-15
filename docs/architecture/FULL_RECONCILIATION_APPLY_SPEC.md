# Full Reconciliation Apply Spec

**Status:** Phase 7 planning only  
**Scope:** design specification, no runtime behavior  
**Last updated:** 2026-05-06

## 1. Purpose

Full reconciliation apply is the future controlled mechanism for applying reviewed path corrections across CrateIQ's canonical database and related path-based references.

It is distinct from the existing audit and planning tools:

- `path-audit` reports inconsistencies between database state and filesystem state.
- `path-reconcile` dry-run creates a reconciliation plan from audit findings.
- Full reconciliation apply would apply reviewed and approved corrections from a dry-run plan.

The apply model must be designed before implementation because path correction can affect several systems at once:

- `tracks`
- `processed_state`
- enrichment queue files
- enrichment review state
- duplicate tracking
- cue/set references if applicable
- rollback ledgers

The first implementation target must be DB-only reviewed reconciliation. File operations are explicitly out of scope until DB-only apply and rollback are proven.

## 2. Non-Goals

Full reconciliation apply must not become a blind repair tool.

Non-goals:

- No blind automatic apply.
- No audio tag writes.
- No BPM, key, beatgrid, or cue mutation.
- No uncontrolled deletes.
- No cross-root operations.
- No unreviewed path moves.
- No broad filesystem cleanup.
- No inference-only metadata correction.
- No apply behavior for `WEAK_MATCH`.
- No replacement for Rekordbox or Mixed In Key ownership.

The system should prefer `no_op_review_required` over a risky correction.

## 3. Preconditions

Full reconciliation apply must refuse to run unless all preconditions pass.

Required operator preconditions:

- Git working tree is clean.
- A backup of `<root>/logs/processed.db` exists.
- Selected library root is explicitly confirmed.
- Latest path-audit report exists.
- Latest path-reconcile dry-run plan exists.
- Review/export state is backed up.
- Operator has confirmed apply mode with `--yes`.

Required system preconditions:

- Reconcile plan root matches selected root.
- Audit report root matches selected root.
- Plan was generated from the latest audit or explicitly acknowledged as stale.
- Plan schema version is supported.
- All paths are absolute after normalization.
- Every candidate path is contained by selected root.
- No pending plan validation errors exist.

Recommended backup artifacts:

```text
<root>/logs/processed.db
<root>/data/intelligence/enrichment_review_queue.jsonl
<root>/data/intelligence/enrichment_review_state.json
<root>/logs/path_audit/latest report
<root>/logs/path_reconcile/latest plan
```

## 4. Reconciliation Sources

Full reconciliation apply must only operate from explicit sources.

### `tracks`

`tracks` is the canonical current-state table. It represents the active library view used by the backend and dashboard.

Apply behavior should treat `tracks.filepath` as the primary current path reference when `tracks` is populated.

### `processed_state`

`processed_state` is history and audit. It tracks stage processing outcomes and incremental-run state.

Apply behavior may update active non-stale rows only when the plan explicitly says those rows represent the same current file path. Stale rows are protected by default.

### Review State JSON

Review state lives at:

```text
<root>/data/intelligence/enrichment_review_state.json
```

It may contain path references captured at review time. Queue/reference update operations must treat it as a path-reference artifact, not as canonical track metadata.

### Path Audit Reports

Path audit reports live under:

```text
<root>/logs/path_audit/
```

Audit reports provide findings and summary counts. They are input evidence, not apply instructions by themselves.

### Reconcile Plan JSON

Reconcile plans live under:

```text
<root>/logs/path_reconcile/
```

The reconcile plan is the primary apply input. Full apply must not synthesize new operations outside the reviewed plan.

## 5. Apply Modes

Full reconciliation apply should expose narrow modes.

### `--apply-reviewed`

Applies only reviewed operations from the latest or selected reconcile plan.

Requirements:

- Operation has human approval.
- Operation type is supported.
- Safety gates pass at apply time.
- Dry-run parity hash matches or drift is explicitly rejected.
- Rollback ledger row is created before commit completion.

### `--apply-auto-safe-only`

Applies only operations classified as auto-safe by the planner.

This mode already exists in narrower form for processed-state path updates. In full reconciliation it must remain conservative and should not expand to file moves or queue rewrites without separate review.

Requirements:

- Planner classification is `AUTO_SAFE`.
- Current DB/filesystem state still matches dry-run assumptions.
- No cross-root paths.
- No collision.
- Transaction and ledger are required.

### `--mark-stale-only`

Marks eligible processed-state path references as stale without changing path values.

Requirements:

- Only `processed_state` rows are affected.
- Existing stale rows are left unchanged.
- `tracks` is not changed.
- Queue files are not changed.
- Ledger records before/after status.

### `--rollback <ledger-id>`

Rolls back operations recorded in a specific ledger.

Requirements:

- Ledger exists.
- Ledger is complete and verified.
- Current state is compatible with rollback.
- Rollback itself writes a new ledger entry.
- Rollback refuses if later dependent ledgers exist unless explicitly forced by a future, separately designed recovery mode.

## 6. Operation Types

The reconcile plan must use explicit operation types.

### `update_tracks_path`

Updates `tracks.filepath` and `tracks.filename` from `old_path` to `new_path`.

Allowed only when:

- `old_path` exists in `tracks`.
- `new_path` exists on disk.
- `new_path` does not already exist in `tracks` for another row.
- Both paths are under selected root.

### `update_processed_state_path`

Updates active non-stale `processed_state.filepath` rows from `old_path` to `new_path`.

Allowed only when:

- Rows are not stale.
- Stage/status criteria match the plan.
- `old_path` exists in `processed_state`.
- `new_path` exists on disk or is a DB-only reference to an already verified path.

### `update_queue_reference`

Updates path references in queue-style JSON/JSONL artifacts after DB success.

Potential files:

- enrichment review queue
- enrichment review state
- artist review queues
- future reconciliation review exports

Allowed only after DB transaction success and only when the queue file still matches the plan's expected before state.

### `mark_stale_processed_state_path`

Marks processed-state rows stale without changing the path.

Allowed only when:

- Row is not already stale.
- Plan identifies the row as superseded or orphaned.
- No current `tracks` row depends on the same path.

### `archive_orphan_reference`

Archives a reference that is no longer valid without deleting the original evidence.

This is a metadata/reference archival operation, not a file delete.

Allowed only when:

- The orphan reference is confirmed.
- The archival target is under selected root.
- The original artifact is preserved or ledgered.

### `no_op_review_required`

Represents a finding that must not be applied automatically.

Required for:

- Ambiguous matches.
- Weak matches.
- Missing target files.
- Potential duplicate targets.
- Cross-root paths.
- Any `REVIEW_CAREFULLY` classification without human approval.

## 7. Safety Gates

Every apply operation must pass safety gates at execution time.

Required gates:

- Root containment.
- `old_path` must exist in the expected DB table or reference artifact.
- `new_path` must exist on disk for path correction operations.
- `new_path` must not collide with an existing canonical row.
- Confidence/review requirement.
- Transaction required for DB changes.
- Rollback ledger required.
- Dry-run parity required.

Additional gates:

- Selected root must match plan root.
- Plan must match current schema version.
- Operation type must be known.
- Operation must not touch BPM/key/cue fields.
- Operation must not write audio tags.
- Operation must not modify files unless a future file-operation phase explicitly enables it.
- `WEAK_MATCH` must never apply.
- `REVIEW_CAREFULLY` must require human approval.

Dry-run parity should compare:

- Operation ID.
- Operation type.
- Old path.
- New path.
- Expected affected table names.
- Expected row IDs when available.
- Plan confidence/review state.
- Plan root.

If parity fails, apply must stop before mutation.

## 8. Rollback Ledger

Every apply operation must create a rollback ledger.

Recommended storage:

```text
<root>/logs/reconciliation_ledger/
```

Optional DB table name:

```text
reconciliation_ledger
```

Ledger schema:

| Field | Description |
|---|---|
| `ledger_id` | Stable unique ledger identifier |
| `timestamp` | UTC timestamp |
| `root` | Selected library root |
| `operation_type` | Explicit operation type |
| `old_path` | Previous path/reference |
| `new_path` | New path/reference |
| `affected_tables` | Tables or artifacts touched |
| `before_values` | JSON object with pre-apply values |
| `after_values` | JSON object with post-apply values |
| `status` | `planned`, `applied`, `rolled_back`, `failed`, `partial` |
| `error` | Error string or null |

Recommended extra fields:

- `plan_id`
- `operation_id`
- `audit_report_path`
- `reconcile_plan_path`
- `dry_run_hash`
- `operator`
- `created_by_version`
- `rollback_of_ledger_id`

Ledger rows must be append-only. Rollback should not delete the original ledger.

## 9. Transaction Model

### DB Changes

Database changes must be transactional.

One operation may use one transaction, or a reviewed batch may use one transaction if all operations are independent and can be safely rolled back together.

Initial implementation should prefer one transaction per operation for simpler rollback and failure isolation.

### Filesystem Changes

Filesystem changes are not part of the initial full reconciliation apply.

Future file operation support must stage file changes before commit and must include:

- source path check
- destination path check
- collision check
- reversible move strategy
- ledger entry
- rollback test coverage

### Queue Changes

Queue/reference changes should occur after DB success.

For queue files:

- Read current file.
- Verify expected before state.
- Write temp file under same directory.
- fsync if practical.
- Atomic replace.
- Record ledger before/after snippets.

If queue update fails after DB success:

- Mark ledger `partial`.
- Do not hide the DB success.
- Emit recovery instructions.
- Provide a retry operation for queue-reference update.

### Failure Handling

Failure handling must be explicit:

- Failure before transaction: no mutation.
- Failure during DB transaction: rollback transaction.
- Failure after DB commit but before queue update: ledger is `partial`.
- Failure during rollback: rollback ledger is `failed` or `partial`.

### Rollback Behavior

Rollback should use ledger before/after values, not fresh inference.

Rollback must verify:

- Current values equal ledger `after_values`.
- Reverting to `before_values` will not collide.
- Root containment still holds.

If verification fails, rollback must stop and report manual intervention required.

## 10. Queue/Reference Handling

Path references may exist outside `tracks` and `processed_state`.

### Enrichment Queue

File:

```text
<root>/data/intelligence/enrichment_review_queue.jsonl
```

Update rules:

- Only update path fields matched by old path.
- Preserve all candidate metadata.
- Preserve original review context.
- Record before/after queue line snippets in ledger.

### Review State

File:

```text
<root>/data/intelligence/enrichment_review_state.json
```

Update rules:

- Update path snapshots only when track identity is stable.
- Preserve approved/rejected/deferred status.
- Preserve timestamps unless the state file itself has an explicit updated timestamp for reference maintenance.

### Duplicate Groups

Potential table:

```text
duplicate_groups
```

Update rules:

- Update `original` and `duplicate` path references only when the plan identifies the same file identity.
- Never delete duplicate history.
- If uncertain, emit `no_op_review_required`.

### Cue/Set References

Potential tables/artifacts:

- `cue_points`
- set playlist tables
- playlist exports

Rules:

- Never update cue timing, cue labels, BPM, key, beatgrid, or musical values.
- Path-reference updates are allowed only when identity is proven.
- If Rekordbox/Mixed In Key owns the reference, prefer external re-export or manual verification.

## 11. CLI Design

Proposed commands:

```bash
python3 pipeline.py path-reconcile --root <root> --apply-reviewed --yes
```

Applies reviewed operations from the selected/latest reconcile plan.

```bash
python3 pipeline.py path-reconcile --root <root> --rollback <ledger-id>
```

Rolls back one ledger if verification passes.

```bash
python3 pipeline.py path-reconcile --root <root> --ledger
```

Lists reconciliation ledgers in read-only mode.

```bash
python3 pipeline.py path-reconcile --root <root> --verify-ledger <ledger-id>
```

Verifies whether a ledger can be rolled back or whether manual intervention is required.

Additional future options:

```bash
--plan <path>
--audit <path>
--db-only
--queue-only
--operation-id <id>
--max-operations <n>
```

All write-capable modes require `--yes`.

## 12. Testing Strategy

Required test coverage before enabling apply behavior:

- Dry-run/apply parity.
- Rollback tests.
- Collision tests.
- Mixed-root rejection.
- Stale-row protection.
- Queue update tests.
- Failure injection.
- Ledger append-only behavior.
- Missing backup refusal.
- Missing audit refusal.
- Missing reconcile plan refusal.
- Unsupported operation refusal.
- `WEAK_MATCH` refusal.
- `REVIEW_CAREFULLY` without approval refusal.

Specific scenarios:

- `tracks.filepath` update succeeds and can roll back.
- `processed_state` active row update succeeds and can roll back.
- stale `processed_state` rows are not updated.
- target path collision rejects before mutation.
- target outside selected root rejects before mutation.
- queue JSONL path update preserves non-path fields.
- review state path snapshot update preserves review status.
- DB commit success plus queue failure creates `partial` ledger.
- rollback refuses when current state no longer matches ledger `after_values`.

## 13. Implementation Order

Recommended implementation order:

1. Ledger table.
2. Read-only ledger listing.
3. Dry-run plan validation.
4. `apply-reviewed` DB-only.
5. Rollback DB-only.
6. Queue reference updates.
7. Optional file operation support.

The first production candidate should stop at DB-only apply plus DB-only rollback. Queue updates should come after the ledger model is stable. File operations should be last.

## 14. Explicit Warnings

- Do not implement file moves until DB-only apply is proven.
- Never update BPM/key/cue data.
- Never apply `WEAK_MATCH`.
- `REVIEW_CAREFULLY` must require human approval.
- Never apply across roots.
- Never delete uncontrolled references.
- Never mutate tags during reconciliation.
- Never use audit findings alone as apply instructions.
- Always back up `<root>/logs/processed.db` before reconciliation apply work.
- Always produce a rollback ledger before considering an apply operation complete.

