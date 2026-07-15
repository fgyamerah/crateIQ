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
