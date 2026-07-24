"""
Local-runtime preflight checks for CrateIQ.

Read-only readiness evaluation. Every check inspects configuration, paths,
and binary availability only: nothing is created, modified, moved, renamed,
or deleted, no pipeline command is executed, and no job is started.

Status contract:

  check status   : pass | warn | fail
  overall status : ready | degraded | not_ready

  ready     — all checks pass
  degraded  — required checks pass, but one or more checks warn
              (or an optional check fails)
  not_ready — one or more required checks fail

Required checks: library_root, pipeline_db, pipeline_entrypoint.
Everything else (backend data dir, external binaries) is workflow-specific
and can only warn — a missing optional tool must never break readiness
reporting or the server.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import BACKEND_DATA_DIR, PIPELINE_PY
from .library_root import library_db_path, selected_library_root

# backend/app/core/ → ×3 → repository root
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Documented override: set to "1" to accept a library root that matches the
# unsafe-root list below. Intended only for unusual, deliberate setups.
ALLOW_UNSAFE_ROOT_ENV = "CRATEIQ_ALLOW_UNSAFE_ROOT"

# Roots that are almost certainly a misconfiguration when used directly as a
# library root. Matching is by exact resolved path — a library that merely
# lives *under* one of these (e.g. /home/user/Music/library) is unaffected.
def _unsafe_roots() -> set[Path]:
    candidates = {
        Path("/"),
        Path("/System"),
        Path("/Users"),
        Path("/home"),
        Path.home(),
        _REPO_ROOT,
    }
    return {p.resolve(strict=False) for p in candidates}


def redact_path(path: Path | str) -> str:
    """Collapse the home-directory prefix to '~' for display output."""
    text = str(path)
    home = str(Path.home())
    if home and home != "/" and text.startswith(home):
        return "~" + text[len(home):]
    return text


# ---------------------------------------------------------------------------
# Check result model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    status: str            # pass | warn | fail
    message: str
    required: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "required": self.required,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Individual checks (all read-only)
# ---------------------------------------------------------------------------

def _resolve_root() -> Tuple[Optional[Path], Optional[str]]:
    """Resolve the selected library root, returning (root, error_message)."""
    try:
        return selected_library_root(), None
    except Exception as exc:  # RuntimeError for unset/relative roots
        return None, str(exc)


def _check_library_root(root: Optional[Path], error: Optional[str]) -> CheckResult:
    name = "library_root"
    if root is None:
        return CheckResult(
            name, "fail",
            f"No usable library root is configured: {error}",
            required=True,
        )
    meta = {"root": redact_path(root)}
    if not root.exists():
        return CheckResult(
            name, "fail",
            "Configured library root does not exist.",
            required=True, metadata=meta,
        )
    if not root.is_dir():
        return CheckResult(
            name, "fail",
            "Configured library root is not a directory.",
            required=True, metadata=meta,
        )
    if not os.access(root, os.R_OK):
        return CheckResult(
            name, "fail",
            "Configured library root is not readable.",
            required=True, metadata=meta,
        )
    resolved = root.resolve(strict=False)
    if resolved in _unsafe_roots():
        if os.environ.get(ALLOW_UNSAFE_ROOT_ENV, "").strip() == "1":
            return CheckResult(
                name, "pass",
                "Library root matches a broad system root but was explicitly "
                f"allowed via {ALLOW_UNSAFE_ROOT_ENV}=1.",
                required=True, metadata=meta,
            )
        return CheckResult(
            name, "fail",
            "Configured library root is an unsafe broad root (filesystem root, "
            "system directory, home directory, or the repository itself). "
            f"Point CRATEIQ_LIBRARY_ROOT at a dedicated library folder, or set "
            f"{ALLOW_UNSAFE_ROOT_ENV}=1 to override deliberately.",
            required=True, metadata=meta,
        )
    return CheckResult(
        name, "pass",
        "Library root exists, is a directory, and is readable.",
        required=True, metadata=meta,
    )


def _check_pipeline_db(root: Optional[Path]) -> CheckResult:
    name = "pipeline_db"
    if root is None:
        return CheckResult(
            name, "fail",
            "Cannot evaluate the pipeline database without a usable library root.",
            required=True,
        )
    db_path = library_db_path(root)
    meta = {"db_path": redact_path(db_path)}
    try:
        db_path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return CheckResult(
            name, "fail",
            "Pipeline database path is not contained under the library root.",
            required=True, metadata=meta,
        )
    if not db_path.exists():
        return CheckResult(
            name, "warn",
            "Pipeline database not found. It is created by pipeline commands "
            "such as `build-tracks`; browsing will show an empty library "
            "until then.",
            required=True, metadata=meta,
        )
    return CheckResult(
        name, "pass",
        "Pipeline database is present under the library root.",
        required=True, metadata=meta,
    )


def _check_pipeline_entrypoint() -> CheckResult:
    name = "pipeline_entrypoint"
    if PIPELINE_PY.is_file():
        return CheckResult(
            name, "pass", "pipeline.py entrypoint found.", required=True,
        )
    return CheckResult(
        name, "fail",
        "pipeline.py entrypoint not found; job submission cannot work.",
        required=True,
    )


def _check_backend_data_dir() -> CheckResult:
    name = "backend_data_dir"
    meta = {"path": redact_path(BACKEND_DATA_DIR)}
    if BACKEND_DATA_DIR.is_dir():
        if os.access(BACKEND_DATA_DIR, os.W_OK):
            return CheckResult(
                name, "pass", "Backend data directory exists and is writable.",
                metadata=meta,
            )
        return CheckResult(
            name, "warn",
            "Backend data directory exists but is not writable; job tracking "
            "will fail.",
            metadata=meta,
        )
    parent = BACKEND_DATA_DIR.parent
    if parent.is_dir() and os.access(parent, os.W_OK):
        return CheckResult(
            name, "warn",
            "Backend data directory does not exist yet; it is created on the "
            "first backend start.",
            metadata=meta,
        )
    return CheckResult(
        name, "warn",
        "Backend data directory is missing and its parent is not writable; "
        "job tracking will fail.",
        metadata=meta,
    )


# name, env override, default candidates, what the binary enables
_BINARY_CHECKS: Tuple[Tuple[str, str, Tuple[str, ...], str], ...] = (
    ("ffprobe",       "FFPROBE_BIN",   ("ffprobe",),             "quality audit and metadata probing"),
    ("ffmpeg",        "FFMPEG_BIN",    ("ffmpeg",),              "audio conversion"),
    ("keyfinder-cli", "KEYFINDER_BIN", ("keyfinder-cli",),       "key analysis fallback for tracks without MIK data"),
    ("aubio",         "AUBIO_BIN",     ("aubio", "aubiotrack"),  "BPM analysis fallback for tracks without MIK data"),
    ("beet",          "BEET_BIN",      ("beet",),                "beets metadata import"),
    ("rmlint",        "RMLINT_BIN",    ("rmlint",),              "duplicate detection"),
    ("rsync",         "RSYNC_BIN",     ("rsync",),               "SSD sync preview and transfer"),
)


def _check_binary(
    name: str, env_name: str, defaults: Tuple[str, ...], purpose: str
) -> CheckResult:
    check_name = f"binary_{name.replace('-', '_')}"
    override = os.environ.get(env_name, "").strip()
    candidates = (override,) if override else defaults
    for candidate in candidates:
        if os.sep in candidate:
            path = Path(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return CheckResult(
                    check_name, "pass", f"{name} available (workflow: {purpose}).",
                    metadata={"resolved": redact_path(path), "source": env_name if override else "PATH"},
                )
        elif shutil.which(candidate):
            return CheckResult(
                check_name, "pass", f"{name} available (workflow: {purpose}).",
                metadata={"resolved": candidate, "source": env_name if override else "PATH"},
            )
    return CheckResult(
        check_name, "warn",
        f"{name} not found on PATH (override with {env_name}). "
        f"Workflows that need it will be unavailable: {purpose}.",
        metadata={"env_override": env_name},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_preflight() -> Dict[str, Any]:
    """
    Run all readiness checks and return a JSON-serializable report:

        {"status": "ready" | "degraded" | "not_ready", "checks": [...]}

    Never raises for expected misconfiguration — problems are reported as
    check results. Never mutates any file, database, or environment state.
    """
    root, root_error = _resolve_root()
    checks: List[CheckResult] = [
        _check_library_root(root, root_error),
        _check_pipeline_db(root),
        _check_pipeline_entrypoint(),
        _check_backend_data_dir(),
    ]
    for spec in _BINARY_CHECKS:
        checks.append(_check_binary(*spec))

    if any(c.required and c.status == "fail" for c in checks):
        overall = "not_ready"
    elif any(c.status != "pass" for c in checks):
        overall = "degraded"
    else:
        overall = "ready"

    return {"status": overall, "checks": [c.to_dict() for c in checks]}
