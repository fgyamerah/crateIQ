"""Reference-artifact reconciliation preview plus the narrow Stage 4A/B writer.

The writer is intentionally limited to ``cue_points.filepath`` and
``set_playlist_tracks.filepath``.  It never touches a media file, tags,
tracks, or cue content.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import reference_findings_service, reference_plan_service


_REFERENCE_LEDGER_TABLE = "reference_artifact_ledger"
_WRITABLE_ARTIFACTS = {
    "cue_point": "cue_points",
    "playlist_track": "set_playlist_tracks",
}


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


def _cue_target_collision(conn: sqlite3.Connection, root: Path, target: str, row_id: int, cue_type: Any) -> bool:
    """Check the cue uniqueness rule using canonical, not storage-form, paths."""
    for candidate in conn.execute(
        "SELECT id, filepath FROM cue_points WHERE cue_type=? AND id != ?",
        (cue_type, row_id),
    ):
        try:
            candidate_path = str(assert_path_under_root(str(candidate["filepath"] or ""), root))
        except ValueError:
            # An unrelated unsafe legacy row cannot authorize this write and
            # cannot be treated as the same root-contained target either.
            continue
        if candidate_path == target:
            return True
    return False


def _path_artifact_blockers(root: Path, action: dict[str, Any]) -> list[str]:
    artifact = action.get("artifact_type")
    if artifact == "queue_entry":
        return ["queue_schema_not_authorized_for_mutation"]
    table = _WRITABLE_ARTIFACTS.get(artifact)
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
            conn.row_factory = sqlite3.Row
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
            if row is not None and table == "cue_points" and "cue_type" in row.keys():
                target = str(assert_path_under_root(str(action.get("new_path") or ""), root))
                if _cue_target_collision(conn, root, target, row_id, row["cue_type"]):
                    return ["artifact_target_collision"]
    except sqlite3.Error:
        return ["artifact_precondition_check_failed"]
    if row is None:
        return ["artifact_row_missing"]
    try:
        actual = assert_path_under_root(str(row["filepath"] or ""), root)
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


def _require_table(conn: sqlite3.Connection, table: str) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        raise ReferenceApplyPreviewError("required_table_missing", f"Required table is missing: {table}.")


def _reference_ledger_ddl(conn: sqlite3.Connection) -> None:
    """Add the isolated ledger lazily for existing processed.db files.

    This runs only after a confirmed action has passed Stage-3 eligibility and
    inside its writer transaction; it does not alter preview/read-only paths.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS reference_artifact_ledger (
            ledger_id TEXT PRIMARY KEY, parent_ledger_id TEXT, created_at TEXT NOT NULL,
            root TEXT NOT NULL, plan_path TEXT, plan_id TEXT, plan_sha256 TEXT,
            action_id TEXT, artifact_type TEXT NOT NULL, artifact_identifier TEXT NOT NULL,
            table_name TEXT NOT NULL, row_id INTEGER NOT NULL, reference_field TEXT NOT NULL,
            old_path TEXT NOT NULL, new_path TEXT NOT NULL, before_values_json TEXT NOT NULL,
            after_values_json TEXT NOT NULL, backup_path TEXT NOT NULL,
            backup_sha256 TEXT NOT NULL, status TEXT NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reference_artifact_ledger_created_at ON reference_artifact_ledger(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reference_artifact_ledger_parent ON reference_artifact_ledger(parent_ledger_id)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_backup_path(root: Path, ledger_id: str) -> Path:
    directory = root / "logs" / "reference_artifact_backups"
    try:
        # Validate the existing ancestry before mkdir follows it.  In
        # particular, ``logs`` must not be a symlink escaping the selected
        # root merely because the backup directory has not been created yet.
        directory = assert_path_under_root(directory, root)
        directory.mkdir(parents=True, exist_ok=True)
        directory = assert_path_under_root(directory, root)
        path = assert_path_under_root(directory / f"{ledger_id}_processed.db", root)
    except (OSError, ValueError) as exc:
        raise ReferenceApplyPreviewError("backup_path_outside_selected_root", "Reference-artifact backup directory is not safely contained by the selected root.") from exc
    if path.parent != directory or path.exists():
        raise ReferenceApplyPreviewError("backup_collision", "Refusing to overwrite an existing reference-artifact backup.")
    return path


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    with _ro(source_path) as source:
        with sqlite3.connect(destination_path) as destination:
            source.backup(destination)


def _verify_backup(backup_path: Path, table: str, expected: dict[str, Any]) -> str:
    if not backup_path.is_file():
        raise ReferenceApplyPreviewError("backup_missing", "Reference-artifact SQLite backup was not created.")
    try:
        with _ro(backup_path) as conn:
            conn.row_factory = sqlite3.Row
            check = conn.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise ReferenceApplyPreviewError("backup_verification_failed", "Reference-artifact SQLite backup integrity verification failed.")
            _require_table(conn, table)
            columns = list(expected)
            actual = conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id=?", (expected["id"],)).fetchone()
            if actual is None or dict(actual) != expected:
                raise ReferenceApplyPreviewError("backup_verification_failed", "Reference-artifact backup does not contain the protected row pre-state.")
    except ReferenceApplyPreviewError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReferenceApplyPreviewError("backup_verification_failed", "Reference-artifact backup could not be read and verified.") from exc
    return _sha256(backup_path)


def _backup_locked(root: Path, db_path: Path, ledger_id: str, table: str, expected: dict[str, Any]) -> tuple[Path, str]:
    path = _safe_backup_path(root, ledger_id)
    try:
        _sqlite_backup(db_path, path)
        return path, _verify_backup(path, table, expected)
    except (OSError, sqlite3.Error) as exc:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReferenceApplyPreviewError("backup_creation_failed", "Reference-artifact SQLite backup could not be created.") from exc
    except Exception:
        # A backup is useful only when it is verified and ledgered.  The
        # caller cannot safely refer to this one, so do not leave an
        # unledgered full copy behind on creation or verification failure.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _action_identity(action: dict[str, Any], root: Path) -> tuple[str, str, int, str, str]:
    artifact = action.get("artifact_type")
    table = _WRITABLE_ARTIFACTS.get(artifact)
    if table is None or action.get("action_type") != "update_path_reference" or action.get("reference_field") != "filepath":
        raise ReferenceApplyPreviewError("unsupported_reference_action", "Only reviewed cue-point or set-playlist filepath updates are supported.")
    try:
        prefix, row_text = str(action.get("artifact_identifier")).rsplit(":", 1)
        row_id = int(row_text)
        old = str(assert_path_under_root(str(action.get("old_path") or ""), root))
        new = str(assert_path_under_root(str(action.get("new_path") or ""), root))
    except (TypeError, ValueError) as exc:
        raise ReferenceApplyPreviewError("artifact_identity_incomplete", "Reviewed reference action has incomplete or unsafe artifact identity.") from exc
    if prefix != table:
        raise ReferenceApplyPreviewError("artifact_identity_incomplete", "Reviewed artifact identity does not match its supported table.")
    return str(artifact), table, row_id, old, new


def _current_canonical_target(conn: sqlite3.Connection, root: Path, raw_path: str) -> str:
    try:
        target = assert_path_under_root(raw_path, root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("target_path_outside_selected_root", "Target path is outside the selected root.") from exc
    if not target.is_file():
        raise ReferenceApplyPreviewError("target_path_missing_at_apply", "Target path is no longer a regular file; no reference was changed.")
    _require_table(conn, "tracks")
    if len(conn.execute("SELECT id FROM tracks WHERE filepath=?", (str(target),)).fetchall()) != 1:
        raise ReferenceApplyPreviewError("target_path_missing_or_not_canonical", "Target path is no longer the unique canonical track path.")
    return str(target)


def _read_protected_row(conn: sqlite3.Connection, root: Path, action: dict[str, Any]) -> tuple[str, str, int, str, str, dict[str, Any]]:
    artifact, table, row_id, old, new = _action_identity(action, root)
    _require_table(conn, table)
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise ReferenceApplyPreviewError("artifact_row_missing", "Reviewed reference row no longer exists.")
    before = dict(row)
    if "filepath" not in before:
        raise ReferenceApplyPreviewError("artifact_identity_incomplete", "Reviewed reference row has no filepath column.")
    try:
        actual = str(assert_path_under_root(str(before["filepath"] or ""), root))
    except ValueError as exc:
        raise ReferenceApplyPreviewError("artifact_reference_outside_selected_root", "Current reference path is outside the selected root.") from exc
    if actual != old:
        raise ReferenceApplyPreviewError("artifact_expected_before_mismatch", "Reviewed reference row no longer matches the planned old path.")
    if table == "cue_points" and "cue_type" in before:
        if _cue_target_collision(conn, root, new, row_id, before["cue_type"]):
            raise ReferenceApplyPreviewError("artifact_target_collision", "Target already has a cue point with the same cue type.")
    return artifact, table, row_id, old, new, before


def _insert_ledger(conn: sqlite3.Connection, *, ledger_id: str, parent_ledger_id: str | None, root: Path,
                   plan_path: str | None, plan_id: str | None, plan_sha256: str | None, action_id: str | None,
                   artifact: str, table: str, row_id: int, old: str, new: str, before: dict[str, Any],
                   after: dict[str, Any], backup_path: Path, backup_hash: str, status: str) -> None:
    _require_table(conn, _REFERENCE_LEDGER_TABLE)
    conn.execute(
        "INSERT INTO reference_artifact_ledger (ledger_id, parent_ledger_id, created_at, root, plan_path, plan_id, plan_sha256, action_id, artifact_type, artifact_identifier, table_name, row_id, reference_field, old_path, new_path, before_values_json, after_values_json, backup_path, backup_sha256, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'filepath', ?, ?, ?, ?, ?, ?, ?)",
        (ledger_id, parent_ledger_id, _now(), str(root), plan_path, plan_id, plan_sha256, action_id,
         artifact, f"{table}:{row_id}", table, row_id, old, new,
         json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True), str(backup_path), backup_hash, status),
    )


def _stage3_eligible(root: Path, path: Path, plan: dict[str, Any], validation: dict[str, Any], action_id: str) -> dict[str, Any]:
    matches = [item for item in plan["planned_actions"] if isinstance(item, dict) and item.get("action_id") == action_id]
    if not matches:
        return {"action_id": action_id, "eligible": False, "blockers": ["action_id_not_in_plan"]}
    records = {item.get("action_id"): item for item in validation.get("validation_records", []) if isinstance(item, dict)}
    result = _preview_action(root, matches[0], records.get(action_id))
    if result["eligible"]:
        try:
            artifact, _table, _row_id, _old, _new = _action_identity(matches[0], root)
            if artifact not in _WRITABLE_ARTIFACTS:
                result = {**result, "eligible": False, "blockers": ["unsupported_reference_action"]}
        except ReferenceApplyPreviewError as exc:
            result = {**result, "eligible": False, "blockers": [exc.code]}
    return result


def apply(plan_path: str | None, plan_id: str, plan_sha256: str, action_ids: list[str], *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ReferenceApplyPreviewError("confirmation_required", "Set confirm=true to apply one reviewed reference-artifact action.")
    if not action_ids:
        raise ReferenceApplyPreviewError("action_selection_required", "Select one reviewed plan action.")
    if len(set(action_ids)) != len(action_ids):
        raise ReferenceApplyPreviewError("duplicate_action_id", "Each reviewed action may be selected only once.")
    if len(action_ids) != 1:
        raise ReferenceApplyPreviewError("single_action_required", "Apply accepts exactly one reviewed action per request.")
    root = selected_library_root().resolve(strict=False)
    path, plan, snapshot_hash, validation = _load(root, plan_path, plan_id)
    if plan_sha256 != snapshot_hash:
        raise ReferenceApplyPreviewError("stale_plan_sha256", "Plan bytes changed or do not match the reviewed Stage-3 preview hash.")
    eligibility = _stage3_eligible(root, path, plan, validation, action_ids[0])
    if not eligibility["eligible"]:
        raise ReferenceApplyPreviewError("action_not_eligible", "Selected action is blocked: " + ", ".join(eligibility["blockers"]))
    action = next(item for item in plan["planned_actions"] if isinstance(item, dict) and item.get("action_id") == action_ids[0])
    db_path = library_db_path(root)
    if not db_path.is_file():
        raise ReferenceApplyPreviewError("library_not_initialized", "Configured library is not initialized.")
    ledger_id = f"ref-{uuid.uuid4().hex}"
    backup_path: Path | None = None
    backup_hash = ""
    committed = False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            artifact, table, row_id, old, planned_new, before = _read_protected_row(conn, root, action)
            new = _current_canonical_target(conn, root, planned_new)
            if new != planned_new:
                raise ReferenceApplyPreviewError("target_path_changed_at_apply", "Target canonical path changed before reference mutation.")
            backup_path, backup_hash = _backup_locked(root, db_path, ledger_id, table, before)
            # File state is outside SQLite's writer lock.  Recheck it at the last
            # possible point so a target removed while the backup was created can
            # never receive a newly written reference.
            current_new = _current_canonical_target(conn, root, planned_new)
            if current_new != new:
                raise ReferenceApplyPreviewError("target_path_changed_at_apply", "Target canonical path changed before reference mutation.")
            # The backup deliberately precedes even the additive ledger DDL so
            # it is a copy of the complete protected pre-change database
            # state, rather than a copy made after any writer-side change.
            _reference_ledger_ddl(conn)
            cursor = conn.execute(f"UPDATE {table} SET filepath=? WHERE id=? AND filepath=?", (new, row_id, before["filepath"]))
            if cursor.rowcount != 1:
                raise ReferenceApplyPreviewError("reference_update_count_mismatch", "Reference row changed before it could be updated.")
            actual = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
            after = {**before, "filepath": new}
            if actual is None or dict(actual) != after:
                raise ReferenceApplyPreviewError("reference_postcondition_failed", "Reference row postcondition verification failed.")
            _insert_ledger(conn, ledger_id=ledger_id, parent_ledger_id=None, root=root, plan_path=str(path), plan_id=plan_id,
                           plan_sha256=snapshot_hash, action_id=action_ids[0], artifact=artifact, table=table, row_id=row_id,
                           old=old, new=new, before=before, after=after, backup_path=backup_path, backup_hash=backup_hash, status="applied")
            conn.commit()
            committed = True
    except Exception:
        if backup_path is not None and not committed:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {"plan_path": str(path), "plan_id": plan_id, "plan_sha256": snapshot_hash, "results": [{"action_id": action_ids[0], "ledger_id": ledger_id, "status": "applied", "backup_path": str(backup_path), "backup_sha256": backup_hash, "verification_status": "verified"}], "message": "One reference filepath was updated and verified. Music files, tags, BPM, key, and cue content were not changed."}


def _json_row(value: str, *, code: str) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReferenceApplyPreviewError(code, "Reference ledger row state is unreadable.") from exc
    if not isinstance(result, dict):
        raise ReferenceApplyPreviewError(code, "Reference ledger row state is malformed.")
    return result


def _verified_original_backup(root: Path, row: sqlite3.Row, table: str, before: dict[str, Any]) -> None:
    try:
        path = assert_path_under_root(str(row["backup_path"] or ""), root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup path is outside the selected root.") from exc
    expected_directory = root / "logs" / "reference_artifact_backups"
    try:
        expected_directory = assert_path_under_root(expected_directory, root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup directory is outside the selected root.") from exc
    if path.parent != expected_directory or path.name != f"{row['ledger_id']}_processed.db":
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup path does not match its ledger identity.")
    if not path.is_file():
        raise ReferenceApplyPreviewError("rollback_backup_missing", "Reference rollback backup is no longer available.")
    try:
        backup_hash = _sha256(path)
    except OSError as exc:
        raise ReferenceApplyPreviewError("rollback_backup_unreadable", "Reference rollback backup could not be read.") from exc
    if backup_hash != row["backup_sha256"]:
        raise ReferenceApplyPreviewError("rollback_backup_hash_mismatch", "Reference rollback backup hash does not match its ledger provenance.")
    _verify_backup(path, table, before)


def rollback(ledger_id: str, *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ReferenceApplyPreviewError("confirmation_required", "Set confirm=true to rollback one applied reference-artifact action.")
    root = selected_library_root().resolve(strict=False)
    db_path = library_db_path(root)
    if not db_path.is_file():
        raise ReferenceApplyPreviewError("library_not_initialized", "Configured library is not initialized.")
    rollback_id = f"ref-rollback-{uuid.uuid4().hex}"
    rollback_backup: Path | None = None
    rollback_hash = ""
    committed = False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            # A rollback must only operate on a pre-existing successful apply;
            # do not create a ledger table for an invalid rollback request.
            _require_table(conn, _REFERENCE_LEDGER_TABLE)
            original = conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (ledger_id,)).fetchone()
            if original is None:
                raise ReferenceApplyPreviewError("ledger_not_found", "Reference-artifact ledger entry was not found.")
            if original["root"] != str(root) or original["status"] != "applied" or original["parent_ledger_id"] is not None:
                raise ReferenceApplyPreviewError("ledger_not_rollback_eligible", "Ledger entry is not an applied reference action for the selected root.")
            if conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='rolled_back' LIMIT 1", (ledger_id,)).fetchone():
                raise ReferenceApplyPreviewError("rollback_already_completed", "This reference-artifact ledger entry has already been rolled back.")
            table = str(original["table_name"] or "")
            artifact = str(original["artifact_type"] or "")
            if _WRITABLE_ARTIFACTS.get(artifact) != table or original["reference_field"] != "filepath":
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Ledger entry is not a supported reference filepath apply.")
            before = _json_row(original["before_values_json"], code="ledger_rollback_provenance_missing")
            after = _json_row(original["after_values_json"], code="ledger_rollback_provenance_missing")
            if (
                set(before) != set(after)
                or before.get("id") != original["row_id"]
                or after.get("id") != original["row_id"]
                or any(before[key] != after[key] for key in before if key != "filepath")
            ):
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Ledger entry does not record a filepath-only row change.")
            try:
                old = str(assert_path_under_root(str(original["old_path"]), root))
                new = str(assert_path_under_root(str(original["new_path"]), root))
            except ValueError as exc:
                raise ReferenceApplyPreviewError("rollback_path_outside_selected_root", "Rollback refuses a recorded path outside the selected root.") from exc
            try:
                before_path = str(assert_path_under_root(str(before.get("filepath") or ""), root))
            except ValueError as exc:
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Ledger pre-state filepath is outside the selected root.") from exc
            if before_path != old or after.get("filepath") != new:
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Ledger paths do not match its protected row state.")
            _verified_original_backup(root, original, table, before)
            _require_table(conn, table)
            current = conn.execute(f"SELECT * FROM {table} WHERE id=?", (original["row_id"],)).fetchone()
            if current is None or dict(current) != after:
                raise ReferenceApplyPreviewError("rollback_state_drift", "Current reference row no longer matches the recorded applied state.")
            if table == "cue_points" and "cue_type" in before and _cue_target_collision(conn, root, before_path, original["row_id"], before["cue_type"]):
                raise ReferenceApplyPreviewError("rollback_target_collision", "Rollback target already has a cue point with the same cue type.")
            rollback_backup, rollback_hash = _backup_locked(root, db_path, rollback_id, table, after)
            cursor = conn.execute(f"UPDATE {table} SET filepath=? WHERE id=? AND filepath=?", (before["filepath"], original["row_id"], after["filepath"]))
            if cursor.rowcount != 1:
                raise ReferenceApplyPreviewError("rollback_update_count_mismatch", "Reference row changed before it could be restored.")
            restored = conn.execute(f"SELECT * FROM {table} WHERE id=?", (original["row_id"],)).fetchone()
            if restored is None or dict(restored) != before:
                raise ReferenceApplyPreviewError("rollback_postcondition_failed", "Reference rollback postcondition verification failed.")
            _insert_ledger(conn, ledger_id=rollback_id, parent_ledger_id=ledger_id, root=root, plan_path=original["plan_path"],
                           plan_id=original["plan_id"], plan_sha256=original["plan_sha256"], action_id=original["action_id"],
                           artifact=artifact, table=table, row_id=original["row_id"], old=new, new=old, before=after, after=before,
                           backup_path=rollback_backup, backup_hash=rollback_hash, status="rolled_back")
            conn.commit()
            committed = True
    except Exception:
        if rollback_backup is not None and not committed:
            try:
                rollback_backup.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {"ledger_id": rollback_id, "rollback_of_ledger_id": ledger_id, "status": "rolled_back", "backup_path": str(rollback_backup), "backup_sha256": rollback_hash, "verification_status": "verified"}


def _ledger_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_ledger(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    root = selected_library_root().resolve(strict=False)
    db_path = library_db_path(root)
    if not db_path.is_file():
        return []
    try:
        with _ro(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_REFERENCE_LEDGER_TABLE,)).fetchone() is None:
                return []
            rows = conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} ORDER BY created_at DESC, ledger_id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            return [_ledger_dict(row) for row in rows]
    except sqlite3.Error as exc:
        raise ReferenceApplyPreviewError("reference_ledger_unreadable", "Reference-artifact ledger could not be read.") from exc


def get_ledger(ledger_id: str) -> dict[str, Any] | None:
    root = selected_library_root().resolve(strict=False)
    db_path = library_db_path(root)
    if not db_path.is_file():
        return None
    try:
        with _ro(db_path) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_REFERENCE_LEDGER_TABLE,)).fetchone() is None:
                return None
            row = conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (ledger_id,)).fetchone()
            return _ledger_dict(row) if row is not None else None
    except sqlite3.Error as exc:
        raise ReferenceApplyPreviewError("reference_ledger_unreadable", "Reference-artifact ledger could not be read.") from exc
