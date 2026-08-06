"""
SQLite layer for the backend's own job-tracking database.

This is completely separate from the toolkit's pipeline database
(processed.db).  The jobs table records every pipeline.py invocation
made through the API, its current state, and where its log file lives.
"""
import contextlib
import logging
import sqlite3
from typing import Iterator

from .config import JOBS_DB_PATH

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT    PRIMARY KEY,
    command          TEXT    NOT NULL,
    args_json        TEXT    NOT NULL DEFAULT '[]',
    status           TEXT    NOT NULL DEFAULT 'pending',
    created_at       TEXT    NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    exit_code        INTEGER,
    log_path         TEXT,
    pid              INTEGER,
    progress_current INTEGER,
    progress_total   INTEGER,
    progress_percent REAL,
    progress_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

-- BPM anomaly review state.
-- track_id and filepath reference the pipeline DB (processed.db) — read-only.
-- review_status: pending | reviewed | ignored | requeued | resolved
-- resolved = was anomalous at last check but looks fine now (re-scan promoted it)
CREATE TABLE IF NOT EXISTS bpm_anomalies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id            INTEGER NOT NULL,
    filepath            TEXT    NOT NULL,
    artist              TEXT,
    title               TEXT,
    genre               TEXT,
    current_bpm         REAL,
    suggested_bpm       REAL,
    reason              TEXT    NOT NULL,
    review_status       TEXT    NOT NULL DEFAULT 'pending',
    detected_at         TEXT    NOT NULL,
    reviewed_at         TEXT,
    review_note         TEXT,
    reanalysis_job_id   TEXT,
    UNIQUE(track_id)
);

CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_status ON bpm_anomalies(review_status);
CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_reason ON bpm_anomalies(reason);

-- Disposable waveform linkage/state.  This operational data intentionally
-- lives in jobs.db and never in the trusted pipeline processed.db.
CREATE TABLE IF NOT EXISTS waveform_track_state (
    library_id          TEXT    NOT NULL,
    track_id            INTEGER NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'not_generated'
                                CHECK (status IN (
                                    'not_generated', 'queued', 'processing',
                                    'ready', 'failed', 'unsupported', 'stale',
                                    'cancelled'
                                )),
    schema_version      INTEGER NOT NULL,
    algorithm_version   TEXT    NOT NULL,
    source_size_bytes   INTEGER,
    source_mtime_ns     INTEGER,
    source_ctime_ns     INTEGER,
    source_device       INTEGER,
    source_inode        INTEGER,
    source_sha256       TEXT,
    cache_key           TEXT,
    generated_at        TEXT,
    last_error_code     TEXT,
    updated_at          TEXT    NOT NULL,
    PRIMARY KEY (library_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_waveform_track_status
    ON waveform_track_state(status);
CREATE INDEX IF NOT EXISTS idx_waveform_track_cache_key
    ON waveform_track_state(cache_key);

-- W1 persisted job records only.  W3 adds the explicit generation lifecycle:
-- generation_key is the stat-based signature digest used for active-job
-- deduplication and cache naming.  It is never a source content hash.
CREATE TABLE IF NOT EXISTS waveform_jobs (
    id                  TEXT    PRIMARY KEY,
    library_id          TEXT    NOT NULL,
    track_id            INTEGER NOT NULL,
    status              TEXT    NOT NULL
                                CHECK (status IN (
                                    'queued', 'processing', 'succeeded',
                                    'failed', 'cancelled'
                                )),
    created_at          TEXT    NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    cancel_requested    INTEGER NOT NULL DEFAULT 0
                                CHECK (cancel_requested IN (0, 1)),
    error_code          TEXT,
    generation_key      TEXT
);

CREATE INDEX IF NOT EXISTS idx_waveform_jobs_status
    ON waveform_jobs(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_waveform_one_active_track
    ON waveform_jobs(library_id, track_id)
    WHERE status IN ('queued', 'processing');
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a WAL-mode connection; commit on clean exit, rollback on error."""
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(JOBS_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # WAL + synchronous=NORMAL is SQLite's documented pairing: it cannot
    # corrupt the database and only risks the most recent transactions on a
    # power loss. jobs.db holds disposable operational state (job rows,
    # progress, waveform lifecycle), never trusted library metadata, so the
    # full fsync per commit that synchronous=FULL imposes is not warranted.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def _add_column_safe(
    conn: sqlite3.Connection, table: str, column: str, defn: str
) -> None:
    """
    Add a column to an existing table if it does not already exist.

    SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so we
    catch the OperationalError that fires when the column is already present.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {defn}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            pass  # already migrated — safe to ignore
        else:
            raise


def init_db() -> None:
    """
    Create tables if they don't exist and apply any pending column migrations.
    Safe to call on every startup.
    """
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)

        # Migrate older DBs that were created before progress tracking was added.
        for col, defn in [
            ("pid",              "INTEGER"),
            ("progress_current", "INTEGER"),
            ("progress_total",   "INTEGER"),
            ("progress_percent", "REAL"),
            ("progress_message", "TEXT"),
        ]:
            _add_column_safe(conn, "jobs", col, defn)

        # Migrate W1-era waveform job rows to the W3 generation lifecycle.
        _add_column_safe(conn, "waveform_jobs", "generation_key", "TEXT")

        # W6 LRU support: application-owned access timestamp. Filesystem atime
        # is unreliable (relatime/noatime mounts defer or disable it), so cache
        # eviction ordering uses this column instead.
        _add_column_safe(conn, "waveform_track_state", "last_accessed_at", "TEXT")

    log.info("Backend operational DB ready")
