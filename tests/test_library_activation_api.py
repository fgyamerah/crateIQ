"""Focused no-live-service coverage for registry-bound launcher activation."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app import supervisor
from backend.app.services import library_registry_service as registry
from backend.app.services import supervisor_ipc


def _configure_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(registry, "REGISTRY_PATH", tmp_path / ".run/local/library_registry.json")
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", tmp_path / ".run/local/crateiq.env")
    monkeypatch.delenv("CRATEIQ_LIBRARY_ROOT", raising=False)
    monkeypatch.delenv("CRATEMINDAI_LIBRARY_ROOT", raising=False)
    monkeypatch.setenv("CRATEIQ_LAUNCH_ACCESS_MODE", "lan")


def _managed(root: Path) -> Path:
    root.mkdir()
    for zone in registry.ZONE_NAMES:
        (root / zone).mkdir()
    (root / registry.WORKSPACE_MARKER_NAME).write_text(json.dumps({"version": 1}), encoding="utf-8")
    return root


def _target(root: Path) -> dict[str, object]:
    library_id = registry.library_id_for_root(root)
    return {
        "library_id": library_id,
        "library_root": str(root.resolve()),
        "library_key": registry.library_key_for_root(root),
        "classification": "managed_workspace",
    }


def test_activation_accepts_registry_id_for_lan_and_rejects_path_override(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    calls: list[tuple[str, dict[str, object] | None]] = []
    monkeypatch.setattr(
        supervisor_ipc,
        "request",
        lambda operation, payload=None: calls.append((operation, payload)) or {
            "result": "activation_started", "activation_id": "safe-activation", "activation_status": "activating",
        },
    )

    with TestClient(backend_main.app, client=("192.168.1.50", 50000)) as client:
        response = client.post("/api/launcher/activate-library", json={"library_id": registry.library_id_for_root(root)})
        override = client.post("/api/launcher/activate-library", json={"library_id": registry.library_id_for_root(root), "library_root": "/etc"})
        path_admin = client.post("/api/launcher/library-classification", json={"library_root": str(root)})
        health = client.get("/api/health")

    assert response.status_code == 202
    assert response.json() == {"activation_id": "safe-activation", "activation_status": "activating"}
    assert calls == [("activate_registered_library", _target(root))]
    assert override.status_code == 422
    assert path_admin.status_code == 403
    assert health.status_code == 200
    assert len(calls) == 1


def test_unknown_or_unsafe_registry_entry_never_reaches_supervisor(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    calls: list[object] = []
    monkeypatch.setattr(supervisor_ipc, "request", lambda *args, **kwargs: calls.append((args, kwargs)))

    with TestClient(backend_main.app) as client:
        unknown = client.post("/api/launcher/activate-library", json={"library_id": "library_" + "0" * 64})
        (root / "Inbox").rmdir()
        unsafe = client.post("/api/launcher/activate-library", json={"library_id": registry.library_id_for_root(root)})

    assert unknown.status_code == 404
    assert unsafe.status_code == 422
    assert calls == []
    assert registry._read_registry()[0]["last_opened_at"] == "2026-08-30T10:00:00Z"


@pytest.mark.parametrize(
    ("supervisor_code", "expected"),
    [("activation_in_progress", 409), ("supervisor_fail_closed", 409), ("supervisor_unavailable", 503)],
)
def test_activation_maps_supervisor_admission_errors_without_recency_write(monkeypatch, tmp_path, supervisor_code, expected):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    monkeypatch.setattr(supervisor_ipc, "request", lambda *_args, **_kwargs: (_ for _ in ()).throw(supervisor_ipc.SupervisorIPCError(supervisor_code)))

    with TestClient(backend_main.app) as client:
        response = client.post("/api/launcher/activate-library", json={"library_id": registry.library_id_for_root(root)})

    assert response.status_code == expected
    expected_code = supervisor_code if expected == 409 else "supervisor_unavailable"
    assert response.json()["detail"]["code"] == expected_code
    assert registry._read_registry()[0]["last_opened_at"] == "2026-08-30T10:00:00Z"


def test_supervisor_worker_updates_recency_only_after_success_and_warns_on_write_failure(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    local = supervisor.LocalSupervisor(repo_root=tmp_path, state_store=supervisor.ActivationStateStore(tmp_path / "state.json"))
    target = _target(root)
    monkeypatch.setattr(local, "handoff_library", lambda _root: {"result": "activated"})

    local._run_registered_library_activation("success", target)

    assert local.status()["activation"] == {
        "status": "succeeded", "activation_id": "success", "library_id": target["library_id"],
        "result": "activated", "registry_recency_updated": True, "warning_code": None,
    }
    assert registry._read_registry()[0]["last_opened_at"] != "2026-08-30T10:00:00Z"

    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    monkeypatch.setattr(registry, "mark_registered_library_opened", lambda _id: (_ for _ in ()).throw(OSError("readonly")))
    local._run_registered_library_activation("warning", target)
    record = local.status()["activation"]
    assert record["status"] == "succeeded"
    assert record["registry_recency_updated"] is False
    assert record["warning_code"] == "registry_recency_update_failed"
    assert registry._read_registry()[0]["last_opened_at"] == "2026-08-30T10:00:00Z"


def test_supervisor_worker_does_not_update_recency_after_rollback_or_blocker(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    local = supervisor.LocalSupervisor(repo_root=tmp_path, state_store=supervisor.ActivationStateStore(tmp_path / "state.json"))
    target = _target(root)
    monkeypatch.setattr(local, "handoff_library", lambda _root: (_ for _ in ()).throw(supervisor.SupervisorError("handoff rolled back: persisted operation state blocks a safe library switch")))
    local._last_blocker_summary = {"category": "active_work", "count": 1, "message": "The active library has work in progress."}

    local._run_registered_library_activation("blocked", target)

    assert local.status()["activation"]["status"] == "blocked"
    assert local.status()["activation"]["blocker"]["count"] == 1
    assert registry._read_registry()[0]["last_opened_at"] == "2026-08-30T10:00:00Z"


def test_supervisor_activation_ipc_is_strict_and_allows_only_one_handoff(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), opened_at="2026-08-30T10:00:00Z")
    local = supervisor.LocalSupervisor(repo_root=tmp_path, state_store=supervisor.ActivationStateStore(tmp_path / "state.json"))
    target = _target(root)
    entered = threading.Event()
    release = threading.Event()

    def handoff(_root):
        entered.set()
        assert release.wait(1)
        return {"result": "activated"}

    monkeypatch.setattr(local, "handoff_library", handoff)
    with pytest.raises(supervisor.SupervisorError, match="malformed_request"):
        local.handle_message({"schema_version": 1, "operation": "activate_registered_library", "payload": {"library_id": target["library_id"]}})
    mismatched = {**target, "library_key": "0" * 64}
    with pytest.raises(supervisor.SupervisorError, match="invalid_activation_target"):
        local.handle_message({"schema_version": 1, "operation": "activate_registered_library", "payload": mismatched})

    started = local.handle_message({"schema_version": 1, "operation": "activate_registered_library", "payload": target})
    assert started["result"] == "activation_started"
    assert entered.wait(1)
    with pytest.raises(supervisor.SupervisorError, match="activation_in_progress"):
        local.handle_message({"schema_version": 1, "operation": "activate_registered_library", "payload": target})
    assert local.status()["activation"]["status"] == "activating"
    release.set()
    assert local._activation_thread is not None
    local._activation_thread.join(1)
    assert local.status()["activation"]["status"] == "succeeded"


def test_activation_status_and_current_library_sanitize_supervisor_data(monkeypatch, tmp_path):
    _configure_registry(monkeypatch, tmp_path)
    root = _managed(tmp_path / "B")
    registry.record_recent_library(str(root), display_name="B", opened_at="2026-08-30T10:00:00Z")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(supervisor_ipc, "request", lambda _operation: {
        "activation": {"status": "succeeded", "activation_id": "id", "library_id": registry.library_id_for_root(root), "result": "already_active", "registry_recency_updated": True, "warning_code": None},
        "active_child": {"pid": 123, "library_root": "/sensitive", "verification_token": "never"},
    })

    with TestClient(backend_main.app) as client:
        state = client.get("/api/launcher/activation-status")
        current = client.get("/api/launcher/current-library")
        listing = client.get("/api/launcher/library-registry")

    assert state.status_code == 200
    assert "sensitive" not in state.text and "verification_token" not in state.text
    assert current.json()["library_id"] == registry.library_id_for_root(root)
    assert listing.json()["recent_libraries"][0]["active"] is True
