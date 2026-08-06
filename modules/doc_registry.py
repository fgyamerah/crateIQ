"""
CrateIQ — Command Registry (single source of truth).

Every subcommand and main-pipeline flag is listed here.  The generate-docs
and validate-docs subcommands read this registry to regenerate COMMANDS.txt,
README.md, and COMMANDS.html, and to verify that those docs are up-to-date.

Structure of each entry (dict):
    name        — command name, or "MAIN" for the main pipeline
    category    — section heading used in the docs
    description — single-line summary (shown in --help and tables)
    usage       — usage line shown at the top of each section
    flags       — list of flag dicts:  {flag, meta, description, default}
                  meta is the metavar placeholder (e.g. "DIR"); omit if flag
                  is a boolean store_true action.
    examples    — list of example strings (each is a full command)
    notes       — optional multi-line string shown below the flags table
"""

VERSION = "1.7.0"

REGISTRY = [

    # -----------------------------------------------------------------------
    # MAIN PIPELINE
    # -----------------------------------------------------------------------
    {
        "name": "MAIN",
        "category": "MAIN PIPELINE",
        "description": (
            "Run the full 9-step inbox pipeline: QC → dedupe → organize → "
            "sanitize → analyze → tag → cue-suggest → playlists → report."
        ),
        "usage": "python3 pipeline.py [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": (
                    "Run all detection and analysis but make no file changes. "
                    "Does not write tags, move files, or modify the DB."
                ),
            },
            {
                "flag": "--skip-beets",
                "description": (
                    "Skip Beets/MusicBrainz lookup. Uses the Python filename-parser "
                    "fallback for organizing files. Useful when Beets is slow, "
                    "unavailable, or gives wrong results."
                ),
            },
            {
                "flag": "--skip-analysis",
                "description": (
                    "[Legacy — rarely needed] Force-skip all BPM/key analysis even "
                    "for tracks missing those values. The pipeline is MIK-first and "
                    "only fills gaps by default; use this only to skip analysis entirely."
                ),
            },
            {
                "flag": "--reanalyze",
                "description": (
                    "Re-run BPM+key analysis on sorted library tracks that are missing "
                    "BPM or key in the DB. Does not process new inbox files."
                ),
            },
            {
                "flag": "--force-cue-suggest",
                "description": (
                    "Enable cue point suggestion after tag writing. "
                    "DISABLED by default (MIK-first policy: cue data is owned by MIK). "
                    "Only use this if you are not using Mixed In Key."
                ),
            },
            {
                "flag": "--label-enrich-from-library",
                "description": (
                    "Enrich the label database using real BPM/genre data from all "
                    "processed tracks. Reads the TPUB/organization tag from every "
                    "status=ok track in the DB — no audio re-analysis."
                ),
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": (
                    "Override the music root directory for this run. "
                    "Replaces DJ_MUSIC_ROOT and all derived paths (inbox, library, "
                    "playlists, logs, DB) with paths relative to DIR."
                ),
            },
            {
                "flag": "--verbose / -v",
                "description": "Enable debug-level logging.",
            },
        ],
        "examples": [
            "python3 pipeline.py",
            "python3 pipeline.py --dry-run",
            "python3 pipeline.py --skip-beets",
            "python3 pipeline.py --path /mnt/music_ssd/KKDJ/",
            "python3 pipeline.py --reanalyze",
            "python3 pipeline.py --force-cue-suggest",
            "python3 pipeline.py --label-enrich-from-library",
        ],
        "notes": (
            "MIK-FIRST POLICY:\n"
            "  Mixed In Key is the authoritative source for BPM, key, and cue data.\n"
            "  The pipeline NEVER overwrites existing BPM, key, or cue values.\n"
            "  Analysis only runs for tracks where those values are absent.\n\n"
            "IDEMPOTENCY:\n"
            "  Already-processed tracks (TXXX:PROCESSED=1 + status=ok in DB) are\n"
            "  skipped automatically — safe to re-run at any time.\n\n"
            "Supported audio formats: .mp3 .flac .wav .aiff .aif .m4a .ogg .opus"
        ),
    },

    # -----------------------------------------------------------------------
    # LIBRARY MAINTENANCE
    # -----------------------------------------------------------------------
    {
        "name": "dedupe",
        "category": "LIBRARY MAINTENANCE",
        "description": "Detect and quarantine duplicate audio files across the library.",
        "usage": "python3 pipeline.py dedupe [FLAGS]",
        "flags": [
            {"flag": "--dry-run", "description": "Preview duplicate groups — move no files."},
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan this directory instead of pulling tracks from the database.",
            },
            {
                "flag": "--quarantine-dir",
                "meta": "DIR",
                "description": "Directory to move duplicate files into.",
                "default": "library/sorted/_duplicates",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py dedupe --dry-run",
            "python3 pipeline.py dedupe",
            "python3 pipeline.py dedupe --path /mnt/music_ssd/KKDJ/",
            "python3 pipeline.py dedupe --quarantine-dir /music/review/dupes/",
        ],
        "notes": (
            "Detection cases:\n"
            "  Case A  Exact duplicate (same SHA-256 hash)       -> quarantine all but one\n"
            "  Case B  Same track, different quality/format       -> quarantine lower quality\n"
            "  Case C  Different versions (Extended vs Radio)     -> report only, never moved\n\n"
            "Quality priority (highest first):\n"
            "  WAV/AIFF > FLAC > MP3 320 > MP3 256 > M4A > MP3 192 > OGG/OPUS > MP3 128"
        ),
    },
    {
        "name": "metadata-clean",
        "category": "LIBRARY MAINTENANCE",
        "description": "Strip URL watermarks and promo junk from all metadata fields across the library.",
        "usage": "python3 pipeline.py metadata-clean [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Preview all field changes — make no file writes.",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan audio files in this directory instead of pulling from the DB.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py metadata-clean --dry-run",
            "python3 pipeline.py metadata-clean",
            "python3 pipeline.py metadata-clean --path /mnt/music_ssd/KKDJ/",
        ],
        "notes": (
            "Fields cleaned: title, artist, album, albumartist, genre, comment,\n"
            "  organization/label (TPUB), grouping (TIT1), catalog number\n\n"
            "What is removed:\n"
            "  URLs/domains      https://djsoundtop.com, TraxCrate.com, www.djcity.com\n"
            "  DJ pool phrases   fordjonly, djcity, zipdj, musicafresca, promo only\n"
            "  Promo phrases     official audio, free download, downloaded from\n"
            "  Comment noise     Camelot keys, BPM strings (e.g. 6A | Gm | 121 BPM)"
        ),
    },
    {
        "name": "tag-normalize",
        "category": "LIBRARY MAINTENANCE",
        "description": "Standardize MP3 ID3 tag format for Rekordbox (ID3v2.4 → ID3v2.3, remove ID3v1).",
        "usage": "python3 pipeline.py tag-normalize [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Detect issues without writing any files.",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan this directory instead of the default sorted library.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py tag-normalize --dry-run",
            "python3 pipeline.py tag-normalize",
            "python3 pipeline.py tag-normalize --path /mnt/music_ssd/KKDJ/sorted/",
        ],
        "notes": "Non-MP3 files (FLAC, WAV, AIFF, M4A, OGG, OPUS) are always skipped.",
    },
    {
        "name": "analyze-missing",
        "category": "LIBRARY MAINTENANCE",
        "description": "Detect BPM and key for tracks missing that data — writes to DB and audio tags.",
        "usage": "python3 pipeline.py analyze-missing [FLAGS]",
        "flags": [
            {
                "flag": "--path",
                "meta": "PATH",
                "description": "Restrict analysis to tracks under this directory.",
                "default": "entire library",
            },
            {
                "flag": "--dry-run",
                "description": "Run detection but do not write to DB or audio file tags.",
            },
            {
                "flag": "--limit",
                "meta": "N",
                "description": "Maximum number of tracks to process in this run.",
            },
            {
                "flag": "--timeout-sec",
                "meta": "N",
                "description": "Stop processing after this many seconds.",
                "default": "no timeout",
            },
            {
                "flag": "--min-confidence",
                "meta": "FLOAT",
                "description": "Minimum BPM confidence score to accept a result.",
                "default": "0.0 (accept all)",
            },
            {
                "flag": "--file-timeout-sec",
                "meta": "N",
                "description": "Hard per-file wall-clock timeout in seconds.",
                "default": "10",
            },
            {
                "flag": "--no-isolate-corrupt",
                "description": "Disable automatic corrupt-file isolation (isolation is ON by default).",
            },
            {
                "flag": "--corrupt-dir",
                "meta": "PATH",
                "description": "Base directory for quarantined corrupt files.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py analyze-missing",
            "python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/",
            "python3 pipeline.py analyze-missing --limit 50 --timeout-sec 300",
            "python3 pipeline.py analyze-missing --dry-run --verbose",
            "python3 pipeline.py analyze-missing --file-timeout-sec 20",
        ],
        "notes": (
            "Safe to run multiple times — will not overwrite valid existing values.\n"
            "This is the MIK-first analysis: only fills gaps left by Mixed In Key."
        ),
    },
    {
        "name": "audit-quality",
        "category": "LIBRARY MAINTENANCE",
        "description": "Audit library for codec/bitrate quality — classify into LOSSLESS/HIGH/MEDIUM/LOW/UNKNOWN.",
        "usage": "python3 pipeline.py audit-quality [FLAGS]",
        "flags": [
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan this directory.",
                "default": "library/sorted",
            },
            {
                "flag": "--dry-run",
                "description": "Probe and classify; log intended actions; write no files.",
            },
            {
                "flag": "--move-low-quality",
                "meta": "DIR",
                "description": "Move only LOW quality files to DIR (folder structure preserved).",
            },
            {
                "flag": "--write-tags",
                "description": "Write a QUALITY tag to each file. UNKNOWN files are skipped.",
            },
            {
                "flag": "--report-format",
                "meta": "FORMATS",
                "description": "Comma-separated list of output formats: csv, json.",
                "default": "csv,json",
            },
            {
                "flag": "--min-lossy-kbps",
                "meta": "N",
                "description": "Bitrate threshold (kbps) separating LOW from MEDIUM.",
                "default": "192",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging and per-file output."},
        ],
        "examples": [
            "python3 pipeline.py audit-quality",
            "python3 pipeline.py audit-quality --path /mnt/music_ssd/KKDJ/",
            "python3 pipeline.py audit-quality --dry-run --verbose",
            "python3 pipeline.py audit-quality --move-low-quality /music/_low_quality",
            "python3 pipeline.py audit-quality --write-tags",
            "python3 pipeline.py audit-quality --report-format csv",
            "python3 pipeline.py audit-quality --min-lossy-kbps 160",
        ],
        "notes": (
            "Quality tiers:\n"
            "  LOSSLESS  FLAC / ALAC / WAV / AIFF (codec-based; bitrate irrelevant)\n"
            "  HIGH      lossy codec (MP3/AAC) >= 256 kbps\n"
            "  MEDIUM    lossy codec 192-255 kbps\n"
            "  LOW       lossy codec < 192 kbps  (threshold: --min-lossy-kbps)\n"
            "  UNKNOWN   ffprobe could not read file or codec/bitrate unrecognized\n\n"
            "QUALITY tag locations:\n"
            "  MP3  : TXXX:QUALITY  (ID3v2.3 custom text frame)\n"
            "  FLAC : QUALITY       (Vorbis comment)\n"
            "  M4A  : ----:com.apple.iTunes:QUALITY  (MP4 freeform atom)\n"
            "  AIFF/WAV : skipped safely (tagging unreliable; no error raised)"
        ),
    },
    {
        "name": "artist-folder-clean",
        "category": "LIBRARY MAINTENANCE",
        "description": "Fix bad artist folder names across the library (Camelot prefixes, URL junk, symbols).",
        "usage": "python3 pipeline.py artist-folder-clean [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Scan and report only. No file moves (same as default).",
            },
            {
                "flag": "--apply",
                "description": "Apply all recoverable renames and merges. Unrecoverable folders go to the review report only.",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan this directory instead of the default sorted library.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py artist-folder-clean --dry-run",
            "python3 pipeline.py artist-folder-clean --apply",
            "python3 pipeline.py artist-folder-clean --apply --path /mnt/music_ssd/KKDJ/",
        ],
        "notes": (
            "Detection rules:\n"
            "  pure_camelot    e.g. '10B', '1A'               -> review\n"
            "  camelot_prefix  e.g. '1A - Afrikan Roots'       -> rename/merge\n"
            "  bracket_junk    e.g. '[HouseGrooveSA]'          -> review\n"
            "  url_junk        e.g. 'djcity.com'               -> review\n"
            "  symbol_heavy    < 40% alphanumeric chars        -> review"
        ),
    },
    {
        "name": "artist-merge",
        "category": "LIBRARY MAINTENANCE",
        "description": "Merge artist folder spelling variants into a single canonical folder.",
        "usage": "python3 pipeline.py artist-merge [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Scan and report only. No file moves.",
            },
            {
                "flag": "--apply",
                "description": "Apply safe merges. Uncertain merges go to the review report.",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan this directory instead of the default sorted library.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py artist-merge --dry-run",
            "python3 pipeline.py artist-merge --apply",
            "python3 pipeline.py artist-merge --apply --path /mnt/music_ssd/KKDJ/",
        ],
    },
    {
        "name": "db-prune-stale",
        "category": "LIBRARY MAINTENANCE",
        "description": "Mark DB rows stale when the file no longer exists on disk.",
        "usage": "python3 pipeline.py db-prune-stale [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Report stale rows without marking them.",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Library root to search for files.",
                "default": "RB_LINUX_ROOT from config (/mnt/music_ssd)",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py db-prune-stale --dry-run",
            "python3 pipeline.py db-prune-stale",
            "python3 pipeline.py db-prune-stale --path /mnt/music_ssd/KKDJ/",
        ],
        "notes": (
            "Stale rows are marked status='stale' — they are NEVER deleted.\n"
            "After pruning, rekordbox-export will no longer warn about them."
        ),
    },

    # -----------------------------------------------------------------------
    # AUDIO CONVERSION
    # -----------------------------------------------------------------------
    {
        "name": "convert-audio",
        "category": "AUDIO CONVERSION",
        "description": "Convert .m4a files to .aiff with parallel ffmpeg, preserving metadata and archiving originals.",
        "usage": "python3 pipeline.py convert-audio --src PATH --dst PATH --archive PATH [FLAGS]",
        "flags": [
            {
                "flag": "--src",
                "meta": "PATH",
                "description": "Root directory containing .m4a files (scanned recursively). REQUIRED.",
            },
            {
                "flag": "--dst",
                "meta": "PATH",
                "description": "Root directory for output .aiff files. REQUIRED.",
            },
            {
                "flag": "--archive",
                "meta": "PATH",
                "description": "Root directory where original .m4a files are moved after success. REQUIRED.",
            },
            {
                "flag": "--workers",
                "meta": "N",
                "description": "Number of parallel ffmpeg workers.",
                "default": "4",
            },
            {
                "flag": "--overwrite",
                "description": "Re-convert files that already have a .aiff output in --dst.",
            },
            {
                "flag": "--verify-tolerance-sec",
                "meta": "SECS",
                "description": "Maximum allowed duration difference (seconds) between source and output.",
                "default": "1.0",
            },
            {
                "flag": "--dry-run",
                "description": "Probe sources and show what would be converted. Write no files.",
            },
            {
                "flag": "--no-progress",
                "description": "Disable the tqdm progress bar even when tqdm is installed.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            (
                "python3 pipeline.py convert-audio \\\n"
                "    --src /downloads/m4a \\\n"
                "    --dst /mnt/music_ssd/KKDJ/inbox \\\n"
                "    --archive /mnt/music_ssd/originals_m4a"
            ),
            (
                "python3 pipeline.py convert-audio \\\n"
                "    --src /downloads --dst /music --archive /archive \\\n"
                "    --workers 8 --verify-tolerance-sec 2.0 --dry-run"
            ),
        ],
        "notes": (
            "Workflow per file:\n"
            "  1. ffprobe validates source (corrupt files skipped)\n"
            "  2. ffmpeg converts with metadata copied (-map_metadata 0)\n"
            "  3. Output verified: ffprobe check + duration delta <= tolerance\n"
            "  4. On success: original .m4a moved to --archive\n"
            "  5. On failure: original left untouched; broken output removed\n\n"
            "Output codec: pcm_s16be (16-bit big-endian PCM AIFF).\n\n"
            "Environment overrides:\n"
            "  FFMPEG_BIN   path to ffmpeg binary (default: ffmpeg)\n"
            "  FFPROBE_BIN  path to ffprobe binary (default: ffprobe)"
        ),
    },

    # -----------------------------------------------------------------------
    # PLAYLISTS AND EXPORT
    # -----------------------------------------------------------------------
    {
        "name": "playlists",
        "category": "PLAYLISTS AND EXPORT",
        "description": "Generate all M3U playlists and Rekordbox XML from the library DB.",
        "usage": "python3 pipeline.py playlists [FLAGS]",
        "flags": [
            {"flag": "--dry-run", "description": "Show what would be written — create no files."},
            {"flag": "--no-genre", "description": "Skip Genre/ playlists."},
            {"flag": "--no-energy", "description": "Skip Energy/ playlists."},
            {"flag": "--no-combined", "description": "Skip Combined/ playlists."},
            {"flag": "--no-key", "description": "Skip Key/ (Camelot) playlists."},
            {"flag": "--no-route", "description": "Skip Route/ playlists (Acapella, Tool, Vocal)."},
            {"flag": "--no-xml", "description": "Skip Rekordbox XML export."},
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Override the music root directory for all output paths.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py playlists --dry-run",
            "python3 pipeline.py playlists",
            "python3 pipeline.py playlists --no-xml",
            "python3 pipeline.py playlists --no-key --no-route",
            "python3 pipeline.py playlists --path /mnt/music_ssd/",
        ],
        "notes": (
            "Output structure:\n"
            "  M3U_DIR/              letter playlists (A.m3u8...Z.m3u8) + _all_tracks.m3u8\n"
            "  M3U_DIR/Genre/        Afro House.m3u8, Amapiano.m3u8 ...\n"
            "  M3U_DIR/Energy/       Peak.m3u8, Mid.m3u8, Chill.m3u8\n"
            "  M3U_DIR/Combined/     Peak Afro House.m3u8, Chill Deep House.m3u8 ...\n"
            "  M3U_DIR/Key/          1A.m3u8 ... 12B.m3u8\n"
            "  M3U_DIR/Route/        Acapella.m3u8, Tool.m3u8, Vocal.m3u8\n"
            "  XML_DIR/              rekordbox_library.xml"
        ),
    },
    {
        "name": "rekordbox-export",
        "category": "PLAYLISTS AND EXPORT",
        "description": "Export library as Rekordbox-ready M3U playlists for Windows (Linux→Windows path mapping).",
        "usage": "python3 pipeline.py rekordbox-export [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Preview what would be exported. Tag warnings still shown.",
            },
            {
                "flag": "--force-xml",
                "description": (
                    "Enable Rekordbox XML generation. NOT RECOMMENDED when using Mixed In Key — "
                    "toolkit XML will overwrite MIK cue data on next Rekordbox import."
                ),
            },
            {
                "flag": "--no-xml",
                "description": "[No-op] XML is disabled by default. Use --force-xml to enable.",
            },
            {"flag": "--no-m3u", "description": "Skip M3U playlist generation."},
            {
                "flag": "--win-drive",
                "meta": "LETTER",
                "description": "Windows drive letter for path mapping.",
                "default": "M (from RB_WIN_DRIVE env or config)",
            },
            {
                "flag": "--linux-root",
                "meta": "PATH",
                "description": "Linux path corresponding to the root of the Windows drive.",
                "default": "/mnt/music_ssd",
            },
            {
                "flag": "--export-root",
                "meta": "PATH",
                "description": "Override the export output root directory.",
            },
            {
                "flag": "--recover-missing-analysis",
                "description": (
                    "Run inline BPM/key analysis for tracks missing those values "
                    "before deciding to exclude them. For large libraries, prefer "
                    "running analyze-missing separately."
                ),
            },
            {
                "flag": "--recover-limit",
                "meta": "N",
                "description": "Maximum tracks to analyse inline when --recover-missing-analysis is active.",
                "default": "unlimited",
            },
            {
                "flag": "--recover-timeout-sec",
                "meta": "N",
                "description": "Stop inline analysis after N seconds when --recover-missing-analysis is active.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py rekordbox-export --dry-run",
            "python3 pipeline.py rekordbox-export",
            "python3 pipeline.py rekordbox-export --no-m3u",
            "python3 pipeline.py rekordbox-export --force-xml       # NOT recommended with MIK",
            "python3 pipeline.py rekordbox-export --recover-missing-analysis",
            (
                "python3 pipeline.py rekordbox-export \\\n"
                "    --recover-missing-analysis \\\n"
                "    --recover-limit 50 \\\n"
                "    --recover-timeout-sec 300"
            ),
        ],
        "notes": (
            "MIK-FIRST POLICY:\n"
            "  Rekordbox XML is owned by Rekordbox + Mixed In Key.\n"
            "  XML export is DISABLED by default to prevent data loss.\n"
            "  Use --force-xml only if you are not using Mixed In Key.\n\n"
            "Path mapping (defaults):\n"
            "  Linux root  : /mnt/music_ssd   (= root of M: drive on Windows)\n"
            "  Windows     : M:\\\n\n"
            "Environment overrides:\n"
            "  RB_LINUX_ROOT   Linux path that is the root of the Windows drive\n"
            "  RB_WIN_DRIVE    Windows drive letter (e.g. M)"
        ),
    },

    # -----------------------------------------------------------------------
    # CUES AND SETS
    # -----------------------------------------------------------------------
    {
        "name": "cue-suggest",
        "category": "CUES AND SETS",
        "description": "Auto-detect cue points (intro / drop / outro) and store in the DB.",
        "usage": "python3 pipeline.py cue-suggest [FLAGS]",
        "flags": [
            {"flag": "--dry-run", "description": "Analyse and print cue points. No DB writes."},
            {
                "flag": "--min-confidence",
                "meta": "FLOAT",
                "description": "Minimum confidence score to store a cue point.",
                "default": "0.4",
            },
            {"flag": "--limit", "meta": "N", "description": "Stop after analysing this many tracks."},
            {
                "flag": "--track",
                "meta": "NAME",
                "description": "Only analyse tracks whose artist, title, or filename contains NAME (case-insensitive).",
            },
            {
                "flag": "--export-format",
                "meta": "FMT",
                "description": "Comma-separated output formats: json, csv.",
                "default": "both",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Analyse audio files in this directory instead of the library DB.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py cue-suggest --dry-run",
            "python3 pipeline.py cue-suggest",
            "python3 pipeline.py cue-suggest --limit 20 --track 'Black Coffee'",
            "python3 pipeline.py cue-suggest --export-format json",
            "python3 pipeline.py cue-suggest --path /music/inbox/",
        ],
        "notes": (
            "NOTE: These are SUGGESTED positions only. Native Rekordbox hot-cues are NOT written.\n"
            "Review all cues in Rekordbox after analysis.\n\n"
            "MIK-FIRST POLICY:\n"
            "  Cue data is owned by Mixed In Key / Rekordbox.\n"
            "  This subcommand is safe to use explicitly, but cue-suggest is\n"
            "  DISABLED inside the main pipeline by default.\n"
            "  Use `python3 pipeline.py --force-cue-suggest` to enable it there.\n\n"
            "Cue types detected:\n"
            "  intro_start   bar 1 (always present, confidence 1.0)\n"
            "  mix_in        first stable DJ entry point\n"
            "  groove_start  first full-arrangement section\n"
            "  drop          main energy arrival / impact\n"
            "  breakdown     energy/density reduction after peak\n"
            "  outro_start   beginning of mix-out section\n\n"
            "Output files:\n"
            "  logs/cue_suggest/cue_suggestions.json     (master, all tracks)\n"
            "  logs/cue_suggest/cue_suggestions.csv      (wide format, 1 row/track)\n"
            "  logs/cue_suggest/runs/cues_TIMESTAMP.csv  (per-run detail log)"
        ),
    },
    {
        "name": "set-builder",
        "category": "CUES AND SETS",
        "description": "Build an energy-curve DJ set from the library database and export as M3U + CSV.",
        "usage": "python3 pipeline.py set-builder [FLAGS]",
        "flags": [
            {"flag": "--dry-run", "description": "Preview the set. Write no files."},
            {
                "flag": "--vibe",
                "meta": "VIBE",
                "description": "Phase-weight preset: warm, peak, deep, driving.",
                "default": "peak",
            },
            {
                "flag": "--duration",
                "meta": "MINS",
                "description": "Target set duration in minutes.",
                "default": "60",
            },
            {
                "flag": "--genre",
                "meta": "GENRE",
                "description": "Restrict track selection to this genre (substring match).",
            },
            {
                "flag": "--strategy",
                "meta": "STRATEGY",
                "description": "Harmonic transition ranking strategy: safest, energy_lift, smooth_blend, best_warmup, best_late_set.",
                "default": "safest",
            },
            {
                "flag": "--structure",
                "meta": "STRUCTURE",
                "description": "Phase structure: full, simple, peak_only.",
                "default": "full",
            },
            {
                "flag": "--max-bpm-jump",
                "meta": "BPM",
                "description": "Maximum allowed absolute BPM difference between consecutive tracks. Set to 0 to disable.",
                "default": "3",
            },
            {
                "flag": "--no-strict-harmonic",
                "description": "Disable strict Camelot key validation — falls back to scoring only.",
            },
            {
                "flag": "--artist-repeat-window",
                "meta": "N",
                "description": "Hard-reject any candidate whose primary artist appeared within the last N tracks.",
                "default": "3",
            },
            {
                "flag": "--start-energy",
                "meta": "TIER",
                "description": "Preferred energy tier for the first track: Chill, Mid, Peak.",
            },
            {
                "flag": "--end-energy",
                "meta": "TIER",
                "description": "Preferred energy tier for the last track: Chill, Mid, Peak.",
            },
            {
                "flag": "--name",
                "meta": "NAME",
                "description": "Base name for output files (no extension).",
                "default": "auto-generated timestamp",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py set-builder --dry-run",
            "python3 pipeline.py set-builder --vibe peak --duration 90",
            "python3 pipeline.py set-builder --vibe deep --genre 'afro house'",
            "python3 pipeline.py set-builder --strategy energy_lift --name friday_night",
            (
                "python3 pipeline.py set-builder \\\n"
                "    --vibe peak --duration 120 --genre 'amapiano' \\\n"
                "    --start-energy Mid --end-energy Peak --name amapiano_set"
            ),
        ],
        "notes": (
            "Phases (always in this order):\n"
            "  warmup   gentle intro, Chill/Mid energy\n"
            "  build    rising energy\n"
            "  peak     high-energy section\n"
            "  release  brief energy drop after peak\n"
            "  outro    wind-down / closing\n\n"
            "Output files:\n"
            "  SET_BUILDER_OUTPUT_DIR/<name>.m3u8   playable playlist\n"
            "  SET_BUILDER_OUTPUT_DIR/<name>.csv    full metadata + transition notes\n\n"
            "Default output location: /mnt/music_ssd/KKDJ/_SETS/"
        ),
    },
    {
        "name": "harmonic-suggest",
        "category": "CUES AND SETS",
        "description": "Suggest the best next tracks using harmonic + BPM + energy scoring.",
        "usage": "python3 pipeline.py harmonic-suggest [--track PATH | --key KEY --bpm BPM] [FLAGS]",
        "flags": [
            {
                "flag": "--track",
                "meta": "PATH",
                "description": "Path to a track already in the library DB to suggest from. Mutually exclusive with --key/--bpm.",
            },
            {
                "flag": "--key",
                "meta": "KEY",
                "description": "Camelot key of the current track (e.g. 8A, 5B). Used together with --bpm.",
            },
            {
                "flag": "--bpm",
                "meta": "BPM",
                "description": "BPM of the current track. Used together with --key.",
            },
            {
                "flag": "--strategy",
                "meta": "STRATEGY",
                "description": "Ranking strategy: safest, energy_lift, smooth_blend, best_warmup, best_late_set.",
                "default": "safest",
            },
            {
                "flag": "--top-n",
                "meta": "N",
                "description": "Number of suggestions to return.",
                "default": "10",
            },
            {
                "flag": "--energy",
                "meta": "TIER",
                "description": "Treat the current track as this energy tier: Chill, Mid, Peak (used with --key/--bpm).",
            },
            {
                "flag": "--genre",
                "meta": "GENRE",
                "description": "Genre of the current track (used with --key/--bpm for genre scoring).",
            },
            {
                "flag": "--json",
                "description": "Write suggestions to a JSON file in HARMONIC_SUGGEST_OUTPUT_DIR.",
            },
            {"flag": "--dry-run", "description": "Print suggestions only — do not write JSON output."},
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            'python3 pipeline.py harmonic-suggest --track "/music/sorted/Artist/track.mp3"',
            "python3 pipeline.py harmonic-suggest --key 8A --bpm 128",
            "python3 pipeline.py harmonic-suggest --key 5B --bpm 124 --top-n 20 --json",
            (
                "python3 pipeline.py harmonic-suggest \\\n"
                '    --track "/music/sorted/Artist/track.mp3" \\\n'
                "    --strategy energy_lift"
            ),
        ],
        "notes": (
            "Scoring factors:\n"
            "  Camelot compatibility  35%   Camelot wheel distance\n"
            "  BPM compatibility      30%   tempo delta, halftime/doubletime aware\n"
            "  Energy compatibility   20%   Peak / Mid / Chill tier match\n"
            "  Genre compatibility    15%   exact / related / different\n\n"
            "Input modes (choose one):\n"
            "  --track PATH           suggest from a specific file in the library DB\n"
            "  --key KEY --bpm BPM    suggest from a virtual track (key + BPM only)"
        ),
    },

    # -----------------------------------------------------------------------
    # LABEL INTELLIGENCE
    # -----------------------------------------------------------------------
    {
        "name": "label-intel",
        "category": "LABEL INTELLIGENCE",
        "description": "Scrape label metadata from Beatport / Traxsource and export to JSON/CSV/TXT/SQLite.",
        "usage": "python3 pipeline.py label-intel [FLAGS]",
        "flags": [
            {
                "flag": "--label-seeds",
                "meta": "FILE",
                "description": "Seeds file with one label name per line.",
                "default": "$DJ_MUSIC_ROOT/data/labels/seeds.txt",
            },
            {
                "flag": "--label-output",
                "meta": "DIR",
                "description": "Output directory for exported files.",
                "default": "$DJ_MUSIC_ROOT/data/labels/output/",
            },
            {
                "flag": "--label-cache",
                "meta": "DIR",
                "description": "HTTP cache directory.",
                "default": "$DJ_MUSIC_ROOT/.cache/label_intel/",
            },
            {
                "flag": "--label-sources",
                "meta": "SOURCE [SOURCE ...]",
                "description": "Sources to scrape: beatport, traxsource.",
                "default": "beatport traxsource",
            },
            {
                "flag": "--label-delay",
                "meta": "SECS",
                "description": "Per-host request delay in seconds.",
                "default": "2.0",
            },
            {
                "flag": "--label-skip-enrich",
                "description": "Skip label page enrichment (faster; search results only).",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py label-intel",
            "python3 pipeline.py label-intel --label-sources beatport",
            (
                "python3 pipeline.py label-intel \\\n"
                "    --label-seeds /music/data/labels/seeds.txt \\\n"
                "    --label-delay 3.0"
            ),
        ],
        "notes": (
            "Output files (under --label-output):\n"
            "  labels.json    full metadata\n"
            "  labels.csv     spreadsheet-friendly\n"
            "  labels.txt     one name per line (copy to known_labels.txt for parser blocklist)\n"
            "  labels.db      SQLite for ad-hoc queries"
        ),
    },
    {
        "name": "label-clean",
        "category": "LABEL INTELLIGENCE",
        "description": "Detect, normalize, and optionally write back label metadata (Phase 1: local).",
        "usage": "python3 pipeline.py label-clean [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Scan and report only — make no file changes (default behavior).",
            },
            {
                "flag": "--write-tags",
                "description": "Write high-confidence labels (>= threshold) back to the organization/TPUB tag.",
            },
            {
                "flag": "--review-only",
                "description": "Only export the review file (unresolved / low-confidence tracks).",
            },
            {
                "flag": "--confidence-threshold",
                "meta": "FLOAT",
                "description": "Minimum confidence for write-back.",
                "default": "0.85",
            },
            {
                "flag": "--use-discogs",
                "description": "[Phase 2 — not yet implemented] Match via Discogs API.",
            },
            {
                "flag": "--use-beatport",
                "description": "[Phase 2 — not yet implemented] Match via Beatport.",
            },
            {
                "flag": "--path",
                "meta": "DIR",
                "description": "Scan audio files in this directory instead of pulling from the database.",
            },
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
        ],
        "examples": [
            "python3 pipeline.py label-clean",
            "python3 pipeline.py label-clean --write-tags",
            "python3 pipeline.py label-clean --review-only",
            "python3 pipeline.py label-clean --write-tags --confidence-threshold 0.75",
            "python3 pipeline.py label-clean --path /mnt/music_ssd/KKDJ/",
        ],
        "notes": (
            "Detection order (confidence shown):\n"
            "  1. organization/TPUB embedded tag     0.95\n"
            "  2. grouping tag fallback              0.75\n"
            "  3. comment tag fallback               0.60\n"
            "  4. filename pattern parsing           0.55-0.70\n"
            "  5. unresolved                         0.00\n\n"
            "Write-back only applies when confidence >= threshold (default 0.85).\n"
            "At the default threshold, only embedded-tag results are auto-written."
        ),
    },

    # -----------------------------------------------------------------------
    # METADATA INTELLIGENCE
    # -----------------------------------------------------------------------
    {
        "name": "metadata-sanitize",
        "category": "METADATA INTELLIGENCE",
        "description": (
            "Deterministic offline cleaning of all metadata fields. Removes URL "
            "watermarks, promo artifacts, DJ pool tags, malformed ISRCs, and "
            "BPM/key comment noise."
        ),
        "usage": "python3 pipeline.py metadata-sanitize --input DIR [FLAGS]",
        "flags": [
            {"flag": "--input", "meta": "DIR", "description": "Directory of audio files to scan (recursive). Required."},
            {"flag": "--limit", "meta": "N", "description": "Maximum number of files to process in this run.", "default": "no limit"},
            {"flag": "--apply", "description": "Write changes to audio file tags. Without this flag, changes are only previewed."},
            {"flag": "--output-json", "meta": "FILE", "description": "Save the full change log to this JSON file."},
            {"flag": "--verbose / -v", "description": "Enable debug logging and show unmodified corrupt files."},
            {"flag": "--log-dir", "meta": "DIR", "description": "Directory for run logs.", "default": "logs/metadata-sanitize/"},
            {"flag": "--force", "description": "Reprocess all files, ignoring processed-state tracking."},
            {"flag": "--reset-stage", "description": "Clear processed-state tracking for this stage before running."},
        ],
        "examples": [
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox",
            "python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply",
        ],
        "notes": (
            "Fully deterministic — no AI, no network calls.\n"
            "Idempotent — re-running a clean file produces no further changes.\n"
            "Workflow position: run before ai-normalize / artist-intelligence / "
            "metadata-enrich-online."
        ),
    },
    {
        "name": "artist-intelligence",
        "category": "METADATA INTELLIGENCE",
        "description": (
            "Deterministic artist normalization, alias resolution, and identity "
            "consistency across the library."
        ),
        "usage": "python3 pipeline.py artist-intelligence --input DIR [FLAGS]",
        "flags": [
            {"flag": "--input", "meta": "DIR", "description": "Directory of audio files to process (scanned recursively). Required."},
            {"flag": "--limit", "meta": "N", "description": "Maximum number of files to process in this run.", "default": "no limit"},
            {"flag": "--dry-run", "description": "Parse and show diffs — write no files."},
            {"flag": "--apply", "description": "Write high-confidence changes to audio file tags. Cannot be combined with --dry-run."},
            {"flag": "--min-confidence", "meta": "FLOAT", "description": "Minimum confidence (0.0-1.0) required to apply a change.", "default": "0.90"},
            {"flag": "--output-json", "meta": "FILE", "description": "Save the full diff preview to this JSON file."},
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
            {"flag": "--log-dir", "meta": "DIR", "description": "Directory for run logs.", "default": "logs/artist-intelligence/"},
            {"flag": "--force", "description": "Reprocess all files, ignoring processed-state tracking."},
            {"flag": "--reset-stage", "description": "Clear processed-state tracking for this stage before running."},
        ],
        "examples": [
            "python3 pipeline.py artist-intelligence --input ~/Music/inbox",
            "python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply",
        ],
        "notes": (
            "Alias store  : data/intelligence/artist_aliases.json\n"
            "Review queue : data/intelligence/artist_review_queue.json\n"
            "Never rewrites the title field or moves '(feat ...)' from title into artist."
        ),
    },
    {
        "name": "ai-normalize",
        "category": "METADATA INTELLIGENCE",
        "description": (
            "Local AI (Ollama) metadata proposals for artist, title, version, "
            "label, remixers, and featured artists. Preview by default; --apply "
            "to write. BPM, key, and cues are never touched."
        ),
        "usage": "python3 pipeline.py ai-normalize --input DIR [FLAGS]",
        "flags": [
            {"flag": "--input", "meta": "DIR", "description": "Directory of audio files to normalize (scanned recursively). Required."},
            {"flag": "--model", "meta": "MODEL", "description": "Ollama model name to use.", "default": "OLLAMA_DEFAULT_MODEL env"},
            {"flag": "--ollama-url", "meta": "URL", "description": "Ollama server base URL.", "default": "OLLAMA_BASE_URL env"},
            {"flag": "--timeout", "meta": "SECS", "description": "Per-request timeout in seconds.", "default": "OLLAMA_TIMEOUT env"},
            {"flag": "--limit", "meta": "N", "description": "Maximum number of files to process in this run.", "default": "50"},
            {"flag": "--dry-run", "description": "Run AI inference and show diffs — write no files."},
            {"flag": "--apply", "description": "Write high-confidence changes to audio file tags. Cannot be combined with --dry-run."},
            {"flag": "--min-confidence", "meta": "FLOAT", "description": "Minimum model confidence (0.0-1.0) required to apply a change.", "default": "0.80"},
            {"flag": "--output-json", "meta": "FILE", "description": "Save the full diff preview to this JSON file."},
            {"flag": "--pre-sanitize", "description": "Run metadata-sanitize before AI normalization (recommended)."},
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
            {"flag": "--log-dir", "meta": "DIR", "description": "Directory for run logs.", "default": "logs/ai-normalize/"},
            {"flag": "--force", "description": "Reprocess all files, ignoring processed-state tracking."},
            {"flag": "--reset-stage", "description": "Clear processed-state tracking for this stage before running."},
        ],
        "examples": [
            "python3 pipeline.py ai-normalize --input ~/Music/inbox",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --apply",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply",
            "python3 pipeline.py ai-normalize --input ~/Music/inbox --min-confidence 0.85 --apply",
        ],
        "notes": (
            "Requirements: Ollama must be running locally (ollama serve) and the "
            "model must be pulled.\n"
            "Only writes artist, title (+ version), label. Never touches BPM, key, "
            "cue points, or genre."
        ),
    },
    {
        "name": "metadata-enrich-online",
        "category": "METADATA INTELLIGENCE",
        "description": (
            "Fill missing album, label, and ISRC via Spotify + Deezer matching "
            "with confidence scoring. Preview by default; --apply to write. "
            "Artist field is never proposed."
        ),
        "usage": "python3 pipeline.py metadata-enrich-online --input DIR [FLAGS]",
        "flags": [
            {"flag": "--input", "meta": "DIR", "description": "Directory of audio files to enrich (scanned recursively). Required."},
            {"flag": "--dry-run", "description": "Run API lookups and show diffs — write no files."},
            {"flag": "--apply", "description": "Write high-confidence changes to audio file tags. Cannot be combined with --dry-run."},
            {"flag": "--limit", "meta": "N", "description": "Maximum number of files to process in this run.", "default": "50"},
            {"flag": "--min-confidence", "meta": "FLOAT", "description": "Minimum confidence (0.0-1.0) required to apply a change.", "default": "0.80"},
            {"flag": "--spotify-client-id", "meta": "ID", "description": "Spotify API client ID (overrides SPOTIFY_CLIENT_ID env var)."},
            {"flag": "--spotify-client-secret", "meta": "SECRET", "description": "Spotify API client secret (overrides SPOTIFY_CLIENT_SECRET env var)."},
            {"flag": "--output-json", "meta": "FILE", "description": "Save full results to this JSON file for offline review."},
            {"flag": "--enable-traxsource", "description": "Enable Traxsource as a dance-music specialist fallback source. Disabled by default."},
            {"flag": "--clean-junk-only", "description": "Run only the junk metadata cleaner — no API calls."},
            {"flag": "--move-ignored", "description": "Move low-confidence files to the IGNORED quarantine directory. review and matched files are never moved."},
            {"flag": "--verbose / -v", "description": "Enable debug logging."},
            {"flag": "--log-dir", "meta": "DIR", "description": "Directory for run logs.", "default": "logs/metadata-enrich-online/"},
            {"flag": "--force", "description": "Reprocess all files, ignoring processed-state tracking."},
            {"flag": "--reset-stage", "description": "Clear processed-state tracking for this stage before running."},
        ],
        "examples": [
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored",
            "python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --min-confidence 0.85",
        ],
        "notes": (
            "Sources: Spotify Web API (ISRC lookup, then artist+title search), "
            "then Deezer as fallback.\n"
            "Requires SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET for Spotify lookups; "
            "Deezer needs no credentials."
        ),
    },
    {
        "name": "review-queue",
        "category": "METADATA INTELLIGENCE",
        "description": (
            "Review and resolve medium-confidence enrichment results "
            "interactively. Reads entries populated by metadata-enrich-online."
        ),
        "usage": "python3 pipeline.py review-queue [FLAGS]",
        "flags": [
            {"flag": "--list-only", "description": "Print all queued items and exit — do not prompt for actions."},
            {"flag": "--apply", "description": "Enable interactive queue changes. Without this flag, list-only dry-run mode is used."},
            {"flag": "--yes", "description": "Confirm queue/tag writes when used with --apply."},
        ],
        "examples": [
            "python3 pipeline.py review-queue",
            "python3 pipeline.py review-queue --list-only",
        ],
        "notes": (
            "Queue file: data/intelligence/enrichment_review_queue.json\n"
            "Actions (interactive mode): a=apply  s=skip  d=delete  n=next  q=quit"
        ),
    },
    {
        "name": "filename-normalize",
        "category": "METADATA INTELLIGENCE",
        "description": (
            "Rename audio files to {artist} - {title} ({version}).ext using "
            "embedded tags. Preview by default; --apply to commit."
        ),
        "usage": "python3 pipeline.py filename-normalize --input DIR [FLAGS]",
        "flags": [
            {"flag": "--input", "meta": "DIR", "description": "Directory of audio files to process. Required."},
            {"flag": "--apply", "description": "Commit renames. Without this flag, preview only."},
            {"flag": "--verbose / -v", "description": "Show skipped and no-change files; enable debug logging."},
            {"flag": "--limit", "meta": "N", "description": "Process at most N files."},
            {"flag": "--force", "description": "Reprocess all files, ignoring processed-state tracking."},
            {"flag": "--reset-stage", "description": "Clear processed-state tracking for this stage before running."},
            {"flag": "--move-artist-review", "description": "Move unsafe-artist files to .BIN/ARTIST_REVIEW/ (requires --apply)."},
        ],
        "examples": [
            "python3 pipeline.py filename-normalize --input ~/Music/inbox",
            "python3 pipeline.py filename-normalize --input ~/Music/inbox --apply",
        ],
        "notes": (
            "No overwrite — collisions get a safe suffix: ' (1)', ' (2)', ...\n"
            "Tags are never modified. BPM, key, and cues are untouched.\n"
            "Skipped if artist or title tag is missing."
        ),
    },

    # -----------------------------------------------------------------------
    # DOCS / META
    # -----------------------------------------------------------------------
    {
        "name": "generate-docs",
        "category": "DOCS",
        "description": "Regenerate COMMANDS.txt, README.md command sections, and COMMANDS.html from the command registry.",
        "usage": "python3 pipeline.py generate-docs [FLAGS]",
        "flags": [
            {
                "flag": "--dry-run",
                "description": "Preview generated output to stdout — write no files.",
            },
            {
                "flag": "--output-dir",
                "meta": "DIR",
                "description": "Write generated files here instead of the project root.",
            },
            {
                "flag": "--format",
                "meta": "FORMATS",
                "description": "Comma-separated list of formats to generate: txt, md, html.",
                "default": "txt,md,html",
            },
        ],
        "examples": [
            "python3 pipeline.py generate-docs",
            "python3 pipeline.py generate-docs --dry-run",
            "python3 pipeline.py generate-docs --format txt,html",
            "python3 pipeline.py generate-docs --output-dir /tmp/docs",
        ],
    },
    {
        "name": "validate-docs",
        "category": "DOCS",
        "description": "Check that COMMANDS.txt is in sync with the command registry — reports missing or stale entries.",
        "usage": "python3 pipeline.py validate-docs [FLAGS]",
        "flags": [
            {
                "flag": "--strict",
                "description": "Exit with code 1 if any mismatches are found (useful in CI/pre-commit).",
            },
        ],
        "examples": [
            "python3 pipeline.py validate-docs",
            "python3 pipeline.py validate-docs --strict",
        ],
    },
]

# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def all_command_names() -> list:
    """Return all command names (excluding MAIN, generate-docs, validate-docs)."""
    return [e["name"] for e in REGISTRY if e["name"] not in ("MAIN",)]


def get_command(name: str) -> dict | None:
    """Look up a registry entry by command name."""
    for entry in REGISTRY:
        if entry["name"] == name:
            return entry
    return None


def commands_by_category() -> dict:
    """Return {category: [entry, ...]} preserving insertion order."""
    result: dict = {}
    for entry in REGISTRY:
        cat = entry.get("category", "OTHER")
        result.setdefault(cat, []).append(entry)
    return result
