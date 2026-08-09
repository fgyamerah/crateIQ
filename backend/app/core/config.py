"""
Backend configuration.

All paths are resolved at import time so they are absolute and stable
regardless of the working directory from which the server is started.
"""
import shutil
import sys
from pathlib import Path

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
# Runtime
# ---------------------------------------------------------------------------

# Use the same Python interpreter that is running the backend so all toolkit
# dependencies (mutagen, librosa, etc.) are available to subprocesses.
PYTHON_BIN = sys.executable

BACKEND_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# SSD sync / Publish
#
# Source is never hardcoded here — it is derived from the active workspace
# at call time by services.sync_destination_service.get_sync_source() (the
# managed workspace's Library/ folder, or the legacy root itself in Legacy
# Direct Library compatibility mode). Destination is a user-configured
# absolute path with no default, persisted and validated by the same
# service. See services.sync_destination_service for the full contract.
# ---------------------------------------------------------------------------

# rsync binary (resolved at import time)
RSYNC_BIN: str = shutil.which("rsync") or "/usr/bin/rsync"
