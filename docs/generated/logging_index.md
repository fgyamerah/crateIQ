# Generated Logging Index

## `ai/normalizer.py`
- Line 43: `log = logging.getLogger(__name__)`
- Line 914: `level = _logging.DEBUG if getattr(args, "verbose", False) else _logging.INFO`
- Line 916: `_logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")`

## `ai/review_dataset.py`
- Line 5: `review_queue.jsonl      — one entry per file processed by ai-normalize (all outcomes)`
- Line 6: `accepted_examples.jsonl — auto-applied changes (training positives)`
- Line 7: `rejected_examples.jsonl — rejected / skipped / errored results (training negatives)`
- Line 8: `review_decisions.jsonl  — reserved for future manual review decisions`
- Line 9: `fewshot_examples.jsonl  — curated diverse subset built by build_fewshot()`
- Line 32: `log = logging.getLogger(__name__)`
- Line 47: `"""Append one JSON object as a line to a JSONL file. Thread-safe for single-process use."""`
- Line 57: `"""Read all valid JSON objects from a JSONL file. Skips malformed lines."""`
- Line 101: `Append one record to review_queue.jsonl for a file processed by ai-normalize.`
- Line 134: `Append one record to accepted_examples.jsonl when --apply writes a change.`
- Line 164: `Append one record to rejected_examples.jsonl.`
- Line 186: `Read accepted_examples.jsonl, pick a diverse subset, write fewshot_examples.jsonl.`
- Line 257: `Return the current fewshot_examples.jsonl as a list of dicts.`

## `backend/app/api/routes/analysis.py`
- Line 38: `log = logging.getLogger(__name__)`

## `backend/app/api/routes/exports.py`
- Line 21: `log = logging.getLogger(__name__)`

## `backend/app/api/routes/jobs.py`
- Line 23: `log = logging.getLogger(__name__)`
- Line 119: `if not job.log_path:`
- Line 122: `log_path = Path(job.log_path)`

## `backend/app/api/routes/library.py`
- Line 262: `cmd_dir.glob("*_summary.json"),`
- Line 266: `prefix = sf.name[: -len("_summary.json")]`
- Line 295: `p = _logs_path(command, f"{prefix}_summary.json")`
- Line 308: `for pg in sorted(cmd_dir.glob(f"{prefix}_{group}_*.json")):`
- Line 309: `m = _re.search(r'_(\d+)\.json$', pg.name)`
- Line 355: `p = _logs_path(command, f"{prefix}_{group}_{page}.json")`

## `backend/app/api/routes/playlists.py`
- Line 25: `log = logging.getLogger(__name__)`

## `backend/app/api/routes/sync.py`
- Line 28: `log = logging.getLogger(__name__)`

## `backend/app/api/routes/tracks.py`
- Line 22: `log = logging.getLogger(__name__)`

## `backend/app/core/db.py`
- Line 15: `log = logging.getLogger(__name__)`

## `backend/app/core/pipeline_db.py`
- Line 18: `log = logging.getLogger(__name__)`

## `backend/app/main.py`
- Line 34: `logging.basicConfig(`
- Line 35: `level=logging.INFO,`
- Line 40: `log = logging.getLogger(__name__)`

## `backend/app/schemas/job.py`
- Line 77: `log_path         = job.log_path,`

## `backend/app/services/bpm_analysis.py`
- Line 41: `log = logging.getLogger(__name__)`

## `backend/app/services/export_validation.py`
- Line 34: `log = logging.getLogger(__name__)`

## `backend/app/services/job_service.py`
- Line 23: `log = logging.getLogger(__name__)`
- Line 49: `log_path  = str(JOBS_LOG_DIR / f"{job_id}.log")`

## `backend/app/services/playlist_service.py`
- Line 16: `log = logging.getLogger(__name__)`

## `backend/app/services/process_registry.py`
- Line 20: `log = logging.getLogger(__name__)`

## `backend/app/services/rsync_runner.py`
- Line 47: `log = logging.getLogger(__name__)`
- Line 307: `log_path = JOBS_LOG_DIR / f"{job.id}.log"`

## `backend/app/services/toolkit_runner.py`
- Line 35: `log = logging.getLogger(__name__)`
- Line 248: `log_path = JOBS_LOG_DIR / f"{job_id}.log"`

## `backend/app/services/track_service.py`
- Line 17: `log = logging.getLogger(__name__)`

## `config.py`
- Line 54: `BEETS_LOG        = LOGS_DIR / "beets_import.log"`
- Line 140: `CUE_SUGGEST_WRITE_SIDECARS = False    # write .cues.json sidecar next to each audio file`
- Line 234: `ARTIST_ALIAS_STORE  = _INTEL_DIR / "artist_aliases.json"`
- Line 235: `ARTIST_REVIEW_QUEUE = _INTEL_DIR / "artist_review_queue.json"`
- Line 248: `# JSONL files under data/intelligence/ — one object per line.`
- Line 251: `AI_REVIEW_QUEUE      = _INTEL_DIR / "review_queue.jsonl"`
- Line 252: `AI_ACCEPTED_EXAMPLES = _INTEL_DIR / "accepted_examples.jsonl"`
- Line 253: `AI_REJECTED_EXAMPLES = _INTEL_DIR / "rejected_examples.jsonl"`
- Line 254: `AI_REVIEW_DECISIONS  = _INTEL_DIR / "review_decisions.jsonl"`
- Line 255: `AI_FEWSHOT_EXAMPLES  = _INTEL_DIR / "fewshot_examples.jsonl"`
- Line 280: `# Dataset JSONL files for the enrichment pipeline`
- Line 281: `AI_ENRICH_QUEUE    = _INTEL_DIR / "enrichment_queue.jsonl"`
- Line 282: `AI_ENRICH_ACCEPTED = _INTEL_DIR / "enrichment_accepted.jsonl"`
- Line 283: `AI_ENRICH_REJECTED = _INTEL_DIR / "enrichment_rejected.jsonl"`

## `intelligence/artist/artist_alias_store.py`
- Line 5: `Alias store  (data/intelligence/artist_aliases.json):`
- Line 15: `Review queue (data/intelligence/artist_review_queue.json):`
- Line 46: `log = logging.getLogger(__name__)`

## `intelligence/artist/runner.py`
- Line 41: `log = logging.getLogger(__name__)`
- Line 383: `level = _logging.DEBUG if getattr(args, "verbose", False) else _logging.INFO`
- Line 385: `_logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")`

## `intelligence/enrichment/deezer_lookup.py`
- Line 31: `log = logging.getLogger(__name__)`
- Line 93: `return _parse_tracks(resp.json())`

## `intelligence/enrichment/metadata_matcher.py`
- Line 91: `log = logging.getLogger(__name__)`

## `intelligence/enrichment/runner.py`
- Line 37: `enrichment_queue.jsonl    — one entry per processed file (all outcomes)`
- Line 38: `enrichment_accepted.jsonl — changes that were applied`
- Line 39: `enrichment_rejected.jsonl — changes that were skipped or below threshold`
- Line 69: `log = logging.getLogger(__name__)`
- Line 79: `# Lives alongside the other enrichment JSONL files under data/intelligence/.`
- Line 80: `_ENRICH_REVIEW_QUEUE: Path = config.AI_ENRICH_QUEUE.parent / "enrichment_review_queue.json"`
- Line 645: `"""Append one record to each relevant dataset JSONL file."""`
- Line 741: `tmp = queue_path.with_suffix(".json.tmp")`
- Line 861: `Loads enrichment_review_queue.json and either lists all items (--list-only)`
- Line 869: `_logging.basicConfig(level=_logging.INFO,`
- Line 969: `level = _logging.DEBUG if getattr(args, "verbose", False) else _logging.INFO`
- Line 971: `_logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")`
- Line 1303: `print(f"  Review queue    : {n_review}  (see enrichment_review_queue.json)")`

## `intelligence/enrichment/spotify_lookup.py`
- Line 39: `log = logging.getLogger(__name__)`
- Line 106: `body = resp.json()`
- Line 142: `return _parse_tracks(resp.json(), source="spotify")`
- Line 188: `return _parse_tracks(resp.json(), source="spotify")`

## `intelligence/enrichment/traxsource_lookup.py`
- Line 58: `log = logging.getLogger(__name__)`
- Line 373: `logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")`

## `intelligence/label/cleaner.py`
- Line 26: `log = logging.getLogger(__name__)`
- Line 29: `# Junk-label detection — loaded from config/junk_patterns.json`

## `intelligence/label/reports.py`
- Line 6: `label_clean_report.json    — full per-track details`
- Line 8: `label_clean_review.json    — only unresolved / low-confidence cases`
- Line 155: `p_review = output_dir / "label_clean_review.json"`
- Line 160: `p_json = output_dir / "label_clean_report.json"`

## `modules/analyze_missing.py`
- Line 33: `log = logging.getLogger(__name__)`
- Line 401: `log_path = log_dir / f"{ts}.log"`
- Line 434: `logging.getLogger().setLevel(logging.DEBUG)`

## `modules/analyzer.py`
- Line 33: `log = logging.getLogger(__name__)`

## `modules/artist_folder_clean.py`
- Line 52: `log = logging.getLogger(__name__)`
- Line 101: `# Source/promo-junk sets — loaded from config/junk_patterns.json.`
- Line 735: `report_path = report_dir / f"artist_folder_clean_{mode}.json"`

## `modules/artist_merge.py`
- Line 40: `log = logging.getLogger(__name__)`
- Line 710: `report_path = report_dir / f"artist_merge_{mode}.json"`

## `modules/artist_repair.py`
- Line 27: `Review queue : data/intelligence/artist_repair_queue.json`
- Line 28: `Log summary  : logs/artist-repair/<timestamp>_artist-repair_summary.json`
- Line 45: `log = logging.getLogger(__name__)`
- Line 182: `3. Keys and variants from artist_aliases.json`
- Line 570: `logging.basicConfig(level=logging.DEBUG)`
- Line 617: `"data/intelligence/artist_repair_queue.json")`
- Line 725: `summary_path = log_dir / f"{ts}_artist-repair_summary.json"`
- Line 782: `"data/intelligence/artist_repair_queue.json")`

## `modules/audit_quality.py`
- Line 46: `log = logging.getLogger(__name__)`
- Line 419: `json_path = report_dir / f"audit_quality_{timestamp}.json"`

## `modules/convert_audio.py`
- Line 40: `log = logging.getLogger(__name__)`
- Line 248: `path = log_dir / f"convert_{ts}.log"`
- Line 255: `"""Write line to log file (if open) and emit via module logger."""`
- Line 286: `logging.getLogger().setLevel(logging.DEBUG)`

## `modules/cue_suggest.py`
- Line 41: `CUE_SUGGEST_OUTPUT_DIR/cue_suggestions.json  — master, all tracks in DB`
- Line 44: `<audio_file>.cues.json                        — sidecar (opt-in via config)`
- Line 62: `log = logging.getLogger(__name__)`
- Line 770: `path = out_dir / "cue_suggestions.json"`
- Line 824: `sidecar = Path(tc.filepath).with_suffix(".cues.json")`

## `modules/dedupe.py`
- Line 23: `log = logging.getLogger(__name__)`
- Line 139: `db.log_duplicate(run_id, original, duplicate, reason="byte-identical")`

## `modules/doc_registry.py`
- Line 93: `"description": "Enable debug-level logging.",`
- Line 138: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 170: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 202: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 259: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 354: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 389: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 413: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 474: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 524: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 602: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 664: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 689: `"  logs/cue_suggest/cue_suggestions.json     (master, all tracks)\n"`
- Line 762: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 836: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 903: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`
- Line 916: `"  labels.json    full metadata\n"`
- Line 959: `{"flag": "--verbose / -v", "description": "Enable debug logging."},`

## `modules/filename_normalize.py`
- Line 34: `_ARTIST_REVIEW_QUEUE = Path(__file__).parent.parent / "data" / "review" / "artist_review_queue.jsonl"`

## `modules/harmonic.py`
- Line 64: `log = logging.getLogger(__name__)`
- Line 753: `path   = output_dir / f"harmonic_{stem}_{ts_str}.json"`

## `modules/junk_patterns.py`
- Line 6: `Loads config/junk_patterns.json once (cached at module level) and exposes`
- Line 29: `log = logging.getLogger(__name__)`
- Line 32: `_JSON_PATH = Path(__file__).parent.parent / "config" / "junk_patterns.json"`
- Line 41: `Compiled junk-detection data loaded from config/junk_patterns.json.`
- Line 139: `log.warning("junk_patterns.json: bad regex %r — %s", entry.get("regex"), exc)`
- Line 188: `log.warning("junk_patterns.json: failed to load (%s) — using fallback", exc)`
- Line 190: `log.warning("junk_patterns.json not found at %s — using fallback", _JSON_PATH)`

## `modules/library_dedupe.py`
- Line 44: `log = logging.getLogger(__name__)`

## `modules/metadata_clean.py`
- Line 37: `log = logging.getLogger(__name__)`

## `modules/metadata_sanitize.py`
- Line 29: `log = logging.getLogger(__name__)`
- Line 701: `logging.basicConfig(level=logging.DEBUG)`
- Line 965: `jsonl_path = Path(args.jsonl).expanduser().resolve()`
- Line 971: `print(f"ERROR: Log file does not exist: {jsonl_path}", file=sys.stderr)`

## `modules/organizer.py`
- Line 31: `log = logging.getLogger(__name__)`

## `modules/parser.py`
- Line 29: `log = logging.getLogger(__name__)`

## `modules/playlists.py`
- Line 32: `log = logging.getLogger(__name__)`

## `modules/qc.py`
- Line 18: `log = logging.getLogger(__name__)`

## `modules/rekordbox_export.py`
- Line 64: `log = logging.getLogger(__name__)`

## `modules/reporter.py`
- Line 22: `log = logging.getLogger(__name__)`

## `modules/run_logger.py`
- Line 2: `modules/run_logger.py`

## `modules/sanitizer.py`
- Line 26: `log = logging.getLogger(__name__)`
- Line 81: `# TLD list is loaded from config/junk_patterns.json at module init time.`
- Line 114: `# Promo / source phrase patterns — loaded from config/junk_patterns.json.`

## `modules/set_builder.py`
- Line 46: `log = logging.getLogger(__name__)`

## `modules/tag_normalize.py`
- Line 34: `log = logging.getLogger(__name__)`

## `modules/tagger.py`
- Line 28: `log = logging.getLogger(__name__)`

## `modules/textlog.py`
- Line 27: `log   = logging.getLogger(__name__)`

## `pipeline.py`
- Line 82: `level = logging.DEBUG if verbose else logging.INFO`
- Line 85: `logging.basicConfig(level=level, format=fmt, datefmt=datefmt)`
- Line 88: `fh = logging.FileHandler(config.LOGS_DIR / "pipeline.log", encoding="utf-8")`
- Line 89: `fh.setLevel(logging.DEBUG)`
- Line 90: `fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))`
- Line 91: `logging.getLogger().addHandler(fh)`
- Line 94: `log = logging.getLogger("pipeline")`
- Line 186: `config.BEETS_LOG         = config.LOGS_DIR  / "beets_import.log"`
- Line 465: `exporters.export_json(records,   output_dir / "labels.json")`
- Line 471: `log.info("  labels.json  — full metadata")`
- Line 520: `Loads labels.json (if it exists), merges in library metadata via`
- Line 521: `enrich_store_from_tracks(), then overwrites labels.json / labels.csv /`
- Line 543: `json_path  = output_dir / "labels.json"`
- Line 560: `log.info("No labels.json found — starting with an empty store")`
- Line 591: `exporters.export_json(records,   output_dir / "labels.json")`
- Line 1024: `in the database.  Optionally writes .cues.json sidecars per track.`
- Line 2343: `help="Show skipped and no-change files; enable debug logging.",`
- Line 2397: `help="Show already-correct files; enable debug logging.",`
- Line 2982: `"  logs/cue_suggest/cue_suggestions.json   (master, all tracks)\n"`
- Line 3310: `"  python3 pipeline.py metadata-sanitize --input /mnt/music_ssd/inbox --apply --output-json sanitize_log.json\n"`
- Line 3335: `help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/metadata-sanitize/",`
- Line 3365: `"  python3 pipeline.py metadata-sanitize-rollback --jsonl sanitize_log.json\n"`
- Line 3366: `"  python3 pipeline.py metadata-sanitize-rollback --jsonl sanitize_log.json --only-suspicious\n"`
- Line 3367: `"  python3 pipeline.py metadata-sanitize-rollback --jsonl sanitize_log.json --only-suspicious --apply\n"`
- Line 3450: `"Review queue : data/intelligence/artist_repair_queue.json\n"`
- Line 3485: `help="Enable debug logging.",`
- Line 3507: `"Queue file: data/intelligence/artist_repair_queue.json\n\n"`
- Line 3559: `"Alias store  : data/intelligence/artist_aliases.json\n"`
- Line 3560: `"Review queue : data/intelligence/artist_review_queue.json\n\n"`
- Line 3565: `"  python3 pipeline.py artist-intelligence --input /mnt/music_ssd/inbox --output-json preview.json\n"`
- Line 3599: `help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/artist-intelligence/",`
- Line 3631: `"  python3 pipeline.py ai-normalize --input ~/Music/inbox --output-json preview.json\n"`
- Line 3706: `help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/ai-normalize/",`
- Line 3723: `"Read data/intelligence/accepted_examples.jsonl, select a diverse subset\n"`
- Line 3724: `"of high-quality examples, and write data/intelligence/fewshot_examples.jsonl.\n\n"`
- Line 3766: `"      --output-json enrich_preview.json\n"`
- Line 3849: `help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/metadata-enrich-online/",`
- Line 3869: `"Queue file: data/intelligence/enrichment_review_queue.json\n\n"`
- Line 4053: `"accepted_examples.jsonl.",`

## `scripts/generate-docs.py`
- Line 49: `{"flag": "--verbose",   "description": "Enable debug logging."},`
- Line 89: `{"flag": "--verbose",             "description": "Enable debug logging."},`
- Line 125: `{"flag": "--verbose",   "description": "Enable debug logging."},`
- Line 164: `{"flag": "--verbose",             "description": "Enable debug logging."},`
- Line 195: `"Queue file: data/intelligence/enrichment_review_queue.json\n"`

## `tests/test_artist_intelligence.py`
- Line 186: `store_path = tmp_path / "aliases.json"`
- Line 233: `store_path = tmp_path / "empty.json"`
- Line 239: `store = ArtistAliasStore(tmp_path / "nonexistent.json")`

## `tests/test_artist_repair.py`
- Line 426: `q = tmp_path / "q.json"`
- Line 433: `assert _load_queue(tmp_path / "missing.json") == []`
- Line 436: `q = tmp_path / "q.json"`
- Line 442: `q = tmp_path / "q.json"`
- Line 449: `q = tmp_path / "q.json"`
- Line 461: `q = tmp_path / "q.json"`
- Line 470: `q = tmp_path / "q.json"`

## `tools/static_analysis/generate_repo_inventory.py`
- Line 38: `if rel.suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".md", ".txt", ".json"}:`
- Line 160: `".jsonl",`
- Line 161: `".json",`
- Line 162: `".log",`
- Line 163: `"logging.",`
- Line 164: `"logger.",`
- Line 165: `"Log file",`
- Line 166: `"Summary file",`
- Line 167: `"JSONL file",`

## `utils/prompt_logger.py`
- Line 2: `utils/prompt_logger.py — Timestamped prompt/response logger for LLM requests.`
- Line 115: `# Pipeline run logging — per-run .log, .jsonl, paged detail files, _summary.json`
- Line 125: `class _JsonlHandler(_logging.Handler):`
- Line 126: `"""Appends structured JSON log records to a .jsonl file."""`
- Line 133: `def emit(self, record: _logging.LogRecord) -> None:`
- Line 150: `Per-run observability: .log file, structured .jsonl, paged detail files,`
- Line 151: `and a _summary.json written on finish().`
- Line 165: `self.log_path     = self._run_dir / f"{self._prefix}.log"`
- Line 166: `self.jsonl_path   = self._run_dir / f"{self._prefix}.jsonl"`
- Line 167: `self.summary_path = self._run_dir / f"{self._prefix}_summary.json"`
- Line 179: `self._fh = _logging.FileHandler(self.log_path, encoding="utf-8")`
- Line 180: `self._fh.setLevel(_logging.DEBUG)`
- Line 181: `self._fh.setFormatter(_logging.Formatter(fmt, datefmt=datefmt))`
- Line 183: `self._jh = _JsonlHandler(self.jsonl_path, command)`
- Line 184: `self._jh.setLevel(_logging.DEBUG)`
- Line 185: `self._jh.setFormatter(_logging.Formatter("%(message)s"))`
- Line 187: `root = _logging.getLogger()`
- Line 229: `filename = f"{self._prefix}_{group}_{page_num:03d}.json"`
- Line 254: `"""Write a rich per-file event directly to the JSONL file."""`
- Line 263: `with open(self.jsonl_path, "a", encoding="utf-8") as f:`
- Line 275: `Flushes pages, writes _summary.json, detaches handlers, prints paths.`
- Line 346: `root = _logging.getLogger()`
- Line 360: `print(f"Log file    : {self.log_path}")`
- Line 361: `print(f"JSONL file  : {self.jsonl_path}")`
- Line 362: `print(f"Summary file: {self.summary_path}")`

