"""
Regression coverage for pipeline.py validate-docs's music-root decoupling.

Phase 5 dependency isolation removed the incidental _setup_logging() call
from run_validate_docs (it only reads COMMANDS.txt and the in-process doc
registry via print(); no log.*() call happens on that path). Previously,
_setup_logging() unconditionally created config.LOGS_DIR under MUSIC_ROOT,
which defaults to /music and raised PermissionError for any user who cannot
create /music. This test proves bare `validate-docs --strict` no longer
needs DJ_MUSIC_ROOT/CRATEIQ_LIBRARY_ROOT set.
"""
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_bare_validate_docs_strict_does_not_require_music_root():
    env = {"PATH": "/usr/bin:/bin", "HOME": str(_REPO_ROOT)}
    result = subprocess.run(
        [sys.executable, "pipeline.py", "validate-docs", "--strict"],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PermissionError" not in result.stderr
    assert "/music" not in result.stderr
    assert "validate-docs: OK" in result.stdout
