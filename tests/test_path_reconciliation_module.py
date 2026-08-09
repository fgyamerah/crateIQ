"""Regression coverage for the neutral utils/path_reconciliation.py extraction.

Proves:
  * importing the module has no side effects (no directories/DB created, no
    FastAPI/pipeline CLI behavior triggered, no /music dependency);
  * pipeline.py's compatibility wrappers (_path_audit_report,
    _path_reconcile_plan, _path_reconcile_validate_plan,
    _path_reconcile_latest_plan_path) produce output equivalent to calling
    the neutral module directly, so there is exactly one implementation.
"""
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

import pipeline
from utils import path_reconciliation


def _strip_generated_at(payload: dict) -> dict:
    payload = dict(payload)
    payload.pop("generated_at", None)
    return payload


def _create_db(root: Path) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY, filepath TEXT, "
        "filename TEXT, status TEXT, filesize_bytes INTEGER)"
    )
    present = root / "Library" / "Artist - Title.mp3"
    present.parent.mkdir(parents=True, exist_ok=True)
    present.write_bytes(b"x" * 1000)
    missing = root / "Library" / "Missing - Track.mp3"
    conn.execute(
        "INSERT INTO tracks (filepath, filename, status, filesize_bytes) VALUES (?, ?, ?, ?)",
        (str(present), present.name, "ok", 1000),
    )
    conn.execute(
        "INSERT INTO tracks (filepath, filename, status, filesize_bytes) VALUES (?, ?, ?, ?)",
        (str(missing), missing.name, "ok", 500),
    )
    conn.commit()
    conn.close()
    return db_path


def test_import_has_no_side_effects():
    """Importing the module in a fresh subprocess must not touch the filesystem,
    require a music root, initialize FastAPI, or execute pipeline CLI behavior."""
    result = subprocess.run(
        [sys.executable, "-c", "from utils import path_reconciliation"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_module_has_no_fastapi_or_pipeline_cli_imports():
    import ast

    source = (Path(__file__).resolve().parents[1] / "utils" / "path_reconciliation.py").read_text()
    tree = ast.parse(source)
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "fastapi" not in imported_roots
    assert "pipeline" not in imported_roots


def test_pipeline_wrapper_matches_neutral_module_directly(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    db_path = _create_db(root)

    via_pipeline = pipeline._path_audit_report(root, db_path, include_orphan_candidates=True)
    via_neutral = path_reconciliation.path_audit_report(root, db_path, include_orphan_candidates=True)
    assert _strip_generated_at(via_pipeline) == _strip_generated_at(via_neutral)

    plan_via_pipeline = pipeline._path_reconcile_plan(root, via_pipeline)
    plan_via_neutral = path_reconciliation.path_reconcile_plan(root, via_neutral)
    assert _strip_generated_at(plan_via_pipeline) == _strip_generated_at(plan_via_neutral)

    plan_dir = root / "logs" / "path_reconcile"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir / "20260101_path_reconcile_plan.json"
    plan_path.write_text(json.dumps(plan_via_pipeline), encoding="utf-8")

    assert pipeline._path_reconcile_latest_plan_path(root) == path_reconciliation.path_reconcile_latest_plan_path(root) == plan_path

    validate_via_pipeline = pipeline._path_reconcile_validate_plan(plan_path)
    validate_via_neutral = path_reconciliation.path_reconcile_validate_plan(plan_path)
    assert _strip_generated_at(validate_via_pipeline) == _strip_generated_at(validate_via_neutral)


def test_neutral_module_audio_extensions_default_matches_config():
    import config

    assert path_reconciliation.DEFAULT_AUDIO_EXTENSIONS == config.AUDIO_EXTENSIONS
    assert path_reconciliation.DEFAULT_MAINTENANCE_SKIP_DIRS == config.MAINTENANCE_SKIP_DIRS
