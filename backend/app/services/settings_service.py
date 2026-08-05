"""Safe, library-scoped settings diagnostics and small local preferences."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.crate_db import crates_db_path
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from ..core.preflight import redact_path, run_preflight

_DEFAULT_PREFERENCES = {"default_export_path_mode": "filename"}
_VALID_PATH_MODES = {"filename", "relative", "absolute"}


def _settings_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "app_settings.json", root)


def _load_preferences(root: Path) -> dict[str, str]:
    path = _settings_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(_DEFAULT_PREFERENCES)
    mode = raw.get("default_export_path_mode") if isinstance(raw, dict) else None
    return {"default_export_path_mode": mode if mode in _VALID_PATH_MODES else "filename"}


def _is_demo_root(root: Path) -> bool:
    repo_root = Path(__file__).resolve().parents[3]
    return root.resolve(strict=False) == (repo_root / ".run" / "demo-library").resolve(strict=False)


def _tool_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    tools = []
    for check in report["checks"]:
        if not check["name"].startswith("binary_"):
            continue
        metadata = check.get("metadata", {})
        tools.append({
            "name": check["name"].removeprefix("binary_").replace("_", "-"),
            "status": check["status"],
            "message": check["message"],
            "source": metadata.get("source") or metadata.get("env_override") or "PATH",
            "resolved": metadata.get("resolved"),
        })
    return tools


def get_settings() -> dict[str, Any]:
    root = selected_library_root()
    report = run_preflight()
    return {
        "library": {
            "mode": "demo" if _is_demo_root(root) else "configured",
            "library_root": redact_path(root),
            "processed_db": redact_path(library_db_path(root)),
            "manual_crates_db": redact_path(crates_db_path()),
            "exports_root": redact_path(assert_path_under_root(root / "exports", root)),
            "restart_required": False,
            "readiness_status": report["status"],
        },
        "tools": _tool_rows(report),
        "safety": {
            "mixed_in_key_authoritative": True,
            "missing_data_only_analysis": True,
            "no_automatic_file_or_tag_modification": True,
            "no_live_serato_writes": True,
            "no_live_rekordbox_database_writes": True,
            "preview_before_export_or_apply": True,
        },
        "preferences": _load_preferences(root),
    }


def update_preferences(default_export_path_mode: str | None) -> dict[str, Any]:
    if default_export_path_mode is None:
        return get_settings()
    if default_export_path_mode not in _VALID_PATH_MODES:
        raise ValueError("default_export_path_mode must be filename, relative, or absolute")
    root = selected_library_root()
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"default_export_path_mode": default_export_path_mode}, indent=2) + "\n",
        encoding="utf-8",
    )
    return get_settings()
