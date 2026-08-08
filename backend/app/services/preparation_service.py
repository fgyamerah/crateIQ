"""
Batch Inbox preparation engine (Cycle 10, provider consensus wired in Cycle 12).

"Process All" and "Clean/Enrich Selected" are thin orchestrators over
already-existing, already-safety-reviewed engines -- this module never
re-implements provider adapters, consensus scoring, tag writing, or analysis:

  - deterministic cleanup   -> modules.sanitizer.sanitize_metadata()
  - identification/enrichment -> provider_routing_service.gather_evidence()
    (Cycle 11 staged provider routing: Beets+MusicBrainz first, then
    AcoustID/Discogs/Beatport/Spotify/Deezer/Last.fm/YouTube only where
    configured and available) reduced to a verdict per field by
    consensus_service.build_track_consensus()
  - metadata write-back     -> tag_write_service.build_plan()/apply_plan()
  - BPM/key analysis        -> analysis_jobs_service.run()

HIGH-confidence auto-apply rule (product decision): a cleanup change is
always deterministic-safe by construction (sanitize_metadata only ever
strips known junk tokens/formatting, never invents new values). An
enrichment field is HIGH-confidence only per consensus_service's own
field-level verdict -- a HIGH track identity never implies every field is
HIGH, and Genre in particular is resolved through the existing genre
taxonomy/authority rules, never a blanket copy of identity confidence.
A HIGH field only auto-applies if the current value is empty, or -- the one
approved exception -- already identified as junk/placeholder by
export_validation's existing rules, in which case the junk value is cleared
first so the write is additive, never a silent overwrite of a value someone
could have deliberately set. Anything else -- MEDIUM/LOW/CONFLICT, or a HIGH
field blocked by an existing non-empty non-junk value -- is left exactly as
-is and queued (with full provider evidence) into the exact same
enrichment_review_service decision queue online_lookup() already populates,
so unified Needs Review (needs_review_service) surfaces it unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from . import (
    consensus_service,
    enrichment_review_service,
    export_validation,
    preparation_operations_service,
    provider_routing_service,
    tag_write_service,
    workspace_service,
)
from ..core.library_root import assert_path_under_root

log = logging.getLogger(__name__)

_MAX_ENRICH_LOOKUPS_PER_RUN = 25
_WRITE_CHUNK_SIZE = 50
_SANITIZE_FIELDS = ("artist", "title", "album", "genre")
_CONSENSUS_FIELDS = ("artist", "title", "genre")


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
# Enrich stage -- Cycle 11 provider routing + field-level consensus,
# bounded network lookups, reuses enrichment_review_service for decision
# bookkeeping and tag_write_service (via write_tracks) for the actual write.
# ---------------------------------------------------------------------------

def _is_junk_value(field: str, value: str | None) -> bool:
    """Empty, or matches export_validation's existing approved junk/placeholder rules."""
    v = (value or "").strip()
    if not v:
        return True
    if field == "genre":
        return export_validation.is_junk_genre(v)
    return v.lower() in export_validation.UNKNOWN_ARTISTS


def _needs_consensus_enrichment(row: Any) -> bool:
    return any(_is_junk_value(field, row[field]) for field in _CONSENSUS_FIELDS)


def enrich_tracks(root: Path, track_ids: list[int], *, limit: int = _MAX_ENRICH_LOOKUPS_PER_RUN) -> dict[str, Any]:
    with _sqlite_connect(root) as conn:
        placeholders = ",".join("?" * len(track_ids)) if track_ids else ""
        rows = conn.execute(
            f"SELECT id, artist, title, genre, filename, filepath FROM tracks "
            f"WHERE storage_zone = 'INBOX' AND id IN ({placeholders})", track_ids,
        ).fetchall() if track_ids else []

    eligible = [row for row in rows if _needs_consensus_enrichment(row)][:limit]

    enriched = review_added = 0
    to_apply: list[dict[str, Any]] = []
    warnings: list[str] = []
    credentials_by_source = provider_routing_service.all_provider_credentials()

    for row in eligible:
        track_id = row["id"]
        try:
            inbox_path = Path(row["filepath"])
            evidence = provider_routing_service.gather_evidence(
                track_id, artist=row["artist"], title=row["title"],
                inbox_copy_path=inbox_path if inbox_path.is_file() else None,
                credentials_by_source=credentials_by_source,
            )
            with _sqlite_connect(root) as conn:
                consensus = consensus_service.build_track_consensus(track_id, evidence, conn=conn)
        except Exception as exc:  # noqa: BLE001 - one track's provider/consensus failure must not abort the batch
            warnings.append(f"track {track_id}: provider consensus failed ({exc}).")
            continue

        apply_fields: dict[str, str] = {}
        junk_fields: list[str] = []
        review_fields: dict[str, Any] = {}

        for field_name in _CONSENSUS_FIELDS:
            fc = consensus.fields.get(field_name)
            if fc is None or fc.reason_code == "no_evidence":
                continue  # nothing was found for this field at all -- not worth surfacing
            current_value = (row[field_name] or "").strip()
            if fc.confidence == "HIGH" and fc.value:
                if not current_value:
                    apply_fields[field_name] = fc.value
                elif _is_junk_value(field_name, current_value):
                    apply_fields[field_name] = fc.value
                    junk_fields.append(field_name)
                else:
                    review_fields[field_name] = fc  # valid existing data is never auto-replaced
            else:
                review_fields[field_name] = fc  # MEDIUM / LOW / CONFLICT (value may be None)

        try:
            relative_path = str(assert_path_under_root(row["filepath"], root).relative_to(root))
        except (ValueError, TypeError):
            relative_path = None
        current_fields = {f: (row[f] or None) for f in _CONSENSUS_FIELDS}

        if apply_fields:
            if junk_fields:
                # The one approved exception to "never overwrite existing
                # metadata": the current value is already junk/placeholder.
                # Clear it first so the decision-queue write below is an
                # ordinary fill of an empty field, never a silent overwrite.
                with _sqlite_connect(root) as clear_conn:
                    clear_conn.execute(
                        f"UPDATE tracks SET {', '.join(f'{f} = NULL' for f in junk_fields)} WHERE id = ?",
                        (track_id,),
                    )
                    clear_conn.commit()
                for f in junk_fields:
                    current_fields[f] = None

            suggestion_id = f"sug-{uuid.uuid4().hex[:12]}"
            reason = "Auto-approved: Process All (Cycle 11 provider consensus, HIGH field confidence"
            reason += f", junk value cleared for {', '.join(junk_fields)}" if junk_fields else ""
            reason += ")."
            enrichment_review_service.queue_consensus_suggestions([{
                "suggestion_id": suggestion_id, "track_id": track_id, "source_id": "consensus_apply",
                "confidence": "high", "reason": reason,
                "filename": row["filename"], "relative_path": relative_path,
                "current_fields": current_fields, "suggested_fields": apply_fields,
                "allowed_fields": list(apply_fields),
                "evidence": {f: consensus.fields[f].evidence for f in apply_fields},
            }])
            try:
                enrichment_review_service.update_suggestion(track_id, suggestion_id, "applied", reason, apply_fields)
                to_apply.append({"track_id": track_id, "suggestion_id": suggestion_id, "fields": apply_fields})
            except (ValueError, LookupError) as exc:
                warnings.append(f"track {track_id}: could not save consensus decision ({exc}).")

        if review_fields:
            worst = "low" if any(fc.confidence in ("LOW", "CONFLICT") for fc in review_fields.values()) else "medium"
            summary = "; ".join(f"{f}: {fc.reason_code} ({fc.confidence})" for f, fc in review_fields.items())
            enrichment_review_service.queue_consensus_suggestions([{
                "suggestion_id": f"sug-{uuid.uuid4().hex[:12]}", "track_id": track_id, "source_id": "consensus_review",
                "confidence": worst, "reason": f"Provider consensus needs review: {summary}.",
                "filename": row["filename"], "relative_path": relative_path,
                "current_fields": current_fields,
                "suggested_fields": {f: fc.value for f, fc in review_fields.items() if fc.value},
                "allowed_fields": [f for f, fc in review_fields.items() if fc.value],
                "evidence": {f: fc.evidence for f, fc in review_fields.items()},
            }])
            review_added += 1

    if to_apply:
        try:
            result = enrichment_review_service.apply_selected(to_apply, confirm=True)
            enriched = result["applied"]
            warnings.extend(result["warnings"])
        except ValueError as exc:
            warnings.append(str(exc))

    return {"enriched_count": enriched, "considered": len(eligible), "review_added": review_added, "warnings": warnings}


# ---------------------------------------------------------------------------
# Write stage -- reuses tag_write_service exactly, chunked to its own cap
# ---------------------------------------------------------------------------

def write_tracks(track_ids: list[int]) -> dict[str, Any]:
    """
    Reuses tag_write_service's exact build_plan -> apply_plan contract,
    chunked to its own per-request cap. `results` carries a per-track outcome
    (matching tag_write_service.apply_plan's status vocabulary, plus
    "no_op"/"blocked" for tracks apply_plan is never even called for) so
    callers that need track-level truth -- e.g. bulk Inbox edits -- don't
    have to re-implement this chunking loop themselves.
    """
    written = failed = 0
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    for start in range(0, len(track_ids), _WRITE_CHUNK_SIZE):
        chunk = track_ids[start:start + _WRITE_CHUNK_SIZE]
        try:
            plan = tag_write_service.build_plan(chunk)
        except ValueError as exc:
            warnings.append(str(exc))
            continue

        # Build blocked/no_op results directly from the plan (single source
        # of truth) rather than from apply_plan's own "skipped" status, which
        # collapses both cases under one label distinguishable only by reason
        # text -- fragile to depend on from a caller.
        results.extend(
            {"track_id": item["track_id"], "status": "blocked", "reason": item["blocker"]}
            for item in plan["items"] if item["blocked"]
        )
        results.extend(
            {"track_id": item["track_id"], "status": "no_op"}
            for item in plan["items"] if not item["blocked"] and not item["fields"]
        )
        expected = {
            item["track_id"]: {"expected_size": item["expected_size"], "expected_mtime_ns": item["expected_mtime_ns"]}
            for item in plan["items"] if not item["blocked"] and item["fields"]
        }
        if not expected:
            continue
        result = tag_write_service.apply_plan(chunk, expected, confirm=True)
        written += result["applied"]
        failed += result["failed"]
        # apply_plan recomputes its own plan over the whole chunk and would
        # re-report the blocked/no-field tracks above (as "skipped") -- keep
        # only the entries for tracks that were actually changeable here.
        results.extend(r for r in result["results"] if r["track_id"] in expected)
    return {"written_count": written, "failed_count": failed, "warnings": warnings, "results": results}


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
