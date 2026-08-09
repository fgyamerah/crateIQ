# Toolkit Command Classification (Phase 5)

**Purpose:** Record, for Phase 6 planning, the current caller/test/mutation-risk
status of every `backend/app/services/toolkit_runner.py` `ALLOWED_COMMANDS`
entry as of the Phase 5 legacy-pipeline dependency-isolation cycle. This is a
classification snapshot, not a removal plan — see `AGENTS.md` Section 4.4 and
`PROJECT_CONTEXT.md`'s Legacy Compatibility section for the general policy.

All 19 commands remain allowlisted. None were removed in Phase 5: none met
the bar of being both independently proven unused *and* safely superseded by
current FastAPI backend logic.

## Dispatch paths

Every allowlisted command is reachable through the generic
`POST /api/jobs` route (`backend/app/api/routes/jobs.py`), which validates
`body.command` against `ALLOWED_COMMANDS` and calls
`toolkit_runner.create_and_start_job`. As of this writing, the frontend does
not call this generic endpoint (`submitJob()` in
`frontend/src/api/jobs.ts` is defined but unused) — `Jobs.tsx` is a
read-only job list/log/cancel view, not a generic command launcher.

Three commands additionally have a **dedicated, typed backend route** that
calls `toolkit_runner.build_command()` directly with a fixed command name:

| Command | Dedicated route | Frontend caller |
|---|---|---|
| `rekordbox-export` | `POST /api/exports/run` | `Export.tsx` via `runExport()` |
| `set-builder` | `POST /api/playlists/set-builder` | `SetBuilder.tsx` via `runSetBuilder()` |
| `analyze-missing` | `POST /api/analysis/reanalyze` | none currently — intentionally listed in `tests/test_supported_route_contracts.py`'s deferred-mutating-endpoint set (see `NEXT_TASKS.txt` "Contract coverage for deferred mutating endpoints") |

## Classification

| Command | Classification | Caller | Mutation risk | Keep/remove |
|---|---|---|---|---|
| `rekordbox-export` | CURRENT BACKEND MAINTENANCE | `Export.tsx` (live) | Writes M3U/XML export files under the configured export root | Keep |
| `set-builder` | CURRENT BACKEND MAINTENANCE | `SetBuilder.tsx` (live) | Read-mostly; writes playlist DB rows | Keep |
| `analyze-missing` | CURRENT BACKEND MAINTENANCE (deferred UI) | `POST /api/analysis/reanalyze`, no live frontend caller yet | Writes BPM/key analysis results | Keep |
| `validate-docs` | CURRENT BACKEND MAINTENANCE (dev/CI tooling) | generic `/api/jobs`; primary use is direct CLI (`pipeline.py validate-docs`) | None — read-only doc/registry check | Keep |
| `generate-docs` | CURRENT BACKEND MAINTENANCE (dev/CI tooling) | generic `/api/jobs`; primary use is direct CLI | Writes `COMMANDS.txt`/docs artifacts | Keep |
| `db-prune-stale` | LEGACY COMPATIBILITY (explicitly documented in `AGENTS.md`/`PROJECT_CONTEXT.md` as a still-load-bearing maintenance flow) | generic `/api/jobs` only | Prunes stale `processed_state` DB rows | Keep |
| `playlists` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Writes M3U/XML playlist files | Keep |
| `dedupe` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Quarantines duplicate files (moves, not deletes) | Keep |
| `audit-quality` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Read-mostly; optional tag writes with `--write-tags` | Keep |
| `metadata-clean` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Writes tag fields | Keep |
| `metadata-sanitize` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Writes tag fields | Keep |
| `artist-merge` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Renames/merges artist folders (guarded by `--apply`) | Keep |
| `artist-folder-clean` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Renames folders (guarded by `--apply`) | Keep |
| `convert-audio` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Writes converted audio files | Keep |
| `label-intel` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Network scraping + writes report/cache files | Keep |
| `harmonic-suggest` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Read-only suggestion output | Keep |
| `tag-normalize` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Writes tag fields | Keep |
| `label-clean` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Optional tag writes with `--write-tags` | Keep |
| `cue-suggest` | LEGACY COMPATIBILITY | generic `/api/jobs` only | Writes cue-point suggestions to DB | Keep |

All 19 commands are documented in `COMMANDS.txt` and pass `validate-docs
--strict` (registry/doc sync). None showed evidence of being fully
superseded by an equivalent current FastAPI service, so none were removed —
per `AGENTS.md`/`NEXT_TASKS.txt`, retiring individual legacy-only commands is
scoped to a later legacy/intelligence cleanup phase, not Phase 5.

## Legacy-only pipeline.py commands (no toolkit_runner allowlist entry)

Verified still present in `pipeline.py`'s argparse subcommand set, with no
`toolkit_runner` allowlist entry and no current backend route:
`filename-normalize`, `library-organize`, `orphan-scan`,
`extract-track-metadata`, `title-number-recover`, `review-queue`,
`build-fewshot`. These remain CLI-only maintenance tooling; not touched in
this phase.

Correction to the historical audit list this phase started from:
`label-enrich-from-library` is not a subcommand — it is a top-level bare-mode
flag (`python pipeline.py --label-enrich-from-library`), unrelated to
`toolkit_runner`'s subcommand allowlist.
