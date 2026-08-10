"""Reviewed, DB-only reconciliation apply and rollback.

This is deliberately separate from :mod:`utils.path_reconciliation`: the
shared module remains a deterministic, read-only planner.  This service is
the current FastAPI application's narrowly scoped writer for the selected
library database.  It never opens, moves, renames, deletes, or tags media.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils import path_reconciliation

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import reconciliation_plan_service

_SUPPORTED_ACTIONS = {"update_path_reference", "mark_stale_processed_state_path"}
_APPLY_CONTRACT = "crateiq_reconciliation_db_only_v1"


class ReconciliationApplyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_id(snapshot: bytes) -> str:
    """Stable identity derived from one already-read plan artifact snapshot."""
    return "plan-" + hashlib.sha256(snapshot).hexdigest()


def _db_path(root: Path) -> Path:
    path = library_db_path(root)
    if not path.is_file():
        raise ReconciliationApplyError("library_not_initialized", "Configured library is not initialized.")
    return path


def _read_only_sqlite_uri(path: Path) -> str:
    """Build a correctly escaped SQLite read-only URI for a local path."""
    return path.resolve().as_uri() + "?mode=ro"


def _plan_path(root: Path, value: str | None) -> Path:
    if not value:
        raise ReconciliationApplyError("plan_path_required", "An exact saved reconciliation plan is required.")
    try:
        path = assert_path_under_root(value, root)
        path.relative_to(root / "logs" / "path_reconcile")
    except ValueError as exc:
        raise ReconciliationApplyError("plan_outside_selected_root", "Plan artifact must be under logs/path_reconcile in the selected root.") from exc
    if not path.is_file():
        raise ReconciliationApplyError("plan_not_found", "Selected reconciliation plan artifact was not found.")
    return path


def _load_plan(root: Path, value: str | None) -> tuple[Path, dict[str, Any], str]:
    """Read one immutable plan snapshot and derive all request state from it."""
    path = _plan_path(root, value)
    try:
        snapshot = path.read_bytes()
        plan = json.loads(snapshot.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationApplyError("plan_unreadable", "Selected reconciliation plan is not valid JSON.") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("planned_actions"), list):
        raise ReconciliationApplyError("unsupported_plan_schema", "Selected reconciliation plan has no supported planned_actions list.")
    try:
        plan_root = Path(str(plan.get("root") or "")).expanduser().resolve(strict=False)
    except (TypeError, ValueError) as exc:
        raise ReconciliationApplyError("plan_root_missing", "Selected reconciliation plan has no valid root.") from exc
    if plan_root != root:
        raise ReconciliationApplyError("plan_root_mismatch", "Selected plan root does not match the selected library root.")
    return path, plan, _plan_id(snapshot)


def _validation(path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    try:
        return reconciliation_plan_service.augment_with_cross_action_checks(
            path_reconciliation.path_reconcile_validate_plan_contents(plan, path)
        )
    except Exception as exc:
        raise ReconciliationApplyError("plan_validation_failed", f"Plan validation could not run: {exc}") from exc


def _action_records(path: Path, plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validation = _validation(path, plan)
    records = validation.get("validation_records", [])
    actions = plan["planned_actions"]
    result: dict[str, dict[str, Any]] = {}
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        action_id = path_reconciliation.path_reconcile_action_id(index, action)
        record = records[index] if index < len(records) else {"status": "invalid", "issues": ["validation_record_missing"]}
        result[action_id] = {"index": index, "action": action, "record": record}
    return result


def _eligible(root: Path, path: Path, plan: dict[str, Any], action_ids: list[str]) -> list[dict[str, Any]]:
    records = _action_records(path, plan)
    result: list[dict[str, Any]] = []
    for action_id in action_ids:
        item = records.get(action_id)
        if item is None:
            result.append({"action_id": action_id, "eligible": False, "blockers": ["action_id_not_in_plan"]})
            continue
        action = item["action"]
        record = item["record"]
        blockers: list[str] = []
        operation = str(action.get("action") or "")
        if operation not in _SUPPORTED_ACTIONS:
            blockers.append("unsupported_db_only_action")
        # Only the planner's exact stale-state report operation is permitted
        # to carry its intentional ``skipped/report_only`` result.  A plan
        # artifact must never use report_only to skip validation of a mutating
        # path-reference update.
        intentional_report_only = (
            operation == "mark_stale_processed_state_path"
            and record.get("status") == "skipped"
            and record.get("reason") == "report_only"
        )
        if record.get("status") != "valid" and not intentional_report_only:
            blockers.extend(record.get("issues") or [str(record.get("reason") or "plan_action_invalid")])
        if str(action.get("review_tier") or "") == "WEAK_MATCH":
            blockers.append("weak_match_rejected")
        if operation == "update_path_reference":
            try:
                old_path = assert_path_under_root(str(action.get("old_path") or ""), root)
                new_path = assert_path_under_root(str(action.get("new_path") or ""), root)
                if not new_path.is_file():
                    blockers.append("target_path_missing")
            except ValueError:
                blockers.append("path_outside_selected_root")
        elif operation == "mark_stale_processed_state_path":
            try:
                old_path = assert_path_under_root(str(action.get("old_path") or ""), root)
                replacement = assert_path_under_root(str(action.get("replacement_path") or ""), root)
                if old_path.exists():
                    blockers.append("old_path_still_exists")
                if not replacement.is_file():
                    blockers.append("replacement_path_missing")
                source_rows = action.get("source_rows")
                if not isinstance(source_rows, list) or not source_rows:
                    blockers.append("missing_processed_state_source_row")
            except ValueError:
                blockers.append("path_outside_selected_root")
        if not blockers:
            blockers.extend(_db_precondition_blockers(root, action, operation))
        result.append({
            "action_id": action_id,
            "index": item["index"],
            "operation_type": operation,
            "eligible": not blockers,
            "blockers": sorted(set(blockers)),
            "action": action,
        })
    return result


def _db_precondition_blockers(root: Path, action: dict[str, Any], operation: str) -> list[str]:
    """Read the exact expected rows through SQLite's read-only URI.

    Preview must expose collision/drift blockers without creating tables or
    any other database state.  Apply repeats these checks inside its writer
    transaction to close the time-of-check/time-of-use window.
    """
    db_path = _db_path(root)
    try:
        with sqlite3.connect(_read_only_sqlite_uri(db_path), uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if operation == "update_path_reference":
                old = str(assert_path_under_root(str(action.get("old_path") or ""), root))
                new = str(assert_path_under_root(str(action.get("new_path") or ""), root))
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone() is None:
                    return ["required_table_missing"]
                rows = conn.execute("SELECT id FROM tracks WHERE filepath=?", (old,)).fetchall()
                if len(rows) != 1:
                    return ["expected_tracks_row_mismatch"]
                if conn.execute("SELECT 1 FROM tracks WHERE filepath=? AND id != ?", (new, rows[0]["id"])).fetchone():
                    return ["target_canonical_row_collision"]
            elif operation == "mark_stale_processed_state_path":
                old = str(assert_path_under_root(str(action.get("old_path") or ""), root))
                replacement = str(assert_path_under_root(str(action.get("replacement_path") or ""), root))
                sources = [row for row in action.get("source_rows", []) if isinstance(row, dict) and row.get("table") == "processed_state" and row.get("id") is not None]
                if len(sources) != 1:
                    return ["ambiguous_processed_state_source_rows"]
                row = conn.execute("SELECT id, stage, filepath, status FROM processed_state WHERE id=?", (sources[0]["id"],)).fetchone()
                if row is None or row["filepath"] != old or row["stage"] != sources[0].get("stage") or str(row["status"] or "").lower() == "stale":
                    return ["processed_state_expected_before_mismatch"]
                if conn.execute("SELECT 1 FROM processed_state WHERE filepath=? AND status != 'stale' LIMIT 1", (replacement,)).fetchone() is None:
                    return ["replacement_not_active_processed_state"]
    except sqlite3.Error:
        return ["database_precondition_check_failed"]
    return []


def preview(plan_path: str | None, action_ids: list[str]) -> dict[str, Any]:
    root = selected_library_root()
    _db_path(root)
    if not action_ids:
        raise ReconciliationApplyError("action_selection_required", "Select one reviewed plan action.")
    if len(action_ids) != 1:
        raise ReconciliationApplyError("single_action_required", "Apply accepts exactly one reviewed action per request.")
    if len(set(action_ids)) != len(action_ids):
        raise ReconciliationApplyError("duplicate_action_id", "Each reviewed action may be selected only once.")
    path, plan, plan_id = _load_plan(root, plan_path)
    actions = _eligible(root, path, plan, action_ids)
    return {
        "plan_path": str(path), "plan_id": plan_id, "root": str(root),
        "db_only": True, "actions": actions,
        "message": "Updates CrateIQ database path references only. Music files are not moved.",
    }


def _sqlite_backup(source_db_path: Path, backup_path: Path) -> None:
    """Create a logical SQLite snapshot without copying WAL sidecar files.

    The caller holds the authoritative connection's ``BEGIN IMMEDIATE``
    reservation while this separate read-only source connection invokes
    SQLite's backup API.  That blocks a concurrent writer from committing
    between the protected operation's pre-state and its eventual mutation,
    while still allowing SQLite to include committed WAL-resident pages.
    """
    source_uri = _read_only_sqlite_uri(source_db_path)
    with sqlite3.connect(source_uri, uri=True) as source:
        with sqlite3.connect(backup_path) as destination:
            source.backup(destination)


def _verify_backup(backup_path: Path, expected_rows: dict[str, list[dict[str, Any]]]) -> str:
    """Prove the backup is readable and contains this operation's pre-state."""
    if not backup_path.is_file():
        raise ReconciliationApplyError("backup_missing", "SQLite reconciliation backup was not created.")
    try:
        with sqlite3.connect(_read_only_sqlite_uri(backup_path), uri=True) as backup:
            backup.row_factory = sqlite3.Row
            check = backup.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise ReconciliationApplyError("backup_verification_failed", "SQLite reconciliation backup integrity verification failed.")
            for table, rows in expected_rows.items():
                _require_table(backup, table)
                for expected in rows:
                    columns = list(expected.keys())
                    actual = backup.execute(
                        f"SELECT {', '.join(columns)} FROM {table} WHERE id=?",
                        (expected["id"],),
                    ).fetchone()
                    if actual is None or dict(actual) != expected:
                        raise ReconciliationApplyError("backup_verification_failed", "SQLite reconciliation backup does not contain the expected pre-mutation state.")
    except ReconciliationApplyError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ReconciliationApplyError("backup_verification_failed", "SQLite reconciliation backup could not be read and verified.") from exc
    return _sha256(backup_path)


def _safe_backup_path(root: Path, ledger_id: str) -> Path:
    """Return a root-contained backup destination without following escapes.

    The artifact itself does not exist yet, so containment is checked through
    the resolved existing parent both before and after directory creation.
    """
    requested_dir = root / "logs" / "reconciliation_backups"
    try:
        backup_dir = assert_path_under_root(requested_dir, root)
    except ValueError as exc:
        raise ReconciliationApplyError("backup_path_outside_selected_root", "Reconciliation backup directory escapes the selected library root.") from exc
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = assert_path_under_root(backup_dir, root)
        backup_path = assert_path_under_root(backup_dir / f"{ledger_id}_processed.db", root)
    except (OSError, ValueError) as exc:
        raise ReconciliationApplyError("backup_path_outside_selected_root", "Reconciliation backup directory is not safely contained by the selected library root.") from exc
    if backup_path.parent != backup_dir:
        raise ReconciliationApplyError("backup_path_outside_selected_root", "Reconciliation backup destination is not safely contained by the selected library root.")
    return backup_path


def _backup_locked(root: Path, db_path: Path, ledger_id: str, expected_rows: dict[str, list[dict[str, Any]]]) -> tuple[Path, str]:
    """Create and verify a unique logical backup while the writer lock is held."""
    backup_path = _safe_backup_path(root, ledger_id)
    if backup_path.exists():  # uuid makes this defensive branch practically unreachable
        raise ReconciliationApplyError("backup_collision", "Refusing to overwrite an existing reconciliation backup.")
    try:
        _sqlite_backup(db_path, backup_path)
    except (OSError, sqlite3.Error) as exc:
        raise ReconciliationApplyError("backup_creation_failed", "SQLite reconciliation backup could not be created.") from exc
    return backup_path, _verify_backup(backup_path, expected_rows)


def _require_table(conn: sqlite3.Connection, table: str) -> None:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        raise ReconciliationApplyError("required_table_missing", f"Required table is missing: {table}.")


def _insert_ledger(conn: sqlite3.Connection, *, ledger_id: str, root: Path, operation: str, old_path: str | None, new_path: str | None, tables: list[str], before: dict[str, Any], after: dict[str, Any], status: str, error: str | None = None) -> None:
    _require_table(conn, "reconciliation_ledger")
    conn.execute(
        "INSERT INTO reconciliation_ledger (ledger_id, created_at, root, operation_type, old_path, new_path, affected_tables, before_values_json, after_values_json, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ledger_id, _now(), str(root), operation, old_path, new_path, json.dumps(tables, sort_keys=True), json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True), status, error),
    )


def _require_current_target_file(root: Path, raw_path: str) -> str:
    """Revalidate the track target at the last safe point before mutation."""
    try:
        target = assert_path_under_root(raw_path, root)
    except ValueError as exc:
        raise ReconciliationApplyError("target_path_outside_selected_root", "Target path is outside the selected library root.") from exc
    if not target.is_file():
        raise ReconciliationApplyError("target_path_missing_at_apply", "Target path is no longer a regular file; no database reference was changed.")
    return str(target)


def _apply_one(root: Path, db_path: Path, plan_id: str, item: dict[str, Any]) -> dict[str, Any]:
    action = item["action"]
    operation = item["operation_type"]
    action_id = item["action_id"]
    ledger_id = f"recon-{uuid.uuid4().hex}"
    old_path = str(action.get("old_path") or "")
    new_path = str(action.get("new_path") or action.get("replacement_path") or "")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if operation == "update_path_reference":
            _require_table(conn, "tracks")
            old = str(assert_path_under_root(old_path, root))
            planned_new = str(assert_path_under_root(new_path, root))
            rows = conn.execute("SELECT id, filepath, filename FROM tracks WHERE filepath=?", (old,)).fetchall()
            if len(rows) != 1:
                raise ReconciliationApplyError("expected_tracks_row_mismatch", "Expected exactly one unchanged tracks row at the plan old path.")
            if conn.execute("SELECT 1 FROM tracks WHERE filepath=? AND id != ?", (planned_new, rows[0]["id"])).fetchone():
                raise ReconciliationApplyError("target_canonical_row_collision", "Target path is already used by another canonical tracks row.")
            before_rows = {"tracks": [dict(rows[0])]}
            backup_path, backup_hash = _backup_locked(root, db_path, ledger_id, before_rows)
            # This must remain immediately before the write: preview-time
            # checks cannot protect a target that disappears later.
            new = _require_current_target_file(root, new_path)
            if new != planned_new or conn.execute("SELECT 1 FROM tracks WHERE filepath=? AND id != ?", (new, rows[0]["id"])).fetchone():
                raise ReconciliationApplyError("target_canonical_row_collision", "Target path changed incompatibly before database mutation.")
            before: dict[str, Any] = {"apply_contract": _APPLY_CONTRACT, "verification_status": "verified", "plan_id": plan_id, "action_id": action_id, "backup_path": str(backup_path), "backup_sha256": backup_hash, "rows": before_rows}
            expected_after = {"id": rows[0]["id"], "filepath": new, "filename": Path(new).name}
            cursor = conn.execute("UPDATE tracks SET filepath=?, filename=? WHERE id=? AND filepath=? AND filename=?", (new, Path(new).name, rows[0]["id"], old, rows[0]["filename"]))
            if cursor.rowcount != 1:
                raise ReconciliationApplyError("tracks_update_count_mismatch", "Tracks row changed before reconciliation could apply it.")
            actual = conn.execute("SELECT id, filepath, filename FROM tracks WHERE id=?", (rows[0]["id"],)).fetchone()
            if actual is None or dict(actual) != expected_after:
                raise ReconciliationApplyError("tracks_postcondition_failed", "Tracks path postcondition verification failed.")
            after = {"apply_contract": _APPLY_CONTRACT, "verification_status": "verified", "plan_id": plan_id, "action_id": action_id, "backup_path": str(backup_path), "backup_sha256": backup_hash, "rows": {"tracks": [expected_after]}}
            tables = ["tracks"]
        else:
            _require_table(conn, "processed_state")
            old = str(assert_path_under_root(old_path, root))
            replacement = str(assert_path_under_root(new_path, root))
            sources = [row for row in action.get("source_rows", []) if isinstance(row, dict) and row.get("table") == "processed_state" and row.get("id") is not None]
            if len(sources) != 1:
                raise ReconciliationApplyError("ambiguous_processed_state_source_rows", "Plan must identify exactly one processed-state source row.")
            source = sources[0]
            row = conn.execute("SELECT id, stage, filepath, status, reason FROM processed_state WHERE id=?", (source["id"],)).fetchone()
            if row is None or row["filepath"] != old or row["stage"] != source.get("stage") or str(row["status"] or "").lower() == "stale":
                raise ReconciliationApplyError("processed_state_expected_before_mismatch", "Processed-state row no longer matches the reviewed plan.")
            if conn.execute("SELECT 1 FROM processed_state WHERE filepath=? AND status != 'stale' LIMIT 1", (replacement,)).fetchone() is None:
                raise ReconciliationApplyError("replacement_not_active_processed_state", "Replacement path is not an active processed-state reference.")
            before_rows = {"processed_state": [dict(row)]}
            backup_path, backup_hash = _backup_locked(root, db_path, ledger_id, before_rows)
            before = {"apply_contract": _APPLY_CONTRACT, "verification_status": "verified", "plan_id": plan_id, "action_id": action_id, "backup_path": str(backup_path), "backup_sha256": backup_hash, "rows": before_rows}
            reason = f"superseded_by_existing_path:{replacement}"
            cursor = conn.execute("UPDATE processed_state SET status=?, reason=? WHERE id=? AND filepath=? AND status != 'stale'", ("stale", reason, row["id"], old))
            if cursor.rowcount != 1:
                raise ReconciliationApplyError("processed_state_update_count_mismatch", "Processed-state row changed before reconciliation could apply it.")
            actual = conn.execute("SELECT id, stage, filepath, status, reason FROM processed_state WHERE id=?", (row["id"],)).fetchone()
            expected_after = {"id": row["id"], "stage": row["stage"], "filepath": old, "status": "stale", "reason": reason}
            if actual is None or dict(actual) != expected_after:
                raise ReconciliationApplyError("processed_state_postcondition_failed", "Processed-state postcondition verification failed.")
            after = {"apply_contract": _APPLY_CONTRACT, "verification_status": "verified", "plan_id": plan_id, "action_id": action_id, "backup_path": str(backup_path), "backup_sha256": backup_hash, "rows": {"processed_state": [expected_after]}}
            tables = ["processed_state"]
        # Ledger path columns are authoritative rollback provenance.  Keep
        # them in the exact canonical representation written/validated above,
        # never the raw spelling from the saved plan (which can be an in-root
        # symlink alias).
        ledger_new_path = new if operation == "update_path_reference" else replacement
        _insert_ledger(conn, ledger_id=ledger_id, root=root, operation=operation, old_path=old, new_path=ledger_new_path, tables=tables, before=before, after=after, status="applied")
        conn.commit()
    return {"action_id": action_id, "ledger_id": ledger_id, "status": "applied", "backup_path": str(backup_path), "backup_sha256": backup_hash, "verification_status": "verified"}


def apply(plan_path: str | None, plan_id: str, action_ids: list[str], *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ReconciliationApplyError("confirmation_required", "Set confirm=true to apply reviewed DB-only reconciliation actions.")
    root = selected_library_root()
    _db_path(root)
    if not action_ids:
        raise ReconciliationApplyError("action_selection_required", "Select one reviewed plan action.")
    if len(action_ids) != 1:
        raise ReconciliationApplyError("single_action_required", "Apply accepts exactly one reviewed action per request.")
    if len(set(action_ids)) != len(action_ids):
        raise ReconciliationApplyError("duplicate_action_id", "Each reviewed action may be selected only once.")
    path, plan, fresh_plan_id = _load_plan(root, plan_path)
    if plan_id != fresh_plan_id:
        raise ReconciliationApplyError("stale_plan_identity", "Plan content changed; reload and review the exact saved plan again.")
    actions = _eligible(root, path, plan, action_ids)
    ineligible = [item for item in actions if not item["eligible"]]
    if ineligible:
        raise ReconciliationApplyError("action_not_eligible", "Selected action is blocked: " + ", ".join(ineligible[0]["blockers"]))
    db_path = _db_path(root)
    results = [_apply_one(root, db_path, fresh_plan_id, actions[0])]
    return {"plan_id": plan_id, "db_only": True, "results": results, "message": "Database references updated and verified. Music files were not moved."}


def _ledger_row(conn: sqlite3.Connection, ledger_id: str) -> sqlite3.Row:
    _require_table(conn, "reconciliation_ledger")
    row = conn.execute("SELECT * FROM reconciliation_ledger WHERE ledger_id=?", (ledger_id,)).fetchone()
    if row is None:
        raise ReconciliationApplyError("ledger_not_found", "Reconciliation ledger entry was not found.")
    return row


def _rollback_already_recorded(conn: sqlite3.Connection, ledger_id: str) -> bool:
    """Avoid treating a completed rollback as a fresh operation."""
    for row in conn.execute("SELECT before_values_json FROM reconciliation_ledger WHERE operation_type LIKE 'rollback:%'"):
        try:
            if json.loads(row["before_values_json"] or "{}").get("rollback_of_ledger_id") == ledger_id:
                return True
        except json.JSONDecodeError:
            # A malformed unrelated historic row cannot authorize a rollback.
            continue
    return False


def _ledger_json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ReconciliationApplyError("ledger_state_unreadable", "Ledger before/after state is unreadable.") from exc
    if not isinstance(parsed, dict):
        raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry does not have the current DB-only rollback contract.")
    return parsed


def _contained_ledger_path(root: Path, raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry does not have a safe recorded path state.")
    try:
        return str(assert_path_under_root(raw, root))
    except ValueError as exc:
        raise ReconciliationApplyError("rollback_path_outside_selected_root", "Rollback refuses a recorded path outside the selected library root.") from exc


def _rollback_contract(original: sqlite3.Row, root: Path) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """Fail closed unless this is a verified Task 2 DB-only apply ledger."""
    operation = str(original["operation_type"] or "")
    if operation not in _SUPPORTED_ACTIONS:
        raise ReconciliationApplyError("ledger_operation_unsupported", "Ledger operation is not supported by DB-only reconciliation rollback.")
    before = _ledger_json(original["before_values_json"])
    after = _ledger_json(original["after_values_json"])
    shared = ("apply_contract", "verification_status", "plan_id", "action_id", "backup_path", "backup_sha256")
    if (
        any(not isinstance(before.get(key), str) or not before.get(key) for key in shared)
        or any(not isinstance(after.get(key), str) or not after.get(key) for key in shared)
        or before["apply_contract"] != _APPLY_CONTRACT
        or after["apply_contract"] != _APPLY_CONTRACT
        or before["verification_status"] != "verified"
        or after["verification_status"] != "verified"
        or before["plan_id"] != after["plan_id"]
        or before["action_id"] != after["action_id"]
        or before["backup_path"] != after["backup_path"]
        or before["backup_sha256"] != after["backup_sha256"]
    ):
        raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry does not have the current verified DB-only apply provenance.")

    before_rows = before.get("rows")
    after_rows = after.get("rows")
    expected_table = "tracks" if operation == "update_path_reference" else "processed_state"
    if (
        not isinstance(before_rows, dict)
        or not isinstance(after_rows, dict)
        or list(before_rows) != [expected_table]
        or list(after_rows) != [expected_table]
        or not isinstance(before_rows[expected_table], list)
        or not isinstance(after_rows[expected_table], list)
        or len(before_rows[expected_table]) != 1
        or len(after_rows[expected_table]) != 1
    ):
        raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry has malformed DB-only before/after state.")

    before_row = before_rows[expected_table][0]
    after_row = after_rows[expected_table][0]
    if not isinstance(before_row, dict) or not isinstance(after_row, dict):
        raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry has malformed DB-only row state.")
    if expected_table == "tracks":
        required = {"id", "filepath", "filename"}
        if set(before_row) != required or set(after_row) != required or before_row["id"] != after_row["id"]:
            raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry has malformed tracks rollback state.")
        old_path = _contained_ledger_path(root, before_row["filepath"])
        new_path = _contained_ledger_path(root, after_row["filepath"])
        if original["old_path"] != old_path or original["new_path"] != new_path:
            raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger path columns do not match its recorded DB-only state.")
        if before_row["filename"] != Path(old_path).name or after_row["filename"] != Path(new_path).name:
            raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger tracks filenames do not match their recorded paths.")
    else:
        required = {"id", "stage", "filepath", "status", "reason"}
        if set(before_row) != required or set(after_row) != required or before_row["id"] != after_row["id"] or before_row["stage"] != after_row["stage"]:
            raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger entry has malformed processed-state rollback state.")
        old_path = _contained_ledger_path(root, before_row["filepath"])
        after_path = _contained_ledger_path(root, after_row["filepath"])
        replacement = _contained_ledger_path(root, original["new_path"])
        if old_path != after_path or original["old_path"] != old_path or not replacement:
            raise ReconciliationApplyError("ledger_rollback_provenance_missing", "Ledger path columns do not match its recorded DB-only state.")
    return [expected_table], before, after


def rollback(ledger_id: str, *, confirm: bool) -> dict[str, Any]:
    if not confirm:
        raise ReconciliationApplyError("confirmation_required", "Set confirm=true to rollback a reviewed reconciliation action.")
    root = selected_library_root()
    db_path = _db_path(root)
    rollback_id = f"recon-rollback-{uuid.uuid4().hex}"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        original = _ledger_row(conn, ledger_id)
        if original["root"] != str(root) or original["status"] != "applied":
            raise ReconciliationApplyError("ledger_not_rollback_eligible", "Ledger entry is not an applied action for the selected root.")
        if _rollback_already_recorded(conn, ledger_id):
            raise ReconciliationApplyError("rollback_already_completed", "This reconciliation ledger entry has already been rolled back.")
        tables, before, after = _rollback_contract(original, root)
        for table in tables:
            _require_table(conn, table)
            current_rows = []
            for expected in after["rows"][table]:
                columns = list(expected.keys())
                current = conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id=?", (expected["id"],)).fetchone()
                if current is None or dict(current) != expected:
                    raise ReconciliationApplyError("rollback_state_drift", "Current database state no longer matches the recorded applied state.")
                current_rows.append(dict(current))
        backup_path, backup_hash = _backup_locked(root, db_path, rollback_id, after["rows"])
        restored_rows: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            current_rows = []
            for expected in after["rows"][table]:
                columns = list(expected.keys())
                current = conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id=?", (expected["id"],)).fetchone()
                if current is None or dict(current) != expected:
                    raise ReconciliationApplyError("rollback_state_drift", "Current database state no longer matches the recorded applied state.")
                current_rows.append(dict(current))
            for desired, current in zip(before["rows"][table], current_rows):
                if table == "tracks":
                    canonical_filepath = _contained_ledger_path(root, desired["filepath"])
                    restored = {**desired, "filepath": canonical_filepath, "filename": Path(canonical_filepath).name}
                    if conn.execute("SELECT 1 FROM tracks WHERE filepath=? AND id != ?", (canonical_filepath, desired["id"])).fetchone():
                        raise ReconciliationApplyError("rollback_target_collision", "Rollback target path is now used by another tracks row.")
                    cursor = conn.execute("UPDATE tracks SET filepath=?, filename=? WHERE id=? AND filepath=? AND filename=?", (canonical_filepath, Path(canonical_filepath).name, desired["id"], current["filepath"], current["filename"]))
                else:
                    restored = desired
                    cursor = conn.execute("UPDATE processed_state SET status=?, reason=? WHERE id=? AND filepath=? AND status=? AND reason=?", (desired["status"], desired["reason"], desired["id"], current["filepath"], current["status"], current["reason"]))
                if cursor.rowcount != 1:
                    raise ReconciliationApplyError("rollback_update_count_mismatch", "Rollback row changed before it could be restored.")
                verify = conn.execute(f"SELECT {', '.join(restored.keys())} FROM {table} WHERE id=?", (restored["id"],)).fetchone()
                if verify is None or dict(verify) != restored:
                    raise ReconciliationApplyError("rollback_postcondition_failed", "Rollback postcondition verification failed.")
                restored_rows.setdefault(table, []).append(restored)
        rollback_before = {"apply_contract": _APPLY_CONTRACT, "verification_status": "verified", "rollback_of_ledger_id": ledger_id, "backup_path": str(backup_path), "backup_sha256": backup_hash, "rows": after["rows"]}
        rollback_after = {"apply_contract": _APPLY_CONTRACT, "verification_status": "verified", "rollback_of_ledger_id": ledger_id, "backup_path": str(backup_path), "backup_sha256": backup_hash, "rows": restored_rows}
        rollback_old_path = _contained_ledger_path(root, original["new_path"])
        rollback_new_path = _contained_ledger_path(root, original["old_path"])
        _insert_ledger(conn, ledger_id=rollback_id, root=root, operation=f"rollback:{original['operation_type']}", old_path=rollback_old_path, new_path=rollback_new_path, tables=tables, before=rollback_before, after=rollback_after, status="rolled_back")
        conn.commit()
    return {"ledger_id": rollback_id, "rollback_of_ledger_id": ledger_id, "status": "rolled_back", "backup_path": str(backup_path), "backup_sha256": backup_hash, "verification_status": "verified"}
