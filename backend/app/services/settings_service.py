"""Safe, library-scoped settings diagnostics and small local preferences."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core.crate_db import crates_db_path
from ..core.library_root import assert_path_under_root, library_db_path, selected_library_root
from ..core.preflight import redact_path, run_preflight

_DEFAULT_ANALYSIS_PREFERENCES = {
    "analyze_bpm": False,
    "analyze_key": False,
    "use_mik_when_present": True,
    "preserve_existing_bpm_key_cues": True,
    "missing_data_only": True,
    "use_external_tools": True,
}
_DEFAULT_PREFERENCES = {
    "default_export_path_mode": "filename",
    "analysis": _DEFAULT_ANALYSIS_PREFERENCES,
}
_VALID_PATH_MODES = {"filename", "relative", "absolute"}
_EDITABLE_ANALYSIS_PREFERENCES = {"analyze_bpm", "analyze_key", "use_external_tools"}
_LOCKED_ANALYSIS_PREFERENCES = {
    "use_mik_when_present",
    "preserve_existing_bpm_key_cues",
    "missing_data_only",
}
_REPO_ROOT = Path(__file__).resolve().parents[3]
# This is deliberately repo-scoped rather than library-scoped: saving a new
# root must not write into the library that has not yet been selected.
LOCAL_ENV_PATH = _REPO_ROOT / ".run" / "local" / "crateiq.env"
RESTART_COMMAND = "scripts/crateiq-local-services.sh stop && scripts/crateiq-local-services.sh start"


def _settings_path(root: Path) -> Path:
    return assert_path_under_root(root / "logs" / "app_settings.json", root)


def _load_preferences(root: Path) -> dict[str, Any]:
    path = _settings_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "default_export_path_mode": _DEFAULT_PREFERENCES["default_export_path_mode"],
            "analysis": dict(_DEFAULT_ANALYSIS_PREFERENCES),
        }
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("default_export_path_mode")
    raw_analysis = raw.get("analysis")
    analysis = dict(_DEFAULT_ANALYSIS_PREFERENCES)
    if isinstance(raw_analysis, dict):
        for key in _DEFAULT_ANALYSIS_PREFERENCES:
            value = raw_analysis.get(key)
            if isinstance(value, bool):
                analysis[key] = value
    # Locked safety policies must never be disabled by stale or hand-edited
    # local preferences. Keep them explicit in the response rather than
    # silently treating a false value as a supported choice.
    for key in _LOCKED_ANALYSIS_PREFERENCES:
        analysis[key] = True
    return {
        "default_export_path_mode": mode if mode in _VALID_PATH_MODES else "filename",
        "analysis": analysis,
    }


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


def get_capabilities(
    report: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return action-level availability without invoking optional tools.

    Preflight's overall status remains useful for diagnostics, but it is not a
    product capability contract: an unavailable optional executable must not
    make import, browsing, crates, or exports unavailable.
    """
    report = report or run_preflight()
    preferences = preferences or _load_preferences(selected_library_root())
    analysis_preferences = preferences["analysis"]
    tools = {item["name"]: item for item in _tool_rows(report)}

    def has_tool(name: str) -> bool:
        return tools.get(name, {}).get("status") == "pass"

    def optional_tool_capability(
        *,
        tool: str,
        purpose: str,
        message: str,
        enabled: bool = False,
    ) -> dict[str, Any]:
        available = has_tool(tool)
        return {
            "available": available,
            "status": "available" if available else "missing",
            "required_tool": tool,
            "purpose": purpose,
            "message": message if available else f"{tool} is not available. {message}",
            "enabled": enabled,
            # Availability means setup is complete. The action remains clearly
            # deferred until its dedicated DB-only runner is implemented.
            "action_state": "coming_soon" if available else "setup_required",
        }

    missing_quality_tools = [name for name in ("ffprobe", "ffmpeg") if not has_tool(name)]
    quality_available = not missing_quality_tools
    return {
        "core": {
            "library_import": {
                "available": True,
                "status": "available",
                "purpose": "Initialize, scan, and import a local library index.",
                "message": "Import without analysis is always available.",
                "action_state": "ready",
            },
            "browse": {
                "available": True,
                "status": "available",
                "purpose": "Browse and search the local CrateIQ index.",
                "message": "Browsing does not require optional analysis tools.",
                "action_state": "ready",
            },
            "manual_crates": {
                "available": True,
                "status": "available",
                "purpose": "Create and order local Manual Crates.",
                "message": "Manual Crates use local CrateIQ state only.",
                "action_state": "ready",
            },
            "smart_crates": {
                "available": True,
                "status": "available",
                "purpose": "Suggest crates from metadata already in the local index.",
                "message": "Smart Crates do not require new BPM or key analysis.",
                "action_state": "ready",
            },
            "exports": {
                "available": True,
                "status": "available",
                "purpose": "Create portable and staged crate exports.",
                "message": "Saved crate exports do not require optional analysis tools.",
                "action_state": "ready",
            },
        },
        "analysis": {
            "mixed_in_key_coverage": {
                "available": False,
                "status": "unknown",
                "required_source": "Existing Mixed In Key or compatible metadata",
                "purpose": "Review source-aware BPM, key, Camelot, and cue coverage.",
                "message": "MIK-compatible values are preserved when present. Coverage review is not implemented yet.",
                "enabled": True,
                "locked": True,
                "action_state": "coming_soon",
            },
            "bpm_analysis": optional_tool_capability(
                tool="aubio",
                purpose="Analyze only tracks missing trusted BPM.",
                message="Install or configure aubio to enable optional BPM analysis. Import remains available without it.",
                enabled=analysis_preferences["analyze_bpm"],
            ),
            "key_analysis": optional_tool_capability(
                tool="keyfinder-cli",
                purpose="Analyze only tracks missing trusted key or Camelot values.",
                message="Install or configure keyfinder-cli to enable optional key analysis. Import remains available without it.",
                enabled=analysis_preferences["analyze_key"],
            ),
            "beets_enrichment": optional_tool_capability(
                tool="beet",
                purpose="Run the explicit Beets import/enrichment workflow.",
                message="Install or configure beet to use the optional Beets workflow.",
            ),
            "duplicate_detection": optional_tool_capability(
                tool="rmlint",
                purpose="Run the explicit duplicate detection workflow.",
                message="Install or configure rmlint to scan for duplicates. No files are removed automatically.",
            ),
            "audio_quality_probe": {
                "available": quality_available,
                "status": "available" if quality_available else "missing",
                "required_tools": ["ffprobe", "ffmpeg"],
                "purpose": "Probe audio quality and support conversion validation.",
                "message": (
                    "ffprobe and ffmpeg are available for optional audio quality workflows."
                    if quality_available
                    else f"Missing {', '.join(missing_quality_tools)}. Install or configure the missing tool(s) for optional audio probing."
                ),
                "enabled": analysis_preferences["use_external_tools"],
                "action_state": "coming_soon" if quality_available else "setup_required",
            },
        },
        "policies": {
            "preserve_mik_values": True,
            "preserve_existing_bpm_key_cues": True,
            "missing_data_only": True,
            "no_tag_writes": True,
        },
    }


def get_settings() -> dict[str, Any]:
    root = selected_library_root()
    pending_root = _pending_library_root()
    report = run_preflight()
    preferences = _load_preferences(root)
    return {
        "library": {
            "mode": "demo" if _is_demo_root(root) else "configured",
            "library_root": redact_path(root),
            "processed_db": redact_path(library_db_path(root)),
            "manual_crates_db": redact_path(crates_db_path()),
            "exports_root": redact_path(assert_path_under_root(root / "exports", root)),
            "library_initialized": library_db_path(root).is_file(),
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
        "preferences": preferences,
        "capabilities": get_capabilities(report, preferences),
    }


def update_preferences(
    default_export_path_mode: str | None,
    analysis_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = selected_library_root()
    preferences = _load_preferences(root)
    if default_export_path_mode is not None:
        if default_export_path_mode not in _VALID_PATH_MODES:
            raise ValueError("default_export_path_mode must be filename, relative, or absolute")
        preferences["default_export_path_mode"] = default_export_path_mode
    if analysis_updates:
        locked_updates = [key for key in _LOCKED_ANALYSIS_PREFERENCES if analysis_updates.get(key) is False]
        if locked_updates:
            raise ValueError(f"{locked_updates[0]} is a locked safety policy and must remain enabled")
        for key, value in analysis_updates.items():
            if key in _EDITABLE_ANALYSIS_PREFERENCES:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} must be true or false")
                preferences["analysis"][key] = value
            elif key in _LOCKED_ANALYSIS_PREFERENCES and value is not True:
                raise ValueError(f"{key} is a locked safety policy and must remain enabled")
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(preferences, indent=2) + "\n",
        encoding="utf-8",
    )
    return get_settings()


def update_library_root(value: str) -> dict[str, Any]:
    _write_pending_library_root(_validated_library_root(value))
    return get_settings()
