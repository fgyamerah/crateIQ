# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

DJs and crate-diggers who manage their own local DJ music library and run
CrateIQ themselves, locally, on their own machine. This is shared local-first
software — other DJs besides the primary maintainer install and run their own
instance against their own library, not a hosted multi-tenant service. Each
user operates their own trusted local environment; there is no shared backend
or account system between users.

## Product Purpose

CrateIQ is a local-first DJ library operations platform for building and
maintaining a clean, auditable, Rekordbox-ready music library. It inspects,
normalizes, reconciles, and enriches a DJ library without handing control of
musical analysis or performance-critical data to unstable automation. Success
looks like: a large, messy library of downloaded audio becomes an organized,
metadata-clean, duplicate-free, Rekordbox-ready library, with every
metadata/path change explainable and reviewable rather than silently applied.

## Positioning

CrateIQ is not a Rekordbox replacement and does not compete with Mixed In Key.
Its distinct mechanism is a review-first operational layer around the
library: deterministic local processing before AI or online lookup, explicit
human review queues before any metadata is applied, and strict ownership
boundaries (Mixed In Key/Rekordbox own BPM/key/cue/beatgrid data; CrateIQ
never overwrites it). A neighboring "auto-tag and auto-organize" tool could
not truthfully copy this claim, since CrateIQ's whole design bet is that
accidental writes and path drift are more dangerous than a missed enrichment
opportunity.

## Operating Context

- Runs entirely locally: a Python pipeline CLI (`pipeline.py`) plus a FastAPI
  backend plus a React/Vite frontend, all against a local SQLite DB
  (`logs/processed.db`) and a user-selected library root on disk.
- Typical session: a DJ points CrateIQ at a folder of downloaded/ripped
  tracks, runs pipeline stages (dedupe, organize, sanitize, analyze, tag,
  cue, playlists), then uses the web dashboard to browse tracks, review
  issues/enrichment candidates, inspect BPM/Camelot-key compatibility, check
  audit/reconciliation state, and eventually export to a Rekordbox-compatible
  drive.
- No authentication, no accounts, no multi-user concurrency within one
  instance — each DJ's install is their own trusted local environment.
- Supported frontend routes today: `/` (Library), `/quality`, `/issues`,
  `/enrichment`, `/metadata-repair`, `/metadata-sanitation`, `/bpm-review`,
  `/audit`, `/folders`, `/jobs`, `/set-builder`, `/exports`, `/sync`,
  `/reconciliation`. A handful of legacy pages (`Dashboard`, `Collection`,
  `Tracks`, `Settings`) exist in source but redirect to `/` and are not part
  of the supported surface.
- Demo data for local UI work: `scripts/seed_demo_library.py` seeds a fake
  library under `.run/demo-library/` only; never touches real audio or a
  real `DJ_MUSIC_ROOT`.

## Capabilities and Constraints

- Deterministic local metadata extraction and filename parsing (with
  confidence scoring) before any AI or online enrichment.
- Online enrichment is candidate scoring plus a human review queue
  (pending/approved/rejected/deferred) — never blind auto-apply.
- Dry-run is the default for every write-capable pipeline command; apply
  mode requires explicit `--apply --yes`.
- CrateIQ must never overwrite BPM, musical key, cue points, or beatgrid
  data — Mixed In Key and Rekordbox are authoritative for that data.
- Read-only backend browsing/inspection endpoints (tracks, issues, folders,
  audit, overview, compatible-tracks/Camelot matching) versus a small set of
  explicit, narrowly-scoped write endpoints (enrichment review
  approve/reject/defer, apply-approved with `confirm=true`).
- Path reconciliation is planning/preview-first; full automatic `--apply` is
  intentionally not implemented yet.
- Large-library performance work already done: DB indexes, paging, debounced
  search, virtualized tables, persisted UI state.
- Open/undecided: no formal design system or component library exists yet
  outside the newly-redesigned Library view; most other routes still use an
  older visual treatment that this rollout is meant to unify.

## Brand Commitments

Product name is **CrateIQ** only — no committed logo, tagline, or additional
identity language beyond the name itself.

## Evidence on Hand

- The live, redesigned Library view (`frontend/src/components/library/`) is
  the approved visual baseline: Inter typeface, dark background, an
  emerald/teal/cyan/violet/coral accent palette, compact runtime/status
  strips, KPI overview cards, filter chips, a dense track table, a right-side
  track inspector with an accessible SVG Camelot wheel and a real
  compatible-tracks panel backed by `GET /api/tracks/{id}/compatible`.
- `docs/architecture/STABILITY_MATRIX.md` and `PROJECT_CONTEXT.md` document
  current subsystem stability and history in detail.
- No user research, testimonials, or external evidence beyond the working
  codebase and its own documentation; do not fabricate any.

## Product Principles

1. Prefer no change over an unsafe or silent change — this applies to visual
   changes too: never fake data, never collapse a real degraded/error state
   into something that looks fine.
2. Deterministic and reviewable beats automatic and confident-looking, in UI
   copy and states as much as in pipeline logic.
3. One coherent visual system, not per-page reinvention — the Library view
   is the source of truth other routes converge toward.
4. Dense, work-focused operational UI, not a marketing surface — DJs
   scanning a large library value scanability and information density over
   decoration.
5. Respect existing safety boundaries (MIK/Rekordbox data ownership, dry-run
   defaults, no auth) as durable constraints the visual layer must never
   obscure or make easier to bypass.

## Accessibility & Inclusion

No formally documented accessibility requirement beyond general good
practice. The Library view already establishes a bar worth preserving:
accessible SVG (`role="img"`, `aria-label`, `aria-describedby` text
alternative on the Camelot wheel), verified color-contrast on highlighted
states, and readable status/degraded messaging without relying on color
alone.
