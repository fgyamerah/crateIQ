"""Safe, read-only orphan / stale-path / quarantine findings.

Reuses the existing ``pipeline.py`` path-audit engine (``_path_audit_report``)
for detection; this module only reshapes that report into a bounded,
relative-path-only finding contract and adds a read-only quarantine listing.
It performs no filesystem mutation, DB write, move, rename, or delete.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pipeline

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_QUARANTINE_RELATIVE = Path(".BIN") / "QUARANTINE"
_UNTRACKED_LIMIT = 500


def _finding_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}"


def _relative(path_value: Any, root: Path) -> str | None:
    if not path_value:
        return None
    try:
        resolved = assert_path_under_root(str(path_value), root)
    except ValueError:
        return None
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return None


def _missing_file_finding(row: dict[str, Any], root: Path, has_candidate: bool) -> dict[str, Any]:
    relative_path = _relative(row.get("filepath"), root)
    return {
        "finding_id": _finding_id("missing", str(row.get("filepath") or "")),
        "finding_type": "indexed_missing_file",
        "summary": "The local index has a track row pointing at a file that no longer exists at that path.",
        "db_side": {
            "track_id": row.get("id"), "relative_path": relative_path,
            "filename": row.get("filename"), "status": row.get("status"), "size_bytes": row.get("filesize_bytes"),
        },
        "filesystem_side": None,
        "evidence": {"has_path_candidate": has_candidate},
    }


def _untracked_file_finding(path: Path, root: Path) -> dict[str, Any] | None:
    relative_path = _relative(path, root)
    if relative_path is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return {
        "finding_id": _finding_id("untracked", relative_path),
        "finding_type": "untracked_file",
        "summary": "A readable audio file exists on disk under the selected library but is not indexed in the local database.",
        "db_side": None,
        "filesystem_side": {"relative_path": relative_path, "filename": path.name, "size_bytes": size},
        "evidence": {},
    }


def _stale_path_finding(row: dict[str, Any], root: Path) -> dict[str, Any]:
    old_relative = _relative(row.get("old_path"), root)
    new_relative = _relative(row.get("replacement_path"), root)
    return {
        "finding_id": _finding_id("stale", str(row.get("old_path") or ""), str(row.get("stage") or "")),
        "finding_type": "stale_path",
        "summary": "A processed-state DB row still references an old path that has been superseded by a current, existing path.",
        "db_side": {"relative_path": old_relative, "stage": row.get("stage")},
        "filesystem_side": {"relative_path": new_relative},
        "evidence": {"reason": row.get("reason")},
    }


def _path_candidate_finding(entry: dict[str, Any], root: Path, match_kind: str) -> dict[str, Any] | None:
    old_path = entry.get("old_path")
    if not old_path:
        db_row = entry.get("db_row")
        old_path = db_row.get("filepath") if isinstance(db_row, dict) else None
    new_path = entry.get("new_path")
    old_relative = _relative(old_path, root)
    new_relative = _relative(new_path, root)
    if old_relative is None and new_relative is None:
        return None
    return {
        "finding_id": _finding_id("candidate", match_kind, str(old_path or ""), str(new_path or "")),
        "finding_type": "path_candidate",
        "summary": "An untracked file on disk may be the renamed or relocated version of a missing indexed track.",
        "db_side": {"relative_path": old_relative},
        "filesystem_side": {"relative_path": new_relative},
        "evidence": {
            "match_kind": match_kind,
            "similarity": entry.get("similarity") or entry.get("token_overlap"),
            "size_diff_pct": entry.get("size_diff_pct"),
            "reason": entry.get("reason"),
        },
    }


def _empty_response(message: str) -> dict[str, Any]:
    return {
        "summary": {"indexed_missing_file": 0, "untracked_file": 0, "stale_path": 0, "path_candidate": 0},
        "findings": [], "warnings": [], "generated_at": None, "message": message,
    }


def get_findings() -> dict[str, Any]:
    root = selected_library_root()
    db_path = library_db_path(root)
    if not db_path.is_file():
        return _empty_response("Initialize the local library index before requesting reconciliation findings.")

    report = pipeline._path_audit_report(root, db_path, include_orphan_candidates=False)
    warnings: list[str] = []
    if report.get("db_error"):
        warnings.append(str(report["db_error"]))

    candidate_old_paths = {
        str(entry.get("old_path") or (entry.get("db_row") or {}).get("filepath"))
        for entry in [*report.get("possible_renames", []), *report.get("relocation_candidates", [])]
    }

    findings: list[dict[str, Any]] = []
    for row in report.get("missing_files", []):
        findings.append(_missing_file_finding(row, root, str(row.get("filepath")) in candidate_old_paths))

    untracked_paths = [Path(p) for p in report.get("untracked_files", [])][:_UNTRACKED_LIMIT]
    if len(report.get("untracked_files", [])) > _UNTRACKED_LIMIT:
        warnings.append(f"Untracked-file findings are capped at {_UNTRACKED_LIMIT}; rescope the library to see the rest.")
    for path in untracked_paths:
        finding = _untracked_file_finding(path, root)
        if finding is not None:
            findings.append(finding)

    for row in report.get("stale_processed_state_rows", []):
        findings.append(_stale_path_finding(row, root))

    for entry in report.get("possible_renames", []):
        finding = _path_candidate_finding(entry, root, "possible_rename")
        if finding is not None:
            findings.append(finding)
    for entry in report.get("relocation_candidates", []):
        finding = _path_candidate_finding(entry, root, "relocation")
        if finding is not None:
            findings.append(finding)

    summary = {"indexed_missing_file": 0, "untracked_file": 0, "stale_path": 0, "path_candidate": 0}
    for finding in findings:
        summary[finding["finding_type"]] += 1

    return {
        "summary": summary, "findings": findings, "warnings": warnings,
        "generated_at": report.get("generated_at"),
        "message": "Findings are read-only evidence. No file, tag, or database action exists here.",
    }


def get_quarantine_listing() -> dict[str, Any]:
    root = selected_library_root()
    quarantine_dir = root / _QUARANTINE_RELATIVE
    try:
        resolved = assert_path_under_root(quarantine_dir, root)
    except ValueError:
        return {"supported": True, "items": [], "message": "The quarantine directory could not be safely resolved under the selected library root."}
    if not resolved.is_dir():
        return {"supported": True, "items": [], "message": "No quarantine directory exists yet under this library root."}

    items: list[dict[str, Any]] = []
    for path in sorted(resolved.rglob("*")):
        if not path.is_file():
            continue
        try:
            safe_path = assert_path_under_root(path, root)
        except ValueError:
            continue
        try:
            size = safe_path.stat().st_size
        except OSError:
            size = None
        items.append({
            "relative_path": str(safe_path.relative_to(root)),
            "filename": safe_path.name,
            "size_bytes": size,
            "original_relative_path": None,
            "reason": None,
            "operation_id": None,
            "quarantined_at": None,
            "restore_supported": False,
        })
    return {
        "supported": True,
        "items": items,
        "message": (
            "Quarantine listing is read-only. Original location, reason, operation id, and timestamp are not "
            "currently persisted for legacy quarantine actions, so restore is not offered."
        ),
    }
