"""
Report generator — produces a human-readable text report after each pipeline run.

Report includes:
    - Run summary (counts, duration)
    - Rejected files (with reasons)
    - Duplicates found (with original)
    - Files needing manual review (_unsorted)
    - All OK tracks (artist, title, BPM, key)

Reports are written to REPORTS_DIR/pipeline_YYYYMMDD_HHMMSS.txt
"""
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import config
import db

log = logging.getLogger(__name__)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _sep(char: str = "-", width: int = 70) -> str:
    return char * width


def generate(run_id: int, duration_sec: float, dry_run: bool = False) -> Path:
    """
    Write a full report for this pipeline run. Returns the report path.
    """
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    prefix  = "dryrun_" if dry_run else ""
    outpath = config.REPORTS_DIR / f"{prefix}pipeline_{_now_str()}.txt"

    rejected  = db.get_tracks_by_status("rejected")
    dupes     = db.get_unresolved_duplicates(run_id)
    unsorted  = db.get_tracks_by_status("needs_review")
    ok_tracks = db.get_all_ok_tracks()
    errors    = db.get_tracks_by_status("error")

    # Gather run stats
    with db.get_conn() as conn:
        run_row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id=?", (run_id,)
        ).fetchone()

    lines: List[str] = []
    w = lines.append

    w(_sep("="))
    w(f"CRATEIQ PIPELINE REPORT")
    w(f"Run ID:    {run_id}")
    w(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    w(f"Dry-run:   {'YES' if dry_run else 'no'}")
    w(f"Duration:  {duration_sec:.1f}s")
    w(_sep("="))
    w("")

    # Summary
    w("SUMMARY")
    w(_sep())
    if run_row:
        w(f"  Inbox files scanned : {run_row['inbox_count']}")
        w(f"  Successfully processed: {run_row['processed']}")
        w(f"  Rejected (bad quality): {run_row['rejected']}")
        w(f"  Duplicates quarantined: {run_row['duplicates']}")
        w(f"  Needs manual review  : {run_row['unsorted']}")
        w(f"  Errors               : {run_row['errors']}")
    w(f"  Total OK in library  : {len(ok_tracks)}")
    w("")

    # Rejected
    if rejected:
        w("REJECTED FILES  (moved to /music/rejected/)")
        w(_sep())
        for row in rejected:
            w(f"  {row['filename']}")
            w(f"    Reason: {row['error_msg'] or 'unknown'}")
        w("")

    # Duplicates
    if dupes:
        w("DUPLICATES  (moved to /music/duplicates/) — review before deleting")
        w(_sep())
        for row in dupes:
            w(f"  DUPE:     {Path(row['duplicate']).name}")
            w(f"  ORIGINAL: {Path(row['original']).name}")
        w("")
        w("  ACTION: Check /music/duplicates/ and delete files you don't want.")
        w("")

    # Needs review
    if unsorted:
        w("NEEDS MANUAL REVIEW  (in /music/library/sorted/_unsorted/)")
        w(_sep())
        for row in unsorted:
            w(f"  {row['filename']}")
            w(f"    Reason: {row['error_msg'] or 'could not auto-organize'}")
        w("")
        w("  ACTION: Tag these manually in kid3-cli or beets, then re-run pipeline.")
        w("")

    # Errors
    if errors:
        w("PIPELINE ERRORS")
        w(_sep())
        for row in errors:
            w(f"  {row['filename']} — {row['error_msg'] or 'unknown error'}")
        w("")

    # OK tracks (full list)
    if ok_tracks:
        w(f"PROCESSED TRACKS  ({len(ok_tracks)} total)")
        w(_sep())
        w(f"  {'Artist':<30}  {'Title':<35}  {'BPM':>5}  {'Key':<5}")
        w(f"  {'-'*30}  {'-'*35}  {'-'*5}  {'-'*5}")
        for row in ok_tracks:
            artist = (row["artist"] or "")[:30]
            title  = (row["title"]  or Path(row["filepath"]).stem)[:35]
            bpm    = f"{int(round(float(row['bpm'])))}" if row["bpm"] else "?"
            key    = row["key_camelot"] or "?"
            w(f"  {artist:<30}  {title:<35}  {bpm:>5}  {key:<5}")
        w("")

    w(_sep("="))
    w("END OF REPORT")
    w(_sep("="))

    report_text = "\n".join(lines)
    outpath.write_text(report_text, encoding="utf-8")
    log.info("Report written: %s", outpath)
    return outpath


def generate_readme(run_id: int, duration_sec: float, dry_run: bool = False) -> Path:
    """
    Write (overwrite) a human-friendly README.md summarising the library state.
    Intended for _LOGS/README.md — gives a quick overview without opening the DB.
    """
    outpath = config.README_PATH
    outpath.parent.mkdir(parents=True, exist_ok=True)

    now_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ok_tracks   = db.get_all_ok_tracks()
    rejected    = db.get_tracks_by_status("rejected")
    dupes       = db.get_unresolved_duplicates(run_id)
    unsorted    = db.get_tracks_by_status("needs_review")
    errors      = db.get_tracks_by_status("error")

    # Gather run stats
    with db.get_conn() as conn:
        run_row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id=?", (run_id,)
        ).fetchone()

    # Count genres from ok tracks
    from modules.playlists import normalize_genre
    genre_counter: Counter = Counter()
    artists: list = []
    for row in ok_tracks:
        genre = normalize_genre(row["genre"])
        if genre:
            genre_counter[genre] += 1
        if row["artist"] and row["artist"] not in artists:
            artists.append(row["artist"])

    # Top genres (up to 20)
    top_genres = genre_counter.most_common(20)

    sanitize_enabled = getattr(config, "SANITIZE_TAGS", True)

    lines: List[str] = []
    w = lines.append

    w("# CrateIQ — Library README")
    w("")
    w(f"_Generated: {now_str}_  ")
    w(f"_Run ID: {run_id}_  ")
    if dry_run:
        w("_⚠ This was a dry-run — no files were changed._")
    w("")

    w("## Description")
    w("")
    w("Automated DJ library preparation pipeline running on Ubuntu Studio 24.")
    w("Outputs a Rekordbox-ready library for Windows import via exFAT transfer drive.")
    w("")

    w("## Features Enabled")
    w("")
    w(f"- [{'x' if sanitize_enabled else ' '}] Metadata sanitization (SANITIZE_TAGS={sanitize_enabled})")
    w("- [x] BPM detection (aubio)")
    w("- [x] Key detection (keyfinder-cli, Camelot notation)")
    w("- [x] Genre playlists (M3U + Rekordbox XML)")
    w("- [x] Duplicate detection (rmlint)")
    w("- [x] Quality control (ffprobe)")
    w("- [x] Track history + rollback (SQLite)")
    w("- [x] Human-readable processing log")
    w("")

    w("## Folder Structure")
    w("")
    w("```")
    w(f"{config.MUSIC_ROOT}/")
    w("├── inbox/              ← drop new music here")
    w("├── library/")
    w("│   └── sorted/         ← organized library (Artist/Track.mp3)")
    w("│       ├── _unsorted/  ← files that couldn't be auto-organized")
    w("│       └── _compilations/")
    w("├── duplicates/         ← quarantined duplicates (review before deleting)")
    w("├── rejected/           ← files that failed quality control")
    w("├── playlists/")
    w("│   ├── m3u/            ← letter playlists (A.m3u8, B.m3u8, ...)")
    w("│   │   └── Genre/      ← genre playlists (Afro House.m3u8, ...)")
    w("│   └── xml/            ← rekordbox_library.xml")
    w("└── logs/")
    w("    ├── README.md           ← this file")
    w("    ├── processing_log.txt  ← human-readable run log")
    w("    ├── processed.db        ← SQLite state DB")
    w("    └── reports/            ← per-run detailed reports")
    w("```")
    w("")

    w("## Last Run Summary")
    w("")
    if run_row:
        w(f"| Metric | Count |")
        w(f"|--------|-------|")
        w(f"| Inbox files scanned   | {run_row['inbox_count']} |")
        w(f"| Successfully processed | {run_row['processed']} |")
        w(f"| Rejected (bad quality) | {run_row['rejected']} |")
        w(f"| Duplicates quarantined | {run_row['duplicates']} |")
        w(f"| Needs manual review    | {run_row['unsorted']} |")
        w(f"| Errors                 | {run_row['errors']} |")
        w(f"| Duration               | {duration_sec:.1f}s |")
    w(f"| **Total OK in library** | **{len(ok_tracks)}** |")
    w("")

    # Checkpoints
    if rejected or dupes or unsorted or errors:
        w("### Action Required")
        w("")
        if dupes:
            w(f"- **{len(dupes)} duplicate(s)** in `{config.DUPLICATES}/` — review and delete unwanted copies")
        if unsorted:
            w(f"- **{len(unsorted)} file(s)** in `_unsorted/` — tag manually and re-run")
        if rejected:
            w(f"- **{len(rejected)} file(s)** rejected — check quality or format")
        if errors:
            w(f"- **{len(errors)} error(s)** — see `logs/reports/` for details")
        w("")

    # Genres
    if top_genres:
        w("## Detected Genres")
        w("")
        for genre, count in top_genres:
            w(f"- {genre} ({count} track{'s' if count != 1 else ''})")
        w("")

    # Artists (up to 30 new ones from this run — simplified: all known artists)
    if artists:
        sample = sorted(set(artists))[:30]
        w("## Artists in Library")
        w(f"_{len(artists)} total — showing first {min(len(artists), 30)}_")
        w("")
        for artist in sample:
            w(f"- {artist}")
        if len(artists) > 30:
            w(f"- _(… {len(artists) - 30} more)_")
        w("")

    w("---")
    w(f"_CrateIQ v{config.PIPELINE_VERSION} — generated {now_str}_")

    outpath.write_text("\n".join(lines), encoding="utf-8")
    log.info("README written: %s", outpath)
    return outpath


def print_summary(run_id: int, duration_sec: float) -> None:
    """Print a short summary to stdout (for pipeline.sh to display)."""
    rejected = db.get_tracks_by_status("rejected")
    dupes    = db.get_unresolved_duplicates(run_id)
    unsorted = db.get_tracks_by_status("needs_review")
    ok       = db.get_all_ok_tracks()

    print(_sep("="))
    print("CRATEIQ — RUN COMPLETE")
    print(_sep())
    print(f"  OK tracks in library : {len(ok)}")
    print(f"  Rejected             : {len(rejected)}")
    print(f"  Duplicates found     : {len(dupes)}")
    print(f"  Needs review         : {len(unsorted)}")
    print(f"  Duration             : {duration_sec:.1f}s")
    print(_sep())

    if dupes:
        print(f"  [!] CHECKPOINT: Review /music/duplicates/ ({len(dupes)} files)")
    if unsorted:
        print(f"  [!] CHECKPOINT: Review /music/library/sorted/_unsorted/ ({len(unsorted)} files)")
    if not dupes and not unsorted:
        print("  [✓] No checkpoints required — fully automated this run")

    print(_sep("="))
