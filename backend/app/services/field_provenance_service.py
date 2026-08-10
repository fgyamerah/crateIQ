"""Durable, additive field-level metadata provenance for the selected library.

Metadata model Phase 2. This module owns its own `field_provenance` table in
the selected library's local index (the same `processed.db` as
`quality_review_findings`/`bpm_retry_pauses`), never in the trusted legacy
pipeline schema owned by `db.py`. Schema creation is additive/idempotent and
happens only on a write path -- read paths never create the table, matching
`quality_findings_service`'s "table absence means no records yet" contract.

Answers, per (track_id, field_name):
  - the current value and where it came from (`origin`/`source`);
  - a provider confidence verdict, when one applies;
  - a bounded reason/evidence reference;
  - when it was observed/applied;
  - whether the row is the current value or superseded history.

Contract:
  - `origin` is one of 'provider' (a metadata provider / consensus verdict),
    'user' (an explicit human edit), or 'system' (deterministic app logic,
    including an honest `existing_local_value` backfill baseline).
  - `confidence` (HIGH/MEDIUM/LOW/CONFLICT) may only be set for
    origin='provider'. This is the one hard rule that keeps a human/manual
    edit from ever masquerading as provider HIGH confidence -- `record()`
    raises if a caller tries to pair a non-provider origin with a
    confidence value.
  - Exactly one row per (track_id, field_name) is ever `is_current = 1`
    (enforced by a partial unique index, not just application logic).
    Recording an identical repeat event (same value/origin/source/
    confidence) only refreshes the existing current row's `updated_at`,
    `reason`, and `evidence_json` -- it never inserts a duplicate row, so a
    Process All rerun over an already-applied track cannot explode rows.
    Recording a genuinely different value/origin/source/confidence closes
    the previous current row into immutable history (`is_current = 0`) and
    inserts a new current row.

Local-index only; never touches media files or tags. Evidence/reason text is
length-bounded before storage so this can never become a home for unbounded
provider payloads or secrets.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_TABLE = "field_provenance"
_ORIGINS = {"provider", "user", "system"}
_CONFIDENCES = {"HIGH", "MEDIUM", "LOW", "CONFLICT"}
_VALUE_MAX_LEN = 1000
_REASON_MAX_LEN = 500
_EVIDENCE_VALUE_MAX_LEN = 500
_EVIDENCE_LIST_MAX_ITEMS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> Path:
    root = selected_library_root()
    path = assert_path_under_root(library_db_path(root), root)
    if not path.is_file():
        raise ValueError("Configured library is not initialized.")
    return path


@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        conn.row_factory = sqlite3.Row
        yield conn
        return
    with sqlite3.connect(_db_path()) as owned:
        owned.row_factory = sqlite3.Row
        yield owned


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id      INTEGER NOT NULL,
            field_name    TEXT    NOT NULL,
            value         TEXT,
            origin        TEXT    NOT NULL CHECK (origin IN ('provider', 'user', 'system')),
            source        TEXT    NOT NULL,
            confidence    TEXT    CHECK (confidence IS NULL OR confidence IN ('HIGH', 'MEDIUM', 'LOW', 'CONFLICT')),
            reason        TEXT,
            evidence_json TEXT,
            is_current    INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
            recorded_at   TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_track ON {_TABLE}(track_id, field_name)")
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{_TABLE}_current "
        f"ON {_TABLE}(track_id, field_name) WHERE is_current = 1"
    )


def _bound(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return value[:max_len]


def _bound_evidence(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bound string values before serialization -- never slice serialized JSON.

    Slicing an already-serialized JSON string can cut it mid-object and store
    invalid JSON; bounding each string value first guarantees the eventual
    `json.dumps()` output is always valid, whatever the input size.
    """
    if not evidence:
        return None
    bounded: dict[str, Any] = {}
    for key, value in evidence.items():
        if isinstance(value, str):
            bounded[key] = value[:_EVIDENCE_VALUE_MAX_LEN]
        elif isinstance(value, list):
            bounded[key] = [str(item)[:_EVIDENCE_VALUE_MAX_LEN] for item in value[:_EVIDENCE_LIST_MAX_ITEMS]]
        else:
            bounded[key] = value
    return bounded


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    try:
        evidence = json.loads(row["evidence_json"]) if row["evidence_json"] else None
    except (TypeError, json.JSONDecodeError):
        evidence = None
    return {
        "id": row["id"],
        "track_id": row["track_id"],
        "field_name": row["field_name"],
        "value": row["value"],
        "origin": row["origin"],
        "source": row["source"],
        "confidence": row["confidence"],
        "reason": row["reason"],
        "evidence": evidence,
        "is_current": bool(row["is_current"]),
        "recorded_at": row["recorded_at"],
        "updated_at": row["updated_at"],
    }


def record(
    track_id: int,
    field_name: str,
    value: str | None,
    *,
    origin: str,
    source: str,
    confidence: str | None = None,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Idempotently record the current provenance for one (track_id, field_name).

    See module docstring for the full origin/confidence/idempotency contract.
    """
    if origin not in _ORIGINS:
        raise ValueError(f"Unknown provenance origin: {origin!r}")
    if confidence is not None:
        if confidence not in _CONFIDENCES:
            raise ValueError(f"Unknown provenance confidence: {confidence!r}")
        if origin != "provider":
            raise ValueError("Only provider-origin provenance may carry a confidence verdict.")
    if not field_name or not field_name.strip():
        raise ValueError("field_name is required.")
    if not source or not source.strip():
        raise ValueError("source is required.")

    field_name = field_name.strip()
    source = source.strip()
    bounded_value = _bound(value, _VALUE_MAX_LEN)
    bounded_reason = _bound(reason, _REASON_MAX_LEN)
    bounded_evidence = _bound_evidence(evidence)
    evidence_json = json.dumps(bounded_evidence) if bounded_evidence else None
    now = _now()

    with _connection(conn) as active:
        _ensure_table(active)
        existing = active.execute(
            f"SELECT * FROM {_TABLE} WHERE track_id = ? AND field_name = ? AND is_current = 1",
            (track_id, field_name),
        ).fetchone()

        if (
            existing is not None
            and existing["value"] == bounded_value
            and existing["origin"] == origin
            and existing["source"] == source
            and existing["confidence"] == confidence
        ):
            # Identical repeat event (e.g. a Process All rerun over an
            # already-applied track): refresh the current row, never
            # duplicate it.
            active.execute(
                f"UPDATE {_TABLE} SET updated_at = ?, reason = ?, evidence_json = ? WHERE id = ?",
                (now, bounded_reason, evidence_json, existing["id"]),
            )
            row = active.execute(f"SELECT * FROM {_TABLE} WHERE id = ?", (existing["id"],)).fetchone()
            return _row_to_dict(row)

        if existing is not None:
            active.execute(f"UPDATE {_TABLE} SET is_current = 0 WHERE id = ?", (existing["id"],))

        cursor = active.execute(
            f"""
            INSERT INTO {_TABLE}
                (track_id, field_name, value, origin, source, confidence, reason, evidence_json,
                 is_current, recorded_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (track_id, field_name, bounded_value, origin, source, confidence,
             bounded_reason, evidence_json, now, now),
        )
        row = active.execute(f"SELECT * FROM {_TABLE} WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _row_to_dict(row)


def list_for_track(
    track_id: int, *, include_history: bool = True, conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Read-only; never creates the table -- absence means "no provenance recorded yet."."""
    try:
        with _connection(conn) as active:
            tables = {row[0] for row in active.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if _TABLE not in tables:
                return []
            query = f"SELECT * FROM {_TABLE} WHERE track_id = ?"
            params: list[Any] = [track_id]
            if not include_history:
                query += " AND is_current = 1"
            query += " ORDER BY field_name, recorded_at DESC"
            rows = active.execute(query, params).fetchall()
            return [_row_to_dict(row) for row in rows]
    except ValueError:
        return []


def current_for_track(track_id: int, *, conn: sqlite3.Connection | None = None) -> dict[str, dict[str, Any]]:
    """Read-only convenience: {field_name: current provenance row} for one track."""
    return {item["field_name"]: item for item in list_for_track(track_id, include_history=False, conn=conn)}
