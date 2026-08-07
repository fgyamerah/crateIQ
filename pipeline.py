#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
CrateIQ — main pipeline entry point.

Usage:
    python3 pipeline.py [--dry-run] [--skip-beets] [--skip-analysis]
    python3 pipeline.py label-intel [--label-seeds PATH] [--label-output DIR]

Steps (in order):
    1. Init dirs + DB
    2. Collect inbox files
    3. QC check (ffprobe)
    4. Duplicate detection (rmlint)
    5. Organize (beets → fallback Python)
    6. BPM + key analysis (aubio + keyfinder-cli)
    7. Tag writing (mutagen)
    8. Mark tracks OK in DB
    9. Playlist generation (M3U + Rekordbox XML)
   10. Report

All steps are idempotent. Already-processed tracks (TXXX:PROCESSED=1 in tags
and status='ok' in DB) are skipped on subsequent runs.

Label Intelligence subcommand:
    python3 pipeline.py label-intel
        Scrape Beatport/Traxsource for every label in the seeds file and
        export results to JSON, CSV, TXT, and SQLite under the output dir.
        Seeds default: $DJ_MUSIC_ROOT/data/labels/seeds.txt
        Output default: $DJ_MUSIC_ROOT/data/labels/output/
        Cache default:  $DJ_MUSIC_ROOT/.cache/label_intel/
"""
import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make sure the djtoolkit directory is on the path
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

import config
import db
from modules import (
    qc, dedupe, organizer, sanitizer, analyzer, tagger, playlists, reporter,
    artist_merge, artist_folder_clean, metadata_clean, tag_normalize,
)
from modules.filename_parse import parse_filename_metadata
from modules.parser import is_valid_artist, is_valid_title
from modules.textlog import log_action, log_run_separator


# ---------------------------------------------------------------------------
# Virtualenv check
# ---------------------------------------------------------------------------
def _warn_if_no_venv() -> None:
    """
    Print a one-time warning if the script is running outside a virtualenv.
    Detection: sys.prefix != sys.base_prefix (set by venv/virtualenv) and
    VIRTUAL_ENV env var not set (set by activation scripts).
    Non-fatal — just advisory.
    """
    import os
    in_venv = (
        sys.prefix != sys.base_prefix
        or os.environ.get("VIRTUAL_ENV")
        or os.environ.get("CONDA_DEFAULT_ENV")
    )
    if not in_venv:
        print(
            "WARNING: Virtual environment does not appear to be active.\n"
            "  Recommended: source .venv/bin/activate\n"
            "  Or with direnv: direnv allow .\n",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
    # Also write to file
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(config.LOGS_DIR / "pipeline.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logging.getLogger().addHandler(fh)


log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Apply/dry-run command safety
# ---------------------------------------------------------------------------
def assert_apply_mode(args) -> bool:
    """
    Normalize write-capable subcommands to dry-run by default.

    Returns True only when writes are explicitly allowed. Raises ValueError
    when the requested mode is ambiguous or lacks confirmation.
    """
    command = getattr(args, "command", "command")
    do_apply = bool(getattr(args, "apply", False))
    explicit_dry_run = bool(getattr(args, "dry_run", False))

    if do_apply and explicit_dry_run:
        raise ValueError(f"{command}: --apply cannot be combined with --dry-run")

    if not do_apply:
        setattr(args, "dry_run", True)
        print("MODE: DRY-RUN")
        log_action(f"{command}: MODE DRY-RUN")
        return False

    confirmed = bool(getattr(args, "yes", False)) or bool(getattr(args, "force", False))
    if not confirmed:
        raise ValueError(f"{command}: --apply requires --yes or --force")

    setattr(args, "dry_run", False)
    print("MODE: APPLY")
    log_action(f"{command}: MODE APPLY")
    return True


def _apply_mode_or_error(args) -> bool | None:
    try:
        return assert_apply_mode(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Custom library path support
# ---------------------------------------------------------------------------

def _resolve_path(path_arg: str | None) -> Path | None:
    """
    Validate and return the user-supplied --path directory.
    Returns None when no --path was given (fall back to config defaults).
    Exits with an error message when the path does not exist.
    """
    if not path_arg:
        return None
    root = Path(path_arg).expanduser().resolve()
    if not root.exists():
        print(f"ERROR: --path directory does not exist: {root}", file=sys.stderr)
        sys.exit(2)
    if not root.is_dir():
        print(f"ERROR: --path is not a directory: {root}", file=sys.stderr)
        sys.exit(2)
    return root


def _resolve_library_root(scan_path: Path) -> Path:
    """
    Derive the library system root from a scan path.

    When the leaf directory is named 'sorted' (case-insensitive) the root is
    its parent — matching the convention <lib_root>/sorted.
    Otherwise the scan path itself is the root.

    Examples:
        /mnt/music_ssd/KKDJ/sorted → /mnt/music_ssd/KKDJ
        /home/user/Music/inbox     → /home/user/Music/inbox
    """
    if scan_path.name.lower() == "sorted":
        return scan_path.parent
    return scan_path


def resolve_library_root(args=None) -> Path:
    """Resolve the active library root for root-scoped commands."""
    root_arg = getattr(args, "root", None) if args is not None else None
    root = Path(root_arg).expanduser() if root_arg else Path(config.MUSIC_ROOT).expanduser()
    root = root.resolve()
    if not root.exists():
        raise ValueError(f"library root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"library root is not a directory: {root}")
    if not root.is_absolute():
        raise ValueError(f"library root must be absolute: {root}")
    return root


def assert_path_under_root(path: Path | str, root: Path | str) -> Path:
    """
    Resolve path and verify it stays under root.

    Relative paths are interpreted relative to root so benign relative DB paths
    can be audited, while '../' traversal resolves outside root and is rejected.
    """
    root_path = Path(root).expanduser().resolve()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root_path / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"path outside selected root: {resolved} not under {root_path}") from exc
    return resolved


def _collect_audio_from_dir(root: Path) -> list:
    """Return all audio files under root, excluding maintenance/quarantine directories."""
    skip = config.MAINTENANCE_SKIP_DIRS
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(root.rglob(f"*{ext}"))
        files.extend(root.rglob(f"*{ext.upper()}"))
    seen: set = set()
    result = []
    for f in sorted(files):
        key = str(f)
        if key in seen:
            continue
        # skip exact maintenance dir names
        if any(part in skip for part in f.parts):
            continue
        # skip hidden directories (any path component starting with ".")
        if any(part.startswith(".") for part in f.parts):
            continue
        seen.add(key)
        result.append(f)
    return result


def _override_music_root(root: Path) -> None:
    """
    Override every config path that is derived from MUSIC_ROOT.
    Called when --path is passed to the main pipeline run so that all
    modules (organizer, analyzer, tagger, playlists …) use the custom root.
    """
    config.MUSIC_ROOT        = root
    config.INBOX             = root / "inbox"
    config.PROCESSING        = root / "processing"
    config.LIBRARY           = root / "library"
    config.SORTED            = config.LIBRARY / "sorted"
    config.UNSORTED          = config.SORTED  / "_unsorted"
    config.COMPILATIONS      = config.SORTED  / "_compilations"
    config.DUPLICATES        = root / "duplicates"
    config.REJECTED          = root / "rejected"
    config.PLAYLISTS         = root / "playlists"
    config.M3U_DIR           = config.PLAYLISTS / "m3u"
    config.GENRE_M3U_DIR     = config.M3U_DIR   / "Genre"
    config.ENERGY_M3U_DIR    = config.M3U_DIR   / "Energy"
    config.COMBINED_M3U_DIR  = config.M3U_DIR   / "Combined"
    config.KEY_M3U_DIR       = config.M3U_DIR   / "Key"
    config.ROUTE_M3U_DIR     = config.M3U_DIR   / "Route"
    config.XML_DIR           = config.PLAYLISTS / "xml"
    config.LOGS_DIR          = root / "logs"
    config.DB_PATH           = config.LOGS_DIR  / "processed.db"
    config.REPORTS_DIR       = config.LOGS_DIR  / "reports"
    config.BEETS_LOG         = config.LOGS_DIR  / "beets_import.log"
    config.TEXT_LOG_PATH     = config.LOGS_DIR  / "processing_log.txt"
    config.README_PATH       = config.LOGS_DIR  / "README.md"
    config.LABEL_INTEL_SEEDS             = root / "data" / "labels" / "seeds.txt"
    config.LABEL_INTEL_OUTPUT            = root / "data" / "labels" / "output"
    config.LABEL_INTEL_CACHE             = root / ".cache" / "label_intel"
    config.LABEL_CLEAN_OUTPUT            = root / "data" / "labels" / "clean"
    config.METADATA_CLEAN_REPORT_DIR      = config.LOGS_DIR / "metadata_clean"
    config.ARTIST_MERGE_REPORT_DIR        = config.LOGS_DIR / "artist_merge"
    config.ARTIST_FOLDER_CLEAN_REPORT_DIR = config.LOGS_DIR / "artist_folder_clean"
    config.DEDUPE_QUARANTINE_DIR          = config.SORTED / "_duplicates"


def _log_active_path(label: str, path: Path) -> None:
    """Emit a consistent INFO line and textlog entry for the active library path."""
    log.info("Using library path: %s", path)
    log_action(f"{label} — library path: {path}")


# ---------------------------------------------------------------------------
# Directory initialization
# ---------------------------------------------------------------------------
def _init_dirs() -> None:
    for d in [
        config.INBOX,
        config.PROCESSING,
        config.SORTED,
        config.UNSORTED,
        config.COMPILATIONS,
        config.DUPLICATES,
        config.REJECTED,
        config.M3U_DIR,
        config.GENRE_M3U_DIR,
        config.ENERGY_M3U_DIR,
        config.COMBINED_M3U_DIR,
        config.KEY_M3U_DIR,
        config.ROUTE_M3U_DIR,
        config.XML_DIR,
        config.LOGS_DIR,
        config.REPORTS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------
def _collect_library_for_reanalysis() -> list:
    """Return all audio files in SORTED that are missing BPM or key."""
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(config.SORTED.rglob(f"*{ext}"))
        files.extend(config.SORTED.rglob(f"*{ext.upper()}"))
    seen = set()
    result = []
    for f in sorted(files):
        if str(f) in seen:
            continue
        seen.add(str(f))
        row = db.get_track(str(f))
        # Include if missing BPM, key, or not yet processed
        if row is None or row["bpm"] is None or row["key_camelot"] is None:
            if row is None:
                db.upsert_track(str(f), status="pending")
            result.append(f)
    return result


def _collect_inbox() -> list:
    """Return all audio files in INBOX (recursive). Skip already-processed."""
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(config.INBOX.rglob(f"*{ext}"))
        files.extend(config.INBOX.rglob(f"*{ext.upper()}"))
    # Deduplicate (rglob can match same file twice on case-insensitive FS)
    seen = set()
    unique = []
    for f in sorted(files):
        if str(f) not in seen:
            seen.add(str(f))
            unique.append(f)
    return unique


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(dry_run: bool, skip_beets: bool, skip_analysis: bool, verbose: bool,
                 reanalyze: bool = False, custom_path: Path | None = None,
                 skip_cue_suggest: bool = True) -> int:
    """
    Execute the full pipeline.
    Returns exit code: 0 = success, 1 = some files failed, 2 = fatal error.
    """
    t_start = time.monotonic()

    if custom_path is not None:
        _override_music_root(custom_path)

    _setup_logging(verbose)
    _log_active_path("PIPELINE", config.MUSIC_ROOT)
    _init_dirs()
    db.init_db()

    run_id = db.start_run(dry_run)
    log.info("Pipeline start (run_id=%d, dry_run=%s)", run_id, dry_run)
    log_run_separator(f"run_id={run_id}" + (" DRY-RUN" if dry_run else ""))

    # --- Step 1: Collect files ---
    if reanalyze:
        # Re-analyze all tracks in sorted library that are missing BPM or key
        inbox_files = _collect_library_for_reanalysis()
        if not inbox_files:
            log.info("No tracks need re-analysis")
            db.finish_run(run_id, inbox_count=0, processed=0, duration_sec=0.0)
            return 0
        log.info("Re-analysis mode: %d tracks to process", len(inbox_files))
    else:
        inbox_files = _collect_inbox()
        if not inbox_files:
            log.info("Inbox is empty — nothing to process")
            db.finish_run(run_id, inbox_count=0, processed=0, duration_sec=0.0)
            return 0

    log.info("Inbox: %d files found", len(inbox_files))
    db.finish_run(run_id, inbox_count=len(inbox_files))

    # Register all inbox files in DB as 'pending' and log each one
    for f in inbox_files:
        if not db.is_processed(str(f)):
            db.upsert_track(str(f), status="pending")
        log_action(f"PROCESS: {f.name}")

    # --- Step 2: QC ---
    log.info("[1/7] Quality control ...")
    files = qc.run(inbox_files, run_id, dry_run)
    rejected_count = len(inbox_files) - len(files)

    # --- Step 3: Deduplicate ---
    log.info("[2/7] Duplicate detection ...")
    files = dedupe.run(files, run_id, dry_run)
    dupe_count = (len(inbox_files) - rejected_count) - len(files)

    # --- Step 4: Organize ---
    log.info("[3/7] Organizing library ...")
    files = organizer.run(files, run_id, dry_run, use_beets=not skip_beets)

    # --- Label enrichment (optional post-pipeline step) ---
    # Run separately:  python pipeline.py --label-enrich-from-library

    # --- Step 5: Sanitize tags ---
    log.info("[4/7] Sanitizing tags ...")
    files = sanitizer.run(files, run_id, dry_run)

    # --- Step 6: BPM + key analysis ---
    if not skip_analysis:
        log.info("[5/8] BPM + key analysis ...")
        files = analyzer.run(files, run_id, dry_run)
    else:
        log.info("[5/8] Skipping analysis (--skip-analysis)")

    # --- Step 7: Write tags ---
    log.info("[6/8] Writing tags ...")
    files = tagger.run(files, run_id, dry_run)

    # --- Step 8: Mark as OK in DB ---
    processed_count = 0
    error_count     = 0
    for f in files:
        row = db.get_track(str(f))
        if row and row["status"] not in ("rejected", "duplicate", "needs_review"):
            db.mark_status(str(f), "ok")
            processed_count += 1
        elif row and row["status"] == "error":
            error_count += 1

    # --- Step 8b: Cue point suggestion (disabled by default; use --force-cue-suggest) ---
    # MIK-first policy: cue data is owned by Mixed In Key / Rekordbox.
    # The toolkit will not generate or overwrite cues unless explicitly forced.
    if not skip_cue_suggest and not skip_analysis and files:
        log.info("[7/8] Cue point suggestion ...")
        try:
            from modules import cue_suggest as _cue_suggest
            min_conf = getattr(config, "CUE_SUGGEST_MIN_CONFIDENCE", 0.4)
            _analysed, _stored = _cue_suggest.run(
                [Path(f) if not isinstance(f, Path) else f for f in files],
                dry_run  = dry_run,
                min_conf = min_conf,
            )
            log.info("Cue suggest: %d analysed, %d stored", _analysed, _stored)
        except Exception as exc:
            log.warning("Cue suggest step failed (non-fatal): %s", exc)
    else:
        log.info(
            "[7/8] Cue point suggestion skipped "
            "(disabled by default — use --force-cue-suggest to enable)"
        )

    # --- Step 8c: Playlist generation ---
    log.info("[8/8] Generating playlists ...")
    playlists.run(files, run_id, dry_run)

    # --- Step 9: Report ---
    t_end       = time.monotonic()
    duration    = t_end - t_start
    unsorted    = db.get_tracks_by_status("needs_review")

    db.finish_run(
        run_id,
        inbox_count=len(inbox_files),
        processed=processed_count,
        rejected=rejected_count,
        duplicates=dupe_count,
        unsorted=len(unsorted),
        errors=error_count,
        duration_sec=duration,
    )

    log.info("[9/9] Writing report ...")
    report_path = reporter.generate(run_id, duration, dry_run)
    reporter.generate_readme(run_id, duration, dry_run)
    reporter.print_summary(run_id, duration)
    log.info("Report: %s", report_path)
    log_action(f"RUN COMPLETE: run_id={run_id}, processed={processed_count}, errors={error_count}, duration={duration:.1f}s")

    return 0 if error_count == 0 else 1


# ---------------------------------------------------------------------------
# Label Intelligence
# ---------------------------------------------------------------------------
def run_label_intel(args) -> int:
    """Scrape label metadata and export to all formats."""
    _setup_logging(getattr(args, "verbose", False))

    seeds_path  = Path(args.label_seeds)
    output_dir  = Path(args.label_output)
    cache_dir   = Path(args.label_cache)
    sources     = args.label_sources
    delay       = float(args.label_delay)
    skip_enrich = args.label_skip_enrich

    if not seeds_path.exists():
        log.error("Seeds file not found: %s", seeds_path)
        log.error(
            "Create it with one label name per line, for example:\n"
            "  MoBlack Records\n"
            "  Defected Records\n"
            "  Drumcode"
        )
        return 2

    try:
        from intelligence.label.scraper import scrape_labels
        from intelligence.label import exporters
    except ImportError as exc:
        log.error("intelligence.label package not found (%s). "
                  "Ensure intelligence/label/ is at the project root.", exc)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    log_action("LABEL-INTEL START")
    log.info("Seeds:   %s", seeds_path)
    log.info("Output:  %s", output_dir)
    log.info("Cache:   %s", cache_dir)
    log.info("Sources: %s  |  delay: %.1fs  |  skip_enrich: %s",
             sources, delay, skip_enrich)

    store = scrape_labels(
        seed_path=seeds_path,
        cache_dir=cache_dir,
        source_names=sources,
        delay=delay,
        skip_enrich=skip_enrich,
    )

    records = store.values()
    log.info("Scraped %d label record(s)", len(records))

    exporters.export_json(records,   output_dir / "labels.json")
    exporters.export_csv(records,    output_dir / "labels.csv")
    exporters.export_txt(records,    output_dir / "labels.txt")
    exporters.export_sqlite(records, output_dir / "labels.db")

    log.info("Exported to %s:", output_dir)
    log.info("  labels.json  — full metadata")
    log.info("  labels.csv   — spreadsheet-friendly")
    log.info("  labels.txt   — one name per line  "
             "(copy to known_labels.txt to update parser blocklist)")
    log.info("  labels.db    — SQLite for ad-hoc queries")
    log_action(f"LABEL-INTEL DONE: {len(records)} records → {output_dir}")
    return 0


# ---------------------------------------------------------------------------
# Label Enrichment from Library
# ---------------------------------------------------------------------------
def _collect_library_tracks_for_enrichment() -> list:
    """
    Return [{label, bpm, genre}] for every OK track in the library.

    Reads genre + bpm from the pipeline DB (already stored there after the
    analyze/tag steps) and recovers the record-label name from the audio
    file's 'organization' easy-tag (mutagen → TPUB for ID3, ORGANIZATION
    for Vorbis).  No BPM/key re-analysis is performed.
    """
    from mutagen import File as MFile

    rows   = db.get_all_ok_tracks()
    tracks = []
    for row in rows:
        fpath = row["filepath"]
        try:
            audio = MFile(fpath, easy=True)
            if audio is None:
                continue
            label = (audio.get("organization") or [""])[0].strip()
            if not label:
                continue
        except Exception:
            continue

        tracks.append({
            "label": label,
            "bpm":   row["bpm"],
            "genre": row["genre"] or "",
        })
    return tracks


def run_label_enrichment_from_library(verbose: bool = False) -> int:
    """
    Enrich the label database with real BPM/genre data from the local library.

    Loads labels.json (if it exists), merges in library metadata via
    enrich_store_from_tracks(), then overwrites labels.json / labels.csv /
    labels.db.  Only improves bpm_min/max, genres, subgenres, energy_profile
    and creates new label records for labels not seen before.
    """
    _setup_logging(verbose)

    try:
        from intelligence.label.enrich_from_library import enrich_store_from_tracks
        from intelligence.label.store import LabelStore
        from intelligence.label.models import LabelRecord
        from intelligence.label import exporters
        from intelligence.label.utils import normalize_label_name
    except ImportError as exc:
        log.error("intelligence.label package not found (%s). "
                  "Ensure intelligence/label/ is at the project root.", exc)
        return 2

    import json as _json
    import dataclasses

    db.init_db()
    output_dir = config.LABEL_INTEL_OUTPUT
    json_path  = output_dir / "labels.json"

    # --- Load existing store ---
    store = LabelStore()
    if json_path.exists():
        raw          = _json.loads(json_path.read_text(encoding="utf-8"))
        valid_fields = {f.name for f in dataclasses.fields(LabelRecord)}
        loaded       = 0
        for item in raw:
            try:
                rec = LabelRecord(**{k: v for k, v in item.items() if k in valid_fields})
                store.records[rec.normalized_name] = rec
                loaded += 1
            except Exception as exc:
                log.debug("Skipped malformed label record: %s", exc)
        log.info("Loaded %d existing label record(s) from %s", loaded, json_path)
    else:
        log.info("No labels.json found — starting with an empty store")

    # --- Collect tracks ---
    tracks = _collect_library_tracks_for_enrichment()
    log.info("Collected %d track(s) with label metadata from library", len(tracks))

    if not tracks:
        log.warning(
            "No labelled tracks found in the library database.\n"
            "Tip: run the full pipeline first so tracks are organised and "
            "their tags are stored (status='ok')."
        )
        return 0

    # --- Snapshot keys for summary counts ---
    before_keys  = set(store.records.keys())
    matched_keys = {
        normalize_label_name(t["label"]) for t in tracks if t.get("label")
    }
    n_will_enrich = len(before_keys & matched_keys)

    log_action("LABEL-ENRICH-LIBRARY START")
    enrich_store_from_tracks(store, tracks)

    after_keys = set(store.records.keys())
    n_new      = len(after_keys - before_keys)
    total      = len(store.records)

    # --- Re-export (TXT intentionally omitted here; use label-intel for a fresh scrape) ---
    output_dir.mkdir(parents=True, exist_ok=True)
    records = store.values()
    exporters.export_json(records,   output_dir / "labels.json")
    exporters.export_csv(records,    output_dir / "labels.csv")
    exporters.export_sqlite(records, output_dir / "labels.db")

    log.info("Label enrichment from library complete:")
    log.info("  %d new label(s) discovered from library", n_new)
    log.info("  %d existing label(s) enriched (bpm / genres / energy)", n_will_enrich)
    log.info("  %d total label(s) in database", total)
    log.info("  Exported to: %s", output_dir)
    log_action(
        f"LABEL-ENRICH-LIBRARY DONE: {n_new} new, {n_will_enrich} enriched → {output_dir}"
    )
    return 0


# ---------------------------------------------------------------------------
# Artist Merge
# ---------------------------------------------------------------------------
def run_artist_merge(args) -> int:
    """
    Scan the sorted library for artist folder variants and merge them.

    Modes:
      --dry-run   scan + report JSON, no file moves (default when neither flag given)
      --apply     apply safe merges; uncertain cases go to report only
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    sorted_root = custom_path if custom_path is not None else config.SORTED
    report_dir  = config.ARTIST_MERGE_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    _log_active_path("ARTIST-MERGE", sorted_root)

    do_apply = getattr(args, "apply", False)

    if do_apply:
        log_action("ARTIST-MERGE APPLY START")
        artist_merge.run_apply(sorted_root, report_dir)
        log_action("ARTIST-MERGE APPLY DONE")
    else:
        log_action("ARTIST-MERGE DRY-RUN START")
        artist_merge.run_dry_run(sorted_root, report_dir)
        log_action("ARTIST-MERGE DRY-RUN DONE")

    return 0


# ---------------------------------------------------------------------------
# Artist Folder Clean
# ---------------------------------------------------------------------------
def run_artist_folder_clean(args) -> int:
    """
    Scan the sorted library for artist folders with bad names (Camelot key
    prefixes, bracket watermarks, URL/domain names, symbol garbage) and fix
    them retroactively.

    Modes:
      --dry-run   scan + report JSON, no file moves (default when neither flag given)
      --apply     apply all recoverable renames/merges; review cases go to report only
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    sorted_root = custom_path if custom_path is not None else config.SORTED
    report_dir  = config.ARTIST_FOLDER_CLEAN_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    _log_active_path("FOLDER-CLEAN", sorted_root)

    do_apply = getattr(args, "apply", False)

    if do_apply:
        log_action("FOLDER-CLEAN APPLY START")
        rc = artist_folder_clean.run_apply(sorted_root, report_dir)
        log_action("FOLDER-CLEAN APPLY DONE")
    else:
        log_action("FOLDER-CLEAN DRY-RUN START")
        rc = artist_folder_clean.run_dry_run(sorted_root, report_dir)
        log_action("FOLDER-CLEAN DRY-RUN DONE")

    return rc


# ---------------------------------------------------------------------------
# Label Clean
# ---------------------------------------------------------------------------
def run_label_clean(args) -> int:
    """
    Detect, normalize, and (optionally) write back label metadata.

    Modes:
      default / --dry-run   scan + report, no file writes
      --write-tags          scan + report + write high-confidence labels
      --review-only         scan + export only unresolved / low-confidence cases
    """
    _setup_logging(getattr(args, "verbose", False))

    try:
        from intelligence.label.cleaner import (
            scan_tracks, write_label_tag, WRITE_THRESHOLD,
        )
        from intelligence.label.normalizer import AliasRegistry
        from intelligence.label import reports as _reports
    except ImportError as exc:
        log.error("intelligence.label package not found (%s). "
                  "Ensure intelligence/label/ is at the project root.", exc)
        return 2

    # Provider placeholder warnings
    if getattr(args, "use_discogs", False):
        log.warning("--use-discogs: Discogs provider is not yet implemented (Phase 2) — skipped.")
    if getattr(args, "use_beatport", False):
        log.warning("--use-beatport: Beatport clean provider is not yet implemented (Phase 2) — skipped.")

    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))

    if custom_path is not None:
        _log_active_path("LABEL-CLEAN", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("LABEL-CLEAN", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    threshold   = getattr(args, "confidence_threshold", config.LABEL_CLEAN_THRESHOLD)
    do_write    = getattr(args, "write_tags", False) and not getattr(args, "dry_run", False)
    review_only = getattr(args, "review_only", False)
    output_dir  = config.LABEL_CLEAN_OUTPUT

    log.info("Scanning %d track(s) for label metadata ...", len(paths))
    log.info("Confidence threshold : %.2f   write-back: %s   review-only: %s",
             threshold, do_write, review_only)
    log_action("LABEL-CLEAN START")

    alias_registry = AliasRegistry()
    results = scan_tracks(paths, write_threshold=threshold, alias_registry=alias_registry)

    # --- Write-back ---
    written = 0
    if do_write:
        for r in results:
            if r.writable and r.cleaned_label:
                if write_label_tag(Path(r.filepath), r.cleaned_label):
                    r.action_taken = "written"
                    written += 1
                    log.info("WROTE label %r → %s", r.cleaned_label, Path(r.filepath).name)

    # --- Reports ---
    report_paths = _reports.generate_all(
        results, output_dir, written=written, review_only=review_only,
    )
    _reports.print_summary(results, written)

    log.info("Reports written to: %s", output_dir)
    for label, rpath in report_paths.items():
        log.info("  %-15s %s", label, rpath.name)

    alias_merges = alias_registry.alias_count()
    if alias_merges:
        log.info("Alias merges detected: %d label(s) have multiple spellings", alias_merges)

    log_action(
        f"LABEL-CLEAN DONE: {len(results)} scanned, {written} written, "
        f"{alias_merges} alias merges → {output_dir}"
    )
    return 0


# ---------------------------------------------------------------------------
# Dedupe library
# ---------------------------------------------------------------------------
def run_dedupe(args) -> int:
    """
    Scan the sorted library (or a custom path) for duplicate audio files and
    optionally quarantine them.

    Modes:
      --dry-run   scan + preview groups, no files moved
      (no flag)   scan + quarantine duplicates

    Detection:
      Case A — exact hash match       → safe to quarantine automatically
      Case B — same title, lower quality → quarantine lower-quality copy
      Case C — different versions     → reported only, never removed
    """
    from modules import library_dedupe

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path    = _resolve_path(getattr(args, "path", None))
    quarantine_raw = getattr(args, "quarantine_dir", None)

    # Derive quarantine under the selected library root, not the global config default.
    # Explicit --quarantine-dir always wins; otherwise derive from the scan path.
    if quarantine_raw:
        quarantine_dir = Path(quarantine_raw)
    elif custom_path is not None:
        quarantine_dir = _resolve_library_root(custom_path) / ".BIN" / "QUARANTINE"
    else:
        quarantine_dir = config.DEDUPE_QUARANTINE_DIR

    if custom_path is not None:
        _log_active_path("DEDUPE", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("DEDUPE", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    do_apply = getattr(args, "apply", False)
    dry_run  = not do_apply

    source_root = custom_path if custom_path is not None else Path(config.SORTED)

    print(f"  Quarantine : {quarantine_dir}")
    log.info(
        "Dedupe: %d track(s) to scan  dry_run=%s  quarantine=%s",
        len(paths), dry_run, quarantine_dir,
    )

    scanned, groups, quarantined, bytes_freed = library_dedupe.run(
        paths          = paths,
        dry_run        = dry_run,
        quarantine_dir = quarantine_dir,
        source_root    = source_root,
    )

    return 0


# ---------------------------------------------------------------------------
# Orphan scan
# ---------------------------------------------------------------------------
def run_orphan_scan(args) -> int:
    """
    Detect two categories of orphan records:

      stale_db_rows   — DB rows (non-stale) whose file no longer exists on disk
      untracked_files — audio files on disk that have no DB row

    Preview by default; use --apply to write stale status to the DB.
    Untracked files are always reported only (never auto-added or deleted).
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    lib_root = _resolve_path(getattr(args, "path", None)) or Path(config.SORTED)
    do_apply = getattr(args, "apply", False)
    no_untracked = getattr(args, "no_untracked", False)
    verbose_list = getattr(args, "verbose_list", False)

    _log_active_path("ORPHAN-SCAN", lib_root)

    stale_rows, untracked = db.scan_orphans(
        lib_root,
        include_untracked=not no_untracked,
        scan_root=lib_root,
    )

    print(f"\n=== orphan-scan {'APPLY' if do_apply else 'PREVIEW'} ===\n")
    print(f"  Scan root       : {lib_root}")
    print(f"  stale_db_rows   (DB record, file missing) : {len(stale_rows)}")
    print(f"  untracked_files (file on disk, no DB row) : {len(untracked)}")

    if stale_rows:
        print("\n── Stale DB rows ────────────────────────────────────────")
        for fp in sorted(stale_rows):
            print(f"  STALE  {fp}")
        if do_apply:
            with db.get_conn() as conn:
                conn.executemany(
                    "UPDATE tracks SET status='stale', error_msg=? WHERE filepath=?",
                    [("orphan-scan: file not found on disk", fp) for fp in stale_rows],
                )
            print(f"\n  Marked {len(stale_rows)} row(s) as stale in DB.")
        else:
            print(f"\n  Run with --apply to mark {len(stale_rows)} row(s) as stale.")

    if untracked:
        print("\n── Untracked files ──────────────────────────────────────")

        from collections import defaultdict as _dd
        folder_counts: dict = _dd(int)
        ext_counts: dict = _dd(int)
        for _p in untracked:
            try:
                _rel = _p.relative_to(lib_root)
                _top = _rel.parts[0] if len(_rel.parts) > 1 else "(root)"
            except ValueError:
                _top = "(other)"
            folder_counts[_top] += 1
            ext_counts[_p.suffix.lower()] += 1

        print("\n  By folder:")
        _fcol = max((len(k) for k in folder_counts), default=6)
        for _folder, _count in sorted(folder_counts.items(), key=lambda x: -x[1]):
            print(f"    {_folder:<{_fcol}}  : {_count}")

        print("\n  By extension:")
        _ecol = max((len(k) for k in ext_counts), default=4)
        for _ext, _count in sorted(ext_counts.items(), key=lambda x: -x[1]):
            print(f"    {_ext:<{_ecol}}  : {_count}")

        if len(untracked) > 1000:
            print(f"\n  WARNING: {len(untracked):,} untracked files — large count.")
            if not verbose_list:
                print("  Run with --verbose-list to print all paths.")

        if verbose_list:
            print()
            for _p in sorted(untracked):
                print(f"  UNTRACKED  {_p}")

        print(f"\n  {len(untracked):,} untracked file(s) — review manually.")
        print("  (Run the main pipeline or 'metadata-clean' to ingest them.)")

    if not stale_rows and not untracked:
        print("  No orphans found.")

    print()
    from modules.textlog import log_action
    log_action(
        f"ORPHAN-SCAN {'APPLY' if do_apply else 'PREVIEW'}: "
        f"{len(stale_rows)} stale_db_rows, {len(untracked)} untracked_files"
    )
    return 0


# ---------------------------------------------------------------------------
# Read-only path audit
# ---------------------------------------------------------------------------
def _path_audit_audio_files(root: Path) -> list[Path]:
    skip = config.MAINTENANCE_SKIP_DIRS
    files: list[Path] = []
    seen: set[str] = set()
    for ext in config.AUDIO_EXTENSIONS:
        for pattern in (f"*{ext}", f"*{ext.upper()}"):
            for path in root.rglob(pattern):
                if any(part in skip for part in path.parts):
                    continue
                if any(part.startswith(".") for part in path.parts):
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                files.append(path.resolve())
    return sorted(files)


def _path_audit_db_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "processed.db", root)


def _path_audit_table_columns(conn, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


_PATH_AUDIT_STAGE_PRIORITY = {
    "metadata-sanitize": 0,
    "artist-intelligence": 1,
    "metadata-enrich-online": 2,
    "filename-normalize": 3,
    "library-organize": 4,
}


_BUILD_TRACKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath        TEXT    NOT NULL UNIQUE,
    filename        TEXT    NOT NULL,
    artist          TEXT,
    title           TEXT,
    album           TEXT,
    genre           TEXT,
    bpm             REAL,
    key_musical     TEXT,
    key_camelot     TEXT,
    duration_sec    REAL,
    bitrate_kbps    INTEGER,
    filesize_bytes  INTEGER,
    status          TEXT    NOT NULL DEFAULT 'pending',
    error_msg       TEXT,
    processed_at    TEXT,
    pipeline_ver    TEXT,
    parse_confidence TEXT
);
"""


def _build_tracks_ensure_schema(conn) -> None:
    conn.execute(_BUILD_TRACKS_SCHEMA)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_filepath ON tracks(filepath)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_artist_lc ON tracks(LOWER(COALESCE(artist,'')))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_title_lc ON tracks(LOWER(COALESCE(title,'')))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_genre_lc ON tracks(LOWER(COALESCE(genre,'')))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_parse_confidence_lc ON tracks(UPPER(COALESCE(parse_confidence,'')))")


def _build_tracks_source_rows(conn) -> list[dict]:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_state'"
    ).fetchone()
    if table is None:
        return []
    columns = _path_audit_table_columns(conn, "processed_state")
    path_col = "filepath" if "filepath" in columns else "path" if "path" in columns else None
    if path_col is None:
        return []
    select_cols = [
        "id",
        "stage",
        f"{path_col} AS filepath",
        "file_size" if "file_size" in columns else "NULL AS file_size",
        "status" if "status" in columns else "NULL AS status",
        "processed_at" if "processed_at" in columns else "NULL AS processed_at",
        "pipeline_ver" if "pipeline_ver" in columns else "NULL AS pipeline_ver",
    ]
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT {', '.join(select_cols)} FROM processed_state"
        ).fetchall()
    ]


def _build_tracks_row_rank(row: dict) -> tuple[int, str, int]:
    stage = str(row.get("stage") or "")
    return (
        _PATH_AUDIT_STAGE_PRIORITY.get(stage, -1),
        str(row.get("processed_at") or ""),
        int(row.get("id") or 0),
    )


def _build_tracks_select_sources(root: Path, rows: list[dict]) -> tuple[dict[str, dict], dict]:
    stats = {
        "source_rows": len(rows),
        "skipped_stale": 0,
        "skipped_missing_file": 0,
        "skipped_outside_root": 0,
        "duplicate_filepaths_collapsed": 0,
    }
    selected: dict[str, dict] = {}
    seen_valid_paths = 0
    for row in rows:
        if str(row.get("status") or "").lower() == "stale":
            stats["skipped_stale"] += 1
            continue
        raw_path = str(row.get("filepath") or "")
        if not raw_path:
            stats["skipped_missing_file"] += 1
            continue
        try:
            filepath = assert_path_under_root(raw_path, root)
        except ValueError:
            stats["skipped_outside_root"] += 1
            continue
        if not filepath.exists():
            stats["skipped_missing_file"] += 1
            continue
        seen_valid_paths += 1
        key = str(filepath)
        candidate = dict(row)
        candidate["filepath"] = key
        current = selected.get(key)
        if current is None or _build_tracks_row_rank(candidate) > _build_tracks_row_rank(current):
            selected[key] = candidate
    stats["duplicate_filepaths_collapsed"] = max(0, seen_valid_paths - len(selected))
    return selected, stats


def _build_tracks_upsert(conn, root: Path) -> dict:
    _build_tracks_ensure_schema(conn)
    rows = _build_tracks_source_rows(conn)
    selected, stats = _build_tracks_select_sources(root, rows)
    track_columns = set(_path_audit_table_columns(conn, "tracks"))
    writable_columns = [
        column for column in [
            "filepath",
            "filename",
            "filesize_bytes",
            "status",
            "processed_at",
            "pipeline_ver",
        ]
        if column in track_columns
    ]
    inserted = 0
    updated = 0
    unchanged = 0
    for filepath, row in sorted(selected.items()):
        path = Path(filepath)
        values = {
            "filepath": filepath,
            "filename": path.name,
            "filesize_bytes": path.stat().st_size,
            "status": row.get("status") or "ok",
            "processed_at": row.get("processed_at"),
            "pipeline_ver": row.get("pipeline_ver"),
        }
        values = {key: values[key] for key in writable_columns}
        existing = conn.execute(
            "SELECT * FROM tracks WHERE filepath = ?",
            (filepath,),
        ).fetchone()
        if existing is not None:
            changed = any(existing[column] != values.get(column) for column in writable_columns)
            if not changed:
                unchanged += 1
                continue
        columns = list(values.keys())
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column}=excluded.{column}" for column in columns if column != "filepath"
        )
        conn.execute(
            f"INSERT INTO tracks ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(filepath) DO UPDATE SET {updates}",
            [values[column] for column in columns],
        )
        if existing is None:
            inserted += 1
        else:
            updated += 1
    final_tracks_count = conn.execute("SELECT COUNT(*) AS n FROM tracks").fetchone()["n"]
    return {
        **stats,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "final_tracks_count": final_tracks_count,
    }


def _path_audit_current_processed_rows(rows: list[dict]) -> list[dict]:
    known_rows = [
        row for row in rows
        if row.get("stage") in _PATH_AUDIT_STAGE_PRIORITY
    ]
    if known_rows:
        final_priority = max(
            _PATH_AUDIT_STAGE_PRIORITY[row["stage"]]
            for row in known_rows
        )
        return [
            row for row in known_rows
            if _PATH_AUDIT_STAGE_PRIORITY[row["stage"]] == final_priority
        ]

    latest_by_path: dict[str, dict] = {}
    for row in rows:
        fp = str(row.get("filepath") or "")
        if not fp:
            continue
        current = latest_by_path.get(fp)
        if current is None or str(row.get("processed_at") or "") > str(current.get("processed_at") or ""):
            latest_by_path[fp] = row
    return list(latest_by_path.values())


def _path_audit_normalized_filename(path: Path) -> str:
    import re

    stem = path.stem.lower()
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    stem = re.sub(r"\s*-\s*[0-9]{1,2}[ab]\s*-\s*\d{2,3}\s*$", "", stem)
    stem = re.sub(r"\s*-\s*\d{2,3}\s*-\s*[0-9]{1,2}[ab]\s*$", "", stem)
    stem = re.sub(r"[^a-z0-9]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _path_audit_size_diff_pct(old_size, new_size: int) -> float | None:
    if old_size in (None, "", 0):
        return None
    try:
        old = float(old_size)
    except (TypeError, ValueError):
        return None
    if old <= 0:
        return None
    return abs(old - float(new_size)) / old


def _path_audit_fuzzy_similarity(old_path: Path, new_path: Path) -> float:
    from difflib import SequenceMatcher

    old_name = _path_audit_normalized_filename(old_path)
    new_name = _path_audit_normalized_filename(new_path)
    if not old_name or not new_name:
        return 0.0
    return SequenceMatcher(None, old_name, new_name).ratio()


_PATH_AUDIT_RELOCATION_IGNORE_TOKENS = {
    "remix", "mix", "original", "feat", "ft", "featuring", "extended",
}


def _path_audit_filename_tokens(path: Path) -> set[str]:
    return {
        token for token in _path_audit_normalized_filename(path).split()
        if len(token) > 1 and token not in _PATH_AUDIT_RELOCATION_IGNORE_TOKENS
    }


def _path_audit_token_overlap(old_path: Path, new_path: Path) -> float:
    old_tokens = _path_audit_filename_tokens(old_path)
    if not old_tokens:
        return 0.0
    new_tokens = _path_audit_filename_tokens(new_path)
    return len(old_tokens & new_tokens) / len(old_tokens)


_PATH_AUDIT_VERSION_TOKENS = {
    "original",
    "remix",
    "extended",
    "dub",
    "vocal",
    "instrumental",
    "bootleg",
    "edit",
    "radio",
    "club",
    "amapiano",
    "re-edit",
    "journey",
}


def _path_audit_version_tokens(path: Path) -> set[str]:
    import re

    raw_stem = path.stem.lower()
    tokens = set(_path_audit_normalized_filename(path).split())
    version_tokens = {
        token for token in tokens
        if token in _PATH_AUDIT_VERSION_TOKENS
    }
    if re.search(r"\bre[\s-]*edit\b", raw_stem):
        version_tokens.add("re-edit")
        version_tokens.discard("edit")
    return version_tokens


def _path_audit_numeric_title_risk(old_path: Path, new_path: Path) -> bool:
    old_title = old_path.stem.split(" - ", 1)[-1]
    new_title = new_path.stem.split(" - ", 1)[-1]
    old_tokens = _path_audit_normalized_filename(Path(old_title)).split()
    new_tokens = _path_audit_normalized_filename(Path(new_title)).split()
    if not old_tokens or not new_tokens:
        return False
    old_has_number = old_tokens[0].isdigit()
    new_has_number = new_tokens[0].isdigit()
    if old_has_number == new_has_number:
        return False
    old_without_number = old_tokens[1:] if old_has_number else old_tokens
    new_without_number = new_tokens[1:] if new_has_number else new_tokens
    return old_without_number == new_without_number


_PATH_AUDIT_ARTIST_CONNECTOR_TOKENS = {
    "and",
    "feat",
    "featuring",
    "ft",
    "pres",
    "presents",
    "vs",
    "with",
    "x",
}


def _path_audit_artist_tokens(path: Path) -> set[str]:
    artist_part = path.stem.split(" - ", 1)[0]
    normalized = _path_audit_normalized_filename(Path(artist_part))
    return {
        token for token in normalized.split()
        if len(token) > 1 and token not in _PATH_AUDIT_ARTIST_CONNECTOR_TOKENS
    }


def _path_audit_artist_expansion_risk(old_path: Path, new_path: Path) -> bool:
    old_artist_tokens = _path_audit_artist_tokens(old_path)
    if not old_artist_tokens:
        return False
    new_artist_tokens = _path_audit_artist_tokens(new_path)
    return len(new_artist_tokens - old_artist_tokens) >= 2


def _path_audit_auto_safe_downgrade_risk(old_path: Path, new_path: Path) -> bool:
    old_version_tokens = _path_audit_version_tokens(old_path)
    new_version_tokens = _path_audit_version_tokens(new_path)
    if old_version_tokens != new_version_tokens:
        return True
    if _path_audit_numeric_title_risk(old_path, new_path):
        return True
    return _path_audit_artist_expansion_risk(old_path, new_path)


def _path_audit_top_folder(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return "(outside_root)"
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "library" and parts[1] == "sorted":
        return "sorted"
    return parts[0] if parts else "(root)"


def _path_audit_orphan_analysis(orphan_rows: list[dict], disk_files: list[Path], root: Path) -> dict:
    from collections import Counter

    by_top_folder: Counter = Counter()
    by_stage_status: Counter = Counter()
    by_parent_folder: Counter = Counter()
    exact_size = 0
    near_size = 0
    token_50 = 0
    token_60 = 0
    token_70 = 0

    disk_sizes: list[int] = []
    disk_token_rows: list[tuple[str, set[str]]] = []
    for path in disk_files:
        try:
            disk_sizes.append(path.stat().st_size)
        except OSError:
            pass
        tokens = _path_audit_filename_tokens(path)
        if tokens:
            disk_token_rows.append((path.suffix.lower(), tokens))

    for orphan in orphan_rows:
        old_path = Path(orphan["filepath"])
        by_top_folder[_path_audit_top_folder(old_path, root)] += 1
        source_rows = orphan.get("source_rows") or []
        if source_rows:
            for source in source_rows:
                stage = source.get("stage") or source.get("table") or "unknown"
                status = orphan.get("status") or "unknown"
                by_stage_status[f"{stage}/{status}"] += 1
        else:
            status = orphan.get("status") or "unknown"
            by_stage_status[f"unknown/{status}"] += 1
        by_parent_folder[str(old_path.parent)] += 1

        old_size = orphan.get("filesize_bytes")
        try:
            old_size_int = int(old_size)
        except (TypeError, ValueError):
            old_size_int = None
        if old_size_int and any(size == old_size_int for size in disk_sizes):
            exact_size += 1
        if old_size_int and any(abs(size - old_size_int) / old_size_int < 0.10 for size in disk_sizes):
            near_size += 1

        old_tokens = _path_audit_filename_tokens(old_path)
        best_overlap = 0.0
        if old_tokens:
            old_suffix = old_path.suffix.lower()
            for candidate_suffix, candidate_tokens in disk_token_rows:
                if candidate_suffix != old_suffix:
                    continue
                overlap = len(old_tokens & candidate_tokens) / len(old_tokens)
                if overlap > best_overlap:
                    best_overlap = overlap
        if best_overlap >= 0.50:
            token_50 += 1
        if best_overlap >= 0.60:
            token_60 += 1
        if best_overlap >= 0.70:
            token_70 += 1

    return {
        "orphan_by_top_folder": dict(sorted(by_top_folder.items())),
        "orphan_by_stage_status": dict(sorted(by_stage_status.items())),
        "orphan_by_parent_folder_sample": [
            {"parent_folder": folder, "count": count}
            for folder, count in by_parent_folder.most_common(30)
        ],
        "orphan_size_match_stats": {
            "exact_file_size_exists_elsewhere": exact_size,
            "near_file_size_within_10pct_exists_elsewhere": near_size,
        },
        "orphan_filename_token_match_stats": {
            "token_overlap_gte_50pct": token_50,
            "token_overlap_gte_60pct": token_60,
            "token_overlap_gte_70pct": token_70,
        },
    }


def _path_audit_orphan_candidates(orphan_rows: list[dict], disk_files: list[Path]) -> list[dict]:
    candidates: list[dict] = []
    for orphan in orphan_rows:
        old_path = Path(orphan["filepath"])
        old_size = orphan.get("filesize_bytes")
        scored: list[dict] = []
        for disk_path in disk_files:
            try:
                candidate_size = disk_path.stat().st_size
            except OSError:
                continue
            token_overlap = _path_audit_token_overlap(old_path, disk_path)
            size_diff_pct = _path_audit_size_diff_pct(old_size, candidate_size)
            if size_diff_pct is None:
                size_similarity = 0.0
            else:
                size_similarity = max(0.0, 1.0 - min(size_diff_pct, 1.0))
            same_extension = old_path.suffix.lower() == disk_path.suffix.lower()
            score = (token_overlap * 0.60) + (size_similarity * 0.30) + (0.10 if same_extension else 0.0)
            if score <= 0:
                continue
            rounded_score = round(score, 6)
            rounded_size_diff = round(size_diff_pct, 6) if size_diff_pct is not None else ""
            review_tier = _path_audit_orphan_candidate_tier(
                rounded_score,
                token_overlap,
                size_diff_pct,
                same_extension,
                old_path,
                disk_path,
            )
            reason_bits = []
            if token_overlap:
                reason_bits.append("token_overlap")
            if size_similarity:
                reason_bits.append("size_similarity")
            if same_extension:
                reason_bits.append("same_extension")
            scored.append({
                "old_path": orphan["filepath"],
                "candidate_path": str(disk_path),
                "score": rounded_score,
                "token_overlap": round(token_overlap, 4),
                "size_diff_pct": rounded_size_diff,
                "same_extension": same_extension,
                "review_tier": review_tier,
                "old_filename": old_path.name,
                "candidate_filename": disk_path.name,
                "old_size": old_size,
                "candidate_size": candidate_size,
                "reason": "+".join(reason_bits) if reason_bits else "weak",
            })
        scored.sort(key=lambda row: (-row["score"], row["candidate_path"]))
        top_candidates = scored[:5]
        auto_safe_candidates = [
            row for row in top_candidates
            if row["review_tier"] == "AUTO_SAFE_CANDIDATE"
        ]
        if len(auto_safe_candidates) > 1:
            for row in auto_safe_candidates:
                row["review_tier"] = "REVIEW_CAREFULLY"
        candidates.extend(top_candidates)
    candidates.sort(key=lambda row: (row["old_path"], -row["score"]))
    return candidates


def _path_audit_orphan_candidate_tier(
    score: float,
    token_overlap: float,
    size_diff_pct,
    same_extension: bool,
    old_path: Path | None = None,
    new_path: Path | None = None,
) -> str:
    if (
        score >= 0.95
        and token_overlap >= 0.90
        and size_diff_pct is not None
        and size_diff_pct < 0.01
        and same_extension
    ):
        if old_path is not None and new_path is not None:
            if _path_audit_auto_safe_downgrade_risk(old_path, new_path):
                return "REVIEW_CAREFULLY"
        return "AUTO_SAFE_CANDIDATE"
    if score >= 0.80:
        return "REVIEW_CAREFULLY"
    return "WEAK_MATCH"


def _path_audit_orphan_candidate_tier_counts(candidates: list[dict]) -> dict:
    counts = {
        "AUTO_SAFE_CANDIDATE": 0,
        "REVIEW_CAREFULLY": 0,
        "WEAK_MATCH": 0,
    }
    for candidate in candidates:
        tier = candidate.get("review_tier", "WEAK_MATCH")
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def _path_audit_best_rename_match(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    rank = {
        "same_basename": 0,
        "fuzzy_filename": 1,
        "same_size_and_extension": 2,
    }
    return sorted(
        matches,
        key=lambda m: (
            rank.get(m.get("reason", ""), 99),
            -(m.get("similarity") or 0),
            m.get("size_diff_pct") if m.get("size_diff_pct") is not None else 999,
        ),
    )[0]


def _path_audit_db_rows(db_path: Path) -> tuple[list[dict], list[dict], dict, str | None]:
    import sqlite3

    if not db_path.exists():
        return [], [], {
            "tracks_rows": 0,
            "processed_state_rows": 0,
            "combined_db_paths": 0,
            "processed_state_path_column": None,
            "repeated_processed_state_paths": 0,
            "cross_source_overlap_count": 0,
            "historical_paths_count": 0,
            "stale_processed_state_rows_total": 0,
            "active_processed_state_rows": 0,
            "canonical_source": "processed_state",
            "current_processed_state_stage": None,
        }, f"database not found: {db_path}"

    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            combined: dict[str, dict] = {}
            duplicates: list[dict] = []
            source_counts = {
                "tracks_rows": 0,
                "processed_state_rows": 0,
                "combined_db_paths": 0,
                "processed_state_path_column": None,
                "repeated_processed_state_paths": 0,
                "cross_source_overlap_count": 0,
                "historical_paths_count": 0,
                "stale_processed_state_rows_total": 0,
                "active_processed_state_rows": 0,
                "canonical_source": "processed_state",
                "current_processed_state_stage": None,
            }
            track_rows: list[dict] = []

            if "tracks" in tables:
                track_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, filepath, filename, status, filesize_bytes FROM tracks"
                    ).fetchall()
                ]
                source_counts["tracks_rows"] = len(track_rows)
                for row in track_rows:
                    fp = str(row.get("filepath") or "")
                    if not fp:
                        continue
                    item = combined.setdefault(fp, {
                        "filepath": fp,
                        "filename": row.get("filename") or Path(fp).name,
                        "status": row.get("status"),
                        "filesize_bytes": row.get("filesize_bytes"),
                        "sources": [],
                        "source_rows": [],
                    })
                    if "tracks" not in item["sources"]:
                        item["sources"].append("tracks")
                    if item.get("filesize_bytes") is None:
                        item["filesize_bytes"] = row.get("filesize_bytes")
                    item["source_rows"].append({"table": "tracks", "id": row.get("id")})

                duplicates.extend([
                    {
                        "filepath": row["filepath"],
                        "count": row["n"],
                        "sources": ["tracks"],
                        "duplicate_type": "within_table",
                        "table": "tracks",
                        "row_ids": [
                            r["id"] for r in track_rows
                            if str(r.get("filepath", "")) == str(row["filepath"])
                        ],
                    }
                    for row in conn.execute(
                        "SELECT filepath, COUNT(*) AS n FROM tracks "
                        "GROUP BY filepath HAVING COUNT(*) > 1"
                    ).fetchall()
                ])

            has_track_rows = len(track_rows) > 0
            source_counts["canonical_source"] = "tracks" if has_track_rows else "processed_state"

            if "processed_state" in tables:
                columns = _path_audit_table_columns(conn, "processed_state")
                path_col = "filepath" if "filepath" in columns else "path" if "path" in columns else None
                source_counts["processed_state_path_column"] = path_col
                if path_col:
                    processed_rows = [
                        dict(row)
                        for row in conn.execute(
                            f"SELECT id, stage, {path_col} AS filepath, file_size, "
                            "file_mtime, status, processed_at, reason "
                            "FROM processed_state"
                        ).fetchall()
                    ]
                    source_counts["historical_paths_count"] = len(processed_rows)
                    stale_processed_rows = [
                        row for row in processed_rows
                        if str(row.get("status") or "").lower() == "stale"
                    ]
                    active_processed_rows = [
                        row for row in processed_rows
                        if str(row.get("status") or "").lower() != "stale"
                    ]
                    source_counts["stale_processed_state_rows_total"] = len(stale_processed_rows)
                    current_processed_rows = _path_audit_current_processed_rows(active_processed_rows)
                    source_counts["processed_state_rows"] = len(current_processed_rows)
                    source_counts["active_processed_state_rows"] = len(current_processed_rows)
                    current_stages = sorted({
                        str(row.get("stage") or "")
                        for row in current_processed_rows
                        if row.get("stage")
                    })
                    source_counts["current_processed_state_stage"] = (
                        current_stages[0] if len(current_stages) == 1 else current_stages
                    )
                    if not has_track_rows:
                        for row in current_processed_rows:
                            fp = str(row.get("filepath") or "")
                            if not fp:
                                continue
                            item = combined.setdefault(fp, {
                                "filepath": fp,
                                "filename": Path(fp).name,
                                "status": row.get("status"),
                                "filesize_bytes": row.get("file_size"),
                                "sources": [],
                                "source_rows": [],
                            })
                            if "processed_state" not in item["sources"]:
                                item["sources"].append("processed_state")
                            if item.get("filesize_bytes") is None:
                                item["filesize_bytes"] = row.get("file_size")
                            item["source_rows"].append({
                                "table": "processed_state",
                                "id": row.get("id"),
                                "stage": row.get("stage"),
                            })

                    repeated_rows = conn.execute(
                        f"SELECT {path_col} AS filepath, COUNT(*) AS n "
                        "FROM processed_state "
                        f"GROUP BY {path_col} HAVING COUNT(*) > 1"
                    ).fetchall()
                    source_counts["repeated_processed_state_paths"] = len(repeated_rows)

                    if not has_track_rows:
                        current_stage_path_rows: dict[tuple[str, str], list[dict]] = {}
                        for row in current_processed_rows:
                            key = (str(row.get("stage") or ""), str(row.get("filepath") or ""))
                            current_stage_path_rows.setdefault(key, []).append(row)
                        for (stage, filepath), grouped_rows in current_stage_path_rows.items():
                            if len(grouped_rows) <= 1:
                                continue
                            duplicates.append({
                                "filepath": filepath,
                                "count": len(grouped_rows),
                                "sources": ["processed_state"],
                                "duplicate_type": "within_stage",
                                "table": "processed_state",
                                "stage": stage,
                                "row_ids": [r["id"] for r in grouped_rows],
                            })

                    if has_track_rows:
                        track_paths = {str(row.get("filepath") or "") for row in track_rows if row.get("filepath")}
                        processed_paths = {
                            str(row.get("filepath") or "")
                            for row in current_processed_rows
                            if row.get("filepath")
                        }
                        source_counts["cross_source_overlap_count"] = len(track_paths & processed_paths)
                    else:
                        for item in combined.values():
                            if "tracks" in item["sources"] and "processed_state" in item["sources"]:
                                source_counts["cross_source_overlap_count"] += 1

            rows = sorted(combined.values(), key=lambda r: r["filepath"])
            source_counts["combined_db_paths"] = len(rows)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [], [], {
            "tracks_rows": 0,
            "processed_state_rows": 0,
            "combined_db_paths": 0,
                "processed_state_path_column": None,
                "repeated_processed_state_paths": 0,
                "cross_source_overlap_count": 0,
                "historical_paths_count": 0,
                "stale_processed_state_rows_total": 0,
                "active_processed_state_rows": 0,
                "canonical_source": "processed_state",
                "current_processed_state_stage": None,
            }, f"could not read database {db_path}: {exc}"

    return rows, duplicates, source_counts, None


def _path_audit_all_processed_state_rows(db_path: Path) -> list[dict]:
    import sqlite3

    if not db_path.exists():
        return []
    uri = f"file:{db_path.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_state'"
            ).fetchone()
            if table is None:
                return []
            columns = _path_audit_table_columns(conn, "processed_state")
            path_col = "filepath" if "filepath" in columns else "path" if "path" in columns else None
            if path_col is None:
                return []
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT id, stage, {path_col} AS filepath, file_size, "
                    "file_mtime, status, processed_at, reason "
                    "FROM processed_state"
                ).fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _path_audit_stale_processed_state_rows(root: Path, db_path: Path) -> list[dict]:
    rows = _path_audit_all_processed_state_rows(db_path)
    candidate_paths: set[Path] = set()
    for row in rows:
        if str(row.get("status") or "").lower() == "stale":
            continue
        raw_path = str(row.get("filepath") or "")
        if not raw_path:
            continue
        try:
            path = assert_path_under_root(raw_path, root)
        except ValueError:
            continue
        if path.exists():
            candidate_paths.add(path)
    candidate_paths_by_name: dict[str, list[Path]] = {}
    for path in candidate_paths:
        key = _path_audit_normalized_filename(path)
        if key:
            candidate_paths_by_name.setdefault(key, []).append(path)

    stale_rows: list[dict] = []
    for row in rows:
        if str(row.get("status") or "").lower() == "stale":
            continue
        raw_path = str(row.get("filepath") or "")
        if not raw_path:
            continue
        try:
            old_path = assert_path_under_root(raw_path, root)
        except ValueError:
            continue
        if old_path.exists():
            continue
        candidate_subset = candidate_paths_by_name.get(_path_audit_normalized_filename(old_path), [])
        if not candidate_subset:
            continue
        orphan = {
            "filepath": str(old_path),
            "filesize_bytes": row.get("file_size"),
        }
        candidates = _path_audit_orphan_candidates([orphan], sorted(candidate_subset))
        auto_safe = [
            candidate for candidate in candidates
            if candidate.get("review_tier") == "AUTO_SAFE_CANDIDATE"
        ]
        if not auto_safe:
            continue
        best = auto_safe[0]
        stale_rows.append({
            "old_path": str(old_path),
            "replacement_path": best["candidate_path"],
            "stage": row.get("stage"),
            "reason": "superseded_by_existing_path",
            "source_rows": [
                {
                    "table": "processed_state",
                    "id": row.get("id"),
                    "stage": row.get("stage"),
                }
            ],
        })
    stale_rows.sort(key=lambda item: (item["old_path"], item["stage"] or ""))
    return stale_rows


def _path_audit_queue_files(root: Path) -> list[Path]:
    queue_files: list[Path] = []
    base = root / "data"
    if not base.exists():
        return []
    for suffix in ("*.json", "*.jsonl"):
        for path in base.rglob(suffix):
            if "queue" in path.name.lower():
                queue_files.append(path.resolve())
    return sorted(set(queue_files))


def _path_audit_iter_paths(value, *, field: str = "", location: str = ""):
    path_keys = {
        "file",
        "filepath",
        "path",
        "track_path",
        "original_path",
        "current_path",
        "target_path",
        "old_path",
        "new_path",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}" if location else str(key)
            if isinstance(child, str) and key in path_keys:
                yield key, child, child_location
            else:
                yield from _path_audit_iter_paths(
                    child, field=str(key), location=child_location
                )
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            child_location = f"{location}[{idx}]"
            yield from _path_audit_iter_paths(
                child, field=field, location=child_location
            )


def _path_audit_stale_queue_entries(root: Path) -> list[dict]:
    import json

    stale: list[dict] = []
    for queue_file in _path_audit_queue_files(root):
        try:
            if queue_file.suffix.lower() == ".jsonl":
                records = []
                for line_no, line in enumerate(
                    queue_file.read_text(encoding="utf-8").splitlines(),
                    start=1,
                ):
                    if not line.strip():
                        continue
                    try:
                        records.append((f"line {line_no}", json.loads(line)))
                    except json.JSONDecodeError:
                        stale.append({
                            "queue_file": str(queue_file),
                            "location": f"line {line_no}",
                            "field": "",
                            "path": "",
                            "reason": "invalid_json",
                        })
            else:
                records = [("json", json.loads(queue_file.read_text(encoding="utf-8")))]
        except (OSError, json.JSONDecodeError) as exc:
            stale.append({
                "queue_file": str(queue_file),
                "location": "",
                "field": "",
                "path": "",
                "reason": f"unreadable_queue: {exc}",
            })
            continue

        for record_location, record in records:
            for field, raw_path, location in _path_audit_iter_paths(record):
                candidate = Path(raw_path).expanduser()
                checked = candidate if candidate.is_absolute() else root / candidate
                if not checked.exists():
                    stale.append({
                        "queue_file": str(queue_file),
                        "location": f"{record_location}:{location}",
                        "field": field,
                        "path": raw_path,
                        "reason": "path_not_found",
                    })
    return stale


def _path_audit_report(
    root: Path,
    db_path: Path,
    *,
    include_orphan_candidates: bool = False,
) -> dict:
    from collections import defaultdict
    from datetime import datetime, timezone

    db_rows, duplicate_db_entries, source_counts, db_error = _path_audit_db_rows(db_path)
    disk_files = _path_audit_audio_files(root)
    mixed_root_db_paths: list[dict] = []
    scoped_db_rows: list[dict] = []
    for row in db_rows:
        raw_fp = str(row.get("filepath") or "")
        try:
            scoped_path = assert_path_under_root(raw_fp, root)
        except ValueError as exc:
            mixed_root_db_paths.append({
                "filepath": raw_fp,
                "sources": row.get("sources", []),
                "source_rows": row.get("source_rows", []),
                "reason": str(exc),
            })
            continue
        scoped = dict(row)
        scoped["filepath"] = str(scoped_path)
        scoped_db_rows.append(scoped)
    db_rows = scoped_db_rows
    scoped_duplicates: list[dict] = []
    for duplicate in duplicate_db_entries:
        try:
            scoped_dup_path = assert_path_under_root(duplicate.get("filepath", ""), root)
        except ValueError:
            continue
        scoped_duplicate = dict(duplicate)
        scoped_duplicate["filepath"] = str(scoped_dup_path)
        scoped_duplicates.append(scoped_duplicate)
    duplicate_db_entries = scoped_duplicates

    db_paths_exact = {str(Path(row["filepath"]).expanduser()) for row in db_rows}
    db_paths_resolved = set()
    for row in db_rows:
        try:
            db_paths_resolved.add(str(Path(row["filepath"]).expanduser().resolve()))
        except OSError:
            pass

    untracked = [
        path for path in disk_files
        if str(path) not in db_paths_exact and str(path.resolve()) not in db_paths_resolved
    ]

    by_basename: dict[str, list[Path]] = defaultdict(list)
    by_size_ext: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for path in untracked:
        by_basename[path.name.lower()].append(path)
        try:
            by_size_ext[(path.stat().st_size, path.suffix.lower())].append(path)
        except OSError:
            pass

    missing_files: list[dict] = []
    possible_renames: list[dict] = []
    relocation_candidates: list[dict] = []
    orphan_db_rows: list[dict] = []

    for row in db_rows:
        fp = str(row["filepath"])
        path = Path(fp).expanduser()
        if path.exists():
            continue

        missing = {
            "id": row.get("id"),
            "filepath": fp,
            "filename": row.get("filename") or path.name,
            "status": row.get("status"),
            "filesize_bytes": row.get("filesize_bytes"),
            "sources": row.get("sources", []),
            "source_rows": row.get("source_rows", []),
        }
        missing_files.append(missing)

        matches: list[dict] = []
        for candidate in by_basename.get(path.name.lower(), []):
            candidate_size = candidate.stat().st_size
            size_diff_pct = _path_audit_size_diff_pct(row.get("filesize_bytes"), candidate_size)
            matches.append({
                "path": str(candidate),
                "reason": "same_basename",
                "size": candidate_size,
                "similarity": 1.0,
                "size_diff_pct": size_diff_pct,
            })

        if matches:
            matched_paths = {match["path"] for match in matches}
        else:
            matched_paths = set()

        for candidate in untracked:
            if str(candidate) in matched_paths:
                continue
            if candidate.suffix.lower() != path.suffix.lower():
                continue
            try:
                candidate_size = candidate.stat().st_size
            except OSError:
                continue
            size_diff_pct = _path_audit_size_diff_pct(row.get("filesize_bytes"), candidate_size)
            if size_diff_pct is None or size_diff_pct >= 0.05:
                continue
            similarity = _path_audit_fuzzy_similarity(path, candidate)
            if similarity <= 0.85:
                continue
            matches.append({
                "path": str(candidate),
                "reason": "fuzzy_filename",
                "size": candidate_size,
                "similarity": round(similarity, 4),
                "size_diff_pct": round(size_diff_pct, 6),
            })
            matched_paths.add(str(candidate))

        if matches:
            best_match = _path_audit_best_rename_match(matches)
            possible_renames.append({
                "old_path": missing["filepath"],
                "new_path": best_match.get("path") if best_match else None,
                "similarity": best_match.get("similarity") if best_match else None,
                "size_diff_pct": best_match.get("size_diff_pct") if best_match else None,
                "reason": best_match.get("reason") if best_match else None,
                "db_row": missing,
                "matches": matches,
            })
        else:
            orphan_db_rows.append(missing)

    remaining_orphans: list[dict] = []
    for orphan in orphan_db_rows:
        old_path = Path(orphan["filepath"])
        best_candidate: dict | None = None
        for candidate in untracked:
            if candidate.suffix.lower() != old_path.suffix.lower():
                continue
            try:
                candidate_size = candidate.stat().st_size
            except OSError:
                continue
            size_diff_pct = _path_audit_size_diff_pct(orphan.get("filesize_bytes"), candidate_size)
            if size_diff_pct is None or size_diff_pct >= 0.10:
                continue
            token_overlap = _path_audit_token_overlap(old_path, candidate)
            if token_overlap < 0.70:
                continue
            match = {
                "old_path": orphan["filepath"],
                "new_path": str(candidate),
                "match_type": "relocation",
                "token_overlap": round(token_overlap, 4),
                "size_diff_pct": round(size_diff_pct, 6),
                "db_row": orphan,
            }
            if best_candidate is None:
                best_candidate = match
                continue
            if (
                match["token_overlap"] > best_candidate["token_overlap"]
                or (
                    match["token_overlap"] == best_candidate["token_overlap"]
                    and match["size_diff_pct"] < best_candidate["size_diff_pct"]
                )
            ):
                best_candidate = match
        if best_candidate:
            relocation_candidates.append(best_candidate)
        else:
            remaining_orphans.append(orphan)
    orphan_db_rows = remaining_orphans
    orphan_analysis = _path_audit_orphan_analysis(orphan_db_rows, disk_files, root)
    orphan_candidates = (
        _path_audit_orphan_candidates(orphan_db_rows, disk_files)
        if include_orphan_candidates else []
    )
    orphan_candidate_tiers = _path_audit_orphan_candidate_tier_counts(orphan_candidates)
    stale_processed_state_rows = _path_audit_stale_processed_state_rows(root, db_path)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "database": str(db_path),
        "read_only": True,
        "db_error": db_error,
        "summary": {
            "db_rows": len(db_rows),
            "tracks_rows": source_counts["tracks_rows"],
            "processed_state_rows": source_counts["processed_state_rows"],
            "canonical_source": source_counts["canonical_source"],
            "combined_db_paths": len(db_rows),
            "mixed_root_db_paths": len(mixed_root_db_paths),
            "repeated_processed_state_paths": source_counts["repeated_processed_state_paths"],
            "cross_source_overlap_count": source_counts["cross_source_overlap_count"],
            "historical_paths_count": source_counts["historical_paths_count"],
            "stale_processed_state_rows_total": source_counts["stale_processed_state_rows_total"],
            "active_processed_state_rows": source_counts["active_processed_state_rows"],
            "disk_audio_files": len(disk_files),
            "missing_files": len(missing_files),
            "untracked_files": len(untracked),
            "possible_renames": len(possible_renames),
            "relocation_candidates": len(relocation_candidates),
            "duplicate_db_entries": len(duplicate_db_entries),
            "stale_queue_entries": 0,
            "stale_processed_state_count": len(stale_processed_state_rows),
            "orphan_db_rows": len(orphan_db_rows),
            "orphan_candidate_scoring_enabled": include_orphan_candidates,
        },
        "path_sources": {
            "tracks_rows": source_counts["tracks_rows"],
            "processed_state_rows": source_counts["processed_state_rows"],
            "canonical_source": source_counts["canonical_source"],
            "combined_db_paths": len(db_rows),
            "mixed_root_db_paths": len(mixed_root_db_paths),
            "repeated_processed_state_paths": source_counts["repeated_processed_state_paths"],
            "cross_source_overlap_count": source_counts["cross_source_overlap_count"],
            "historical_paths_count": source_counts["historical_paths_count"],
            "stale_processed_state_rows_total": source_counts["stale_processed_state_rows_total"],
            "active_processed_state_rows": source_counts["active_processed_state_rows"],
            "current_processed_state_stage": source_counts["current_processed_state_stage"],
            "processed_state_path_column": source_counts["processed_state_path_column"],
        },
        "missing_files": missing_files,
        "mixed_root_db_paths": mixed_root_db_paths,
        **orphan_analysis,
        "orphan_candidate_tiers": orphan_candidate_tiers,
        "orphan_candidates": orphan_candidates,
        "untracked_files": [str(path) for path in untracked],
        "possible_renames": possible_renames,
        "relocation_candidates": relocation_candidates,
        "duplicate_db_entries": duplicate_db_entries,
        "stale_queue_entries": _path_audit_stale_queue_entries(root),
        "stale_processed_state_rows": stale_processed_state_rows,
        "orphan_db_rows": orphan_db_rows,
        "limitations": [
            "rename matching is heuristic only: same basename or same filesize plus extension",
            "filesize rename matching requires tracks.filesize_bytes to be populated",
            "queue auditing checks JSON/JSONL files with 'queue' in the filename under data/",
        ],
    }


def _path_audit_print_summary(report: dict, json_path: Path) -> None:
    summary = report["summary"]
    print("\n=== path-audit READ-ONLY ===\n")
    print(f"  Root                  : {report['root']}")
    print(f"  Database              : {report['database']}")
    if report.get("db_error"):
        print(f"  DB warning            : {report['db_error']}")
    print(f"  DB rows               : {summary['db_rows']}")
    print(f"  Canonical source      : {summary['canonical_source']}")
    print(f"  Tracks rows           : {summary['tracks_rows']}")
    print(f"  Processed-state rows  : {summary['processed_state_rows']}")
    print(f"  Combined DB paths     : {summary['combined_db_paths']}")
    print(f"  Mixed-root DB paths   : {summary['mixed_root_db_paths']}")
    print(f"  Repeated pstate paths : {summary['repeated_processed_state_paths']}")
    print(f"  Cross-source overlap  : {summary['cross_source_overlap_count']}")
    print(f"  Historical paths      : {summary['historical_paths_count']}")
    print(f"  Active pstate rows    : {summary['active_processed_state_rows']}")
    print(f"  Stale pstate total    : {summary['stale_processed_state_rows_total']}")
    print(f"  Disk audio files      : {summary['disk_audio_files']}")
    print(f"  Missing files         : {summary['missing_files']}")
    print(f"  Untracked files       : {summary['untracked_files']}")
    print(f"  Possible renames      : {summary['possible_renames']}")
    print(f"  Relocation candidates : {summary['relocation_candidates']}")
    print(f"  Duplicate DB entries  : {summary['duplicate_db_entries']}")
    print(f"  Stale queue entries   : {summary['stale_queue_entries']}")
    print(f"  Stale pstate rows     : {summary['stale_processed_state_count']}")
    print(f"  Orphan DB rows        : {summary['orphan_db_rows']}")
    print(f"  Orphan scoring        : {summary['orphan_candidate_scoring_enabled']}")
    if summary["orphan_db_rows"]:
        print("  Orphans by top folder :")
        for folder, count in report.get("orphan_by_top_folder", {}).items():
            print(f"    {folder}: {count}")
        print("  Orphan size matches   :")
        for key, count in report.get("orphan_size_match_stats", {}).items():
            print(f"    {key}: {count}")
        print("  Orphan token matches  :")
        for key, count in report.get("orphan_filename_token_match_stats", {}).items():
            print(f"    {key}: {count}")
        if summary["orphan_candidate_scoring_enabled"]:
            print("  Orphan candidate tiers:")
            for key, count in report.get("orphan_candidate_tiers", {}).items():
                print(f"    {key}: {count}")
    print(f"\n  JSON report           : {json_path}")


def _path_audit_write_renames_csv(report: dict, csv_path: Path) -> None:
    import csv

    rows: list[dict] = []
    for item in report.get("possible_renames", []):
        best = _path_audit_best_rename_match(item.get("matches", []))
        if not best:
            continue
        old_path = Path(item.get("old_path") or item.get("db_row", {}).get("filepath", ""))
        new_path = Path(best.get("path", ""))
        rows.append({
            "similarity": best.get("similarity"),
            "size_diff_pct": best.get("size_diff_pct"),
            "reason": best.get("reason"),
            "old_path": str(old_path),
            "new_path": str(new_path),
            "old_filename": old_path.name,
            "new_filename": new_path.name,
            "old_size": item.get("db_row", {}).get("filesize_bytes"),
            "new_size": best.get("size"),
        })

    rows.sort(
        key=lambda row: (
            -(float(row["similarity"]) if row["similarity"] is not None else 0.0),
            float(row["size_diff_pct"]) if row["size_diff_pct"] is not None else 999.0,
        )
    )
    fieldnames = [
        "similarity",
        "size_diff_pct",
        "reason",
        "old_path",
        "new_path",
        "old_filename",
        "new_filename",
        "old_size",
        "new_size",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _path_audit_write_orphan_candidates_csv(report: dict, csv_path: Path) -> None:
    import csv

    fieldnames = [
        "old_path",
        "candidate_path",
        "score",
        "token_overlap",
        "size_diff_pct",
        "same_extension",
        "review_tier",
        "old_filename",
        "candidate_filename",
        "old_size",
        "candidate_size",
        "reason",
    ]
    rows = list(report.get("orphan_candidates", []))
    rows.sort(key=lambda row: (row["old_path"], -float(row["score"])))
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _path_audit_write_stale_rows_csv(report: dict, csv_path: Path) -> None:
    import csv

    fieldnames = ["old_path", "replacement_path", "stage", "reason"]
    rows = [
        {field: row.get(field, "") for field in fieldnames}
        for row in report.get("stale_processed_state_rows", [])
    ]
    rows.sort(key=lambda row: (row["old_path"], row.get("stage") or ""))
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_path_audit(args) -> int:
    import json
    from datetime import datetime

    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    db_path = _path_audit_db_path(root)
    include_orphan_candidates = getattr(args, "include_orphan_candidates", False)
    report = _path_audit_report(
        root,
        db_path,
        include_orphan_candidates=include_orphan_candidates,
    )
    report["summary"]["stale_queue_entries"] = len(report["stale_queue_entries"])

    log_dir = root / "logs" / "path_audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = log_dir / f"path_audit_{stamp}.json"
    text_path = log_dir / f"path_audit_{stamp}.log"
    rename_csv_path = log_dir / f"path_audit_{stamp}_possible_renames.csv"
    stale_csv_path = log_dir / f"path_audit_{stamp}_stale_rows.csv"

    json_text = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(json_text + "\n", encoding="utf-8")
    text_path.write_text(
        "\n".join(
            [
                "path-audit READ-ONLY",
                f"root={report['root']}",
                f"database={report['database']}",
                *(f"{k}={v}" for k, v in report["summary"].items()),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _path_audit_write_renames_csv(report, rename_csv_path)
    if include_orphan_candidates:
        orphan_csv_path = log_dir / f"path_audit_{stamp}_orphan_candidates.csv"
        _path_audit_write_orphan_candidates_csv(report, orphan_csv_path)
    _path_audit_write_stale_rows_csv(report, stale_csv_path)

    _path_audit_print_summary(report, json_path)
    return 0


def _build_tracks_write_log(result: dict, log_path: Path) -> None:
    lines = [
        "build-tracks",
        f"source_rows={result.get('source_rows', 0)}",
        f"inserted={result.get('inserted', 0)}",
        f"updated={result.get('updated', 0)}",
        f"unchanged={result.get('unchanged', 0)}",
        f"skipped_missing_file={result.get('skipped_missing_file', 0)}",
        f"skipped_stale={result.get('skipped_stale', 0)}",
        f"skipped_outside_root={result.get('skipped_outside_root', 0)}",
        f"duplicate_filepaths_collapsed={result.get('duplicate_filepaths_collapsed', 0)}",
        f"final_tracks_count={result.get('final_tracks_count', 0)}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_build_tracks(args) -> int:
    import sqlite3
    from datetime import datetime

    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    db_path = _path_audit_db_path(root)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 2

    log_dir = root / "logs" / "tracks"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{stamp}_build_tracks.log"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        result = _build_tracks_upsert(conn, root)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _build_tracks_write_log(result, log_path)
    print("\n=== build-tracks ===\n")
    print(f"  Root                  : {root}")
    print(f"  Database              : {db_path}")
    print(f"  Source rows           : {result['source_rows']}")
    print(f"  Inserted              : {result['inserted']}")
    print(f"  Updated               : {result['updated']}")
    print(f"  Unchanged             : {result['unchanged']}")
    print(f"  Skipped missing file  : {result['skipped_missing_file']}")
    print(f"  Skipped stale         : {result['skipped_stale']}")
    print(f"  Final tracks count    : {result['final_tracks_count']}")
    print(f"\n  Log                   : {log_path}")
    return 0


def run_metadata_score_online(args) -> int:
    """Run read-only online metadata scoring against the canonical tracks table."""
    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from modules import metadata_enrich_online

    result = metadata_enrich_online.run(
        root,
        mock_providers=getattr(args, "mock_providers", False),
    )
    print("\n=== metadata-score-online ===\n")
    print(f"  Root          : {root}")
    print(f"  Tracks scored : {result['tracks_scored']}")
    print(f"  Log           : {result['log_path']}")
    print()
    return 0


def run_metadata_repair_scan(args) -> int:
    """Generate deterministic metadata repair proposals without DB writes."""
    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from modules import metadata_repair

    result = metadata_repair.scan(root)
    metadata_repair.print_scan_summary(result)
    return 0


def run_metadata_repair_apply(args) -> int:
    """Dry-run or apply approved metadata repair proposals to tracks only."""
    if getattr(args, "apply", False) and not getattr(args, "yes", False):
        print("ERROR: --apply requires --yes for metadata-repair-apply.", file=sys.stderr)
        return 2
    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from modules import metadata_repair

    result = metadata_repair.apply_approved(root, apply=bool(args.apply))
    metadata_repair.print_apply_summary(result)
    return 0


def run_metadata_sanitation_scan(args) -> int:
    """Generate deterministic metadata sanitation proposals without DB writes."""
    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from modules import metadata_sanitation

    result = metadata_sanitation.scan(root)
    print("\n=== metadata-sanitation-scan ===")
    print(f"root: {result['root']}")
    print(f"queue: {result['queue_path']}")
    print(
        f"tracks scanned: {result['total_tracks']}  "
        f"proposals: {result['proposal_count']}  "
        f"skipped: {result['skipped_count']}"
    )
    confidence = result.get("counts", {}).get("by_confidence", {})
    print(
        "confidence: "
        f"HIGH={confidence.get('HIGH', 0)} "
        f"MEDIUM={confidence.get('MEDIUM', 0)} "
        f"LOW={confidence.get('LOW', 0)}"
    )
    return 0


def run_metadata_sanitation_apply(args) -> int:
    """Dry-run or apply approved metadata sanitation proposals to tracks only."""
    if getattr(args, "apply", False) and not getattr(args, "yes", False):
        print("ERROR: --apply requires --yes for metadata-sanitation-apply.", file=sys.stderr)
        return 2
    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from modules import metadata_sanitation

    result = metadata_sanitation.apply_approved(root, apply=bool(args.apply))
    mode = "APPLY" if result.get("dry_run") is False else "DRY RUN"
    print(f"\n=== metadata-sanitation-apply {mode} ===")
    print(f"root: {result['root']}")
    print(f"queue: {result['queue_path']}")
    print(f"approved seen: {result['approved_seen']}")
    print(
        f"proposed: {result['proposed_count']}  "
        f"applied: {result['applied_count']}  "
        f"skipped: {result['skipped_count']}"
    )
    return 0


def _load_enrichment_review_queue(root: Path) -> list[dict]:
    queue_path = root / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    if not queue_path.exists():
        return []

    entries: list[dict] = []
    for raw_line in queue_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _print_enrichment_review_entry(entry: dict) -> None:
    query = entry.get("query") or {}
    best_match = entry.get("best_match") or {}
    print(f"  filepath : {entry.get('filepath', '')}")
    print(
        "  query    : "
        f"{query.get('artist', '')} - {query.get('title', '')}"
    )
    if best_match:
        print(
            "  best     : "
            f"{best_match.get('provider', '')} | "
            f"{best_match.get('artist', '')} - {best_match.get('title', '')}"
        )
    else:
        print("  best     : -")
    print(f"  score    : {entry.get('score', 0.0)}")
    print(f"  conf     : {entry.get('confidence', 'LOW')}")
    print(f"  action   : {entry.get('action_suggestion', 'ignore')}")
    print()


def run_enrichment_review(args) -> int:
    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    entries = _load_enrichment_review_queue(root)
    total = len(entries)
    summary = {
        "auto_candidate": 0,
        "review": 0,
        "ignore": 0,
    }
    for entry in entries:
        action = str(entry.get("action_suggestion", "ignore"))
        if action not in summary:
            action = "ignore"
        summary[action] += 1

    confidence_filter = getattr(args, "confidence", None)
    action_filter = getattr(args, "action", None)
    limit = getattr(args, "limit", None)
    top_high = getattr(args, "top_high", None)

    filtered = []
    for entry in entries:
        if confidence_filter and str(entry.get("confidence", "")).upper() != confidence_filter:
            continue
        if action_filter and str(entry.get("action_suggestion", "")) != action_filter:
            continue
        filtered.append(entry)

    print("\n=== enrichment-review ===\n")
    print(f"  Root             : {root}")
    print(f"  Total entries    : {total}")
    print(f"  auto_candidate   : {summary['auto_candidate']}")
    print(f"  review           : {summary['review']}")
    print(f"  ignore           : {summary['ignore']}")
    if confidence_filter:
        print(f"  Filter confidence: {confidence_filter}")
    if action_filter:
        print(f"  Filter action    : {action_filter}")

    top_high_paths: set[str] = set()
    if top_high is not None:
        high_entries = [
            entry for entry in entries
            if str(entry.get("confidence", "")).upper() == "HIGH"
        ]
        high_entries.sort(key=lambda entry: float(entry.get("score", 0.0)), reverse=True)
        top_high_entries = high_entries[:top_high]
        top_high_paths = {str(entry.get("filepath", "")) for entry in top_high_entries}
        print(f"  Top HIGH candidates ({top_high})")
        print("  ---------------------------")
        for entry in top_high_entries:
            _print_enrichment_review_entry(entry)

    visible = [
        entry for entry in filtered
        if str(entry.get("filepath", "")) not in top_high_paths
    ]
    if limit is not None:
        visible = visible[:limit]
    print(f"  Display count    : {len(visible)}")
    print()
    for entry in visible:
        _print_enrichment_review_entry(entry)

    return 0


def run_enrichment_apply_approved(args) -> int:
    """Apply approved enrichment metadata from review_state.json to tracks."""
    _setup_logging(getattr(args, "verbose", False))

    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    mode = _apply_mode_or_error(args)
    if mode is None:
        return 2

    from modules import enrichment_apply

    result = enrichment_apply.apply_approved_enrichment(root, apply=mode)

    print("\n=== enrichment-apply-approved ===\n")
    print(f"  Root             : {root}")
    print(f"  Database         : {result['db_path']}")
    print(f"  Review state     : {result['state_path']}")
    print(f"  Mode             : {'APPLY' if not result['dry_run'] else 'DRY-RUN'}")
    print(f"  Approved seen    : {result['approved_seen']}")
    print(f"  Proposed updates : {result['proposed_count']}")
    print(f"  Applied updates  : {result['applied_count']}")
    print(f"  Skipped          : {result['skipped_count']}")
    print(f"  Log              : {result['log_path']}")

    if result["changes"]:
        print("\n  Sample proposed changes:")
        for change in result["changes"][:5]:
            fields = ", ".join(change.get("fields", []))
            print(
                f"    - track {change.get('track_id')} | {Path(change.get('filepath', '')).name} | "
                f"{fields}"
            )
    if result["skipped"]:
        print("\n  Sample skipped items:")
        for skip in result["skipped"][:5]:
            print(
                f"    - track {skip.get('track_id')} | {skip.get('reason')} | {skip.get('filepath')}"
            )
    print()
    return 0


# ---------------------------------------------------------------------------
# Read-only path reconciliation planning
# ---------------------------------------------------------------------------
def _path_reconcile_best_match(matches: list[dict]) -> dict | None:
    if not matches:
        return None
    reason_rank = {
        "same_basename": 0,
        "fuzzy_filename": 1,
        "same_size_and_extension": 2,
    }
    return sorted(matches, key=lambda m: reason_rank.get(m.get("reason", ""), 99))[0]


def _path_reconcile_confidence(reason: str) -> float:
    if reason == "same_basename":
        return 0.90
    if reason == "same_size_and_extension":
        return 0.70
    if reason == "fuzzy_filename":
        return 0.80
    return 0.50


def _path_reconcile_candidate_tier(old_path: Path, new_path: Path, old_size, new_size) -> str:
    token_overlap = _path_audit_token_overlap(old_path, new_path)
    size_diff_pct = _path_audit_size_diff_pct(old_size, new_size)
    if size_diff_pct is None:
        size_similarity = 0.0
    else:
        size_similarity = max(0.0, 1.0 - min(size_diff_pct, 1.0))
    same_extension = old_path.suffix.lower() == new_path.suffix.lower()
    score = (token_overlap * 0.60) + (size_similarity * 0.30) + (0.10 if same_extension else 0.0)
    return _path_audit_orphan_candidate_tier(
        round(score, 6),
        token_overlap,
        size_diff_pct,
        same_extension,
        old_path,
        new_path,
    )


def _path_reconcile_plan(root: Path, audit: dict) -> dict:
    from datetime import datetime, timezone

    actions: list[dict] = []
    rename_by_old_path: dict[str, dict] = {}

    for item in audit.get("possible_renames", []):
        old_path = item.get("db_row", {}).get("filepath", "")
        match = _path_reconcile_best_match(item.get("matches", []))
        if not old_path or match is None:
            continue
        reason = match.get("reason", "unknown")
        new_path = match.get("path")
        review_tier = _path_reconcile_candidate_tier(
            Path(old_path),
            Path(new_path),
            item.get("db_row", {}).get("filesize_bytes"),
            match.get("size"),
        ) if new_path else "REVIEW_CAREFULLY"
        action = {
            "action": "update_path_reference",
            "old_path": old_path,
            "new_path": new_path,
            "confidence": _path_reconcile_confidence(reason),
            "reason": reason,
            "risk": "LOW" if review_tier == "AUTO_SAFE_CANDIDATE" else "REVIEW_REQUIRED",
            "review_tier": review_tier,
        }
        actions.append(action)
        rename_by_old_path[old_path] = action

    for item in audit.get("relocation_candidates", []):
        old_path = item.get("old_path", "")
        new_path = item.get("new_path")
        if not old_path or not new_path:
            continue
        action = {
            "action": "update_path_reference",
            "old_path": old_path,
            "new_path": new_path,
            "confidence": 0.65,
            "reason": "relocation",
            "risk": "REVIEW_REQUIRED",
            "review_tier": "REVIEW_CAREFULLY",
            "token_overlap": item.get("token_overlap"),
            "size_diff_pct": item.get("size_diff_pct"),
        }
        actions.append(action)
        rename_by_old_path[old_path] = action

    for item in audit.get("orphan_candidates", []):
        if item.get("review_tier") != "AUTO_SAFE_CANDIDATE":
            continue
        old_path = item.get("old_path", "")
        new_path = item.get("candidate_path")
        if not old_path or not new_path:
            continue
        action = {
            "action": "update_path_reference",
            "old_path": old_path,
            "new_path": new_path,
            "confidence": item.get("score", 0.95),
            "reason": "orphan_auto_safe_candidate",
            "risk": "LOW",
            "review_tier": "AUTO_SAFE_CANDIDATE",
            "token_overlap": item.get("token_overlap"),
            "size_diff_pct": item.get("size_diff_pct"),
        }
        actions.append(action)
        rename_by_old_path[old_path] = action

    for entry in audit.get("stale_queue_entries", []):
        old_path = entry.get("path", "")
        candidate = rename_by_old_path.get(old_path)
        if candidate:
            actions.append({
                "action": "update_queue_reference",
                "queue_file": entry.get("queue_file"),
                "old_path": old_path,
                "new_path": candidate["new_path"],
                "confidence": candidate["confidence"],
                "reason": "candidate_found_from_path_audit",
                "risk": candidate.get("risk", "LOW"),
                "unresolved": False,
            })
        else:
            actions.append({
                "action": "update_queue_reference",
                "queue_file": entry.get("queue_file"),
                "old_path": old_path,
                "new_path": None,
                "confidence": 0.0,
                "reason": "unresolved_no_candidate",
                "risk": "REVIEW_REQUIRED",
                "unresolved": True,
            })

    for row in audit.get("orphan_db_rows", []):
        actions.append({
            "action": "mark_orphan_candidate",
            "old_path": row.get("filepath"),
            "reason": "missing_file_no_rename_candidate",
            "risk": "REVIEW_REQUIRED",
        })

    for duplicate in audit.get("duplicate_db_entries", []):
        actions.append({
            "action": "investigate_duplicate_path",
            "filepath": duplicate.get("filepath"),
            "count": duplicate.get("count"),
            "row_ids": duplicate.get("row_ids", []),
            "risk": "REVIEW_REQUIRED",
        })

    for row in audit.get("stale_processed_state_rows", []):
        actions.append({
            "action": "mark_stale_processed_state_path",
            "old_path": row.get("old_path"),
            "replacement_path": row.get("replacement_path"),
            "stage": row.get("stage"),
            "reason": row.get("reason", "superseded_by_existing_path"),
            "source_rows": row.get("source_rows", []),
            "risk": "LOW",
            "report_only": True,
        })

    summary: dict[str, int] = {}
    for action in actions:
        key = action["action"]
        summary[key] = summary.get(key, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "database": audit.get("database"),
        "dry_run": True,
        "apply_supported": False,
        "audit_summary": audit.get("summary", {}),
        "audit_findings": {
            "missing_files": audit.get("missing_files", []),
            "mixed_root_db_paths": audit.get("mixed_root_db_paths", []),
            "possible_renames": audit.get("possible_renames", []),
            "relocation_candidates": audit.get("relocation_candidates", []),
            "duplicate_db_entries": audit.get("duplicate_db_entries", []),
            "stale_queue_entries": audit.get("stale_queue_entries", []),
            "stale_processed_state_rows": audit.get("stale_processed_state_rows", []),
            "orphan_db_rows": audit.get("orphan_db_rows", []),
        },
        "planned_action_summary": summary,
        "planned_actions": actions,
        "limitations": [
            "plan only; no database, queue, file, or tag updates are implemented",
            "rename candidates come from path-audit heuristics only",
            "queue updates are unresolved unless the queue path exactly matches a rename old_path",
        ],
    }


def _path_reconcile_write_text_plan(plan: dict, text_path: Path) -> None:
    lines = [
        "path-reconcile DRY-RUN PLAN",
        f"root={plan['root']}",
        f"database={plan['database']}",
        "",
        "Audit summary:",
    ]
    for key, value in plan.get("audit_summary", {}).items():
        lines.append(f"  {key}: {value}")
    lines.extend(["", "Planned actions:"])
    for action in plan.get("planned_actions", []):
        kind = action.get("action")
        if kind == "update_path_reference":
            lines.append(
                "  update_path_reference "
                f"{action.get('old_path')} -> {action.get('new_path')} "
                f"confidence={action.get('confidence')} reason={action.get('reason')}"
            )
        elif kind == "update_queue_reference":
            lines.append(
                "  update_queue_reference "
                f"{action.get('old_path')} -> {action.get('new_path')} "
                f"queue={action.get('queue_file')} unresolved={action.get('unresolved')}"
            )
        elif kind == "mark_orphan_candidate":
            lines.append(
                f"  mark_orphan_candidate {action.get('old_path')} "
                f"reason={action.get('reason')}"
            )
        elif kind == "investigate_duplicate_path":
            lines.append(
                f"  investigate_duplicate_path {action.get('filepath')} "
                f"count={action.get('count')}"
            )
        elif kind == "mark_stale_processed_state_path":
            lines.append(
                "  mark_stale_processed_state_path "
                f"{action.get('old_path')} -> {action.get('replacement_path')} "
                f"stage={action.get('stage')} reason={action.get('reason')}"
            )
        else:
            lines.append(f"  {kind}: {action}")
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _path_reconcile_write_csv_plan(plan: dict, csv_path: Path) -> None:
    import csv

    fieldnames = ["action", "confidence", "reason", "old_path", "new_path", "risk"]
    rows = []
    for action in plan.get("planned_actions", []):
        rows.append({
            "action": action.get("action"),
            "confidence": action.get("confidence", ""),
            "reason": action.get("reason", ""),
            "old_path": action.get("old_path") or action.get("filepath", ""),
            "new_path": action.get("new_path", ""),
            "risk": action.get("risk", ""),
        })
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _path_reconcile_print_summary(plan: dict, json_path: Path) -> None:
    print("\n=== path-reconcile DRY-RUN PLAN ===\n")
    print(f"  Root                  : {plan['root']}")
    print(f"  Database              : {plan['database']}")
    for action, count in sorted(plan.get("planned_action_summary", {}).items()):
        print(f"  {action:<22}: {count}")
    print(f"\n  JSON plan             : {json_path}")
    print("  Apply                 : --apply not implemented; --apply-auto-safe-only available")


def _path_reconcile_apply_auto_safe(root: Path, db_path: Path, plan: dict) -> dict:
    import sqlite3
    from collections import Counter

    actions = [
        action for action in plan.get("planned_actions", [])
        if action.get("action") == "update_path_reference"
        and action.get("review_tier") == "AUTO_SAFE_CANDIDATE"
    ]
    old_path_counts = Counter(action.get("old_path") for action in actions)
    result = {
        "total_candidates": len(actions),
        "applied_count": 0,
        "rows_updated": 0,
        "skipped_count": 0,
        "skipped": [],
        "applied": [],
    }

    if not db_path.exists():
        for action in actions:
            result["skipped_count"] += 1
            result["skipped"].append({
                "old_path": action.get("old_path"),
                "new_path": action.get("new_path"),
                "reason": "processed_db_not_found",
            })
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_state'"
        ).fetchone()
        if table is None:
            for action in actions:
                result["skipped_count"] += 1
                result["skipped"].append({
                    "old_path": action.get("old_path"),
                    "new_path": action.get("new_path"),
                    "reason": "processed_state_table_missing",
                })
            return result

        conn.execute("BEGIN")
        for action in actions:
            old_path_raw = action.get("old_path")
            new_path_raw = action.get("new_path")
            skip_reason = None
            try:
                if not old_path_raw or not new_path_raw:
                    skip_reason = "missing_path_in_action"
                elif old_path_counts[old_path_raw] > 1:
                    skip_reason = "multiple_candidate_matches_for_old_path"
                else:
                    old_path = str(assert_path_under_root(old_path_raw, root))
                    new_path = str(assert_path_under_root(new_path_raw, root))
                    if not Path(new_path).exists():
                        skip_reason = "new_path_missing_on_disk"
                    else:
                        old_rows = conn.execute(
                            "SELECT DISTINCT stage FROM processed_state WHERE filepath = ?",
                            (old_path,),
                        ).fetchall()
                        if not old_rows:
                            skip_reason = "old_path_not_in_processed_state"
                        else:
                            conflict = None
                            for row in old_rows:
                                stage = row["stage"]
                                exists = conn.execute(
                                    "SELECT 1 FROM processed_state WHERE filepath = ? AND stage = ? LIMIT 1",
                                    (new_path, stage),
                                ).fetchone()
                                if exists:
                                    conflict = stage
                                    break
                            if conflict:
                                skip_reason = f"new_path_already_exists_in_same_stage:{conflict}"
                            else:
                                cursor = conn.execute(
                                    "UPDATE processed_state SET filepath = ? WHERE filepath = ?",
                                    (new_path, old_path),
                                )
                                result["applied_count"] += 1
                                result["rows_updated"] += cursor.rowcount
                                result["applied"].append({
                                    "old_path": old_path,
                                    "new_path": new_path,
                                    "rows_updated": cursor.rowcount,
                                })
            except ValueError as exc:
                skip_reason = f"path_outside_root:{exc}"

            if skip_reason:
                result["skipped_count"] += 1
                result["skipped"].append({
                    "old_path": old_path_raw,
                    "new_path": new_path_raw,
                    "reason": skip_reason,
                })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return result


def _path_reconcile_write_apply_auto_safe_log(result: dict, log_path: Path) -> None:
    lines = [
        "path-reconcile apply-auto-safe-only",
        f"total_candidates={result.get('total_candidates', 0)}",
        f"applied_count={result.get('applied_count', 0)}",
        f"rows_updated={result.get('rows_updated', 0)}",
        f"skipped_count={result.get('skipped_count', 0)}",
        "",
        "Applied:",
    ]
    for item in result.get("applied", []):
        lines.append(
            f"  {item.get('old_path')} -> {item.get('new_path')} "
            f"rows_updated={item.get('rows_updated')}"
        )
    lines.append("")
    lines.append("Skipped:")
    for item in result.get("skipped", []):
        lines.append(
            f"  {item.get('old_path')} -> {item.get('new_path')} "
            f"reason={item.get('reason')}"
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _path_reconcile_print_apply_auto_safe_summary(result: dict, log_path: Path) -> None:
    print("\n=== path-reconcile APPLY AUTO-SAFE ONLY ===\n")
    print(f"  Total candidates      : {result.get('total_candidates', 0)}")
    print(f"  Applied               : {result.get('applied_count', 0)}")
    print(f"  Rows updated          : {result.get('rows_updated', 0)}")
    print(f"  Skipped               : {result.get('skipped_count', 0)}")
    print(f"\n  Apply log             : {log_path}")


def _path_reconcile_mark_stale_pstate(root: Path, db_path: Path, plan: dict) -> dict:
    import sqlite3

    actions = [
        action for action in plan.get("planned_actions", [])
        if action.get("action") == "mark_stale_processed_state_path"
    ]
    result = {
        "total_candidates": len(actions),
        "marked_count": 0,
        "rows_updated": 0,
        "skipped_count": 0,
        "marked": [],
        "skipped": [],
    }

    if not db_path.exists():
        for action in actions:
            result["skipped_count"] += 1
            result["skipped"].append({
                "old_path": action.get("old_path"),
                "replacement_path": action.get("replacement_path"),
                "reason": "processed_db_not_found",
            })
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_state'"
        ).fetchone()
        if table is None:
            for action in actions:
                result["skipped_count"] += 1
                result["skipped"].append({
                    "old_path": action.get("old_path"),
                    "replacement_path": action.get("replacement_path"),
                    "reason": "processed_state_table_missing",
                })
            return result

        conn.execute("BEGIN")
        for action in actions:
            old_path_raw = action.get("old_path")
            replacement_raw = action.get("replacement_path")
            source_rows = [
                row for row in action.get("source_rows", [])
                if row.get("table") == "processed_state" and row.get("id") is not None
            ]
            skip_reason = None
            row_id = None
            try:
                if not source_rows:
                    skip_reason = "missing_processed_state_row_id"
                elif not old_path_raw or not replacement_raw:
                    skip_reason = "missing_path_in_action"
                else:
                    row_id = source_rows[0]["id"]
                    old_path = str(assert_path_under_root(old_path_raw, root))
                    replacement_path = str(assert_path_under_root(replacement_raw, root))
                    if Path(old_path).exists():
                        skip_reason = "old_path_exists_on_disk"
                    elif not Path(replacement_path).exists():
                        skip_reason = "replacement_path_missing_on_disk"
                    else:
                        stale_row = conn.execute(
                            "SELECT id, filepath FROM processed_state WHERE id = ? AND filepath = ?",
                            (row_id, old_path),
                        ).fetchone()
                        if stale_row is None:
                            skip_reason = "processed_state_id_path_mismatch"
                        else:
                            replacement_row = conn.execute(
                                "SELECT 1 FROM processed_state WHERE filepath = ? LIMIT 1",
                                (replacement_path,),
                            ).fetchone()
                            if replacement_row is None:
                                skip_reason = "replacement_path_not_in_processed_state"
                            else:
                                reason = f"superseded_by_existing_path:{replacement_path}"
                                cursor = conn.execute(
                                    "UPDATE processed_state "
                                    "SET status = ?, reason = ? "
                                    "WHERE id = ? AND filepath = ?",
                                    ("stale", reason, row_id, old_path),
                                )
                                result["marked_count"] += 1
                                result["rows_updated"] += cursor.rowcount
                                result["marked"].append({
                                    "id": row_id,
                                    "old_path": old_path,
                                    "replacement_path": replacement_path,
                                    "rows_updated": cursor.rowcount,
                                })
            except ValueError as exc:
                skip_reason = f"path_outside_root:{exc}"

            if skip_reason:
                result["skipped_count"] += 1
                result["skipped"].append({
                    "id": row_id,
                    "old_path": old_path_raw,
                    "replacement_path": replacement_raw,
                    "reason": skip_reason,
                })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return result


def _path_reconcile_write_mark_stale_pstate_log(result: dict, log_path: Path) -> None:
    lines = [
        "path-reconcile mark-stale-pstate",
        f"total_candidates={result.get('total_candidates', 0)}",
        f"marked_count={result.get('marked_count', 0)}",
        f"rows_updated={result.get('rows_updated', 0)}",
        f"skipped_count={result.get('skipped_count', 0)}",
        "",
        "Marked:",
    ]
    for item in result.get("marked", []):
        lines.append(
            f"  id={item.get('id')} {item.get('old_path')} "
            f"replacement={item.get('replacement_path')} "
            f"rows_updated={item.get('rows_updated')}"
        )
    lines.append("")
    lines.append("Skipped:")
    for item in result.get("skipped", []):
        lines.append(
            f"  id={item.get('id')} {item.get('old_path')} "
            f"replacement={item.get('replacement_path')} reason={item.get('reason')}"
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _path_reconcile_print_mark_stale_pstate_summary(result: dict, log_path: Path) -> None:
    print("\n=== path-reconcile MARK STALE PSTATE ===\n")
    print(f"  Total candidates      : {result.get('total_candidates', 0)}")
    print(f"  Marked                : {result.get('marked_count', 0)}")
    print(f"  Rows updated          : {result.get('rows_updated', 0)}")
    print(f"  Skipped               : {result.get('skipped_count', 0)}")
    print(f"\n  Apply log             : {log_path}")


def _path_reconcile_print_ledger_summary(rows: list[dict]) -> None:
    print("\n=== path-reconcile LEDGER ===\n")
    if not rows:
        print("  No reconciliation ledger entries found.")
        return

    headers = ["ledger_id", "created_at", "operation_type", "status", "root", "affected_tables"]
    widths = {name: len(name) for name in headers}
    normalized_rows: list[dict[str, str]] = []
    for row in rows:
        normalized = {key: str(row.get(key) or "") for key in headers}
        normalized_rows.append(normalized)
        for key, value in normalized.items():
            widths[key] = max(widths[key], len(value))

    for header in headers:
        print(f"  {header:<{widths[header]}}", end="  ")
    print()
    for header in headers:
        print(f"  {'-' * widths[header]}", end="  ")
    print()
    for row in normalized_rows:
        for header in headers:
            print(f"  {row[header]:<{widths[header]}}", end="  ")
        print()


def _path_reconcile_verify_ledger_entry(row: dict) -> dict:
    import json

    issues: list[str] = []
    normalized: dict[str, object] = dict(row)

    for field in ("ledger_id", "created_at", "operation_type", "status"):
        if not str(row.get(field) or "").strip():
            issues.append(f"missing_required_field:{field}")

    root = str(row.get("root") or "").strip()
    if root and not Path(root).expanduser().exists():
        issues.append(f"missing_root_path:{root}")

    for field in ("old_path", "new_path"):
        value = str(row.get(field) or "").strip()
        if value and not Path(value).expanduser().exists():
            issues.append(f"missing_referenced_path:{field}:{value}")

    affected_raw = str(row.get("affected_tables") or "").strip()
    affected_tables: list[str] = []
    if affected_raw:
        try:
            parsed = json.loads(affected_raw)
            if isinstance(parsed, list):
                affected_tables = [str(item) for item in parsed if str(item).strip()]
            elif isinstance(parsed, str):
                affected_tables = [parsed]
            else:
                issues.append("affected_tables_not_list")
        except Exception:
            affected_tables = [item.strip() for item in affected_raw.split(",") if item.strip()]
    else:
        issues.append("missing_affected_tables")

    before_raw = str(row.get("before_values_json") or "").strip()
    after_raw = str(row.get("after_values_json") or "").strip()
    for field, raw in (("before_values_json", before_raw), ("after_values_json", after_raw)):
        if not raw:
            issues.append(f"missing_{field}")
            continue
        try:
            normalized[field] = json.loads(raw)
        except Exception:
            issues.append(f"invalid_json:{field}")

    if not affected_tables:
        issues.append("empty_affected_tables")

    normalized["affected_tables"] = affected_tables
    normalized["issues"] = issues
    normalized["ok"] = not issues
    return normalized


def _path_reconcile_print_verify_ledger(result: dict) -> None:
    print("\n=== path-reconcile VERIFY LEDGER ===\n")
    print(f"  Ledger ID             : {result.get('ledger_id')}")
    print(f"  Status                : {'OK' if result.get('ok') else 'ISSUES'}")
    print(f"  Root                  : {result.get('root')}")
    print(f"  Operation type        : {result.get('operation_type')}")
    print(f"  Affected tables       : {', '.join(result.get('affected_tables', [])) or '—'}")
    print(f"  Old path              : {result.get('old_path')}")
    print(f"  New path              : {result.get('new_path')}")
    if result.get("issues"):
        print("  Issues:")
        for issue in result["issues"]:
            print(f"    - {issue}")
    else:
        print("  Issues                : none")


def _path_reconcile_plan_review_state_candidates(plan_path: Path) -> list[Path]:
    candidates = [
        plan_path.with_name(f"{plan_path.stem}_review_state.json"),
    ]
    try:
        root = plan_path.parent.parent.parent
        candidates.append(root / "data" / "intelligence" / "path_reconcile_review_state.json")
    except Exception:
        pass
    return candidates


def _path_reconcile_load_review_state(plan_path: Path) -> dict:
    import json

    for candidate in _path_reconcile_plan_review_state_candidates(plan_path):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _path_reconcile_action_is_approved(action: dict, plan_path: Path) -> bool:
    review_state = _path_reconcile_load_review_state(plan_path)

    for field in ("approved", "is_approved"):
        if action.get(field) is True:
            return True
    if str(action.get("review_status") or "").lower() == "approved":
        return True
    if str(action.get("approval_status") or "").lower() == "approved":
        return True

    approvals = review_state.get("approved_actions")
    if isinstance(approvals, list):
        for entry in approvals:
            if isinstance(entry, str):
                if entry == action.get("action_id") or entry == action.get("ledger_id"):
                    return True
            elif isinstance(entry, dict):
                keys = (
                    ("action_id", "action_id"),
                    ("ledger_id", "ledger_id"),
                    ("action", "action"),
                    ("old_path", "old_path"),
                    ("new_path", "new_path"),
                )
                if all(
                    entry.get(entry_key) == action.get(action_key)
                    for action_key, entry_key in keys
                    if action.get(action_key) not in (None, "")
                ):
                    return True

    items = review_state.get("items")
    if isinstance(items, dict):
        for key, value in items.items():
            if not isinstance(value, dict):
                continue
            if str(value.get("review_status") or "").lower() != "approved":
                continue
            signature = _path_reconcile_action_signature(action)
            if key == signature:
                return True
            if str(value.get("action_id") or "") == str(action.get("action_id") or ""):
                return True
            if value.get("old_path") == action.get("old_path") and value.get("new_path") == action.get("new_path"):
                return True
    return False


def _path_reconcile_action_signature(action: dict) -> str:
    return "|".join(
        [
            str(action.get("action") or ""),
            str(action.get("old_path") or ""),
            str(action.get("new_path") or ""),
            str(action.get("queue_file") or ""),
        ]
    )


def _path_reconcile_canonical_paths(root: Path) -> set[str]:
    db_path = _path_audit_db_path(root)
    if not db_path.exists():
        return set()
    try:
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            paths: set[str] = set()
            for table in ("tracks", "processed_state"):
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if exists is None:
                    continue
                for row in conn.execute(f"SELECT filepath FROM {table} WHERE filepath IS NOT NULL"):
                    raw = str(row["filepath"] or "")
                    if raw:
                        paths.add(str(Path(raw).expanduser().resolve(strict=False)))
                        paths.add(raw)
            return paths
        finally:
            conn.close()
    except Exception:
        return set()


def _path_reconcile_validate_action(
    action: dict,
    *,
    plan_path: Path,
    root: Path,
    canonical_paths: set[str],
) -> dict:
    import json

    issues: list[str] = []
    warnings: list[str] = []
    action_type = str(action.get("action") or "").strip()
    old_path = str(action.get("old_path") or "").strip()
    new_path = str(action.get("new_path") or "").strip()
    review_tier = str(action.get("review_tier") or "").strip()
    risk = str(action.get("risk") or "").strip()
    confidence = action.get("confidence")

    allowed_actions = {
        "update_path_reference",
        "update_queue_reference",
        "mark_orphan_candidate",
        "investigate_duplicate_path",
        "mark_stale_processed_state_path",
    }
    report_only_actions = {
        "mark_orphan_candidate",
        "investigate_duplicate_path",
        "mark_stale_processed_state_path",
    }

    if not action_type:
        issues.append("missing_action_type")
    elif action_type not in allowed_actions:
        issues.append(f"unsupported_action_type:{action_type}")

    if action_type in report_only_actions or action.get("report_only") is True:
        return {
            "action": action,
            "action_type": action_type,
            "status": "skipped",
            "reason": "report_only",
            "issues": [],
            "warnings": warnings,
        }

    if review_tier == "WEAK_MATCH":
        issues.append("weak_match_rejected")

    if review_tier == "REVIEW_CAREFULLY" and risk not in {"REVIEW_REQUIRED", "LOW"}:
        issues.append(f"invalid_risk_for_review_tier:{risk or 'missing'}")

    if risk == "REVIEW_REQUIRED" and not _path_reconcile_action_is_approved(action, plan_path):
        issues.append("review_required_not_approved")

    if action_type == "update_path_reference":
        if not old_path:
            issues.append("missing_old_path")
        if not new_path:
            issues.append("missing_new_path")
        # old_path is expected to be missing on disk: this action type exists
        # specifically to relink a DB row whose file is gone to a candidate
        # found elsewhere (see _path_reconcile_plan). Only the candidate
        # (new_path) must actually exist for the action to be applicable.
        if new_path and not Path(new_path).expanduser().exists():
            issues.append("new_path_missing_on_disk")
    elif action_type == "update_queue_reference":
        if not old_path:
            issues.append("missing_old_path")
        if action.get("unresolved") is True or not new_path:
            warnings.append("queue_reference_unresolved")
        elif not Path(new_path).expanduser().exists():
            issues.append("new_path_missing_on_disk")
    elif action_type == "mark_stale_processed_state_path":
        if not old_path:
            issues.append("missing_old_path")
    elif action_type in {"mark_orphan_candidate", "investigate_duplicate_path"}:
        if not old_path and not str(action.get("filepath") or "").strip():
            issues.append("missing_reference_path")

    if old_path:
        resolved_old = str(Path(old_path).expanduser().resolve(strict=False))
        if resolved_old not in canonical_paths and old_path not in canonical_paths:
            issues.append("old_path_not_in_canonical_db")
        try:
            resolved_root = Path(old_path).expanduser().resolve(strict=False)
            resolved_root.relative_to(root)
        except Exception:
            issues.append("old_path_outside_root")

    if new_path:
        try:
            resolved_new = Path(new_path).expanduser().resolve(strict=False)
            resolved_new.relative_to(root)
        except Exception:
            issues.append("new_path_outside_root")

    if confidence is not None:
        try:
            conf = float(confidence)
            if not 0.0 <= conf <= 1.0:
                issues.append("confidence_out_of_range")
        except Exception:
            issues.append("confidence_not_numeric")

    if review_tier and review_tier not in {"AUTO_SAFE_CANDIDATE", "REVIEW_CAREFULLY", "WEAK_MATCH"}:
        warnings.append(f"unexpected_review_tier:{review_tier}")
    if risk and risk not in {"LOW", "REVIEW_REQUIRED"}:
        warnings.append(f"unexpected_risk:{risk}")

    status = "valid" if not issues else "invalid"
    return {
        "action": action,
        "action_type": action_type,
        "status": status,
        "reason": None if status == "valid" else issues[0],
        "issues": issues,
        "warnings": warnings,
    }


def _path_reconcile_validate_plan(plan_path: Path) -> dict:
    import json
    from datetime import datetime, timezone

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("plan json must be an object")

    plan_root_raw = str(plan.get("root") or "").strip()
    if plan_root_raw:
        root = Path(plan_root_raw).expanduser().resolve()
    else:
        root = plan_path.parent.parent.parent.resolve()
    if not root.exists():
        raise ValueError(f"plan root does not exist: {root}")

    planned_actions = plan.get("planned_actions")
    if not isinstance(planned_actions, list):
        raise ValueError("plan json missing planned_actions list")

    canonical_paths = _path_reconcile_canonical_paths(root)
    validation_records: list[dict] = []
    reasons: dict[str, int] = {}
    totals = {"valid": 0, "invalid": 0, "skipped": 0}

    for action in planned_actions:
        if not isinstance(action, dict):
            record = {
                "action": action,
                "status": "invalid",
                "reason": "action_not_object",
                "issues": ["action_not_object"],
                "warnings": [],
            }
        else:
            record = _path_reconcile_validate_action(
                action,
                plan_path=plan_path,
                root=root,
                canonical_paths=canonical_paths,
            )
        status = record["status"]
        totals[status] = totals.get(status, 0) + 1
        if status != "valid":
            for issue in record.get("issues", []):
                reasons[issue] = reasons.get(issue, 0) + 1
        validation_records.append(record)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan_path": str(plan_path),
        "root": str(root),
        "total_actions": len(planned_actions),
        "valid_actions": totals.get("valid", 0),
        "invalid_actions": totals.get("invalid", 0),
        "skipped_actions": totals.get("skipped", 0),
        "reasons": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        "validation_records": validation_records,
    }
    return result


def _path_reconcile_write_validation_result(root: Path, result: dict) -> Path:
    from datetime import datetime

    log_dir = root / "logs" / "path_reconcile"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    path = log_dir / f"{day}_validate_plan.json"
    path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _path_reconcile_latest_plan_path(root: Path) -> Path | None:
    log_dir = root / "logs" / "path_reconcile"
    if not log_dir.exists():
        return None
    candidates = sorted(log_dir.glob("*_path_reconcile_plan.json"))
    return candidates[-1] if candidates else None


def _path_reconcile_print_validate_summary(result: dict, output_path: Path) -> None:
    print("\n=== path-reconcile VALIDATE PLAN ===\n")
    print(f"  Plan path             : {result.get('plan_path')}")
    print(f"  Root                  : {result.get('root')}")
    print(f"  Total actions         : {result.get('total_actions', 0)}")
    print(f"  Valid actions         : {result.get('valid_actions', 0)}")
    print(f"  Invalid actions       : {result.get('invalid_actions', 0)}")
    print(f"  Skipped actions       : {result.get('skipped_actions', 0)}")
    print(f"  Validation JSON       : {output_path}")
    if result.get("reasons"):
        print("  Reasons:")
        for reason, count in result["reasons"].items():
            print(f"    - {reason}: {count}")


def run_path_reconcile(args) -> int:
    import json
    from datetime import datetime

    ledger_mode = getattr(args, "ledger", False)
    verify_ledger = getattr(args, "verify_ledger", None)
    validate_plan = getattr(args, "validate_plan", None)
    if ledger_mode and verify_ledger:
        print("path-reconcile --ledger cannot be combined with --verify-ledger", file=sys.stderr)
        return 2
    if validate_plan and (ledger_mode or verify_ledger):
        print("path-reconcile --validate-plan cannot be combined with ledger modes", file=sys.stderr)
        return 2
    if ledger_mode or verify_ledger:
        if getattr(args, "apply", False) or getattr(args, "apply_auto_safe_only", False) or getattr(args, "mark_stale_pstate", False) or getattr(args, "dry_run", False):
            print("path-reconcile ledger modes are read-only and cannot be combined with apply or dry-run flags", file=sys.stderr)
            return 2
        if ledger_mode:
            rows = [dict(row) for row in db.list_reconciliation_ledger()]
            _path_reconcile_print_ledger_summary(rows)
            return 0
        row = db.get_reconciliation_ledger(str(verify_ledger))
        if row is None:
            print(f"ERROR: reconciliation ledger entry not found: {verify_ledger}", file=sys.stderr)
            return 1
        result = _path_reconcile_verify_ledger_entry(dict(row))
        _path_reconcile_print_verify_ledger(result)
        return 0 if result.get("ok") else 1

    if validate_plan:
        try:
            plan_path = Path(validate_plan).expanduser().resolve()
        except Exception as exc:
            print(f"ERROR: invalid plan path: {exc}", file=sys.stderr)
            return 2
        if not plan_path.exists():
            print(f"ERROR: plan json does not exist: {plan_path}", file=sys.stderr)
            return 2
        try:
            result = _path_reconcile_validate_plan(plan_path)
        except Exception as exc:
            print(f"ERROR: failed to validate plan: {exc}", file=sys.stderr)
            return 1
        output_path = _path_reconcile_write_validation_result(Path(result["root"]), result)
        _path_reconcile_print_validate_summary(result, output_path)
        return 0 if result.get("invalid_actions", 0) == 0 else 1

    if getattr(args, "apply", False):
        print("path-reconcile --apply is not implemented yet", file=sys.stderr)
        return 2
    apply_auto_safe = getattr(args, "apply_auto_safe_only", False)
    mark_stale_pstate = getattr(args, "mark_stale_pstate", False)
    if not getattr(args, "dry_run", False) and not apply_auto_safe and not mark_stale_pstate:
        print("path-reconcile requires --dry-run", file=sys.stderr)
        return 2

    try:
        root = resolve_library_root(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    db_path = _path_audit_db_path(root)
    audit = _path_audit_report(
        root,
        db_path,
        include_orphan_candidates=apply_auto_safe,
    )
    audit["summary"]["stale_queue_entries"] = len(audit["stale_queue_entries"])
    plan = _path_reconcile_plan(root, audit)

    log_dir = root / "logs" / "path_reconcile"
    log_dir.mkdir(parents=True, exist_ok=True)
    if apply_auto_safe:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"{stamp}_apply_auto_safe.log"
        try:
            result = _path_reconcile_apply_auto_safe(root, db_path, plan)
        except Exception as exc:
            result = {
                "total_candidates": 0,
                "applied_count": 0,
                "rows_updated": 0,
                "skipped_count": 0,
                "skipped": [{"old_path": "", "new_path": "", "reason": f"rolled_back:{exc}"}],
                "applied": [],
            }
            _path_reconcile_write_apply_auto_safe_log(result, log_path)
            print(f"ERROR: path-reconcile apply-auto-safe-only rolled back: {exc}", file=sys.stderr)
            return 1
        _path_reconcile_write_apply_auto_safe_log(result, log_path)
        _path_reconcile_print_apply_auto_safe_summary(result, log_path)
        return 0
    if mark_stale_pstate:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = log_dir / f"{stamp}_mark_stale_pstate.log"
        try:
            result = _path_reconcile_mark_stale_pstate(root, db_path, plan)
        except Exception as exc:
            result = {
                "total_candidates": 0,
                "marked_count": 0,
                "rows_updated": 0,
                "skipped_count": 0,
                "marked": [],
                "skipped": [{"id": None, "old_path": "", "replacement_path": "", "reason": f"rolled_back:{exc}"}],
            }
            _path_reconcile_write_mark_stale_pstate_log(result, log_path)
            print(f"ERROR: path-reconcile mark-stale-pstate rolled back: {exc}", file=sys.stderr)
            return 1
        _path_reconcile_write_mark_stale_pstate_log(result, log_path)
        _path_reconcile_print_mark_stale_pstate_summary(result, log_path)
        return 0

    day = datetime.now().strftime("%Y%m%d")
    json_path = log_dir / f"{day}_path_reconcile_plan.json"
    text_path = log_dir / f"{day}_path_reconcile_plan.txt"
    csv_path = log_dir / f"{day}_path_reconcile_plan.csv"

    json_path.write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _path_reconcile_write_text_plan(plan, text_path)
    _path_reconcile_write_csv_plan(plan, csv_path)
    _path_reconcile_print_summary(plan, json_path)
    return 0


# ---------------------------------------------------------------------------
# Standalone playlist generation
# ---------------------------------------------------------------------------
def run_playlists(args) -> int:
    """
    Generate all M3U playlists and Rekordbox XML from the current library DB.

    Runs outside the full pipeline — useful after manual library edits, after
    dedupe cleanup, or any time you want to refresh exports without re-processing
    the inbox.

    Modes:
      --dry-run     print what would be written, no files created
      (no flag)     write all playlist files and rekordbox_library.xml

    Subsets (default: all):
      --no-genre    skip Genre/ playlists
      --no-energy   skip Energy/ playlists
      --no-combined skip Combined/ playlists
      --no-key      skip Key/ playlists
      --no-route    skip Route/ playlists
      --no-xml      skip Rekordbox XML generation
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    if custom_path is not None:
        _override_music_root(custom_path)
        _log_active_path("PLAYLISTS", custom_path)

    dry_run = getattr(args, "dry_run", False)

    # Per-category toggles (command-line can disable individual categories)
    if getattr(args, "no_genre", False):
        config.GENERATE_GENRE_PLAYLISTS = False
    if getattr(args, "no_energy", False):
        config.GENERATE_ENERGY_PLAYLISTS = False
    if getattr(args, "no_combined", False):
        config.GENERATE_COMBINED_PLAYLISTS = False
    if getattr(args, "no_key", False):
        config.GENERATE_KEY_PLAYLISTS = False
    if getattr(args, "no_route", False):
        config.GENERATE_ROUTE_PLAYLISTS = False

    # Ensure output directories exist
    for d in [
        config.M3U_DIR, config.GENRE_M3U_DIR, config.ENERGY_M3U_DIR,
        config.COMBINED_M3U_DIR, config.KEY_M3U_DIR, config.ROUTE_M3U_DIR,
        config.XML_DIR,
    ]:
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)

    log_action(f"PLAYLISTS {'DRY-RUN' if dry_run else 'GENERATE'} START")

    playlists.generate_m3u(dry_run)
    playlists.generate_genre_m3u(dry_run)
    playlists.generate_energy_m3u(dry_run)
    playlists.generate_combined_m3u(dry_run)
    playlists.generate_key_m3u(dry_run)
    playlists.generate_route_m3u(dry_run)

    if not getattr(args, "no_xml", False):
        xml_path = playlists.generate_rekordbox_xml(dry_run)
        if not dry_run:
            log.info("Rekordbox XML: %s", xml_path)

    log_action(f"PLAYLISTS {'DRY-RUN' if dry_run else 'GENERATE'} DONE")
    return 0


# ---------------------------------------------------------------------------
# Cue Suggest
# ---------------------------------------------------------------------------
def run_cue_suggest(args) -> int:
    """
    Analyse tracks for cue points (intro / drop / outro) and store results
    in the database.  Optionally writes .cues.json sidecars per track.

    Modes:
      --dry-run   analyse and print cues, no DB writes or sidecars
      --apply     analyse + store in DB after confirmation
      (no flag)   dry-run preview
    """
    from modules import cue_suggest

    _setup_logging(getattr(args, "verbose", False))
    do_apply = _apply_mode_or_error(args)
    if do_apply is None:
        return 2
    dry_run = not do_apply
    if do_apply:
        db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    min_conf    = getattr(args, "min_confidence", config.CUE_SUGGEST_MIN_CONFIDENCE)

    if custom_path is not None:
        _log_active_path("CUE-SUGGEST", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("CUE-SUGGEST", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    track_filter = getattr(args, "track",         None)
    limit        = getattr(args, "limit",         None)
    fmt_raw      = getattr(args, "export_format", None)
    export_fmts  = None
    if fmt_raw:
        export_fmts = [f.strip().lower() for f in fmt_raw.split(",") if f.strip()]

    # Apply track filter and limit to the candidate list up front so that
    # the logged count and the actual iteration both reflect the restriction.
    if track_filter:
        paths = [
            p for p in paths
            if track_filter.lower() in f"{p.stem} {p.parent.name}".lower()
        ]
    if limit is not None:
        paths = paths[:limit]

    log.info(
        "cue-suggest: %d candidate(s)  dry_run=%s  min_conf=%.2f",
        len(paths), dry_run, min_conf,
    )
    log_action(f"CUE-SUGGEST {'DRY-RUN' if dry_run else 'START'}: {len(paths)} candidates")

    analysed, stored = cue_suggest.run(
        paths,
        dry_run        = dry_run,
        min_conf       = min_conf,
        export_formats = export_fmts,
    )

    log.info("cue-suggest complete: %d analysed, %d cues stored", analysed, stored)
    log_action(f"CUE-SUGGEST DONE: {analysed} analysed, {stored} cues stored")
    return 0


# ---------------------------------------------------------------------------
# Set Builder
# ---------------------------------------------------------------------------
def run_set_builder(args) -> int:
    """
    Build an energy-curve DJ set from the library database and export it as
    an M3U playlist + CSV summary.

    Phases: warmup → build → peak → release → outro
    """
    from modules import set_builder

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    dry_run              = getattr(args, "dry_run",               False)
    vibe                 = getattr(args, "vibe",                  "peak")
    duration             = getattr(args, "duration",              60)
    genre                = getattr(args, "genre",                 None)
    strategy             = getattr(args, "strategy",              "safest")
    structure            = getattr(args, "structure",              "full")
    max_bpm_jump         = getattr(args, "max_bpm_jump",          3.0)
    strict_harmonic      = getattr(args, "strict_harmonic",       True)
    artist_repeat_window = getattr(args, "artist_repeat_window",  3)
    name                 = getattr(args, "name",                  None)
    start_e              = getattr(args, "start_energy",          None)
    end_e                = getattr(args, "end_energy",             None)

    log.info(
        "set-builder: vibe=%s  structure=%s  duration=%dmin  genre=%s  strategy=%s  "
        "max_bpm_jump=%s  strict_harmonic=%s  artist_repeat_window=%d  dry_run=%s",
        vibe, structure, duration, genre or "any", strategy,
        max_bpm_jump, strict_harmonic, artist_repeat_window, dry_run,
    )

    count, m3u_path = set_builder.run(
        target_duration_min  = duration,
        genre_filter         = genre,
        vibe                 = vibe,
        start_energy         = start_e,
        end_energy           = end_e,
        strategy             = strategy,
        structure            = structure,
        max_bpm_jump         = max_bpm_jump,
        strict_harmonic      = strict_harmonic,
        artist_repeat_window = artist_repeat_window,
        name                 = name,
        dry_run              = dry_run,
    )

    if count == 0:
        log.warning("set-builder produced no tracks — is your DB populated?")
        return 1

    if not dry_run and m3u_path:
        log.info("Set playlist: %s", m3u_path)

    return 0


# ---------------------------------------------------------------------------
# Harmonic Suggest
# ---------------------------------------------------------------------------
def run_harmonic_suggest(args) -> int:
    """
    Suggest the best next tracks based on harmonic / BPM / energy compatibility.

    Two modes:
      --track PATH   suggest from a specific file already in the library
      --key K --bpm B  suggest from a virtual track (key + BPM only)
    """
    from modules import harmonic

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    track_path = getattr(args, "track",    None)
    key        = getattr(args, "key",      None)
    bpm        = getattr(args, "bpm",      None)
    strategy   = getattr(args, "strategy", "safest")
    top_n      = getattr(args, "top_n",    10)
    energy     = getattr(args, "energy",   None)
    genre      = getattr(args, "genre",    None)
    json_out   = getattr(args, "json",     False)
    dry_run    = getattr(args, "dry_run",  False)

    if not track_path and not (key and bpm):
        log.error(
            "harmonic-suggest: provide either --track PATH or both --key and --bpm."
        )
        return 2

    if track_path:
        # Track-based mode: look up the source track's metadata from the DB
        # so we can pass from_title / from_key / from_bpm to the table formatter.
        # (track lookup not yet implemented — placeholder values used until then)
        log.info("harmonic-suggest: from track %s  strategy=%s  top_n=%d",
                 Path(track_path).name, strategy, top_n)
        results = harmonic.suggest_next(
            from_filepath    = track_path,
            strategy         = strategy,
            top_n            = top_n,
        )
        from_title = Path(track_path).stem
        from_key   = key   or ""
        from_bpm   = float(bpm) if bpm else 0.0
    else:
        log.info("harmonic-suggest: key=%s  bpm=%s  strategy=%s  top_n=%d",
                 key, bpm, strategy, top_n)
        results = harmonic.suggest_by_key_bpm(
            key      = key,
            bpm      = float(bpm),
            energy   = energy,
            genre    = genre,
            strategy = strategy,
            top_n    = top_n,
        )
        from_title = "Manual Input"
        from_key   = key
        from_bpm   = float(bpm)

    if not results:
        log.warning("harmonic-suggest: no results — is your DB populated?")
        return 0

    print(harmonic.format_suggestions_table(
        results, strategy=strategy,
        from_title=from_title, from_key=from_key, from_bpm=from_bpm,
    ))

    if json_out and not dry_run:
        out_path = harmonic.write_suggestions_json(
            results,
            strategy    = strategy,
            from_path   = track_path or f"key={key} bpm={bpm}",
        )
        log.info("JSON output: %s", out_path)

    log_action(
        f"HARMONIC-SUGGEST DONE: {len(results)} suggestions  strategy={strategy}"
    )
    return 0


# ---------------------------------------------------------------------------
# Analyze Missing
# ---------------------------------------------------------------------------
def run_analyze_missing(args) -> int:
    """
    Scan the library for tracks missing BPM or Camelot key, run analysis on
    those tracks only, and write results back to the DB and audio file tags.
    """
    from modules import analyze_missing

    _setup_logging(getattr(args, "verbose", False))
    do_apply = _apply_mode_or_error(args)
    if do_apply is None:
        return 2
    dry_run = not do_apply
    if do_apply:
        db.init_db()

    raw_path = getattr(args, "path", None)
    path = _resolve_path(raw_path) if raw_path else None

    if path:
        _log_active_path("analyze-missing scope", path)

    raw_corrupt = getattr(args, "corrupt_dir", None)
    corrupt_base_dir = _resolve_path(raw_corrupt) if raw_corrupt else None

    return analyze_missing.run(
        path             = path,
        dry_run          = dry_run,
        limit            = getattr(args, "limit",             None),
        timeout_sec      = getattr(args, "timeout_sec",       None),
        min_confidence   = getattr(args, "min_confidence",    0.0),
        verbose          = getattr(args, "verbose",           False),
        per_file_timeout = getattr(args, "file_timeout_sec",  10.0),
        isolate_corrupt  = getattr(args, "isolate_corrupt",   True),
        corrupt_base_dir = corrupt_base_dir,
    )


# ---------------------------------------------------------------------------
# Rekordbox Export
# ---------------------------------------------------------------------------
def run_rekordbox_export(args) -> int:
    """
    Export the full library as a Rekordbox-ready package for Windows (M: drive).

    Outputs:
      _REKORDBOX_XML_EXPORT/rekordbox_library.xml  — Rekordbox-importable XML
      _REKORDBOX_XML_EXPORT/export_report.txt       — tag validation warnings
      _PLAYLISTS_M3U_EXPORT/Genre/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Energy/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Combined/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Key/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Route/*.m3u8

    Path mapping:
      Linux  (RB_LINUX_ROOT)    /mnt/music_ssd/
      Windows (RB_WINDOWS_DRIVE) M:\\
    """
    from modules import rekordbox_export

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    dry_run              = getattr(args, "dry_run",                 False)
    skip_m3u             = getattr(args, "no_m3u",                  False)
    recover_missing      = getattr(args, "recover_missing_analysis", False)

    # MIK-first: Rekordbox XML is owned by Rekordbox + Mixed In Key.
    # XML export is DISABLED by default to prevent accidental data loss.
    # Use --force-xml to override.
    force_xml = getattr(args, "force_xml", False)
    skip_xml  = not force_xml
    if not skip_xml:
        log.warning(
            "WARNING: Rekordbox XML is managed by Rekordbox + Mixed In Key. "
            "Toolkit export is disabled by default to prevent data loss. "
            "Proceeding because --force-xml was explicitly requested."
        )
    recover_limit        = getattr(args, "recover_limit",            None)
    recover_timeout_sec  = getattr(args, "recover_timeout_sec",      None)

    # Allow per-run overrides of drive letter, Linux root, and export root
    win_drive   = getattr(args, "win_drive",   None)
    linux_root  = getattr(args, "linux_root",  None)
    export_root = getattr(args, "export_root", None)
    if win_drive:
        config.RB_WINDOWS_DRIVE = win_drive.rstrip(":\\")
    if linux_root:
        from pathlib import Path as _Path
        config.RB_LINUX_ROOT = _Path(linux_root)
    if export_root:
        from pathlib import Path as _Path
        _root = _Path(export_root)
        config.REKORDBOX_XML_EXPORT_DIR = _root / "_REKORDBOX_XML_EXPORT"
        config.REKORDBOX_M3U_EXPORT_DIR = _root / "_PLAYLISTS_M3U_EXPORT"

    return rekordbox_export.run(
        dry_run             = dry_run,
        skip_xml            = skip_xml,
        skip_m3u            = skip_m3u,
        recover_missing     = recover_missing,
        recover_limit       = recover_limit,
        recover_timeout_sec = recover_timeout_sec,
    )


# ---------------------------------------------------------------------------
# Metadata Clean
# ---------------------------------------------------------------------------
def run_metadata_clean(args) -> int:
    """
    Scan the sorted library for URL/promo junk in all metadata fields and
    optionally write cleaned values back.

    Modes:
      --dry-run   scan + preview, no file writes (default when neither flag given)
      --apply     scan + apply all changes after confirmation
      (no flag)   dry-run preview
    """
    _setup_logging(getattr(args, "verbose", False))
    do_apply = _apply_mode_or_error(args)
    if do_apply is None:
        return 2
    dry_run = not do_apply
    if do_apply:
        db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))

    if custom_path is not None:
        _log_active_path("METADATA-CLEAN", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("METADATA-CLEAN", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    log.info(
        "metadata-clean: scanning %d track(s)  dry_run=%s",
        len(paths), dry_run,
    )
    log_action(f"METADATA-CLEAN {'DRY-RUN' if dry_run else 'APPLY'}: {len(paths)} track(s)")

    report_dir = config.METADATA_CLEAN_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    scanned, changed, fields = metadata_clean.run(paths, dry_run=dry_run)
    log_action(
        f"METADATA-CLEAN {'DRY-RUN' if dry_run else 'APPLY'} DONE: "
        f"{scanned} scanned, {changed} {'planned' if dry_run else 'applied'}, {fields} fields"
    )

    if not dry_run:
        log.info(
            "metadata-clean summary: %d scanned / %d files modified / %d fields cleaned",
            scanned, changed, fields,
        )
        _print_metadata_clean_summary(scanned, changed, fields)

    return 0


def _print_metadata_clean_summary(scanned: int, changed: int, fields: int) -> None:
    """Print a brief terminal summary after applying metadata-clean."""
    print(f"\n=== metadata-clean complete ===")
    print(f"  Tracks scanned  : {scanned}")
    print(f"  Files modified  : {changed}")
    print(f"  Fields cleaned  : {fields}")
    if changed:
        print(
            f"\n  Rekordbox note  : re-import your library after cleaning so "
            f"Rekordbox picks up the updated tags."
        )
    print()


# ---------------------------------------------------------------------------
# Extract Track Metadata
# ---------------------------------------------------------------------------
_RE_EXTRACT_CAMELOT = re.compile(r"^(1[0-2]|[1-9])[AB]$", re.IGNORECASE)
_RE_EXTRACT_NUMBER = re.compile(r"(\d+(?:\.\d+)?)")


def _extract_tag_text(tags, *keys: str) -> str:
    if tags is None:
        return ""
    for key in keys:
        try:
            raw = tags.get(key)
        except Exception:
            raw = None
        if raw is None:
            continue
        if isinstance(raw, (list, tuple)):
            for item in raw:
                text = _extract_tag_text({"_": item}, "_")
                if text:
                    return text
            continue
        if hasattr(raw, "text"):
            text_values = getattr(raw, "text", None)
            if isinstance(text_values, (list, tuple)):
                for item in text_values:
                    text = str(item).strip()
                    if text:
                        return text
            else:
                text = str(text_values).strip()
                if text:
                    return text
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def _extract_numeric_tag(tags, *keys: str) -> float | None:
    text = _extract_tag_text(tags, *keys)
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        match = _RE_EXTRACT_NUMBER.search(text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except Exception:
            return None


def _classify_key_value(value: str) -> tuple[str | None, str | None]:
    text = value.strip()
    if not text:
        return None, None
    if _RE_EXTRACT_CAMELOT.match(text):
        return None, text.upper()
    return text, None


def _extract_local_metadata(path: Path) -> dict[str, object] | None:
    try:
        from mutagen import File as MFile
    except Exception as exc:
        log.error("mutagen is unavailable: %s", exc)
        return None

    try:
        easy_audio = MFile(str(path), easy=True)
        full_audio = MFile(str(path))
    except Exception:
        return None

    if easy_audio is None and full_audio is None:
        return None

    easy_tags = easy_audio if easy_audio is not None else {}
    full_tags = getattr(full_audio, "tags", None) if full_audio is not None else None
    info = getattr(full_audio, "info", None) or getattr(easy_audio, "info", None)

    raw_artist = _extract_tag_text(easy_tags, "artist")
    raw_title = _extract_tag_text(easy_tags, "title")
    album = _extract_tag_text(easy_tags, "album")
    genre = _extract_tag_text(easy_tags, "genre")

    artist = raw_artist if is_valid_artist(raw_artist) else ""
    title = raw_title if is_valid_title(raw_title) else ""
    filename_parse = parse_filename_metadata(path.stem)
    parse_attempted = not artist or not title
    parse_accepted = bool(filename_parse.accepted and filename_parse.artist and filename_parse.title)
    if parse_accepted:
        if not artist:
            artist = filename_parse.artist
        if not title:
            title = filename_parse.combined_title()

    bpm = _extract_numeric_tag(easy_tags, "bpm")
    if bpm is None:
        bpm = _extract_numeric_tag(full_tags, "TBPM", "tmpo", "BPM", "bpm")

    key_musical = ""
    key_camelot = ""
    for source_tags, keys in [
        (easy_tags, ("initialkey", "key", "musicalkey")),
        (full_tags, ("TKEY", "initialkey", "KEY", "key")),
    ]:
        raw = _extract_tag_text(source_tags, *keys)
        if not raw:
            continue
        musical, camelot = _classify_key_value(raw)
        if camelot and not key_camelot:
            key_camelot = camelot
        if musical and not key_musical:
            key_musical = musical

    duration_sec = None
    bitrate_kbps = None
    if info is not None:
        length = getattr(info, "length", None)
        bitrate = getattr(info, "bitrate", None)
        try:
            if length is not None:
                duration_sec = float(length)
        except Exception:
            duration_sec = None
        try:
            if bitrate is not None:
                bitrate_kbps = int(round(float(bitrate) / 1000.0))
        except Exception:
            bitrate_kbps = None

    parse_confidence = "HIGH" if artist and title and is_valid_artist(raw_artist) and is_valid_title(raw_title) else (
        filename_parse.parse_confidence if parse_accepted else ("LOW" if parse_attempted else "HIGH")
    )

    return {
        "artist": artist or None,
        "title": title or None,
        "album": album or None,
        "genre": genre or None,
        "bpm": bpm,
        "key_musical": key_musical or None,
        "key_camelot": key_camelot or None,
        "duration_sec": duration_sec,
        "bitrate_kbps": bitrate_kbps,
        "parse_confidence": parse_confidence,
        "_filename_parse_attempted": parse_attempted,
        "_filename_parse_accepted": parse_accepted,
    }


def _extract_row_is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _extract_track_metadata_updates(row: dict[str, object], extracted: dict[str, object]) -> tuple[dict[str, object], dict[str, int], bool]:
    updates: dict[str, object] = {}
    field_counts = {
        "artist": 0,
        "title": 0,
        "album": 0,
        "genre": 0,
        "bpm": 0,
        "key_musical": 0,
        "key_camelot": 0,
        "duration_sec": 0,
        "bitrate_kbps": 0,
        "parse_confidence": 0,
    }
    changed = False

    for field in field_counts:
        current = row.get(field)
        extracted_value = extracted.get(field)
        if not _extract_row_is_blank(current):
            continue
        if extracted_value is None or extracted_value == "":
            continue
        updates[field] = extracted_value
        field_counts[field] = 1
        changed = True

    return updates, field_counts, changed


def _write_extract_log(
    log_path: Path,
    *,
    root: Path,
    scanned: int,
    updated: int,
    skipped_existing: int,
    unreadable_files: int,
    parse_attempted: int,
    parse_accepted: int,
    parse_rejected: int,
    field_counts: dict[str, int],
    sample_updates: list[dict[str, object]],
    dry_run: bool,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "extract-track-metadata",
        f"root={root}",
        f"mode={'DRY-RUN' if dry_run else 'APPLY'}",
        f"scanned={scanned}",
        f"updated={updated}",
        f"skipped_existing={skipped_existing}",
        f"unreadable_files={unreadable_files}",
        f"filename_parse_attempted={parse_attempted}",
        f"filename_parse_accepted={parse_accepted}",
        f"filename_parse_rejected={parse_rejected}",
        "field_counts=" + json.dumps(field_counts, sort_keys=True),
    ]
    if sample_updates:
        lines.append("sample_updates=" + json.dumps(sample_updates[:5], sort_keys=True))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_extract_track_metadata(args) -> int:
    """
    Populate missing track metadata from audio file tags.

    Dry-run by default. Use --apply --yes to write to the DB.
    """
    _setup_logging(getattr(args, "verbose", False))

    root = resolve_library_root(args)
    do_apply = bool(getattr(args, "apply", False))
    yes = bool(getattr(args, "yes", False))
    if do_apply and not yes:
        print("ERROR: --apply requires --yes for extract-track-metadata.", file=sys.stderr)
        return 2
    dry_run = not do_apply

    db_path = root / "logs" / "processed.db"
    log_dir = root / "logs" / "metadata_extract"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_extract.log"

    if not db_path.exists():
        _write_extract_log(
            log_path,
            root=root,
            scanned=0,
            updated=0,
            skipped_existing=0,
            unreadable_files=0,
            parse_attempted=0,
            parse_accepted=0,
            parse_rejected=0,
            field_counts={},
            sample_updates=[],
            dry_run=dry_run,
        )
        print(f"extract-track-metadata: no database found at {db_path}")
        return 0

    import sqlite3

    def _table_columns() -> list[str]:
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                rows = conn.execute("PRAGMA table_info(tracks)").fetchall()
            return [row[1] for row in rows]
        except Exception:
            return []

    columns = _table_columns()
    if "filepath" not in columns:
        _write_extract_log(
            log_path,
            root=root,
            scanned=0,
            updated=0,
            skipped_existing=0,
            unreadable_files=0,
            parse_attempted=0,
            parse_accepted=0,
            parse_rejected=0,
            field_counts={},
            sample_updates=[],
            dry_run=dry_run,
        )
        print("extract-track-metadata: tracks table is not available")
        return 0

    if do_apply and "album" not in columns:
        with sqlite3.connect(str(db_path)) as migrate_conn:
            migrate_conn.execute("ALTER TABLE tracks ADD COLUMN album TEXT")
        columns.append("album")
    if do_apply and "parse_confidence" not in columns:
        with sqlite3.connect(str(db_path)) as migrate_conn:
            migrate_conn.execute("ALTER TABLE tracks ADD COLUMN parse_confidence TEXT")
        columns.append("parse_confidence")

    target_fields = [
        "artist",
        "title",
        "album",
        "genre",
        "bpm",
        "key_musical",
        "key_camelot",
        "duration_sec",
        "bitrate_kbps",
        "parse_confidence",
    ]
    select_fields = ["id", "filepath"]
    for field in target_fields:
        if field in columns:
            select_fields.append(field)
        else:
            select_fields.append(f"NULL AS {field}")

    rows: list[dict[str, object]] = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                f"SELECT {', '.join(select_fields)} FROM tracks ORDER BY id"
            ):
                rows.append(dict(row))
    except Exception as exc:
        print(f"ERROR: failed to read tracks table: {exc}", file=sys.stderr)
        _write_extract_log(
            log_path,
            root=root,
            scanned=0,
            updated=0,
            skipped_existing=0,
            unreadable_files=0,
            parse_attempted=0,
            parse_accepted=0,
            parse_rejected=0,
            field_counts={},
            sample_updates=[],
            dry_run=dry_run,
        )
        return 1

    scanned = len(rows)
    updated = 0
    skipped_existing = 0
    unreadable_files = 0
    parse_attempted = 0
    parse_accepted = 0
    parse_rejected = 0
    field_counts = {field: 0 for field in target_fields}
    sample_updates: list[dict[str, object]] = []

    def _process_rows(write_conn=None) -> None:
        nonlocal updated, skipped_existing, unreadable_files
        nonlocal parse_attempted, parse_accepted, parse_rejected
        for row in rows:
            raw_path = str(row.get("filepath") or "")
            try:
                resolved = assert_path_under_root(raw_path, root)
            except Exception:
                unreadable_files += 1
                continue
            if not resolved.exists() or not resolved.is_file():
                unreadable_files += 1
                continue

            extracted = _extract_local_metadata(resolved)
            if extracted is None:
                unreadable_files += 1
                continue
            if extracted.get("_filename_parse_attempted"):
                parse_attempted += 1
                if extracted.get("_filename_parse_accepted"):
                    parse_accepted += 1
                else:
                    parse_rejected += 1

            updates, changed_counts, changed = _extract_track_metadata_updates(row, extracted)
            if not changed:
                skipped_existing += 1
                continue

            for field, count in changed_counts.items():
                field_counts[field] += count

            if write_conn is not None and updates:
                placeholders = ", ".join(f"{field}=?" for field in updates)
                params = list(updates.values()) + [row["filepath"]]
                write_conn.execute(
                    f"UPDATE tracks SET {placeholders} WHERE filepath=?",
                    params,
                )
            updated += 1
            if len(sample_updates) < 5:
                sample_updates.append({
                    "filepath": str(resolved),
                    "fields": list(updates.keys()),
                })

    if do_apply:
        with sqlite3.connect(str(db_path)) as write_conn:
            _process_rows(write_conn)
    else:
        _process_rows(None)

    _write_extract_log(
        log_path,
        root=root,
        scanned=scanned,
        updated=updated,
        skipped_existing=skipped_existing,
        unreadable_files=unreadable_files,
        parse_attempted=parse_attempted,
        parse_accepted=parse_accepted,
        parse_rejected=parse_rejected,
        field_counts=field_counts,
        sample_updates=sample_updates,
        dry_run=dry_run,
    )

    print("\n=== extract-track-metadata complete ===")
    print(f"  Root            : {root}")
    print(f"  Scanned         : {scanned}")
    print(f"  Updated         : {updated}")
    print(f"  Skipped existing: {skipped_existing}")
    print(f"  Unreadable      : {unreadable_files}")
    print(f"  Parse accepted  : {parse_accepted}/{parse_attempted}")
    print(f"  Mode            : {'APPLY' if do_apply else 'DRY-RUN'}")
    print(f"  Log             : {log_path}")
    print("  Fields updated  :")
    for field in target_fields:
        print(f"    {field}: {field_counts[field]}")
    if sample_updates:
        print("  Sample updated tracks:")
        for sample in sample_updates[:5]:
            fields = ", ".join(sample["fields"]) if sample["fields"] else "(none)"
            print(f"    - {sample['filepath']} [{fields}]")
    print()

    return 0


# ---------------------------------------------------------------------------
# Tag Normalize
# ---------------------------------------------------------------------------
def run_tag_normalize(args) -> int:
    """
    Scan the sorted library (or a custom path) for MP3 files with ID3v2.4 tags
    or a trailing ID3v1 block, and normalise them to ID3v2.3 / no ID3v1.
    """
    _setup_logging(getattr(args, "verbose", False))
    do_apply = _apply_mode_or_error(args)
    if do_apply is None:
        return 2
    dry_run = not do_apply

    custom_path = _resolve_path(getattr(args, "path", None))
    if custom_path is not None:
        _log_active_path("TAG-NORMALIZE", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("TAG-NORMALIZE", config.SORTED)
        paths = _collect_audio_from_dir(config.SORTED)

    mp3_paths = [p for p in paths if p.suffix.lower() == ".mp3"]

    if not mp3_paths:
        log.warning("tag-normalize: no MP3 files found in scan path")
        return 0

    scanned, normalized, v24, v1 = tag_normalize.run(
        paths=mp3_paths,
        dry_run=dry_run,
        verbose=getattr(args, "verbose", False),
    )
    log_action(
        f"TAG-NORMALIZE {'DRY-RUN' if dry_run else 'APPLY'}: "
        f"{scanned} scanned, {normalized} {'planned' if dry_run else 'applied'}"
    )

    if not dry_run:
        print(f"\n=== tag-normalize complete ===")
        print(f"  MP3s scanned    : {scanned}")
        print(f"  Normalized      : {normalized}")
        print(f"  v2.4 downgraded : {v24}")
        print(f"  v1 removed      : {v1}")
        if normalized:
            print(
                f"\n  Rekordbox note  : re-import your library after normalizing so "
                f"Rekordbox picks up the updated tag format."
            )
        print()

    return 0


# ---------------------------------------------------------------------------
# Filename normalize
# ---------------------------------------------------------------------------
def run_filename_normalize(args) -> int:
    """
    Rename audio files to {artist} - {title} ({version}).ext using embedded tags.
    Preview by default; use --apply to commit renames.
    """
    db.init_db()
    _setup_logging(getattr(args, "verbose", False))

    input_path = _resolve_path(getattr(args, "input", None))
    if not input_path:
        print("ERROR: --input DIR is required.", file=sys.stderr)
        return 1

    from modules.filename_normalize import run as _fn_run

    stats = _fn_run(
        input_path,
        apply=getattr(args, "apply", False),
        verbose=getattr(args, "verbose", False),
        force=getattr(args, "force", False),
        reset_stage=getattr(args, "reset_stage", False),
        limit=getattr(args, "limit", None),
        move_artist_review=getattr(args, "move_artist_review", False),
    )

    print("── Summary ──────────────────────────────────────────────")
    print(f"  Files scanned            : {stats['scanned']}")
    print(f"  Rename candidates        : {stats['candidates']}")
    print(f"  Renamed                  : {stats['renamed']}")
    print(f"  Skipped (no artist/title): {stats['skipped_no_tags']}")
    print(f"  Skipped (unsafe artist)  : {stats['skipped_unsafe_artist']}")
    print(f"  Artist review queued     : {stats['artist_review_count']}")
    print(f"  Artist review moved      : {stats['moved_to_artist_review']}")
    print(f"  Skipped (no change)      : {stats['skipped_no_change']}")
    print(f"  Collision renames        : {stats['collisions']}")
    print(f"  Version stripped (junk)  : {stats['stripped_version']}")
    print(f"  Errors                   : {stats['skipped_errors']}")
    if not getattr(args, "apply", False) and stats["candidates"] > 0:
        print(f"\n  Run with --apply to rename {stats['candidates']} file(s).")
    if not getattr(args, "apply", False) and stats["artist_review_count"] > 0:
        print(f"  Run with --move-artist-review --apply to quarantine {stats['artist_review_count']} review file(s).")
    print()
    return 0


# ---------------------------------------------------------------------------
# Library Organize
# ---------------------------------------------------------------------------
def run_library_organize(args) -> int:
    """
    Reorganize audio files into <sorted_root>/<letter>/<primary-artist>/<filename>.
    Preview by default; use --apply to commit moves.
    """
    db.init_db()
    _setup_logging(getattr(args, "verbose", False))

    input_path = _resolve_path(getattr(args, "input", None))
    if not input_path:
        print("ERROR: --input DIR is required.", file=sys.stderr)
        return 1

    from modules.library_organize import run as _lo_run

    flatten             = getattr(args, "flatten_collab_folders", False)
    move_unsafe_artists = getattr(args, "move_unsafe_artists", False)

    stats = _lo_run(
        input_path,
        apply=getattr(args, "apply", False),
        verbose=getattr(args, "verbose", False),
        force=getattr(args, "force", False),
        reset_stage=getattr(args, "reset_stage", False),
        limit=getattr(args, "limit", None),
        flatten_collab_folders=flatten,
        move_unsafe_artists=move_unsafe_artists,
    )

    if flatten:
        print("── Flatten Summary ───────────────────────────────────────")
        print(f"  Files scanned            : {stats['scanned']}")
        print(f"  Flatten candidates       : {stats['candidates']}")
        print(f"  Flattened                : {stats['moved']}")
        print(f"  Already correct          : {stats['skipped_already_correct']}")
        print(f"  Collisions               : {stats['collisions']}")
        print(f"  Errors                   : {stats['skipped_errors']}")
        if not getattr(args, "apply", False) and stats["candidates"] > 0:
            print(f"\n  Run with --apply to flatten {stats['candidates']} file(s).")
    else:
        print("── Summary ──────────────────────────────────────────────")
        print(f"  Files scanned            : {stats['scanned']}")
        print(f"  Move candidates          : {stats['candidates']}")
        print(f"  Moved                    : {stats['moved']}")
        print(f"  Skipped (unchanged)      : {stats['skipped_unchanged']}")
        print(f"  Skipped (no artist)      : {stats['skipped_no_artist']}")
        print(f"  Unsafe artist (total)    : {stats['unsafe_artist_count']}")
        print(f"  → Moved to CHKARTISTNAMES: {stats['moved_to_chkartistnames']}")
        print(f"  → Left in place          : {stats['skipped_unsafe_artist']}")
        print(f"  Skipped (already correct): {stats['skipped_already_correct']}")
        print(f"  Collision renames        : {stats['collisions']}")
        print(f"  Errors                   : {stats['skipped_errors']}")
        if not getattr(args, "apply", False) and stats["candidates"] > 0:
            print(f"\n  Run with --apply to move {stats['candidates']} file(s).")
    print()
    return 0


# ---------------------------------------------------------------------------
# DB Prune Stale
# ---------------------------------------------------------------------------
def run_db_prune_stale(args) -> int:
    """
    Mark DB rows as 'stale' when the file no longer exists on the current
    SSD library and cannot be located by filename anywhere under --path.
    Rows are marked, never deleted, so you can always review what was pruned.
    """
    _setup_logging(getattr(args, "verbose", False))
    do_apply = _apply_mode_or_error(args)
    if do_apply is None:
        return 2
    dry_run = not do_apply
    if do_apply:
        db.init_db()

    raw_path = getattr(args, "path", None)
    lib_root = _resolve_path(raw_path) if raw_path else Path(config.RB_LINUX_ROOT)

    if lib_root is None or not lib_root.exists():
        log.error("db-prune-stale: path not found: %s", lib_root or raw_path)
        return 1

    mode    = "DRY-RUN" if dry_run else "APPLY"
    log.info("db-prune-stale %s: scanning DB against %s", mode, lib_root)

    checked, pruned = db.prune_stale_tracks(lib_root, dry_run=dry_run)

    print(f"\n=== db-prune-stale {'(DRY-RUN) ' if dry_run else ''}===")
    print(f"  Library root    : {lib_root}")
    print(f"  DB rows checked : {checked}")
    print(f"  Stale rows      : {pruned}"
          + (" (would mark stale)" if dry_run else " (marked status='stale')"))
    if pruned and dry_run:
        print( "  Run with --apply --yes to apply.")
    if pruned and not dry_run:
        print( "  These rows are now excluded from rekordbox-export.")
        print( "  They are NOT deleted — query the DB to review them:")
        print( "    SELECT filepath FROM tracks WHERE status='stale';")
    print()

    log_action(
        f"DB-PRUNE-STALE {mode}: {checked} checked, {pruned} marked stale, "
        f"lib_root={lib_root}"
    )
    return 0


# ---------------------------------------------------------------------------
# Audit Quality
# ---------------------------------------------------------------------------
def run_audit_quality(args) -> int:
    """
    Audit the library (or a custom path) for codec/bitrate quality.

    Default: non-destructive audit + report only.
    Optional actions: --move-low-quality DIR, --write-tags.
    """
    from modules import audit_quality

    _setup_logging(getattr(args, "verbose", False))

    raw_path = getattr(args, "path", None)
    if raw_path:
        scan_root = _resolve_path(raw_path)
    else:
        scan_root = config.SORTED
        if not scan_root.exists():
            # Fall back to LIBRARY if SORTED doesn't exist (useful on a fresh install)
            scan_root = config.LIBRARY

    if scan_root is None or not scan_root.exists():
        print(
            f"ERROR: scan path does not exist: {scan_root or raw_path}",
            file=sys.stderr,
        )
        return 2

    _log_active_path("AUDIT-QUALITY", scan_root)

    dry_run = getattr(args, "dry_run", False)

    # --move-low-quality DIR
    move_low_raw = getattr(args, "move_low_quality", None)
    move_low_dir = Path(move_low_raw).expanduser().resolve() if move_low_raw else None

    # --report-format csv,json  (default: both)
    fmt_raw = getattr(args, "report_format", "csv,json") or "csv,json"
    report_formats = [f.strip().lower() for f in fmt_raw.split(",") if f.strip()]
    valid_fmts = {"csv", "json"}
    report_formats = [f for f in report_formats if f in valid_fmts]
    if not report_formats:
        log.warning("No valid report formats specified; defaulting to csv,json")
        report_formats = ["csv", "json"]

    min_lossy_kbps = getattr(args, "min_lossy_kbps", 192)
    write_tags     = getattr(args, "write_tags", False)
    report_dir     = config.AUDIT_QUALITY_REPORT_DIR

    log.info(
        "audit-quality: path=%s  dry_run=%s  move_low=%s  write_tags=%s  "
        "min_lossy_kbps=%d  report_format=%s",
        scan_root, dry_run, move_low_dir or "off", write_tags,
        min_lossy_kbps, ",".join(report_formats),
    )
    log_action(
        f"AUDIT-QUALITY {'DRY-RUN' if dry_run else 'START'}: "
        f"path={scan_root}  move_low={move_low_dir or 'off'}  write_tags={write_tags}"
    )

    # Ensure DB is ready (quality_tier column migration runs in init_db)
    db.init_db()

    results, report_paths = audit_quality.run(
        scan_root      = scan_root,
        dry_run        = dry_run,
        move_low_dir   = move_low_dir,
        write_tags     = write_tags,
        report_formats = report_formats,
        min_lossy_kbps = min_lossy_kbps,
        verbose        = getattr(args, "verbose", False),
        ffprobe_bin    = getattr(config, "FFPROBE_BIN", "ffprobe"),
        report_dir     = report_dir,
        store_in_db    = not dry_run,
    )

    moved_count      = sum(1 for r in results if r.action_taken == "moved")
    tagged_count     = sum(1 for r in results if r.action_taken == "tag_written")
    unreadable_count = sum(1 for r in results if r.action_taken == "unreadable")

    if report_paths:
        log.info("Reports written to: %s", report_dir)
        for fmt, rpath in report_paths.items():
            log.info("  %-5s %s", fmt, rpath.name)

    log_action(
        f"AUDIT-QUALITY DONE: {len(results)} scanned, {moved_count} moved, "
        f"{tagged_count} tagged, {unreadable_count} unreadable → {report_dir}"
    )
    return 0


# ---------------------------------------------------------------------------
# Convert Audio
# ---------------------------------------------------------------------------
def run_convert_audio(args) -> int:
    """
    Convert .m4a files to .aiff, preserve metadata, archive originals.

    Requires:
      --src   root directory containing .m4a files (scanned recursively)
      --dst   root directory for .aiff output files (relative structure preserved)
      --archive  root directory where original .m4a files are moved after conversion

    On success: original .m4a is moved to --archive (never deleted outright).
    On failure: original is left in place; failed output file is removed.
    """
    from modules import convert_audio

    _setup_logging(getattr(args, "verbose", False))
    do_apply = _apply_mode_or_error(args)
    if do_apply is None:
        return 2
    dry_run = not do_apply

    src_raw     = getattr(args, "src",     None)
    dst_raw     = getattr(args, "dst",     None)
    archive_raw = getattr(args, "archive", None)

    if not src_raw:
        print("ERROR: --src is required", file=sys.stderr)
        return 2
    if not dst_raw:
        print("ERROR: --dst is required", file=sys.stderr)
        return 2
    if not archive_raw:
        print("ERROR: --archive is required", file=sys.stderr)
        return 2

    src_path     = Path(src_raw).expanduser().resolve()
    dst_path     = Path(dst_raw).expanduser().resolve()
    archive_path = Path(archive_raw).expanduser().resolve()

    if not src_path.is_dir():
        print(f"ERROR: --src does not exist or is not a directory: {src_path}", file=sys.stderr)
        return 2

    # Safety guard: make sure archive is not inside src or dst
    if str(archive_path).startswith(str(src_path) + "/"):
        print("ERROR: --archive must not be inside --src", file=sys.stderr)
        return 2
    if str(archive_path).startswith(str(dst_path) + "/"):
        print("ERROR: --archive must not be inside --dst", file=sys.stderr)
        return 2

    workers   = getattr(args, "workers",              4)
    overwrite = getattr(args, "overwrite",             False)
    tolerance = getattr(args, "verify_tolerance_sec",  1.0)
    verbose   = getattr(args, "verbose",               False)
    no_prog   = getattr(args, "no_progress",           False)

    ffmpeg_bin  = getattr(config, "FFMPEG_BIN",  "ffmpeg")
    ffprobe_bin = getattr(config, "FFPROBE_BIN", "ffprobe")

    log.info(
        "convert-audio: src=%s  dst=%s  archive=%s  workers=%d  overwrite=%s  "
        "tolerance=%.2fs  dry_run=%s",
        src_path, dst_path, archive_path, workers, overwrite, tolerance, dry_run,
    )
    log_action(
        f"CONVERT-AUDIO {'DRY-RUN' if dry_run else 'START'}: "
        f"src={src_path}  dst={dst_path}  archive={archive_path}"
    )

    rc = convert_audio.run(
        src           = src_path,
        dst           = dst_path,
        archive       = archive_path,
        workers       = workers,
        overwrite     = overwrite,
        tolerance     = tolerance,
        dry_run       = dry_run,
        verbose       = verbose,
        show_progress = not no_prog,
        ffmpeg_bin    = ffmpeg_bin,
        ffprobe_bin   = ffprobe_bin,
    )

    log_action(f"CONVERT-AUDIO {'DRY-RUN' if dry_run else 'DONE'}: rc={rc}")
    return rc


# ---------------------------------------------------------------------------
# Review Queue
# ---------------------------------------------------------------------------
def run_review_queue_command(args) -> int:
    """
    Keep review-queue read-only unless --apply is explicitly confirmed.
    Dry-run mode maps to the existing list-only queue view.
    """
    _setup_logging(getattr(args, "verbose", False))

    if getattr(args, "list_only", False):
        setattr(args, "dry_run", True)
        print("MODE: DRY-RUN")
        log_action("review-queue: MODE DRY-RUN")
    else:
        do_apply = _apply_mode_or_error(args)
        if do_apply is None:
            return 2
        if not do_apply:
            setattr(args, "list_only", True)

    from intelligence.enrichment.runner import run_review_queue
    return run_review_queue(args)


# ---------------------------------------------------------------------------
# Generate Docs
# ---------------------------------------------------------------------------
def run_generate_docs(args) -> int:
    """
    Regenerate COMMANDS.txt, README.md (commands section), and COMMANDS.html
    from the centralized command registry in modules/doc_registry.py.
    """
    _setup_logging(getattr(args, "verbose", False))

    from modules import doc_registry, doc_gen

    dry_run    = getattr(args, "dry_run",    False)
    output_dir = getattr(args, "output_dir", None)
    fmt_raw    = getattr(args, "format",     "txt,md,html") or "txt,md,html"
    formats    = {f.strip().lower() for f in fmt_raw.split(",") if f.strip()}

    project_root = Path(__file__).parent
    out_root     = Path(output_dir).expanduser().resolve() if output_dir else project_root

    if not dry_run:
        out_root.mkdir(parents=True, exist_ok=True)

    version = doc_registry.VERSION
    registry = doc_registry.REGISTRY

    generated: list[tuple[str, str]] = []   # (label, content)

    if "txt" in formats:
        content = doc_gen.generate_commands_txt(registry, version)
        generated.append(("COMMANDS.txt", content))
        content = doc_gen.generate_commands_md(registry, version)
        generated.append(("COMMANDS.md", content))

    if "md" in formats:
        readme_path = out_root / "README.md"
        section     = doc_gen.generate_readme_commands_section(registry)
        content     = doc_gen.splice_readme_commands(readme_path, section)
        generated.append(("README.md", content))

    if "html" in formats:
        content = doc_gen.generate_commands_html(registry, version)
        generated.append(("COMMANDS.html", content))

    if dry_run:
        for label, content in generated:
            print(f"\n{'='*70}")
            print(f"  DRY-RUN PREVIEW: {label}")
            print(f"{'='*70}")
            # Print first 60 lines as a preview
            preview_lines = content.splitlines()[:60]
            print("\n".join(preview_lines))
            if len(content.splitlines()) > 60:
                print(f"  ... ({len(content.splitlines())} total lines)")
        return 0

    for label, content in generated:
        dest = out_root / label
        dest.write_text(content, encoding="utf-8")
        log.info("Wrote: %s", dest)
        print(f"  WROTE  {dest}")

    print(f"\ngenerate-docs complete: {len(generated)} file(s) written to {out_root}")
    return 0


# ---------------------------------------------------------------------------
# Validate Docs
# ---------------------------------------------------------------------------
def run_validate_docs(args) -> int:
    """
    Check that COMMANDS.txt is in sync with the command registry.
    Reports commands that are in the registry but absent from COMMANDS.txt,
    and vice versa (stale entries).
    """
    _setup_logging(getattr(args, "verbose", False))

    from modules import doc_registry

    strict      = getattr(args, "strict", False)
    project_root = Path(__file__).parent
    commands_txt = project_root / "COMMANDS.txt"

    if not commands_txt.exists():
        print(f"ERROR: COMMANDS.txt not found at {commands_txt}")
        return 1

    text = commands_txt.read_text(encoding="utf-8")

    # Every registry entry (except MAIN) should appear somewhere in COMMANDS.txt.
    # We look for the command name as a standalone token (start of a line or
    # followed by a space / dash / newline).
    import re

    registry_names = [e["name"] for e in doc_registry.REGISTRY if e["name"] != "MAIN"]

    missing: list[str] = []
    for name in registry_names:
        # Look for "pipeline.py <name>" or "\n<name> " patterns
        if not re.search(
            r"(?:pipeline\.py\s+" + re.escape(name) + r"|^" + re.escape(name) + r"\b)",
            text,
            re.MULTILINE,
        ):
            missing.append(name)

    ok = True
    if missing:
        ok = False
        print(f"\nMISSING from COMMANDS.txt ({len(missing)} command(s)):")
        for name in missing:
            print(f"  - {name}")

    # Check for subcommand sections in COMMANDS.txt that have no registry entry.
    # We look for "pipeline.py <something>" where <something> contains a hyphen
    # (all subcommands have hyphens) to avoid matching flags like --dry-run.
    documented = re.findall(r"pipeline\.py\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)", text)
    documented_set = set(documented)
    registry_set   = set(registry_names)
    stale = documented_set - registry_set

    if stale:
        ok = False
        print(f"\nPOTENTIALLY STALE in COMMANDS.txt ({len(stale)} entry/ies):")
        for name in sorted(stale):
            print(f"  ? {name}  (not in registry — may be a flag, not a subcommand)")

    if ok:
        print(
            f"validate-docs: OK — all {len(registry_names)} registry commands "
            f"are present in COMMANDS.txt"
        )
        return 0
    else:
        if strict:
            print(
                "\nvalidate-docs: FAIL (--strict mode). "
                "Run `python3 pipeline.py generate-docs` to sync."
            )
            return 1
        else:
            print(
                "\nvalidate-docs: warnings found. "
                "Run `python3 pipeline.py generate-docs` to regenerate docs."
            )
            return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="CrateIQ — automated DJ library preparation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Artist Folder Clean (retroactive bad-name fix):\n"
            "  python pipeline.py artist-folder-clean --dry-run # scan + report, no moves\n"
            "  python pipeline.py artist-folder-clean --apply   # fix recoverable folders\n\n"
            "Artist Merge:\n"
            "  python pipeline.py artist-merge --dry-run        # scan + report, no moves\n"
            "  python pipeline.py artist-merge --apply          # apply safe merges\n\n"
            "Duplicate Detection and Cleanup:\n"
            "  python pipeline.py dedupe --dry-run              # preview duplicate groups\n"
            "  python pipeline.py dedupe                        # quarantine duplicates\n"
            "  python pipeline.py dedupe --path /mnt/music/     # scan custom directory\n\n"
            "Metadata Clean (global junk removal):\n"
            "  python pipeline.py metadata-clean --dry-run      # preview all field changes\n"
            "  python pipeline.py metadata-clean                # apply changes to library\n\n"
            "Metadata Sanitation:\n"
            "  python pipeline.py metadata-sanitation-scan --root /mnt/music_ssd/KKDJ\n"
            "  python pipeline.py metadata-sanitation-apply --root /mnt/music_ssd/KKDJ --apply --yes\n\n"
            "Label Clean (local, Phase 1):\n"
            "  python pipeline.py label-clean                   # scan + report, no writes\n"
            "  python pipeline.py label-clean --write-tags      # write high-confidence labels\n"
            "  python pipeline.py label-clean --review-only     # export unresolved only\n"
            "  python pipeline.py label-clean --confidence-threshold 0.75  # broader writes\n\n"
            "Label Intelligence (web scrape):\n"
            "  python pipeline.py label-intel\n"
            "  python pipeline.py label-intel --label-seeds /music/data/labels/seeds.txt\n\n"
            "Label Enrichment from Library:\n"
            "  python pipeline.py --label-enrich-from-library\n\n"
            "Playlist Generation and Rekordbox Export:\n"
            "  python pipeline.py playlists --dry-run              # preview all outputs\n"
            "  python pipeline.py playlists                        # write M3U + XML\n"
            "  python pipeline.py playlists --no-xml               # M3U only\n"
            "  python pipeline.py playlists --no-key --no-route    # skip Key/Route\n\n"
            "Cue Point Suggestion (intro / drop / outro detection):\n"
            "  python pipeline.py cue-suggest --dry-run            # analyse, no writes\n"
            "  python pipeline.py cue-suggest                      # analyse + store in DB\n"
            "  python pipeline.py cue-suggest --path /music/inbox/ # custom directory\n\n"
            "Set Builder (energy-curve auto set):\n"
            "  python pipeline.py set-builder --dry-run            # preview set, no files\n"
            "  python pipeline.py set-builder --vibe peak          # build a peak-energy set\n"
            "  python pipeline.py set-builder --duration 90 --vibe warm --genre 'afro house'\n\n"
            "Harmonic Mixing Suggestions:\n"
            "  python pipeline.py harmonic-suggest --track /path/to/track.mp3\n"
            "  python pipeline.py harmonic-suggest --key 8A --bpm 128\n"
            "  python pipeline.py harmonic-suggest --track ... --strategy energy_lift --json\n\n"
            "Audit Quality:\n"
            "  python3 pipeline.py audit-quality                        # audit + report\n"
            "  python3 pipeline.py audit-quality --dry-run --verbose    # preview\n"
            "  python3 pipeline.py audit-quality --move-low-quality /music/_low_quality\n"
            "  python3 pipeline.py audit-quality --write-tags           # write QUALITY tag\n"
            "  python3 pipeline.py audit-quality --report-format csv    # CSV only\n"
        ),
    )
    # ----- existing pipeline flags -----
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run all detection/analysis but make no file changes"
    )
    parser.add_argument(
        "--skip-beets", action="store_true",
        help="Skip beets import (use pure-Python organizer only)"
    )
    parser.add_argument(
        "--skip-analysis", action="store_true",
        help=(
            "[legacy] Force-skip all BPM/key analysis, even for tracks missing those values. "
            "Normally not needed — the pipeline is MIK-first and only fills gaps by default."
        ),
    )
    parser.add_argument(
        "--reanalyze", action="store_true",
        help="Re-run BPM+key analysis on sorted library tracks missing those values"
    )
    parser.add_argument(
        "--skip-cue-suggest", action="store_true",
        help=(
            "[deprecated — no-op] Cue suggest is now disabled by default. "
            "Use --force-cue-suggest to enable it."
        ),
    )
    parser.add_argument(
        "--force-cue-suggest", action="store_true",
        help=(
            "Enable cue point suggestion after tag writing. "
            "Disabled by default (MIK-first policy: cues are owned by Mixed In Key). "
            "Only use this if you are not using Mixed In Key."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--label-enrich-from-library", action="store_true",
        help=(
            "Enrich the label database using BPM/genre data from your local library. "
            "Reads the label tag (TPUB/organization) from all OK tracks — no re-analysis. "
            "Example: python pipeline.py --label-enrich-from-library"
        ),
    )
    parser.add_argument(
        "--path", metavar="DIR",
        help=(
            "Override the music root directory. Replaces DJ_MUSIC_ROOT / config defaults. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- subcommands -----
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_li = subparsers.add_parser(
        "label-intel",
        help="Scrape and export label metadata from Beatport/Traxsource",
    )
    p_li.add_argument(
        "--label-seeds", metavar="FILE",
        default=config.LABEL_INTEL_SEEDS,
        help=f"Seeds file (one label name per line). Default: {config.LABEL_INTEL_SEEDS}",
    )
    p_li.add_argument(
        "--label-output", metavar="DIR",
        default=config.LABEL_INTEL_OUTPUT,
        help=f"Output directory for exported files. Default: {config.LABEL_INTEL_OUTPUT}",
    )
    p_li.add_argument(
        "--label-cache", metavar="DIR",
        default=config.LABEL_INTEL_CACHE,
        help=f"HTTP cache directory. Default: {config.LABEL_INTEL_CACHE}",
    )
    p_li.add_argument(
        "--label-sources", nargs="+", metavar="SOURCE",
        default=config.LABEL_INTEL_SOURCES,
        choices=["beatport", "traxsource"],
        help="Sources to scrape. Default: beatport traxsource",
    )
    p_li.add_argument(
        "--label-delay", type=float, metavar="SECS",
        default=config.LABEL_INTEL_DELAY,
        help=f"Per-host request delay in seconds. Default: {config.LABEL_INTEL_DELAY}",
    )
    p_li.add_argument(
        "--label-skip-enrich", action="store_true",
        help="Skip label page enrichment (faster; search results only)",
    )
    p_li.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- artist-folder-clean subcommand -----
    p_afc = subparsers.add_parser(
        "artist-folder-clean",
        help="Fix bad artist folder names already on disk (Camelot prefixes, bracket junk, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Retroactively clean up artist folder names that were created before\n"
            "parser/sanitization fixes were in place.\n\n"
            "Detection rules:\n"
            "  pure_camelot    e.g. '10B', '1A'                  → review\n"
            "  camelot_prefix  e.g. '1A - Afrikan Roots'         → rename/merge\n"
            "  bracket_junk    e.g. '[HouseGrooveSA]'            → review\n"
            "  url_junk        e.g. 'djcity.com'                 → review\n"
            "  symbol_heavy    < 40%% alphanumeric chars         → review\n\n"
            "Outcomes:\n"
            "  rename  — cleaned name is valid, target folder does not exist\n"
            "  merge   — cleaned name is valid, target folder already exists\n"
            "  review  — no valid name can be recovered; written to report only\n\n"
            "Examples:\n"
            "  python pipeline.py artist-folder-clean --dry-run\n"
            "  python pipeline.py artist-folder-clean --apply\n"
        ),
    )
    p_afc.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report only — make no file moves (default behavior)",
    )
    p_afc.add_argument(
        "--apply", action="store_true",
        help=(
            "Apply all recoverable renames and merges. "
            "Unrecoverable folders go to the review report."
        ),
    )
    p_afc.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_afc.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of the default sorted library. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- label-clean subcommand -----
    p_lc = subparsers.add_parser(
        "label-clean",
        help="Detect, normalize, and optionally write back label metadata (Phase 1: local only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan all processed tracks for label metadata.\n\n"
            "Detection order:\n"
            "  1. organization/TPUB embedded tag    (confidence 0.95)\n"
            "  2. grouping tag fallback             (confidence 0.75)\n"
            "  3. comment tag fallback              (confidence 0.60)\n"
            "  4. filename pattern parsing          (confidence 0.55-0.70)\n"
            "  5. unresolved                        (confidence 0.00)\n\n"
            "Write-back (--write-tags) only applies when confidence >= threshold (default 0.85).\n"
            "At the default threshold only embedded-tag results are written automatically.\n"
        ),
    )
    p_lc.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report only — make no file changes (default behavior)",
    )
    p_lc.add_argument(
        "--write-tags", action="store_true",
        help=(
            f"Write high-confidence labels (>= {config.LABEL_CLEAN_THRESHOLD}) "
            "back to the organization/TPUB tag"
        ),
    )
    p_lc.add_argument(
        "--review-only", action="store_true",
        help="Only export the review file (unresolved / low-confidence tracks)",
    )
    p_lc.add_argument(
        "--confidence-threshold", type=float, metavar="FLOAT",
        default=config.LABEL_CLEAN_THRESHOLD,
        help=f"Minimum confidence for write-back. Default: {config.LABEL_CLEAN_THRESHOLD}",
    )
    p_lc.add_argument(
        "--use-discogs", action="store_true",
        help="[Phase 2 — not yet implemented] Match unresolved labels via Discogs API",
    )
    p_lc.add_argument(
        "--use-beatport", action="store_true",
        help="[Phase 2 — not yet implemented] Match unresolved labels via Beatport",
    )
    p_lc.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_lc.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan audio files in this directory instead of pulling from the database. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- artist-merge subcommand -----
    p_am = subparsers.add_parser(
        "artist-merge",
        help="Merge artist folder variants (capitalisation / feat / collab suffixes)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the sorted library for artist folders that represent the same\n"
            "base artist and merge them into a single canonical folder.\n\n"
            "Safe merges (only case / feat / collaborator differences) are applied\n"
            "automatically with --apply.  Uncertain merges (primary artist differs)\n"
            "are written to the review report only.\n\n"
            "Examples:\n"
            "  python pipeline.py artist-merge --dry-run   # scan + report, no moves\n"
            "  python pipeline.py artist-merge --apply     # apply safe merges\n"
        ),
    )
    p_am.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report only — make no file moves (default behavior)",
    )
    p_am.add_argument(
        "--apply", action="store_true",
        help="Apply safe merges. Uncertain merges go to the review report.",
    )
    p_am.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_am.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of the default sorted library. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- metadata-clean subcommand -----
    p_mc = subparsers.add_parser(
        "metadata-clean",
        help="Remove URL/promo junk from ALL metadata fields across the library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan every processed track for junk metadata and optionally write\n"
            "cleaned values back.\n\n"
            "Fields cleaned:\n"
            "  title, artist, album, albumartist, genre, comment,\n"
            "  label (organization/TPUB), grouping (TIT1), catalog number\n\n"
            "What is removed:\n"
            "  URLs / domains    https://djsoundtop.com, TraxCrate.com, www.djcity.com\n"
            "  DJ pool phrases   fordjonly, djcity, zipdj, musicafresca, promo only\n"
            "  Promo phrases     official audio, free download, downloaded from\n"
            "  Comment noise     Camelot keys (6A), BPM strings (121 BPM),\n"
            "                    combinations like '6A | Gm | 121 BPM'\n\n"
            "Field-specific behaviour:\n"
            "  albumartist     — cleared entirely when the value is a bare URL/domain\n"
            "  catalog number  — cleared entirely when the value is a bare URL/domain\n"
            "  comment         — URL/promo stripped; Camelot + BPM tokens also removed\n\n"
            "Examples:\n"
            "  python pipeline.py metadata-clean --dry-run   # preview, no writes\n"
            "  python pipeline.py metadata-clean --apply --yes  # apply changes\n"
        ),
    )
    p_mc.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be cleaned — make no file changes",
    )
    p_mc.add_argument(
        "--apply", action="store_true",
        help="Apply metadata tag changes. Without this flag, preview only.",
    )
    p_mc.add_argument(
        "--yes", action="store_true",
        help="Confirm writes when used with --apply.",
    )
    p_mc.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_mc.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan audio files in this directory instead of pulling from the database. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- extract-track-metadata subcommand -----
    p_etm = subparsers.add_parser(
        "extract-track-metadata",
        help="Populate missing track metadata from local audio tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read existing audio tags and populate missing metadata in the tracks table.\n\n"
            "Fields considered:\n"
            "  artist, title, album, genre, bpm, key_musical, duration_sec, bitrate_kbps\n\n"
            "Safety:\n"
            "  Dry-run by default.\n"
            "  Use --apply --yes to write DB updates.\n"
            "  Audio files are never modified.\n\n"
            "Examples:\n"
            "  python3 pipeline.py extract-track-metadata --root /mnt/music_ssd/KKDJ\n"
            "  python3 pipeline.py extract-track-metadata --root /mnt/music_ssd/KKDJ --apply --yes\n"
        ),
    )
    p_etm.add_argument(
        "--root", metavar="DIR",
        help="Library root whose tracks table should be scanned.",
    )
    p_etm.add_argument(
        "--apply", action="store_true",
        help="Write missing metadata back to the tracks table.",
    )
    p_etm.add_argument(
        "--yes", action="store_true",
        help="Confirm DB writes when used with --apply.",
    )
    p_etm.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    # ----- tag-normalize subcommand -----
    p_tn = subparsers.add_parser(
        "tag-normalize",
        help="Standardize MP3 ID3 tags for Rekordbox (ID3v2.4→v2.3, remove ID3v1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan MP3 files and normalize their ID3 tag format for Rekordbox compatibility.\n\n"
            "What is fixed:\n"
            "  ID3v2.4 → ID3v2.3  — Rekordbox reads v2.3 correctly on all platforms\n"
            "  ID3v1 removed      — 128-byte end-of-file block, never needed\n\n"
            "Log tags emitted per file:\n"
            "  [ID3V24_DOWNGRADED]   — was ID3v2.4, converted to v2.3\n"
            "  [ID3V1_REMOVED]       — ID3v1 block stripped\n"
            "  [ID3V23_NORMALIZED]   — file saved as ID3v2.3\n\n"
            "Non-MP3 files (FLAC, WAV, AIFF, M4A, OGG, OPUS) are always skipped.\n\n"
            "Examples:\n"
            "  python pipeline.py tag-normalize --dry-run\n"
            "  python pipeline.py tag-normalize --apply --yes\n"
            "  python pipeline.py tag-normalize --path /mnt/music_ssd/KKDJ/sorted/\n"
        ),
    )
    p_tn.add_argument(
        "--dry-run", action="store_true",
        help="Detect issues without writing any files",
    )
    p_tn.add_argument(
        "--apply", action="store_true",
        help="Normalize tag files. Without this flag, preview only.",
    )
    p_tn.add_argument(
        "--yes", action="store_true",
        help="Confirm writes when used with --apply.",
    )
    p_tn.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    p_tn.add_argument(
        "--path", metavar="DIR",
        help="Scan this directory instead of the default sorted library",
    )

    # ----- filename-normalize subcommand -----
    p_fnorm = subparsers.add_parser(
        "filename-normalize",
        help="Rename audio files to {artist} - {title} ({version}).ext using embedded tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Rename audio files using trusted embedded tags.\n\n"
            "Naming pattern:\n"
            "  {artist} - {title} ({version}).ext\n"
            "  {artist} - {title}.ext   (version absent or already embedded in title)\n\n"
            "Examples:\n"
            "  DJ Shimza - African Woman.mp3\n"
            "  Black Coffee - Superman (Original Mix).flac\n"
            "  Caiiro - The Akan (Da Capo Remix).aiff\n\n"
            "Safety:\n"
            "  Preview by default — no files renamed without --apply.\n"
            "  No overwrite — collisions get a safe suffix: ' (1)', ' (2)', ...\n"
            "  Tags are never modified. BPM, key, and cues are untouched.\n"
            "  Skipped if artist or title tag is missing.\n\n"
            "Examples:\n"
            "  python3 pipeline.py filename-normalize --input ~/Music/inbox\n"
            "  python3 pipeline.py filename-normalize --input ~/Music/inbox --apply\n"
        ),
    )
    p_fnorm.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to process.",
    )
    p_fnorm.add_argument(
        "--apply", action="store_true",
        help="Commit renames. Without this flag, preview only.",
    )
    p_fnorm.add_argument(
        "--verbose", action="store_true",
        help="Show skipped and no-change files; enable debug logging.",
    )
    p_fnorm.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Process at most N files.",
    )
    p_fnorm.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_fnorm.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )
    p_fnorm.add_argument(
        "--move-artist-review", action="store_true", dest="move_artist_review",
        help="Move unsafe-artist files to .BIN/ARTIST_REVIEW/ (requires --apply).",
    )

    # ----- library-organize subcommand -----
    p_lorg = subparsers.add_parser(
        "library-organize",
        help="Reorganize files into <sorted>/<letter>/<primary-artist>/<filename>",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Reorganize audio files into a deterministic folder hierarchy:\n\n"
            "  <sorted_root>/<first-letter>/<primary-artist>/<filename>\n\n"
            "Primary artist = first artist before any collaboration separator\n"
            "(feat. / ft. / & / , / ; / x / vs. / with / pres.).\n\n"
            "Examples:\n"
            "  'Papik feat. Michele Ranieri - Track.mp3'\n"
            "     → sorted/P/Papik/Papik feat. Michele Ranieri - Track.mp3\n"
            "  'Black Coffee, Bucie - Song.flac'\n"
            "     → sorted/B/Black Coffee/Black Coffee, Bucie - Song.flac\n\n"
            "Safety:\n"
            "  Preview by default — no files moved without --apply.\n"
            "  No overwrite — collisions get a safe suffix: ' (1)', ' (2)', ...\n"
            "  Tags are never modified. BPM, key, and cues are untouched.\n"
            "  Skips files missing artist tag or with unsafe concatenated names.\n\n"
            "Examples:\n"
            "  python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted\n"
            "  python3 pipeline.py library-organize --input /mnt/music_ssd/KKDJ/sorted --apply\n"
        ),
    )
    p_lorg.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to organize.",
    )
    p_lorg.add_argument(
        "--apply", action="store_true",
        help="Commit moves. Without this flag, preview only.",
    )
    p_lorg.add_argument(
        "--verbose", action="store_true",
        help="Show already-correct files; enable debug logging.",
    )
    p_lorg.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Process at most N files.",
    )
    p_lorg.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_lorg.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )
    p_lorg.add_argument(
        "--flatten-collab-folders", action="store_true", dest="flatten_collab_folders",
        help=(
            "Repair mode: move files out of nested collaborator sub-folders. "
            "sorted/<L>/<Artist>/<Collab>/file → sorted/<L>/<Artist>/file. "
            "Does not read tags; uses existing folder structure only."
        ),
    )
    p_lorg.add_argument(
        "--move-unsafe-artists", action="store_true", dest="move_unsafe_artists",
        help=(
            "Move files with unsafe concatenated artist names to "
            ".BIN/CHKARTISTNAMES/ for manual review. Requires --apply to execute. "
            "Without --apply, shows WOULD MOVE TO CHKARTISTNAMES preview."
        ),
    )

    # ----- db-prune-stale subcommand -----
    p_dps = subparsers.add_parser(
        "db-prune-stale",
        help="Mark DB rows stale when the file no longer exists on the current SSD library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the database for rows whose filepath no longer exists on disk\n"
            "and cannot be located by filename anywhere under the library root.\n\n"
            "Stale rows are marked status='stale' — they are NEVER deleted.\n"
            "After pruning, rekordbox-export will no longer warn about them.\n\n"
            "Examples:\n"
            "  python3 pipeline.py db-prune-stale --dry-run\n"
            "  python3 pipeline.py db-prune-stale --path /mnt/music_ssd/KKDJ/ --apply --yes\n"
        ),
    )
    p_dps.add_argument(
        "--dry-run", action="store_true",
        help="Report stale rows without marking them",
    )
    p_dps.add_argument(
        "--apply", action="store_true",
        help="Mark stale DB rows. Without this flag, report only.",
    )
    p_dps.add_argument(
        "--yes", action="store_true",
        help="Confirm DB writes when used with --apply.",
    )
    p_dps.add_argument(
        "--path", metavar="DIR",
        help=(
            "Library root to search for files "
            "(default: RB_LINUX_ROOT from config, typically /mnt/music_ssd)"
        ),
    )
    p_dps.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    # ----- convert-audio subcommand -----
    p_ca = subparsers.add_parser(
        "convert-audio",
        help="Convert .m4a files to .aiff, preserve metadata, archive originals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Convert all .m4a files under --src to .aiff under --dst.\n"
            "Relative folder structure is preserved in both --dst and --archive.\n\n"
            "Workflow per file:\n"
            "  1. ffprobe validates source — corrupt files are skipped\n"
            "  2. ffmpeg converts: pcm_s16be AIFF with metadata copied (-map_metadata 0)\n"
            "  3. Output is verified: ffprobe check + duration delta <= --verify-tolerance-sec\n"
            "  4. On success: original .m4a moved to --archive (relative path preserved)\n"
            "  5. On failure: original left untouched; broken output removed\n\n"
            "Output codec: pcm_s16be (16-bit big-endian PCM AIFF).\n"
            "Override ffmpeg/ffprobe paths via FFMPEG_BIN / FFPROBE_BIN env vars\n"
            "or config_local.py.\n\n"
            "Examples:\n"
            "  python3 pipeline.py convert-audio \\\n"
            "      --src /downloads/m4a \\\n"
            "      --dst /mnt/music_ssd/KKDJ/inbox \\\n"
            "      --archive /mnt/music_ssd/originals_m4a\n\n"
            "  python3 pipeline.py convert-audio --src /downloads --dst /music --archive /archive \\\n"
            "      --workers 8 --verify-tolerance-sec 2.0 --apply --yes\n"
        ),
    )
    p_ca.add_argument(
        "--src", metavar="PATH", required=True,
        help="Root directory containing .m4a files to convert (scanned recursively)",
    )
    p_ca.add_argument(
        "--dst", metavar="PATH", required=True,
        help="Root directory for output .aiff files (relative folder structure preserved)",
    )
    p_ca.add_argument(
        "--archive", metavar="PATH", required=True,
        help=(
            "Root directory where original .m4a files are moved after successful conversion. "
            "Relative folder structure from --src is preserved. Files are MOVED, never deleted."
        ),
    )
    p_ca.add_argument(
        "--workers", metavar="N", type=int, default=4,
        help="Number of parallel ffmpeg workers (default: 4)",
    )
    p_ca.add_argument(
        "--overwrite", action="store_true",
        help="Re-convert files that already have a .aiff output in --dst",
    )
    p_ca.add_argument(
        "--verify-tolerance-sec", metavar="SECS", type=float, default=1.0,
        dest="verify_tolerance_sec",
        help=(
            "Maximum allowed duration difference (seconds) between source and output. "
            "Conversions outside this tolerance are treated as failures (default: 1.0)"
        ),
    )
    p_ca.add_argument(
        "--dry-run", action="store_true",
        help="Probe sources and show what would be converted — write no files",
    )
    p_ca.add_argument(
        "--apply", action="store_true",
        help="Convert files and archive originals. Without this flag, preview only.",
    )
    p_ca.add_argument(
        "--yes", action="store_true",
        help="Confirm file writes and moves when used with --apply.",
    )
    p_ca.add_argument(
        "--no-progress", action="store_true",
        help="Disable the tqdm progress bar even when tqdm is installed",
    )
    p_ca.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- dedupe subcommand -----
    p_dd = subparsers.add_parser(
        "dedupe",
        help="Detect and quarantine duplicate audio files across the library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the library for duplicate audio files and optionally move\n"
            "them to a quarantine folder (never deleted outright).\n\n"
            "Detection cases:\n"
            "  Case A — Exact duplicate   : same SHA-256 hash\n"
            "                               → keep one, quarantine the rest\n"
            "  Case B — Quality duplicate : same track, different format/bitrate\n"
            "                               → keep best quality, quarantine rest\n"
            "  Case C — Different versions: 'Extended Mix' vs 'Radio Edit' etc.\n"
            "                               → keep all, reported only\n\n"
            "Quality priority (highest first):\n"
            "  WAV / AIFF  >  FLAC  >  MP3 320  >  MP3 256  >  M4A  >\n"
            "  MP3 192  >  OGG / OPUS  >  MP3 128  >  MP3 <128\n\n"
            "Safety rules:\n"
            "  • Files are MOVED, never deleted — always recoverable\n"
            "  • Ambiguous quality ties are skipped (manual review)\n"
            "  • Case C (versions) is never auto-removed\n\n"
            "Examples:\n"
            "  python pipeline.py dedupe                   # preview only (default)\n"
            "  python pipeline.py dedupe --apply           # quarantine duplicates\n"
            "  python pipeline.py dedupe --path /mnt/music_ssd/KKDJ/\n"
            "  python pipeline.py dedupe --apply --quarantine-dir /music/review/\n"
        ),
    )
    p_dd.add_argument(
        "--apply", action="store_true",
        help="Move duplicate files to quarantine (default: preview only)",
    )
    p_dd.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of pulling from the database. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )
    p_dd.add_argument(
        "--quarantine-dir", metavar="DIR",
        default=None,
        help=(
            f"Directory to move duplicate files into. "
            f"Default: {config.DEDUPE_QUARANTINE_DIR}"
        ),
    )
    p_dd.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- orphan-scan subcommand -----
    p_or = subparsers.add_parser(
        "orphan-scan",
        help="Find DB rows with missing files and untracked audio files on disk",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Detect two distinct categories of orphans:\n\n"
            "  stale_db_rows   — rows in DB whose file no longer exists on disk\n"
            "  untracked_files — audio files on disk with no matching DB row\n\n"
            "Preview by default. Use --apply to mark stale rows in the DB.\n"
            "Untracked files are always reported only — never auto-deleted.\n\n"
            "Examples:\n"
            "  python pipeline.py orphan-scan                 # preview\n"
            "  python pipeline.py orphan-scan --apply         # write stale status\n"
            "  python pipeline.py orphan-scan --no-untracked  # DB audit only\n"
        ),
    )
    p_or.add_argument(
        "--apply", action="store_true",
        help="Mark stale_db_rows as status='stale' in the DB (default: preview only)",
    )
    p_or.add_argument(
        "--path", metavar="DIR",
        help="Library root to scan for untracked files (default: config.SORTED)",
    )
    p_or.add_argument(
        "--no-untracked", action="store_true",
        help="Skip the disk scan — only check DB rows for missing files",
    )
    p_or.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_or.add_argument(
        "--verbose-list", action="store_true",
        help="Print every untracked file path (default: summary only)",
    )

    # ----- path-audit subcommand -----
    p_pa = subparsers.add_parser(
        "path-audit",
        help="Read-only audit of DB/file path inconsistencies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Detect DB/filesystem path inconsistencies without modifying the DB,\n"
            "moving files, or writing tags.\n\n"
            "Finds:\n"
            "  missing files, untracked files, possible renames, duplicate DB\n"
            "  filepath entries, stale queue entries, and orphan DB rows.\n\n"
            "Logs are written to <root>/logs/path_audit/.\n\n"
            "Example:\n"
            "  python3 pipeline.py path-audit --root /mnt/music_ssd/KKDJ\n"
        ),
    )
    p_pa.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root to audit. DB defaults to <root>/logs/processed.db when present.",
    )
    p_pa.add_argument(
        "--include-orphan-candidates", action="store_true",
        help="Enable expensive top-5 orphan candidate scoring and CSV export.",
    )

    # ----- build-tracks subcommand -----
    p_bt = subparsers.add_parser(
        "build-tracks",
        help="Populate tracks from valid non-stale processed_state rows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Build the canonical current-state tracks table from processed_state.\n"
            "This command updates only the tracks table. It never moves files,\n"
            "writes tags, deletes rows, or modifies processed_state.\n\n"
            "Example:\n"
            "  python3 pipeline.py build-tracks --root /mnt/music_ssd/KKDJ\n"
        ),
    )
    p_bt.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root. DB defaults to <root>/logs/processed.db.",
    )

    # ----- metadata-score-online subcommand -----
    p_mso = subparsers.add_parser(
        "metadata-score-online",
        help="Read-only online metadata candidate scoring from tracks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Score Spotify/Deezer-style metadata candidates against tracks without\n"
            "writing audio tags, updating the DB, or changing files.\n\n"
            "Input:\n"
            "  <root>/logs/processed.db tracks table\n\n"
            "Output:\n"
            "  <root>/logs/enrichment/*_enrich_online.jsonl\n\n"
            "Example:\n"
            "  python3 pipeline.py metadata-score-online --root /mnt/music_ssd/KKDJ\n"
        ),
    )
    p_mso.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root. DB defaults to <root>/logs/processed.db.",
    )
    p_mso.add_argument(
        "--mock-providers", action="store_true",
        help="Use deterministic mock Spotify/Deezer candidates for scoring tests.",
    )

    # ----- metadata-repair-scan subcommand -----
    p_mrs = subparsers.add_parser(
        "metadata-repair-scan",
        help="Generate deterministic metadata repair proposals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan tracks for safe artist/title metadata repairs using only the\n"
            "canonical DB and deterministic filename parsing. The scan does not\n"
            "write the DB, audio tags, or library files; it writes only the queue\n"
            "JSONL under <root>/data/intelligence/.\n\n"
            "Example:\n"
            "  python3 pipeline.py metadata-repair-scan --root /mnt/music_ssd/KKDJ\n"
        ),
    )
    p_mrs.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root. DB defaults to <root>/logs/processed.db.",
    )

    # ----- metadata-repair-apply subcommand -----
    p_mra = subparsers.add_parser(
        "metadata-repair-apply",
        help="Dry-run or apply approved metadata repairs to tracks only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read metadata_repair_state.json and apply only approved HIGH/MEDIUM\n"
            "repair proposals to tracks.artist/title. No tags, files, BPM, key,\n"
            "cue fields, or processed_state rows are changed.\n\n"
            "Default mode: dry-run\n"
            "Apply mode : --apply --yes\n\n"
            "Examples:\n"
            "  python3 pipeline.py metadata-repair-apply --root /mnt/music_ssd/KKDJ\n"
            "  python3 pipeline.py metadata-repair-apply --root /mnt/music_ssd/KKDJ --apply --yes\n"
        ),
    )
    p_mra.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root. DB defaults to <root>/logs/processed.db.",
    )
    p_mra.add_argument(
        "--apply", action="store_true",
        help="Apply approved updates to the tracks table. Dry-run is the default.",
    )
    p_mra.add_argument(
        "--yes", action="store_true",
        help="Confirm apply mode. Required together with --apply.",
    )

    # ----- metadata-sanitation-scan subcommand -----
    p_mss = subparsers.add_parser(
        "metadata-sanitation-scan",
        help="Generate deterministic metadata sanitation proposals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan tracks for suspicious artist/title contamination using only the\n"
            "canonical DB and deterministic sanitation rules. The scan does not\n"
            "write the DB, audio tags, or library files; it writes only the queue\n"
            "JSONL under <root>/data/intelligence/.\n\n"
            "Example:\n"
            "  python3 pipeline.py metadata-sanitation-scan --root /mnt/music_ssd/KKDJ\n"
        ),
    )
    p_mss.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root. DB defaults to <root>/logs/processed.db.",
    )

    # ----- metadata-sanitation-apply subcommand -----
    p_msa = subparsers.add_parser(
        "metadata-sanitation-apply",
        help="Dry-run or apply approved metadata sanitation proposals to tracks only",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read metadata_sanitation_state.json and apply only approved artist/title\n"
            "sanitation proposals to tracks.artist/title. No tags, files, BPM, key,\n"
            "cue fields, or processed_state rows are changed.\n\n"
            "Default mode: dry-run\n"
            "Apply mode : --apply --yes\n\n"
            "Examples:\n"
            "  python3 pipeline.py metadata-sanitation-apply --root /mnt/music_ssd/KKDJ\n"
            "  python3 pipeline.py metadata-sanitation-apply --root /mnt/music_ssd/KKDJ --apply --yes\n"
        ),
    )
    p_msa.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root. DB defaults to <root>/logs/processed.db.",
    )
    p_msa.add_argument(
        "--apply", action="store_true",
        help="Apply approved updates to the tracks table. Dry-run is the default.",
    )
    p_msa.add_argument(
        "--yes", action="store_true",
        help="Confirm apply mode. Required together with --apply.",
    )

    # ----- enrichment-review subcommand -----
    p_er = subparsers.add_parser(
        "enrichment-review",
        help="Inspect enrichment review queue entries without modifying anything",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read the enrichment review queue JSONL and print summary counts plus\n"
            "optional filtered entry details. This command is read-only.\n\n"
            "Queue file:\n"
            "  <root>/data/intelligence/enrichment_review_queue.jsonl\n\n"
            "Example:\n"
            "  python3 pipeline.py enrichment-review --root /mnt/music_ssd/KKDJ\n"
        ),
    )
    p_er.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root containing data/intelligence/enrichment_review_queue.jsonl.",
    )
    p_er.add_argument(
        "--confidence",
        choices=["HIGH", "MEDIUM", "LOW"],
        default=None,
        help="Only display entries with this confidence.",
    )
    p_er.add_argument(
        "--action",
        choices=["auto_candidate", "review", "ignore"],
        default=None,
        help="Only display entries with this action suggestion.",
    )
    p_er.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Limit the number of displayed entries.",
    )
    p_er.add_argument(
        "--top-high", metavar="N", type=int, default=None, dest="top_high",
        help="Show the top N HIGH-confidence entries by score before the filtered list.",
    )

    # ----- path-reconcile subcommand -----
    p_pr = subparsers.add_parser(
        "path-reconcile",
        help="Create a dry-run reconciliation plan from path-audit findings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate a reconciliation plan for DB/filesystem path inconsistencies.\n"
            "Default mode is planning-only. --apply-auto-safe-only may update\n"
            "processed_state.filepath for AUTO_SAFE_CANDIDATE rows only; it never\n"
            "moves files, edits queues, or writes tags.\n"
            "Read-only ledger inspection is also available via --ledger and\n"
            "--verify-ledger.\n\n"
            "Output:\n"
            "  <root>/logs/path_reconcile/YYYYMMDD_path_reconcile_plan.json\n"
            "  <root>/logs/path_reconcile/YYYYMMDD_path_reconcile_plan.txt\n\n"
            "Example:\n"
            "  python3 pipeline.py path-reconcile --root /mnt/music_ssd/KKDJ --dry-run\n"
            "  python3 pipeline.py path-reconcile --root /mnt/music_ssd/KKDJ --apply-auto-safe-only\n"
            "  python3 pipeline.py path-reconcile --root /mnt/music_ssd/KKDJ --mark-stale-pstate\n"
            "  python3 pipeline.py path-reconcile --ledger\n"
            "  python3 pipeline.py path-reconcile --verify-ledger <ledger-id>\n"
            "  python3 pipeline.py path-reconcile --validate-plan <plan-json>\n"
        ),
    )
    p_pr.add_argument(
        "--root", metavar="DIR",
        help="Library root to reconcile. DB defaults to <root>/logs/processed.db when present.",
    )
    p_pr.add_argument(
        "--ledger", action="store_true",
        help="List recent reconciliation ledger entries (read-only).",
    )
    p_pr.add_argument(
        "--verify-ledger", metavar="LEDGER_ID",
        dest="verify_ledger",
        help="Verify a reconciliation ledger entry for structure and path consistency (read-only).",
    )
    p_pr.add_argument(
        "--validate-plan", metavar="PLAN_JSON",
        dest="validate_plan",
        help="Validate a reconciliation plan JSON before any future apply mode.",
    )
    p_pr.add_argument(
        "--dry-run", action="store_true",
        help="Required. Generate a plan without applying changes.",
    )
    p_pr.add_argument(
        "--apply", action="store_true",
        help="Reserved for a future release; currently exits with an error.",
    )
    p_pr.add_argument(
        "--apply-auto-safe-only", action="store_true",
        help="Update processed_state.filepath only for AUTO_SAFE_CANDIDATE path references.",
    )
    p_pr.add_argument(
        "--mark-stale-pstate", action="store_true",
        help="Mark superseded processed_state rows stale without changing paths.",
    )

    # ----- playlists subcommand -----
    p_pl = subparsers.add_parser(
        "playlists",
        help="Generate all M3U playlists and Rekordbox XML from the library DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate all playlist files from the current library database without\n"
            "running the full inbox pipeline.  Useful after manual library edits,\n"
            "after running dedupe, or any time you want a fresh export.\n\n"
            "Output structure:\n"
            "  M3U_DIR/           letter playlists (A.m3u8 … Z.m3u8) + _all_tracks.m3u8\n"
            "  M3U_DIR/Genre/     Afro House.m3u8, Amapiano.m3u8 …\n"
            "  M3U_DIR/Energy/    Peak.m3u8, Mid.m3u8, Chill.m3u8\n"
            "  M3U_DIR/Combined/  Peak Afro House.m3u8, Chill Deep House.m3u8 …\n"
            "  M3U_DIR/Key/       1A.m3u8, 1B.m3u8 … 12A.m3u8, 12B.m3u8\n"
            "  M3U_DIR/Route/     Acapella.m3u8, Tool.m3u8, Vocal.m3u8\n"
            "  XML_DIR/           rekordbox_library.xml\n\n"
            "Examples:\n"
            "  python pipeline.py playlists --dry-run\n"
            "  python pipeline.py playlists\n"
            "  python pipeline.py playlists --no-xml\n"
            "  python pipeline.py playlists --path /mnt/music_ssd/\n"
        ),
    )
    p_pl.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be written — create no files",
    )
    p_pl.add_argument(
        "--no-genre", action="store_true",
        help="Skip Genre/ playlists",
    )
    p_pl.add_argument(
        "--no-energy", action="store_true",
        help="Skip Energy/ playlists",
    )
    p_pl.add_argument(
        "--no-combined", action="store_true",
        help="Skip Combined/ playlists",
    )
    p_pl.add_argument(
        "--no-key", action="store_true",
        help="Skip Key/ (Camelot) playlists",
    )
    p_pl.add_argument(
        "--no-route", action="store_true",
        help="Skip Route/ playlists (Acapella, Tool, Vocal)",
    )
    p_pl.add_argument(
        "--no-xml", action="store_true",
        help="Skip Rekordbox XML export",
    )
    p_pl.add_argument(
        "--path", metavar="DIR",
        help=(
            "Override the music root directory for all output paths. "
            "Example: --path /mnt/music_ssd/"
        ),
    )
    p_pl.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- rekordbox-export subcommand -----
    p_rb = subparsers.add_parser(
        "rekordbox-export",
        help="Export library as Rekordbox-ready M3U playlists for Windows (M: drive)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate M3U playlists for Windows with Linux→M: drive path mapping.\n\n"
            "MIK-FIRST POLICY:\n"
            "  Rekordbox XML is owned by Rekordbox + Mixed In Key.\n"
            "  XML export is DISABLED by default to prevent data loss.\n"
            "  Use --force-xml only if you are not using Mixed In Key.\n\n"
            "Default outputs:\n"
            "  _PLAYLISTS_M3U_EXPORT/Genre/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Energy/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Combined/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Key/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Route/*.m3u8\n\n"
            "With --force-xml also outputs:\n"
            "  _REKORDBOX_XML_EXPORT/rekordbox_library.xml\n\n"
            "Tracks missing BPM or Camelot key are EXCLUDED (fast, predictable).\n"
            "To recover them inline use --recover-missing-analysis.\n"
            "For large libraries, run analysis separately first:\n"
            "  python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/\n\n"
            "Path mapping (defaults):\n"
            "  Linux root : /mnt/music_ssd   (= root of M: drive on Windows)\n"
            "  Windows    : M:\\\n\n"
            "Override via env vars:  export RB_LINUX_ROOT=/mnt/music_ssd\n"
            "                        export RB_WIN_DRIVE=M\n"
            "Or via config_local.py: RB_LINUX_ROOT = Path('/mnt/music_ssd')\n"
            "                        RB_WINDOWS_DRIVE = 'M'\n\n"
            "Examples:\n"
            "  python3 pipeline.py rekordbox-export --dry-run\n"
            "  python3 pipeline.py rekordbox-export\n"
            "  python3 pipeline.py rekordbox-export --no-m3u\n"
            "  python3 pipeline.py rekordbox-export --force-xml  # NOT recommended with MIK\n"
            "  python3 pipeline.py rekordbox-export --recover-missing-analysis\n"
            "  python3 pipeline.py rekordbox-export --recover-missing-analysis "
            "--recover-limit 50 --recover-timeout-sec 300\n"
        ),
    )
    p_rb.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be exported — create no files (tag warnings still shown)",
    )
    p_rb.add_argument(
        "--no-xml", action="store_true",
        help="[no-op] Rekordbox XML is now disabled by default. Use --force-xml to enable it.",
    )
    p_rb.add_argument(
        "--force-xml", action="store_true", dest="force_xml",
        help=(
            "Enable Rekordbox XML generation. NOT RECOMMENDED when using Mixed In Key — "
            "the toolkit XML will overwrite MIK cue data on next Rekordbox import. "
            "Use only if you are not using Mixed In Key."
        ),
    )
    p_rb.add_argument(
        "--no-m3u", action="store_true",
        help="Skip M3U playlist generation",
    )
    p_rb.add_argument(
        "--win-drive", metavar="LETTER", default=None,
        help="Windows drive letter (default: M, from RB_WIN_DRIVE env or config)",
    )
    p_rb.add_argument(
        "--linux-root", metavar="PATH", default=None,
        help="Linux path that is the root of the Windows drive (default: /mnt/music_ssd)",
    )
    p_rb.add_argument(
        "--export-root", metavar="PATH", default=None,
        help=(
            "Override the export output root (default: /mnt/music_ssd/KKDJ/). "
            "XML lands in <root>/_REKORDBOX_XML_EXPORT/ and M3U in "
            "<root>/_PLAYLISTS_M3U_EXPORT/"
        ),
    )
    p_rb.add_argument(
        "--recover-missing-analysis",
        action="store_true",
        dest="recover_missing_analysis",
        help=(
            "Run aubio BPM detection and keyfinder-cli key detection for tracks "
            "missing those values before deciding to exclude them. "
            "Off by default — export is fast and predictable without it. "
            "For large libraries, prefer running 'analyze-missing' separately first."
        ),
    )
    p_rb.add_argument(
        "--recover-limit",
        metavar="N",
        type=int,
        default=None,
        dest="recover_limit",
        help=(
            "Maximum number of tracks to attempt analysis on when "
            "--recover-missing-analysis is active (default: unlimited)."
        ),
    )
    p_rb.add_argument(
        "--recover-timeout-sec",
        metavar="N",
        type=float,
        default=None,
        dest="recover_timeout_sec",
        help=(
            "Stop inline analysis after this many seconds when "
            "--recover-missing-analysis is active (default: no timeout)."
        ),
    )
    p_rb.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- analyze-missing subcommand -----
    p_am = subparsers.add_parser(
        "analyze-missing",
        help="Detect BPM and key for tracks missing that data — writes to DB and audio tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the library for tracks where BPM or Camelot key is absent,\n"
            "run aubio (BPM) and keyfinder-cli (key) only on those tracks,\n"
            "and write the results back to the database and audio file tags.\n\n"
            "Safe to run multiple times — will not overwrite valid existing values.\n\n"
            "Examples:\n"
            "  python3 pipeline.py analyze-missing --dry-run\n"
            "  python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/ --apply --yes\n"
            "  python3 pipeline.py analyze-missing --limit 50 --timeout-sec 300\n"
            "  python3 pipeline.py analyze-missing --dry-run --verbose\n"
        ),
    )
    p_am.add_argument(
        "--path", metavar="PATH", default=None,
        help="Restrict analysis to tracks under this directory (default: entire library)",
    )
    p_am.add_argument(
        "--dry-run", action="store_true",
        help="Run detection but do not write to DB or audio file tags",
    )
    p_am.add_argument(
        "--apply", action="store_true",
        help="Write BPM/key results and perform enabled corrupt isolation. Without this flag, preview only.",
    )
    p_am.add_argument(
        "--yes", action="store_true",
        help="Confirm DB, tag, and file isolation writes when used with --apply.",
    )
    p_am.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Maximum number of tracks to process in this run",
    )
    p_am.add_argument(
        "--timeout-sec", metavar="N", type=float, default=None, dest="timeout_sec",
        help="Stop processing after this many seconds (default: no timeout)",
    )
    p_am.add_argument(
        "--min-confidence", metavar="FLOAT", type=float, default=0.0, dest="min_confidence",
        help="Minimum BPM confidence score to accept a result (default: 0.0 — accept all)",
    )
    p_am.add_argument(
        "--file-timeout-sec", metavar="N", type=float, default=10.0, dest="file_timeout_sec",
        help=(
            "Hard per-file wall-clock timeout in seconds (default: 10). "
            "Files that exceed this limit are skipped immediately — prevents "
            "corrupt MP3s from causing multi-hour aubio/librosa resync loops."
        ),
    )
    p_am.add_argument(
        "--no-isolate-corrupt",
        action="store_false",
        dest="isolate_corrupt",
        help=(
            "Disable automatic corrupt-file isolation (isolation is ON by default). "
            "By default, files that fail analysis are moved to <corrupt-dir>/. "
            "Bad/non-file paths are always logged but never moved. "
            "A persistent log is written to logs/analyze_missing/corrupt_moves.txt."
        ),
    )
    p_am.add_argument(
        "--corrupt-dir",
        metavar="PATH",
        default=None,
        dest="corrupt_dir",
        help=(
            "Base directory for quarantined files (default: <--path>/_corrupt when "
            "--path is given, otherwise config.CORRUPT_DIR). "
            "Corrupt audio goes into <corrupt-dir>/audio_failures/. "
            "Example: --corrupt-dir /mnt/music_ssd/KKDJ/_corrupt"
        ),
    )
    p_am.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- audit-quality subcommand -----
    p_aq = subparsers.add_parser(
        "audit-quality",
        help="Audit library for codec/bitrate quality — report LOSSLESS/HIGH/MEDIUM/LOW/UNKNOWN",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the library (or a custom path) for audio quality issues.\n\n"
            "Quality tiers:\n"
            "  LOSSLESS  FLAC / ALAC / WAV / AIFF (lossless codec)\n"
            "  HIGH      lossy (MP3/AAC) >= 256 kbps\n"
            "  MEDIUM    lossy (MP3/AAC) 192–255 kbps\n"
            "  LOW       lossy (MP3/AAC) < 192 kbps  (threshold: --min-lossy-kbps)\n"
            "  UNKNOWN   unreadable file or unrecognized codec/bitrate\n\n"
            "Default mode: non-destructive.  No files are moved or modified.\n"
            "Outputs: terminal summary + CSV/JSON report in logs/reports/audit_quality/\n\n"
            "Optional actions (both off by default):\n"
            "  --move-low-quality DIR   Move LOW files to DIR (folder structure preserved)\n"
            "  --write-tags             Write QUALITY tag to each file\n\n"
            "QUALITY tag locations:\n"
            "  MP3  : TXXX:QUALITY  (ID3v2.3 custom text frame)\n"
            "  FLAC : QUALITY       (Vorbis comment)\n"
            "  M4A  : ----:com.apple.iTunes:QUALITY  (MP4 freeform atom)\n"
            "  AIFF/WAV : skipped safely (tagging unreliable — logged, not failed)\n\n"
            "Examples:\n"
            "  python3 pipeline.py audit-quality\n"
            "  python3 pipeline.py audit-quality --path /mnt/music_ssd/KKDJ/\n"
            "  python3 pipeline.py audit-quality --dry-run --verbose\n"
            "  python3 pipeline.py audit-quality --move-low-quality /music/_low_quality\n"
            "  python3 pipeline.py audit-quality --write-tags\n"
            "  python3 pipeline.py audit-quality --report-format csv\n"
            "  python3 pipeline.py audit-quality --min-lossy-kbps 160\n"
        ),
    )
    p_aq.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of the default sorted library. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )
    p_aq.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Show what would happen (probe + classify + report) "
            "without moving files or writing tags"
        ),
    )
    p_aq.add_argument(
        "--move-low-quality", metavar="DIR",
        default=None,
        dest="move_low_quality",
        help=(
            "Move LOW quality files to this directory. "
            "Relative folder structure under the scanned root is preserved. "
            "Only LOW files are moved — LOSSLESS/HIGH/MEDIUM/UNKNOWN are untouched."
        ),
    )
    p_aq.add_argument(
        "--write-tags", action="store_true",
        dest="write_tags",
        help=(
            "Write a QUALITY tag (LOSSLESS/HIGH/MEDIUM/LOW) to each file. "
            "UNKNOWN files are skipped. Off by default."
        ),
    )
    p_aq.add_argument(
        "--report-format", metavar="FORMATS",
        default="csv,json",
        dest="report_format",
        help=(
            "Comma-separated list of report formats to generate: csv, json. "
            "Default: csv,json"
        ),
    )
    p_aq.add_argument(
        "--min-lossy-kbps", metavar="N", type=int,
        default=192,
        dest="min_lossy_kbps",
        help=(
            "Bitrate threshold (kbps) that separates LOW from MEDIUM. "
            "Files below this value are LOW; >= this value are MEDIUM "
            "(unless >= 256 kbps, which is always HIGH). Default: 192"
        ),
    )
    p_aq.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging and per-file output",
    )

    # ----- cue-suggest subcommand -----
    p_cs = subparsers.add_parser(
        "cue-suggest",
        help="Auto-detect cue points (intro / drop / outro) for library tracks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Analyse audio to detect cue point positions for every track\n"
            "in the library and store results in the database.\n\n"
            "NOTE: These are SUGGESTED positions only. Native Rekordbox\n"
            "hot-cues are NOT written. Review all cues in Rekordbox.\n\n"
            "Cue types detected:\n"
            "  intro_start  — bar 1 (always present, confidence 1.0)\n"
            "  mix_in       — first stable DJ entry point\n"
            "  groove_start — first full-arrangement section\n"
            "  drop         — main energy arrival / impact\n"
            "  breakdown    — energy/density reduction after peak\n"
            "  outro_start  — beginning of mix-out section\n\n"
            "Signal features used (full mode):\n"
            "  RMS energy, low-frequency energy (< 250 Hz, bass/kick proxy),\n"
            "  spectral flux (onset strength). All bar-grid aligned via BPM.\n\n"
            "Fallback: BPM-only heuristic when audio decode fails.\n\n"
            "Output files:\n"
            "  logs/cue_suggest/cue_suggestions.json   (master, all tracks)\n"
            "  logs/cue_suggest/cue_suggestions.csv    (wide format, 1 row/track)\n"
            "  logs/cue_suggest/runs/cues_TIMESTAMP.csv (per-run detail log)\n\n"
            "Examples:\n"
            "  python pipeline.py cue-suggest --dry-run\n"
            "  python pipeline.py cue-suggest --apply --yes\n"
            "  python pipeline.py cue-suggest --limit 20 --track 'Black Coffee'\n"
            "  python pipeline.py cue-suggest --export-format json\n"
        ),
    )
    p_cs.add_argument(
        "--dry-run", action="store_true",
        help="Analyse and print cue points — make no DB writes",
    )
    p_cs.add_argument(
        "--apply", action="store_true",
        help="Store cue suggestions and write enabled outputs. Without this flag, preview only.",
    )
    p_cs.add_argument(
        "--yes", action="store_true",
        help="Confirm DB and output writes when used with --apply.",
    )
    p_cs.add_argument(
        "--min-confidence", type=float, metavar="FLOAT",
        default=config.CUE_SUGGEST_MIN_CONFIDENCE,
        help=(
            f"Minimum confidence score to store a cue point. "
            f"Default: {config.CUE_SUGGEST_MIN_CONFIDENCE}"
        ),
    )
    p_cs.add_argument(
        "--limit", type=int, metavar="N",
        default=None,
        help="Stop after analysing this many tracks (useful for testing).",
    )
    p_cs.add_argument(
        "--track", metavar="NAME",
        default=None,
        help=(
            "Only analyse tracks whose artist, title, or filename contains NAME "
            "(case-insensitive substring). Example: --track 'Enoo Napa'"
        ),
    )
    p_cs.add_argument(
        "--export-format", metavar="FMT",
        default=None,
        help=(
            "Comma-separated list of master output formats to write: json, csv. "
            "Default: both. Example: --export-format json,csv"
        ),
    )
    p_cs.add_argument(
        "--path", metavar="DIR",
        help="Analyse audio files in this directory instead of the library DB.",
    )
    p_cs.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- set-builder subcommand -----
    p_sb = subparsers.add_parser(
        "set-builder",
        help="Build an energy-curve DJ set from the library database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Automatically build a DJ set from tracks in the library database,\n"
            "arranging them across energy phases with harmonic transitions.\n\n"
            "Phases (always in order):\n"
            "  warmup  — gentle intro, Chill/Mid energy\n"
            "  build   — rising energy\n"
            "  peak    — high-energy section\n"
            "  release — brief energy drop after peak\n"
            "  outro   — wind-down / closing\n\n"
            "Vibe presets control how much time each phase gets:\n"
            "  warm     — extended warmup/build, light peak\n"
            "  peak     — strong peak section (40% of set)\n"
            "  deep     — melodic/organic genres preferred, relaxed pacing\n"
            "  driving  — sustained mid-to-peak energy throughout\n\n"
            "Transition strategies:\n"
            "  safest       — highest Camelot × BPM composite\n"
            "  energy_lift  — incoming energy or BPM is higher\n"
            "  smooth_blend — very close BPM + Camelot\n"
            "  best_warmup  — Chill/Mid energy, relaxed BPM\n"
            "  best_late_set — Peak energy, high BPM, strong Camelot\n\n"
            "Output:\n"
            "  SET_BUILDER_OUTPUT_DIR/<name>.m3u8   — playable playlist\n"
            "  SET_BUILDER_OUTPUT_DIR/<name>.csv    — full metadata + transition notes\n\n"
            "Examples:\n"
            "  python pipeline.py set-builder --dry-run\n"
            "  python pipeline.py set-builder --vibe peak --duration 90\n"
            "  python pipeline.py set-builder --vibe deep --genre 'afro house'\n"
            "  python pipeline.py set-builder --strategy energy_lift --name my_set\n"
        ),
    )
    p_sb.add_argument(
        "--dry-run", action="store_true",
        help="Preview the set — write no files",
    )
    p_sb.add_argument(
        "--vibe", metavar="VIBE",
        default="peak",
        choices=["warm", "peak", "deep", "driving"],
        help="Phase-weight preset (warm / peak / deep / driving). Default: peak",
    )
    p_sb.add_argument(
        "--duration", type=int, metavar="MINS",
        default=60,
        help="Target set duration in minutes. Default: 60",
    )
    p_sb.add_argument(
        "--genre", metavar="GENRE",
        default=None,
        help="Restrict track selection to this genre (substring match, e.g. 'afro house')",
    )
    p_sb.add_argument(
        "--strategy", metavar="STRATEGY",
        default="safest",
        choices=["safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"],
        help="Harmonic transition ranking strategy. Default: safest",
    )
    p_sb.add_argument(
        "--structure", metavar="STRUCTURE",
        default="full",
        choices=["full", "simple", "peak_only"],
        help=(
            "Phase structure of the set. "
            "full=warmup→build→peak→release→outro (default), "
            "simple=build→peak→outro, "
            "peak_only=peak only"
        ),
    )
    p_sb.add_argument(
        "--max-bpm-jump", metavar="BPM", type=float, default=3.0,
        dest="max_bpm_jump",
        help=(
            "Maximum allowed absolute BPM difference between consecutive tracks. "
            "Candidates exceeding this are hard-rejected. Default: 3. "
            "Set to 0 to disable."
        ),
    )
    p_sb.add_argument(
        "--no-strict-harmonic", action="store_false", dest="strict_harmonic",
        help=(
            "Disable strict harmonic key validation. By default only same key, "
            "±1 same mode, and relative major/minor (A↔B) transitions are allowed; "
            "this flag falls back to scoring-only."
        ),
    )
    p_sb.add_argument(
        "--artist-repeat-window", metavar="N", type=int, default=3,
        dest="artist_repeat_window",
        help=(
            "Hard-reject any candidate whose primary artist appeared within the "
            "last N tracks. Default: 3. Set to 0 to disable."
        ),
    )
    p_sb.add_argument(
        "--start-energy", metavar="TIER",
        default=None,
        choices=["Chill", "Mid", "Peak"],
        help="Preferred energy tier for the first track",
    )
    p_sb.add_argument(
        "--end-energy", metavar="TIER",
        default=None,
        choices=["Chill", "Mid", "Peak"],
        help="Preferred energy tier for the last track",
    )
    p_sb.add_argument(
        "--name", metavar="NAME",
        default=None,
        help="Base name for output files (no extension). Default: auto-generated timestamp",
    )
    p_sb.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- harmonic-suggest subcommand -----
    p_hs = subparsers.add_parser(
        "harmonic-suggest",
        help="Suggest the best next tracks using harmonic + BPM + energy scoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Given a track (or a key + BPM pair), rank every track in the\n"
            "library by harmonic compatibility and print the top suggestions.\n\n"
            "Scoring factors:\n"
            "  Camelot compatibility  (35%)  — Camelot wheel distance\n"
            "  BPM compatibility      (30%)  — tempo delta, halftime/doubletime aware\n"
            "  Energy compatibility   (20%)  — Peak / Mid / Chill tier match\n"
            "  Genre compatibility    (15%)  — exact / related / different\n\n"
            "Ranking strategies:\n"
            "  safest       — highest Camelot × BPM composite\n"
            "  energy_lift  — incoming energy or BPM is higher\n"
            "  smooth_blend — very close BPM + Camelot\n"
            "  best_warmup  — Chill/Mid energy, relaxed BPM, harmonic\n"
            "  best_late_set — Peak energy, high BPM, strong Camelot\n\n"
            "Examples:\n"
            "  python pipeline.py harmonic-suggest --track '/music/.../track.mp3'\n"
            "  python pipeline.py harmonic-suggest --key 8A --bpm 128\n"
            "  python pipeline.py harmonic-suggest --track ... --strategy energy_lift\n"
            "  python pipeline.py harmonic-suggest --key 5B --bpm 124 --top-n 20 --json\n"
        ),
    )
    _hs_group = p_hs.add_mutually_exclusive_group()
    _hs_group.add_argument(
        "--track", metavar="PATH",
        help="Path to a track already in the library DB to suggest from",
    )
    p_hs.add_argument(
        "--key", metavar="KEY",
        help="Camelot key of the current track (e.g. 8A, 5B) — used with --bpm",
    )
    p_hs.add_argument(
        "--bpm", type=float, metavar="BPM",
        help="BPM of the current track — used with --key",
    )
    p_hs.add_argument(
        "--strategy", metavar="STRATEGY",
        default="safest",
        choices=["safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"],
        help="Ranking strategy. Default: safest",
    )
    p_hs.add_argument(
        "--top-n", type=int, metavar="N",
        default=10,
        help="Number of suggestions to return. Default: 10",
    )
    p_hs.add_argument(
        "--energy", metavar="TIER",
        default=None,
        choices=["Chill", "Mid", "Peak"],
        help="Treat the current track as this energy tier (used with --key/--bpm)",
    )
    p_hs.add_argument(
        "--genre", metavar="GENRE",
        default=None,
        help="Genre of the current track (used with --key/--bpm for genre scoring)",
    )
    p_hs.add_argument(
        "--json", action="store_true",
        help="Write suggestions to a JSON file in HARMONIC_SUGGEST_OUTPUT_DIR",
    )
    p_hs.add_argument(
        "--dry-run", action="store_true",
        help="Print suggestions only — do not write JSON output",
    )
    p_hs.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- generate-docs subcommand -----
    p_gd = subparsers.add_parser(
        "generate-docs",
        help="Regenerate COMMANDS.txt, README.md, and COMMANDS.html from the command registry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Reads the centralized command registry (modules/doc_registry.py) and\n"
            "regenerates all documentation files from it.\n\n"
            "Files regenerated (by default):\n"
            "  COMMANDS.txt   — plain-text command reference\n"
            "  README.md      — subcommands section spliced in-place\n"
            "  COMMANDS.html  — dark-themed HTML with sidebar navigation\n\n"
            "Examples:\n"
            "  python3 pipeline.py generate-docs\n"
            "  python3 pipeline.py generate-docs --dry-run\n"
            "  python3 pipeline.py generate-docs --format txt,html\n"
            "  python3 pipeline.py generate-docs --output-dir /tmp/docs\n"
        ),
    )
    p_gd.add_argument(
        "--dry-run", action="store_true",
        help="Preview generated content to stdout — write no files",
    )
    p_gd.add_argument(
        "--output-dir", metavar="DIR", default=None,
        help=(
            "Write generated files to this directory instead of the project root. "
            "The directory is created if it does not exist."
        ),
    )
    p_gd.add_argument(
        "--format", metavar="FORMATS", default="txt,md,html",
        help="Comma-separated list of formats to generate: txt, md, html. Default: txt,md,html",
    )

    # ----- validate-docs subcommand -----
    p_vd = subparsers.add_parser(
        "validate-docs",
        help="Check that COMMANDS.txt is in sync with the command registry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Reads the command registry (modules/doc_registry.py) and COMMANDS.txt,\n"
            "then reports:\n"
            "  - Commands in the registry that are MISSING from COMMANDS.txt\n"
            "  - Entries in COMMANDS.txt that have NO matching registry entry (stale)\n\n"
            "Use --strict to make the command exit 1 on any mismatch (useful in CI\n"
            "and pre-commit hooks).\n\n"
            "Examples:\n"
            "  python3 pipeline.py validate-docs\n"
            "  python3 pipeline.py validate-docs --strict\n"
        ),
    )
    p_vd.add_argument(
        "--strict", action="store_true",
        help=(
            "Exit with code 1 if any commands are missing from or stale in COMMANDS.txt. "
            "Without this flag, mismatches are printed as warnings but the command exits 0."
        ),
    )

    # ----- metadata-sanitize subcommand -----
    p_msan = subparsers.add_parser(
        "metadata-sanitize",
        help="Offline, deterministic tag sanitation (preview by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Offline metadata sanitation — fully deterministic, no AI, no network.\n\n"
            "Scans audio files and applies conservative, rule-based fixes to:\n"
            "  album        — clear if it contains URLs, path fragments, or promo junk\n"
            "  isrc         — clear if the value is not a valid ISRC (CC-XXX-YY-NNNNNNN)\n"
            "  title        — strip leading numeric prefixes; fix spacing/separators/parens\n"
            "  artist       — strip URLs; normalize ft./featuring → feat.; fix whitespace\n"
            "  organization — clear placeholder junk (unknown/n/a/none); fix whitespace\n\n"
            "Safe by design:\n"
            "  • Preview is the default — no files modified without --apply\n"
            "  • Never invents metadata; never guesses missing values\n"
            "  • If a transform is uncertain, it is skipped\n"
            "  • Every change is logged with a reason code\n\n"
            "Workflow position: run BEFORE ai-normalize / artist-intelligence / metadata-enrich-online\n\n"
            "Examples:\n"
            "  python3 pipeline.py metadata-sanitize --input /mnt/music_ssd/inbox\n"
            "  python3 pipeline.py metadata-sanitize --input /mnt/music_ssd/inbox --limit 50\n"
            "  python3 pipeline.py metadata-sanitize --input /mnt/music_ssd/inbox --apply\n"
            "  python3 pipeline.py metadata-sanitize --input /mnt/music_ssd/inbox --apply --output-json sanitize_log.json\n"
        ),
    )
    p_msan.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to scan (recursive)",
    )
    p_msan.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Maximum number of files to process in this run (default: no limit)",
    )
    p_msan.add_argument(
        "--apply", action="store_true",
        help="Write changes to audio file tags. Without this flag, changes are only previewed.",
    )
    p_msan.add_argument(
        "--output-json", metavar="FILE", default=None, dest="output_json",
        help="Save full change log to this JSON file.",
    )
    p_msan.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging and show unmodified corrupt files.",
    )
    p_msan.add_argument(
        "--log-dir", metavar="DIR", default=None, dest="log_dir",
        help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/metadata-sanitize/",
    )
    p_msan.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_msan.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )

    # ----- metadata-sanitize-rollback subcommand -----
    p_msr = subparsers.add_parser(
        "metadata-sanitize-rollback",
        help="Revert bad title_bare_number_stripped changes using a sanitize JSON log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Roll back title changes made by metadata-sanitize that stripped a leading\n"
            "number from titles where the number was actually part of the real title\n"
            "(e.g. '4 You' → 'You', '15 Minutes' → 'Minutes').\n\n"
            "Reads a JSON log produced by:\n"
            "  python3 pipeline.py metadata-sanitize --output-json <logfile>\n\n"
            "Safe by design:\n"
            "  • Preview is the default — no files modified without --apply\n"
            "  • --only-suspicious limits reverts to known false-positive patterns\n"
            "  • Every revert is logged with a reason code\n\n"
            "Suspicious patterns (first word of stripped title):\n"
            "  You, Me, Us, Them, Minutes, Hours, Days, Seconds,\n"
            "  Love, Life, One, Two\n\n"
            "Examples:\n"
            "  python3 pipeline.py metadata-sanitize-rollback --jsonl sanitize_log.json\n"
            "  python3 pipeline.py metadata-sanitize-rollback --jsonl sanitize_log.json --only-suspicious\n"
            "  python3 pipeline.py metadata-sanitize-rollback --jsonl sanitize_log.json --only-suspicious --apply\n"
        ),
    )
    p_msr.add_argument(
        "--jsonl", metavar="FILE", required=True,
        help="JSON log file produced by metadata-sanitize --output-json.",
    )
    p_msr.add_argument(
        "--rule", metavar="RULE", default="title_bare_number_stripped",
        help="Rule code to filter for revert (default: title_bare_number_stripped).",
    )
    p_msr.add_argument(
        "--preview", action="store_true", default=True,
        help="Show what would be reverted without writing (default).",
    )
    p_msr.add_argument(
        "--apply", action="store_true",
        help="Write reverted titles to audio file tags.",
    )
    p_msr.add_argument(
        "--only-suspicious", action="store_true", dest="only_suspicious",
        help="Only revert cases where the stripped result is a known false-positive pattern.",
    )

    # ----- title-number-recover subcommand -----
    p_tnr = subparsers.add_parser(
        "title-number-recover",
        help="Recover title tags damaged by over-aggressive bare-number stripping",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan audio files and recover title tags where a leading number was\n"
            "incorrectly stripped from a real title (e.g. '15 Minutes' → 'Minutes').\n\n"
            "Detection:\n"
            "  1. Parse title from filename using 'Artist - Title.ext' convention.\n"
            "  2. Read current embedded title tag.\n"
            "  3. If filename title starts with N<space>rest and current tag == rest,\n"
            "     propose restoring the full filename title.\n\n"
            "Recovery is only proposed for suspicious cases:\n"
            "  • rest starts with a known title word (You/Me/Minutes/Hours/Days/Love…)\n"
            "  • OR leading number is >= 10 (two-digit numbers rarely appear as track indices)\n\n"
            "Obvious track-index junk (single-digit + unprotected word) is silently skipped.\n\n"
            "Safe by design:\n"
            "  • Preview is the default — no files modified without --apply\n"
            "  • Only writes the title tag — no filename renames\n\n"
            "Examples:\n"
            "  python3 pipeline.py title-number-recover --input /mnt/music_ssd/KKDJ\n"
            "  python3 pipeline.py title-number-recover --input /mnt/music_ssd/KKDJ --verbose\n"
            "  python3 pipeline.py title-number-recover --input /mnt/music_ssd/KKDJ --apply\n"
        ),
    )
    p_tnr.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to scan (recursive).",
    )
    p_tnr.add_argument(
        "--apply", action="store_true",
        help="Write recovered titles to audio file tags. Without this flag, changes are only previewed.",
    )
    p_tnr.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show skipped files and no-match details.",
    )
    p_tnr.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Maximum number of files to process.",
    )

    # ----- artist-repair subcommand -----
    p_arep = subparsers.add_parser(
        "artist-repair",
        help="Detect and repair broken concatenated artist tags (preview by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Detect artist tags where two names were merged without a separator,\n"
            "e.g. 'Afrikan RootsLebo' → 'Afrikan Roots, Lebo'.\n\n"
            "Detection signal: [a-z][A-Z] boundary NOT at a word start.\n"
            "  Merge    : 'Afrikan RootsLebo'       → 's' not preceded by space ✓\n"
            "  Safe     : 'Alan Dixon mOat (UK)'    → 'm' preceded by space → skipped\n"
            "  Safe     : 'AVG (IT)'                → no lowercase→uppercase transitions\n\n"
            "Confidence gates:\n"
            "  HIGH (≥ 0.85) both sides are known artists → eligible with --apply\n"
            "  MEDIUM (0.65) one side known → review queue only\n"
            "  LOW    (0.45) neither side known → review queue only\n\n"
            "Review queue : data/intelligence/artist_repair_queue.json\n"
            "Quarantine   : .BIN/CHKARTISTNAMES/ (with --move-artist-review --apply)\n\n"
            "Recommended position in pipeline:\n"
            "  metadata-sanitize → artist-repair → artist-intelligence → ai-normalize\n\n"
            "Examples:\n"
            "  python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted/A\n"
            "  python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted --apply\n"
            "  python3 pipeline.py artist-repair --input /mnt/music_ssd/KKDJ/sorted "
            "--apply --move-artist-review\n"
        ),
    )
    p_arep.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to scan (recursive).",
    )
    p_arep.add_argument(
        "--apply", action="store_true",
        help=(
            "Write HIGH-confidence repairs to artist tags. "
            "Medium/low-confidence candidates are queued for review — never auto-applied."
        ),
    )
    p_arep.add_argument(
        "--move-artist-review", action="store_true", dest="move_artist_review",
        help=(
            "Move review-queue files to .BIN/CHKARTISTNAMES/ for manual correction. "
            "Requires --apply to execute; preview shows WOULD MOVE."
        ),
    )
    p_arep.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Maximum number of files to process in this run.",
    )
    p_arep.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )
    p_arep.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_arep.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )
    p_arep.add_argument(
        "--log-dir", metavar="DIR", default=None, dest="log_dir",
        help="Directory for run logs. Default: logs/artist-repair/",
    )

    # ----- artist-repair-review subcommand -----
    p_arr = subparsers.add_parser(
        "artist-repair-review",
        help="Review, approve, reject, or apply queued artist repairs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Human approval workflow for artist repairs that could not be auto-applied.\n\n"
            "Queue file: data/intelligence/artist_repair_queue.json\n\n"
            "Workflow:\n"
            "  1. Run artist-repair to populate the queue with MEDIUM/LOW-confidence candidates.\n"
            "  2. Use --list to inspect queued entries with their index numbers.\n"
            "  3. Use --approve INDEX / --reject INDEX to mark decisions.\n"
            "  4. Use --apply-approved to write only approved repairs to audio file tags.\n\n"
            "Safety:\n"
            "  • --list and --approve/--reject only modify the JSON queue file, not audio tags.\n"
            "  • --apply-approved is the only flag that touches audio file tags.\n"
            "  • Missing files are silently skipped.\n"
            "  • Tags changed since queue creation are skipped with a warning.\n"
            "  • Entries are never auto-approved — human decision required.\n\n"
            "Examples:\n"
            "  python3 pipeline.py artist-repair-review --list\n"
            "  python3 pipeline.py artist-repair-review --approve 0\n"
            "  python3 pipeline.py artist-repair-review --reject 1\n"
            "  python3 pipeline.py artist-repair-review --apply-approved\n"
            "  python3 pipeline.py artist-repair-review --approve 2 --apply-approved\n"
        ),
    )
    p_arr.add_argument(
        "--list", action="store_true",
        help="Show all queued entries with index numbers and approval status.",
    )
    p_arr.add_argument(
        "--approve", metavar="INDEX", type=int, default=None,
        help="Mark the entry at INDEX as approved.",
    )
    p_arr.add_argument(
        "--reject", metavar="INDEX", type=int, default=None,
        help="Mark the entry at INDEX as rejected.",
    )
    p_arr.add_argument(
        "--apply-approved", action="store_true", dest="apply_approved",
        help="Write all approved (unapplied) repairs to audio file tags.",
    )

    # ----- artist-intelligence subcommand -----
    p_ari = subparsers.add_parser(
        "artist-intelligence",
        help="Deterministic artist normalization, alias resolution, and review queue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Artist Intelligence — deterministic artist normalization layer.\n\n"
            "Parses compound artist strings, resolves canonical names via the\n"
            "alias store, and proposes corrected artist tags.  Changes are shown\n"
            "as a diff preview and only written when --apply is explicitly passed.\n\n"
            "Safe by design:\n"
            "  • Preview is the default — no writes without --apply\n"
            "  • Never rewrites the title field\n"
            "  • Never moves '(feat ...)' from title into the artist field\n"
            "  • Low-confidence candidates go to the review queue, not auto-applied\n\n"
            "Alias store  : data/intelligence/artist_aliases.json\n"
            "Review queue : data/intelligence/artist_review_queue.json\n\n"
            "Examples:\n"
            "  python3 pipeline.py artist-intelligence --input /mnt/music_ssd/inbox --dry-run\n"
            "  python3 pipeline.py artist-intelligence --input /mnt/music_ssd/inbox --limit 20\n"
            "  python3 pipeline.py artist-intelligence --input /mnt/music_ssd/inbox --apply\n"
            "  python3 pipeline.py artist-intelligence --input /mnt/music_ssd/inbox --output-json preview.json\n"
        ),
    )
    p_ari.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to process (scanned recursively)",
    )
    p_ari.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Maximum number of files to process in this run (default: no limit)",
    )
    p_ari.add_argument(
        "--dry-run", action="store_true",
        help="Parse and show diffs — write no files",
    )
    p_ari.add_argument(
        "--apply", action="store_true",
        help="Write high-confidence changes to audio file tags. Cannot be combined with --dry-run.",
    )
    p_ari.add_argument(
        "--min-confidence", metavar="FLOAT", type=float, default=0.90,
        dest="min_confidence",
        help="Minimum confidence (0.0–1.0) required to apply a change. Default: 0.90",
    )
    p_ari.add_argument(
        "--output-json", metavar="FILE", default=None, dest="output_json",
        help="Save the full diff preview to this JSON file.",
    )
    p_ari.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_ari.add_argument(
        "--log-dir", metavar="DIR", default=None, dest="log_dir",
        help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/artist-intelligence/",
    )
    p_ari.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_ari.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )

    # ----- ai-normalize subcommand -----
    p_ain = subparsers.add_parser(
        "ai-normalize",
        help="Use a local Ollama model to propose normalized metadata (safe preview by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "AI-assisted metadata normalization using a local Ollama model.\n\n"
            "The model proposes normalized artist, title, version, label, remixers,\n"
            "and featured_artists for each track. Changes are shown as a diff preview\n"
            "and only written when --apply is explicitly passed.\n\n"
            "Safe by design:\n"
            "  • Default mode is preview — no file changes without --apply\n"
            "  • Only writes: artist, title (+ version), label\n"
            "  • Never touches BPM, key, cue points, or genre\n"
            "  • Skips tracks where confidence < --min-confidence\n\n"
            "Requirements:\n"
            "  Ollama must be running locally: ollama serve\n"
            "  Model must be pulled:           ollama pull qwen2.5-coder:3b\n\n"
            "Examples:\n"
            "  python3 pipeline.py ai-normalize --input ~/Music/test_batch --dry-run\n"
            "  python3 pipeline.py ai-normalize --input ~/Music/test_batch --limit 20\n"
            "  python3 pipeline.py ai-normalize --input ~/Music/inbox --output-json preview.json\n"
            "  python3 pipeline.py ai-normalize --input ~/Music/inbox --apply --min-confidence 0.85\n"
            "  python3 pipeline.py ai-normalize --input ~/Music/inbox --model qwen2.5:3b\n"
        ),
    )
    p_ain.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to normalize (scanned recursively)",
    )
    p_ain.add_argument(
        "--model", metavar="MODEL",
        default=config.OLLAMA_DEFAULT_MODEL,
        help=f"Ollama model name to use. Default: {config.OLLAMA_DEFAULT_MODEL}",
    )
    p_ain.add_argument(
        "--ollama-url", metavar="URL",
        default=config.OLLAMA_BASE_URL,
        dest="ollama_url",
        help=f"Ollama server base URL. Default: {config.OLLAMA_BASE_URL}",
    )
    p_ain.add_argument(
        "--timeout", metavar="SECS", type=int,
        default=config.OLLAMA_TIMEOUT,
        help=f"Per-request timeout in seconds. Default: {config.OLLAMA_TIMEOUT}",
    )
    p_ain.add_argument(
        "--limit", metavar="N", type=int,
        default=50,
        help="Maximum number of files to process in this run. Default: 50",
    )
    p_ain.add_argument(
        "--dry-run", action="store_true",
        help="Run AI inference and show diffs — write no files",
    )
    p_ain.add_argument(
        "--apply", action="store_true",
        help=(
            "Write high-confidence changes to audio file tags. "
            "Cannot be combined with --dry-run."
        ),
    )
    p_ain.add_argument(
        "--min-confidence", metavar="FLOAT", type=float,
        default=0.80,
        dest="min_confidence",
        help=(
            "Minimum model confidence (0.0–1.0) required to apply a change. "
            "Default: 0.80"
        ),
    )
    p_ain.add_argument(
        "--output-json", metavar="FILE",
        default=None,
        dest="output_json",
        help=(
            "Save the full diff preview to this JSON file. "
            "Useful for reviewing proposals before running --apply."
        ),
    )
    p_ain.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_ain.add_argument(
        "--pre-sanitize", action="store_true",
        dest="pre_sanitize",
        help=(
            "Run metadata-sanitize before AI normalization. "
            "Clears junk tags (URLs, invalid ISRC, promo text, email addresses) "
            "so the model sees clean input. "
            "Respects --apply: without it both steps preview only, no files are written."
        ),
    )
    p_ain.add_argument(
        "--log-dir", metavar="DIR", default=None, dest="log_dir",
        help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/ai-normalize/",
    )
    p_ain.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_ain.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )

    # ----- build-fewshot subcommand -----
    p_bfs = subparsers.add_parser(
        "build-fewshot",
        help="Build a curated few-shot example file from accepted ai-normalize decisions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Read data/intelligence/accepted_examples.jsonl, select a diverse subset\n"
            "of high-quality examples, and write data/intelligence/fewshot_examples.jsonl.\n\n"
            "The fewshot file is a snapshot — it is overwritten on each run.\n"
            "Use --limit to control the target size.\n\n"
            "Examples:\n"
            "  python3 pipeline.py build-fewshot\n"
            "  python3 pipeline.py build-fewshot --limit 20\n"
        ),
    )
    p_bfs.add_argument(
        "--limit", metavar="N", type=int, default=30,
        help="Maximum number of examples to include in the fewshot file. Default: 30",
    )

    # ----- metadata-enrich-online subcommand -----
    p_meo = subparsers.add_parser(
        "metadata-enrich-online",
        help="Enrich track metadata from Spotify and Deezer (preview by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Look up track metadata from online music APIs and propose conservative\n"
            "tag improvements: album name, record label, and ISRC.\n\n"
            "Sources (in order):\n"
            "  1. Spotify Web API  — ISRC lookup first, then artist+title search\n"
            "                        Requires SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET\n"
            "  2. Deezer API       — fallback when Spotify is unavailable or low-confidence\n"
            "                        No credentials required\n\n"
            "Safety rules:\n"
            "  • Default mode is preview — no file changes without --apply\n"
            "  • Artist is never written (owned by the artist-intelligence layer)\n"
            "  • Existing version/remix info in the title is always preserved\n"
            "  • Label only overwritten when current is empty (or conf >= 0.95)\n"
            "  • Min confidence default 0.80 — ISRC exact matches always pass (0.98)\n\n"
            "Credentials:\n"
            "  export SPOTIFY_CLIENT_ID=your_id\n"
            "  export SPOTIFY_CLIENT_SECRET=your_secret\n"
            "  Obtain at: https://developer.spotify.com/dashboard\n\n"
            "Examples:\n"
            "  python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --dry-run\n"
            "  python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --limit 20\n"
            "  python3 pipeline.py metadata-enrich-online --input ~/Music/inbox \\\n"
            "      --apply --min-confidence 0.80\n"
            "  python3 pipeline.py metadata-enrich-online --input ~/Music/inbox \\\n"
            "      --output-json enrich_preview.json\n"
        ),
    )
    p_meo.add_argument(
        "--input", metavar="DIR", required=True,
        help="Directory of audio files to enrich (scanned recursively)",
    )
    p_meo.add_argument(
        "--dry-run", action="store_true",
        help="Run API lookups and show diffs — write no files",
    )
    p_meo.add_argument(
        "--apply", action="store_true",
        help="Write high-confidence changes to audio file tags. Cannot combine with --dry-run.",
    )
    p_meo.add_argument(
        "--limit", metavar="N", type=int, default=50,
        help="Maximum number of files to process in this run. Default: 50",
    )
    p_meo.add_argument(
        "--min-confidence", metavar="FLOAT", type=float,
        default=config.ENRICH_ONLINE_MIN_CONFIDENCE,
        dest="min_confidence",
        help=(
            f"Minimum confidence (0.0–1.0) required to apply a change. "
            f"Default: {config.ENRICH_ONLINE_MIN_CONFIDENCE}"
        ),
    )
    p_meo.add_argument(
        "--spotify-client-id", metavar="ID",
        default=None,
        dest="spotify_client_id",
        help="Spotify API client ID (overrides SPOTIFY_CLIENT_ID env var)",
    )
    p_meo.add_argument(
        "--spotify-client-secret", metavar="SECRET",
        default=None,
        dest="spotify_client_secret",
        help="Spotify API client secret (overrides SPOTIFY_CLIENT_SECRET env var)",
    )
    p_meo.add_argument(
        "--output-json", metavar="FILE",
        default=None,
        dest="output_json",
        help="Save full results to this JSON file for offline review",
    )
    p_meo.add_argument(
        "--enable-traxsource", action="store_true", default=False,
        dest="enable_traxsource",
        help=(
            "Enable Traxsource as a dance-music specialist fallback source. "
            "Disabled by default — Traxsource's scraper is prone to 403 blocks "
            "and adds latency. Enable manually when Spotify/Deezer results are "
            "insufficient for house/Afro/deep tracks."
        ),
    )
    p_meo.add_argument(
        "--clean-junk-only", action="store_true", default=False,
        dest="clean_junk_only",
        help=(
            "Run only the junk metadata cleaner — no API calls. "
            "Detects and clears garbage album values (URLs, piracy watermarks, "
            "filename artifacts). Use --apply to write changes; default is preview. "
            "Example: python3 pipeline.py metadata-enrich-online "
            "--input ~/Music/inbox --clean-junk-only --apply"
        ),
    )
    p_meo.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_meo.add_argument(
        "--move-ignored", action="store_true", default=False,
        dest="move_ignored",
        help=(
            "Move low-confidence files (decision_code=skipped_low_score) to "
            "/home/koolkatdj/Music/music/IGNORED/, preserving the original folder "
            "structure. review items and matched files are never moved. "
            "Use with --apply to combine enrichment writes with cleanup in one pass."
        ),
    )
    p_meo.add_argument(
        "--log-dir", metavar="DIR", default=None, dest="log_dir",
        help="Directory for run logs (.log, .jsonl, _summary.json). Default: logs/metadata-enrich-online/",
    )
    p_meo.add_argument(
        "--force", action="store_true",
        help="Reprocess all files, ignoring processed-state tracking.",
    )
    p_meo.add_argument(
        "--reset-stage", action="store_true", dest="reset_stage",
        help="Clear processed-state tracking for this stage before running.",
    )

    # ----- review-queue subcommand -----
    p_rq = subparsers.add_parser(
        "review-queue",
        help="Interactively review medium-confidence enrichment results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Review items queued by metadata-enrich-online (confidence 0.75–0.89 or\n"
            "ambiguous matches).  Each entry shows the proposed changes so you can\n"
            "apply or discard them without re-running the full enrichment.\n\n"
            "Queue file: data/intelligence/enrichment_review_queue.json\n\n"
            "Actions (interactive mode):\n"
            "  a / apply  — write proposed changes to the audio file, remove entry\n"
            "  s / skip   — remove entry from queue without writing\n"
            "  d / delete — alias for skip\n"
            "  n / next   — leave entry in queue, move to next\n"
            "  q / quit   — exit without further changes\n\n"
            "Examples:\n"
            "  python3 pipeline.py review-queue --list-only\n"
            "  python3 pipeline.py review-queue --apply --yes\n"
        ),
    )
    p_rq.add_argument(
        "--list-only", action="store_true", default=False,
        dest="list_only",
        help="Print all queued items and exit — do not prompt for actions",
    )
    p_rq.add_argument(
        "--apply", action="store_true",
        help="Enable interactive queue changes. Without this flag, list-only dry-run mode is used.",
    )
    p_rq.add_argument(
        "--yes", action="store_true",
        help="Confirm queue/tag writes when used with --apply.",
    )

    # ----- enrichment-apply-approved subcommand -----
    p_eaa = subparsers.add_parser(
        "enrichment-apply-approved",
        help="Apply approved enrichment metadata to the tracks table (dry-run by default)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Controlled enrichment apply step.\n\n"
            "Reads data/intelligence/enrichment_review_state.json and applies only\n"
            "approved HIGH-confidence metadata to the tracks table. No audio tags\n"
            "or filenames are changed.\n\n"
            "Default mode: dry-run\n"
            "Apply mode : --apply --yes\n\n"
            "Examples:\n"
            "  python3 pipeline.py enrichment-apply-approved --root /mnt/music_ssd/KKDJ\n"
            "  python3 pipeline.py enrichment-apply-approved --root /mnt/music_ssd/KKDJ --apply --yes\n"
        ),
    )
    p_eaa.add_argument(
        "--root", metavar="DIR", required=True,
        help="Library root containing logs/processed.db and enrichment review state.",
    )
    p_eaa.add_argument(
        "--apply", action="store_true",
        help="Apply approved updates to the tracks table. Dry-run is the default.",
    )
    p_eaa.add_argument(
        "--yes", action="store_true",
        help="Confirm apply mode. Required together with --apply.",
    )
    p_eaa.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # Warn if running outside a virtualenv (advisory only, non-fatal)
    _warn_if_no_venv()

    # Enable tab-completion when argcomplete is installed.
    # Activate per-command:  eval "$(register-python-argcomplete pipeline.py)"
    # Activate globally:     activate-global-python-argcomplete
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass  # argcomplete is optional; silently skip if not installed

    args = parser.parse_args()

    if args.command == "generate-docs":
        sys.exit(run_generate_docs(args))

    if args.command == "validate-docs":
        sys.exit(run_validate_docs(args))

    if args.command == "artist-merge":
        sys.exit(run_artist_merge(args))

    if args.command == "artist-folder-clean":
        sys.exit(run_artist_folder_clean(args))

    if args.command == "label-intel":
        sys.exit(run_label_intel(args))

    if args.command == "label-clean":
        sys.exit(run_label_clean(args))

    if args.command == "metadata-clean":
        sys.exit(run_metadata_clean(args))

    if args.command == "extract-track-metadata":
        sys.exit(run_extract_track_metadata(args))

    if args.command == "tag-normalize":
        sys.exit(run_tag_normalize(args))

    if args.command == "filename-normalize":
        sys.exit(run_filename_normalize(args))

    if args.command == "library-organize":
        sys.exit(run_library_organize(args))

    if args.command == "db-prune-stale":
        sys.exit(run_db_prune_stale(args))

    if args.command == "convert-audio":
        sys.exit(run_convert_audio(args))

    if args.command == "dedupe":
        sys.exit(run_dedupe(args))

    if args.command == "orphan-scan":
        sys.exit(run_orphan_scan(args))

    if args.command == "path-audit":
        sys.exit(run_path_audit(args))

    if args.command == "build-tracks":
        sys.exit(run_build_tracks(args))

    if args.command == "metadata-score-online":
        sys.exit(run_metadata_score_online(args))

    if args.command == "metadata-repair-scan":
        sys.exit(run_metadata_repair_scan(args))

    if args.command == "metadata-repair-apply":
        sys.exit(run_metadata_repair_apply(args))

    if args.command == "metadata-sanitation-scan":
        sys.exit(run_metadata_sanitation_scan(args))

    if args.command == "metadata-sanitation-apply":
        sys.exit(run_metadata_sanitation_apply(args))

    if args.command == "enrichment-review":
        sys.exit(run_enrichment_review(args))

    if args.command == "enrichment-apply-approved":
        sys.exit(run_enrichment_apply_approved(args))

    if args.command == "path-reconcile":
        sys.exit(run_path_reconcile(args))

    if args.command == "playlists":
        sys.exit(run_playlists(args))

    if args.command == "analyze-missing":
        sys.exit(run_analyze_missing(args))

    if args.command == "audit-quality":
        sys.exit(run_audit_quality(args))

    if args.command == "rekordbox-export":
        sys.exit(run_rekordbox_export(args))

    if args.command == "cue-suggest":
        sys.exit(run_cue_suggest(args))

    if args.command == "set-builder":
        sys.exit(run_set_builder(args))

    if args.command == "harmonic-suggest":
        sys.exit(run_harmonic_suggest(args))

    if args.command == "metadata-sanitize":
        db.init_db()
        from modules.metadata_sanitize import run_metadata_sanitize
        from utils.prompt_logger import start_run
        _rl = start_run("metadata-sanitize", Path(getattr(args, "log_dir", None) or config.PIPELINE_LOGS_DIR))
        _rl.print_paths()
        print()
        _rc = run_metadata_sanitize(args)
        _rl.finish(exit_code=_rc)
        sys.exit(_rc)

    if args.command == "metadata-sanitize-rollback":
        from modules.metadata_sanitize import run_metadata_sanitize_rollback
        sys.exit(run_metadata_sanitize_rollback(args))

    if args.command == "title-number-recover":
        from modules.metadata_sanitize import run_title_number_recover
        sys.exit(run_title_number_recover(args))

    if args.command == "artist-repair":
        db.init_db()
        from modules.artist_repair import run_artist_repair
        _rc = run_artist_repair(args)
        sys.exit(_rc)

    if args.command == "artist-repair-review":
        from modules.artist_repair import run_artist_repair_review
        sys.exit(run_artist_repair_review(args))

    if args.command == "artist-intelligence":
        db.init_db()
        from intelligence.artist.runner import run_artist_intelligence
        from utils.prompt_logger import start_run
        _rl = start_run("artist-intelligence", Path(getattr(args, "log_dir", None) or config.PIPELINE_LOGS_DIR))
        _rl.print_paths()
        print()
        _rc = run_artist_intelligence(args)
        _rl.finish(exit_code=_rc)
        sys.exit(_rc)

    if args.command == "ai-normalize":
        db.init_db()
        from ai.normalizer import run_ai_normalize
        from utils.prompt_logger import start_run
        _rl = start_run("ai-normalize", Path(getattr(args, "log_dir", None) or config.PIPELINE_LOGS_DIR))
        _rl.print_paths()
        print()
        if getattr(args, "pre_sanitize", False):
            import argparse as _ap
            from modules.metadata_sanitize import run_metadata_sanitize
            _san_args = _ap.Namespace(
                input=args.input,
                apply=getattr(args, "apply", False),
                limit=getattr(args, "limit", None),
                output_json=None,
                verbose=getattr(args, "verbose", False),
            )
            _rc = run_metadata_sanitize(_san_args)
            if _rc != 0:
                _rl.finish(exit_code=_rc)
                sys.exit(_rc)
        _rc = run_ai_normalize(args)
        _rl.finish(exit_code=_rc)
        sys.exit(_rc)

    if args.command == "metadata-enrich-online":
        db.init_db()
        from intelligence.enrichment.runner import run_metadata_enrich_online
        from utils.prompt_logger import start_run
        _rl = start_run("metadata-enrich-online", Path(getattr(args, "log_dir", None) or config.PIPELINE_LOGS_DIR))
        _rl.print_paths()
        print()
        _rc = run_metadata_enrich_online(args)
        _rl.finish(exit_code=_rc)
        sys.exit(_rc)

    if args.command == "review-queue":
        sys.exit(run_review_queue_command(args))

    if args.command == "build-fewshot":
        from ai.review_dataset import build_fewshot
        n = build_fewshot(limit=args.limit)
        if n > 0:
            print(f"Wrote {n} example(s) to {config.AI_FEWSHOT_EXAMPLES}")
            sys.exit(0)
        else:
            print(
                "No examples written. Run 'ai-normalize --apply' first to accumulate "
                "accepted_examples.jsonl.",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.label_enrich_from_library:
        sys.exit(run_label_enrichment_from_library(args.verbose))

    sys.exit(run_pipeline(
        dry_run          = args.dry_run,
        skip_beets       = args.skip_beets,
        skip_analysis    = args.skip_analysis,
        verbose          = args.verbose,
        reanalyze        = args.reanalyze,
        custom_path      = _resolve_path(getattr(args, "path", None)),
        # MIK-first: cue suggest is OFF by default; --force-cue-suggest enables it.
        # --skip-cue-suggest is a deprecated no-op (now the default).
        skip_cue_suggest = not getattr(args, "force_cue_suggest", False),
    ))


if __name__ == "__main__":
    main()
