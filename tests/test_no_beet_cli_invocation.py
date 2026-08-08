"""
Static regression guard: CrateIQ production code must never invoke the
`beet` CLI binary via subprocess, os.system, shell, or similar.

The Beets *Python API* (import beets / beets.library.Item / beets.autotag /
MusicBrainzPlugin, as used by backend/app/services/musicbrainz_client.py)
remains fully allowed. Detection-only binary checks (e.g. shutil.which in
backend/app/core/preflight.py) are also allowed since they never execute the
binary. Only arguments passed to subprocess-invocation call sites are
inspected, so unrelated `import beets` statements or `shutil.which("beet")`
detection never trigger this check.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PRODUCTION_DIRS = ("backend/app", "modules", "utils", "intelligence", "ai")
_PRODUCTION_FILES = ("pipeline.py", "config.py")

_SUBPROCESS_CALL_TARGETS = {
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("os", "system"),
    ("os", "popen"),
    (None, "create_subprocess_exec"),
    (None, "create_subprocess_shell"),
}


def _iter_production_files():
    for rel in _PRODUCTION_DIRS:
        base = REPO_ROOT / rel
        if base.is_dir():
            yield from sorted(base.rglob("*.py"))
    for rel in _PRODUCTION_FILES:
        path = REPO_ROOT / rel
        if path.is_file():
            yield path


def _call_target(node: ast.Call) -> tuple[str | None, str] | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return (func.value.id, func.attr)
        return (None, func.attr)
    if isinstance(func, ast.Name):
        return (None, func.id)
    return None


def _find_beet_cli_invocations(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_target(node) not in _SUBPROCESS_CALL_TARGETS:
            continue
        source = ast.unparse(node)
        if "beet" in source.lower():
            try:
                label = path.relative_to(REPO_ROOT)
            except ValueError:
                label = path
            findings.append(f"{label}:{node.lineno}: {source[:200]}")
    return findings


def test_no_subprocess_beet_cli_invocation_in_production_code():
    findings: list[str] = []
    for path in _iter_production_files():
        findings.extend(_find_beet_cli_invocations(path))
    assert not findings, (
        "beet CLI invocation found in production code (forbidden — use the "
        "Beets Python API instead):\n" + "\n".join(findings)
    )


def test_guard_detects_a_planted_beet_cli_invocation(tmp_path):
    """Sanity-check the detector itself against a known-bad snippet."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import subprocess\n"
        "def run():\n"
        "    subprocess.run(['beet', 'import', '.'])\n",
        encoding="utf-8",
    )
    assert _find_beet_cli_invocations(planted)
