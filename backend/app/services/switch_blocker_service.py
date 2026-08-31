"""Persisted switch-safety inspection; intentionally performs no handoff."""
from __future__ import annotations

from typing import Any

from ..core.db import get_conn
from ..core.library_key import current_library_key

# table, family, row identity, key column, active statuses. Terminal rows are ignored.
_FAMILIES = (
    ("jobs", "job", "id", "library_key", ("pending", "running")),
    ("analysis_operations", "analysis", "id", "library_key", ("running",)),
    ("publish_operations", "publish", "id", "library_key", ("running",)),
    ("waveform_operations", "waveform_bulk", "id", "library_key", ("running",)),
    ("tag_write_operations", "tag_write", "id", "library_key", ("running",)),
    ("preparation_operations", "preparation", "id", "library_key", ("running",)),
    ("waveform_jobs", "waveform", "id", "library_id", ("queued", "processing")),
    (
        "waveform_track_state",
        "waveform_track_state",
        "track_id",
        "library_id",
        ("queued", "processing"),
    ),
)


def inspect_switch_blockers(library_key: str | None = None) -> dict[str, Any]:
    """Return conservative persisted blockers for a future supervisor call.

    A known-active foreign row is an installation-level inconsistency: only
    one root-bound backend may be active, so it blocks rather than being
    silently ignored. NULL-key active rows are legacy-ambiguous and likewise
    fail closed. No rows are changed by this diagnostic.
    """
    current = library_key or current_library_key()
    blockers: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    with get_conn() as conn:
        for table, family, id_column, key_column, statuses in _FAMILIES:
            marks = ", ".join("?" for _ in statuses)
            rows = conn.execute(
                f"SELECT {id_column} AS id, status, {key_column} AS library_key FROM {table} "
                f"WHERE status IN ({marks})",
                statuses,
            ).fetchall()
            for row in rows:
                detail = {
                    "family": family,
                    "operation_id": str(row["id"]),
                    "status": str(row["status"]),
                    "library_key": row["library_key"],
                }
                if row["library_key"] is None:
                    detail["reason"] = "ambiguous_legacy_active"
                    ambiguous.append(detail)
                elif row["library_key"] == current:
                    detail["reason"] = "active_current_library"
                    blockers.append(detail)
                else:
                    detail["reason"] = "foreign_active_installation_inconsistency"
                    blockers.append(detail)
    return {
        "can_switch": not blockers and not ambiguous,
        "blockers": blockers,
        "ambiguous_legacy_active": ambiguous,
    }


def assert_no_persisted_switch_blockers(library_key: str | None = None) -> None:
    result = inspect_switch_blockers(library_key)
    if not result["can_switch"]:
        raise RuntimeError("Persisted operation state blocks a safe library switch.")
