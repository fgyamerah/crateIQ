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
    progress_message TEXT,
    library_key      TEXT
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
    library_key         TEXT,
    UNIQUE(library_key, track_id)
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

-- Persisted history for explicit, confirmed Analysis Jobs runs (BPM/key
-- analysis today). Candidate *previews* are never persisted here -- only a
-- confirmed run that actually attempted work creates a row. This is
-- app-owned operational history: it never lives in the trusted pipeline
-- processed.db, and it never stores absolute source paths, secrets, or full
-- per-track logs -- only bounded counts/warnings already surfaced by the
-- existing preview/run contracts.
CREATE TABLE IF NOT EXISTS analysis_operations (
    id                TEXT    PRIMARY KEY,
    job_type          TEXT    NOT NULL
                               CHECK (job_type IN ('bpm_analysis', 'key_analysis')),
    mode              TEXT    NOT NULL DEFAULT 'apply',
    status            TEXT    NOT NULL DEFAULT 'running'
                               CHECK (status IN (
                                   'running', 'completed', 'failed', 'cancelled'
                               )),
    scope_limit       INTEGER NOT NULL,
    eligible_total    INTEGER NOT NULL DEFAULT 0,
    considered        INTEGER NOT NULL DEFAULT 0,
    processed         INTEGER NOT NULL DEFAULT 0,
    succeeded         INTEGER NOT NULL DEFAULT 0,
    skipped           INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    recovered         INTEGER NOT NULL DEFAULT 0,
    remaining_missing INTEGER,
    cancel_requested  INTEGER NOT NULL DEFAULT 0
                               CHECK (cancel_requested IN (0, 1)),
    error_reason      TEXT,
    warnings_json     TEXT,
    created_at        TEXT    NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    library_key       TEXT
);

CREATE INDEX IF NOT EXISTS idx_analysis_operations_created
    ON analysis_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_operations_status
    ON analysis_operations(status);

-- Persisted history for explicit, confirmed Guided Publish operations
-- (crate export and SSD sync). A row is created only for a confirmed
-- operation that actually attempted a write -- readiness/preview calls are
-- never persisted here. No absolute source/destination paths are stored,
-- only a root-relative or category destination string, matching the
-- analysis_operations privacy contract above.
CREATE TABLE IF NOT EXISTS publish_operations (
    id                   TEXT    PRIMARY KEY,
    operation_type       TEXT    NOT NULL
                                  CHECK (operation_type IN ('export', 'sync')),
    export_target        TEXT,
    sync_source          TEXT,
    job_id               TEXT,
    mode                 TEXT    NOT NULL DEFAULT 'apply',
    status                TEXT   NOT NULL DEFAULT 'running'
                                  CHECK (status IN (
                                      'running', 'completed', 'failed', 'cancelled'
                                  )),
    crate_id             INTEGER,
    crate_name           TEXT,
    scope                TEXT,
    track_count          INTEGER NOT NULL DEFAULT 0,
    destination_relative TEXT,
    result               TEXT,
    verification_status  TEXT
                                  CHECK (verification_status IS NULL OR verification_status IN (
                                      'verified', 'failed', 'skipped'
                                  )),
    verification_details_json TEXT,
    warnings_json         TEXT,
    error_reason           TEXT,
    created_at            TEXT   NOT NULL,
    started_at            TEXT,
    finished_at            TEXT,
    library_key            TEXT
);

CREATE INDEX IF NOT EXISTS idx_publish_operations_created
    ON publish_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_publish_operations_status
    ON publish_operations(status);
CREATE INDEX IF NOT EXISTS idx_publish_operations_type
    ON publish_operations(operation_type);

-- Persisted history for explicit, confirmed bulk waveform generation runs
-- ("Generate missing waveforms" on the Jobs page). Mirrors the
-- analysis_operations / publish_operations privacy and lifecycle contract:
-- app-owned, never in the trusted pipeline processed.db, and a row is
-- created only for a confirmed run that is genuinely about to begin work --
-- read-only previews are never persisted. No absolute source paths, cache
-- paths, or content hashes are stored, only bounded counts.
CREATE TABLE IF NOT EXISTS waveform_operations (
    id                TEXT    PRIMARY KEY,
    operation_type    TEXT    NOT NULL DEFAULT 'generate_missing'
                               CHECK (operation_type IN ('generate_missing')),
    status            TEXT    NOT NULL DEFAULT 'running'
                               CHECK (status IN (
                                   'running', 'completed', 'failed', 'cancelled'
                               )),
    total_tracks      INTEGER NOT NULL DEFAULT 0,
    eligible_total    INTEGER NOT NULL DEFAULT 0,
    processed         INTEGER NOT NULL DEFAULT 0,
    generated         INTEGER NOT NULL DEFAULT 0,
    skipped           INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    remaining_missing INTEGER,
    cancel_requested  INTEGER NOT NULL DEFAULT 0
                               CHECK (cancel_requested IN (0, 1)),
    error_reason      TEXT,
    created_at        TEXT    NOT NULL,
    started_at        TEXT,
    finished_at       TEXT,
    library_key       TEXT
);

CREATE INDEX IF NOT EXISTS idx_waveform_operations_created
    ON waveform_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_waveform_operations_status
    ON waveform_operations(status);

-- Persisted history for controlled metadata write-back (Cycle 7). A row is
-- created only when an explicit, confirmed apply is about to begin -- a
-- read-only preview is never persisted. Mirrors the analysis_operations /
-- publish_operations / waveform_operations lifecycle contract, extended
-- with 'previewed' (reserved for future use) and 'restored' -- a write-back
-- operation is the one operation type in this app that can itself be
-- undone, so its terminal state needs to reflect that distinctly from
-- 'completed'. No absolute source paths: only root-relative safe paths
-- inside plan_json/backup_manifest_json/result_json.
CREATE TABLE IF NOT EXISTS tag_write_operations (
    id                   TEXT    PRIMARY KEY,
    status               TEXT    NOT NULL DEFAULT 'running'
                                  CHECK (status IN (
                                      'previewed', 'running', 'completed',
                                      'failed', 'partially_failed', 'restored'
                                  )),
    track_count          INTEGER NOT NULL DEFAULT 0,
    applied_count        INTEGER NOT NULL DEFAULT 0,
    skipped_count        INTEGER NOT NULL DEFAULT 0,
    failed_count         INTEGER NOT NULL DEFAULT 0,
    plan_json            TEXT,
    backup_manifest_json TEXT,
    result_json          TEXT,
    warnings_json         TEXT,
    error_reason          TEXT,
    created_at            TEXT   NOT NULL,
    started_at            TEXT,
    finished_at            TEXT,
    restored_at            TEXT,
    library_key            TEXT
);

CREATE INDEX IF NOT EXISTS idx_tag_write_operations_created
    ON tag_write_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tag_write_operations_status
    ON tag_write_operations(status);

-- Persisted history for batch Inbox preparation ("Process All" and
-- Clean/Enrich/Analyze Selected, Cycle 10). Mirrors the analysis_operations
-- / tag_write_operations lifecycle contract: a row is created only for an
-- explicitly confirmed run that is genuinely about to begin work; read-only
-- preflight previews are never persisted. 'running' rows left behind by a
-- killed backend are closed out at startup, same as every other operation
-- table here -- a restart never silently resumes batch processing. Stage
-- counts are truthful subsets of track_count, never a fabricated percent.
CREATE TABLE IF NOT EXISTS preparation_operations (
    id                 TEXT    PRIMARY KEY,
    operation_type     TEXT    NOT NULL DEFAULT 'process_all'
                                CHECK (operation_type IN (
                                    'process_all', 'clean_selected',
                                    'enrich_selected'
                                )),
    status             TEXT    NOT NULL DEFAULT 'running'
                                CHECK (status IN (
                                    'running', 'completed', 'failed', 'cancelled'
                                )),
    track_count        INTEGER NOT NULL DEFAULT 0,
    cleaned_count      INTEGER NOT NULL DEFAULT 0,
    enriched_count     INTEGER NOT NULL DEFAULT 0,
    written_count      INTEGER NOT NULL DEFAULT 0,
    needs_review_count INTEGER NOT NULL DEFAULT 0,
    ready_count        INTEGER NOT NULL DEFAULT 0,
    failed_count       INTEGER NOT NULL DEFAULT 0,
    cancel_requested   INTEGER NOT NULL DEFAULT 0
                                CHECK (cancel_requested IN (0, 1)),
    warnings_json      TEXT,
    error_reason       TEXT,
    created_at         TEXT    NOT NULL,
    started_at         TEXT,
    finished_at        TEXT,
    library_key        TEXT
);

CREATE INDEX IF NOT EXISTS idx_preparation_operations_created
    ON preparation_operations(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_preparation_operations_status
    ON preparation_operations(status);
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


def _migrate_bpm_anomaly_uniqueness(conn: sqlite3.Connection) -> None:
    """Allow identical pipeline track IDs to exist under distinct libraries.

    SQLite cannot alter a UNIQUE constraint. This conservative copy migration
    preserves every historical NULL-owned row and primary key verbatim.
    """
    indexes = conn.execute("PRAGMA index_list(bpm_anomalies)").fetchall()
    legacy_unique = False
    for index in indexes:
        if not index[2]:
            continue
        columns = [row[2] for row in conn.execute(f"PRAGMA index_info({index[1]})")]
        if columns == ["track_id"]:
            legacy_unique = True
            break
    if not legacy_unique:
        return
    conn.execute("ALTER TABLE bpm_anomalies RENAME TO bpm_anomalies_legacy")
    conn.execute("""
        CREATE TABLE bpm_anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL,
            filepath TEXT NOT NULL, artist TEXT, title TEXT, genre TEXT,
            current_bpm REAL, suggested_bpm REAL, reason TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending', detected_at TEXT NOT NULL,
            reviewed_at TEXT, review_note TEXT, reanalysis_job_id TEXT,
            library_key TEXT, UNIQUE(library_key, track_id)
        )
    """)
    legacy_columns = {row[1] for row in conn.execute("PRAGMA table_info(bpm_anomalies_legacy)")}
    key_expr = "library_key" if "library_key" in legacy_columns else "NULL"
    conn.execute(
        "INSERT INTO bpm_anomalies (id, track_id, filepath, artist, title, genre, current_bpm, "
        "suggested_bpm, reason, review_status, detected_at, reviewed_at, review_note, reanalysis_job_id, library_key) "
        "SELECT id, track_id, filepath, artist, title, genre, current_bpm, suggested_bpm, reason, "
        "review_status, detected_at, reviewed_at, review_note, reanalysis_job_id, " + key_expr + " FROM bpm_anomalies_legacy"
    )
    conn.execute("DROP TABLE bpm_anomalies_legacy")


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

        # Cycle 3 Stage 3: link a publish_operations sync row to its
        # underlying rsync job. Rows created by Stage 2 (export-only) predate
        # this column.
        _add_column_safe(conn, "publish_operations", "job_id", "TEXT")

        # BPM malformed-audio hardening: counts tracks whose BPM was only
        # recoverable via the FFmpeg decode fallback. Rows created before this
        # change default to 0, which is truthful (fallback did not exist yet).
        _add_column_safe(conn, "analysis_operations", "recovered", "INTEGER NOT NULL DEFAULT 0")

        _add_column_safe(conn, "bpm_anomalies", "library_key", "TEXT")
        _migrate_bpm_anomaly_uniqueness(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_status ON bpm_anomalies(review_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_reason ON bpm_anomalies(reason)")

        # 1B.2B-1: NULL preserves unknown ownership of legacy rows. New
        # root-bound writes are always scoped; ordinary reads/recovery never
        # adopt NULL rows.
        scoped_tables = (
            "jobs", "analysis_operations", "publish_operations",
            "waveform_operations", "tag_write_operations", "preparation_operations",
        )
        for table in scoped_tables:
            _add_column_safe(conn, table, "library_key", "TEXT")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_library_key_status "
                f"ON {table}(library_key, status)"
            )
        for table in (
            "jobs", "analysis_operations", "publish_operations", "waveform_operations",
            "tag_write_operations", "preparation_operations",
        ):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_library_key_created "
                f"ON {table}(library_key, created_at DESC)"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_library_key_review "
            "ON bpm_anomalies(library_key, review_status)"
        )
        # These two waveform tables already use `library_id`; its value is
        # now explicitly the canonical library_key compatibility alias.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_waveform_jobs_library_status "
            "ON waveform_jobs(library_id, status)"
        )

    log.info("Backend operational DB ready")
