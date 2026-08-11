"""
Read-only helpers for the backend API.

These helpers only read filesystem artifacts and the pipeline database.
They never mutate the library, the pipeline DB, or the backend job DB.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from collections import Counter
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from ..models.track import Track
from ..core.library_root import (
    enrichment_queue_path,
    enrichment_review_state_path,
    library_audit_dir,
    library_db_path,
    selected_library_root,
)
from ..core.pipeline_db import get_pipeline_conn, storage_zone_predicate

log = logging.getLogger(__name__)
_QUEUE_CACHE: dict[str, Any] = {
    "path": None,
    "mtime_ns": None,
    "size": None,
    "records": [],
}


def get_library_root() -> Path:
    return selected_library_root()


def get_db_path() -> Path:
    root = get_library_root()
    return library_db_path(root)


def db_exists() -> bool:
    return get_db_path().exists()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_path_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve(strict=False))
    except Exception:
        return str(path)


def _track_id_lookup() -> dict[str, int]:
    if not db_exists():
        return {}
    lookup: dict[str, int] = {}
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute("SELECT id, filepath FROM tracks").fetchall()
        for row in rows:
            track_id = int(row["id"])
            raw_path = str(row["filepath"] or "")
            if not raw_path:
                continue
            for key in {raw_path, _normalize_path_key(raw_path)}:
                lookup.setdefault(key, track_id)
        return lookup
    except Exception as exc:
        log.exception("_track_id_lookup failed: %s", exc)
        return {}


def _load_review_state_raw() -> dict[str, Any]:
    path = enrichment_review_state_path()
    if not path.exists():
        return {"updated_at": None, "queue_total": 0, "items": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"updated_at": None, "queue_total": 0, "items": {}}
        items = raw.get("items", {})
        if not isinstance(items, dict):
            items = {}
        return {
            "updated_at": raw.get("updated_at"),
            "queue_total": int(raw.get("queue_total") or 0),
            "items": items,
        }
    except Exception as exc:
        log.exception("Failed to read enrichment review state %s: %s", path, exc)
        return {"updated_at": None, "queue_total": 0, "items": {}}


def _normalize_review_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    items_raw = raw_state.get("items", {})
    normalized_items: dict[str, dict[str, Any]] = {}
    approved: list[int] = []
    rejected: list[int] = []
    deferred: list[int] = []
    approved_high = 0
    approved_medium = 0
    rejected_by_reason: dict[str, int] = {}

    if isinstance(items_raw, dict):
        for key, value in items_raw.items():
            if not isinstance(value, dict):
                continue
            try:
                track_id = int(value.get("track_id", key))
            except Exception:
                continue
            status = str(value.get("review_status") or value.get("status") or "").strip().lower()
            if status not in {"approved", "rejected", "deferred"}:
                continue
            updated_at = value.get("updated_at") or raw_state.get("updated_at")
            item = {
                "track_id": track_id,
                "review_status": status,
                "updated_at": updated_at,
            }
            for field in (
                "filepath",
                "confidence",
                "provider",
                "action_suggestion",
                "reason",
                "rejection_reason",
                "score",
            ):
                if value.get(field) not in (None, ""):
                    item[field] = value.get(field)
            for field in ("query", "best_match", "queue_item", "candidates"):
                if isinstance(value.get(field), (dict, list)):
                    item[field] = value.get(field)
            normalized_items[str(track_id)] = item
            if status == "approved":
                approved.append(track_id)
                confidence = str(value.get("confidence") or value.get("queue_item", {}).get("confidence") or "").upper()
                if confidence == "HIGH":
                    approved_high += 1
                elif confidence == "MEDIUM":
                    approved_medium += 1
            elif status == "rejected":
                rejected.append(track_id)
                reason = str(
                    value.get("reason")
                    or value.get("rejection_reason")
                    or (value.get("queue_item") or {}).get("reason")
                    or (value.get("queue_item") or {}).get("rejection_reason")
                    or ""
                ).strip()
                if reason:
                    rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
            elif status == "deferred":
                deferred.append(track_id)

    return {
        "items": dict(sorted(normalized_items.items(), key=lambda item: int(item[0]))),
        "approved": sorted(approved),
        "rejected": sorted(rejected),
        "deferred": sorted(deferred),
        "counts": {
            "approved": len(approved),
            "rejected": len(rejected),
            "deferred": len(deferred),
        },
        "approved_high_count": approved_high,
        "approved_medium_count": approved_medium,
        "rejected_by_reason": dict(sorted(rejected_by_reason.items())),
        "queue_total": int(raw_state.get("queue_total") or 0),
        "updated_at": raw_state.get("updated_at"),
    }


def load_review_state() -> dict[str, Any]:
    return _normalize_review_state(_load_review_state_raw())


def save_review_state(raw_state: dict[str, Any]) -> dict[str, Any]:
    path = enrichment_review_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_review_state(raw_state)
    payload = {
        "updated_at": normalized["updated_at"] or _utc_now(),
        "queue_total": normalized["queue_total"],
        "items": normalized["items"],
    }
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
    return _normalize_review_state(payload)


def set_review_state(
    track_id: int,
    review_status: str,
    *,
    queue_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if review_status not in {"approved", "rejected", "deferred"}:
        raise ValueError(f"invalid review status: {review_status}")

    if not db_exists():
        raise FileNotFoundError("pipeline database not available")

    with get_pipeline_conn() as conn:
        row = conn.execute(
            "SELECT id, filepath FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()
    if row is None:
        raise LookupError(f"track {track_id} not found")

    state = _load_review_state_raw()
    items = state.setdefault("items", {})
    if not isinstance(items, dict):
        items = {}
        state["items"] = items
    queue_total = 0
    try:
        queue_total = sum(1 for line in enrichment_queue_path().read_text(encoding="utf-8").splitlines() if line.strip())
    except Exception:
        queue_total = int(state.get("queue_total") or 0)
    queue_item = queue_item or lookup_enrichment_queue_item(str(row["filepath"]))
    items[str(track_id)] = {
        "track_id": track_id,
        "review_status": review_status,
        "updated_at": _utc_now(),
        "queue_total": queue_total,
    }
    if queue_item:
        for field in (
            "filepath",
            "confidence",
            "provider",
            "action_suggestion",
            "reason",
            "rejection_reason",
            "score",
        ):
            if queue_item.get(field) not in (None, ""):
                items[str(track_id)][field] = queue_item.get(field)
        for field in ("query", "best_match", "queue_item", "candidates"):
            if isinstance(queue_item.get(field), (dict, list)):
                items[str(track_id)][field] = queue_item.get(field)
        items[str(track_id)]["queue_item"] = queue_item
    state["queue_total"] = queue_total
    state["updated_at"] = _utc_now()
    return save_review_state(state)


def review_status_map() -> dict[int, dict[str, Any]]:
    state = load_review_state()
    items = state.get("items", {})
    return {
        int(track_id): value
        for track_id, value in items.items()
        if isinstance(value, dict)
    }


def build_review_summary() -> dict[str, Any]:
    state = load_review_state()
    items = state.get("items", {})
    approved = state.get("approved", [])
    rejected = state.get("rejected", [])
    deferred = state.get("deferred", [])
    queue_total = int(state.get("queue_total") or 0)
    pending = max(queue_total - len(approved) - len(rejected) - len(deferred), 0)
    rejected_by_reason = state.get("rejected_by_reason", {})
    if not rejected_by_reason:
        rejected_by_reason = {}
        for item in items.values():
            if not isinstance(item, dict) or item.get("review_status") != "rejected":
                continue
            reason = str(
                item.get("reason")
                or item.get("rejection_reason")
                or (item.get("queue_item") or {}).get("reason")
                or (item.get("queue_item") or {}).get("rejection_reason")
                or ""
            ).strip()
            if reason:
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1

    approved_high = int(state.get("approved_high_count") or 0)
    approved_medium = int(state.get("approved_medium_count") or 0)
    if not approved_high and not approved_medium:
        for item in items.values():
            if not isinstance(item, dict) or item.get("review_status") != "approved":
                continue
            confidence = str(
                item.get("confidence")
                or (item.get("queue_item") or {}).get("confidence")
                or ""
            ).upper()
            if confidence == "HIGH":
                approved_high += 1
            elif confidence == "MEDIUM":
                approved_medium += 1
    return {
        "pending_count": pending,
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "deferred_count": len(deferred),
        "approved_high_count": approved_high,
        "approved_medium_count": approved_medium,
        "rejected_by_reason": rejected_by_reason,
        "last_updated": state.get("updated_at"),
        "queue_total": queue_total,
        "items": items,
    }


def count_tracks() -> int:
    if not db_exists():
        return 0
    try:
        with get_pipeline_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        log.exception("count_tracks failed: %s", exc)
        return 0


def latest_audit_report() -> Optional[dict[str, Any]]:
    root = get_library_root()
    audit_dir = library_audit_dir(root)
    if not audit_dir.exists():
        return None

    candidates = sorted(audit_dir.glob("path_audit_*.json"))
    if not candidates:
        return None

    latest = candidates[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        log.exception("latest_audit_report failed for %s: %s", latest, exc)
        return None


def build_stats_payload() -> dict[str, Any]:
    report = latest_audit_report()
    summary = (report or {}).get("summary", {})
    stale_total = (
        summary.get("stale_processed_state_rows_total")
        if summary.get("stale_processed_state_rows_total") is not None
        else summary.get("stale_processed_state_count", 0)
    )
    return {
        "tracks_count": count_tracks(),
        "disk_audio_files": int(summary.get("disk_audio_files", 0) or 0),
        "missing_files": int(summary.get("missing_files", 0) or 0),
        "untracked_files": int(summary.get("untracked_files", 0) or 0),
        "stale_processed_state_total": int(stale_total or 0),
        "canonical_source": summary.get("canonical_source") or "unknown",
        "last_audit_report": report,
    }


def _load_queue_records_cached(path: Path) -> list[dict[str, Any]]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    cache_valid = (
        _QUEUE_CACHE.get("path") == str(path)
        and _QUEUE_CACHE.get("mtime_ns") == stat.st_mtime_ns
        and _QUEUE_CACHE.get("size") == stat.st_size
    )
    if cache_valid:
        return [dict(record) for record in _QUEUE_CACHE.get("records", [])]

    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict):
            records.append(record)
    _QUEUE_CACHE.update({
        "path": str(path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "records": [dict(record) for record in records],
    })
    return records


def _augment_queue_record(
    record: dict[str, Any],
    *,
    review_state: dict[int, dict[str, Any]],
    track_lookup: dict[str, int],
) -> dict[str, Any]:
    item = dict(record)
    raw_path = str(item.get("filepath") or "")
    track_id = item.get("track_id")
    if track_id is None and raw_path:
        track_id = track_lookup.get(raw_path) or track_lookup.get(_normalize_path_key(raw_path))
    if track_id is not None:
        try:
            track_id_int = int(track_id)
            item["track_id"] = track_id_int
            review_item = review_state.get(track_id_int)
            item["review_status"] = (
                review_item.get("review_status") if review_item else "pending"
            )
            if review_item and review_item.get("updated_at"):
                item["review_updated_at"] = review_item.get("updated_at")
        except Exception:
            item["review_status"] = "pending"
    else:
        item["review_status"] = "pending"
    return item


def load_enrichment_queue(
    *,
    action: str | None = None,
    confidence: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    path = enrichment_queue_path()
    if not path.exists():
        return {
            "items": [],
            "counts": {"by_action": {}, "by_confidence": {}},
            "limit": limit,
            "offset": offset,
            "total": 0,
        }

    review_state = review_status_map()
    track_lookup = _track_id_lookup()
    try:
        items: list[dict[str, Any]] = []
        for raw_record in _load_queue_records_cached(path):
            record = _augment_queue_record(
                raw_record,
                review_state=review_state,
                track_lookup=track_lookup,
            )
            if action and str(record.get("action_suggestion")) != action:
                continue
            if confidence and str(record.get("confidence")) != confidence:
                continue
            items.append(record)
    except Exception as exc:
        log.exception("Failed to read enrichment queue %s: %s", path, exc)
        items = []

    action_counts = Counter(
        str(item.get("action_suggestion", ""))
        for item in items
        if item.get("action_suggestion")
    )
    confidence_counts = Counter(
        str(item.get("confidence", ""))
        for item in items
        if item.get("confidence")
    )
    page = items[offset: offset + limit]

    return {
        "items": page,
        "counts": {
            "by_action": dict(sorted(action_counts.items())),
            "by_confidence": dict(sorted(confidence_counts.items())),
        },
        "limit": limit,
        "offset": offset,
        "total": len(items),
    }


def lookup_enrichment_queue_item(filepath: str) -> Optional[dict[str, Any]]:
    path = enrichment_queue_path()
    if not path.exists():
        return None

    try:
        target = str(Path(filepath).resolve(strict=False))
    except Exception:
        target = str(filepath)

    review_state = review_status_map()
    track_lookup = _track_id_lookup()

    try:
        for raw_record in _load_queue_records_cached(path):
            record = _augment_queue_record(
                raw_record,
                review_state=review_state,
                track_lookup=track_lookup,
            )
            raw_path = str(record.get("filepath") or "")
            if not raw_path:
                continue
            try:
                record_path = str(Path(raw_path).resolve(strict=False))
            except Exception:
                record_path = raw_path
            if raw_path == filepath or record_path == target:
                return record
    except Exception as exc:
        log.exception("lookup_enrichment_queue_item failed for %s: %s", filepath, exc)
    return None


def list_folder_stats() -> list[dict[str, Any]]:
    if not db_exists():
        return []

    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"track_count": 0, "issue_count": 0})
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute("SELECT * FROM tracks ORDER BY filepath").fetchall()
        for row in rows:
            track = Track.from_row(row)
            folder = str(Path(track.filepath).parent)
            bucket = buckets[folder]
            bucket["track_count"] += 1
            if track.issues:
                bucket["issue_count"] += 1

        return [
            {"folder": folder, **counts}
            for folder, counts in sorted(buckets.items(), key=lambda item: item[0].lower())
        ]
    except Exception as exc:
        log.exception("list_folder_stats failed: %s", exc)
        return []


def _row_to_ledger_dict(row) -> dict[str, Any]:
    return {
        "ledger_id": row["ledger_id"],
        "created_at": row["created_at"],
        "root": row["root"],
        "operation_type": row["operation_type"],
        "old_path": row["old_path"],
        "new_path": row["new_path"],
        "affected_tables": row["affected_tables"],
        "before_values_json": row["before_values_json"],
        "after_values_json": row["after_values_json"],
        "status": row["status"],
        "error": row["error"],
    }


def list_reconciliation_ledger(limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    if not db_exists():
        return []
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                "SELECT ledger_id, created_at, root, operation_type, old_path, new_path, "
                "affected_tables, before_values_json, after_values_json, status, error "
                "FROM reconciliation_ledger "
                "ORDER BY created_at DESC, ledger_id DESC "
                "LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_ledger_dict(row) for row in rows]
    except Exception as exc:
        log.exception("list_reconciliation_ledger failed: %s", exc)
        return []


def get_reconciliation_ledger(ledger_id: str) -> dict[str, Any] | None:
    if not db_exists():
        return None
    try:
        with get_pipeline_conn() as conn:
            row = conn.execute(
                "SELECT ledger_id, created_at, root, operation_type, old_path, new_path, "
                "affected_tables, before_values_json, after_values_json, status, error "
                "FROM reconciliation_ledger WHERE ledger_id = ?",
                (ledger_id,),
            ).fetchone()
        return _row_to_ledger_dict(row) if row is not None else None
    except Exception as exc:
        log.exception("get_reconciliation_ledger failed for %s: %s", ledger_id, exc)
        return None


def build_overview_payload() -> dict[str, Any]:
    if not db_exists():
        return {
            "total_tracks": 0,
            "tracks_with_bpm": 0,
            "tracks_with_camelot_key": 0,
            "tracks_analyzed": 0,
            "tracks_missing_artist": 0,
            "tracks_missing_title": 0,
            "parse_confidence_breakdown": {},
            "genre_top_counts": [],
        }

    try:
        with get_pipeline_conn() as conn:
            library_where, library_params = storage_zone_predicate(conn, "LIBRARY")
            agg = conn.execute(
                """SELECT
                       COUNT(*) AS total_tracks,
                       SUM(CASE WHEN bpm IS NOT NULL THEN 1 ELSE 0 END) AS tracks_with_bpm,
                       SUM(CASE WHEN TRIM(COALESCE(key_camelot,'')) != ''
                                 OR TRIM(COALESCE(key_musical,'')) != '' THEN 1 ELSE 0 END) AS tracks_with_camelot_key,
                       SUM(CASE WHEN bpm IS NOT NULL
                                 AND (TRIM(COALESCE(key_camelot,'')) != ''
                                      OR TRIM(COALESCE(key_musical,'')) != '') THEN 1 ELSE 0 END) AS tracks_analyzed,
                       SUM(CASE WHEN TRIM(COALESCE(artist,'')) = '' THEN 1 ELSE 0 END) AS tracks_missing_artist,
                       SUM(CASE WHEN TRIM(COALESCE(title,'')) = '' THEN 1 ELSE 0 END) AS tracks_missing_title
                   FROM tracks
                   WHERE """ + library_where,
                library_params,
            ).fetchone()

            confidence_rows = conn.execute(
                """SELECT COALESCE(NULLIF(TRIM(parse_confidence), ''), 'UNKNOWN') AS parse_confidence,
                          COUNT(*) AS cnt
                   FROM tracks
                   WHERE """ + library_where + """
                   GROUP BY COALESCE(NULLIF(TRIM(parse_confidence), ''), 'UNKNOWN')
                   ORDER BY CASE COALESCE(NULLIF(TRIM(parse_confidence), ''), 'UNKNOWN')
                                WHEN 'HIGH' THEN 0
                                WHEN 'MEDIUM' THEN 1
                                WHEN 'LOW' THEN 2
                                ELSE 3
                            END""",
                library_params,
            ).fetchall()

            genre_rows = conn.execute(
                """SELECT COALESCE(NULLIF(TRIM(genre), ''), 'UNKNOWN') AS genre,
                          COUNT(*) AS cnt
                   FROM tracks
                   WHERE """ + library_where + """
                   GROUP BY COALESCE(NULLIF(TRIM(genre), ''), 'UNKNOWN')
                   ORDER BY cnt DESC, LOWER(COALESCE(NULLIF(TRIM(genre), ''), 'UNKNOWN'))
                   LIMIT 10""",
                library_params,
            ).fetchall()

        return {
            "total_tracks": int(agg["total_tracks"] or 0),
            "tracks_with_bpm": int(agg["tracks_with_bpm"] or 0),
            "tracks_with_camelot_key": int(agg["tracks_with_camelot_key"] or 0),
            "tracks_analyzed": int(agg["tracks_analyzed"] or 0),
            "tracks_missing_artist": int(agg["tracks_missing_artist"] or 0),
            "tracks_missing_title": int(agg["tracks_missing_title"] or 0),
            "parse_confidence_breakdown": {
                row["parse_confidence"]: int(row["cnt"] or 0) for row in confidence_rows
            },
            "genre_top_counts": [
                {"genre": row["genre"], "count": int(row["cnt"] or 0)}
                for row in genre_rows
            ],
        }
    except Exception as exc:
        log.exception("build_overview_payload failed: %s", exc)
        return {
            "total_tracks": 0,
            "tracks_with_bpm": 0,
            "tracks_with_camelot_key": 0,
            "tracks_analyzed": 0,
            "tracks_missing_artist": 0,
            "tracks_missing_title": 0,
            "parse_confidence_breakdown": {},
            "genre_top_counts": [],
        }


def latest_audit_path() -> Path | None:
    audit_dir = library_audit_dir()
    if not audit_dir.exists():
        return None
    candidates = sorted(audit_dir.glob("path_audit_*.json"))
    return candidates[-1] if candidates else None


def load_latest_audit_json() -> dict[str, Any] | None:
    path = latest_audit_path()
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.exception("Failed to parse audit JSON %s: %s", path, exc)
        return None


def audit_json_available() -> bool:
    return load_latest_audit_json() is not None
