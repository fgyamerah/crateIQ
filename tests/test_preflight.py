"""
Tests for the local-runtime preflight and the /api/runtime/readiness endpoint.

All tests use temporary roots (the shared conftest already isolates
DJ_MUSIC_ROOT and CRATEIQ_LIBRARY_ROOT) and never touch a real library.
The preflight is read-only: one test asserts the filesystem is unchanged.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core import preflight


VALID_STATUSES = {"pass", "warn", "fail"}


def _make_root(tmp_path: Path, with_db: bool = True) -> Path:
    root = tmp_path / "library"
    root.mkdir(parents=True, exist_ok=True)
    if with_db:
        db = root / "logs" / "processed.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_bytes(b"")
    return root


def _mock_all_binaries_found(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


def _mock_all_binaries_missing(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)


def _check_by_name(report: dict, name: str) -> dict:
    matches = [c for c in report["checks"] if c["name"] == name]
    assert matches, f"check {name!r} missing from report"
    return matches[0]


# ---------------------------------------------------------------------------
# run_preflight() unit behavior
# ---------------------------------------------------------------------------

def test_report_shape_and_ready_status(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    _mock_all_binaries_found(monkeypatch)
    # backend/data is created on first backend start and may not exist in a
    # clean checkout; pin it so the "ready" expectation is deterministic.
    backend_data = tmp_path / "backend_data"
    backend_data.mkdir()
    monkeypatch.setattr(preflight, "BACKEND_DATA_DIR", backend_data)

    report = preflight.run_preflight()

    assert report["status"] == "ready"
    assert isinstance(report["checks"], list) and report["checks"]
    for check in report["checks"]:
        assert set(check) == {"name", "status", "message", "required", "metadata"}
        assert check["status"] in VALID_STATUSES
        assert isinstance(check["message"], str) and check["message"]
    names = {c["name"] for c in report["checks"]}
    assert {"library_root", "pipeline_db", "pipeline_entrypoint"} <= names


def test_missing_library_root_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(tmp_path / "does-not-exist"))
    _mock_all_binaries_found(monkeypatch)

    report = preflight.run_preflight()

    assert report["status"] == "not_ready"
    root_check = _check_by_name(report, "library_root")
    assert root_check["status"] == "fail"
    assert root_check["required"] is True


def test_unsafe_broad_root_is_not_ready(monkeypatch):
    # The repository root always exists and is on the unsafe list.
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(repo_root))
    monkeypatch.delenv("CRATEIQ_ALLOW_UNSAFE_ROOT", raising=False)
    _mock_all_binaries_found(monkeypatch)

    report = preflight.run_preflight()

    assert report["status"] == "not_ready"
    root_check = _check_by_name(report, "library_root")
    assert root_check["status"] == "fail"
    assert "unsafe" in root_check["message"].lower()


def test_unsafe_root_documented_override(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(repo_root))
    monkeypatch.setenv("CRATEIQ_ALLOW_UNSAFE_ROOT", "1")
    _mock_all_binaries_found(monkeypatch)

    report = preflight.run_preflight()

    root_check = _check_by_name(report, "library_root")
    assert root_check["status"] == "pass"
    assert report["status"] != "not_ready"


def test_missing_pipeline_db_is_degraded_not_failed(tmp_path, monkeypatch):
    root = _make_root(tmp_path, with_db=False)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    _mock_all_binaries_found(monkeypatch)

    report = preflight.run_preflight()

    assert report["status"] == "degraded"
    db_check = _check_by_name(report, "pipeline_db")
    assert db_check["status"] == "warn"
    assert "not found" in db_check["message"].lower()


def test_missing_optional_binaries_warn_but_do_not_crash(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    for env in ("FFPROBE_BIN", "FFMPEG_BIN", "KEYFINDER_BIN", "AUBIO_BIN",
                "BEET_BIN", "RMLINT_BIN", "RSYNC_BIN"):
        monkeypatch.delenv(env, raising=False)
    _mock_all_binaries_missing(monkeypatch)

    report = preflight.run_preflight()

    assert report["status"] == "degraded"
    binary_checks = [c for c in report["checks"] if c["name"].startswith("binary_")]
    assert len(binary_checks) == 7
    for check in binary_checks:
        assert check["status"] == "warn"
        assert check["required"] is False


def test_preflight_is_read_only(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    _mock_all_binaries_missing(monkeypatch)

    def snapshot() -> set[tuple[str, int]]:
        return {
            (str(p.relative_to(root)), p.stat().st_size if p.is_file() else -1)
            for p in root.rglob("*")
        }

    before = snapshot()
    preflight.run_preflight()
    after = snapshot()

    assert before == after


# ---------------------------------------------------------------------------
# GET /api/runtime/readiness endpoint
# ---------------------------------------------------------------------------

def test_readiness_endpoint_returns_structured_report(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _mock_all_binaries_found(monkeypatch)

    with TestClient(backend_main.app) as client:
        resp = client.get("/api/runtime/readiness")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] in {"ready", "degraded", "not_ready"}
    assert isinstance(payload["checks"], list) and payload["checks"]
    for check in payload["checks"]:
        assert check["status"] in VALID_STATUSES


def test_readiness_endpoint_handles_broken_root_without_500(tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(tmp_path / "missing"))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _mock_all_binaries_missing(monkeypatch)

    with TestClient(backend_main.app) as client:
        resp = client.get("/api/runtime/readiness")

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_ready"


def test_readiness_does_not_leak_secret_env_values(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    sentinel_secret = "sentinel-secret-value-do-not-leak"
    sentinel_key = "sentinel-api-key-do-not-leak"
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", sentinel_secret)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", sentinel_key)
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel_key)
    _mock_all_binaries_found(monkeypatch)

    with TestClient(backend_main.app) as client:
        resp = client.get("/api/runtime/readiness")

    assert resp.status_code == 200
    assert sentinel_secret not in resp.text
    assert sentinel_key not in resp.text


def test_readiness_does_not_run_pipeline_jobs(tmp_path, monkeypatch):
    """Readiness must never spawn subprocesses or dispatch jobs."""
    import subprocess

    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _mock_all_binaries_found(monkeypatch)

    def _forbidden(*args, **kwargs):  # pragma: no cover - should never run
        raise AssertionError("readiness must not spawn subprocesses")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)

    with TestClient(backend_main.app) as client:
        resp = client.get("/api/runtime/readiness")

    assert resp.status_code == 200


def test_health_endpoint_remains_backward_compatible(tmp_path, monkeypatch):
    root = _make_root(tmp_path)
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)

    with TestClient(backend_main.app) as client:
        resp = client.get("/api/health")

    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload) == {"ok", "library_root", "db_path", "db_exists"}
    assert payload["ok"] is True
