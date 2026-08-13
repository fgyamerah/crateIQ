"""Reference-artifact reconciliation preview and reviewed Stage 4 writers.

Writers are deliberately limited to reference columns: filepath on the two
legacy path surfaces and track_id on current field provenance/manual crates.
They never touch media files, tags, tracks, BPM, key, or cue content.
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


def _manual_crates_path(root: Path) -> Path:
    """Return only an existing root-contained, non-symlinked crate database."""
    candidate = root / "logs" / "manual_crates.db"
    try:
        resolved = assert_path_under_root(candidate, root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("manual_crates_path_unsafe", "Manual crates database is outside the selected root.") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise ReferenceApplyPreviewError("manual_crates_not_initialized", "Manual crates database is not initialized.")
    return resolved


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
            if conn.execute("SELECT 1 FROM field_provenance WHERE id != ? AND track_id=? AND field_name=? AND is_current=1 LIMIT 1", (row_id, new_id, field)).fetchone():
                return ["target_current_provenance_collision"]
            if conn.execute("SELECT 1 FROM tracks WHERE id=?", (old_id,)).fetchone():
                return ["old_track_no_longer_orphaned"]
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
    try:
        crates = _manual_crates_path(root)
    except ReferenceApplyPreviewError as exc:
        return [exc.code]
    try:
        with _ro(crates) as conn:
            rows = conn.execute("SELECT track_id FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, old_id)).fetchall()
            collision = conn.execute("SELECT 1 FROM manual_crate_tracks WHERE crate_id=? AND track_id=? LIMIT 1", (crate_id, new_id)).fetchone()
    except sqlite3.Error:
        return ["crate_row_identity_incomplete"]
    if len(rows) != 1:
        if not rows:
            return ["artifact_row_missing"]
        return ["crate_row_identity_ambiguous"]
    if collision:
        return ["target_crate_track_collision"]
    db_path = library_db_path(root)
    try:
        with _ro(db_path) as conn:
            if conn.execute("SELECT 1 FROM tracks WHERE id=?", (old_id,)).fetchone():
                return ["old_track_no_longer_orphaned"]
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
    # Stage 4D backs up manual_crates.db while the ledger remains in
    # processed.db.  These additive columns retain that distinction without
    # changing existing Stage 4A/B ledger rows.
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({_REFERENCE_LEDGER_TABLE})")}
    if "backup_database" not in columns:
        conn.execute("ALTER TABLE reference_artifact_ledger ADD COLUMN backup_database TEXT NOT NULL DEFAULT 'processed.db'")
    if "backup_owner_ledger_id" not in columns:
        conn.execute("ALTER TABLE reference_artifact_ledger ADD COLUMN backup_owner_ledger_id TEXT")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_backup_path(root: Path, ledger_id: str, *, database_name: str = "processed.db") -> Path:
    if database_name not in {"processed.db", "manual_crates.db"}:
        raise ReferenceApplyPreviewError("backup_database_unsupported", "Reference-artifact backup database is not supported.")
    directory = root / "logs" / "reference_artifact_backups"
    try:
        # Validate the existing ancestry before mkdir follows it.  In
        # particular, ``logs`` must not be a symlink escaping the selected
        # root merely because the backup directory has not been created yet.
        directory = assert_path_under_root(directory, root)
        directory.mkdir(parents=True, exist_ok=True)
        directory = assert_path_under_root(directory, root)
        path = assert_path_under_root(directory / f"{ledger_id}_{database_name}", root)
    except (OSError, ValueError) as exc:
        raise ReferenceApplyPreviewError("backup_path_outside_selected_root", "Reference-artifact backup directory is not safely contained by the selected root.") from exc
    if path.parent != directory or path.exists():
        raise ReferenceApplyPreviewError("backup_collision", "Refusing to overwrite an existing reference-artifact backup.")
    return path


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    with _ro(source_path) as source:
        with sqlite3.connect(destination_path) as destination:
            source.backup(destination)


def _verify_backup(backup_path: Path, table: str, expected: dict[str, Any], *, key_columns: tuple[str, ...] = ("id",)) -> str:
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
            if not key_columns or any(key not in expected for key in key_columns):
                raise ReferenceApplyPreviewError("backup_verification_failed", "Reference-artifact backup lacks its protected row identity.")
            where = " AND ".join(f"{key}=?" for key in key_columns)
            actual = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE {where}",
                tuple(expected[key] for key in key_columns),
            ).fetchone()
            if actual is None or dict(actual) != expected:
                raise ReferenceApplyPreviewError("backup_verification_failed", "Reference-artifact backup does not contain the protected row pre-state.")
    except ReferenceApplyPreviewError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReferenceApplyPreviewError("backup_verification_failed", "Reference-artifact backup could not be read and verified.") from exc
    return _sha256(backup_path)


def _backup_locked(root: Path, db_path: Path, ledger_id: str, table: str, expected: dict[str, Any], *, database_name: str = "processed.db", key_columns: tuple[str, ...] = ("id",)) -> tuple[Path, str]:
    path = _safe_backup_path(root, ledger_id, database_name=database_name)
    try:
        _sqlite_backup(db_path, path)
        return path, _verify_backup(path, table, expected, key_columns=key_columns)
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
                   after: dict[str, Any], backup_path: Path, backup_hash: str, status: str,
                   reference_field: str = "filepath", artifact_identifier: str | None = None,
                   backup_database: str = "processed.db", backup_owner_ledger_id: str | None = None) -> None:
    _require_table(conn, _REFERENCE_LEDGER_TABLE)
    conn.execute(
        "INSERT INTO reference_artifact_ledger (ledger_id, parent_ledger_id, created_at, root, plan_path, plan_id, plan_sha256, action_id, artifact_type, artifact_identifier, table_name, row_id, reference_field, old_path, new_path, before_values_json, after_values_json, backup_path, backup_sha256, status, backup_database, backup_owner_ledger_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ledger_id, parent_ledger_id, _now(), str(root), plan_path, plan_id, plan_sha256, action_id,
         artifact, artifact_identifier or f"{table}:{row_id}", table, row_id, reference_field, old, new,
         json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True), str(backup_path), backup_hash, status,
         backup_database, backup_owner_ledger_id),
    )


def _stage3_eligible(root: Path, path: Path, plan: dict[str, Any], validation: dict[str, Any], action_id: str) -> dict[str, Any]:
    matches = [item for item in plan["planned_actions"] if isinstance(item, dict) and item.get("action_id") == action_id]
    if not matches:
        return {"action_id": action_id, "eligible": False, "blockers": ["action_id_not_in_plan"]}
    records = {item.get("action_id"): item for item in validation.get("validation_records", []) if isinstance(item, dict)}
    result = _preview_action(root, matches[0], records.get(action_id))
    if result["eligible"]:
        try:
            if matches[0].get("action_type") == "update_path_reference":
                artifact, _table, _row_id, _old, _new = _action_identity(matches[0], root)
                if artifact not in _WRITABLE_ARTIFACTS:
                    result = {**result, "eligible": False, "blockers": ["unsupported_reference_action"]}
            elif matches[0].get("action_type") == "rehome_track_id":
                _rehome_identity(matches[0])
            else:
                result = {**result, "eligible": False, "blockers": ["unsupported_reference_action"]}
        except ReferenceApplyPreviewError as exc:
            result = {**result, "eligible": False, "blockers": [exc.code]}
    return result


def _rehome_identity(action: dict[str, Any]) -> tuple[str, str, int, int, str, int | None]:
    """Parse only the two reviewed track-ID writers' exact row identities."""
    try:
        artifact = str(action["artifact_type"])
        old_id, new_id = int(action["old_track_id"]), int(action["new_track_id"])
        if old_id == new_id or action.get("action_type") != "rehome_track_id" or action.get("reference_field") != "track_id":
            raise ValueError
        if artifact == "field_provenance":
            prefix, row_text = str(action["artifact_identifier"]).rsplit(":", 1)
            if prefix != "field_provenance" or not isinstance(action.get("field_name"), str) or not action["field_name"]:
                raise ValueError
            return artifact, "field_provenance", old_id, new_id, str(action["field_name"]), int(row_text)
        if artifact == "crate_track":
            table, crate_text, planned_old = str(action["artifact_identifier"]).split(":", 2)
            if table != "manual_crate_tracks" or int(planned_old) != old_id:
                raise ValueError
            return artifact, "manual_crate_tracks", old_id, new_id, str(int(crate_text)), None
    except (KeyError, TypeError, ValueError):
        pass
    raise ReferenceApplyPreviewError("track_rehome_identity_incomplete", "Reviewed track-ID action has incomplete or inconsistent identity.")


def _target_track(conn: sqlite3.Connection, root: Path, track_id: int) -> None:
    _require_table(conn, "tracks")
    row = conn.execute("SELECT filepath FROM tracks WHERE id=?", (track_id,)).fetchone()
    if row is None:
        raise ReferenceApplyPreviewError("target_track_missing_or_unsafe", "Replacement track no longer exists.")
    if _canonical_target(root, row[0], track_id=track_id):
        raise ReferenceApplyPreviewError("target_track_missing_or_unsafe", "Replacement track is no longer a canonical root-contained file.")


def _read_provenance_rehome(conn: sqlite3.Connection, root: Path, action: dict[str, Any]) -> tuple[int, int, dict[str, Any], dict[str, Any]]:
    _artifact, table, old_id, new_id, field_name, row_id = _rehome_identity(action)
    assert table == "field_provenance" and row_id is not None
    _require_table(conn, table)
    row = conn.execute("SELECT * FROM field_provenance WHERE id=?", (row_id,)).fetchone()
    if row is None:
        raise ReferenceApplyPreviewError("artifact_row_missing", "Reviewed field-provenance row no longer exists.")
    before = dict(row)
    if before.get("track_id") != old_id or before.get("field_name") != field_name or before.get("is_current") != 1:
        raise ReferenceApplyPreviewError("artifact_expected_before_mismatch", "Reviewed field-provenance row no longer matches its planned identity.")
    if conn.execute("SELECT 1 FROM tracks WHERE id=?", (old_id,)).fetchone() is not None:
        raise ReferenceApplyPreviewError("old_track_no_longer_orphaned", "Old provenance track is no longer orphaned; re-review is required.")
    if conn.execute("SELECT 1 FROM field_provenance WHERE id != ? AND track_id=? AND field_name=? AND is_current=1 LIMIT 1", (row_id, new_id, field_name)).fetchone():
        raise ReferenceApplyPreviewError("target_current_provenance_collision", "Replacement track already has current provenance for this field.")
    _target_track(conn, root, new_id)
    return row_id, new_id, before, {**before, "track_id": new_id}


def _read_crate_rehome(crate_conn: sqlite3.Connection, tracks_conn: sqlite3.Connection, root: Path, action: dict[str, Any]) -> tuple[int, int, dict[str, Any], dict[str, Any]]:
    _artifact, table, old_id, new_id, crate_text, _row_id = _rehome_identity(action)
    assert table == "manual_crate_tracks"
    crate_id = int(crate_text)
    _require_table(crate_conn, table)
    rows = crate_conn.execute("SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, old_id)).fetchall()
    if len(rows) != 1:
        if not rows:
            raise ReferenceApplyPreviewError("artifact_row_missing", "Reviewed manual-crate row no longer exists.")
        raise ReferenceApplyPreviewError("crate_row_identity_ambiguous", "Reviewed manual-crate identity matches multiple rows.")
    before = dict(rows[0])
    if crate_conn.execute("SELECT 1 FROM manual_crate_tracks WHERE crate_id=? AND track_id=? LIMIT 1", (crate_id, new_id)).fetchone():
        raise ReferenceApplyPreviewError("target_crate_track_collision", "Replacement track is already present in this manual crate.")
    if tracks_conn.execute("SELECT 1 FROM tracks WHERE id=?", (old_id,)).fetchone() is not None:
        raise ReferenceApplyPreviewError("old_track_no_longer_orphaned", "Old crate track is no longer orphaned; re-review is required.")
    _target_track(tracks_conn, root, new_id)
    return crate_id, new_id, before, {**before, "track_id": new_id}


def _apply_provenance_rehome(root: Path, path: Path, plan_id: str, snapshot_hash: str, action: dict[str, Any]) -> dict[str, Any]:
    db_path = library_db_path(root)
    ledger_id = f"ref-{uuid.uuid4().hex}"
    backup_path: Path | None = None
    backup_hash = ""
    committed = False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row_id, new_id, before, after = _read_provenance_rehome(conn, root, action)
            backup_path, backup_hash = _backup_locked(root, db_path, ledger_id, "field_provenance", before)
            # Revalidate after the external backup operation before the only
            # permitted column update.
            row_id, new_id, current_before, current_after = _read_provenance_rehome(conn, root, action)
            if current_before != before or current_after != after:
                raise ReferenceApplyPreviewError("artifact_expected_before_mismatch", "Field-provenance row changed during backup.")
            _reference_ledger_ddl(conn)
            cursor = conn.execute("UPDATE field_provenance SET track_id=? WHERE id=? AND track_id=? AND field_name=? AND is_current=1", (new_id, row_id, before["track_id"], before["field_name"]))
            if cursor.rowcount != 1:
                raise ReferenceApplyPreviewError("reference_update_count_mismatch", "Field-provenance row changed before it could be rehomed.")
            actual = conn.execute("SELECT * FROM field_provenance WHERE id=?", (row_id,)).fetchone()
            if actual is None or dict(actual) != after:
                raise ReferenceApplyPreviewError("reference_postcondition_failed", "Field-provenance postcondition verification failed.")
            _insert_ledger(conn, ledger_id=ledger_id, parent_ledger_id=None, root=root, plan_path=str(path), plan_id=plan_id,
                           plan_sha256=snapshot_hash, action_id=action["action_id"], artifact="field_provenance",
                           table="field_provenance", row_id=row_id, old=str(before["track_id"]), new=str(new_id), before=before,
                           after=after, backup_path=backup_path, backup_hash=backup_hash, status="applied",
                           reference_field="track_id", artifact_identifier=f"field_provenance:{row_id}")
            conn.commit()
            committed = True
    except Exception:
        if backup_path is not None and not committed:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return {"plan_path": str(path), "plan_id": plan_id, "plan_sha256": snapshot_hash,
            "results": [{"action_id": action["action_id"], "ledger_id": ledger_id, "status": "applied", "backup_path": str(backup_path), "backup_sha256": backup_hash, "verification_status": "verified"}],
            "message": "One current field-provenance track ID was rehomed and verified. No media file, tag, BPM, key, or cue content was changed."}


def _append_manual_completion(conn: sqlite3.Connection, *, ledger_id: str, parent_ledger_id: str, root: Path,
                              original: sqlite3.Row, status: str) -> None:
    before = _json_row(original["before_values_json"], code="manual_crate_recovery_provenance_missing")
    after = _json_row(original["after_values_json"], code="manual_crate_recovery_provenance_missing")
    _insert_ledger(conn, ledger_id=ledger_id, parent_ledger_id=parent_ledger_id, root=root,
                   plan_path=original["plan_path"], plan_id=original["plan_id"], plan_sha256=original["plan_sha256"], action_id=original["action_id"],
                   artifact="crate_track", table="manual_crate_tracks", row_id=0, old=str(before["track_id"]), new=str(after["track_id"]),
                   before=before, after=after, backup_path=Path(original["backup_path"]), backup_hash=original["backup_sha256"], status=status,
                   reference_field="track_id", artifact_identifier=original["artifact_identifier"], backup_database="manual_crates.db",
                   backup_owner_ledger_id=parent_ledger_id)


def _recover_prepared_manual_apply(root: Path, path: Path, plan_id: str, snapshot_hash: str, action: dict[str, Any]) -> dict[str, Any] | None:
    """Close one interrupted Stage 4D apply before its physical row is reused.

    Exact plan binding remains mandatory for returning a recovered result to
    the caller.  The prepare itself is additionally serialized by the crate
    row transition, so a regenerated plan cannot bypass another plan's
    unresolved durable prepare.
    """
    db_path = library_db_path(root)
    _artifact, table, old_id, new_id, crate_text, _row_id = _rehome_identity(action)
    if table != "manual_crate_tracks":
        raise ReferenceApplyPreviewError("track_rehome_identity_incomplete", "Reviewed manual-crate action has incomplete identity.")
    artifact_identifier = f"manual_crate_tracks:{crate_text}:{old_id}"
    try:
        crates_path = _manual_crates_path(root)
    except ReferenceApplyPreviewError:
        return None
    try:
        # Hold both writer locks from state proof through the terminal ledger
        # append.  A read-only proof followed by an independent ledger write
        # would allow a crate writer to invalidate the proven state in between.
        with sqlite3.connect(db_path) as conn, sqlite3.connect(crates_path) as crates_conn:
            conn.row_factory = sqlite3.Row
            crates_conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_REFERENCE_LEDGER_TABLE,)).fetchone() is None:
                return None
            rows = conn.execute(
                f"""SELECT * FROM {_REFERENCE_LEDGER_TABLE}
                    WHERE root=? AND artifact_type='crate_track'
                      AND table_name='manual_crate_tracks' AND reference_field='track_id'
                      AND artifact_identifier=? AND old_path=? AND new_path=?
                      AND status='manual_crate_apply_prepared'
                    ORDER BY created_at DESC""",
                (str(root), artifact_identifier, str(old_id), str(new_id)),
            ).fetchall()
            rows = [row for row in rows if conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status IN ('applied', 'manual_crate_apply_not_committed') LIMIT 1", (row["ledger_id"],)).fetchone() is None]
            if not rows:
                return None
            if len(rows) != 1:
                raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Multiple incomplete manual-crate prepared records require manual recovery.")
            prepared = rows[0]
            before = _json_row(prepared["before_values_json"], code="manual_crate_recovery_provenance_missing")
            after = _json_row(prepared["after_values_json"], code="manual_crate_recovery_provenance_missing")
            try:
                table, crate_text, old_text = str(prepared["artifact_identifier"]).split(":", 2)
                crate_id, old_id = int(crate_text), int(old_text)
                if (table != "manual_crate_tracks" or before.get("crate_id") != crate_id
                        or before.get("track_id") != old_id or after.get("crate_id") != crate_id
                        or after.get("track_id") != new_id):
                    raise ValueError
            except (TypeError, ValueError):
                raise ReferenceApplyPreviewError("manual_crate_recovery_provenance_missing", "Prepared manual-crate ledger identity is malformed.")
            crates_conn.execute("BEGIN IMMEDIATE")
            _verified_original_backup(root, prepared, "manual_crate_tracks", before, key_columns=("crate_id", "track_id"))
            _require_table(crates_conn, "manual_crate_tracks")
            current_before = crates_conn.execute(
                "SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?",
                (crate_id, before["track_id"]),
            ).fetchall()
            current_after = crates_conn.execute(
                "SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?",
                (crate_id, after["track_id"]),
            ).fetchall()
            if len(current_before) == 1 and dict(current_before[0]) == before and not current_after:
                # The first DB committed but the crate transaction did not.
                # The writer locks make this exact state proof and terminal
                # record one serialized decision.
                _insert_ledger(conn, ledger_id=f"ref-recovery-{uuid.uuid4().hex}", parent_ledger_id=prepared["ledger_id"], root=root,
                               plan_path=prepared["plan_path"], plan_id=prepared["plan_id"], plan_sha256=prepared["plan_sha256"], action_id=prepared["action_id"],
                               artifact="crate_track", table="manual_crate_tracks", row_id=0, old=str(before["track_id"]), new=str(before["track_id"]),
                               before=before, after=before, backup_path=Path(prepared["backup_path"]), backup_hash=prepared["backup_sha256"],
                               status="manual_crate_apply_not_committed", reference_field="track_id", artifact_identifier=prepared["artifact_identifier"],
                               backup_database="manual_crates.db", backup_owner_ledger_id=prepared["ledger_id"])
                conn.commit()
                return None
            if len(current_after) != 1 or dict(current_after[0]) != after or current_before:
                raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Incomplete manual-crate prepared record does not match the exact applied state.")
            completed_id = f"ref-{uuid.uuid4().hex}"
            _append_manual_completion(conn, ledger_id=completed_id, parent_ledger_id=prepared["ledger_id"], root=root, original=prepared, status="applied")
            conn.commit()
            if (prepared["plan_path"], prepared["plan_id"], prepared["plan_sha256"], prepared["action_id"]) != (
                str(path), plan_id, snapshot_hash, action["action_id"],
            ):
                raise ReferenceApplyPreviewError("manual_crate_recovery_required", "A different reviewed plan's manual-crate prepare was finalized; re-review is required before any new apply.")
            return {"plan_path": str(path), "plan_id": plan_id, "plan_sha256": snapshot_hash,
                    "results": [{"action_id": action["action_id"], "ledger_id": completed_id, "status": "applied_recovered", "backup_path": str(prepared["backup_path"]), "backup_sha256": prepared["backup_sha256"], "verification_status": "verified"}],
                    "message": "A previously committed manual-crate rehome was verified and its prepared ledger record was finalized."}
    except sqlite3.Error as exc:
        raise ReferenceApplyPreviewError("reference_ledger_unreadable", "Reference-artifact ledger could not be read for manual-crate recovery.") from exc


def _apply_manual_crate_rehome(root: Path, path: Path, plan_id: str, snapshot_hash: str, action: dict[str, Any]) -> dict[str, Any]:
    """Two-DB writer with a durable prepared record before the crate commit.

    SQLite cannot atomically commit these two files.  The prepared ledger row
    plus verified manual_crates backup makes an interrupted finalization
    observable and recoverable, rather than a silent partial success.
    """
    db_path, crates_path = library_db_path(root), _manual_crates_path(root)
    prepared_id, completed_id = f"ref-prepare-{uuid.uuid4().hex}", f"ref-{uuid.uuid4().hex}"
    backup_path: Path | None = None
    backup_hash = ""
    prepared = False
    crates_committed = False
    try:
        with sqlite3.connect(db_path) as tracks_conn, sqlite3.connect(crates_path) as crates_conn:
            tracks_conn.row_factory = sqlite3.Row
            crates_conn.row_factory = sqlite3.Row
            tracks_conn.execute("BEGIN IMMEDIATE")
            crates_conn.execute("BEGIN IMMEDIATE")
            crate_id, new_id, before, after = _read_crate_rehome(crates_conn, tracks_conn, root, action)
            backup_path, backup_hash = _backup_locked(root, crates_path, prepared_id, "manual_crate_tracks", before,
                                                       database_name="manual_crates.db", key_columns=("crate_id", "track_id"))
            crate_id, new_id, current_before, current_after = _read_crate_rehome(crates_conn, tracks_conn, root, action)
            if current_before != before or current_after != after:
                raise ReferenceApplyPreviewError("artifact_expected_before_mismatch", "Manual-crate row changed during backup.")
            _reference_ledger_ddl(tracks_conn)
            _insert_ledger(tracks_conn, ledger_id=prepared_id, parent_ledger_id=None, root=root, plan_path=str(path), plan_id=plan_id,
                           plan_sha256=snapshot_hash, action_id=action["action_id"], artifact="crate_track", table="manual_crate_tracks", row_id=0,
                           old=str(before["track_id"]), new=str(new_id), before=before, after=after, backup_path=backup_path, backup_hash=backup_hash,
                           status="manual_crate_apply_prepared", reference_field="track_id", artifact_identifier=f"manual_crate_tracks:{crate_id}:{before['track_id']}",
                           backup_database="manual_crates.db", backup_owner_ledger_id=prepared_id)
            tracks_conn.commit()
            prepared = True
            # The prepared record has to be durable before the crate commit.
            # Reacquire processed.db's writer lock and revalidate while the
            # crate writer lock is still held, so a concurrent tracks writer
            # cannot invalidate the canonical replacement before this update.
            tracks_conn.execute("BEGIN IMMEDIATE")
            latest = tracks_conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (prepared_id,)).fetchone()
            if latest is None or tracks_conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? LIMIT 1", (prepared_id,)).fetchone():
                raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Prepared manual-crate record changed before the crate update.")
            crate_id, new_id, current_before, current_after = _read_crate_rehome(crates_conn, tracks_conn, root, action)
            if current_before != before or current_after != after:
                raise ReferenceApplyPreviewError("artifact_expected_before_mismatch", "Manual-crate row changed before it could be rehomed.")
            cursor = crates_conn.execute("UPDATE manual_crate_tracks SET track_id=? WHERE crate_id=? AND track_id=?", (new_id, crate_id, before["track_id"]))
            if cursor.rowcount != 1:
                raise ReferenceApplyPreviewError("reference_update_count_mismatch", "Manual-crate row changed before it could be rehomed.")
            actual = crates_conn.execute("SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, new_id)).fetchone()
            if actual is None or dict(actual) != after:
                raise ReferenceApplyPreviewError("reference_postcondition_failed", "Manual-crate postcondition verification failed.")
            crates_conn.commit()
            crates_committed = True
            _append_manual_completion(tracks_conn, ledger_id=completed_id, parent_ledger_id=prepared_id, root=root, original=latest, status="applied")
            tracks_conn.commit()
    except Exception as exc:
        if backup_path is not None and not prepared:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        if prepared and crates_committed:
            raise ReferenceApplyPreviewError("manual_crate_ledger_finalization_required", "Manual-crate change committed with a prepared ledger record; recovery must finalize it before any further action.") from exc
        if prepared:
            raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Manual-crate apply did not commit after its prepared ledger record; retry must first close the recoverable record.") from exc
        raise
    return {"plan_path": str(path), "plan_id": plan_id, "plan_sha256": snapshot_hash,
            "results": [{"action_id": action["action_id"], "ledger_id": completed_id, "status": "applied", "backup_path": str(backup_path), "backup_sha256": backup_hash, "verification_status": "verified"}],
            "message": "One manual-crate track ID was rehomed and verified. No media file, tag, BPM, key, or cue content was changed."}


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
    db_path = library_db_path(root)
    # Recovery opens processed.db with a writer connection.  Check this
    # before inspecting a manual-crate prepared record so a blocked request
    # can never initialize an empty database merely by attempting recovery.
    if not db_path.is_file():
        raise ReferenceApplyPreviewError("library_not_initialized", "Configured library is not initialized.")
    selected_action = next((item for item in plan["planned_actions"] if isinstance(item, dict) and item.get("action_id") == action_ids[0]), None)
    if selected_action is not None and selected_action.get("action_type") == "rehome_track_id" and selected_action.get("artifact_type") == "crate_track":
        recovered = _recover_prepared_manual_apply(root, path, plan_id, snapshot_hash, selected_action)
        if recovered is not None:
            return recovered
    eligibility = _stage3_eligible(root, path, plan, validation, action_ids[0])
    if not eligibility["eligible"]:
        raise ReferenceApplyPreviewError("action_not_eligible", "Selected action is blocked: " + ", ".join(eligibility["blockers"]))
    action = next(item for item in plan["planned_actions"] if isinstance(item, dict) and item.get("action_id") == action_ids[0])
    if action.get("action_type") == "rehome_track_id":
        artifact = action.get("artifact_type")
        if artifact == "field_provenance":
            return _apply_provenance_rehome(root, path, plan_id, snapshot_hash, action)
        if artifact == "crate_track":
            return _apply_manual_crate_rehome(root, path, plan_id, snapshot_hash, action)
        raise ReferenceApplyPreviewError("unsupported_reference_action", "Only reviewed field-provenance or manual-crate track-ID rehomes are supported.")
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


def _verified_original_backup(root: Path, row: sqlite3.Row, table: str, before: dict[str, Any], *, key_columns: tuple[str, ...] = ("id",)) -> None:
    try:
        path = assert_path_under_root(str(row["backup_path"] or ""), root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup path is outside the selected root.") from exc
    expected_directory = root / "logs" / "reference_artifact_backups"
    try:
        expected_directory = assert_path_under_root(expected_directory, root)
    except ValueError as exc:
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup directory is outside the selected root.") from exc
    database_name = row["backup_database"] if "backup_database" in row.keys() else "processed.db"
    owner_id = row["backup_owner_ledger_id"] if "backup_owner_ledger_id" in row.keys() else None
    if database_name not in {"processed.db", "manual_crates.db"}:
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup database provenance is unsupported.")
    if path.parent != expected_directory or path.name != f"{owner_id or row['ledger_id']}_{database_name}":
        raise ReferenceApplyPreviewError("rollback_backup_path_unsafe", "Reference rollback backup path does not match its ledger identity.")
    if not path.is_file():
        raise ReferenceApplyPreviewError("rollback_backup_missing", "Reference rollback backup is no longer available.")
    try:
        backup_hash = _sha256(path)
    except OSError as exc:
        raise ReferenceApplyPreviewError("rollback_backup_unreadable", "Reference rollback backup could not be read.") from exc
    if backup_hash != row["backup_sha256"]:
        raise ReferenceApplyPreviewError("rollback_backup_hash_mismatch", "Reference rollback backup hash does not match its ledger provenance.")
    _verify_backup(path, table, before, key_columns=key_columns)


def _rollback_manual_crate(ledger_id: str, *, confirm: bool) -> dict[str, Any]:
    root = selected_library_root().resolve(strict=False)
    db_path, crates_path = library_db_path(root), _manual_crates_path(root)
    recovered = _recover_prepared_manual_rollback(root, ledger_id)
    if recovered is not None:
        return recovered
    prepare_id, rollback_id = f"ref-rollback-prepare-{uuid.uuid4().hex}", f"ref-rollback-{uuid.uuid4().hex}"
    backup_path: Path | None = None
    backup_hash = ""
    prepared = False
    crates_committed = False
    try:
        with sqlite3.connect(db_path) as tracks_conn, sqlite3.connect(crates_path) as crates_conn:
            tracks_conn.row_factory = sqlite3.Row
            crates_conn.row_factory = sqlite3.Row
            tracks_conn.execute("BEGIN IMMEDIATE")
            crates_conn.execute("BEGIN IMMEDIATE")
            _require_table(tracks_conn, _REFERENCE_LEDGER_TABLE)
            original = tracks_conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (ledger_id,)).fetchone()
            if original is None or original["artifact_type"] != "crate_track" or original["status"] != "applied" or original["reference_field"] != "track_id":
                raise ReferenceApplyPreviewError("ledger_not_rollback_eligible", "Ledger entry is not an applied manual-crate track-ID rehome.")
            if original["root"] != str(root):
                raise ReferenceApplyPreviewError("ledger_not_rollback_eligible", "Ledger entry belongs to a different selected root.")
            if tracks_conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='rolled_back' LIMIT 1", (ledger_id,)).fetchone():
                raise ReferenceApplyPreviewError("rollback_already_completed", "This manual-crate ledger entry has already been rolled back.")
            before = _json_row(original["before_values_json"], code="ledger_rollback_provenance_missing")
            after = _json_row(original["after_values_json"], code="ledger_rollback_provenance_missing")
            if set(before) != set(after) or any(before[key] != after[key] for key in before if key != "track_id"):
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Ledger entry does not record a track-ID-only crate change.")
            try:
                _table, crate_text, old_text = str(original["artifact_identifier"]).split(":", 2)
                crate_id, old_id, new_id = int(crate_text), int(old_text), int(after["track_id"])
                if _table != "manual_crate_tracks" or before.get("crate_id") != crate_id or before.get("track_id") != old_id:
                    raise ValueError
            except (TypeError, ValueError):
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Manual-crate ledger identity is malformed.")
            _verified_original_backup(root, original, "manual_crate_tracks", before, key_columns=("crate_id", "track_id"))
            _require_table(crates_conn, "manual_crate_tracks")
            current_rows = crates_conn.execute("SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, new_id)).fetchall()
            if len(current_rows) != 1 or dict(current_rows[0]) != after:
                raise ReferenceApplyPreviewError("rollback_state_drift", "Current manual-crate row no longer matches the recorded applied state.")
            if crates_conn.execute("SELECT 1 FROM manual_crate_tracks WHERE crate_id=? AND track_id=? LIMIT 1", (crate_id, old_id)).fetchone():
                raise ReferenceApplyPreviewError("rollback_target_collision", "Rollback track ID is already present in this manual crate.")
            if tracks_conn.execute("SELECT 1 FROM tracks WHERE id=? LIMIT 1", (old_id,)).fetchone():
                raise ReferenceApplyPreviewError("rollback_old_track_no_longer_orphaned", "Rollback track ID is now owned by a canonical track.")
            _reference_ledger_ddl(tracks_conn)
            backup_path, backup_hash = _backup_locked(root, crates_path, prepare_id, "manual_crate_tracks", after,
                                                       database_name="manual_crates.db", key_columns=("crate_id", "track_id"))
            current_rows = crates_conn.execute("SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, new_id)).fetchall()
            if len(current_rows) != 1 or dict(current_rows[0]) != after:
                raise ReferenceApplyPreviewError("rollback_state_drift", "Current manual-crate row changed during backup.")
            if crates_conn.execute("SELECT 1 FROM manual_crate_tracks WHERE crate_id=? AND track_id=? LIMIT 1", (crate_id, old_id)).fetchone():
                raise ReferenceApplyPreviewError("rollback_target_collision", "Rollback track ID is already present in this manual crate.")
            _insert_ledger(tracks_conn, ledger_id=prepare_id, parent_ledger_id=ledger_id, root=root,
                           plan_path=original["plan_path"], plan_id=original["plan_id"], plan_sha256=original["plan_sha256"], action_id=original["action_id"],
                           artifact="crate_track", table="manual_crate_tracks", row_id=0, old=str(new_id), new=str(old_id), before=after, after=before,
                           backup_path=backup_path, backup_hash=backup_hash, status="manual_crate_rollback_prepared", reference_field="track_id",
                           artifact_identifier=original["artifact_identifier"], backup_database="manual_crates.db", backup_owner_ledger_id=prepare_id)
            tracks_conn.commit()
            prepared = True
            # The durable prepare is followed by a fresh processed.db writer
            # lock and exact revalidation before the crate mutation.
            tracks_conn.execute("BEGIN IMMEDIATE")
            latest = tracks_conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (prepare_id,)).fetchone()
            if latest is None or tracks_conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='rolled_back' LIMIT 1", (ledger_id,)).fetchone():
                raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Prepared manual-crate rollback changed before the crate update.")
            current_rows = crates_conn.execute("SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, new_id)).fetchall()
            if len(current_rows) != 1 or dict(current_rows[0]) != after:
                raise ReferenceApplyPreviewError("rollback_state_drift", "Current manual-crate row changed before it could be restored.")
            if crates_conn.execute("SELECT 1 FROM manual_crate_tracks WHERE crate_id=? AND track_id=? LIMIT 1", (crate_id, old_id)).fetchone():
                raise ReferenceApplyPreviewError("rollback_target_collision", "Rollback track ID is already present in this manual crate.")
            if tracks_conn.execute("SELECT 1 FROM tracks WHERE id=? LIMIT 1", (old_id,)).fetchone():
                raise ReferenceApplyPreviewError("rollback_old_track_no_longer_orphaned", "Rollback track ID is now owned by a canonical track.")
            cursor = crates_conn.execute("UPDATE manual_crate_tracks SET track_id=? WHERE crate_id=? AND track_id=?", (old_id, crate_id, new_id))
            if cursor.rowcount != 1:
                raise ReferenceApplyPreviewError("rollback_update_count_mismatch", "Manual-crate row changed before it could be restored.")
            restored = crates_conn.execute("SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?", (crate_id, old_id)).fetchone()
            if restored is None or dict(restored) != before:
                raise ReferenceApplyPreviewError("rollback_postcondition_failed", "Manual-crate rollback postcondition verification failed.")
            crates_conn.commit()
            crates_committed = True
            _insert_ledger(tracks_conn, ledger_id=rollback_id, parent_ledger_id=ledger_id, root=root,
                           plan_path=original["plan_path"], plan_id=original["plan_id"], plan_sha256=original["plan_sha256"], action_id=original["action_id"],
                           artifact="crate_track", table="manual_crate_tracks", row_id=0, old=str(new_id), new=str(old_id), before=after, after=before,
                           backup_path=backup_path, backup_hash=backup_hash, status="rolled_back", reference_field="track_id",
                           artifact_identifier=original["artifact_identifier"], backup_database="manual_crates.db", backup_owner_ledger_id=prepare_id)
            tracks_conn.commit()
    except Exception as exc:
        if backup_path is not None and not prepared:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        if prepared and crates_committed:
            raise ReferenceApplyPreviewError("manual_crate_ledger_finalization_required", "Manual-crate rollback committed with a prepared ledger record; recovery must finalize it before any further action.") from exc
        if prepared:
            raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Manual-crate rollback did not commit after its prepared ledger record; retry must first close the recoverable record.") from exc
        raise
    return {"ledger_id": rollback_id, "rollback_of_ledger_id": ledger_id, "status": "rolled_back", "backup_path": str(backup_path), "backup_sha256": backup_hash, "verification_status": "verified"}


def _recover_prepared_manual_rollback(root: Path, original_ledger_id: str) -> dict[str, Any] | None:
    """Finalize or explicitly close an interrupted manual-crate rollback."""
    db_path, crates_path = library_db_path(root), _manual_crates_path(root)
    with sqlite3.connect(db_path) as conn, sqlite3.connect(crates_path) as crates_conn:
        conn.row_factory = sqlite3.Row
        crates_conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_REFERENCE_LEDGER_TABLE,)).fetchone() is None:
            return None
        original = conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (original_ledger_id,)).fetchone()
        rows = conn.execute(
            f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='manual_crate_rollback_prepared' ORDER BY created_at DESC",
            (original_ledger_id,),
        ).fetchall()
        # A completed rollback is deliberately parented to the original apply
        # ledger.  The prepared record owns only its explicit not-committed
        # closure, so recognize both terminal lineages here.
        rollback_completed = conn.execute(
            f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='rolled_back' LIMIT 1",
            (original_ledger_id,),
        ).fetchone()
        rows = [row for row in rows if not rollback_completed and conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='manual_crate_rollback_not_committed' LIMIT 1", (row["ledger_id"],)).fetchone() is None]
        if not rows:
            return None
        if original is None or len(rows) != 1:
            raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Manual-crate rollback has incomplete or ambiguous prepared ledger state.")
        prepared = rows[0]
        before = _json_row(prepared["before_values_json"], code="manual_crate_recovery_provenance_missing")
        after = _json_row(prepared["after_values_json"], code="manual_crate_recovery_provenance_missing")
        try:
            table, crate_text, _old_text = str(prepared["artifact_identifier"]).split(":", 2)
            crate_id = int(crate_text)
            if table != "manual_crate_tracks" or before.get("crate_id") != crate_id or after.get("crate_id") != crate_id:
                raise ValueError
        except (TypeError, ValueError):
            raise ReferenceApplyPreviewError("manual_crate_recovery_provenance_missing", "Prepared manual-crate rollback identity is malformed.")
        crates_conn.execute("BEGIN IMMEDIATE")
        _verified_original_backup(root, prepared, "manual_crate_tracks", before, key_columns=("crate_id", "track_id"))
        _require_table(crates_conn, "manual_crate_tracks")
        current_before = crates_conn.execute(
            "SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?",
            (crate_id, before["track_id"]),
        ).fetchall()
        current_after = crates_conn.execute(
            "SELECT * FROM manual_crate_tracks WHERE crate_id=? AND track_id=?",
            (crate_id, after["track_id"]),
        ).fetchall()
        if len(current_after) == 1 and dict(current_after[0]) == after and not current_before:
            if conn.execute("SELECT 1 FROM tracks WHERE id=? LIMIT 1", (after["track_id"],)).fetchone():
                raise ReferenceApplyPreviewError("rollback_old_track_no_longer_orphaned", "Rollback track ID is now owned by a canonical track.")
            rollback_id = f"ref-rollback-{uuid.uuid4().hex}"
            _insert_ledger(conn, ledger_id=rollback_id, parent_ledger_id=original_ledger_id, root=root,
                           plan_path=original["plan_path"], plan_id=original["plan_id"], plan_sha256=original["plan_sha256"], action_id=original["action_id"],
                           artifact="crate_track", table="manual_crate_tracks", row_id=0, old=str(before["track_id"]), new=str(after["track_id"]),
                           before=before, after=after, backup_path=Path(prepared["backup_path"]), backup_hash=prepared["backup_sha256"], status="rolled_back",
                           reference_field="track_id", artifact_identifier=original["artifact_identifier"], backup_database="manual_crates.db",
                           backup_owner_ledger_id=prepared["ledger_id"])
            conn.commit()
            return {"ledger_id": rollback_id, "rollback_of_ledger_id": original_ledger_id, "status": "rolled_back_recovered", "backup_path": str(prepared["backup_path"]), "backup_sha256": prepared["backup_sha256"], "verification_status": "verified"}
        if len(current_before) == 1 and dict(current_before[0]) == before and not current_after:
            _insert_ledger(conn, ledger_id=f"ref-recovery-{uuid.uuid4().hex}", parent_ledger_id=prepared["ledger_id"], root=root,
                           plan_path=original["plan_path"], plan_id=original["plan_id"], plan_sha256=original["plan_sha256"], action_id=original["action_id"],
                           artifact="crate_track", table="manual_crate_tracks", row_id=0, old=str(before["track_id"]), new=str(before["track_id"]),
                           before=before, after=before, backup_path=Path(prepared["backup_path"]), backup_hash=prepared["backup_sha256"], status="manual_crate_rollback_not_committed",
                           reference_field="track_id", artifact_identifier=original["artifact_identifier"], backup_database="manual_crates.db",
                           backup_owner_ledger_id=prepared["ledger_id"])
            conn.commit()
            return None
        raise ReferenceApplyPreviewError("manual_crate_recovery_required", "Incomplete manual-crate rollback does not match either protected state.")


def _rollback_provenance(ledger_id: str, *, confirm: bool) -> dict[str, Any]:
    root = selected_library_root().resolve(strict=False)
    db_path = library_db_path(root)
    rollback_id = f"ref-rollback-{uuid.uuid4().hex}"
    rollback_backup: Path | None = None
    rollback_hash = ""
    committed = False
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            _require_table(conn, _REFERENCE_LEDGER_TABLE)
            original = conn.execute(f"SELECT * FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (ledger_id,)).fetchone()
            if original is None or original["artifact_type"] != "field_provenance" or original["status"] != "applied" or original["parent_ledger_id"] is not None or original["reference_field"] != "track_id" or original["root"] != str(root):
                raise ReferenceApplyPreviewError("ledger_not_rollback_eligible", "Ledger entry is not an applied field-provenance track-ID rehome.")
            if conn.execute(f"SELECT 1 FROM {_REFERENCE_LEDGER_TABLE} WHERE parent_ledger_id=? AND status='rolled_back' LIMIT 1", (ledger_id,)).fetchone():
                raise ReferenceApplyPreviewError("rollback_already_completed", "This field-provenance ledger entry has already been rolled back.")
            before = _json_row(original["before_values_json"], code="ledger_rollback_provenance_missing")
            after = _json_row(original["after_values_json"], code="ledger_rollback_provenance_missing")
            if set(before) != set(after) or any(before[key] != after[key] for key in before if key != "track_id") or before.get("id") != original["row_id"] or after.get("id") != original["row_id"]:
                raise ReferenceApplyPreviewError("ledger_rollback_provenance_missing", "Ledger entry does not record a track-ID-only provenance change.")
            _verified_original_backup(root, original, "field_provenance", before)
            _require_table(conn, "field_provenance")
            current = conn.execute("SELECT * FROM field_provenance WHERE id=?", (original["row_id"],)).fetchone()
            if current is None or dict(current) != after:
                raise ReferenceApplyPreviewError("rollback_state_drift", "Current field-provenance row no longer matches the recorded applied state.")
            old_id, new_id, field_name = int(before["track_id"]), int(after["track_id"]), before.get("field_name")
            if not isinstance(field_name, str) or not field_name or conn.execute("SELECT 1 FROM field_provenance WHERE id != ? AND track_id=? AND field_name=? AND is_current=1 LIMIT 1", (original["row_id"], old_id, field_name)).fetchone():
                raise ReferenceApplyPreviewError("rollback_target_collision", "Rollback would collide with current field provenance.")
            if conn.execute("SELECT 1 FROM tracks WHERE id=? LIMIT 1", (old_id,)).fetchone():
                raise ReferenceApplyPreviewError("rollback_old_track_no_longer_orphaned", "Rollback track ID is now owned by a canonical track.")
            _reference_ledger_ddl(conn)
            rollback_backup, rollback_hash = _backup_locked(root, db_path, rollback_id, "field_provenance", after)
            cursor = conn.execute("UPDATE field_provenance SET track_id=? WHERE id=? AND track_id=?", (old_id, original["row_id"], new_id))
            if cursor.rowcount != 1:
                raise ReferenceApplyPreviewError("rollback_update_count_mismatch", "Field-provenance row changed before it could be restored.")
            restored = conn.execute("SELECT * FROM field_provenance WHERE id=?", (original["row_id"],)).fetchone()
            if restored is None or dict(restored) != before:
                raise ReferenceApplyPreviewError("rollback_postcondition_failed", "Field-provenance rollback postcondition verification failed.")
            _insert_ledger(conn, ledger_id=rollback_id, parent_ledger_id=ledger_id, root=root, plan_path=original["plan_path"], plan_id=original["plan_id"],
                           plan_sha256=original["plan_sha256"], action_id=original["action_id"], artifact="field_provenance", table="field_provenance",
                           row_id=original["row_id"], old=str(new_id), new=str(old_id), before=after, after=before, backup_path=rollback_backup,
                           backup_hash=rollback_hash, status="rolled_back", reference_field="track_id", artifact_identifier=original["artifact_identifier"])
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


def rollback(ledger_id: str, *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ReferenceApplyPreviewError("confirmation_required", "Set confirm=true to rollback one applied reference-artifact action.")
    root = selected_library_root().resolve(strict=False)
    db_path = library_db_path(root)
    if not db_path.is_file():
        raise ReferenceApplyPreviewError("library_not_initialized", "Configured library is not initialized.")
    try:
        with _ro(db_path) as ledger_conn:
            ledger_conn.row_factory = sqlite3.Row
            if ledger_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_REFERENCE_LEDGER_TABLE,)).fetchone():
                candidate = ledger_conn.execute(f"SELECT artifact_type, reference_field, status FROM {_REFERENCE_LEDGER_TABLE} WHERE ledger_id=?", (ledger_id,)).fetchone()
                if candidate is not None and candidate["artifact_type"] == "crate_track" and candidate["reference_field"] == "track_id":
                    return _rollback_manual_crate(ledger_id, confirm=confirm)
                if candidate is not None and candidate["artifact_type"] == "field_provenance" and candidate["reference_field"] == "track_id":
                    return _rollback_provenance(ledger_id, confirm=confirm)
    except sqlite3.Error as exc:
        raise ReferenceApplyPreviewError("reference_ledger_unreadable", "Reference-artifact ledger could not be read.") from exc
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
            _reference_ledger_ddl(conn)
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
