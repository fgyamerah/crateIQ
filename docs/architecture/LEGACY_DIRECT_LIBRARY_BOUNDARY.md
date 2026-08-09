# Legacy Direct Library Deprecation Boundary (Phase 7)

**Purpose:** Record the explicit boundary between the current,
workspace-selected library model and the surviving "Legacy Direct Library"
compatibility mode, so a future developer can answer "is this code using the
current workspace model or the legacy direct-library model?" without tracing
through unrelated modules. This is a deprecation-clarity phase, not a
removal phase — Legacy Direct Library remains fully functional. See
`AGENTS.md` Section 4.4/17 and `PROJECT_CONTEXT.md`'s Legacy Compatibility
section for the general policy this document narrows.

---

## CURRENT MODEL — workspace-selected library

Single source of truth: `backend/app/core/library_root.selected_library_root()`.

* Reads `CRATEIQ_LIBRARY_ROOT` (preferred) or `CRATEMINDAI_LIBRARY_ROOT`
  (deprecated alias, still honored so existing setups do not silently
  change library). **No further fallback.** If neither is set, it raises
  `RuntimeError("No safe library root is configured.")` — it never reads
  the legacy pipeline's `config.py` `MUSIC_ROOT` (which itself defaults to
  `/music`) and never defaults to any hardcoded path. This was established
  in Phase 5 (commit `12e2fe3`) and is unchanged in Phase 7.
* `library_db_path()`, `library_audit_dir()`, `enrichment_queue_path()`,
  `enrichment_review_state_path()` all derive from this same root.
* `backend/app/core/pipeline_db.py` (read-only `processed.db` access) and
  every backend route/service listed under "active callers" below resolve
  their operational root exclusively through `selected_library_root()`.
* Frontend: `frontend/src/api/workspace.ts` exposes a tri-state
  classification (`managed_workspace` / `legacy_direct_library` /
  `not_configured`); `WorkspacePanel.tsx` and `Settings.tsx` already label
  the legacy path explicitly (see below) and default every new-user flow to
  the managed workspace. No frontend change was needed in Phase 7 — the
  frontend already correctly distinguishes the two models.
* Backend startup (`backend/app/main.py`) calls `selected_library_root()`
  once at startup for storage-zone-column migration; it fails the same way
  as any other current-backend caller if unconfigured.

**Active callers of `selected_library_root()`:** all of `backend/app/api/routes/`
(`workspace`, `genres`, `reconciliation`, `library`, `health`, `reviews`,
`metadata_sanitation`, `metadata_repair`, `settings`, `waveforms`, `insights`)
and the corresponding services layer (`workspace_service`, `settings_service`,
`library_setup_service`, `tag_write_service`, `provider_routing_service`,
`track_service`, and the rest listed by `grep -rl selected_library_root
backend/`). This list is intentionally not exhaustively duplicated here — the
import graph is the source of truth; `test_current_backend_never_imports_
legacy_pipeline_modules` (below) guards the boundary itself, not this list.

---

## LEGACY MODEL — explicit Direct Library compatibility

Legacy Direct Library is pipeline.py's own root model
(`config.py` `MUSIC_ROOT`, env `DJ_MUSIC_ROOT`, default `/music`), and the
narrow set of current-backend entry points that intentionally still accept
an *explicit*, caller-supplied library root instead of the ambient
workspace selection.

| Entry point | Classification | Why it remains | Deprecation treatment |
|---|---|---|---|
| `pipeline.py` CLI (`--root` on root-scoped subcommands; `config.MUSIC_ROOT`/`DJ_MUSIC_ROOT` default on subcommands with no `--root`) | L2 — required maintenance CLI | Still the only implementation for 19 `toolkit_runner`-allowlisted commands plus several CLI-only maintenance subcommands (`filename-normalize`, `library-organize`, `orphan-scan`, `extract-track-metadata`, `title-number-recover`, `review-queue`, `build-fewshot`) — see `docs/architecture/TOOLKIT_COMMAND_CLASSIFICATION.md`. | Documented here and in `config.py`'s `MUSIC_ROOT` comment; not renamed/removed (would break CLI compatibility). |
| `backend/app/services/toolkit_runner.py` subprocess launch of allowlisted `pipeline.py` commands | L1 — required legacy compatibility | The FastAPI backend's only way to run legacy maintenance commands (rekordbox-export, set-builder, dedupe, etc.). Never passes `--root`; the subprocess inherits `DJ_MUSIC_ROOT` from the backend process environment. | Comment added in `build_command()` documenting the env-inheritance contract; `test_toolkit_runner_never_passes_root_flag_to_legacy_commands` and `test_launch_script_bridges_dj_music_root_to_selected_library_root` lock the boundary in code. |
| `scripts/crateiq-local-services.sh` (`_crateiq_start_profile`) | L1 — required bridge | The only supported way to start the real backend; it exports `DJ_MUSIC_ROOT="$CRATEIQ_LIBRARY_ROOT"` alongside `CRATEIQ_LIBRARY_ROOT` so legacy subprocess jobs stay pointed at the selected library instead of `pipeline.py`'s own `/music` default. | An unbridged duplicate start path (`_crateiq_start`, zero callers) was removed in Phase 7 — see "Boundary implemented" below. |
| `library_setup_service._target_root(library_root: str | None)` and its callers (`initialize_library`, `scan_preview`, `import_previewed_library`) | L1 — intentional explicit-path compatibility | Backs the frontend's "Legacy Direct Library" setup wizard (Settings → Advanced), which scans/indexes an existing music folder in place rather than the managed Inbox → Library workflow. Omitting the argument always means "use the canonical selected root for this process" (see the function's own docstring) — it is never an implicit fallback. | Already tested (`tests/test_library_setup_service.py::test_omitted_root_never_uses_pending_root_over_selected_root`, `::test_target_root_fails_closed_when_no_root_is_configured`). No change needed. |
| `POST /settings/library/initialize`, `POST /library/scan-preview`, `POST /library/import` (`backend/app/api/routes/settings.py`) | L1 — intentional explicit-path compatibility | Typed pass-through of the above; `LibrarySetupRequest.library_root` is `Optional[str]`. The current frontend (`frontend/src/api/settings.ts`) never actually sends `library_root` — it always relies on the ambient selected root — so this parameter today only matters for direct API/test/future-CLI callers. | Documented here; not renamed (would be an unrequested API-contract change). |
| `_filepath_prefixes()` (`backend/app/api/routes/library.py`) / `_path_prefix_clauses()` (`backend/app/services/track_service.py`) | L3 — display/query-matching support, not root selection | Pure SQL `LIKE` prefix-matching helpers: the pipeline DB may contain historical rows stored under the `/music` symlink instead of the canonical resolved path. Both call `selected_library_root()` for the canonical form and only add `/music` as an *alternate string to match against*, never as a path to read/write/select. | Comment updated; `test_no_unexplained_music_literal_in_current_backend` locks these as justified `"/music"` query/display-matching literals in `backend/app/`. |
| `_output_paths()` (`backend/app/services/export_validation.py`) | L3 — informational-display fallback, not root selection | Returns the export response's `output_paths` context dict (`m3u`/`xml`/`log` — see `ExportPreviewResponse.output_paths` in `backend/app/schemas/export.py`) for UI display only. The `"log"` fallback value (`"/music/logs" + "/rekordbox_export/invalid_tracks.txt"`, or the hard-coded `"/music/logs/rekordbox_export/invalid_tracks.txt"` if `config.py` can't be loaded) is used only when `LOGS_DIR` isn't defined in the legacy `config.py`; it is never read from, written to, or used to select a root. | Retained as-is (smallest, safest fix — changing it would only be cosmetic). Added to `_JUSTIFIED_MUSIC_LITERAL_FILES` alongside the two helpers above; `test_no_unexplained_music_literal_in_current_backend` now uses an AST-based `/music` **and** `/music/`-prefixed literal scan (previously matched only the exact `"/music"` string and missed this literal). |
| `pipeline.py`'s own `MUSIC_ROOT`/`DJ_MUSIC_ROOT` default (`config.py`) | L2 — required legacy CLI default | Direct CLI invocations of `pipeline.py` outside the launch script (e.g. a maintainer running a subcommand by hand) still need *some* default; `/music` is the historical convention. | Comment added explaining the default is legacy-CLI-only and is bridged away for backend-launched jobs. Not changed — changing it would alter documented legacy CLI behavior. |

**Not legacy / not in scope:** `CRATEMINDAI_LIBRARY_ROOT` is a deprecated
*alias* within the current model (still resolved by
`selected_library_root()` itself), not a separate legacy mechanism — it is
listed here only for completeness.

---

## Boundary implemented in Phase 7

1. **Removed one genuinely dead, unbridged start path.**
   `scripts/crateiq-local-services.sh` had two functions that could launch
   the real backend: `_crateiq_start_profile` (bridges `DJ_MUSIC_ROOT`,
   reachable from every `start*` CLI subcommand) and `_crateiq_start` (no
   env bridging, zero callers anywhere in the script). The second was dead
   code that also happened to be exactly the kind of "ambiguous middle
   path" this phase rules out — if it were ever wired up, backend-launched
   legacy jobs would have silently fallen back to `/music`. Removed as L4
   (proven dead via repo-wide grep for `_crateiq_start\b`, trivial, directly
   related to the boundary).
2. **Documented the `DJ_MUSIC_ROOT` bridge in code**, not only in this file:
   `config.py`'s `MUSIC_ROOT` definition and
   `toolkit_runner.build_command()`'s docstring now both explain the
   env-inheritance contract, so a future edit to either side (adding
   `--root` support, or dropping the bridge from the launch script) is a
   deliberate, visible decision rather than an accidental regression.
3. **Corrected stale/ambiguous naming** in `backend/app/api/routes/library.py`
   (module docstring, a skip-dir comment, and `_build_tree`'s docstring all
   said "MUSIC_ROOT" while the code actually calls
   `selected_library_root()`) — comment-only, no behavior change.
4. **Added architectural regression tests**
   (`tests/test_legacy_direct_library_boundary.py`) that prove the boundary
   in code:
   - current backend never imports `pipeline`/`config`/`db` (AST-based);
   - no unexplained `/music`-rooted string literal (exact `"/music"` or any
     `"/music/..."`-prefixed literal, via AST) exists in `backend/app/`
     outside the justified query/display-matching helpers and the
     informational-display fallback in `export_validation.py`;
   - the launch script's one real backend-start command bridges
     `DJ_MUSIC_ROOT` to `CRATEIQ_LIBRARY_ROOT`;
   - `toolkit_runner` never grows a `--root` flag without that bridge being
     revisited.

   A later post-audit sweep found the literal guard's original substring
   check (`'"/music"' in file text`) matched only the exact `"/music"`
   string and missed `/music/`-prefixed literals such as
   `export_validation.py`'s `"/music/logs"` display fallback. The guard now
   parses each file's AST and flags any string constant equal to `/music`
   or starting with `/music/`, so the check follows real source literals
   rather than a fixed-string grep. The invariant this whole boundary
   protects is unchanged and remains true after that fix: current Managed
   Workspace root selection never silently falls back to `/music` — every
   surviving `/music`-rooted literal in `backend/app/` is either a
   query/display prefix-matching helper or a purely informational display
   fallback, never a root used for filesystem reads/writes/selection.

No other production code changed. Legacy Direct Library's actual runtime
behavior (CLI defaults, the Settings → Advanced setup wizard, allowlisted
job execution) is unchanged.

---

## Removal preconditions (for a future phase, not this one)

Legacy Direct Library is not close to removable yet. Before any future
phase could remove it:

* All 19 `toolkit_runner`-allowlisted commands and the 7 CLI-only
  maintenance subcommands listed in
  `docs/architecture/TOOLKIT_COMMAND_CLASSIFICATION.md` would need current
  FastAPI-backend equivalents, or an explicit decision to drop that
  functionality.
* The Settings → Advanced "Legacy Direct Library" setup wizard would need
  either a real user migration path off it, or confirmation that zero real
  installs still use it.
* `pipeline.py`/`config.py`/`db.py` and the `modules/`, `ai/`,
  `intelligence/` trees that still back them would need their own
  dependency audit (out of scope here; Phase 5/6 already narrowed and
  audited parts of this).
* The `DJ_MUSIC_ROOT` env bridge in `scripts/crateiq-local-services.sh`
  would need to be provably unused before deletion.

No removal date or migration timeline is defined; none is claimed here.
