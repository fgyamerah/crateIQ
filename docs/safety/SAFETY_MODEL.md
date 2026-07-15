# CrateIQ Safety Model

## Core Doctrine

Prefer no change over unsafe change.

The safest state is preview-only. Any write to audio tags, filenames, folders, queues, or databases must be explicit, logged, and recoverable where practical.

## Metadata Ownership Rules

| Field | Owner | Policy |
|---|---|---|
| BPM | Mixed In Key | Pipeline should never overwrite valid existing BPM. `analyze-missing` may fill missing BPM only. |
| key | Mixed In Key | Pipeline should never overwrite valid existing key. `analyze-missing` may fill missing key only. |
| cue points | Mixed In Key | Pipeline cue suggestions are advisory DB/sidecar data only; do not export/write over MIK cues. |
| artist | Human/operator plus deterministic artist tools | AI-normalize artist output is ignored. Artist-changing tools require confidence/review gates. |
| title | Existing tags plus deterministic cleanup/enrichment | Write only when high-confidence and protected by version/artist guards. |
| album | Online enrichment | Fill missing or ISRC-anchored values only. |
| label | Label/enrichment tools | Fill empty or high-confidence values; `label-clean` uses explicit `--write-tags`. |
| ISRC | Existing tag or trusted enrichment | Only write if missing. Do not overwrite existing ISRC. |
| filenames | Filename/library organization tools | Use embedded trusted tags; preview first; no overwrite collisions. |
| folder structure | Library organization/artist folder tools | Preview first; preserve recoverability with move manifests. |

## Safety Gates

| Gate | Current model |
|---|---|
| `--apply` | Required by newer high-risk commands before writing tags, renaming, or moving files. Not universal. |
| dry-run/preview | Preview is default for newer modules. Older modules may apply by default unless `--dry-run` is passed. |
| confidence thresholds | VERIFIED: `ai-normalize` default is 0.80. VERIFIED: online enrichment apply threshold is 0.90 and review threshold is 0.75. CONFLICTING_IMPLEMENTATIONS: older context text still mentions lower thresholds. |
| hard blocks | Artist lock, artist mismatch, version mismatch, ISRC overwrite prevention, and MIK-first BPM/key/cue policy. |
| review states | Review queues hold uncertain AI/enrichment/artist decisions; BPM anomalies use pending/reviewed/ignored/requeued/resolved. |
| quarantine | VERIFIED: active enrichment IGNORED path is `.BIN/IGNORED`; it preserves relative structure and appends `_dupN` on collisions. Other quarantine flows remain partially UNVERIFIED. |

## Verified Safety Details

- VERIFIED: `ai-normalize` ignores AI artist output and reconstructs title deterministically from current title plus guarded version hints.
- VERIFIED: enrichment artist mismatch and version conflict produce no proposed changes and cap confidence at 0.74.
- VERIFIED: enrichment exact ISRC match bypasses matching gates and produces a ready decision.
- VERIFIED: enrichment review queue dedupes by exact `file_path`.
- VERIFIED: artist repair review queue dedupes by `(file, original_artist)` and preserves human approval flags.
- VERIFIED: file rename tracking updates `processed_state` only; other path-bearing tables/queues can stale.
- CONFLICTING_IMPLEMENTATIONS: old high-level context still lists outdated enrichment and AI thresholds.

## Destructive Operation Policy

| Operation | Rule |
|---|---|
| Tag writes | Must preview first, require explicit apply/write flag, preserve before/after values, and never overwrite MIK-owned BPM/key/cues. |
| Renames | Must preview first, avoid overwrites, update all path-bearing DB tables/queues, and record old/new paths. |
| Moves | Must preview first, preserve relative structure where possible, avoid collisions, and write a restore manifest. |
| Deletes | Do not delete audio files automatically. DB deletes should be replaced with tombstones or transactional path updates. |
| DB writes | Use transactions; mark stale instead of hard delete; reconcile DB state with filesystem after path mutations. |

## Rollback Policy

Current rollback is limited.

- `metadata-sanitize` has the clearest rollback path through its JSON/JSONL log and rollback command.
- `track_history` exists, but the active sanitizer path may not write to it.
- Most AI/enrichment/artist/label tag writes do not have a confirmed rollback command.
- File moves/renames do not have a universal restore tool.
- DB state can diverge from filesystem state after renames, moves, queue operations, and external edits.
- VERIFIED: `artist-merge`, `artist-folder-clean`, and legacy `organizer.py` include `DELETE FROM tracks` during move/merge workflows.
- VERIFIED: `convert-audio` unlinks failed destination outputs; source files are archived on success rather than deleted.

Required policy: every destructive operation should record command, timestamp, old path, new path, old tags, new tags, and DB rows changed before applying.

## AI Policy

- AI normalization is local Ollama according to `PROJECT_CONTEXT.txt`; verify before enabling any remote provider.
- AI must not touch BPM, key, or cue points.
- AI must not rename files or move folders.
- AI output must not auto-apply unless deterministic validation passes.
- Artist changes require strict deterministic validation or human approval.
- Treat hallucinated titles, remix/version tokens, labels, and collaborations as expected failure modes.
- ISRC matches are strong but not absolute; source conflicts should force review.

## Operator Rules

- Always run preview first.
- Process small batches before bulk runs.
- Back up audio files and SQLite databases before destructive runs.
- Never trust AI output blindly.
- Never overwrite Mixed In Key BPM, key, or cue fields.
- Review quarantine folders before deleting anything.
- Do not lower confidence thresholds for bulk runs without sampling results first.
- Reconcile DB and filesystem after large rename/move runs.
