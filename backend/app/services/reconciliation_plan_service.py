"""Plan-first library reconciliation: propose, persist, and validate — never apply.

Reuses the neutral path-audit/path-reconcile planner in
``utils/path_reconciliation.py`` (also used by the legacy ``pipeline.py``
CLI) and the existing plan-artifact location/format so ``validate-plan``
(already wired) can read a plan this module proposes without any format
change. This module adds no apply/execute capability; it only proposes a
plan (writing the same JSON artifact the CLI already writes) and strengthens
plan validation with cross-action checks (target collisions, ambiguous
candidates) that the per-action validator does not perform.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import path_reconciliation

from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root

_PATH_FIELDS = ("old_path", "new_path", "filepath", "replacement_path", "queue_file")


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


def _safe_action(action: dict[str, Any], root: Path) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in action.items():
        if key in _PATH_FIELDS and isinstance(value, str) and value:
            safe[key] = _relative(value, root)
        else:
            safe[key] = value
    return safe


def propose_plan() -> dict[str, Any]:
    """Detect + propose a reconciliation plan and persist it as a JSON artifact.

    This writes only the plan artifact under ``logs/path_reconcile/`` (the
    same location and filename pattern the CLI already uses), never audio
    files, tags, or ``processed.db``. Apply is out of scope for this cycle.
    """
    root = selected_library_root()
    db_path = library_db_path(root)
    if not db_path.is_file():
        raise ValueError("Configured library is not initialized.")

    audit = path_reconciliation.path_audit_report(root, db_path, include_orphan_candidates=True)
    plan = path_reconciliation.path_reconcile_plan(root, audit)

    log_dir = root / "logs" / "path_reconcile"
    log_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    json_path = log_dir / f"{day}_path_reconcile_plan.json"
    json_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    safe_actions = [_safe_action(action, root) for action in plan.get("planned_actions", [])]
    return {
        "generated_at": plan.get("generated_at"),
        "plan_artifact": json_path.name,
        "apply_supported": False,
        "planned_action_summary": plan.get("planned_action_summary", {}),
        "planned_actions": safe_actions,
        "audit_summary": plan.get("audit_summary", {}),
        "limitations": plan.get("limitations", []),
        "message": (
            "Plan proposal only. Review each action, then validate the latest plan before any future apply "
            "workflow is considered. No file, tag, or database write was made by this request beyond the plan "
            "artifact itself."
        ),
    }


def augment_with_cross_action_checks(result: dict[str, Any]) -> dict[str, Any]:
    """Add plan-wide collision/ambiguity checks the per-action validator can't see.

    Only ``update_path_reference`` actions that already validated individually
    can be downgraded here; nothing here can turn an invalid action valid.
    """
    records: list[dict[str, Any]] = result.get("validation_records", [])
    new_path_owners: dict[str, list[int]] = {}
    old_path_targets: dict[str, set[str]] = {}
    for idx, record in enumerate(records):
        if record.get("action_type") != "update_path_reference":
            continue
        action = record.get("action") or {}
        new_path = str(action.get("new_path") or "").strip()
        old_path = str(action.get("old_path") or "").strip()
        if new_path:
            new_path_owners.setdefault(new_path, []).append(idx)
        if old_path and new_path:
            old_path_targets.setdefault(old_path, set()).add(new_path)

    newly_invalid = 0
    extra_reason_counts: dict[str, int] = {}

    def _flag(idx: int, issue: str) -> None:
        nonlocal newly_invalid
        record = records[idx]
        if issue not in record["issues"]:
            record["issues"].append(issue)
        extra_reason_counts[issue] = extra_reason_counts.get(issue, 0) + 1
        if record["status"] == "valid":
            record["status"] = "invalid"
            record["reason"] = issue
            newly_invalid += 1

    for new_path, indices in new_path_owners.items():
        if len(indices) > 1:
            for idx in indices:
                _flag(idx, "target_path_collision")

    for old_path, new_paths in old_path_targets.items():
        if len(new_paths) > 1:
            for idx, record in enumerate(records):
                action = record.get("action") or {}
                if record.get("action_type") == "update_path_reference" and str(action.get("old_path") or "").strip() == old_path:
                    _flag(idx, "ambiguous_candidate_for_old_path")

    if newly_invalid:
        result["valid_actions"] = max(0, result.get("valid_actions", 0) - newly_invalid)
        result["invalid_actions"] = result.get("invalid_actions", 0) + newly_invalid
    for issue, count in extra_reason_counts.items():
        result["reasons"][issue] = result.get("reasons", {}).get(issue, 0) + count
    result["reasons"] = dict(sorted(result.get("reasons", {}).items(), key=lambda item: (-item[1], item[0])))
    return result
