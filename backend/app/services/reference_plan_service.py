"""Stage 2 reference-artifact reconciliation plan proposal and validation.

The only write here is a bounded JSON plan below the selected root.  This is
deliberately separate from the DB-only path reconciliation plan/apply stack.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from . import reference_findings_service

_KIND = "reference_artifact_reconciliation_plan"
_VERSION = 1
_PLAN_DIR_PARTS = ("logs", "path_reconcile")
_EXECUTABLE = {"update_path_reference", "rehome_track_id"}
_NON_EXECUTABLE = {"regenerate_review_snapshot", "mark_durable_finding_unresolvable", "regenerate_export", "no_action_historical", "no_action_cache"}
_PATH_EVIDENCE = {
    "same_track_id_current_canonical_path",
    "same_track_id_different_path",
    "playlist_track_resolved_by_canonical_db",
    "historical_original_path_current_canonical_track",
}
_REHOME_EVIDENCE = {
    "proven_replacement",
    "same_path_now_owned_by_replacement_track_id",
    "provenance_trail_renamed_or_promoted",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _action_id(action: dict[str, Any]) -> str:
    stable = {key: value for key, value in action.items() if key != "action_id"}
    return "ref-action-" + hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()[:16]


def _plan_id(root: Path, actions: list[dict[str, Any]]) -> str:
    # The deterministic action list is the durable proposal content.  Keeping
    # timestamps and detector presentation fields out of this identity makes
    # it useful to future stale-plan checks without making it time-dependent.
    material = {"kind": _KIND, "version": _VERSION, "root": str(root), "actions": actions}
    return "ref-plan-" + hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()[:16]


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(str(path.resolve()))}?mode=ro", uri=True)


def _relative(value: Any, root: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return str(assert_path_under_root(value, root).relative_to(root))
    except ValueError:
        return None


def _safe_action(action: dict[str, Any], root: Path) -> dict[str, Any]:
    result = dict(action)
    for field in ("old_path", "new_path"):
        if field in result and isinstance(result[field], str):
            result[field] = _relative(result[field], root) or "<outside selected root>"
    return result


def _plan_dir(root: Path, *, create: bool) -> Path:
    directory = root.joinpath(*_PLAN_DIR_PARTS)
    # An existing symlinked directory is never acceptable for plan writes or reads.
    if directory.exists() and directory.is_symlink():
        raise ValueError("reference plan directory is an unsafe symlink")
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("reference plan directory is unavailable")
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("reference plan directory escapes selected root") from exc
    return resolved


def _finding_action(finding: dict[str, Any]) -> dict[str, Any] | None:
    category = finding.get("classification")
    artifact = finding.get("artifact_type")
    base = {
        "classification": category,
        "artifact_type": artifact,
        "artifact_identifier": finding.get("artifact_identifier"),
        "reference_field": finding.get("reference_field"),
        "finding_ids": [finding.get("finding_id")],
        "blockers": list(finding.get("blockers") or []),
        "confidence": finding.get("confidence"),
        "evidence": finding.get("evidence_source"),
        "executable": False,
    }
    if category == "A":
        return {**base, "action_type": "no_action_historical", "reversibility": "not_applicable"}
    if category == "E":
        return {**base, "action_type": "no_action_cache", "reversibility": "not_applicable"}
    if category != "B":
        return None
    candidate = finding.get("candidate_replacement")
    confidence = finding.get("confidence")
    if artifact in {"cue_point", "playlist_track", "queue_entry"}:
        blockers = list(base["blockers"])
        allowed = (
            confidence in {"HIGH", "MEDIUM"}
            and isinstance(candidate, str)
            and bool(candidate)
            and finding.get("evidence_source") in _PATH_EVIDENCE
            and not blockers
        )
        if artifact == "queue_entry" and "queue_schema_not_authorized_for_mutation" not in blockers:
            blockers.append("queue_schema_not_authorized_for_mutation")
        if not allowed and not blockers:
            blockers.append("missing_deterministic_replacement")
        return {**base, "action_type": "update_path_reference", "old_path": finding.get("stale_value"),
                "new_path": candidate, "blockers": blockers, "reversibility": "rollback_capable",
                "executable": allowed and artifact != "queue_entry"}
    if artifact in {"crate_track", "field_provenance"}:
        blockers = list(base["blockers"])
        allowed = (
            confidence in {"HIGH", "MEDIUM"}
            and isinstance(candidate, int)
            and not isinstance(candidate, bool)
            and finding.get("evidence_source") in _REHOME_EVIDENCE
            and not blockers
        )
        if not allowed and not blockers:
            blockers.append("missing_proven_replacement_track")
        return {**base, "action_type": "rehome_track_id", "old_track_id": finding.get("stale_value"),
                "new_track_id": candidate, "identity_evidence": finding.get("evidence_source"),
                "blockers": blockers, "reversibility": "rollback_capable",
                "executable": allowed}
    return {**base, "action_type": "no_action_historical", "blockers": base["blockers"] + ["unsupported_mutable_artifact_manual_review"], "reversibility": "not_applicable"}


def _category_c_actions(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    actions: list[dict[str, Any]] = []
    for finding in findings:
        if finding.get("classification") != "C":
            continue
        if finding.get("artifact_type") in {"quality_review_finding", "track_review"} and finding.get("recommended_disposition") == "mark_unresolvable":
            actions.append({"classification": "C", "action_type": "mark_durable_finding_unresolvable", "artifact_type": finding["artifact_type"],
                            "finding_ids": [finding["finding_id"]], "artifact_identifier": finding["artifact_identifier"],
                            "orphaned_track_id": finding["stale_value"], "reason": "orphaned_track_id", "blockers": [],
                            "executable": False, "reversibility": "regenerable"})
        else:
            grouped[(str(finding.get("artifact_type")), str(finding.get("artifact_owner")))].append(finding)
    for (artifact, owner), members in sorted(grouped.items()):
        actions.append({"classification": "C", "action_type": "regenerate_review_snapshot", "artifact_type": artifact, "artifact_owner": owner,
                        "finding_ids": sorted(item["finding_id"] for item in members), "reason": "snapshot_scoped_stale_references",
                        "stale_reference_count": len(members), "blockers": [], "executable": False,
                        "reversibility": "regenerable", "decision_preservation": "preserve_surviving_track_decisions"})
    return actions


def _category_d_actions(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    for finding in findings:
        if finding.get("classification") != "D":
            continue
        actions.append({"classification": "D", "action_type": "regenerate_export", "artifact_type": finding["artifact_type"],
                        "artifact_identifier": finding["artifact_identifier"], "finding_ids": [finding["finding_id"]],
                        "reason": "stale_export_path", "blockers": list(finding.get("blockers") or []),
                        "executable": False, "reversibility": "regenerable"})
    return actions


def _field_provenance_name(root: Path, identifier: Any) -> str | None:
    """Read only the original row identity needed for a future rehome check."""
    try:
        row_id = int(str(identifier).rsplit(":", 1)[1])
    except (TypeError, ValueError):
        return None
    db_path = library_db_path(root)
    if not db_path.is_file():
        return None
    try:
        with _ro(db_path) as conn:
            row = conn.execute("SELECT field_name FROM field_provenance WHERE id=? AND is_current=1", (row_id,)).fetchone()
            return str(row[0]) if row and isinstance(row[0], str) and row[0] else None
    except sqlite3.Error:
        return None


def _finalize(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for action in actions:
        action = dict(action)
        action["action_id"] = _action_id(action)
        finalized.append(action)
    return sorted(finalized, key=lambda item: item["action_id"])


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".reference-plan-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def propose_plan() -> dict[str, Any]:
    root = selected_library_root().resolve(strict=False)
    if not root.is_dir():
        raise ValueError("selected library root does not exist")
    report = reference_findings_service.get_reference_findings()
    findings = list(report.get("findings") or [])
    actions = [_finding_action(finding) for finding in findings]
    actions = [action for action in actions if action is not None] + _category_c_actions(findings) + _category_d_actions(findings)
    for action in actions:
        if action.get("action_type") == "rehome_track_id" and action.get("artifact_type") == "field_provenance":
            field_name = _field_provenance_name(root, action.get("artifact_identifier"))
            if field_name:
                action["field_name"] = field_name
            else:
                action["blockers"] = list(action.get("blockers") or []) + ["field_provenance_identity_incomplete"]
                action["executable"] = False
    actions = _finalize(actions)
    counts = Counter(str(item.get("classification")) for item in findings)
    by_artifact = Counter(str(item.get("artifact_type")) for item in findings)
    plan_id = _plan_id(root, actions)
    generated_at = _now()
    plan = {"plan_kind": _KIND, "schema_version": _VERSION, "plan_id": plan_id, "generated_at": generated_at,
            "root": str(root), "database": {"processed_db": str(library_db_path(root))},
            "finding_summary": {"total": len(findings), "by_classification": dict(sorted(counts.items())),
                                "by_artifact_type": dict(sorted(by_artifact.items())), "source_summary": report.get("summary", {})},
            "planned_actions": actions,
            "limitations": ["Plan only; no reference artifact, database, queue, cache, review state, media file, tag, BPM, key, or cue content was changed.",
                            "Only explicit deterministic Stage 1 candidates are eligible for future review; no replacement search was performed.",
                            "Confirmed Stage 4 supports only cue_points.filepath, set_playlist_tracks.filepath, current field_provenance.track_id, and manual_crate_tracks.track_id after preview; queue JSON/JSONL mutation remains unauthorized."],
            "message": "Plan only; no mutation has occurred."}
    directory = _plan_dir(root, create=True)
    filename = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}_reference_artifact_plan_{plan_id[-8:]}.json"
    path = directory / filename
    _atomic_json_write(path, plan)
    return {"plan_id": plan_id, "generated_at": generated_at, "plan_artifact": filename,
            "finding_summary": plan["finding_summary"], "planned_actions": [_safe_action(a, root) for a in actions],
            "limitations": plan["limitations"], "message": plan["message"]}


def latest_plan_path(root: Path) -> Path | None:
    directory = _plan_dir(root, create=False) if root.joinpath(*_PLAN_DIR_PARTS).exists() else None
    if directory is None:
        return None
    plans = sorted(path for path in directory.glob("*_reference_artifact_plan_*.json") if path.is_file() and not path.is_symlink())
    return plans[-1] if plans else None


def _safe_plan_path(value: str | Path, root: Path) -> Path:
    directory = _plan_dir(root, create=False)
    supplied = Path(value).expanduser()
    # Proposal responses expose only the artifact basename, so callers can
    # validate that exact opaque artifact reference without learning root.
    try:
        path = assert_path_under_root(directory / supplied if not supplied.is_absolute() and supplied.parent == Path(".") else supplied, root)
    except ValueError as exc:
        raise ValueError("reference plan artifact is outside selected root") from exc
    if path.is_symlink() or path.parent.resolve(strict=False) != directory or not path.is_file():
        raise ValueError("reference plan artifact is unsafe or outside its plan directory")
    return path


def _canonical_tracks(root: Path) -> tuple[dict[int, Path], set[Path], sqlite3.Connection | None]:
    db_path = library_db_path(root)
    if not db_path.is_file():
        return {}, set(), None
    conn = _ro(db_path)
    try:
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'").fetchone()
        if not table:
            return {}, set(), conn
        rows = conn.execute("SELECT id, filepath FROM tracks WHERE filepath IS NOT NULL").fetchall()
        tracks: dict[int, Path] = {}
        paths: set[Path] = set()
        for track_id, value in rows:
            try:
                resolved = assert_path_under_root(str(value), root)
            except ValueError:
                continue
            tracks[int(track_id)] = resolved
            paths.add(resolved)
        return tracks, paths, conn
    except Exception:
        conn.close()
        raise


def _record(action: Any, issue: str | None, *, status: str) -> dict[str, Any]:
    return {"action_id": action.get("action_id") if isinstance(action, dict) else None, "action_type": action.get("action_type") if isinstance(action, dict) else None,
            "status": status, "reason": issue, "issues": [] if issue is None else [issue]}


def _action_structure_issue(action: dict[str, Any]) -> str | None:
    """Return the first missing or unsafe plan-action contract field."""
    classification = action.get("classification")
    kind = action.get("action_type")
    if classification not in {"A", "B", "C", "D", "E"}:
        return "invalid_or_missing_classification"
    allowed_by_classification = {
        "A": {"no_action_historical"},
        "B": _EXECUTABLE,
        "C": {"regenerate_review_snapshot", "mark_durable_finding_unresolvable"},
        "D": {"regenerate_export"},
        "E": {"no_action_cache"},
    }
    if kind not in allowed_by_classification[classification]:
        return "action_type_not_allowed_for_classification"
    if not isinstance(action.get("artifact_type"), str) or not action["artifact_type"]:
        return "missing_artifact_type"
    if not isinstance(action.get("blockers"), list):
        return "missing_blockers"
    if not isinstance(action.get("executable"), bool):
        return "missing_executable_flag"
    if not isinstance(action.get("reversibility"), str) or not action["reversibility"]:
        return "missing_reversibility"
    if classification in {"A", "C", "D", "E"} and action["executable"]:
        return "non_mutation_classification_cannot_be_executable"
    if kind in _EXECUTABLE:
        for field in ("artifact_identifier", "reference_field", "confidence", "evidence"):
            if not isinstance(action.get(field), str) or not action[field]:
                return f"missing_{field}"
        if action["confidence"] not in {"HIGH", "MEDIUM"}:
            return "invalid_confidence"
    if kind == "update_path_reference":
        if not isinstance(action.get("old_path"), str) or not isinstance(action.get("new_path"), str):
            return "missing_path_target"
    if kind == "rehome_track_id":
        if not isinstance(action.get("old_track_id"), int) or isinstance(action["old_track_id"], bool):
            return "missing_old_track_id"
        if not isinstance(action.get("new_track_id"), int) or isinstance(action["new_track_id"], bool):
            return "missing_new_track_id"
        if action["old_track_id"] == action["new_track_id"]:
            return "track_rehome_requires_distinct_ids"
        if not isinstance(action.get("identity_evidence"), str) or action["identity_evidence"] not in _REHOME_EVIDENCE:
            return "missing_or_unproven_identity_evidence"
    if kind == "regenerate_review_snapshot":
        if not isinstance(action.get("reason"), str) or not action["reason"]:
            return "missing_reason"
        if not isinstance(action.get("stale_reference_count"), int) or action["stale_reference_count"] < 1:
            return "missing_stale_reference_count"
    if kind == "mark_durable_finding_unresolvable":
        if not isinstance(action.get("orphaned_track_id"), int) or isinstance(action["orphaned_track_id"], bool):
            return "missing_orphaned_track_id"
        if not isinstance(action.get("reason"), str) or not action["reason"]:
            return "missing_reason"
    if kind == "regenerate_export":
        if not isinstance(action.get("artifact_identifier"), str) or not action["artifact_identifier"]:
            return "missing_artifact_identifier"
        if not isinstance(action.get("reason"), str) or not action["reason"]:
            return "missing_reason"
    return None


def validate_plan_contents(plan: Any, root: Path) -> dict[str, Any]:
    """Validate one already-loaded plan object without reading its artifact.

    Stage 3 preview uses this entry point so structural validation and its
    revalidation result derive from the same immutable plan byte snapshot.
    """
    if not isinstance(plan, dict) or plan.get("plan_kind") != _KIND or plan.get("schema_version") != _VERSION:
        raise ValueError("not a reference-artifact reconciliation plan")
    if not isinstance(plan.get("generated_at"), str) or not isinstance(plan.get("plan_id"), str):
        raise ValueError("reference plan missing required identity fields")
    database = plan.get("database")
    if not isinstance(database, dict) or not isinstance(database.get("processed_db"), str):
        raise ValueError("reference plan missing database identity")
    if Path(str(plan.get("root") or "")).resolve(strict=False) != root:
        raise ValueError("plan root does not match selected root")
    try:
        if assert_path_under_root(database["processed_db"], root) != library_db_path(root):
            raise ValueError("reference plan database does not match selected root")
    except ValueError as exc:
        raise ValueError("reference plan database does not match selected root") from exc
    actions = plan.get("planned_actions")
    if not isinstance(actions, list):
        raise ValueError("reference plan missing planned_actions list")
    if plan.get("plan_id") != _plan_id(root, actions):
        raise ValueError("reference plan identity does not match its contents")
    tracks, canonical_paths, conn = _canonical_tracks(root)
    records: list[dict[str, Any]] = []
    target_groups: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    action_ids = [item.get("action_id") for item in actions if isinstance(item, dict)]
    duplicate_ids = {item for item, count in Counter(action_ids).items() if item and count > 1}
    try:
        for action in actions:
            if not isinstance(action, dict):
                records.append(_record(action, "action_not_object", status="invalid")); continue
            if action.get("action_id") in duplicate_ids:
                records.append(_record(action, "duplicate_action_id", status="invalid")); continue
            if action.get("action_id") != _action_id(action):
                records.append(_record(action, "action_id_mismatch", status="invalid")); continue
            structure_issue = _action_structure_issue(action)
            if structure_issue:
                records.append(_record(action, structure_issue, status="invalid")); continue
            kind = action.get("action_type")
            if kind not in _EXECUTABLE | _NON_EXECUTABLE:
                records.append(_record(action, "unsupported_action_type", status="invalid")); continue
            if kind in _NON_EXECUTABLE:
                records.append(_record(action, None, status="non_executable")); continue
            blockers = action["blockers"]
            if blockers or action.get("executable") is not True:
                records.append(_record(action, blockers[0] if blockers else "not_executable", status="invalid")); continue
            if kind == "update_path_reference":
                if action.get("artifact_type") not in {"cue_point", "playlist_track", "queue_entry"}:
                    records.append(_record(action, "invalid_artifact_for_path_update", status="invalid")); continue
                if action.get("artifact_type") == "queue_entry":
                    records.append(_record(action, "queue_schema_not_authorized_for_mutation", status="invalid")); continue
                new = _relative(action.get("new_path"), root)
                old = _relative(action.get("old_path"), root)
                if new is None or old is None:
                    records.append(_record(action, "path_outside_selected_root", status="invalid")); continue
                target = assert_path_under_root(new, root)
                if not target.is_file() or target not in canonical_paths:
                    records.append(_record(action, "target_path_missing_or_not_canonical", status="invalid")); continue
                target_groups[(str(action.get("artifact_type")), str(action.get("artifact_identifier")), str(action.get("reference_field")))].add(str(target))
                records.append(_record(action, None, status="valid")); continue
            if kind == "rehome_track_id":
                if action.get("artifact_type") not in {"crate_track", "field_provenance"}:
                    records.append(_record(action, "invalid_artifact_for_track_rehome", status="invalid")); continue
                target_id = action.get("new_track_id")
                if not isinstance(target_id, int) or target_id not in tracks or not tracks[target_id].is_file():
                    records.append(_record(action, "target_track_missing_or_unsafe", status="invalid")); continue
                if action.get("artifact_type") == "field_provenance":
                    field_name = action.get("field_name")
                    if not isinstance(field_name, str) or not field_name or conn is None:
                        records.append(_record(action, "field_provenance_identity_incomplete", status="invalid")); continue
                    try:
                        exists = conn.execute("SELECT 1 FROM field_provenance WHERE track_id=? AND field_name=? AND is_current=1 LIMIT 1", (target_id, field_name)).fetchone()
                    except sqlite3.Error:
                        records.append(_record(action, "field_provenance_identity_incomplete", status="invalid")); continue
                    if exists:
                        records.append(_record(action, "target_current_provenance_collision", status="invalid")); continue
                target_groups[(str(action.get("artifact_type")), str(action.get("artifact_identifier")), str(action.get("reference_field")))].add(str(target_id))
                records.append(_record(action, None, status="valid"))
    finally:
        if conn is not None:
            conn.close()
    for group, targets in target_groups.items():
        if len(targets) > 1:
            for record in records:
                action = next((item for item in actions if isinstance(item, dict) and item.get("action_id") == record.get("action_id")), {})
                key = (str(action.get("artifact_type")), str(action.get("artifact_identifier")), str(action.get("reference_field")))
                if key == group and record["status"] == "valid":
                    record.update(status="invalid", reason="ambiguous", issues=["ambiguous"])
    reasons = Counter(record["reason"] for record in records if record["reason"])
    return {"generated_at": _now(), "plan_id": plan.get("plan_id"), "total_actions": len(actions),
            "valid_actions": sum(record["status"] == "valid" for record in records), "invalid_actions": sum(record["status"] == "invalid" for record in records),
            "non_executable_actions": sum(record["status"] == "non_executable" for record in records), "reasons": dict(sorted(reasons.items())),
            "validation_records": records, "message": "Validation only; no mutation has occurred."}


def validate_plan(plan_path: str | Path) -> dict[str, Any]:
    root = selected_library_root().resolve(strict=False)
    path = _safe_plan_path(plan_path, root)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("reference plan json is unreadable") from exc
    result = validate_plan_contents(plan, root)
    return {**result, "plan_artifact": path.name}
