# docs/archive/

Files in this directory are historical snapshots, retained for provenance
and reference. They are **not authoritative** for current crateIQ
architecture and are not required reading for active project memory.

For current architecture, read (in order of authority): current source
code, `AGENTS.md`, `README.md`, `PROJECT_CONTEXT.md`, `NEXT_TASKS.txt`, and
`CHANGELOG.txt`.

## Contents

* `AUDIT_REPORT.md` — the 2026-07-02 full technical/product audit. Its
  findings were acted on across the cycles that followed; see
  `docs/history/` for what came of them and `NEXT_TASKS.txt`/`AGENTS.md`
  Section 17 for what remains genuinely open today.
* `PROJECT_CONTEXT.txt` — a superseded, pre-web-app project context
  document (predates the FastAPI/React managed-workspace application).
* `DJToolkit_CONTEXT.txt` — the original "DJ Toolkit" project context
  document from before the crateIQ rename. Superseded in full by
  `PROJECT_CONTEXT.md`.
* `legacy-runtime/` — retired pre-managed-workspace runtime scripts and
  systemd units (unattended pipeline timer/watcher, `setup.sh`, `beet` CLI
  bootstrap, `rsync --delete` transfer script). See its own `README.md` —
  these are DO NOT RUN, kept as plain-text snapshots only.

These documents are left largely as originally written. Do not edit them to
make them look current — that would distort the historical record. If a
detail here conflicts with current source or `AGENTS.md`, current source
and `AGENTS.md` win.
