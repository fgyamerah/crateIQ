"""
Selected library-root helpers for the read-only backend API.

The backend should operate against one explicitly selected library root.
CrateIQ uses CRATEIQ_LIBRARY_ROOT as the preferred environment variable.
CRATEMINDAI_LIBRARY_ROOT remains a deprecated compatibility fallback so
existing local setups do not silently change their selected library.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Shared with settings_service's legacy library-root validation so the two
# entry points (existing-root-only legacy setup, new-folder-eligible
# managed workspace setup) can never drift on what counts as an unsafe path.
_FORBIDDEN_NEW_ROOT_PATHS: tuple[Path, ...] = (
    Path("/"), Path("/etc"), Path("/usr"), Path("/bin"), Path("/sbin"),
    Path("/proc"), Path("/sys"), Path("/dev"), Path("/run"),
    _REPO_ROOT, _REPO_ROOT / ".git", _REPO_ROOT / ".venv",
    _REPO_ROOT / "node_modules", _REPO_ROOT / ".run",
)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def assert_safe_new_root_path(value: str) -> Path:
    """
    Validate a user-supplied path is safe to use as a library/workspace root,
    without requiring it to already exist. Raises ValueError with a clear,
    user-facing message. Resolves symlinks (via Path.resolve) so a symlink
    that escapes into a forbidden/system location is still rejected.
    """
    text = value.strip()
    if not text:
        raise ValueError("library_root is required")
    if any(ch in text for ch in ("\x00", "\n", "\r")):
        raise ValueError("library_root contains an unsafe character")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ValueError("library_root must be an absolute path")
    resolved = candidate.resolve(strict=False)
    if any(
        resolved == forbidden.resolve(strict=False)
        if forbidden == Path("/")
        else _is_within(resolved, forbidden.resolve(strict=False))
        for forbidden in _FORBIDDEN_NEW_ROOT_PATHS
    ):
        raise ValueError("library_root cannot be a system or CrateIQ runtime directory")
    return resolved


def _load_toolkit_music_root() -> Path | None:
    """
    Load MUSIC_ROOT from the toolkit config without importing pipeline.py.

    This is used only as a fallback when neither explicit environment variable
    is present.
    """
    try:
        toolkit_root = Path(__file__).resolve().parents[3]
        spec = importlib.util.spec_from_file_location(
            "_tk_config_for_backend_root", str(toolkit_root / "config.py")
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        value = getattr(mod, "MUSIC_ROOT", None)
        return Path(value).expanduser() if value else None
    except Exception:
        return None


def selected_library_root() -> Path:
    """
    Return the active library root for the backend API.

    Preference order:
    1. CRATEIQ_LIBRARY_ROOT
    2. CRATEMINDAI_LIBRARY_ROOT (deprecated compatibility alias)
    3. Toolkit MUSIC_ROOT from config.py
    """
    env_name = "CRATEIQ_LIBRARY_ROOT"
    raw = os.environ.get(env_name)
    if raw is None:
        env_name = "CRATEMINDAI_LIBRARY_ROOT"
        raw = os.environ.get(env_name)
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise RuntimeError(
                f"{env_name} must be absolute: {root}"
            )
    else:
        root = _load_toolkit_music_root()
    if root is None:
        raise RuntimeError("No safe library root is configured.")
    resolved = root.resolve(strict=False)
    if not resolved.is_absolute():
        raise RuntimeError(f"Library root must be absolute: {resolved}")
    return resolved


def library_db_path(root: Path | None = None) -> Path:
    root_path = (root or selected_library_root()).resolve(strict=False)
    return root_path / "logs" / "processed.db"


def library_audit_dir(root: Path | None = None) -> Path:
    root_path = (root or selected_library_root()).resolve(strict=False)
    return root_path / "logs" / "path_audit"


def enrichment_queue_path(root: Path | None = None) -> Path:
    root_path = (root or selected_library_root()).resolve(strict=False)
    return root_path / "data" / "intelligence" / "enrichment_review_queue.jsonl"


def enrichment_review_state_path(root: Path | None = None) -> Path:
    root_path = (root or selected_library_root()).resolve(strict=False)
    return root_path / "data" / "intelligence" / "enrichment_review_state.json"


def assert_path_under_root(path: Path | str, root: Path | str) -> Path:
    """
    Resolve path and verify it stays under root.

    Relative paths are interpreted relative to root so benign relative paths
    can be audited, while '../' traversal resolves outside root and is rejected.
    """
    root_path = Path(root).expanduser().resolve(strict=False)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root_path / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(
            f"path outside selected root: {resolved} not under {root_path}"
        ) from exc
    return resolved
