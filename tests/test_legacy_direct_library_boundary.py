"""
Phase 7 regression guard: the boundary between the current, workspace-
selected library model (backend/app/core/library_root.selected_library_root)
and the surviving "Legacy Direct Library" compatibility mode (pipeline.py's
own config.MUSIC_ROOT-rooted CLI, and the explicit library_root override
accepted by library_setup_service/settings routes for that same legacy
flow — see Settings -> Advanced -> "Legacy Direct Library" in the frontend).

See docs/architecture/LEGACY_DIRECT_LIBRARY_BOUNDARY.md for the full map;
this file proves the boundary in code rather than in prose.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Current backend code must never import the legacy pipeline's own root/config
# modules directly. It may only ever launch pipeline.py as a subprocess
# (backend/app/services/toolkit_runner.py), which inherits the process
# environment rather than importing legacy Python state in-process.
_FORBIDDEN_BACKEND_IMPORT_ROOTS = ("pipeline", "config", "db")


def _iter_backend_files():
    yield from sorted((REPO_ROOT / "backend" / "app").rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_current_backend_never_imports_legacy_pipeline_modules():
    """
    Current backend code must reach a library only through
    core.library_root.selected_library_root(), never by importing the
    legacy pipeline.py / config.py / db.py modules in-process. Legacy
    command execution is confined to toolkit_runner's subprocess boundary.
    """
    findings: list[str] = []
    for path in _iter_backend_files():
        for root in _imported_roots(path):
            if root in _FORBIDDEN_BACKEND_IMPORT_ROOTS:
                findings.append(f"{path.relative_to(REPO_ROOT)}: imports {root!r}")
    assert not findings, (
        "current backend/app must not import legacy pipeline.py/config.py/"
        "db.py modules directly:\n" + "\n".join(findings)
    )


# Any /music literal is either the intentional legacy default in config.py
# (documented, not imported by current backend — see above) or one of the
# two backend-side display/query-matching helpers that translate between the
# canonical selected root and paths the pipeline DB may have historically
# stored using the /music symlink. Any new occurrence must be justified and
# added here explicitly, not silently introduced as a new default root.
_JUSTIFIED_MUSIC_LITERAL_FILES = {
    "backend/app/api/routes/library.py",
    "backend/app/services/track_service.py",
}


def test_no_unexplained_music_literal_in_current_backend():
    findings: list[str] = []
    for path in _iter_backend_files():
        rel = str(path.relative_to(REPO_ROOT))
        if '"/music"' not in path.read_text(encoding="utf-8"):
            continue
        if rel not in _JUSTIFIED_MUSIC_LITERAL_FILES:
            findings.append(rel)
    assert not findings, (
        "unexplained \"/music\" literal found in current backend code "
        "(only justified as a display/query-matching helper in "
        f"{sorted(_JUSTIFIED_MUSIC_LITERAL_FILES)}): {findings}"
    )


def test_launch_script_bridges_dj_music_root_to_selected_library_root():
    """
    pipeline.py subcommands launched as backend job subprocesses
    (toolkit_runner.build_command) receive no --root argument; they resolve
    their own root from config.MUSIC_ROOT (env DJ_MUSIC_ROOT), inherited
    from the backend process's environment. scripts/crateiq-local-services.sh
    is the only place that starts the backend for real use, and it must keep
    exporting DJ_MUSIC_ROOT equal to CRATEIQ_LIBRARY_ROOT in that command, or
    backend-launched legacy jobs would silently fall back to pipeline.py's
    own /music default instead of the selected library.
    """
    script = (REPO_ROOT / "scripts" / "crateiq-local-services.sh").read_text(encoding="utf-8")
    backend_start_lines = [
        line for line in script.splitlines()
        if "uvicorn backend.app.main:app" in line
    ]
    # There must be exactly one code path that starts the real backend
    # (uvicorn backend.app.main:app): the one that bridges DJ_MUSIC_ROOT.
    # A second, unbridged start path (removed in Phase 7 as dead code with
    # zero callers -- _crateiq_start) would silently reintroduce the
    # ambiguous middle path this boundary rules out.
    assert len(backend_start_lines) == 1, (
        f"expected exactly one backend start command, found {len(backend_start_lines)}: "
        f"{backend_start_lines}"
    )
    line = backend_start_lines[0]
    assert re.search(r'CRATEIQ_LIBRARY_ROOT="\$CRATEIQ_LIBRARY_ROOT"', line), line
    assert re.search(r'DJ_MUSIC_ROOT="\$CRATEIQ_LIBRARY_ROOT"', line), (
        "backend start command must export DJ_MUSIC_ROOT=$CRATEIQ_LIBRARY_ROOT "
        f"so legacy subprocess jobs do not fall back to /music: {line}"
    )


def test_toolkit_runner_never_passes_root_flag_to_legacy_commands():
    """
    Locks in the documented boundary: toolkit_runner's allowlisted value
    flags never include --root. If a --root flag is ever added here, the
    DJ_MUSIC_ROOT environment bridge this test suite also guards becomes
    dead code, and the two must be reconciled deliberately, not silently.
    """
    from backend.app.services import toolkit_runner

    assert "--root" not in toolkit_runner._ALLOWED_VALUE_FLAGS
    assert "--root" not in toolkit_runner._ALLOWED_BOOL_FLAGS
