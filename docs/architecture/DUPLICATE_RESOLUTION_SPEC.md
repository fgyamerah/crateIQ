# Duplicate Resolution Spec

**Status:** Plan-first read-only surface implemented; no apply/execute phase exists
**Scope:** GET-only duplicate resolution planning is runtime behavior; all file mutation remains design-only and prohibited by default
**Last updated:** 2026-08-13

## 1. Purpose

Duplicate resolution planning is the future controlled mechanism for acting on reviewed duplicate candidates. It is distinct from the existing Duplicate Review workflow:

- `duplicate_review_service` / `/duplicates` — DB-only human review of rmlint's duplicate preview. Stores `keep`, `ignore`, `review_later`, and `unresolved` decisions against a saved snapshot. Never deletes, moves, renames, quarantines, or writes a tag or file. This remains the sole authoritative human review state.
- `duplicate_resolution_plan_service` / `/duplicates/resolution-plan` (this spec) — a read-only, plan-first layer that derives, from the latest saved review snapshot plus its existing decisions, which tracks are unambiguous enough to be a *future* reversible-resolution candidate, and what a future apply phase would need to prove before it could touch a file. It generates no file, tag, or track-metadata write, and it persists nothing beyond what `duplicate_review_service` already stores; the plan is recomputed on every request.
- A future, separately implemented and separately reviewed **duplicate resolution apply** phase — not implemented. This spec defines the contract it must satisfy. It does not exist yet, and nothing in the current codebase can execute it.

The advisory keeper `recommendation` field already present in `duplicate_review_service` (filename-copy-marker heuristic) is never treated as authorization anywhere in this pipeline. Only an explicit human `keep` decision can make a track a keeper.

## 2. Non-Goals

Duplicate resolution planning must not become a blind cleanup tool.

Non-goals:

- No blind automatic apply.
- No permanent delete, ever, as a plan action or a default apply behavior.
- No audio tag writes.
- No BPM, key, beatgrid, or cue mutation.
- No cross-root operations.
- No action from an advisory keeper recommendation alone.
- No action from filename-similarity evidence alone (`match_basis` other than `content_checksum`).
- No plan-artifact file write to the managed workspace (the plan is reproduced from `duplicate_review_service` state on each request; see Section 4).
- No new Needs Review queue entries generated merely because a plan exists.

## 3. Plan Action Vocabulary

Every plan item carries exactly one of four conservative actions. `delete` is never a valid action anywhere in this contract.

| Action | Meaning |
|---|---|
| `keep` | Explicit human-selected canonical keeper. No mutation now or ever for this action. |
| `candidate_for_reversible_resolution` | Eligible for a *future*, separately implemented reversible action (e.g. a controlled quarantine hold). Not executed now — no apply endpoint exists. |
| `no_action` | The plan declines to propose anything for this track, even if a human decision exists, because current grouping evidence is not content-verified (e.g. `match_basis` is filename-based rather than checksum-based). More review cannot fix insufficient identity evidence; a future preview producing verified content identity would be required first. |
| `review_required` | The group is blocked: an explicit human decision is missing, ambiguous, or current on-disk state cannot be verified. Links back to Duplicate Review — no new queue is created. |

## 4. Actionability Rules

A group becomes plan-eligible (`status: "ready"`) only when every rule below holds. Any failure blocks the whole group (`status: "blocked"`) rather than guessing partial safety.

1. **Snapshot identity is stable/current.** The plan is derived only from the single latest saved snapshot returned by `duplicate_review_service.get_review()`. Decisions are scoped to `(snapshot_id, group_id, track_id)`; a decision recorded against an older snapshot never silently applies to a newer one; after any new preview refresh, prior decisions on the same group read back as `unresolved` until re-recorded, which correctly blocks the group.
2. **Group membership matches the saved snapshot.** The plan only ever iterates the items present in the current snapshot's `groups_json`; it never merges in items from another snapshot or another source.
3. **Content identity is verified.** Only `match_basis == "content_checksum"` groups are eligible; anything else yields `no_action` for every member of the group, with blocker `evidence_insufficient_for_resolution`.
4. **Exactly one explicit keeper.** Zero `keep` decisions yields blocker `no_keeper_selected`; more than one yields `multiple_keepers_selected`. Both block the entire group.
5. **All other members are reviewed.** Any `unresolved` or `review_later` decision in the group yields blocker `unreviewed_members_present` and blocks the group.
6. **Every candidate path is under the selected managed root.** Each member's stored `relative_path` is re-resolved against the currently selected root with `assert_path_under_root` at plan-generation time (not trusted from the snapshot alone); any traversal or absolute escape yields the member-level blocker `path_outside_managed_root`, which is rolled up into the group-level blocker `member_path_or_file_state_invalid`.
7. **No stale/missing file state is silently ignored.** Each member's resolved path is stat-checked against the *current* filesystem at plan-generation time; a missing file yields member-level blocker `missing_file_current_state`, rolled up the same way.
8. **Advisory recommendation alone never authorizes action.** The `recommendation` field is read only for display purposes elsewhere; it has no effect on plan action selection.

## 5. Execution Requirements (Backup/Restore Design Contract)

Every `candidate_for_reversible_resolution` item carries an `execution_requirements` object describing exactly what a future apply phase would need to prove before it could touch that file. This is a design contract, not an implementation — none of these steps run today.

| Field | Meaning |
|---|---|
| `source_relative_path` | The candidate's path, relative to the selected root, as re-verified at plan time. |
| `track_id` | Canonical track identity. |
| `identity_evidence` | `{"type": "checksum_prefix", "value": <12-char rmlint prefix>, "note": "...not a verified full content hash."}` when the snapshot retained a prefix, or `{"type": "none", ...}` otherwise. **Never fabricated as a full hash** — the current rmlint preview (`analysis_jobs_service._preview_duplicate_detection`) only retains a 12-character checksum prefix (`checksum_prefix`) per group, not the full digest, so evidence is labeled truthfully rather than overstated. |
| `observed_at_plan_time` | `{"size_bytes", "mtime_ns"}` captured from a live `stat()` call at plan-generation time — the *current* state, not the snapshot's recorded `size_bytes`, so drift since the snapshot was saved is visible. |
| `proposed_destination_strategy` | Always `"reversible_hold_pending_future_implementation"` today — a placeholder naming the design intent (a reversible hold, not a delete), not an implemented mechanism. |
| `collision_check_required` | Always `true`. A future apply phase must verify the proposed destination does not already exist before any move. |
| `backup_required` | Always `true`. A future apply phase must take a hash-verified backup outside the scanned tree before any mutation, mirroring the existing `tag_write_service` backup-before-write pattern. |
| `restore_preconditions` | A fixed list of preconditions a future restore path must satisfy: backup exists and is hash-verified before mutation; the original remains fully recoverable until the operator confirms the outcome; an operation ledger row records before/after identity and status before the operation is considered complete. |
| `operation_ledger_linkage` | `"not_yet_implemented"` today, pointing back to this spec — no duplicate-resolution ledger table exists yet. A future implementation must add a purpose-specific append-only ledger using the evidence and recovery properties of `reconciliation_ledger` (see `FULL_RECONCILIATION_APPLY_SPEC.md` Section 8); it must not reuse or reinterpret reconciliation ledger rows. |

## 6. Future Controlled Apply Phase (Not Implemented)

This section defines requirements for a future phase. None of it exists today; `duplicate_resolution_plan_service` has no apply/execute function, and no route accepts a POST for it.

### 6.1 Authority and scope boundary

Duplicate resolution is a media-file hold workflow. It is **not** a
reference-artifact reconciliation workflow and must not call, extend, or
write `reconciliation_ledger`, reference-artifact plans, queue JSON/JSONL,
or their apply endpoints. Its only initial file action is to move one reviewed
duplicate candidate to a reversible operational hold. It must not rename the
keeper, reconcile unrelated path references, edit review decisions, or
perform an inferred cleanup.

The initial executor is deliberately narrower than the planner:

- It accepts exactly one ready `candidate_for_reversible_resolution` action
  from one explicitly identified group; no `apply all`, group batch, or
  background auto-execution mode exists.
- It accepts files whose live source is in managed `Inbox/` only. A candidate
  in `Library/` is plan-visible but execution-blocked with
  `library_candidate_requires_dedicated_impact_design`; Library can have
  crates, exports, and external-DJ implications that this executor must not
  silently change. `Quarantine/`, the hold area, and every other zone are
  never valid sources.
- At preview, apply, restore, and recovery, the selected root must be
  classified as exactly `managed_workspace`, with its valid managed-workspace
  marker, by a new **pure duplicate-resolution workspace validator**. That
  validator must perform only `lstat`/marker/root/zone reads and must not call
  `workspace_service.workspace_state` or any migration-capable classifier:
  `workspace_state` currently invokes `ensure_storage_zone_column`, which can
  `ALTER TABLE tracks` and create an index, so it is not side-effect free. The
  validator reimplements the minimal marker/root/zone classification itself,
  side-effect free; this workflow must not make the migration side effect a
  prerequisite. In particular, validation must not run schema setup, alter
  `tracks`, create indexes, or otherwise migrate operational state. The
  presence of an `Inbox/` directory alone is never sufficient: a
  `legacy_direct_library` (including one that happens to contain `Inbox/`),
  `not_configured`, missing, malformed, or changed workspace state fails
  closed with no filesystem mutation.
- Existing `Quarantine/` remains reserved and is never an automatic
  destination. The hold destination is a new, implementation-owned,
  root-contained operational area such as
  `<root>/logs/duplicate_resolution/holds/`; it is outside the managed
  Inbox/Library scan roots and is not a promoted Library location.
- The executor must add an explicit held-operation state/read model before
  enabling file moves, so normal Inbox/Library views do not report a held
  candidate as an unexplained missing path. It must not paper over that state
  by applying reference-artifact reconciliation. The exact UI/read-model work
  belongs in the implementation stage below.

No tag, BPM, key, beatgrid, waveform, cue, playlist, crate, `tracks`,
`processed_state`, or queue mutation is authorized by this design. Any future
need to update a path reference or support Library candidates is a separate
reviewed design and must preserve the then-current authoritative owner.

### 6.2 Confirmed immutable apply preview

The current GET plan is recomputed and contains `generated_at`; it is useful
for review but is not itself an apply token. A future executor must first
create a persisted **duplicate-resolution apply preview** with a schema
version, selected-root canonical path, review `snapshot_id`, exactly one
`action_id`, chosen keeper/candidate IDs, and all execution evidence below.
It is stored only in the selected root's bounded operational log area, never
in a managed media scan root. The server serializes canonical JSON and returns
its SHA-256.

The apply request must include the preview ID, its exact SHA-256, the same one
`action_id`, and `confirm: true`. A missing/false confirmation, more or fewer
than one action, an unsupported schema, a root mismatch, a missing preview,
or a digest mismatch is a hard failure with no writes. The UI must present the
candidate source, keeper, proposed hold, backup location, and an explicit
statement that the operation never permanently deletes media immediately
before the confirmation control; the control cannot be preselected.

Preview data must contain, at minimum:

| Evidence | Required value |
|---|---|
| Review binding | current `snapshot_id`, `group_id`, every member ID/decision, exactly one explicit keeper, and the chosen candidate |
| Content proof | a newly computed full SHA-256 of both keeper and candidate, plus size; the hashes must be equal. A retained rmlint prefix is only corroboration, never execution proof. |
| Source identity | root-relative source path, canonical resolved path, `st_dev`, `st_ino` where available, size, `mtime_ns`, and SHA-256 |
| Destination identity | root-relative hold and backup paths derived server-side from an opaque operation UUID; neither is user supplied |
| Zone proof | current workspace state is `managed_workspace`; source zone is `Inbox`; canonical source is not a symlink and every existing ancestor from root to source is non-symlink |
| Collision proof | hold path and backup path are absent; no existing active or prepared ledger record claims either destination |

Preview preflight is read-only: it uses the pure workspace validator and may
inspect existing ledger state only to report an unfinished-operation blocker.
It must not run recovery, acquire a writer lock that mutates state, append a
ledger event, create operational directories, or repair/migrate any database
state. Ledger-changing recovery is reserved for a confirmed apply or restore,
or an explicit, separately confirmed recovery action; preview never triggers
or performs it. The preview may read/hash files but changes no state other
than its explicit operational preview artifact. It expires on any revalidation
failure; elapsed time alone is not sufficient authorization to bypass a failed
check.

### 6.3 Execution-time gates and transaction protocol

Apply must re-run all Section 4 rules and recreate the full evidence in
Section 6.2 while holding a per-root duplicate-resolution writer lock. It
must reject a new review snapshot, changed decisions, changed group
membership, stale/missing/non-regular files, changed stat/inode/hash,
checksum mismatch with the keeper, a source outside Inbox, symlinks, hard-link
ambiguity not explicitly supported by the implementation, any destination
collision, or a conflict with an unfinished operation. A plan or preview is
never authorization after drift.

The required order is:

1. Acquire the per-root lock; reclassify the root as exactly
   `managed_workspace` using the pure duplicate-resolution workspace
   validator; resolve root and every path with existing
   root-containment helpers, reject symlinks with `lstat`, and revalidate the
   immutable preview and review state.
2. Create an append-only ledger record in `prepared` state before moving a
   file. It includes exact before-state, planned hold/backup paths, preview
   ID/digest, and a recovery nonce. A new operation must not overwrite this
   record.
3. Before creating each server-derived hold or backup parent directory,
   validate every existing ancestor with `lstat` and root containment. Create
   only the missing directories, then repeat `lstat` ancestor validation and
   canonical containment after creation; any symlink, non-directory, or path
   escape fails closed. Copy the candidate into the verified backup location
   outside scan roots using exclusive creation; fsync the file and parent as
   supported; compute SHA-256 of the backup; and require equality with the
   freshly computed candidate hash. On failure, retain evidence and mark the
   ledger failed/recoverable; never move the source.
4. Repeat the verified destination-parent validation for the hold path, then
   recheck source identity and destination absence immediately before the
   move. Move only by same-filesystem atomic rename from Inbox to the unique
   hold path. Cross-device moves are unsupported and must fail closed rather
   than copy-and-delete. Fsync affected directories as supported.
5. Re-hash the held file; require it to match the pre-move source and backup.
   Append a terminal `held_verified` ledger event only after that proof and
   make the held state visible to its dedicated read model.

Every failure path records a terminal append-only failure/recovery event where
the ledger was prepared. It must never delete a source or backup to hide a
failed operation. The operation is idempotent only for the same preview/action:
a retry first performs recovery and then returns the already verified terminal
result or a safe blocker; it never creates a second hold.

### 6.4 Ledger, evidence retention, and recovery

Use a dedicated append-only `duplicate_resolution_ledger`, not the
reconciliation ledger. Each event has an immutable operation UUID, parent
event/operation linkage, UTC timestamp, event type, schema version, selected
root identity, preview ID/SHA-256, snapshot/group/action/track IDs, keeper ID,
source/hold/backup relative paths, complete before/after stat and SHA-256
evidence, verification status, and a machine-readable failure/recovery reason.
The payload must contain no tag values or unrelated private metadata.

Allowed lifecycle events are `prepared`, `backup_verified`, `held_verified`,
`restore_prepared`, `restored_verified`, `failed`, and `recovery_required`.
Events are inserts only; corrections append a child event. An operation is
recoverable until a verified restore is recorded. The backup is retained until
an explicitly designed, separately authorized retention policy exists; this
design authorizes no cleanup job.

At service startup, only a read-only inspection may identify unfinished
operations for display. Before a confirmed apply or restore, or through an
explicit confirmed recovery action, classify the selected root as exactly
`managed_workspace` using the pure duplicate-resolution workspace validator,
then inspect unfinished ledger operations under the same writer lock. Preview
performs only the read-only preflight described in Section 6.2; it never
invokes ledger-changing recovery. Recovery must make the same
managed-workspace and `lstat`/containment checks for every source, hold, and
backup path before inspecting or changing state; a state/marker mismatch or
symlink/path escape blocks recovery with a visible safe error:

- source present + hold absent: retain the verified backup and append
  `failed`/`recovery_required`; do not retry the move implicitly;
- source absent + hold matches recorded hash: append `held_verified` if the
  crash occurred after the move, then expose it for explicit restore;
- source and hold both present, or either identity does not match: append
  `recovery_required` and block further operations for that source/group;
- source absent + hold absent: append `recovery_required` and require manual
  operator investigation using the retained evidence.

Recovery must be deterministic, bounded to the selected root, and visible in
read-only operation list/detail endpoints. It must never infer success from a
missing source alone.

### 6.5 Confirmed restore / rollback

Restore accepts exactly one `held_verified` operation ID and `confirm: true`.
Under the same per-root lock it reclassifies the root as exactly
`managed_workspace`, verifies the ledger chain, preview/root binding, backup
SHA-256, held-file SHA-256, original Inbox destination absence, and that no
later operation depends on the hold. It creates a
`restore_prepared` event, atomically renames the held file back to its exact
original root-relative Inbox path, re-hashes it, compares it with both backup
and recorded source hash, fsyncs as supported, and appends `restored_verified`.
It then updates only the dedicated held-operation read model. A changed,
occupied, symlinked, cross-root, or hash-mismatched destination blocks restore
without overwriting anything. Restore itself never deletes the backup.

### 6.6 Required tests

Use only disposable temporary managed roots and synthetic files. At minimum,
tests must prove:

- API confirmation, exact-one-action, preview ID/SHA, schema-version, and
  selected-root binding failures cause no mutation;
- explicit keeper/all-member/checksum grouping gates, stale snapshot/decision,
  source drift, full-hash mismatch, missing files, Library/Quarantine source,
  legacy-direct roots that contain an `Inbox/`, missing/changed managed
  workspace markers, path traversal, absolute escape, symlinked
  root/ancestor/source, and hard-link policy all fail closed at preview,
  apply, restore, and recovery;
- exclusive destination creation and pre-move recheck reject hold/backup and
  ledger collisions without overwrite; symlinked or escaped existing hold and
  backup ancestors, including a root-contained symlink created before or
  during destination-directory creation, fail closed after each creation and
  cannot redirect an operational destination into Inbox, Library, or outside
  the selected root;
- backup is outside Inbox/Library scan roots, hash-verified before move, and
  every source/backup/hold hash postcondition is enforced;
- successful Inbox hold changes only the candidate's location plus dedicated
  duplicate-resolution operational records, with no permanent delete and no
  tags, BPM, key, cue, playlist, crate, queue, or reconciliation-ledger
  mutation;
- restore succeeds only against exact held state and original destination,
  rejects drift/collision/dependent operation, and produces a verified
  append-only child event;
- injected failures before backup, after backup, after move, and before final
  ledger update recover deterministically after service restart; and
- concurrent apply/restore attempts serialize per root and cannot claim the
  same source or destination twice.

### 6.7 Smallest staged implementation sequence

1. Add read-only schemas and service helpers for canonical preview bytes, a
   pure managed-workspace-marker/root/zone/symlink validator that does not
   call migration-capable workspace classification, full streaming SHA-256,
   and the held-state read model. Add fixture-only tests proving preview
   preflight makes no database or ledger mutation; expose no apply route.
2. Add the additive dedicated preview/ledger storage and read-only preview,
   list, detail, and recovery-inspection endpoints. Prove stale-plan and
   crash-state detection; no filesystem move.
3. Add confirmed one-action Inbox-only apply with backup, atomic hold,
   per-root lock, postcondition verification, and failure injection tests.
   Keep Library, Quarantine, batches, and all reference artifacts blocked.
4. Add confirmed restore with ledger-chain validation and recovery handling.
   Run the full fixture matrix and conduct a manual disposable-root rehearsal.
5. Only after real local-library use has exercised the read model should a
   separate proposal consider Library candidates, retention policy, or any
   reference impact. It is not part of this design or implementation sequence.

Each stage requires its own focused code review and no stage may loosen the
current read-only `/api/duplicates/resolution-plan` contract before its tests
pass.

## 7. Needs Review Integration

Duplicate resolution planning does not create Needs Review entries. A `review_required` plan item links back to the existing `/duplicates` Duplicate Review page so the operator resolves it through the single authoritative review queue rather than a second, parallel one.

## 8. Explicit Warnings

- Do not implement any file mutation until this spec's future apply phase is separately designed, reviewed, and tested.
- Never emit `delete` as a plan action.
- Never treat the advisory keeper recommendation as authorization.
- Never treat filename-similarity grouping as sufficient evidence for any action.
- Never claim a stronger identity guarantee (e.g. a full hash) than the evidence the preview actually retained.
- Always re-verify root containment and on-disk file existence at plan-generation time, not just at snapshot-save time.
- Always keep the plan surface read-only: no plan-artifact file write, no apply endpoint, no track-metadata write.
