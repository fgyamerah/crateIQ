"""Focused launcher Create Library initialization and rollback coverage."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.core.db as backend_core_db
import backend.app.main as backend_main
from backend.app.services import library_launcher_admin_service as admin
from backend.app.services import library_registry_service as registry
from backend.app.services import workspace_service


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, access_mode: str = "local") -> Path:
    parent = tmp_path / "Music"
    parent.mkdir()
    monkeypatch.setattr(registry, "REGISTRY_PATH", tmp_path / ".state/library_registry.json")
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", tmp_path / ".state/crateiq.env")
    monkeypatch.setattr(admin, "_browse_roots", lambda: [tmp_path.resolve()])
    monkeypatch.setattr(backend_core_db, "JOBS_DB_PATH", tmp_path / "backend-data/jobs.db")
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", access_mode)
    return parent


def _create(client: TestClient, parent: Path, name: str = "My CrateIQ Library"):
    return client.post(
        "/api/launcher/create-library",
        json={"parent_directory": str(parent), "name": name},
    )


def test_create_initializes_classifies_and_registers_without_opening(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path)
    with TestClient(backend_main.app) as client:
        response = _create(client, parent)
    root = parent / "My CrateIQ Library"
    assert response.status_code == 201
    assert response.json()["library_id"] == registry.library_id_for_root(root)
    assert response.json()["display_name"] == "My CrateIQ Library"
    assert response.json()["classification"] == "managed_workspace"
    assert response.json()["last_opened_at"] is None
    assert all((root / zone).is_dir() for zone in registry.ZONE_NAMES)
    assert (root / workspace_service.WORKSPACE_MARKER_NAME).is_file()
    assert (root / "logs/processed.db").is_file(), "reuse the established managed-workspace initializer"
    assert registry.classify_library_candidate(str(root))["classification"] == "managed_workspace"
    assert registry._read_registry()[0]["last_opened_at"] is None
    assert not backend_core_db.JOBS_DB_PATH.exists(), "rootless Create must not start runtime jobs state"


@pytest.mark.parametrize(
    "name",
    ["", " leading", "trailing ", "a/b", r"a\\b", "..", ".hidden", "bad\x00name", "bad;command", "x" * 121, "CON"],
)
def test_create_rejects_invalid_names(monkeypatch, tmp_path, name):
    parent = _configure(monkeypatch, tmp_path)
    with TestClient(backend_main.app) as client:
        response = _create(client, parent, name)
    assert response.status_code == 422
    assert not registry.REGISTRY_PATH.exists()
    assert list(parent.iterdir()) == []


def test_create_collision_preserves_preexisting_data(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path)
    existing = parent / "Existing"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with TestClient(backend_main.app) as client:
        response = _create(client, parent, "Existing")
    assert response.status_code == 409
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not registry.REGISTRY_PATH.exists()


@pytest.mark.parametrize("kind", ["missing", "file", "symlink", "unsafe"])
def test_create_rejects_invalid_parent(monkeypatch, tmp_path, kind):
    parent = _configure(monkeypatch, tmp_path)
    if kind == "missing":
        selected = tmp_path / "missing"
    elif kind == "file":
        selected = tmp_path / "file.txt"
        selected.write_text("x", encoding="utf-8")
    elif kind == "symlink":
        selected = tmp_path / "alias"
        selected.symlink_to(parent, target_is_directory=True)
    else:
        selected = Path("/etc")
    with TestClient(backend_main.app) as client:
        response = _create(client, selected)
    assert response.status_code == 422
    assert not (selected / "My CrateIQ Library").exists() if selected.is_dir() else True


def test_create_rejects_inaccessible_parent(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path)
    real_access = admin.os.access
    monkeypatch.setattr(
        admin.os,
        "access",
        lambda path, mode: False if Path(path) == parent.resolve() else real_access(path, mode),
    )
    with TestClient(backend_main.app) as client:
        response = _create(client, parent)
    assert response.status_code == 422


def test_initializer_failure_rolls_back_only_known_operation_created_paths(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path)
    sibling = parent / "Preexisting"
    sibling.mkdir()
    sentinel = sibling / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def fail_after_known_write(root: Path):
        (root / "Inbox").mkdir()
        raise OSError("simulated initializer failure")

    monkeypatch.setattr(workspace_service, "configure_workspace", fail_after_known_write)
    with TestClient(backend_main.app) as client:
        response = _create(client, parent, "Failed")
    assert response.status_code == 500
    assert response.json()["detail"]["partial_directory_left"] is False
    assert not (parent / "Failed").exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not registry.REGISTRY_PATH.exists()


def test_initializer_failure_leaves_unprovable_partial_directory(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path)

    def fail_after_unknown_write(root: Path):
        (root / "foreign-data.txt").write_text("unknown owner", encoding="utf-8")
        raise OSError("simulated initializer failure")

    monkeypatch.setattr(workspace_service, "configure_workspace", fail_after_unknown_write)
    with TestClient(backend_main.app) as client:
        response = _create(client, parent, "Partial")
    assert response.status_code == 500
    assert response.json()["detail"]["partial_directory_left"] is True
    assert (parent / "Partial/foreign-data.txt").read_text(encoding="utf-8") == "unknown owner"
    assert not registry.REGISTRY_PATH.exists()


def test_registry_failure_after_valid_create_leaves_library_for_retry(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        registry,
        "register_library",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated registry failure")),
    )
    with TestClient(backend_main.app) as client:
        response = _create(client, parent, "Retryable")
    root = parent / "Retryable"
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "registry_write_failed_after_create"
    assert registry.classify_library_candidate(str(root))["classification"] == "managed_workspace"
    assert not registry.REGISTRY_PATH.exists()


def test_lan_cannot_create_library(monkeypatch, tmp_path):
    parent = _configure(monkeypatch, tmp_path, access_mode="lan")
    with TestClient(backend_main.app, client=("192.168.1.20", 50100)) as client:
        response = _create(client, parent)
    assert response.status_code == 403
    assert list(parent.iterdir()) == []
