"""
Batch Inbox preparation engine (Cycle 10).

"Process All" and "Clean/Enrich Selected" are thin orchestrators over
already-existing, already-safety-reviewed engines -- this module never
re-implements cleanup, enrichment matching, tag writing, or analysis:

  - deterministic cleanup   -> modules.sanitizer.sanitize_metadata()
  - identification/enrichment -> enrichment_review_service.online_lookup()
    (Beets distance-scored + raw MusicBrainz search, exactly Cycle 6's
    single-track flow, just looped with a bound)
  - metadata write-back     -> tag_write_service.build_plan()/apply_plan()
  - BPM/key analysis        -> analysis_jobs_service.run()

HIGH-confidence auto-apply rule (product decision): a cleanup change is
always deterministic-safe by construction (sanitize_metadata only ever
strips known junk tokens/formatting, never invents new values). An
enrichment candidate is HIGH-confidence only when Beets and MusicBrainz
independently agree on the same normalized artist+title, or either source
alone reports its own 'high' confidence tier. Anything else -- disagreement,
a single low/medium-confidence source, or no candidate at all -- is left
exactly as-is; the existing enrichment_review_service decision queue
(populated by online_lookup itself) is where it surfaces for manual review,
and unified Needs Review (needs_review_service) reads that same queue.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from . import enrichment_review_service, preparation_operations_service, tag_write_service, workspace_service
from ..core.library_root import assert_path_under_root

log = logging.getLogger(__name__)

_MAX_ENRICH_LOOKUPS_PER_RUN = 25
_WRITE_CHUNK_SIZE = 50
_SANITIZE_FIELDS = ("artist", "title", "album", "genre")


def _sqlite_connect(root: Path):
    """
    Fails closed with ValueError rather than letting sqlite3.connect() silently
    create an empty DB file (or later crash with "no such table") when a root
    has no local index yet.
    """
    import sqlite3
    db_path = assert_path_under_root(root / "logs" / "processed.db", root)
    if not db_path.is_file():
        raise ValueError("Configure and initialize the managed workspace first.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _inbox_track_ids(root: Path, track_ids: list[int] | None) -> list[int]:
    with _sqlite_connect(root) as conn:
        if track_ids:
            placeholders = ",".join("?" * len(track_ids))
            rows = conn.execute(
                f"SELECT id FROM tracks WHERE storage_zone = 'INBOX' AND id IN ({placeholders})", track_ids
            ).fetchall()
        else:
            rows = conn.execute("SELECT id FROM tracks WHERE storage_zone = 'INBOX'").fetchall()
    return [row["id"] for row in rows]


# ---------------------------------------------------------------------------
# Preflight preview -- read-only
# ---------------------------------------------------------------------------

def preflight_preview(root: Path) -> dict[str, Any]:
    import modules.sanitizer as sanitizer
    from ..core.preflight import redact_path

    try:
        with _sqlite_connect(root) as conn:
            rows = conn.execute("SELECT * FROM tracks WHERE storage_zone = 'INBOX'").fetchall()
    except ValueError:
        return {
            "library_root": redact_path(root), "inbox_total": 0, "already_ready": 0,
            "need_cleaning": 0, "need_enrichment": 0, "need_analysis": 0, "likely_review": 0,
            "unsupported_write_format": 0, "enrichment_lookup_bound": _MAX_ENRICH_LOOKUPS_PER_RUN,
            "message": "No managed workspace is configured yet.",
        }

    need_cleaning = 0
    need_enrichment = 0
    unsupported = 0
    for row in rows:
        current = {field: row[field] for field in _SANITIZE_FIELDS}
        _sanitized, changes = sanitizer.sanitize_metadata(current)
        if changes:
            need_cleaning += 1
        if not (row["artist"] or "").strip() or not (row["title"] or "").strip():
            need_enrichment += 1
        if Path(row["filepath"]).suffix.lower() not in (".mp3", ".flac"):
            unsupported += 1

    preview = workspace_service.promotion_preview(root, [r["id"] for r in rows])
    return {
        "library_root": preview["library_root"],
        "inbox_total": len(rows),
        "already_ready": preview["ready_count"],
        "need_cleaning": need_cleaning,
        "need_enrichment": need_enrichment,
        "need_analysis": sum(1 for r in rows if r["bpm"] is None or not (r["key_camelot"] or r["key_musical"])),
        "likely_review": preview["blocked_count"],
        "unsupported_write_format": unsupported,
        "enrichment_lookup_bound": _MAX_ENRICH_LOOKUPS_PER_RUN,
        "message": (
            "crateIQ processes managed Inbox copies. Original imported files "
            "remain untouched. Confirming authorizes deterministic safe "
            "cleanup, HIGH-confidence metadata enrichment, writing those "
            "approved changes to the managed Inbox copies, verification, and "
            "BPM/key analysis. It never promotes tracks to the Library."
        ),
    }


# ---------------------------------------------------------------------------
# Clean stage -- deterministic, local, no network
# ---------------------------------------------------------------------------

def clean_tracks(root: Path, track_ids: list[int]) -> dict[str, Any]:
    import modules.sanitizer as sanitizer

    cleaned = 0
    results: list[dict[str, Any]] = []
    with _sqlite_connect(root) as conn:
        placeholders = ",".join("?" * len(track_ids)) if track_ids else ""
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE storage_zone = 'INBOX' AND id IN ({placeholders})", track_ids
        ).fetchall() if track_ids else []
        for row in rows:
            current = {field: row[field] for field in _SANITIZE_FIELDS}
            sanitized, changes = sanitizer.sanitize_metadata(current)
            if not changes:
                results.append({"track_id": row["id"], "status": "no_change"})
                continue
            conn.execute(
                "UPDATE tracks SET artist = ?, title = ?, album = ?, genre = ? WHERE id = ?",
                (sanitized.get("artist") or row["artist"],
                 sanitized.get("title") or row["title"],
                 sanitized.get("album") or row["album"],
                 sanitized.get("genre") or row["genre"],
                 row["id"]),
            )
            cleaned += 1
            results.append({"track_id": row["id"], "status": "cleaned", "changes": changes})
        conn.commit()
    return {"cleaned_count": cleaned, "results": results}


# ---------------------------------------------------------------------------
# Enrich stage -- bounded network lookups, reuses enrichment_review_service
# ---------------------------------------------------------------------------

def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def enrich_tracks(root: Path, track_ids: list[int], *, limit: int = _MAX_ENRICH_LOOKUPS_PER_RUN) -> dict[str, Any]:
    with _sqlite_connect(root) as conn:
        placeholders = ",".join("?" * len(track_ids)) if track_ids else ""
        rows = conn.execute(
            f"SELECT id, artist, title FROM tracks WHERE storage_zone = 'INBOX' AND id IN ({placeholders})",
            track_ids,
        ).fetchall() if track_ids else []

    eligible = [row["id"] for row in rows if not (row["artist"] or "").strip() or not (row["title"] or "").strip()]
    eligible = eligible[:limit]

    enriched = 0
    to_apply: list[dict[str, Any]] = []
    warnings: list[str] = []

    for track_id in eligible:
        try:
            beets_review = enrichment_review_service.online_lookup(track_id, "beets")
            mb_review = enrichment_review_service.online_lookup(track_id, "musicbrainz")
        except Exception as exc:  # noqa: BLE001 - one track's provider failure must not abort the batch
            warnings.append(f"track {track_id}: online lookup failed ({exc}).")
            continue

        beets_item = next((i for i in beets_review["items"] if i["track_id"] == track_id and i["source_id"] == "beets"), None)
        mb_item = next((i for i in mb_review["items"] if i["track_id"] == track_id and i["source_id"] == "musicbrainz"), None)

        candidate = None
        if beets_item and mb_item:
            agree = (
                _normalize(beets_item["suggested_fields"].get("artist")) == _normalize(mb_item["suggested_fields"].get("artist"))
                and _normalize(beets_item["suggested_fields"].get("title")) == _normalize(mb_item["suggested_fields"].get("title"))
                and beets_item["suggested_fields"].get("artist")
            )
            if agree:
                candidate = beets_item
        if candidate is None and beets_item and beets_item.get("confidence") == "high":
            candidate = beets_item
        if candidate is None and mb_item and mb_item.get("confidence") == "high":
            candidate = mb_item

        if candidate is None:
            continue  # left pending in the review queue for manual review

        try:
            enrichment_review_service.update_suggestion(
                track_id, candidate["suggestion_id"], "applied",
                "Auto-approved: Process All (HIGH-confidence provider agreement).",
                candidate["suggested_fields"],
            )
            to_apply.append({"track_id": track_id, "suggestion_id": candidate["suggestion_id"], "fields": candidate["suggested_fields"]})
        except (ValueError, LookupError) as exc:
            warnings.append(f"track {track_id}: could not save enrichment decision ({exc}).")

    if to_apply:
        try:
            result = enrichment_review_service.apply_selected(to_apply, confirm=True)
            enriched = result["applied"]
            warnings.extend(result["warnings"])
        except ValueError as exc:
            warnings.append(str(exc))

    return {"enriched_count": enriched, "considered": len(eligible), "warnings": warnings}


# ---------------------------------------------------------------------------
# Write stage -- reuses tag_write_service exactly, chunked to its own cap
# ---------------------------------------------------------------------------

def write_tracks(track_ids: list[int]) -> dict[str, Any]:
    written = failed = 0
    warnings: list[str] = []
    for start in range(0, len(track_ids), _WRITE_CHUNK_SIZE):
        chunk = track_ids[start:start + _WRITE_CHUNK_SIZE]
        try:
            plan = tag_write_service.build_plan(chunk)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        expected = {
            item["track_id"]: {"expected_size": item["expected_size"], "expected_mtime_ns": item["expected_mtime_ns"]}
            for item in plan["items"] if not item["blocked"] and item["fields"]
        }
        if not expected:
            continue
        result = tag_write_service.apply_plan(chunk, expected, confirm=True)
        written += result["applied"]
        failed += result["failed"]
    return {"written_count": written, "failed_count": failed, "warnings": warnings}


# ---------------------------------------------------------------------------
# Process All -- async background orchestrator (cancellable, restart-safe)
# ---------------------------------------------------------------------------

async def run_process_all(operation_id: str, root: Path, track_ids: list[int]) -> None:
    cleaned = enriched = written = failed = 0
    warnings: list[str] = []
    try:
        clean_result = clean_tracks(root, track_ids)
        cleaned = clean_result["cleaned_count"]
        await asyncio.sleep(0)

        if preparation_operations_service.is_cancel_requested(operation_id):
            _finish(operation_id, root, track_ids, "cancelled", cleaned, enriched, written, failed, warnings)
            return

        enrich_result = enrich_tracks(root, track_ids)
        enriched = enrich_result["enriched_count"]
        warnings.extend(enrich_result["warnings"])
        await asyncio.sleep(0)

        if preparation_operations_service.is_cancel_requested(operation_id):
            _finish(operation_id, root, track_ids, "cancelled", cleaned, enriched, written, failed, warnings)
            return

        write_result = write_tracks(track_ids)
        written = write_result["written_count"]
        failed = write_result["failed_count"]
        warnings.extend(write_result["warnings"])
        await asyncio.sleep(0)

        try:
            from . import analysis_jobs_service
            analysis_jobs_service.run("bpm_analysis", confirm=True, limit=25)
            analysis_jobs_service.run("key_analysis", confirm=True, limit=25)
        except Exception as exc:  # noqa: BLE001 - analysis is best-effort within Process All
            warnings.append(f"Analysis step skipped: {exc}")

        try:
            # Refresh the deterministic local-index issue scan so Needs
            # Review reflects this run's results. Cheap, no network, and
            # legitimate here since Process All is already a write-permitted,
            # explicitly confirmed operation -- unlike a plain GET, which
            # must stay read-only.
            from . import metadata_repair_queue_service
            metadata_repair_queue_service.refresh()
        except Exception as exc:  # noqa: BLE001 - review-queue refresh is best-effort
            warnings.append(f"Needs Review refresh skipped: {exc}")

        status = "cancelled" if preparation_operations_service.is_cancel_requested(operation_id) else "completed"
        _finish(operation_id, root, track_ids, status, cleaned, enriched, written, failed, warnings)
    except Exception as exc:  # noqa: BLE001 - must always reach a terminal state
        log.exception("Process All operation %s failed", operation_id)
        preparation_operations_service.finish_operation(
            operation_id, status="failed", cleaned_count=cleaned, enriched_count=enriched,
            written_count=written, needs_review_count=0, ready_count=0, failed_count=failed,
            warnings=warnings, error_reason=str(exc),
        )


def _finish(
    operation_id: str, root: Path, track_ids: list[int], status: str,
    cleaned: int, enriched: int, written: int, failed: int, warnings: list[str],
) -> None:
    try:
        preview = workspace_service.promotion_preview(root, track_ids)
        ready = preview["ready_count"]
        needs_review = preview["blocked_count"]
    except ValueError:
        ready = needs_review = 0
    preparation_operations_service.finish_operation(
        operation_id, status=status, cleaned_count=cleaned, enriched_count=enriched,
        written_count=written, needs_review_count=needs_review, ready_count=ready,
        failed_count=failed, warnings=warnings,
    )


def start_process_all(root: Path, *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Process All requires confirm=true after reviewing the preflight preview.")
    track_ids = _inbox_track_ids(root, None)
    if not track_ids:
        raise ValueError("Inbox is empty. Import music before running Process All.")
    operation = preparation_operations_service.start_operation("process_all", track_count=len(track_ids))
    asyncio.create_task(run_process_all(operation["id"], root, track_ids))
    return {"operation_id": operation["id"], "track_count": len(track_ids)}
