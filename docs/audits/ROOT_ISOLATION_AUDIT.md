# Root Isolation Audit

## Active Rule

CrateIQ must operate against one selected library root per run.

- The selected root must be absolute and must exist.
- Run logs for root-scoped commands must live under `<root>/logs/`.
- The active DB for root-scoped commands is `<root>/logs/processed.db`.
- Paths read from the DB that resolve outside the selected root are mixed-root findings, not current valid paths.

## Root-Safe Now

- `path-audit --root <library_root>`
  - Resolves the selected root explicitly.
  - Reads only `<root>/logs/processed.db`.
  - Writes reports only under `<root>/logs/path_audit/`.
  - Reports DB rows outside root as `mixed_root_db_paths`.

- `path-reconcile --root <library_root> --dry-run`
  - Uses the same root and DB resolution as `path-audit`.
  - Writes plan logs only under `<root>/logs/path_reconcile/`.
  - Does not plan path updates for DB rows outside the selected root.
  - `--apply` remains unimplemented.

## Commands Still Using Global Paths

These commands still depend on global `config.*` paths unless their local `--path` or `--input` handling overrides enough state:

- main pipeline run: mutates `config.MUSIC_ROOT` only when top-level `--path` is used
- `label-clean`
- `dedupe`
- `orphan-scan`
- `playlists`
- `cue-suggest`
- `metadata-clean`
- `tag-normalize`
- `db-prune-stale`
- `analyze-missing`
- `audit-quality`
- `metadata-sanitize`
- `artist-repair`
- `artist-intelligence`
- `ai-normalize`
- `metadata-enrich-online`
- `review-queue`
- `rekordbox-export`
- `set-builder`
- `harmonic-suggest`

## Hardcoded Path Risks Found

- `config.py`
  - default `MUSIC_ROOT = /music`
  - `SSD_KKDJ_ROOT = /mnt/music_ssd/KKDJ`
  - `SET_BUILDER_OUTPUT_DIR = /mnt/music_ssd/KKDJ/_SETS`
  - Rekordbox defaults under `/mnt/music_ssd`

- `config_local.py`
  - `MUSIC_ROOT = /music`

- `pipeline.py`
  - several help examples use `/music` or `/mnt/music_ssd`
  - metadata-enrich-online help mentions `/home/koolkatdj/Music/music/IGNORED/`
  - several commands call `_log_active_path(..., config.SORTED)` or use `config.LOGS_DIR`

- modules
  - `modules/rekordbox_export.py` has `/mnt/music_ssd` defaults
  - `modules/reporter.py` includes `/music/...` user-facing paths
  - `modules/playlists.py` maps paths relative to `config.MUSIC_ROOT`
  - `modules/dedupe.py` scans `config.INBOX` and `config.SORTED`
  - `modules/analyze_missing.py`, `modules/convert_audio.py`, and others write under `config.LOGS_DIR`

## DB And Log Risks

- `db.get_conn()` always uses `config.DB_PATH` and creates parent directories.
- Commands that call `db.init_db()` before resolving a selected root can create or use the wrong DB.
- Commands using `config.PIPELINE_LOGS_DIR` write to repo-local logs, not library-root logs.
- Commands with both `--path` and global `config.LOGS_DIR` may scan one root but log against another.

## Migration Plan

1. Extend `resolve_library_root(args)` and `assert_path_under_root(path, root)` to more commands.
2. Add a scoped config context that derives `LOGS_DIR`, `DB_PATH`, `SORTED`, queues, and report dirs from the selected root.
3. Make DB-opening helpers accept an explicit DB path or root for root-scoped commands.
4. Add mixed-root DB row reporting before any command writes DB updates.
5. Convert commands one at a time, starting with read-only/reporting commands before mutating commands.
6. Add root-isolation tests for each migrated command.
