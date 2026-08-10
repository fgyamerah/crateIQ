# Duplicate Resolution Spec

**Status:** Plan-first read-only surface implemented; no apply/execute phase exists
**Scope:** GET-only duplicate resolution planning is runtime behavior; all file mutation remains design-only and prohibited by default
**Last updated:** 2026-08-10

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
| `operation_ledger_linkage` | `"not_yet_implemented"` today, pointing back to this spec — no duplicate-resolution ledger table exists yet. A future implementation must add one, following the append-only shape of `reconciliation_ledger` (see `FULL_RECONCILIATION_APPLY_SPEC.md` Section 8) rather than inventing a new ledger model. |

## 6. Future Controlled Apply Phase (Not Implemented)

This section defines requirements for a future phase. None of it exists today; `duplicate_resolution_plan_service` has no apply/execute function, and no route accepts a POST for it.

A future apply phase must:

- Require an explicit reviewed selection of `action_id`s (mirroring the reconciliation apply contract's `reviewed_action_ids` + `confirm` gate), never a bulk "apply all ready" default.
- Re-run every actionability rule in Section 4 at execution time, not trust the plan response as authorization — a plan is a snapshot in time, and file state can drift between plan and apply.
- Take a hash-verified backup of the candidate file outside the scanned tree before any mutation, and refuse to proceed if the backup cannot be verified.
- Never permanently delete a file. The only mutation this spec permits is a *reversible* move/hold (e.g. into a to-be-designed quarantine-style holding location distinct from the existing reserved `Quarantine/` semantics in `AGENTS.md` Section 3), never an unrecoverable delete.
- Record an append-only ledger row (before/after identity, backup location, verification status) before considering the operation complete, following the `reconciliation_ledger` model.
- Support a confirmed restore path that reverses the hold using the ledger's recorded before-state, verifying current state matches the ledger's after-state before restoring (mirroring `FULL_RECONCILIATION_APPLY_SPEC.md` Section 9's rollback verification).
- Require its own dedicated implementation, tests, and review cycle before being enabled; it must not be added as an incremental extension of the plan-only surface described in this document without that separate review.

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
