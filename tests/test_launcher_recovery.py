"""Deterministic recovery coverage with no user service interaction."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from backend.app import launcher_recovery, supervisor
from backend.app.core.library_key import library_key_for_root


def _paths(tmp_path: Path) -> dict[str, Path]:
    local = tmp_path / ".run" / "local"
    local.mkdir(parents=True)
    return {
        "socket_path": local / "crateiq-supervisor.sock",
        "state_path": local / "library_activation_state.json",
        "activation_lock_path": local / "library_activation.lock",
        "supervisor_lock_path": local / "crateiq-supervisor.lock",
        "registry_path": local / "library_registry.json",
        "local_env_path": local / "crateiq.env",
        "proc_root": tmp_path / "proc",
    }


def _fail_closed(paths: dict[str, Path], requested_root: Path, *, old_instance_id: str = "old-child") -> None:
    state = supervisor.ActivationStateStore.empty()
    state.update({
        "activation_id": "activation-test",
        "phase": "fail_closed",
        "requested_root": str(requested_root.resolve()),
        "requested_library_key": library_key_for_root(requested_root.resolve()),
        "old_instance_id": old_instance_id,
        "failure_reason": "rollback replacement failed",
        "failure_stage": "rollback_backend_start",
    })
    supervisor.ActivationStateStore(paths["state_path"]).write(state)


def _idle(paths: dict[str, Path]) -> None:
    supervisor.ActivationStateStore(paths["state_path"]).write(supervisor.ActivationStateStore.empty())


def _stale_socket(path: Path) -> None:
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()


def _registry(paths: dict[str, Path], requested_root: Path) -> bytes:
    data = (
        json.dumps({
            "schema_version": 1,
            "recent_libraries": [{
                "path": str(requested_root.resolve()),
                "display_name": requested_root.name,
                "last_opened_at": None,
            }],
        }, indent=2)
        + "\n"
    ).encode()
    paths["registry_path"].write_bytes(data)
    return data


def _recover(tmp_path: Path, paths: dict[str, Path]) -> dict[str, object]:
    paths["proc_root"].mkdir(exist_ok=True)
    return launcher_recovery.recover_launcher(
        repo_root=tmp_path,
        backend_port=8020,
        **paths,
    )


def _fake_backend_process(
    paths: dict[str, Path], repo_root: Path, *, instance_id: str, port: int = 8020,
) -> None:
    process = paths["proc_root"] / "42420"
    process.mkdir(parents=True)
    (process / "cwd").symlink_to(repo_root, target_is_directory=True)
    (process / "cmdline").write_bytes(
        b"/safe/python\0-m\0backend.app.supervised_backend\0backend.app.main:app\0"
    )
    environment = {
        "CRATEIQ_SUPERVISOR_INSTANCE_ID": "supervisor-test",
        "CRATEIQ_BACKEND_INSTANCE_ID": instance_id,
        "CRATEIQ_BACKEND_START_ROLE": "active",
        "CRATEIQ_BACKEND_BOUND_PORT": str(port),
    }
    (process / "environ").write_bytes(
        b"\0".join(f"{key}={value}".encode() for key, value in environment.items()) + b"\0"
    )


def test_safe_stale_recovery_preserves_registry_saved_root_and_recency(tmp_path):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    _fail_closed(paths, requested_root)
    registry_before = _registry(paths, requested_root)
    saved_before = b"# Managed by CrateIQ.\nCRATEIQ_LIBRARY_ROOT=/missing/prior-library\n"
    paths["local_env_path"].write_bytes(saved_before)

    result = _recover(tmp_path, paths)

    assert result["status"] == "recovered"
    state = supervisor.ActivationStateStore(paths["state_path"]).read()
    assert state["phase"] == "idle"
    assert state["requested_root"] is None
    assert state["active_instance_id"] is None
    assert paths["registry_path"].read_bytes() == registry_before
    assert json.loads(registry_before)["recent_libraries"][0]["last_opened_at"] is None
    assert paths["local_env_path"].read_bytes() == saved_before
    assert paths["activation_lock_path"].read_bytes() == b""
    archives = list(paths["state_path"].parent.glob("library_activation_state.recovered.*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8"))["phase"] == "fail_closed"


def test_matching_live_child_refuses_recovery(tmp_path):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    _fail_closed(paths, requested_root, old_instance_id="owned-child")
    _registry(paths, requested_root)
    paths["proc_root"].mkdir()
    _fake_backend_process(paths, tmp_path, instance_id="owned-child")

    with pytest.raises(launcher_recovery.LauncherRecoveryError, match="matching persisted"):
        _recover(tmp_path, paths)

    assert supervisor.ActivationStateStore(paths["state_path"]).read()["phase"] == "fail_closed"


@pytest.mark.parametrize("ambiguity", ("lock", "socket"))
def test_socket_or_lock_ownership_ambiguity_refuses_recovery(tmp_path, ambiguity):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    _fail_closed(paths, requested_root)
    _registry(paths, requested_root)
    paths["proc_root"].mkdir()

    held_lock = None
    if ambiguity == "lock":
        held_lock = supervisor.SupervisorLock(paths["supervisor_lock_path"])
        held_lock.acquire()
    else:
        paths["socket_path"].write_text("not a socket", encoding="utf-8")
    try:
        with pytest.raises(supervisor.SupervisorError, match="operator intervention|required|refusing"):
            _recover(tmp_path, paths)
    finally:
        if held_lock is not None:
            held_lock.release()

    assert supervisor.ActivationStateStore(paths["state_path"]).read()["phase"] == "fail_closed"


def test_proven_stale_socket_is_removed_during_recovery(tmp_path):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    _fail_closed(paths, requested_root)
    _registry(paths, requested_root)
    paths["proc_root"].mkdir()
    _stale_socket(paths["socket_path"])

    result = _recover(tmp_path, paths)

    assert result["stale_socket_removed"] is True
    assert not paths["socket_path"].exists()


def test_saved_root_advanced_to_requested_root_is_ambiguous(tmp_path):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    _fail_closed(paths, requested_root)
    _registry(paths, requested_root)
    paths["local_env_path"].write_text(
        f"CRATEIQ_LIBRARY_ROOT={requested_root.resolve()}\n", encoding="utf-8",
    )

    with pytest.raises(launcher_recovery.LauncherRecoveryError, match="may have advanced"):
        _recover(tmp_path, paths)

    assert supervisor.ActivationStateStore(paths["state_path"]).read()["phase"] == "fail_closed"


def test_recovery_is_idempotent_for_already_idle_state(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)

    result = launcher_recovery.recover_launcher(
        repo_root=tmp_path,
        backend_port=8020,
        **paths,
    )

    assert result == {"status": "already_idle", "archive": None, "stale_socket_removed": False}
    assert supervisor.ActivationStateStore(paths["state_path"]).read()["phase"] == "idle"


def test_idle_stale_socket_is_safely_recovered_and_recovery_is_idempotent(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    _stale_socket(paths["socket_path"])

    first = _recover(tmp_path, paths)
    second = _recover(tmp_path, paths)

    assert first == {"status": "recovered_idle_socket", "archive": None, "stale_socket_removed": True}
    assert second == {"status": "already_idle", "archive": None, "stale_socket_removed": False}
    assert not paths["socket_path"].exists()
    assert supervisor.ActivationStateStore(paths["state_path"]).read()["phase"] == "idle"


def test_idle_live_supervisor_socket_and_lifetime_lock_are_not_disturbed(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(paths["socket_path"]))
    listener.listen(1)
    lifetime_lock = supervisor.SupervisorLock(paths["supervisor_lock_path"])
    lifetime_lock.acquire()
    try:
        with pytest.raises(launcher_recovery.LauncherRecoveryError, match="lifetime-lock ownership"):
            _recover(tmp_path, paths)
        assert paths["socket_path"].exists()
    finally:
        lifetime_lock.release()
        listener.close()


def test_idle_ambiguous_socket_path_is_not_deleted(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    paths["socket_path"].write_text("not a socket", encoding="utf-8")

    with pytest.raises(launcher_recovery.LauncherRecoveryError, match="socket ownership is ambiguous"):
        _recover(tmp_path, paths)

    assert paths["socket_path"].read_text(encoding="utf-8") == "not a socket"


def test_active_healthy_supervisor_is_not_disturbed(tmp_path):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    state = supervisor.ActivationStateStore.empty()
    state.update({
        "activation_id": "activation-test",
        "phase": "preparing",
        "requested_root": str(requested_root.resolve()),
        "requested_library_key": library_key_for_root(requested_root.resolve()),
    })
    supervisor.ActivationStateStore(paths["state_path"]).write(state)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(paths["socket_path"]))
    listener.listen(1)
    try:
        with pytest.raises(launcher_recovery.LauncherRecoveryError, match="activation is in progress"):
            _recover(tmp_path, paths)
        assert paths["socket_path"].exists()
    finally:
        listener.close()


def test_idle_stale_socket_accepts_dead_activation_lock_pid_and_clears_metadata(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    paths["activation_lock_path"].write_text(
        '{"pid": 42420, "acquired_at": "2026-09-04T00:24:34Z"}\n', encoding="utf-8",
    )
    _stale_socket(paths["socket_path"])

    result = _recover(tmp_path, paths)

    assert result["status"] == "recovered_idle_socket"
    assert paths["activation_lock_path"].read_bytes() == b""


def test_idle_stale_socket_refuses_live_activation_lock_pid(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    (paths["proc_root"] / "42420").mkdir()
    paths["activation_lock_path"].write_text(
        '{"pid": 42420, "acquired_at": "2026-09-04T00:24:34Z"}\n', encoding="utf-8",
    )
    _stale_socket(paths["socket_path"])

    with pytest.raises(launcher_recovery.LauncherRecoveryError, match="references a live process"):
        _recover(tmp_path, paths)

    assert paths["socket_path"].exists()


def test_idle_recovery_removes_only_the_configured_supervisor_socket(tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    unrelated_path = paths["socket_path"].with_name("unrelated.sock")
    _stale_socket(paths["socket_path"])
    _stale_socket(unrelated_path)

    result = _recover(tmp_path, paths)

    assert result["status"] == "recovered_idle_socket"
    assert not paths["socket_path"].exists()
    assert unrelated_path.exists()


def test_rootless_supervisor_start_is_admitted_after_idle_socket_recovery(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    _idle(paths)
    paths["proc_root"].mkdir()
    _stale_socket(paths["socket_path"])
    _recover(tmp_path, paths)
    calls: list[str] = []
    monkeypatch.setattr(supervisor.LocalSupervisor, "bind_control_socket", lambda self: calls.append("bind"))
    monkeypatch.setattr(supervisor.LocalSupervisor, "start_active", lambda self, **kwargs: calls.append(kwargs["role"]))
    monkeypatch.setattr(supervisor.LocalSupervisor, "cleanup", lambda self: calls.append("cleanup"))
    monkeypatch.setattr(supervisor, "serve", lambda local: calls.append("serve"))

    result = supervisor.main([
        "--socket", str(paths["socket_path"]),
        "--state", str(paths["state_path"]),
        "--lock", str(paths["activation_lock_path"]),
        "--active-role", "rootless",
        "--port", "8020",
        "--bind-host", "127.0.0.1",
        "--access-mode", "local",
    ])

    assert result == 0
    assert calls == ["bind", "rootless", "serve", "cleanup"]


def test_rootless_supervisor_start_is_admitted_after_recovery(monkeypatch, tmp_path):
    paths = _paths(tmp_path)
    requested_root = tmp_path / "DJ TEST"
    requested_root.mkdir()
    _fail_closed(paths, requested_root)
    _registry(paths, requested_root)
    _recover(tmp_path, paths)
    calls: list[str] = []
    monkeypatch.setattr(supervisor.LocalSupervisor, "bind_control_socket", lambda self: calls.append("bind"))
    monkeypatch.setattr(supervisor.LocalSupervisor, "start_active", lambda self, **kwargs: calls.append(kwargs["role"]))
    monkeypatch.setattr(supervisor.LocalSupervisor, "cleanup", lambda self: calls.append("cleanup"))
    monkeypatch.setattr(supervisor, "serve", lambda local: calls.append("serve"))

    result = supervisor.main([
        "--socket", str(paths["socket_path"]),
        "--state", str(paths["state_path"]),
        "--lock", str(paths["activation_lock_path"]),
        "--active-role", "rootless",
        "--port", "8020",
        "--bind-host", "127.0.0.1",
        "--access-mode", "local",
    ])

    assert result == 0
    assert calls == ["bind", "rootless", "serve", "cleanup"]


def test_service_script_exposes_only_explicit_recovery_command():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "crateiq-local-services.sh").read_text(encoding="utf-8")
    assert "recover-launcher) _crateiq_recover_launcher" in script
    assert "crateiq_recover_launcher()" in script
    assert "backend.app.launcher_recovery" in script
