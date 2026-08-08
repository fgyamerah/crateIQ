# CLAUDE.md

This file contains Claude Code-specific guidance for working in this repository.

**Read and follow `AGENTS.md` before modifying the repository.** AGENTS.md is
the authoritative source for current crateIQ architecture, safety rules,
development conventions, and repository behavior. If architecture
descriptions conflict, current source + AGENTS.md take precedence over
historical or archived documents unless the user explicitly says otherwise.

---

## Controlled Scope

* Work only on the requested task.
* Targeted inspection necessary to perform the task safely is allowed.
* Do not broadly explore unrelated parts of the repo.
* Ask before materially expanding product scope.
* Keep changes focused.

## Token / Context Discipline

* Prefer targeted grep/read over broad file dumps.
* Do not repeatedly reread unchanged files.
* Avoid broad subagents; use one only if it clearly reduces total context cost.
* Keep tool/test output concise.
* Compact context during long same-task sessions when appropriate.
* Use a new/clear session for unrelated tasks.

## Working Modes

**Audit / Explore** — read-only investigation when requested.

**Modify** — targeted inspection plus implementation for an approved task.

**Restricted** — when the user explicitly limits files/scope, respect that
restriction and do not search the broader repository.

## Safety

Full safety policy lives in AGENTS.md — do not duplicate it here. High-value
reminders worth repeating:

* Preserve user music/data. Never take destructive filesystem or data actions
  without explicit authorization.
* Do not expose secrets, API keys, tokens, or credentialed paths.
* Externally imported originals must remain untouched; crateIQ only writes to
  managed Inbox copies.
* Beets Python API is allowed. The `beet` CLI binary is forbidden.
* Follow commit/push authorization as defined in AGENTS.md or given
  explicitly by the user for the current task.

## Git

Keep this short — see AGENTS.md for full git rules. If the current user
prompt explicitly authorizes commit/push for the task, that authorization is
sufficient within the stated scope. Do not merge to `main` unless explicitly
instructed.
