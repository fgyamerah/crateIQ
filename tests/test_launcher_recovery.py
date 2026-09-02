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
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(paths["socket_path"]))
    stale.close()

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
    supervisor.ActivationStateStore(paths["state_path"]).write(supervisor.ActivationStateStore.empty())

    result = launcher_recovery.recover_launcher(
        repo_root=tmp_path,
        backend_port=8020,
        **paths,
    )

    assert result == {"status": "already_idle", "archive": None, "stale_socket_removed": False}
    assert supervisor.ActivationStateStore(paths["state_path"]).read()["phase"] == "idle"


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
