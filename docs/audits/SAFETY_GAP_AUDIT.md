# Safety Gap Audit

**Project:** CrateIQ
**Date:** 2026-05-03  
**Sources used first:** `PROJECT_CONTEXT.txt`, generated indexes under `docs/generated/`, `docs/audits/CLI_RISK_AUDIT.md`, `docs/audits/DB_SCHEMA_AUDIT.md`.

Missing requested inputs: `docs/audits/PHASE1_INVENTORY.md` and `docs/audits/DOCUMENTATION_GAPS.md` were not present in this workspace.

## Status Key

| Status | Meaning |
|---|---|
| implemented | Confirmed in generated/static docs |
| partial | Exists, but coverage or consistency is incomplete |
| missing | No evidence of implementation in reviewed docs |
| UNVERIFIED | Needs source/runtime verification |

## Verification Markers

| Marker | Meaning |
|---|---|
| VERIFIED | Confirmed by generated docs plus targeted source inspection |
| UNVERIFIED | Not confirmed; do not assume |
| CONFLICTING_IMPLEMENTATIONS | Source/comments/docs disagree or multiple implementations differ |

## Phase A Verification Addendum

| Detail | Marker | Finding |
|---|---|---|
| `ai-normalize` confidence default | VERIFIED | `ai/metadata_schema.py` defines `MIN_AI_CONFIDENCE = 0.80`. Older context text saying 0.75 is stale/conflicting. |
| Enrichment apply/review thresholds | VERIFIED / CONFLICTING_IMPLEMENTATIONS | Runtime matcher constants are `THRESHOLD_APPLY = 0.90`, `THRESHOLD_REVIEW = 0.75`; `config.ENRICH_ONLINE_MIN_CONFIDENCE` default is 0.90. Older context saying apply 0.80/review 0.70 is stale/conflicting. |
| Enrichment hard confidence caps | VERIFIED | Artist mismatch and version conflict return `confidence=min(top_conf, 0.74)` with no proposed changes. |
| ISRC exact override | VERIFIED | Exact ISRC match bypasses gates, returns `decision_code="ready"`, confidence 0.98 from scoring, and builds changes as ISRC-matched. |
| Enrichment review queue dedupe | VERIFIED | `enrichment_review_queue.json` dedupes by exact `file_path`; existing entry is replaced. |
| Artist repair queue dedupe | VERIFIED | `artist_repair_queue.json` dedupes by `(file, original_artist)` and preserves approved/rejected/applied flags. |
| IGNORED destination | VERIFIED / CONFLICTING_IMPLEMENTATIONS | `config.IGNORED_DIR = BIN_DIR / "IGNORED"`; older `PROJECT_CONTEXT.txt` says `/home/koolkatdj/Music/music/IGNORED/`. Active source points to `.BIN/IGNORED`. |
| IGNORED structure/collisions | VERIFIED | Enrichment move preserves structure relative to `ignored_root.parent`; collisions append `_dupN`. |
| Apply/dry-run defaults | VERIFIED | `metadata-clean`, `tag-normalize`, `analyze-missing`, `convert-audio`, `cue-suggest`, and `db-prune-stale` write by default unless `--dry-run` is passed. Newer AI/artist/enrichment/rename/move commands use preview plus `--apply`. |
| Rename tracking scope | VERIFIED | `run_logger.rename_path()` calls `db.rename_processed_path()`, which updates `processed_state` only. |
| Rollback snapshots | VERIFIED / PARTIAL | `track_history` and `scripts/rollback.py` exist; active `metadata-sanitize` rollback is JSON/JSONL-log based. Universal before/after snapshots are missing. |
| Delete behavior | VERIFIED | `artist-merge`, `artist-folder-clean`, and legacy `organizer.py` contain `DELETE FROM tracks`; file deletes are not the normal audio-file strategy, but `convert-audio` unlinks failed destination outputs. |
| Organizer sync behavior | VERIFIED / PARTIAL | Organizer moves files and writes `track_history`; exact beets old/new path tracking is limited by existing audit notes. |

## 1. Hard Safety Blocks

| Block | Status | Where discovered | Protects | Risk remains |
|---|---|---|---|---|
| Preview/default no-write mode for newer commands | partial | `safety_logic_index.md`, `CLI_RISK_AUDIT.md`, `pipeline.py` help excerpts in `dangerous_operations_index.md` | Prevents accidental tag writes, renames, moves | Several commands still write by default unless `--dry-run` is passed: `metadata-clean`, `analyze-missing`, `tag-normalize`, `convert-audio`, `cue-suggest`; interactive `review-queue` writes on `a/apply`. |
| `--apply` and `--dry-run` mutual exclusion | implemented | `ai/normalizer.py`, `artist/runner.py`, `enrichment/runner.py` entries in `safety_logic_index.md` | Prevents ambiguous execution mode | Not universal across older modules. |
| AI confidence gate | implemented | `ai/metadata_schema.py`, `ai/normalizer.py`, `pipeline.py` help | Blocks low-confidence AI tag writes | VERIFIED default is 0.80; older 0.75 context is stale/conflicting. |
| Enrichment confidence gate | implemented | `metadata_matcher.py`, `config.py`, `PROJECT_CONTEXT.txt` | Separates apply/review/skip | VERIFIED apply >= 0.90 and review >= 0.75; older context saying apply >= 0.80/review >= 0.70 is CONFLICTING_IMPLEMENTATIONS. |
| Artist lock in `ai-normalize` | implemented | `ai/normalizer.py` `_apply_hard_guards` in `safety_logic_index.md` | AI artist output ignored; prevents hallucinated artist changes | Other artist-writing modules exist (`artist-intelligence`, `artist-repair`) and need their own confidence/review gates. |
| Artist mismatch protection in online enrichment | implemented | `metadata_matcher.py` targeted source | Prevents metadata from wrong artist being applied | VERIFIED cap to 0.74 and skipped decision without ISRC anchor; bad canonical artist data still risky. |
| Version mismatch protection | implemented/partial | `metadata_matcher.py`, `ai/normalizer.py` targeted source | Prevents clean/radio/remix/version collisions | VERIFIED enrichment version conflict cap to 0.74; version token coverage remains partly UNVERIFIED across all modules. |
| ISRC exact-match override | implemented | `metadata_matcher.py`, enrichment write logic | Allows strong match despite weaker text similarity | VERIFIED bypasses gates; risk remains if upstream/current ISRC is wrong. |
| ISRC overwrite prevention | implemented | `PROJECT_CONTEXT.txt` change policy | Avoids replacing existing ISRC | `metadata-sanitize` can delete malformed ISRCs; recovery depends on JSON log and is not DB-backed. |
| BPM/key/cue ownership by MIK | partial | `PROJECT_CONTEXT.txt`, `doc_registry.py` entries | Prevents pipeline from overwriting Mixed In Key data | `analyze-missing` and legacy `tagger.py` can write BPM/key when missing/calculated. Need explicit guard against overwriting valid MIK fields in all write paths. |
| Unsafe mutation prevention for filenames | implemented/partial | `filename_normalize.py`, `library_organize.py` entries | Blocks unsafe artist filenames and avoids overwrite collisions | Moves/renames have no global rollback ledger. |
| Collision handling for moves/renames | implemented/partial | `dangerous_operations_index.md` | Avoids file overwrite | Collision suffix behavior varies by module; not centrally tested. |

## 2. Review Queues

| Queue | Status | File | Lifecycle | Deduplication | Gaps |
|---|---|---|---|---|---|
| AI normalize dataset queue | implemented | `data/intelligence/review_queue.jsonl` | Appends one entry per file processed by `ai-normalize`; accepted/rejected examples written separately | none discovered; append-only JSONL | Not a true work queue; stale entries accumulate; `review_decisions.jsonl` is reserved/future. |
| Enrichment review queue | implemented | `data/intelligence/enrichment_review_queue.json` | Low/medium confidence or review-needed enrichment entries are saved; `review-queue` can apply/skip/delete | Dedupes by `file_path`; existing entry replaced/refreshed | Interactive apply writes tags without `--apply`; queue paths can become invalid after moves/renames; skipped/deleted removes queue entry but does not preserve decision history in reviewed docs. |
| Artist intelligence review queue | implemented/partial | `data/intelligence/artist_review_queue.json` | Ambiguous artist aliases/normalizations queued | Updated in-place by file+artist per index | Apply/review lifecycle details not fully verified. |
| Filename unsafe artist review queue | implemented/partial | `data/review/artist_review_queue.jsonl` | Unsafe concatenated artist names are logged; optional quarantine with `--move-artist-review --apply` | none discovered | Separate path from artist intelligence queue; stale duplicate review surfaces likely. |
| Artist repair queue | implemented | `data/intelligence/artist_repair_queue.json` | Medium/low confidence repairs queued; approve/reject/apply-approved workflow | UNVERIFIED | Approved repairs write with `--apply-approved`; stale paths remain a risk. |
| BPM anomaly review | implemented | `backend/data/jobs.db:bpm_anomalies` | Backend detects anomalies; statuses: pending, reviewed, ignored, requeued, resolved | UNIQUE(track_id) | Cross-DB track reference can dangle; filepath/artist/title snapshots can become stale. |

**Queue lifecycle risks**

- Review queues are path-based; file moves/renames can invalidate entries.
- Most queues do not appear to store immutable before/after tag snapshots sufficient for rollback.
- Deduplication by `file_path` refreshes active queue entries but can hide older recommendations and decision history.
- JSON/JSONL queues are not reconciled against `tracks.filepath` or actual filesystem paths.

## 3. Quarantine / IGNORED Workflow

| Workflow | Status | Trigger | Destination | Structure | Collision handling | Recovery gap |
|---|---|---|---|---|---|---|
| Enrichment IGNORED | implemented | `metadata-enrich-online --move-ignored --apply`; hard skips such as low score, artist mismatch, version conflict | VERIFIED active source: `config.IGNORED_DIR = BIN_DIR / "IGNORED"` | VERIFIED relative to IGNORED parent | VERIFIED `_dup1`, `_dup2`, ... | No rollback command; review queue/DB paths may not update after move. |
| Artist repair quarantine | implemented | `artist-repair --move-artist-review --apply` for review/blocked files | `.BIN/ARTIST_REVIEW/` or `.BIN/CHKARTISTNAMES/` per docs | Relative structure preserved per module header | UNVERIFIED | No restore command; queue entry can point to old or moved path depending implementation. |
| Filename artist review quarantine | implemented/partial | `filename-normalize --move-artist-review --apply` | `.BIN/ARTIST_REVIEW/`/`CHKARTISTNAMES` references vary | UNVERIFIED | Safe suffix collisions indicated | Multiple artist review queue locations. |
| Dedupe quarantine | implemented | `dedupe --apply` or library dedupe apply | duplicates/quarantine dir | UNVERIFIED | UNVERIFIED | Files are moved, never deleted, but no reconstruction tool was found. |
| Audit-quality low-quality move | implemented/partial | `audit-quality --move-low-quality DIR` | operator-supplied dir | UNVERIFIED | UNVERIFIED | No rollback tool; low-quality classification mistakes can remove files from active library. |

**Accidental data loss risk:** moderate to high. Files are usually moved rather than deleted, but move manifests are inconsistent, DB updates are incomplete across tables, and there is no universal restore command.

## 4. Destructive Operations

| Command/Module | Operation | Risk | Protection | Gap |
|---|---|---:|---|---|
| `metadata-sanitize` | Writes/deletes selected tags, can delete malformed ISRC | MODERATE | Preview default, `--apply`, JSON log, rollback subcommand for titles | Rollback depends on external log; not DB-backed. |
| `metadata-sanitize-rollback` | Writes title tags from log | MODERATE | Preview default, `--apply` | Only covers sanitizer/title recovery paths, not all tag writes. |
| `ai-normalize` | Writes artist/title/version/label tags | HIGH | Preview default, `--apply`, confidence gate, artist lock, hard guards | AI hallucination risk; no general rollback. |
| `artist-intelligence` | Writes artist tag | HIGH | Preview default, `--apply`, confidence gate | Artist changes are high-impact; rollback not found. |
| `artist-repair` | Writes artist tag; optionally moves review files | HIGH | Preview default, `--apply`; blocked lower confidence queued | Queue/path staleness; no restore tool. |
| `artist-repair-review` | Writes approved artist repairs | HIGH | `--apply-approved` only | Manual approval can still apply stale path/proposal. |
| `metadata-enrich-online` | Writes album/label/ISRC/title changes; moves ignored | HIGH | Preview default, `--apply`, confidence/review/hard blocks | Threshold/path conflicts; interactive review applies without `--apply`; no rollback. |
| `review-queue` | Interactive enrichment tag writes; removes queue entries | HIGH | Human prompt | No `--apply`/dry-run gate; no durable decision/rollback log found. |
| `label-clean` | Writes organization/TPUB label | MODERATE | Requires `--write-tags`; threshold default 0.85 | Lower threshold option can broaden writes; no rollback. |
| `metadata-clean` | Cleans raw/easy tags, strips ID3v1, deletes junk frames | HIGH | `--dry-run` exists | Applies by default; no `--apply` gate. |
| `tag-normalize` | Converts ID3v2.4 to v2.3; strips ID3v1 | HIGH | `--dry-run` exists | Applies by default; conversion/removal not generally reversible. |
| `analyze-missing` | Writes BPM/key to DB and audio tags; can move files | HIGH | `--dry-run` exists; should only fill missing values | Applies by default; BPM anomaly issue known; must not overwrite MIK. |
| `cue-suggest` | Writes cue suggestions to DB and optional sidecars | MODERATE | `--dry-run` exists; sidecars disabled by config by default | No `--apply`; cue rows orphan after rename. |
| `filename-normalize` | Renames files; can move unsafe artist files | HIGH | Preview default, `--apply`, no overwrite collisions | No move manifest/rollback; only `processed_state` path update confirmed. |
| `library-organize` | Moves files into sorted/flattened structure | HIGH | Preview default, `--apply`, collision suffixes | DB/path updates incomplete across cue/set/review data. |
| `artist-merge` | Moves files, merges folders, deletes old `tracks` rows | CRITICAL | Preview default, `--apply`, only "safe" groups auto-applied | DB row delete is permanent if destination upsert fails; no rollback. |
| `artist-folder-clean` | Renames/merges folders, deletes old `tracks` rows | CRITICAL | Preview default, `--apply`, reports | Same DB delete/upsert risk; no rollback. |
| `organizer.py` | Moves files and deletes old `tracks` rows | CRITICAL | Legacy pipeline step; UNVERIFIED active use | Beets moves can prevent exact old/new tracking. |
| `dedupe` / `library_dedupe` | Moves duplicates to quarantine | HIGH | `--apply`; never deletes automatically | No restore command; duplicate_groups lacks moved destination/resolution lifecycle. |
| `audit-quality` | Optional QUALITY tag write; optional low-quality move | MODERATE | Defaults read-only; opt-in flags | No rollback for moves/tags. |
| `convert-audio` | Converts files, may overwrite output, archives source, unlinks failed output | HIGH | `--dry-run`; no source delete | Applies by default; `--overwrite` and failed-output unlink need careful path validation. |
| `playlists` / `rekordbox-export` | Writes M3U/XML/export files | LOW | Dry-run in export paths | Can overwrite export artifacts; no audio mutation. |
| `set-builder` | Writes M3U/CSV and DB set records | LOW | `--dry-run` | Stored paths become stale after renames. |
| `orphan-scan` | Marks stale DB rows | MODERATE | Preview default, `--apply`; never deletes files | Stale marking can diverge from filesystem if paths later restored. |
| Backend SSD sync | `rsync`, optional `--delete` on destination | HIGH | Preview endpoint; `allow_delete=False` default | Destination deletion is destructive; depends on UI/operator safeguards. |

## 5. Rollback / Recovery Gaps

| Area | Current recovery | Cannot reliably reverse | Notes |
|---|---|---|---|
| `metadata-sanitize` | `metadata-sanitize-rollback` with JSON/JSONL log | Full multi-field recovery if log missing; all other modules | Only clear rollback coverage found. |
| `track_history` | `scripts/rollback.py` references DB history | Active new sanitizer does not write to `track_history` | DB audit says table is effectively orphaned by active pipeline. |
| Tag writes from AI/enrichment/artist/label/clean | none found | Original tag values | Need before/after snapshots before apply. |
| File renames/moves | partial logs/reports per module | Universal old->new reconstruction | `processed_state` may update, but cue/set/review queues can stale. |
| DB mutations | SQLite transactions per helper | Cross-table logical rollback | `DELETE FROM tracks` in merge/folder/organizer is risky. |
| Review queues | entry removal on apply/skip | Decision audit and stale proposal recovery | Accepted/rejected JSONL exists for AI/enrichment datasets, but not a universal queue history. |

**DB/filesystem divergence is currently possible** whenever a file is moved/renamed and only a subset of tables/queues are updated.

## 6. AI Safety Boundaries

| AI-driven module | Metadata impact | Current protections | Risk |
|---|---|---|---|
| `ai-normalize` | Proposes artist/title/version/label, but artist is hard-locked in AI path | Local Ollama per context, confidence gate, hard guards, preview default, no BPM/key/cue writes | Hallucinated title/version/label; threshold ambiguity; no rollback. |
| `metadata-enrich-online` | Online sources fill album/label/ISRC/title under policy | Confidence/review/hard blocks, ISRC anchor, preview default | Bad upstream match, stale ISRC, source disagreement; exact thresholds conflict in docs. |
| `artist-intelligence` | Canonicalizes artist | Confidence gate, review queue | Artist identity mistakes are high blast-radius. |
| `artist-repair` | Repairs concatenated/broken artist names | High-confidence only auto-eligible; lower confidence queued/quarantined | Known-artist registry errors can produce wrong repairs. |
| `label-intelligence` / `label-clean` | Label parsing/enrichment | Threshold gate, write-tags opt-in | Label is lower-risk but still user-visible. |
| `cue-suggest` | Algorithmic cue suggestions stored in DB/sidecars | MIK-first doctrine; no audio cue writes found | Cue data can be mistaken for authoritative if exported later. |

**Recommended restrictions**

- AI must not write BPM, key, cue points, filenames, or folder structure.
- AI outputs should only auto-apply after deterministic validation against current tags and filename tokens.
- Artist/title changes should require either high confidence plus hard guards or manual approval.
- ISRC-based overrides should log source, before/after, confidence, and match rationale.
- All AI/applied recommendations need durable before/after rollback records.

## 7. DB / Filesystem Consistency Risks

| Risk | Status | Evidence | Impact |
|---|---|---|---|
| Stale absolute paths in `tracks` | implemented risk | `DB_SCHEMA_AUDIT.md` | Renames outside pipeline silently stale DB rows. |
| `cue_points` not updated on rename | implemented risk | `DB_SCHEMA_AUDIT.md` | Cue suggestions orphan under old path. |
| `set_playlist_tracks` stale after rename | implemented risk | `DB_SCHEMA_AUDIT.md` | Web UI/set history points to missing files. |
| `duplicate_groups` lacks moved destination/resolution | implemented risk | `DB_SCHEMA_AUDIT.md` | Duplicate quarantine cannot be reconstructed. |
| Review queue paths invalid after moves | implemented risk | queue files are path-based | Queue entries may apply to missing/wrong files. |
| `bpm_anomalies` cross-DB track reference dangles | implemented risk | `jobs.db` schema audit | Backend review state can outlive track row. |
| `processed_state` mtime/size skip false negative | implemented risk | `DB_SCHEMA_AUDIT.md` | Changed file can be skipped if size/mtime match. |
| Missing reconciliation command | missing | No generated index evidence | Need command to compare DB, queues, logs, and filesystem. |

## Critical Fixes

| Problem | Why it matters | Suggested implementation | Files likely involved |
|---|---|---|---|
| Apply-by-default destructive commands | One missed `--dry-run` can mutate tags/DB/files | Standardize all destructive commands to preview-first plus explicit `--apply`; keep legacy compatibility with deprecation warning only if needed | `pipeline.py`, `modules/metadata_clean.py`, `modules/analyze_missing.py`, `modules/tag_normalize.py`, `modules/convert_audio.py`, `modules/cue_suggest.py` |
| No universal rollback ledger | Most tag writes/moves cannot be reversed | Add append-only `operation_history` or JSONL ledger recording command, file, old path, new path, before tags, after tags, DB rows touched | `db.py`, write modules, `modules/run_logger.py` |
| Merge/folder/organizer delete `tracks` rows | Failed destination upsert can permanently lose DB state | Replace delete+upsert with transactional path update or tombstone row; record before/after | `modules/artist_merge.py`, `modules/artist_folder_clean.py`, `modules/organizer.py`, `db.py` |
| `review-queue` writes without apply gate | Interactive keypress can mutate audio without preview/confirmation | Require `--apply` or a second confirmation for writes; add `--dry-run`; record decision history | `intelligence/enrichment/runner.py`, `pipeline.py` |
| DB/filesystem reconciliation missing | Stale paths break queues, cues, sets, backend review | Add `reconcile-library` command that checks tracks, cue_points, sets, duplicate_groups, review queues, and actual files | `pipeline.py`, `db.py`, queue modules |

## Important Fixes

| Problem | Why it matters | Suggested implementation | Files likely involved |
|---|---|---|---|
| Threshold documentation conflicts | Operators cannot know which safety gate is active | Generate threshold docs from config/constants; add tests asserting CLI defaults | `config.py`, `ai/metadata_schema.py`, `intelligence/enrichment/metadata_matcher.py`, docs generator |
| Queue staleness | Old proposals can apply to missing or changed files | Store file size/mtime/hash and current tag fingerprint in queue entries; validate before apply | queue writers/readers |
| `track_history` orphaned by active sanitizer | Existing rollback script may not cover new runs | Either write active sanitizer snapshots to `track_history` or deprecate DB rollback in docs | `modules/metadata_sanitize.py`, `db.py`, `scripts/rollback.py` |
| `cue_points` stale after rename | Set/cue UI can show wrong path | Update cue/set/duplicate paths in path-rename helper, not only `processed_state` | `db.py`, `modules/run_logger.py` |
| ISRC override lacks conflict review evidence | Bad ISRC can authorize wrong metadata | If multiple sources disagree, force review even with an ISRC match; log source provenance | `intelligence/enrichment/metadata_matcher.py`, `runner.py` |
| Backend sync delete risk | `rsync --delete` can remove destination files | Require preview token or recent preview before delete-enabled sync | `backend/app/services/rsync_runner.py`, sync API/UI |

## Optional Improvements

| Problem | Why it matters | Suggested implementation | Files likely involved |
|---|---|---|---|
| Multiple artist review queue paths | Operators can miss review items | Consolidate queue schema or create unified review dashboard | `config.py`, artist/filename modules, backend UI |
| Weak DB constraints | Bad statuses and invalid values can persist | Add CHECK constraints in migration path where safe | `db.py`, `backend/app/core/db.py` |
| No queue retention policy | JSONL queues/logs grow forever | Add compaction/archive command | queue modules |
| Missing per-command stability docs | Future AI sessions need quick risk context | Keep `STABILITY_MATRIX.md` generated/maintained with command risk level | docs generator |
