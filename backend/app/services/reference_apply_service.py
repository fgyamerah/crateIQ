"""Read-only Stage 3 preview for reference-artifact reconciliation plans."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import reference_findings_service, reference_plan_service


class ReferenceApplyPreviewError(ValueError):
    """A bounded request or fail-closed preview error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)


def _plan_path(root: Path, value: str | None) -> Path:
    if not value:
        raise ReferenceApplyPreviewError("plan_path_required", "An exact saved reference plan is required.")
    try:
        path = reference_plan_service._safe_plan_path(value, root)
    except ValueError as exc:
        message = str(exc)
        code = "plan_not_found" if "unavailable" in message or "unsafe" in message else "plan_outside_selected_root"
        raise ReferenceApplyPreviewError(code, "Plan artifact must be a regular file under logs/path_reconcile in the selected root.") from exc
    return path


def _snapshot_bytes(path: Path) -> bytes:
    """Read one regular, non-symlinked artifact through the opened file."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("plan artifact is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _load(root: Path, value: str | None, requested_id: str) -> tuple[Path, dict[str, Any], str, dict[str, Any]]:
    path = _plan_path(root, value)
    if path.is_symlink():
        raise ReferenceApplyPreviewError("plan_outside_selected_root", "Plan artifact must not be a symlink.")
    try:
        snapshot = _snapshot_bytes(path)
        plan = json.loads(snapshot.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ReferenceApplyPreviewError("plan_not_found", "Selected reference plan artifact was not found.") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReferenceApplyPreviewError("plan_unreadable", "Selected reference plan is not valid JSON.") from exc
    if not isinstance(plan, dict) or plan.get("plan_kind") != "reference_artifact_reconciliation_plan" or plan.get("schema_version") != 1:
        raise ReferenceApplyPreviewError("unsupported_plan_schema", "Selected artifact is not a supported reference reconciliation plan.")
    if Path(str(plan.get("root") or "")).expanduser().resolve(strict=False) != root:
        raise ReferenceApplyPreviewError("plan_root_mismatch", "Selected plan root does not match the selected library root.")
    if plan.get("plan_id") != requested_id:
        raise ReferenceApplyPreviewError("plan_id_mismatch", "Requested plan ID does not match the saved plan.")
    try:
        validation = reference_plan_service.validate_plan_contents(plan, root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("plan_validation_failed", "Selected reference plan failed structural validation.") from exc
    return path, plan, hashlib.sha256(snapshot).hexdigest(), validation


def _finding_blockers(action: dict[str, Any]) -> list[str]:
    finding_ids = action.get("finding_ids")
    if not isinstance(finding_ids, list) or not finding_ids or not all(isinstance(item, str) and item for item in finding_ids):
        return ["source_finding_identity_missing"]
    try:
        report = reference_findings_service.get_reference_findings()
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        return ["source_finding_revalidation_failed"]
    current = {item.get("finding_id"): item for item in report.get("findings", []) if isinstance(item, dict)}
    blockers: list[str] = []
    expected = {
        "artifact_type": action.get("artifact_type"), "artifact_identifier": action.get("artifact_identifier"),
        "reference_field": action.get("reference_field"), "stale_value": action.get("old_path", action.get("old_track_id")),
        "candidate_replacement": action.get("new_path", action.get("new_track_id")), "evidence_source": action.get("evidence"),
        "classification": action.get("classification"), "confidence": action.get("confidence"),
    }
    for finding_id in finding_ids:
        finding = current.get(finding_id)
        if finding is None:
            blockers.append("source_finding_no_longer_current")
            continue
        if any(finding.get(key) != value for key, value in expected.items()):
            blockers.append("source_finding_drifted")
        if finding.get("blockers"):
            blockers.append("source_finding_has_blockers")
    return blockers


def _canonical_target(root: Path, target_value: Any, *, track_id: int | None = None) -> list[str]:
    try:
        target = assert_path_under_root(str(target_value or ""), root)
    except ValueError:
        return ["target_path_outside_selected_root"]
    if not target.is_file():
        return ["target_path_missing_or_not_canonical"]
    db_path = library_db_path(root)
    if not db_path.is_file():
        return ["target_path_missing_or_not_canonical"]
    try:
        with _ro(db_path) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone() is None:
                return ["target_path_missing_or_not_canonical"]
            if track_id is None:
                rows = conn.execute("SELECT id FROM tracks WHERE filepath=?", (str(target),)).fetchall()
            else:
                rows = conn.execute("SELECT id FROM tracks WHERE id=? AND filepath=?", (track_id, str(target))).fetchall()
    except sqlite3.Error:
        return ["canonical_target_check_failed"]
    if len(rows) != 1:
        return ["target_path_missing_or_not_canonical"]
    return []


def _path_artifact_blockers(root: Path, action: dict[str, Any]) -> list[str]:
    artifact = action.get("artifact_type")
    if artifact == "queue_entry":
        return ["queue_schema_not_authorized_for_mutation"]
    table = {"cue_point": "cue_points", "playlist_track": "set_playlist_tracks"}.get(artifact)
    if table is None:
        return ["invalid_artifact_for_path_update"]
    try:
        identifier = str(action.get("artifact_identifier"))
        prefix, row_text = identifier.rsplit(":", 1)
        if prefix != table or action.get("reference_field") != "filepath":
            return ["artifact_identity_incomplete"]
        row_id = int(row_text)
        expected = assert_path_under_root(str(action.get("old_path") or ""), root)
    except (TypeError, ValueError):
        return ["artifact_identity_incomplete"]
    try:
        with _ro(library_db_path(root)) as conn:
            row = conn.execute(f"SELECT filepath FROM {table} WHERE id=?", (row_id,)).fetchone()
    except sqlite3.Error:
        return ["artifact_precondition_check_failed"]
    if row is None:
        return ["artifact_row_missing"]
    try:
        actual = assert_path_under_root(str(row[0] or ""), root)
    except ValueError:
        return ["artifact_reference_outside_selected_root"]
    return [] if actual == expected else ["artifact_expected_before_mismatch"]


def _provenance_blockers(root: Path, action: dict[str, Any]) -> list[str]:
    try:
        identifier = str(action.get("artifact_identifier"))
        prefix, row_text = identifier.rsplit(":", 1)
        if prefix != "field_provenance" or action.get("reference_field") != "track_id":
            return ["field_provenance_identity_incomplete"]
        row_id = int(row_text)
        old_id, new_id = int(action["old_track_id"]), int(action["new_track_id"])
        field = action["field_name"]
    except (KeyError, TypeError, ValueError):
        return ["field_provenance_identity_incomplete"]
    if not isinstance(field, str) or not field:
        return ["field_provenance_identity_incomplete"]
    try:
        with _ro(library_db_path(root)) as conn:
            row = conn.execute("SELECT track_id, field_name, is_current FROM field_provenance WHERE id=?", (row_id,)).fetchone()
            if row is None:
                return ["artifact_row_missing"]
            if row[0] != old_id or row[1] != field or row[2] != 1:
                return ["artifact_expected_before_mismatch"]
            if conn.execute("SELECT 1 FROM field_provenance WHERE track_id=? AND field_name=? AND is_current=1 LIMIT 1", (new_id, field)).fetchone():
                return ["target_current_provenance_collision"]
            target = conn.execute("SELECT filepath FROM tracks WHERE id=?", (new_id,)).fetchall()
    except sqlite3.Error:
        return ["artifact_precondition_check_failed"]
    if len(target) != 1:
        return ["target_track_missing_or_unsafe"]
    return _canonical_target(root, target[0][0], track_id=new_id)


def _crate_blockers(root: Path, action: dict[str, Any]) -> list[str]:
    try:
        _table, crate_id, old_text = str(action.get("artifact_identifier")).split(":", 2)
        if _table != "manual_crate_tracks" or action.get("reference_field") != "track_id":
            return ["crate_row_identity_incomplete"]
        old_id, new_id = int(old_text), int(action["new_track_id"])
    except (KeyError, TypeError, ValueError):
        return ["crate_row_identity_incomplete"]
    crates = root / "logs" / "manual_crates.db"
    if not crates.is_file():
        return ["artifact_row_missing"]
    try:
        with _ro(crates) as conn:
            rows = conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, old_id)).fetchall()
    except sqlite3.Error:
        return ["crate_row_identity_incomplete"]
    if len(rows) != 1:
        if not rows:
            return ["artifact_row_missing"]
        return ["crate_row_identity_ambiguous"]
    db_path = library_db_path(root)
    try:
        with _ro(db_path) as conn:
            target = conn.execute("SELECT filepath FROM tracks WHERE id=?", (new_id,)).fetchall()
    except sqlite3.Error:
        return ["canonical_target_check_failed"]
    if len(target) != 1:
        return ["target_track_missing_or_unsafe"]
    return _canonical_target(root, target[0][0], track_id=new_id)


def _preview_action(root: Path, action: dict[str, Any], validation: dict[str, Any] | None) -> dict[str, Any]:
    blockers = list(action.get("blockers") or [])
    if validation is None or validation.get("status") != "valid":
        blockers.extend((validation or {}).get("issues") or [str((validation or {}).get("reason") or "plan_action_invalid")])
    kind, artifact = action.get("action_type"), action.get("artifact_type")
    if kind not in {"update_path_reference", "rehome_track_id"} or action.get("executable") is not True:
        blockers.append("not_executable")
    if action.get("classification") in {"A", "C", "E"}:
        blockers.append("non_mutation_classification")
    if artifact == "queue_entry":
        blockers.append("queue_schema_not_authorized_for_mutation")
    if not blockers and action.get("classification") == "B":
        blockers.extend(_finding_blockers(action))
        if kind == "update_path_reference":
            blockers.extend(_path_artifact_blockers(root, action))
            blockers.extend(_canonical_target(root, action.get("new_path")))
        elif artifact == "field_provenance":
            blockers.extend(_provenance_blockers(root, action))
        elif artifact == "crate_track":
            blockers.extend(_crate_blockers(root, action))
        else:
            blockers.append("invalid_artifact_for_track_rehome")
    blockers = sorted(set(str(item) for item in blockers if item))
    return {"action_id": action.get("action_id"), "action_type": kind, "artifact_type": artifact,
            "eligible": not blockers, "blockers": blockers}


def preview(plan_path: str | None, plan_id: str, action_ids: list[str]) -> dict[str, Any]:
    if not action_ids:
        raise ReferenceApplyPreviewError("action_selection_required", "Select one reviewed plan action.")
    if len(set(action_ids)) != len(action_ids):
        raise ReferenceApplyPreviewError("duplicate_action_id", "Each reviewed action may be selected only once.")
    if len(action_ids) != 1:
        raise ReferenceApplyPreviewError("single_action_required", "Preview accepts exactly one reviewed action per request.")
    root = selected_library_root().resolve(strict=False)
    path, plan, snapshot_hash, validation = _load(root, plan_path, plan_id)
    actions = plan["planned_actions"]
    matches = [item for item in actions if isinstance(item, dict) and item.get("action_id") == action_ids[0]]
    if not matches:
        return {"plan_artifact": path.name, "plan_id": plan_id, "plan_sha256": snapshot_hash, "previewed_at": _now(), "read_only": True,
                "actions": [{"action_id": action_ids[0], "action_type": None, "artifact_type": None, "eligible": False, "blockers": ["action_id_not_in_plan"]}],
                "message": "Preview/revalidation only; no mutation has occurred."}
    records = {item.get("action_id"): item for item in validation.get("validation_records", []) if isinstance(item, dict)}
    result = _preview_action(root, matches[0], records.get(action_ids[0]))
    return {"plan_artifact": path.name, "plan_id": plan_id, "plan_sha256": snapshot_hash, "previewed_at": _now(), "read_only": True,
            "actions": [result], "message": "Preview/revalidation only; no mutation has occurred."}
