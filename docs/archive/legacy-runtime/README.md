# docs/archive/legacy-runtime/

Historical "DJ Toolkit" / pre-managed-workspace CrateIQ runtime artifacts,
archived as plain-text snapshots during the Legacy Architecture Cleanup
(Phase 3, 2026-08-09). They are **not part of current crateIQ operation**.

## DO NOT RUN

Every script and unit file in this directory assumes an old, no-longer-valid
runtime model:

* a flat `/music` directory tree (`inbox/`, `library/sorted/`, ...), not the
  current managed `Inbox/` / `Library/` / `Quarantine/` workspace;
* fully automatic, unattended pipeline processing on a systemd timer and an
  inotify-based folder watcher — the current product requires an explicit
  "Process All" action, never silent background processing;
* installing and driving the **`beet` CLI binary**, including writing a
  global `~/.config/beets/config.yaml` and moving files on disk
  (`move: yes`) — current crateIQ only ever uses the Beets **Python API**,
  never the CLI, and never moves/writes music files via Beets;
* `scripts/transfer.sh` ran `rsync --delete` against an old
  `/music/library/sorted` tree — current Publish/SSD Sync
  (`sync_destination_service`) never uses `--delete` and derives its source
  from the active managed workspace, never a hardcoded path;
* old `koolkatdj/djtoolkit` repository paths, GitHub references, and
  `%h/code/apps/djtoolkit` systemd `WorkingDirectory` values that do not
  correspond to this repository's actual location on any current machine.

Do not execute, source, symlink, or systemctl-enable anything in this
directory. Do not copy fragments of it into current tooling.

## Current equivalents

* Local frontend/backend service management: `scripts/crateiq-local-services.sh`.
* Import/preparation: the managed workspace's Inbox → explicit "Process All"
  → Needs Review → promotion workflow (see `PROJECT_CONTEXT.md`).
* Publish/SSD Sync: the guarded validate → preview → confirm → execute →
  verify contract in `backend/app/services/publish_sync_service.py` and
  `sync_destination_service.py` (no `--delete`, explicit confirmation,
  workspace-derived source, user-configured destination).
* Beets: Python API only, invoked per-track from Enrichment/Beets Review;
  the `beet` CLI binary remains forbidden (see
  `tests/test_no_beet_cli_invocation.py`).

## Contents

* `setup.sh.txt` — the original first-time bootstrap: created the old
  `/music` tree, ran `sudo apt` installs, installed/configured the `beet`
  CLI and a global Beets config, wrote `config_local.py`, and installed the
  systemd units below.
* `pipeline.sh.txt` — the unattended shell wrapper around the legacy
  automatic pipeline (distinct from `pipeline.py`, which remains active and
  load-bearing — see `AGENTS.md` Section 4.4).
* `watch_inbox.sh.txt` — an `inotifywait`-based watcher that triggered
  `pipeline.sh` automatically on file drop into the old `/music/inbox`.
* `transfer.sh.txt` — an `rsync --delete`-based transfer of the old
  `/music/library/sorted` tree and playlists to an external drive.
* `djtoolkit.service.txt`, `djtoolkit.timer.txt`,
  `djtoolkit-watch.service.txt` — the systemd user units that ran the above
  unattended, on old `koolkatdj/djtoolkit` repository paths.
* `beets_config.yaml.txt` — the legacy global Beets CLI configuration
  (`move: yes`, old `/music/library/sorted` paths, MusicBrainz plugins)
  installed by `setup.sh` into `~/.config/beets/config.yaml`.

Git history retains the original executable files (with their original
names and permissions) prior to this archival; these `.txt` copies exist
only so their content remains easy to read without executing anything or
digging through history.

These documents are left as originally written (aside from the `.txt`
suffix) for historical accuracy. If a detail here conflicts with current
source or `AGENTS.md`, current source and `AGENTS.md` win.
