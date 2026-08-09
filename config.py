"""
Central configuration for the CrateIQ pipeline.
Override any value by creating config_local.py in this directory.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Root paths
#
# LEGACY DIRECT LIBRARY: this is pipeline.py's own root, independent of the
# current backend's workspace-selected library (backend/app/core/library_root
# .selected_library_root, which reads CRATEIQ_LIBRARY_ROOT and fails closed
# with no /music default). When the FastAPI backend launches a pipeline.py
# subcommand as a job subprocess (services/toolkit_runner.py), it does not
# pass --root; the subprocess instead inherits DJ_MUSIC_ROOT from the
# process environment. scripts/crateiq-local-services.sh is the one place
# that sets DJ_MUSIC_ROOT, always mirroring CRATEIQ_LIBRARY_ROOT, so
# backend-launched legacy jobs stay pointed at the selected library rather
# than silently defaulting to /music. Direct CLI invocations of pipeline.py
# outside that launch script still default to /music here if DJ_MUSIC_ROOT
# is unset — that is intentional legacy CLI behavior, not a bug.
# ---------------------------------------------------------------------------
MUSIC_ROOT   = Path(os.environ.get("DJ_MUSIC_ROOT", "/music"))

# Operational/maintenance storage root — intentionally hidden so scanners skip it.
# All non-library folders live here; never place active library content inside.
BIN_DIR      = MUSIC_ROOT / ".BIN"

INBOX        = MUSIC_ROOT / "inbox"
PROCESSING   = BIN_DIR / "PROCESSING"   # pipeline staging area (was MUSIC_ROOT/processing)
LIBRARY      = MUSIC_ROOT / "library"
SORTED       = LIBRARY / "sorted"
UNSORTED     = SORTED / "_unsorted"
COMPILATIONS = SORTED / "_compilations"

# ---------------------------------------------------------------------------
# Special-purpose route directories
# Files matching route patterns are organised here instead of SORTED.
# ---------------------------------------------------------------------------
ACAPELLA     = LIBRARY / "acapella"
INSTRUMENTAL = LIBRARY / "instrumental"
DJ_TOOLS     = LIBRARY / "dj_tools"
EDITS        = LIBRARY / "edits"
BOOTLEGS     = LIBRARY / "bootlegs"
LIVE         = LIBRARY / "live"
UNKNOWN_ROUTE = LIBRARY / "unknown"   # for tracks with too little metadata

DUPLICATES   = BIN_DIR / "DUPLICATES"   # was: MUSIC_ROOT/duplicates
REJECTED     = BIN_DIR / "REJECTED"     # was: MUSIC_ROOT/rejected
# Quarantine folder for corrupt/unreadable audio files.
# Overridable via config_local.py or the --corrupt-dir CLI flag.
CORRUPT_DIR  = BIN_DIR / "CORRUPT"     # was: LIBRARY/_corrupt
PLAYLISTS        = MUSIC_ROOT / "playlists"
M3U_DIR          = PLAYLISTS / "m3u"
GENRE_M3U_DIR    = M3U_DIR / "Genre"    # genre-based M3U playlists
ENERGY_M3U_DIR   = M3U_DIR / "Energy"   # energy-tier M3U playlists (Peak / Mid / Chill)
COMBINED_M3U_DIR = M3U_DIR / "Combined" # genre+energy combined M3U playlists
KEY_M3U_DIR      = M3U_DIR / "Key"      # Camelot key playlists (1A … 12B)
ROUTE_M3U_DIR    = M3U_DIR / "Route"    # route-type playlists (Acapella, Tool, Vocal …)
XML_DIR          = PLAYLISTS / "xml"
LOGS_DIR         = MUSIC_ROOT / "logs"
DB_PATH          = LOGS_DIR / "processed.db"
REPORTS_DIR      = LOGS_DIR / "reports"
ARTIST_MERGE_REPORT_DIR       = LOGS_DIR / "artist_merge"
ARTIST_FOLDER_CLEAN_REPORT_DIR = LOGS_DIR / "artist_folder_clean"
BEETS_LOG        = LOGS_DIR / "beets_import.log"
TEXT_LOG_PATH    = LOGS_DIR / "processing_log.txt"   # human-readable append-only run log
README_PATH      = LOGS_DIR / "README.md"             # auto-generated, overwritten each run

# ---------------------------------------------------------------------------
# Windows transfer — used when generating Rekordbox XML
# Set WINDOWS_DRIVE_LETTER to whatever drive letter you always assign to your
# external SSD/HDD on Windows (fix it in Windows Disk Management).
# ---------------------------------------------------------------------------
WINDOWS_DRIVE_LETTER = os.environ.get("DJ_WIN_DRIVE", "E")
WINDOWS_MUSIC_ROOT   = f"{WINDOWS_DRIVE_LETTER}:\\music"
# Rekordbox XML location attribute format
WINDOWS_BASE_URL     = f"file://localhost/{WINDOWS_DRIVE_LETTER}:/music"

# ---------------------------------------------------------------------------
# Rekordbox export profile  (rekordbox-export subcommand)
#
# Maps your Linux SSD mount point to the Windows drive letter so that all
# generated paths are immediately usable inside Rekordbox on Windows.
#
# Example layout:
#   SSD mounted on Linux : /mnt/music_ssd/
#   Same SSD on Windows  : M:\
#   Your KKDJ folder     : /mnt/music_ssd/KKDJ/  ↔  M:\KKDJ\
#
# Override either value in config_local.py or via env vars:
#   export RB_LINUX_ROOT=/mnt/music_ssd
#   export RB_WIN_DRIVE=M
# ---------------------------------------------------------------------------
# Linux path that corresponds to the root of the Windows drive (M:\)
RB_LINUX_ROOT    = Path(os.environ.get("RB_LINUX_ROOT",  "/mnt/music_ssd"))
# Windows drive letter assigned to the SSD in Windows Disk Management
RB_WINDOWS_DRIVE = os.environ.get("RB_WIN_DRIVE", "M")

# Output directories — written directly to the SSD so they are immediately
# visible on Windows/Rekordbox.  Override via --export-root, config_local.py,
# or the SSD_KKDJ_ROOT constant below.
SSD_KKDJ_ROOT            = Path("/mnt/music_ssd/KKDJ")
REKORDBOX_XML_EXPORT_DIR = SSD_KKDJ_ROOT / "_REKORDBOX_XML_EXPORT"
REKORDBOX_M3U_EXPORT_DIR = SSD_KKDJ_ROOT / "_PLAYLISTS_M3U_EXPORT"

# ---------------------------------------------------------------------------
# Quality thresholds
# ---------------------------------------------------------------------------
MIN_BITRATE_KBPS = 128       # reject files below this bitrate
MIN_DURATION_SEC = 30        # reject files shorter than this
MAX_DURATION_SEC = 7200      # reject files longer than 2 hours (likely mixes/wrong)

# ---------------------------------------------------------------------------
# Audio extensions to process
# ---------------------------------------------------------------------------
AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg", ".opus"}

# ---------------------------------------------------------------------------
# BPM sanity bounds (genre-aware halving/doubling happens in analyzer.py)
# ---------------------------------------------------------------------------
BPM_MIN = 60
BPM_MAX = 200

# ---------------------------------------------------------------------------
# ID3 settings
# ---------------------------------------------------------------------------
ID3_VERSION  = 3      # ID3v2.3 — best Rekordbox compatibility
ARTWORK_SIZE = 500    # px square, JPEG

# ---------------------------------------------------------------------------
# Label Intelligence
# ---------------------------------------------------------------------------
LABEL_INTEL_SEEDS   = MUSIC_ROOT / "data" / "labels" / "seeds.txt"
LABEL_INTEL_OUTPUT  = MUSIC_ROOT / "data" / "labels" / "output"
LABEL_INTEL_CACHE   = MUSIC_ROOT / ".cache" / "label_intel"
LABEL_INTEL_SOURCES = ["beatport", "traxsource"]
LABEL_INTEL_DELAY   = 2.0

# label-clean subcommand
LABEL_CLEAN_OUTPUT    = MUSIC_ROOT / "data" / "labels" / "clean"
LABEL_CLEAN_THRESHOLD = 0.85    # minimum confidence for automatic tag write-back

# metadata-clean subcommand
METADATA_CLEAN_REPORT_DIR = LOGS_DIR / "metadata_clean"

# audit-quality subcommand
AUDIT_QUALITY_REPORT_DIR  = REPORTS_DIR / "audit_quality"

# cue-suggest subcommand
CUE_SUGGEST_OUTPUT_DIR    = LOGS_DIR / "cue_suggest"
CUE_SUGGEST_WRITE_SIDECARS = False    # write .cues.json sidecar next to each audio file
CUE_SUGGEST_MIN_CONFIDENCE = 0.4      # ignore cues below this confidence when writing to DB

# set-builder subcommand
# Sets are saved directly to the SSD DJ library so they're visible on Windows/Rekordbox.
SET_BUILDER_OUTPUT_DIR     = Path("/mnt/music_ssd/KKDJ/_SETS")

# harmonic-suggest subcommand
HARMONIC_SUGGEST_OUTPUT_DIR = LOGS_DIR / "harmonic_suggest"

# Enrichment move-to-ignored destination (was hardcoded in enrichment runner).
IGNORED_DIR = BIN_DIR / "IGNORED"

# dedupe subcommand — where duplicate files are moved (never deleted outright).
# Lives under .BIN so it is excluded from all library scans automatically.
DEDUPE_QUARANTINE_DIR = BIN_DIR / "QUARANTINE"

# ---------------------------------------------------------------------------
# Scan exclusion rules
# ---------------------------------------------------------------------------
# Exact directory-name components that scanners must skip.
# Checked against every part of a candidate path.
MAINTENANCE_SKIP_DIRS: frozenset = frozenset({
    ".BIN",
    "QUARANTINE", "IGNORED", "CORRUPT", "DUPLICATES", "REJECTED",
    "_duplicates", "_corrupt",
    "__pycache__",
})
# Note: scanners also skip any path component that starts with "." (hidden dirs),
# which covers .BIN and any other hidden operational folders implicitly.

# filename-normalize subcommand — extends MAINTENANCE_SKIP_DIRS with output dirs
# that should never have their contents renamed (exports, logs, sets).
FILENAME_NORMALIZE_SKIP_DIRS: frozenset = MAINTENANCE_SKIP_DIRS | frozenset({
    "exports", "logs", "sets",
})

# library-organize subcommand — same exclusions as filename-normalize.
LIBRARY_ORGANIZE_SKIP_DIRS: frozenset = MAINTENANCE_SKIP_DIRS | frozenset({
    "exports", "logs", "sets",
})

# ---------------------------------------------------------------------------
# Beets
# ---------------------------------------------------------------------------
BEETS_CONFIG = Path.home() / ".config" / "beets" / "config.yaml"

# ---------------------------------------------------------------------------
# Tag sanitization
# ---------------------------------------------------------------------------
# Set to False to disable tag sanitization entirely (useful for debugging)
SANITIZE_TAGS = True

# ---------------------------------------------------------------------------
# Playlist generation toggles
# ---------------------------------------------------------------------------
GENERATE_GENRE_PLAYLISTS    = True   # per-genre playlists (Afro House.m3u8, Amapiano.m3u8 …)
GENERATE_ENERGY_PLAYLISTS   = True   # Peak / Mid / Chill energy-tier playlists
GENERATE_COMBINED_PLAYLISTS = True   # Genre+Energy combined playlists (e.g. Peak Afro House)
GENERATE_KEY_PLAYLISTS      = True   # Camelot key playlists (1A.m3u8 … 12B.m3u8)
GENERATE_ROUTE_PLAYLISTS    = True   # Route playlists (Acapella.m3u8, Tool.m3u8, Vocal.m3u8)

# Minimum number of tracks a playlist must contain to be written.
# Raises this above 1 to suppress single-track noise in combined playlists.
PLAYLIST_MIN_TRACKS = 2

# ---------------------------------------------------------------------------
# Pipeline metadata
# ---------------------------------------------------------------------------
PIPELINE_VERSION = "1.4.0"

# ---------------------------------------------------------------------------
# rmlint binary (override if not in PATH)
# ---------------------------------------------------------------------------
RMLINT_BIN     = os.environ.get("RMLINT_BIN", "rmlint")
# Aubio BPM detection — leave empty for auto-detection (recommended).
# analyzer.py will probe shutil.which("aubio") then shutil.which("aubiotrack").
# Set to an explicit path only if your binary is in a non-standard location,
# e.g.  AUBIO_BIN = "/opt/aubio/bin/aubio"
AUBIO_BIN      = os.environ.get("AUBIO_BIN", "")
# Legacy name kept so existing config_local.py overrides still work.
AUBIOBPM_BIN   = os.environ.get("AUBIOBPM_BIN", "aubiobpm")
# Empty overrides intentionally mean "search PATH", matching .env.example.
KEYFINDER_BIN  = os.environ.get("KEYFINDER_BIN", "").strip() or "keyfinder-cli"
FFPROBE_BIN    = os.environ.get("FFPROBE_BIN", "ffprobe")
FFMPEG_BIN     = os.environ.get("FFMPEG_BIN",  "ffmpeg")
BEET_BIN       = os.environ.get("BEET_BIN", "").strip() or "beet"

# ---------------------------------------------------------------------------
# Artist Intelligence (artist-intelligence subcommand)
# Deterministic normalization, alias resolution, and review queue.
# Data lives in the project repo at data/intelligence/ (version-controlled).
# Override paths in config_local.py if needed.
# ---------------------------------------------------------------------------
_INTEL_DIR          = Path(__file__).parent / "data" / "intelligence"
ARTIST_ALIAS_STORE  = _INTEL_DIR / "artist_aliases.json"
ARTIST_REVIEW_QUEUE = _INTEL_DIR / "artist_review_queue.json"

# ---------------------------------------------------------------------------
# AI normalization (ai-normalize subcommand)
# Local Ollama inference — no cloud, no API keys required.
# Override in config_local.py or via env vars if your Ollama runs elsewhere.
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL      = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL",    "qwen2.5:3b")
OLLAMA_TIMEOUT       = int(os.environ.get("OLLAMA_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Review dataset (ai-normalize → training data pipeline)
# JSONL files under data/intelligence/ — one object per line.
# Accumulated across runs; never truncated by the pipeline.
# ---------------------------------------------------------------------------
AI_REVIEW_QUEUE      = _INTEL_DIR / "review_queue.jsonl"
AI_ACCEPTED_EXAMPLES = _INTEL_DIR / "accepted_examples.jsonl"
AI_REJECTED_EXAMPLES = _INTEL_DIR / "rejected_examples.jsonl"
AI_REVIEW_DECISIONS  = _INTEL_DIR / "review_decisions.jsonl"
AI_FEWSHOT_EXAMPLES  = _INTEL_DIR / "fewshot_examples.jsonl"

# ---------------------------------------------------------------------------
# Online metadata enrichment (metadata-enrich-online subcommand)
#
# Spotify: obtain credentials at https://developer.spotify.com/dashboard
#   1. Create an app (name/description can be anything).
#   2. Copy the Client ID and Client Secret.
#   3. Set the env vars below, or override in config_local.py.
#
# Deezer:  no credentials required — public API, no sign-up needed.
#
# Extension point: when traxsource_lookup.py is added, add its config here.
# ---------------------------------------------------------------------------
SPOTIFY_CLIENT_ID     = os.environ.get("SPOTIFY_CLIENT_ID",     "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

# Minimum confidence to auto-apply an online enrichment change.
# Higher than ai-normalize's 0.75 because string matching is noisier than
# local model inference.  ISRC exact matches always score 0.98 and will
# pass this threshold.
ENRICH_ONLINE_MIN_CONFIDENCE = float(
    os.environ.get("ENRICH_ONLINE_MIN_CONFIDENCE", "0.90")
)

# Dataset JSONL files for the enrichment pipeline
AI_ENRICH_QUEUE    = _INTEL_DIR / "enrichment_queue.jsonl"
AI_ENRICH_ACCEPTED = _INTEL_DIR / "enrichment_accepted.jsonl"
AI_ENRICH_REJECTED = _INTEL_DIR / "enrichment_rejected.jsonl"

# Pipeline run logging
PIPELINE_LOGS_DIR = Path(__file__).parent / "logs"

# ---------------------------------------------------------------------------
# Local overrides (git-ignored, create config_local.py to override anything)
# ---------------------------------------------------------------------------
try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass
