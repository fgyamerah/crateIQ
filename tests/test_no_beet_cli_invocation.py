"""
Static regression guard: CrateIQ production/tooling code must never invoke
the `beet` CLI binary via subprocess, os.system, shell, or similar.

The Beets *Python API* (import beets / beets.library.Item / beets.autotag /
MusicBrainzPlugin, as used by backend/app/services/musicbrainz_client.py)
remains fully allowed. Detection-only binary checks (e.g. shutil.which in
backend/app/core/preflight.py) are also allowed since they never execute the
binary. Only arguments passed to subprocess-invocation call sites are
inspected, so unrelated `import beets` statements or `shutil.which("beet")`
detection never trigger this check.

Coverage is two-layered:
  * Python: AST-walked for subprocess/os.system/create_subprocess_* call
    sites, across both the FastAPI backend and active shell/Python tooling
    under scripts/ (generate-docs.py, rollback.py, seed_demo_library.py, ...).
  * Shell: scripts/*.sh cannot be AST-parsed as Python, so they get a
    focused text scan for a bare `beet` command word outside comments.
    This is intentionally coarser than the Python AST check -- shell
    quoting/escaping is not fully modeled -- but it only needs to catch a
    literal `beet ...` invocation being reintroduced, not analyze arbitrary
    shell semantics.

Retired legacy scripts that installed/drove the beet CLI (setup.sh,
pipeline.sh) were archived as plain-text `.txt` snapshots under
docs/archive/legacy-runtime/ during the Legacy Architecture Cleanup; neither
scanner below touches docs/archive/ or `.txt` files, so those historical
snapshots can never trip this guard.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PRODUCTION_DIRS = ("backend/app", "modules", "utils", "intelligence", "ai", "scripts")
_PRODUCTION_FILES = ("pipeline.py", "config.py")

_SHELL_DIRS = ("scripts",)
_SHELL_BEET_WORD_RE = re.compile(r"\bbeet\b")

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


def _iter_shell_scripts():
    for rel in _SHELL_DIRS:
        base = REPO_ROOT / rel
        if base.is_dir():
            yield from sorted(base.rglob("*.sh"))


def _find_beet_cli_invocations_in_shell(path: Path) -> list[str]:
    findings: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return findings
    for lineno, line in enumerate(lines, start=1):
        code = line.split("#", 1)[0]
        if _SHELL_BEET_WORD_RE.search(code):
            try:
                label = path.relative_to(REPO_ROOT)
            except ValueError:
                label = path
            findings.append(f"{label}:{lineno}: {code.strip()[:200]}")
    return findings


def test_no_beet_cli_invocation_in_active_shell_scripts():
    findings: list[str] = []
    for path in _iter_shell_scripts():
        findings.extend(_find_beet_cli_invocations_in_shell(path))
    assert not findings, (
        "beet CLI reference found in an active shell script (forbidden — "
        "active tooling must never install/invoke the beet CLI):\n"
        + "\n".join(findings)
    )


def test_shell_guard_detects_a_planted_beet_cli_invocation(tmp_path):
    """Sanity-check the shell-text detector against a known-bad snippet,
    and confirm a merely commented-out mention is not a false positive."""
    planted = tmp_path / "planted.sh"
    planted.write_text(
        "#!/usr/bin/env bash\n"
        "# beet is mentioned here only in a comment -- must not trigger\n"
        "beet import --quiet /music/inbox\n",
        encoding="utf-8",
    )
    findings = _find_beet_cli_invocations_in_shell(planted)
    assert len(findings) == 1
    assert "beet import" in findings[0]


def test_shell_guard_ignores_archived_legacy_snapshots():
    """The retired scripts were archived as .txt precisely so this guard's
    scripts/*.sh glob can never see them again."""
    archive_dir = REPO_ROOT / "docs" / "archive" / "legacy-runtime"
    assert not list(archive_dir.glob("*.sh")), (
        "legacy-runtime archive must only contain .txt snapshots, not "
        "executable-looking .sh files"
    )
    assert any(archive_dir.glob("*.txt")), "expected archived .txt snapshots to exist"
