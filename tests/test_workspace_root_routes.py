"""
API-level tests for the workspace root classify/create onboarding routes.

Covers: read-only classification never touches disk, create requires
confirm and only creates the single final directory, forbidden/system
paths are rejected at the route layer, and an existing legacy root is
classified but never restructured through these endpoints.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main


def _write(path: Path, content: bytes = b"fake-audio-bytes") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "active-root"
    root.mkdir()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "jobs.db")
    test_client = TestClient(backend_main.app)
    with test_client:
        yield test_client, tmp_path


def test_classify_new_folder_is_read_only(client):
    test_client, tmp_path = client
    target = tmp_path / "Music" / "crateIQ"
    (tmp_path / "Music").mkdir()

    resp = test_client.post("/api/workspace/root/classify", json={"library_root": str(target)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is False
    assert body["can_create"] is True
    assert not target.exists(), "classify must never create anything on disk"


def test_classify_rejects_forbidden_path(client):
    test_client, _ = client
    resp = test_client.post("/api/workspace/root/classify", json={"library_root": "/etc/crateiq"})
    assert resp.status_code == 422


def test_create_requires_confirm(client):
    test_client, tmp_path = client
    (tmp_path / "Music").mkdir()
    target = tmp_path / "Music" / "crateIQ"

    resp = test_client.post("/api/workspace/root/create", json={"library_root": str(target), "confirm": False})

    assert resp.status_code == 422
    assert not target.exists()


def test_create_creates_only_final_directory(client):
    test_client, tmp_path = client
    (tmp_path / "Music").mkdir()
    target = tmp_path / "Music" / "crateIQ"

    resp = test_client.post("/api/workspace/root/create", json={"library_root": str(target), "confirm": True})

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert body["state"] == "not_configured"
    assert target.is_dir()


def test_create_rejects_missing_parent(client):
    test_client, tmp_path = client
    target = tmp_path / "no-such-parent" / "crateIQ"

    resp = test_client.post("/api/workspace/root/create", json={"library_root": str(target), "confirm": True})

    assert resp.status_code == 422
    assert not target.exists()
    assert not (tmp_path / "no-such-parent").exists()


def test_classify_existing_legacy_root_not_restructured(client):
    test_client, tmp_path = client
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _write(legacy / "track.mp3")

    resp = test_client.post("/api/workspace/root/classify", json={"library_root": str(legacy)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "legacy_direct_library"
    assert not (legacy / "Inbox").exists()
    assert (legacy / "track.mp3").is_file()
