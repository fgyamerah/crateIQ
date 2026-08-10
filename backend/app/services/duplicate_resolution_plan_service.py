"""Plan-first duplicate resolution: propose a reversible-resolution plan, never apply it.

Separate from ``duplicate_review_service``. Existing duplicate decisions
(`keep`/`ignore`/`review_later`/`unresolved`) remain the authoritative human
review state and are never mutated here. This module only *reads* the latest
saved snapshot plus its recorded decisions and derives a deterministic,
reproducible plan describing what each track's resolution status is and what
would need to be proven before any future controlled apply phase could act on
it. It never deletes, moves, renames, quarantines, or writes a tag/file, and
there is no apply/execute endpoint anywhere in this module.

Actionability requires unambiguous evidence: exactly one explicit keeper, all
other members reviewed (not unresolved/review_later), every member path
verified under the selected managed root with a file that currently exists,
and grouping evidence based on verified content identity rather than
filename similarity alone. Any ambiguity blocks the whole group rather than
guessing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.library_root import assert_path_under_root, selected_library_root
from . import duplicate_review_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

_SAFETY = [
    "plan_only", "no_apply_endpoint", "no_delete", "no_move", "no_rename",
    "no_quarantine", "no_tag_writes", "no_track_metadata_writes",
]

_RESTORE_PRECONDITIONS = [
    "A hash-verified backup must exist outside the scanned tree before any mutation begins.",
    "The original file must remain fully recoverable until the operator confirms the resolution outcome.",
    "An operation ledger row must record before/after identity and status before the operation is considered complete.",
]

_DESTINATION_STRATEGY = "reversible_hold_pending_future_implementation"
_LEDGER_LINKAGE = (
    "not_yet_implemented -- no duplicate-resolution apply/ledger exists yet; "
    "see docs/architecture/DUPLICATE_RESOLUTION_SPEC.md"
)


def _empty_plan(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": _now(), "latest_preview_at": review.get("latest_preview_at"),
        "source": review.get("source"), "apply_supported": False, "groups": [],
        "summary": {
            "groups": 0, "ready_groups": 0, "blocked_groups": 0, "keep": 0,
            "candidate_for_reversible_resolution": 0, "no_action": 0, "review_required": 0,
        },
        "safety": _SAFETY, "warnings": review.get("warnings", []),
        "message": review.get("message") or "No duplicate preview is saved yet. There is nothing to plan.",
    }


def _identity_evidence(checksum_prefix: str | None) -> dict[str, Any]:
    if checksum_prefix:
        return {
            "type": "checksum_prefix", "value": checksum_prefix,
            "note": "12-character rmlint checksum prefix only; not a verified full content hash.",
        }
    return {"type": "none", "value": None, "note": "No checksum evidence was retained from the preview."}


def _observed_stat(path: Path) -> tuple[dict[str, Any], bool]:
    """Return (observed stat, is_file) for the current on-disk state, never raising."""
    try:
        if not path.is_file():
            return {"size_bytes": None, "mtime_ns": None}, False
        stat = path.stat()
        return {"size_bytes": stat.st_size, "mtime_ns": str(stat.st_mtime_ns)}, True
    except OSError:
        return {"size_bytes": None, "mtime_ns": None}, False


def _item_path_state(item: dict[str, Any], root: Path) -> tuple[dict[str, Any], bool, list[str]]:
    """Re-verify current root containment and on-disk existence; never trust the snapshot alone."""
    blockers: list[str] = []
    relative_path = item.get("relative_path")
    if not relative_path:
        blockers.append("relative_path_unavailable")
        return {"size_bytes": None, "mtime_ns": None}, False, blockers
    try:
        resolved = assert_path_under_root(relative_path, root)
    except ValueError:
        blockers.append("path_outside_managed_root")
        return {"size_bytes": None, "mtime_ns": None}, False, blockers
    observed, is_file = _observed_stat(resolved)
    if not is_file:
        blockers.append("missing_file_current_state")
    return observed, is_file, blockers


def _plan_group(group: dict[str, Any], root: Path) -> dict[str, Any]:
    items = group["items"]
    content_identity_ok = group.get("match_basis") == "content_checksum"
    keepers = [item for item in items if item["decision"] == "keep"]
    unreviewed = any(item["decision"] in ("unresolved", "review_later") for item in items)

    item_states: dict[int, tuple[dict[str, Any], bool, list[str]]] = {
        item["track_id"]: _item_path_state(item, root) for item in items
    }
    any_path_issue = any(state[2] for state in item_states.values())

    group_blockers: list[str] = []
    if not content_identity_ok:
        group_blockers.append("evidence_insufficient_for_resolution")
    else:
        if unreviewed:
            group_blockers.append("unreviewed_members_present")
        if not keepers:
            group_blockers.append("no_keeper_selected")
        elif len(keepers) > 1:
            group_blockers.append("multiple_keepers_selected")
        if any_path_issue:
            group_blockers.append("member_path_or_file_state_invalid")

    status = "blocked" if group_blockers else "ready"
    keeper_track_id = keepers[0]["track_id"] if status == "ready" and len(keepers) == 1 else None

    plan_items = []
    for item in items:
        observed, is_file, path_blockers = item_states[item["track_id"]]
        action_id = f"{group['group_id']}:{item['track_id']}"
        if not content_identity_ok:
            action = "no_action"
            blockers = list(group_blockers)
        elif group_blockers:
            action = "review_required"
            blockers = list(group_blockers) + path_blockers
        elif item["track_id"] == keeper_track_id:
            action = "keep"
            blockers = []
        else:
            action = "candidate_for_reversible_resolution"
            blockers = []

        execution_requirements = None
        if action == "candidate_for_reversible_resolution":
            execution_requirements = {
                "source_relative_path": item["relative_path"],
                "track_id": item["track_id"],
                "identity_evidence": _identity_evidence(group.get("checksum_prefix")),
                "observed_at_plan_time": observed,
                "proposed_destination_strategy": _DESTINATION_STRATEGY,
                "collision_check_required": True,
                "backup_required": True,
                "restore_preconditions": list(_RESTORE_PRECONDITIONS),
                "operation_ledger_linkage": _LEDGER_LINKAGE,
            }

        plan_items.append({
            "action_id": action_id, "track_id": item["track_id"], "filename": item["filename"],
            "relative_path": item["relative_path"], "action": action, "blockers": blockers,
            "execution_requirements": execution_requirements,
        })

    return {
        "group_id": group["group_id"], "status": status, "blockers": group_blockers,
        "keeper_track_id": keeper_track_id, "match_basis": group.get("match_basis", "unknown"),
        "checksum_prefix": group.get("checksum_prefix"), "items": plan_items,
    }


def get_plan() -> dict[str, Any]:
    """Derive a deterministic, reproducible resolution plan from the latest saved review state.

    Read-only: this recomputes on-disk containment/existence checks but makes
    no write of any kind (no file, tag, track-metadata, or plan-artifact write).
    """
    review = duplicate_review_service.get_review()
    if not review.get("groups"):
        return _empty_plan(review)

    root = selected_library_root()
    groups = [_plan_group(group, root) for group in review["groups"]]

    summary = {
        "groups": len(groups),
        "ready_groups": sum(1 for group in groups if group["status"] == "ready"),
        "blocked_groups": sum(1 for group in groups if group["status"] == "blocked"),
        "keep": 0, "candidate_for_reversible_resolution": 0, "no_action": 0, "review_required": 0,
    }
    for group in groups:
        for item in group["items"]:
            summary[item["action"]] += 1

    return {
        "generated_at": _now(), "latest_preview_at": review.get("latest_preview_at"),
        "source": review.get("source"), "apply_supported": False, "groups": groups, "summary": summary,
        "safety": _SAFETY, "warnings": review.get("warnings", []),
        "message": (
            "Plan preview only -- no files changed. No delete, move, rename, quarantine, tag, or track-metadata "
            "write exists in this workflow, and there is no apply/execute endpoint. Ready groups still require a "
            "future, separately reviewed controlled apply phase before any file action can occur."
        ),
    }
