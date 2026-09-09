"""Focused launcher browse/register API safety and registry coverage."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.services import library_launcher_admin_service as admin
from backend.app.services import library_registry_service as registry
from backend.app.services import supervisor_ipc


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, access_mode: str = "local") -> None:
    monkeypatch.setattr(registry, "REGISTRY_PATH", tmp_path / ".state/library_registry.json")
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", tmp_path / ".state/crateiq.env")
    monkeypatch.setattr(admin, "_browse_roots", lambda: [tmp_path.resolve()])
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", access_mode)


def _managed(root: Path) -> Path:
    root.mkdir()
    for zone in registry.ZONE_NAMES:
        (root / zone).mkdir()
    (root / registry.WORKSPACE_MARKER_NAME).write_text('{"version": 1}', encoding="utf-8")
    return root


def _legacy(root: Path) -> Path:
    root.mkdir()
    db_path = root / "logs/processed.db"
    db_path.parent.mkdir()
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY, filepath TEXT, filename TEXT,
                status TEXT, pipeline_ver TEXT, key_musical TEXT, key_camelot TEXT
            );
            CREATE TABLE track_history (
                id INTEGER PRIMARY KEY, filepath TEXT, original_meta TEXT,
                cleaned_meta TEXT, actions TEXT, rolled_back INTEGER
            );
            CREATE TABLE pipeline_runs (
                id INTEGER PRIMARY KEY, run_at TEXT, dry_run INTEGER,
                inbox_count INTEGER, processed INTEGER, rejected INTEGER,
                duplicates INTEGER, unsorted INTEGER, errors INTEGER
            );
            CREATE TABLE duplicate_groups (
                id INTEGER PRIMARY KEY, run_id INTEGER, original TEXT,
                duplicate TEXT, reason TEXT, resolved INTEGER
            );
        """)
    return root


def test_browse_is_sorted_bounded_and_classifies_safe_directories(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _managed(tmp_path / "Zulu Managed")
    _legacy(tmp_path / "Bravo Legacy")
    (tmp_path / "Alpha Empty").mkdir()
    malformed = tmp_path / "Charlie Malformed"
    malformed.mkdir()
    (malformed / "notes.txt").write_text("not a library", encoding="utf-8")
    (tmp_path / ".hidden-secret").mkdir()
    (tmp_path / "ordinary-file.txt").write_text("not returned", encoding="utf-8")
    (tmp_path / "Delta Link").symlink_to(tmp_path / "Alpha Empty", target_is_directory=True)

    with TestClient(backend_main.app) as client:
        first = client.get("/api/launcher/browse", params={"path": str(tmp_path), "limit": 3})
        second = client.get("/api/launcher/browse", params={"path": str(tmp_path), "offset": 3, "limit": 3})

    assert first.status_code == 200
    assert [entry["display_name"] for entry in first.json()["entries"]] == [
        "Alpha Empty", "Bravo Legacy", "Charlie Malformed",
    ]
    assert first.json()["truncated"] is True
    assert second.json()["entries"][0]["display_name"] == "Delta Link"
    assert second.json()["entries"][0]["entry_type"] == "symlink"
    assert second.json()["entries"][0]["selectable"] is False
    by_name = {entry["display_name"]: entry for entry in first.json()["entries"] + second.json()["entries"]}
    assert by_name["Bravo Legacy"]["classification"] == "legacy_direct_library"
    assert by_name["Bravo Legacy"]["selectable"] is True
    assert by_name["Zulu Managed"]["classification"] == "managed_workspace"
    assert by_name["Alpha Empty"]["classification"] == "empty_folder"
    assert ".hidden-secret" not in by_name and "ordinary-file.txt" not in by_name


def test_browse_parent_navigation_stops_at_allowed_root(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    with TestClient(backend_main.app) as client:
        root_response = client.get("/api/launcher/browse", params={"path": str(tmp_path)})
        child_response = client.get("/api/launcher/browse", params={"path": str(child)})
    assert root_response.json()["parent_path"] is None
    assert child_response.json()["parent_path"] == str(tmp_path.resolve())


@pytest.mark.parametrize("kind", ["missing", "file", "symlink", "outside"])
def test_browse_rejects_invalid_locations(monkeypatch, tmp_path, kind):
    _configure(monkeypatch, tmp_path)
    if kind == "missing":
        location = tmp_path / "missing"
    elif kind == "file":
        location = tmp_path / "file.txt"
        location.write_text("x", encoding="utf-8")
    elif kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        location = tmp_path / "alias"
        location.symlink_to(target, target_is_directory=True)
    else:
        location = Path("/etc")
    with TestClient(backend_main.app) as client:
        response = client.get("/api/launcher/browse", params={"path": str(location)})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_browse_location"


def test_browse_rejects_inaccessible_directory(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    real_access = admin.os.access
    monkeypatch.setattr(
        admin.os,
        "access",
        lambda path, mode: False if Path(path) == locked.resolve() else real_access(path, mode),
    )
    with TestClient(backend_main.app) as client:
        response = client.get("/api/launcher/browse", params={"path": str(locked)})
    assert response.status_code == 422


def test_register_managed_is_stable_deduplicated_and_does_not_set_recency(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    root = _managed(tmp_path / "Managed")
    with TestClient(backend_main.app) as client:
        first = client.post("/api/launcher/register-library", json={"path": str(root)})
        second = client.post("/api/launcher/register-library", json={"path": str(root.resolve())})
    assert first.status_code == second.status_code == 200
    assert first.json()["library_id"] == second.json()["library_id"] == registry.library_id_for_root(root)
    assert first.json()["library_key"] == registry.library_key_for_root(root)
    assert first.json()["last_opened_at"] is None
    entries = registry._read_registry()
    assert len(entries) == 1
    assert entries[0]["last_opened_at"] is None
    assert registry.get_launcher_registry()["recent_libraries"] == []


def test_register_existing_opened_entry_preserves_last_opened(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    root = _managed(tmp_path / "Managed")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    with TestClient(backend_main.app) as client:
        response = client.post("/api/launcher/register-library", json={"path": str(root)})
    assert response.status_code == 200
    assert registry._read_registry()[0]["last_opened_at"] == "2026-08-30T10:00:00Z"


def test_register_valid_legacy_library(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    root = _legacy(tmp_path / "Legacy")
    with TestClient(backend_main.app) as client:
        response = client.post("/api/launcher/register-library", json={"path": str(root)})
    assert response.status_code == 200
    assert response.json()["classification"] == "legacy_direct_library"


def test_register_reclassifies_after_browse_and_rejects_stale_or_symlink(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    root = _managed(tmp_path / "Managed")
    alias = tmp_path / "Alias"
    alias.symlink_to(root, target_is_directory=True)
    with TestClient(backend_main.app) as client:
        browsed = client.get("/api/launcher/browse", params={"path": str(tmp_path)})
        (root / "Inbox").rmdir()
        stale = client.post("/api/launcher/register-library", json={"path": str(root)})
        symlinked = client.post("/api/launcher/register-library", json={"path": str(alias)})
    assert any(entry["path"] == str(root) and entry["selectable"] for entry in browsed.json()["entries"])
    assert stale.status_code == 422
    assert stale.json()["detail"]["code"] == "not_a_valid_library"
    assert symlinked.status_code == 422
    assert not registry.REGISTRY_PATH.exists()


def test_register_rejects_uninitialized_and_malformed_directories(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    empty = tmp_path / "Empty"
    empty.mkdir()
    malformed = tmp_path / "Malformed"
    malformed.mkdir()
    (malformed / registry.WORKSPACE_MARKER_NAME).write_text("{broken", encoding="utf-8")
    with TestClient(backend_main.app) as client:
        empty_response = client.post("/api/launcher/register-library", json={"path": str(empty)})
        malformed_response = client.post("/api/launcher/register-library", json={"path": str(malformed)})
    assert empty_response.status_code == malformed_response.status_code == 422


def test_register_write_failure_preserves_existing_registry(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    existing = _managed(tmp_path / "Existing")
    candidate = _managed(tmp_path / "Candidate")
    registry.record_recent_library(str(existing), opened_at="2026-08-30T10:00:00Z")
    before = registry.REGISTRY_PATH.read_bytes()
    monkeypatch.setattr(registry, "_atomic_write_registry", lambda _entries: (_ for _ in ()).throw(OSError("disk full")))
    with TestClient(backend_main.app) as client:
        response = client.post("/api/launcher/register-library", json={"path": str(candidate)})
    assert response.status_code == 503
    assert registry.REGISTRY_PATH.read_bytes() == before


def test_lan_denies_filesystem_admin_but_registered_id_activation_still_works(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path, access_mode="lan")
    root = _managed(tmp_path / "Managed")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    monkeypatch.setattr(supervisor_ipc, "request", lambda _operation, _payload=None: {
        "result": "activation_started", "activation_id": "lan-safe",
    })
    with TestClient(backend_main.app, client=("192.168.1.20", 50100)) as client:
        browse = client.get("/api/launcher/browse", params={"path": str(tmp_path)})
        register = client.post("/api/launcher/register-library", json={"path": str(root)})
        create = client.post(
            "/api/launcher/create-library",
            json={"parent_directory": str(tmp_path), "name": "New Library"},
        )
        activate = client.post(
            "/api/launcher/activate-library",
            json={"library_id": registry.library_id_for_root(root)},
        )
    assert browse.status_code == register.status_code == create.status_code == 403
    assert activate.status_code == 202
