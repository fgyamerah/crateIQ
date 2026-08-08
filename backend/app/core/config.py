"""
Backend configuration.

All paths are resolved at import time so they are absolute and stable
regardless of the working directory from which the server is started.
"""
import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Dict

# ---------------------------------------------------------------------------
# Project layout
# ---------------------------------------------------------------------------

# backend/app/core/  →  ×4  →  djtoolkit project root
_HERE         = Path(__file__).parent          # backend/app/core
_BACKEND_ROOT = _HERE.parent.parent            # backend/
TOOLKIT_ROOT  = _BACKEND_ROOT.parent.resolve() # djtoolkit/

PIPELINE_PY   = TOOLKIT_ROOT / "pipeline.py"

# ---------------------------------------------------------------------------
# Backend-specific storage (git-ignored; created on first run)
# ---------------------------------------------------------------------------
BACKEND_DATA_DIR = _BACKEND_ROOT / "data"
JOBS_DB_PATH     = BACKEND_DATA_DIR / "jobs.db"
JOBS_LOG_DIR     = BACKEND_DATA_DIR / "logs"

# Byte-for-byte backups of source files taken immediately before a
# confirmed tag write-back (Cycle 7). Deliberately outside any possible
# scanned music library root (settings_service._forbidden_library_roots
# blocks the whole repo tree, this directory included, from ever being
# selected as a library root) and never itself scanned/imported as music.
TAG_WRITE_BACKUP_DIR = BACKEND_DATA_DIR / "tag_write_backups"

# ---------------------------------------------------------------------------
# Pipeline DB — read from toolkit config.py at import time.
# The pipeline writes to processed.db; the backend only ever reads it.
# ---------------------------------------------------------------------------

def _resolve_pipeline_db() -> Path:
    """Load DB_PATH from the toolkit's config.py without importing pipeline.py."""
    fallback = TOOLKIT_ROOT / "logs" / "processed.db"
    try:
        spec = importlib.util.spec_from_file_location(
            "_tk_config_for_db", str(TOOLKIT_ROOT / "config.py")
        )
        if spec is None or spec.loader is None:
            return fallback
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        db_path = getattr(mod, "DB_PATH", None)
        return Path(db_path) if db_path else fallback
    except Exception:
        return fallback


PIPELINE_DB_PATH: Path = _resolve_pipeline_db()


def _resolve_music_root() -> Path:
    """Read MUSIC_ROOT from the toolkit's config.py and return its resolved canonical path."""
    fallback = Path("/music").resolve()
    try:
        spec = importlib.util.spec_from_file_location(
            "_tk_config_for_music_root", str(TOOLKIT_ROOT / "config.py")
        )
        if spec is None or spec.loader is None:
            return fallback
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        val = getattr(mod, "MUSIC_ROOT", None)
        return Path(val).resolve() if val else fallback
    except Exception:
        return fallback


MUSIC_ROOT: Path = _resolve_music_root()

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

# Use the same Python interpreter that is running the backend so all toolkit
# dependencies (mutagen, librosa, etc.) are available to subprocesses.
PYTHON_BIN = sys.executable

BACKEND_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# SSD sync configuration
# Source of truth: working library on the local machine.
# Destination:     external SSD used as the Rekordbox deployment target.
#
# These are validated against every sync request — raw paths are never
# accepted from clients.
# ---------------------------------------------------------------------------

# Named sync sources.  "library" is the primary working collection;
# "inbox" is the staging area for new downloads before pipeline processing.
SYNC_SOURCE_LIBRARY: Path = Path("/home/koolkatdj/Music/music/library")
SYNC_SOURCE_INBOX:   Path = Path("/home/koolkatdj/Music/music/inbox")

SYNC_SOURCE_MAP: Dict[str, Path] = {
    "library": SYNC_SOURCE_LIBRARY,
    "inbox":   SYNC_SOURCE_INBOX,
}

# Only valid destination — the external SSD target.
# The SSD is write-only from the pipeline's perspective (never read for
# analysis results or stale-path detection).
SYNC_DEST_SSD: Path = Path("/mnt/music_ssd/KKDJ")

# rsync binary (resolved at import time)
RSYNC_BIN: str = shutil.which("rsync") or "/usr/bin/rsync"
