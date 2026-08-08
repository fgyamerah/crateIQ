# Workspace Onboarding and Inbox — Cycles 14–15

Branch: `feat/crateiq-workspace-onboarding` (base Cycle 13 from
`MANAGED_LIBRARY_CYCLES_9_13.md`). Not merged to main.

## Cycle 14 — Workspace Onboarding + Settings UX (2026-08-08)

Fixes the confusing first-run experience where Settings exposed the legacy
direct-library wizard (Initialize index / Scan preview / Import previewed
tracks) as the primary onboarding path alongside the managed workspace,
with no way to create a brand-new workspace folder from the UI. Workspace
is now the first Settings tab; legacy setup plus active/pending root and
internal DB paths moved to a new Advanced tab behind a collapsed "Legacy
Direct Library" disclosure. New `WorkspacePanel` component covers five
states (Ready, Not Set Up, Pending Restart with current-vs-new columns,
Legacy Detected, first-run setup form).

New backend safe-creation endpoints
`POST /api/workspace/root/{classify,create}` (single-directory only,
confirm required, no recursive parent creation), built on a shared
`core/library_root.assert_safe_new_root_path()` so legacy and
managed-workspace validation can't drift. Closed a self-import containment
gap: importing a source that *contains* the managed root is now rejected
(previously only source == or inside root was caught). Inbox's
not_configured/legacy states now route to `/settings#workspace` instead of
duplicating setup UI. 51 new backend tests; 1630 backend tests pass total.

Live-verified end to end against the real running dev environment (found
mid-scenario in exactly the bug state this cycle fixes — pending root
distinct from active): restart → empty-root init → real MP3 import with
SHA-256 verification → self-import rejection → legacy detection, all
confirmed through the real UI. Responsive check at 760px/390px could not
be verified live (browser resize automation didn't change
`window.innerWidth` in that environment) — verified by CSS review instead,
disclosed rather than claimed as tested.

## Cycle 15 — Inbox Inline Editing + Bulk Edit + Sortable Columns (2026-08-08)

Closes the "Inbox has no inline metadata editor yet" gap noted in Cycle 9.
Track/file (managed Inbox filename, extension always locked), Artist, and
Genre are now directly editable inline. New safe rename contract
(`workspace_service.rename_inbox_track`) rejects separators/traversal/
control chars/empty/reserved Windows names/trailing dot-or-space,
preserves Unicode, never overwrites on collision (no silent "(2)" suffix —
that stays exclusive to auto-import), rejects symlinks, is a no-op for an
identical name, and attempts to roll back the filesystem rename if the DB
update after it fails. Artist/Genre edits (single via
`PATCH /api/workspace/inbox/tracks/{id}`, and bulk via
`POST /api/workspace/inbox/bulk-edit/{preview,apply}`, confirm required,
Artist/Genre only — never filename) reuse `tag_write_service`'s exact
backup/write/re-read/verify contract through `preparation_service.
write_tracks()` — no second writer.

No new provenance column: "manual edit survives Process All" already holds
via `enrich_tracks()`'s existing never-overwrite-non-empty-non-junk rule,
now regression-tested. Inbox columns are sortable server-side
(`track_service.VALID_SORT_KEYS` extended with genre/key/readiness;
unknown sort key now 422s instead of silently falling back; blank/NULL
values sort last in both directions; deterministic id tiebreak). Readiness
sort is an explicit field-completeness proxy, not the same computation as
the live per-track Ready badge (documented distinction — avoids O(n) disk
I/O per list request).

73 new backend tests; 1703 backend tests pass total (zero regressions);
frontend typecheck/build pass. Live-verified end to end against the real
running dev backend and the already-prepared managed workspace at the
operator's real library (its intentional near-duplicate-name pair used
only as a do-not-touch check); every test mutation was reverted afterward.

**Known gaps carried forward** (see `NEXT_TASKS.txt`): no bulk/manual
filename rename (collision handling for a *bulk* rename is materially
harder — single-track only for now); Genre Taxonomy suggestions are not
wired into the inline/bulk Genre editors (accepts any valid non-empty
string, by product decision); no frontend automated test framework exists
in this repo (typecheck + build + manual/live verification used instead).
