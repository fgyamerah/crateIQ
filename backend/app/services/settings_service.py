"""Safe, library-scoped settings diagnostics and small local preferences."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core.crate_db import crates_db_path
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from ..core.preflight import redact_path, run_preflight

_DEFAULT_PREFERENCES = {"default_export_path_mode": "filename"}
_VALID_PATH_MODES = {"filename", "relative", "absolute"}
_REPO_ROOT = Path(__file__).resolve().parents[3]
# This is deliberately repo-scoped rather than library-scoped: saving a new
# root must not write into the library that has not yet been selected.
LOCAL_ENV_PATH = _REPO_ROOT / ".run" / "local" / "crateiq.env"
RESTART_COMMAND = "scripts/crateiq-local-services.sh stop && scripts/crateiq-local-services.sh start"


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
    return root.resolve(strict=False) == (_REPO_ROOT / ".run" / "demo-library").resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _forbidden_library_roots() -> tuple[Path, ...]:
    return (
        Path("/"), Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
        Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"),
        _REPO_ROOT, _REPO_ROOT / ".git", _REPO_ROOT / ".venv",
        _REPO_ROOT / "node_modules", _REPO_ROOT / ".run",
    )


def _validated_library_root(value: str) -> Path:
    candidate_text = value.strip()
    if not candidate_text:
        raise ValueError("library_root is required")
    if "\x00" in candidate_text or "\n" in candidate_text or "\r" in candidate_text:
        raise ValueError("library_root contains an unsafe character")
    candidate = Path(candidate_text).expanduser()
    if not candidate.is_absolute():
        raise ValueError("library_root must be an absolute path")
    resolved = candidate.resolve(strict=False)
    if any(
        resolved == forbidden.resolve(strict=False)
        if forbidden == Path("/")
        else _is_within(resolved, forbidden.resolve(strict=False))
        for forbidden in _forbidden_library_roots()
    ):
        raise ValueError("library_root cannot be a system or CrateIQ runtime directory")
    if not resolved.exists():
        raise ValueError("library_root does not exist")
    if not resolved.is_dir():
        raise ValueError("library_root must be a directory")
    if not os.access(resolved, os.R_OK):
        raise ValueError("library_root is not readable")
    return resolved


def validate_library_root(value: str) -> dict[str, Any]:
    root = _validated_library_root(value)
    return {
        "library_root": redact_path(root),
        "valid": True,
        "message": "Library root is valid. Saving it creates a pending change that requires restart.",
    }


def _pending_library_root() -> Path | None:
    try:
        for line in LOCAL_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("CRATEIQ_LIBRARY_ROOT="):
                value = line.partition("=")[2]
                return _validated_library_root(value)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return None


def _write_pending_library_root(root: Path) -> None:
    LOCAL_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LOCAL_ENV_PATH.with_suffix(".tmp")
    temporary.write_text(
        "# Managed by CrateIQ Settings. This file is local-only and contains no secrets.\n"
        f"CRATEIQ_LIBRARY_ROOT={root}\n",
        encoding="utf-8",
    )
    temporary.replace(LOCAL_ENV_PATH)


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
    pending_root = _pending_library_root()
    report = run_preflight()
    return {
        "library": {
            "mode": "demo" if _is_demo_root(root) else "configured",
            "library_root": redact_path(root),
            "processed_db": redact_path(library_db_path(root)),
            "manual_crates_db": redact_path(crates_db_path()),
            "exports_root": redact_path(assert_path_under_root(root / "exports", root)),
            "pending_library_root": redact_path(pending_root) if pending_root else None,
            "pending_library_initialized": bool(pending_root and (pending_root / "logs" / "processed.db").is_file()),
            "restart_required": pending_root is not None and pending_root != root.resolve(strict=False),
            "restart_command": RESTART_COMMAND,
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


def update_library_root(value: str) -> dict[str, Any]:
    _write_pending_library_root(_validated_library_root(value))
    return get_settings()
