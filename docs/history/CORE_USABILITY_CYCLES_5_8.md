# Core Usability Cycles 5–8

Branch: `feat/crateiq-core-usability` (base `main` `4119013`). Not merged to
main. Goal: turn the existing backend contracts into one coherent guided
workflow (Library Prep) covering import through DJ readiness, then make the
highest-risk step — real file tag writes — safe.

## Cycle 5 — Core Library Workflow (2026-08-08)

New unified `/library-prep` workspace presenting the target workflow as
seven steps (Import, Clean, Enrich, Review, Apply, Analyze, Ready). Steps
1–2 were genuinely functional (reused existing scan/import + sanitation/
repair contracts unchanged); steps 3–7 were truthfully labeled "not
available yet" placeholders. Closed a real gap: web import now reads
embedded tags via `mutagen` before falling back to filename parsing,
matching the CLI pipeline. Impeccable review found and fixed a CSS wrapper
bug, a locked-step link affordance issue, a silently-swallowed load error,
and a duplicate icon. 1444 backend tests pass (+13).

Known tradeoff, deliberately not fixed: Settings.tsx kept its own separate
import scan/import UI alongside Library Prep's — two UI entry points over
the same backend contracts (see `NEXT_TASKS.txt`).

## Cycle 6 — Real Enrichment (2026-08-08)

Real Beets (distance-scored) and MusicBrainz (raw search) suggestions,
wrapping beets' own upstream-tested MB HTTP client. New
`POST /api/enrichment/review/tracks/{id}/online-lookup`: explicit,
single-track, bounded, cached 30 days, never overwrites non-empty fields.
Live-verified against the real backend. 1459 backend tests pass.

**Incident:** an ad-hoc `beet --version` shell invocation during manual
testing (outside the isolated adapter code) touched the real
`~/.config/beets/library.db` and ran schema migrations against it; restored
from beets' own pre-migration backup, verified empty tables matched the
pre-existing empty library. **Resulting hard rule: never invoke the `beet`
CLI binary from CrateIQ code** — only the Python API. This rule is now
enforced by a static AST regression guard added in the pre-merge hardening
pass below.

## Cycle 7 — Controlled Metadata Write-Back (2026-08-08)

The highest-risk cycle: real writes to actual audio file tags. New
`tag_write_service`: `build_plan()` (read-only diff), `apply_plan()`
(stale-check against a fresh preview, hash-verified backup outside the
scanned tree, mutagen write of only diffed fields, re-read verify),
`restore_file()` (atomic hash-verified restore). Write surface is
deliberately four fields only — artist, title, album, genre — MP3/FLAC
only; every other format is an explicit blocker. New `/apply-to-files`
page and `tag_write_operations` history table with restart recovery.

**Near-miss:** a hand-written acceptance script omitted an explicit
`library_root` argument to two Cycle-5 functions, which fell back to a
*pending* root file rather than the active one — it silently ran against
the real sanctioned library. Confirmed harmless (no-op write only, no
files changed) but documented as a trap for any future caller of those two
entry points. 1465 backend tests pass (10 new, real ffmpeg-generated
fixtures).

## Pre-merge hardening pass (2026-08-08)

Three fixes found via inspection and a real disposable-library browser
walkthrough:

1. `library_setup_service._target_root()` no longer falls back to the
   pending library root when a mutating call omits an explicit root — it
   now always resolves the canonical active root and fails closed,
   closing the Cycle 7 near-miss above.
2. `modules/organizer.py`'s default beets-CLI subprocess path was removed
   — it always uses the Python fallback organizer now; added
   `tests/test_no_beet_cli_invocation.py`, a static AST-based guard that
   fails the suite if any production code ever invokes the `beet` binary.
3. Found live in the browser: every real UI-driven Apply-to-Files write
   failed as a false "stale plan" because `expected_mtime_ns` (nanosecond
   epoch) exceeded JS's safe-integer range and got silently corrupted on
   the JSON round-trip — fixed by sending it as a string end-to-end.

1474 backend tests pass; `validate-docs --strict` passes.

## Cycle 8 — DJ Preparation (2026-08-08)

Final cycle: Library Prep's Analyze step now launches real BPM/key
analysis and waveform generation (reusing existing `analysis_jobs_service`/
`waveform_bulk_service` contracts, no new engine). New
`library_readiness_service.build_readiness()` composes existing signals
into BLOCKER/WARNING/OPTIONAL reason codes; `ready = zero blockers`. New
`GET /api/library/readiness`. Also fixed stale Cycle-5-era copy claiming
Beets/MusicBrainz "land in the next cycle" (false since Cycle 6) and
removed the now-fully-dead "unavailable"/locked step state.

**Final end-to-end acceptance**: one script chained the full 17-step
journey (import → sanitation → real Beets/MusicBrainz enrichment → write
plan → backup → confirmed write → re-read verify → restore byte-identical
→ real BPM analysis → key/waveform launch paths exercised → readiness
`ready=true` with 2 truthful warnings). All 88 sanctioned library files
confirmed byte-unchanged throughout the whole 4-cycle program. 1468
backend tests pass; `validate-docs --strict` passes (24/24 registry
commands present).
