"""Read-only Stage 1 reference-artifact reconciliation findings.

This service intentionally opens SQLite databases in ``mode=ro`` and only
reads the bounded queue discovery surface.  It neither initializes optional
tables nor writes plans, caches, files, or reconciliation state.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core import db as backend_db
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from .track_source_service import library_identity

_MAX_FINDINGS = 500
_MAX_QUEUE_FILES = 50
_MAX_QUEUE_BYTES = 1_000_000
_MAX_QUEUE_RECORDS = 500
_PATH_KEYS = {"file", "filepath", "path", "track_path", "original_path", "current_path", "target_path", "old_path", "new_path", "relative_path"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ro_connection(path: Path) -> sqlite3.Connection:
    # Quote the filesystem path so a valid selected-root component such as
    # ``#`` or ``?`` cannot alter SQLite URI parameters.
    return sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _rows(conn: sqlite3.Connection, table: str) -> Iterator[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    for row in conn.execute(f'SELECT * FROM "{table}"'):
        yield dict(row)


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"ref-stale-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def _safe_path(value: Any, root: Path) -> tuple[str | None, bool]:
    if not isinstance(value, str) or not value:
        return None, False
    try:
        resolved = assert_path_under_root(value, root)
    except ValueError:
        return None, False
    return str(resolved.relative_to(root)), resolved.exists()


def _finding(*, artifact_type: str, owner: str, identifier: str, field: str,
             stale: str | int, evidence: str, category: str, disposition: str,
             now: str, blockers: list[str] | None = None,
             candidate: str | int | None = None, confidence: str | None = None) -> dict[str, Any]:
    return {
        "finding_id": _stable_id(artifact_type, identifier, field, stale),
        "artifact_type": artifact_type,
        "artifact_owner": owner,
        "artifact_identifier": identifier,
        "reference_field": field,
        "stale_value": stale,
        "candidate_replacement": candidate,
        "evidence_source": evidence,
        "classification": category,
        "confidence": confidence,
        "blockers": blockers or [],
        "recommended_disposition": disposition,
        "generated_at": now,
    }


def _missing_path_finding(row: dict[str, Any], *, artifact_type: str, owner: str,
                          identifier: str, field: str, category: str,
                          disposition: str, now: str) -> dict[str, Any] | None:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    relative, exists = _safe_path(value, selected_library_root())
    if exists:
        return None
    stale = relative if relative is not None else "<outside selected root>"
    blockers = ["reference_outside_selected_root"] if relative is None else ["no_deterministic_track_identity"]
    if category == "A":
        blockers = ["immutable_historical_provenance"] + blockers
    return _finding(artifact_type=artifact_type, owner=owner, identifier=identifier, field=field,
                    stale=stale, evidence="root_contained_path_existence_check", category=category,
                    disposition=disposition, now=now, blockers=blockers)


def _track_finding(row: dict[str, Any], *, artifact_type: str, owner: str,
                   identifier: str, field: str, category: str, disposition: str,
                   track_ids: set[int], now: str) -> dict[str, Any] | None:
    value = row.get(field)
    if not isinstance(value, int) or value in track_ids:
        return None
    blockers = ["orphaned_track_id"]
    if category == "A":
        blockers.insert(0, "immutable_historical_provenance")
    elif category == "E":
        blockers.insert(0, "disposable_operational_state")
    return _finding(artifact_type=artifact_type, owner=owner, identifier=identifier, field=field,
                    stale=value, evidence="canonical_tracks_id_lookup", category=category,
                    disposition=disposition, now=now, blockers=blockers)


def _cached_path_finding(row: dict[str, Any], *, artifact_type: str, owner: str,
                         identifier: str, field: str, category: str, disposition: str,
                         current_paths: dict[int, str], root: Path, now: str) -> dict[str, Any] | None:
    """Compare a path snapshot only when its row carries a surviving track ID."""
    track_id = row.get("track_id")
    current = current_paths.get(track_id) if isinstance(track_id, int) else None
    value = row.get(field)
    if not current or not isinstance(value, str) or not value.strip() or value == current:
        return None
    stale, _ = _safe_path(value, root)
    candidate, candidate_exists = _safe_path(current, root)
    if stale is None:
        stale = "<outside selected root>"
    blockers = [] if candidate_exists and candidate is not None else ["canonical_track_path_missing_or_unsafe"]
    return _finding(artifact_type=artifact_type, owner=owner, identifier=identifier, field=field,
                    stale=stale, candidate=candidate if candidate_exists else None,
                    confidence="HIGH" if candidate_exists else None,
                    evidence="same_track_id_current_canonical_path", category=category,
                    disposition=disposition if candidate_exists else "manual_review", now=now, blockers=blockers)


def _json_references(value: Any, location: str = "") -> Iterator[tuple[str, Any, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}" if location else key
            if key in _PATH_KEYS and isinstance(child, str):
                yield key, child, child_location
            elif key == "track_id" and isinstance(child, int):
                yield key, child, child_location
            else:
                yield from _json_references(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _json_references(child, f"{location}[{index}]")


def _scan_json_blob(row: dict[str, Any], *, table: str, artifact_type: str, owner: str,
                    blob_field: str, category: str, disposition: str, track_ids: set[int],
                    current_paths: dict[int, str], root: Path, now: str,
                    warnings: list[str]) -> list[dict[str, Any]]:
    raw = row.get(blob_field)
    if not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        warnings.append(f"Skipped malformed {table}.{blob_field} row {row.get('id', '<unknown>')}.")
        return []
    found: list[dict[str, Any]] = []

    def scan(value: Any, location: str = "") -> None:
        if isinstance(value, list):
            for index, child in enumerate(value):
                scan(child, f"{location}[{index}]")
            return
        if not isinstance(value, dict):
            return

        track_id = value.get("track_id")
        if isinstance(track_id, int):
            identifier = f"{row.get('id', '<row>')}:{blob_field}:{location}.track_id"
            finding = _track_finding({"track_id": track_id}, artifact_type=artifact_type,
                                     owner=owner, identifier=identifier, field="track_id",
                                     category=category, disposition=disposition,
                                     track_ids=track_ids, now=now)
            if finding:
                found.append(finding)

        for field, child in value.items():
            child_location = f"{location}.{field}" if location else field
            if field in _PATH_KEYS and isinstance(child, str):
                identifier = f"{row.get('id', '<row>')}:{blob_field}:{child_location}"
                current = current_paths.get(track_id) if isinstance(track_id, int) else None
                if category == "C" and current and child != current:
                    stale, _ = _safe_path(child, root)
                    candidate, candidate_exists = _safe_path(current, root)
                    blockers = ["snapshot_scoped_reference"]
                    if stale is None:
                        blockers.append("reference_outside_selected_root")
                    if not candidate_exists or candidate is None:
                        blockers.append("canonical_track_path_missing_or_unsafe")
                    found.append(_finding(
                        artifact_type=artifact_type, owner=owner, identifier=identifier,
                        field=field, stale=stale or "<outside selected root>",
                        candidate=candidate if candidate_exists else None,
                        confidence="HIGH" if candidate_exists else None,
                        evidence="same_track_id_current_canonical_path",
                        category=category, disposition=disposition, now=now,
                        blockers=blockers,
                    ))
                else:
                    relative, exists = _safe_path(child, root)
                    if not exists:
                        stale = relative if relative is not None else "<outside selected root>"
                        blockers = ["snapshot_scoped_reference"] if category == "C" else ["immutable_historical_provenance"]
                        if relative is None:
                            blockers.append("reference_outside_selected_root")
                        found.append(_finding(
                            artifact_type=artifact_type, owner=owner, identifier=identifier,
                            field=field, stale=stale,
                            evidence="serialized_reference_path_check", category=category,
                            disposition=disposition, now=now, blockers=blockers,
                        ))
            elif field != "track_id":
                scan(child, child_location)

    scan(parsed)
    return found


def _queue_files(root: Path, warnings: list[str]) -> list[Path]:
    base = root / "data"
    if base.is_symlink():
        warnings.append("Skipped unsafe queue data directory.")
        return []
    if not base.is_dir():
        return []
    files: list[Path] = []
    for pattern in ("*queue*.json", "*queue*.jsonl"):
        for path in base.rglob(pattern):
            if len(files) >= _MAX_QUEUE_FILES:
                warnings.append(f"Queue discovery capped at {_MAX_QUEUE_FILES} files.")
                return sorted(set(files))
            if path.is_symlink() or not path.is_file():
                warnings.append(f"Skipped unsafe queue path {path.name}.")
                continue
            try:
                assert_path_under_root(path, root)
                if path.stat().st_size > _MAX_QUEUE_BYTES:
                    warnings.append(f"Skipped oversized queue file {path.name}.")
                    continue
            except (OSError, ValueError):
                warnings.append(f"Skipped unreadable queue path {path.name}.")
                continue
            files.append(path)
    return sorted(set(files))


def _scan_queue_files(root: Path, now: str, warnings: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    records_seen = 0
    for path in _queue_files(root, warnings):
        try:
            lines = path.read_text(encoding="utf-8").splitlines() if path.suffix == ".jsonl" else [path.read_text(encoding="utf-8")]
        except (OSError, UnicodeError):
            warnings.append(f"Skipped unreadable queue file {path.name}.")
            continue
        for index, text in enumerate(lines, start=1):
            if not text.strip():
                continue
            if records_seen >= _MAX_QUEUE_RECORDS:
                warnings.append(f"Queue entries capped at {_MAX_QUEUE_RECORDS} records.")
                return findings
            records_seen += 1
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                warnings.append(f"Skipped malformed queue record in {path.name} at {index}.")
                continue
            if not isinstance(record, (dict, list)):
                warnings.append(f"Skipped unsupported queue structure in {path.name} at {index}.")
                continue
            supported_reference_found = False
            for field, value, location in _json_references(record):
                if field == "track_id":
                    continue
                supported_reference_found = True
                relative, exists = _safe_path(value, root)
                if exists:
                    continue
                stale = relative if relative is not None else "<outside selected root>"
                blockers = ["queue_schema_not_authorized_for_mutation"]
                if relative is None:
                    blockers.append("reference_outside_selected_root")
                findings.append(_finding(artifact_type="queue_entry", owner="utils.path_reconciliation",
                    identifier=f"{path.relative_to(root)}:{index}:{location}", field=field, stale=stale,
                    evidence="bounded_queue_path_check", category="B", disposition="manual_review",
                    now=now, blockers=blockers))
            if not supported_reference_found:
                warnings.append(f"Skipped unsupported queue schema in {path.name} at {index}.")
    return findings


def get_reference_findings() -> dict[str, Any]:
    root = selected_library_root()
    now = _now()
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    db_path = library_db_path(root)
    track_ids: set[int] = set()
    current_paths: dict[int, str] = {}

    processed_db_ready = False
    if db_path.is_file():
        try:
            with _ro_connection(db_path) as conn:
                tables = _tables(conn)
                if "tracks" in tables:
                    tracks = list(_rows(conn, "tracks"))
                    track_ids = {int(row["id"]) for row in tracks}
                    current_paths = {int(row["id"]): str(row["filepath"]) for row in tracks if row.get("filepath")}
                    processed_db_ready = True
                else:
                    warnings.append("processed.db has no tracks table; database-backed reference findings are empty.")
                    tables = set()
                path_specs = (("cue_points", "cue_point", "db.py", "filepath", "B", "manual_review"),
                              ("set_playlist_tracks", "playlist_track", "set_builder.py", "filepath", "B", "manual_review"),
                              ("track_history", "historical_reference", "db.py", "filepath", "A", "ignore"),
                              ("track_history", "historical_reference", "db.py", "original_path", "A", "ignore"),
                              ("duplicate_groups", "historical_reference", "db.py", "original", "A", "ignore"),
                              ("duplicate_groups", "historical_reference", "db.py", "duplicate", "A", "ignore"),
                              ("reconciliation_ledger", "historical_reference", "reconciliation_apply_service.py", "old_path", "A", "ignore"),
                              ("reconciliation_ledger", "historical_reference", "reconciliation_apply_service.py", "new_path", "A", "ignore"))
                for table, typ, owner, field, category, disposition in path_specs:
                    if table in tables and field in _columns(conn, table):
                        for row in _rows(conn, table):
                            finding = _missing_path_finding(row, artifact_type=typ, owner=owner,
                                identifier=f"{table}:{row.get('id', row.get('ledger_id', '<row>'))}", field=field,
                                category=category, disposition=disposition, now=now)
                            if finding:
                                findings.append(finding)
                id_specs = (("metadata_repair_queue", "repair_queue_item", "metadata_repair_queue_service.py", "C", "regenerate"),
                            ("enrichment_review_decisions", "enrichment_review_snapshot", "enrichment_review_service.py", "C", "regenerate"),
                            ("beets_review_decisions", "beets_review_snapshot", "beets_review_service.py", "C", "regenerate"),
                            ("duplicate_review_decisions", "duplicate_review_snapshot", "duplicate_review_service.py", "C", "regenerate"),
                            ("quality_review_decisions", "quality_review_snapshot", "quality_review_service.py", "C", "regenerate"),
                            ("quality_review_findings", "quality_review_finding", "quality_findings_service.py", "C", "mark_unresolvable"),
                            ("track_reviews", "track_review", "reviews.py", "C", "mark_unresolvable"),
                            ("bpm_retry_pauses", "bpm_retry_pause", "bpm_retry_policy_service.py", "E", "ignore"),
                            ("track_fingerprints", "fingerprint_cache", "track_identity_service.py", "E", "ignore"))
                for table, typ, owner, category, disposition in id_specs:
                    if table in tables and "track_id" in _columns(conn, table):
                        for row in _rows(conn, table):
                            finding = _track_finding(row, artifact_type=typ, owner=owner,
                                identifier=f"{table}:{row.get('id', row.get('track_id', '<row>'))}", field="track_id",
                                category=category, disposition=disposition, track_ids=track_ids, now=now)
                            if finding:
                                findings.append(finding)
                            if table == "metadata_repair_queue":
                                path_finding = _cached_path_finding(row, artifact_type=typ, owner=owner,
                                    identifier=f"{table}:{row.get('id', '<row>')}", field="relative_path", category="C",
                                    disposition="regenerate", current_paths=current_paths, root=root, now=now)
                                if path_finding:
                                    findings.append(path_finding)
                if "field_provenance" in tables and {"track_id", "is_current"} <= _columns(conn, "field_provenance"):
                    for row in _rows(conn, "field_provenance"):
                        category = "B" if row.get("is_current") else "A"
                        finding = _track_finding(row, artifact_type="field_provenance", owner="field_provenance_service.py",
                            identifier=f"field_provenance:{row.get('id', '<row>')}", field="track_id", category=category,
                            disposition="manual_review" if category == "B" else "ignore", track_ids=track_ids, now=now)
                        if finding:
                            findings.append(finding)
                blob_specs = (("enrichment_review_snapshots", "enrichment_review_snapshot", "enrichment_review_service.py", "items_json"),
                              ("beets_review_snapshots", "beets_review_snapshot", "beets_review_service.py", "items_json"),
                              ("duplicate_review_snapshots", "duplicate_review_snapshot", "duplicate_review_service.py", "groups_json"),
                              ("quality_review_snapshots", "quality_review_snapshot", "quality_review_service.py", "items_json"),
                              ("genre_review_snapshots", "genre_review_snapshot", "genre_taxonomy_service.py", "items_json"))
                for table, typ, owner, field in blob_specs:
                    if table in tables and field in _columns(conn, table):
                        for row in _rows(conn, table):
                            findings.extend(_scan_json_blob(row, table=table, artifact_type=typ, owner=owner,
                                blob_field=field, category="C", disposition="regenerate", track_ids=track_ids,
                                current_paths=current_paths, root=root, now=now, warnings=warnings))
        except sqlite3.Error as exc:
            warnings.append(f"Could not read processed.db: {exc}")
    else:
        warnings.append("processed.db is not initialized; database-backed reference findings are empty.")

    crates_path = root / "logs" / "manual_crates.db"
    if processed_db_ready and crates_path.is_file():
        try:
            with _ro_connection(crates_path) as conn:
                if "manual_crate_tracks" in _tables(conn):
                    for row in _rows(conn, "manual_crate_tracks"):
                        finding = _track_finding(row, artifact_type="crate_track", owner="crate_db.py",
                            identifier=f"manual_crate_tracks:{row.get('crate_id')}:{row.get('track_id')}", field="track_id",
                            category="B", disposition="manual_review", track_ids=track_ids, now=now)
                        if finding:
                            findings.append(finding)
        except sqlite3.Error as exc:
            warnings.append(f"Could not read manual_crates.db: {exc}")

    jobs_path = backend_db.JOBS_DB_PATH
    if processed_db_ready and jobs_path.is_file():
        try:
            with _ro_connection(jobs_path) as conn:
                tables = _tables(conn)
                selected_library_id = library_identity(root)
                for table, typ, owner, category, disposition in (("bpm_anomalies", "bpm_anomaly", "backend.app.core.db", "C", "regenerate"),
                    ("waveform_track_state", "waveform_track_state", "waveform_state_service.py", "E", "ignore"),
                    ("waveform_jobs", "waveform_job", "waveform_job_service.py", "E", "ignore")):
                    if table in tables:
                        columns = _columns(conn, table)
                        if table in {"waveform_track_state", "waveform_jobs"} and "library_id" not in columns:
                            warnings.append(f"Skipped {table} without library_id isolation.")
                            continue
                        for row in _rows(conn, table):
                            if table in {"waveform_track_state", "waveform_jobs"} and row.get("library_id") != selected_library_id:
                                continue
                            if table == "waveform_jobs" and row.get("status") in {"queued", "processing"}:
                                continue
                            finding = _track_finding(row, artifact_type=typ, owner=owner,
                                identifier=f"{table}:{row.get('id', row.get('track_id', '<row>'))}", field="track_id",
                                category=category, disposition=disposition, track_ids=track_ids, now=now)
                            if finding:
                                findings.append(finding)
                            if table == "bpm_anomalies":
                                path_finding = _cached_path_finding(row, artifact_type=typ, owner=owner,
                                    identifier=f"{table}:{row.get('id', '<row>')}", field="filepath", category="C",
                                    disposition="regenerate", current_paths=current_paths, root=root, now=now)
                                if path_finding:
                                    findings.append(path_finding)
                if "tag_write_operations" in tables:
                    for row in _rows(conn, "tag_write_operations"):
                        for field in ("plan_json", "backup_manifest_json", "result_json"):
                            findings.extend(_scan_json_blob(row, table="tag_write_operations", artifact_type="tag_write_operation",
                                owner="tag_write_service.py", blob_field=field, category="A", disposition="ignore",
                                track_ids=track_ids, current_paths=current_paths, root=root, now=now,
                                warnings=warnings))
        except sqlite3.Error as exc:
            warnings.append(f"Could not read jobs.db: {exc}")
    elif not processed_db_ready and (crates_path.is_file() or jobs_path.is_file()):
        warnings.append("Skipped crate/jobs reference checks because processed.db is not initialized.")

    findings.extend(_scan_queue_files(root, now, warnings))
    findings.sort(key=lambda item: item["finding_id"])
    if len(findings) > _MAX_FINDINGS:
        warnings.append(f"Reference findings capped at {_MAX_FINDINGS}.")
        findings = findings[:_MAX_FINDINGS]
    summary = {"total": len(findings), "category_a": 0, "category_b": 0, "category_c": 0, "category_e": 0}
    for finding in findings:
        summary[f"category_{finding['classification'].lower()}"] += 1
    return {"summary": summary, "findings": findings, "warnings": warnings, "generated_at": now,
            "message": "Reference-artifact findings are read-only observations. No artifact, database, cache, or media file was changed."}
