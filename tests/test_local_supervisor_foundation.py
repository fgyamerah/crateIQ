"""Focused no-live-process coverage for Checkpoint 1B.2A supervisor seams."""
from __future__ import annotations

import json
import multiprocessing
import os
import socket
import stat
import threading
import time
import asyncio
from pathlib import Path

import pytest

from backend.app import supervisor
from backend.app.services import library_registry_service as registry
from backend.app.services.operation_admission_gate import (
    AdmissionState,
    LibraryOperationDrainingError,
    OperationAdmissionGate,
)
from backend.app.services.supervisor_ipc import request
from backend.app.services.backend_instance_identity import current_backend_identity
from backend.app.services.backend_instance_identity import verify_supervisor_token
from backend.app import supervised_backend
from backend.app.api.routes import health as health_route
from starlette.requests import Request


class FakeProcess:
    next_pid = 41000

    def __init__(self) -> None:
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode = None
        self.terminated = False
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.terminated = True
        self.returncode = -9


class FakePopen:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        return FakeProcess()


def _managed_root(path: Path) -> Path:
    path.mkdir()
    for zone in registry.ZONE_NAMES:
        (path / zone).mkdir()
    (path / registry.WORKSPACE_MARKER_NAME).write_text('{"version": 1}', encoding="utf-8")
    return path


def _supervisor(tmp_path: Path, popen=None, **kwargs) -> supervisor.LocalSupervisor:
    return supervisor.LocalSupervisor(
        repo_root=tmp_path,
        socket_path=tmp_path / ".run/local/crateiq-supervisor.sock",
        state_store=supervisor.ActivationStateStore(tmp_path / ".run/local/library_activation_state.json"),
        lock_path=tmp_path / ".run/local/library_activation.lock",
        supervisor_lock_path=tmp_path / ".run/local/crateiq-supervisor.lock",
        python_executable="/safe/python",
        popen=popen or FakePopen(),
        readiness_timeout_seconds=0.01,
        **kwargs,
    )


def _hold_control_socket(socket_path: str, lock_path: str, ready, release) -> None:
    """Separate process so flock contention uses its real kernel semantics."""
    local = supervisor.LocalSupervisor(
        repo_root=Path(socket_path).parent,
        socket_path=Path(socket_path),
        supervisor_lock_path=Path(lock_path),
        python_executable="/safe/python",
    )
    local.bind_control_socket()
    thread = threading.Thread(target=supervisor.serve, args=(local,), daemon=True)
    thread.start()
    ready.set()
    release.wait(5)
    local.cleanup()
    thread.join(timeout=1)


def _acquire_lock_then_exit(lock_path: str, acquired) -> None:
    lock = supervisor.SupervisorLock(Path(lock_path))
    lock.acquire()
    acquired.set()


def _acquire_activation_lock_then_exit(lock_path: str, acquired) -> None:
    lock = supervisor.ActivationLock(Path(lock_path))
    lock.acquire()
    acquired.set()


def test_supervisor_starts_without_children_and_has_unique_identity(tmp_path):
    first = _supervisor(tmp_path)
    second = _supervisor(tmp_path)
    assert first.instance_id != second.instance_id
    assert first.status()["active_child"] is None
    assert first.status()["candidate_child"] is None


def test_rootless_child_uses_fixed_non_reload_command_and_clears_inherited_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", "/stale")
    monkeypatch.setenv("DJ_MUSIC_ROOT", "/stale")
    popen = FakePopen()
    local = _supervisor(tmp_path, popen)
    child = local.start_active(role="rootless", port=8020, bind_host="127.0.0.1", library_root=None, access_mode="local")
    call = popen.calls[-1]
    assert child.instance_id
    assert "--reload" not in call["command"]
    assert "--fd" in call["command"]
    assert call["shell"] is False
    assert "backend.app.supervised_backend" in call["command"]
    assert call["env"].get("CRATEIQ_LIBRARY_ROOT") is None
    assert call["env"].get("DJ_MUSIC_ROOT") is None
    assert call["env"]["CRATEIQ_BACKEND_START_ROLE"] == "rootless"


def test_root_bound_child_passes_only_canonical_root_and_unique_instance(tmp_path):
    popen = FakePopen()
    local = _supervisor(tmp_path, popen)
    root = _managed_root(tmp_path / "library")
    child = local.start_active(role="active", port=8020, bind_host="0.0.0.0", library_root=root.resolve(), access_mode="lan")
    env = popen.calls[-1]["env"]
    assert env["CRATEIQ_LIBRARY_ROOT"] == str(root.resolve())
    assert env["DJ_MUSIC_ROOT"] == str(root.resolve())
    assert env["CRATEIQ_BACKEND_INSTANCE_ID"] == child.instance_id
    assert env["CRATEIQ_BACKEND_START_ROLE"] == "active"
    assert env["CRATEIQ_BACKEND_BOUND_PORT"] == "8020"


def test_candidate_is_loopback_ephemeral_and_verified_state_is_persisted(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library")
    popen = FakePopen()
    local = _supervisor(tmp_path, popen)
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda child: True)
    child = local.start_candidate(str(root))
    assert child.spec.role == "candidate"
    assert child.spec.bind_host == "127.0.0.1"
    assert child.spec.port != 8020
    assert popen.calls[-1]["env"]["CRATEIQ_LIBRARY_ROOT"] == str(root.resolve())
    assert "--reload" not in popen.calls[-1]["command"]
    state = local.state_store.read()
    assert state["phase"] == "candidate_verified"
    assert state["requested_root"] == str(root.resolve())
    assert state["candidate_instance_id"] == child.instance_id
    assert local.state_store.path.stat().st_mode & 0o777 == 0o600
    assert "credential" not in local.state_store.path.read_text(encoding="utf-8").lower()


def test_candidate_failure_terminates_only_candidate_and_returns_to_idle(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "candidate")
    popen = FakePopen()
    local = _supervisor(tmp_path, popen)
    active = local.start_active(role="rootless", port=8020, bind_host="127.0.0.1", library_root=None, access_mode="local")
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda child: False)
    with pytest.raises(supervisor.SupervisorError, match="readiness"):
        local.start_candidate(str(root))
    assert local.active is active
    assert active.process.poll() is None
    assert local.candidate is None
    assert local.state_store.read()["phase"] == "idle"


def test_identity_verification_rejects_wrong_token_and_wrong_root(monkeypatch, tmp_path):
    local = _supervisor(tmp_path)
    root = _managed_root(tmp_path / "library")
    child = local._start_child(supervisor.ChildSpec("candidate", 0, "127.0.0.1", root.resolve(), "local"))

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, _size):
            return json.dumps({"instance": {"instance_id": "wrong", "library_root": str(root.resolve())}}).encode()

    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    assert local._wait_for_verified_identity(child) is False

    class WrongRootResponse(Response):
        def read(self, _size):
            return json.dumps({"instance": {
                "instance_id": child.instance_id,
                "supervisor_instance_id": local.instance_id,
                "role": "candidate", "port": child.spec.port,
                "library_root": "/wrong", "library_key": "wrong",
            }}).encode()
    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *args, **kwargs: WrongRootResponse())
    assert local._wait_for_verified_identity(child) is False


def test_supervised_backend_identity_is_available_only_with_the_private_token(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library").resolve()
    monkeypatch.setenv("CRATEIQ_BACKEND_INSTANCE_ID", "child-token")
    monkeypatch.setenv("CRATEIQ_SUPERVISOR_INSTANCE_ID", "supervisor-token")
    monkeypatch.setenv("CRATEIQ_BACKEND_START_ROLE", "candidate")
    monkeypatch.setenv("CRATEIQ_BACKEND_BOUND_PORT", "49152")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setenv("CRATEIQ_BACKEND_VERIFY_TOKEN", "private-token")
    identity = current_backend_identity()
    assert identity and identity["instance_id"] == "child-token"
    assert identity["library_root"] == str(root)
    assert identity["library_key"]
    assert "secret" not in identity
    assert verify_supervisor_token("wrong") is None
    assert verify_supervisor_token(None) is None
    assert verify_supervisor_token("private-token") == identity


def test_public_health_omits_paths_and_internal_identity(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library").resolve()
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setenv("CRATEIQ_BACKEND_INSTANCE_ID", "child")
    monkeypatch.setenv("CRATEIQ_SUPERVISOR_INSTANCE_ID", "supervisor")
    monkeypatch.setenv("CRATEIQ_BACKEND_START_ROLE", "candidate")
    monkeypatch.setenv("CRATEIQ_BACKEND_BOUND_PORT", "49001")
    monkeypatch.setenv("CRATEIQ_BACKEND_VERIFY_TOKEN", "private-token")
    payload = asyncio.run(health_route.health()).model_dump()
    assert "library_root" not in payload and "db_path" not in payload and "instance" not in payload
    scope = {"type": "http", "method": "GET", "path": "/api/internal/supervisor-identity", "headers": [], "client": ("127.0.0.1", 1000)}
    request = Request(scope)
    internal = asyncio.run(health_route.supervisor_identity(request, "private-token"))
    assert internal["identity"]["library_root"] == str(root)
    with pytest.raises(Exception):
        asyncio.run(health_route.supervisor_identity(request, "wrong"))
    lan_scope = {**scope, "client": ("192.168.1.20", 1000)}
    with pytest.raises(Exception):
        asyncio.run(health_route.supervisor_identity(Request(lan_scope), "private-token"))


def test_private_admission_bridge_is_token_and_loopback_only(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library").resolve()
    monkeypatch.setenv("CRATEIQ_BACKEND_INSTANCE_ID", "child")
    monkeypatch.setenv("CRATEIQ_SUPERVISOR_INSTANCE_ID", "supervisor")
    monkeypatch.setenv("CRATEIQ_BACKEND_START_ROLE", "active")
    monkeypatch.setenv("CRATEIQ_BACKEND_BOUND_PORT", "49002")
    monkeypatch.setenv("CRATEIQ_LIBRARY_ROOT", str(root))
    monkeypatch.setenv("CRATEIQ_BACKEND_VERIFY_TOKEN", "private-token")
    scope = {"type": "http", "method": "POST", "path": "/api/internal/supervisor-admission", "headers": [], "client": ("127.0.0.1", 1000)}
    request = Request(scope)
    assert asyncio.run(health_route.supervisor_admission(request, "status", "private-token"))["admission"]["draining"] is False
    assert asyncio.run(health_route.supervisor_admission(request, "begin_draining", "private-token"))["admission"]["draining"] is True
    assert asyncio.run(health_route.supervisor_admission(request, "abort_draining", "private-token"))["admission"]["draining"] is False
    with pytest.raises(Exception):
        asyncio.run(health_route.supervisor_admission(Request({**scope, "client": ("192.168.1.20", 1000)}), "status", "private-token"))
    with pytest.raises(Exception):
        asyncio.run(health_route.supervisor_admission(request, "status", "wrong"))


def test_activation_state_rejects_malformed_data_and_invalid_transition(tmp_path):
    store = supervisor.ActivationStateStore(tmp_path / "state.json")
    store.path.write_text("{bad", encoding="utf-8")
    with pytest.raises(supervisor.MalformedActivationState):
        store.read()
    state = store.empty()
    with pytest.raises(supervisor.SupervisorError):
        store.transition(state, "candidate_verified")


def test_exact_legacy_idle_activation_state_is_migrated_but_partial_v1_fails_closed(tmp_path):
    store = supervisor.ActivationStateStore(tmp_path / "state.json")
    legacy_idle = {
        "schema_version": 1, "activation_id": None, "phase": "idle",
        "old_verified_root": None, "requested_root": None, "old_instance_id": None,
        "candidate_instance_id": None, "updated_at": "2026-08-31T00:00:00Z",
    }
    store.path.write_text(json.dumps(legacy_idle), encoding="utf-8")
    assert store.read()["schema_version"] == 2
    legacy_idle["phase"] = "candidate_verified"
    store.path.write_text(json.dumps(legacy_idle), encoding="utf-8")
    with pytest.raises(supervisor.MalformedActivationState):
        store.read()


def test_activation_state_incomplete_is_detectable_and_stop_candidate_cleans_it(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library")
    local = _supervisor(tmp_path)
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda child: True)
    local.start_candidate(str(root))
    assert local.state_store.incomplete() is True
    local.stop_candidate()
    assert local.state_store.read()["phase"] == "idle"


def test_activation_lock_allows_one_holder_and_releases_after_close(tmp_path):
    first = supervisor.ActivationLock(tmp_path / "activation.lock")
    second = supervisor.ActivationLock(tmp_path / "activation.lock")
    first.acquire()
    with pytest.raises(supervisor.ActivationLockUnavailable):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


def test_activation_lock_rejects_final_symlink_without_touching_target(tmp_path):
    target = tmp_path / "target.txt"
    target.write_bytes(b"keep these bytes exactly")
    lock_path = tmp_path / "activation.lock"
    lock_path.symlink_to(target)
    with pytest.raises(supervisor.ActivationLockUnavailable):
        supervisor.ActivationLock(lock_path).acquire()
    assert target.read_bytes() == b"keep these bytes exactly"
    assert lock_path.is_symlink()


def test_activation_lock_rejects_hard_link_without_touching_aliased_target(tmp_path):
    target = tmp_path / "arbitrary.txt"
    target.write_bytes(b"keep these bytes exactly")
    os.chmod(target, 0o640)
    target_mode = target.stat().st_mode & 0o777
    lock_path = tmp_path / "activation.lock"
    os.link(target, lock_path)

    with pytest.raises(supervisor.ActivationLockUnavailable, match="multiply-linked"):
        supervisor.ActivationLock(lock_path).acquire()

    assert target.read_bytes() == b"keep these bytes exactly"
    assert target.stat().st_mode & 0o777 == target_mode
    assert lock_path.read_bytes() == b"keep these bytes exactly"
    assert target.stat().st_nlink == 2


@pytest.mark.parametrize("kind", ("directory", "fifo"))
def test_activation_lock_rejects_non_regular_final_path(tmp_path, kind):
    lock_path = tmp_path / "activation.lock"
    if kind == "directory":
        lock_path.mkdir()
    else:
        os.mkfifo(lock_path)
    with pytest.raises(supervisor.ActivationLockUnavailable):
        supervisor.ActivationLock(lock_path).acquire()
    assert stat.S_ISDIR(lock_path.lstat().st_mode) if kind == "directory" else stat.S_ISFIFO(lock_path.lstat().st_mode)


def test_activation_lock_rejects_symlinked_runtime_parent(tmp_path):
    controlled = tmp_path / "controlled"
    controlled.mkdir()
    unsafe_parent = tmp_path / "unsafe"
    unsafe_parent.symlink_to(controlled, target_is_directory=True)
    with pytest.raises(supervisor.ActivationLockUnavailable):
        supervisor.ActivationLock(unsafe_parent / "activation.lock").acquire()
    assert not (controlled / "activation.lock").exists()


def test_activation_lock_stale_regular_file_is_reusable_and_crash_releases_flock(tmp_path):
    lock_path = tmp_path / "activation.lock"
    lock_path.write_text("stale", encoding="utf-8")
    recovered = supervisor.ActivationLock(lock_path)
    recovered.acquire()
    assert lock_path.stat().st_mode & 0o777 == 0o600
    recovered.release()
    context = multiprocessing.get_context("fork")
    acquired = context.Event()
    holder = context.Process(target=_acquire_activation_lock_then_exit, args=(str(lock_path), acquired))
    holder.start()
    assert acquired.wait(3)
    holder.join(3)
    assert holder.exitcode == 0
    final = supervisor.ActivationLock(lock_path)
    final.acquire()
    final.release()


def test_message_protocol_rejects_malformed_unknown_and_has_no_execution_primitive(tmp_path):
    local = _supervisor(tmp_path)
    with pytest.raises(supervisor.SupervisorError, match="malformed"):
        local.handle_message({"operation": "ping"})
    with pytest.raises(supervisor.SupervisorError, match="unknown"):
        local.handle_message({"schema_version": 1, "operation": "execute", "payload": {}})
    assert "execute" not in supervisor._ALLOWED_OPERATIONS


def test_unix_socket_ping_status_permissions_and_oversize_rejection(tmp_path):
    local = _supervisor(tmp_path)
    thread = threading.Thread(target=supervisor.serve, args=(local,), daemon=True)
    thread.start()
    deadline = time.monotonic() + 2
    while not local.socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert local.socket_path.stat().st_mode & 0o777 == 0o600
    assert request("ping", socket_path=local.socket_path)["pong"] is True
    assert request("status", socket_path=local.socket_path)["active_child"] is None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(local.socket_path))
        client.sendall(b"x" * (supervisor.MAX_IPC_MESSAGE_BYTES + 1))
        assert json.loads(client.recv(1024))["ok"] is False
    local.cleanup()
    thread.join(timeout=1)


def test_cleanup_quiesces_accepted_mutating_handler_before_releasing_ownership(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library")
    popen = FakePopen()
    local = _supervisor(tmp_path, popen)
    local.bind_control_socket()
    serving = threading.Thread(target=supervisor.serve, args=(local,), daemon=True)
    serving.start()
    accepted = threading.Event()
    local._on_ipc_handler_accepted = accepted.set
    client_done = threading.Event()
    client_errors: list[Exception] = []

    def send_start() -> None:
        try:
            request("start_candidate", {"library_root": str(root)}, socket_path=local.socket_path)
        except Exception as exc:  # shutdown rejection is expected
            client_errors.append(exc)
        finally:
            client_done.set()

    release_observed: list[bool] = []
    original_release = supervisor.SupervisorLock.release
    monkeypatch.setattr(
        supervisor.SupervisorLock,
        "release",
        lambda lock: (release_observed.append(local._ipc_handlers_quiesced.is_set()), original_release(lock))[1],
    )
    with local._state_lock:
        client = threading.Thread(target=send_start)
        client.start()
        assert accepted.wait(1)
        cleanup_done = threading.Event()
        cleaner = threading.Thread(target=lambda: (local.cleanup(), cleanup_done.set()))
        cleaner.start()
        assert not cleanup_done.wait(0.1)
    cleaner.join(2)
    client.join(2)
    assert cleanup_done.is_set() and client_done.is_set()
    assert client_errors
    assert popen.calls == []
    assert release_observed == [True]
    assert local.candidate is None
    serving.join(1)


@pytest.mark.parametrize("partial", (b"", b'{"schema_version":1'))
def test_cleanup_interrupts_incomplete_ipc_readers_and_releases_lifetime_lock(monkeypatch, tmp_path, partial):
    local = _supervisor(tmp_path, ipc_read_timeout_seconds=10.0)
    local.bind_control_socket()
    serving = threading.Thread(target=supervisor.serve, args=(local,), daemon=True)
    serving.start()
    accepted = threading.Event()
    local._on_ipc_handler_accepted = accepted.set
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(local.socket_path))
    if partial:
        client.sendall(partial)
    assert accepted.wait(1)
    released_after_handlers: list[bool] = []
    original_release = supervisor.SupervisorLock.release
    monkeypatch.setattr(
        supervisor.SupervisorLock, "release",
        lambda lock: (released_after_handlers.append(local._ipc_handlers_quiesced.is_set()), original_release(lock))[1],
    )
    started = time.monotonic()
    local.cleanup()
    assert time.monotonic() - started < 1.0
    assert local._ipc_connections == set()
    assert released_after_handlers == [True]
    client.close()
    serving.join(1)


def test_ipc_incomplete_frame_times_out_without_mutation_and_split_frame_succeeds(tmp_path):
    local = _supervisor(tmp_path, ipc_read_timeout_seconds=0.03)
    local.bind_control_socket()
    serving = threading.Thread(target=supervisor.serve, args=(local,), daemon=True)
    serving.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(0.2)
    client.connect(str(local.socket_path))
    client.sendall(b'{"schema_version":1')
    assert client.recv(1) == b""
    client.close()
    split = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    split.settimeout(0.2)
    split.connect(str(local.socket_path))
    split.sendall(b'{"schema_version":1,"operation":"ping",')
    split.sendall(b'"payload":{}}\n')
    assert json.loads(split.recv(1024))["ok"] is True
    split.close()
    local.cleanup()
    serving.join(1)


def test_shutdown_closes_ipc_admission_and_rejects_new_mutation(tmp_path):
    local = _supervisor(tmp_path)
    local._begin_ipc_shutdown()
    with pytest.raises(supervisor.SupervisorError, match="shutting_down"):
        local.handle_message({
            "schema_version": 1, "operation": "start_candidate", "payload": {"library_root": "/nope"},
        })


def test_stale_or_live_socket_is_never_replaced_without_atomic_ownership(tmp_path):
    path = tmp_path / "local.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path)); stale.close()
    with pytest.raises(supervisor.SupervisorError, match="explicit safe recovery"):
        supervisor.prepare_socket(path)
    assert path.exists()
    path.unlink()
    live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    live.bind(str(path)); live.listen(1)
    with pytest.raises(supervisor.SupervisorError, match="live"):
        supervisor.prepare_socket(path)
    live.close(); path.unlink()


def test_stale_socket_replacement_during_recovery_is_left_untouched(tmp_path):
    path = tmp_path / "local.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path)); stale.close()

    def replace() -> None:
        path.unlink()
        path.write_bytes(b"replacement")

    with pytest.raises(supervisor.SupervisorError, match="explicit safe recovery"):
        supervisor.prepare_socket(path, before_stale_unlink=replace)
    assert path.read_bytes() == b"replacement"


def test_bind_identity_and_cleanup_replacement_races_leave_replacements_untouched(tmp_path):
    local = _supervisor(tmp_path)

    def replace_during_identity() -> None:
        local.socket_path.unlink()
        local.socket_path.write_bytes(b"bind replacement")

    local._before_socket_identity_recording = replace_during_identity
    with pytest.raises(supervisor.SupervisorError, match="changed"):
        local.bind_control_socket()
    assert local.socket_path.read_bytes() == b"bind replacement"

    local = _supervisor(tmp_path)
    local.socket_path.unlink()
    local.bind_control_socket()

    def replace_during_cleanup() -> None:
        local.socket_path.unlink()
        local.socket_path.write_bytes(b"cleanup replacement")

    local._before_socket_cleanup_unlink = replace_during_cleanup
    local.cleanup()
    assert local.socket_path.read_bytes() == b"cleanup replacement"


def test_socket_cleanup_never_follows_replacement_symlink(tmp_path):
    local = _supervisor(tmp_path)
    local.bind_control_socket()
    target = tmp_path / "target"
    target.write_bytes(b"do not touch")

    def replace() -> None:
        local.socket_path.unlink()
        local.socket_path.symlink_to(target)

    local._before_socket_cleanup_unlink = replace
    local.cleanup()
    assert target.read_bytes() == b"do not touch"
    assert local.socket_path.is_symlink()


def test_lifetime_flock_blocks_concurrent_supervisor_without_touching_its_socket(tmp_path):
    socket_path = tmp_path / ".run/local/crateiq-supervisor.sock"
    lock_path = tmp_path / ".run/local/crateiq-supervisor.lock"
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    first = context.Process(target=_hold_control_socket, args=(str(socket_path), str(lock_path), ready, release))
    first.start()
    try:
        assert ready.wait(3)
        second_popen = FakePopen()
        second = _supervisor(tmp_path, second_popen)
        with pytest.raises(supervisor.SupervisorLockUnavailable):
            second.bind_control_socket()
        assert second_popen.calls == []
        assert socket_path.exists()
        assert request("ping", socket_path=socket_path)["pong"] is True
        # Failed cleanup is a no-op: the first process's bound socket remains.
        second.cleanup()
        assert socket_path.exists()
        assert request("status", socket_path=socket_path)["active_child"] is None
    finally:
        release.set()
        first.join(5)
        if first.is_alive():
            first.terminate()
            first.join(1)
    assert first.exitcode == 0
    assert not socket_path.exists()
    restarted = _supervisor(tmp_path)
    restarted.bind_control_socket()
    assert restarted.socket_path.exists()
    restarted.cleanup()


def test_supervisor_lock_pathname_is_recoverable_and_cleanup_checks_socket_identity(tmp_path):
    local = _supervisor(tmp_path)
    lock_path = local.supervisor_lock_path
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("stale pathname", encoding="utf-8")
    local.bind_control_socket()
    assert lock_path.stat().st_mode & 0o777 == 0o600
    original = local.socket_path.lstat()
    local.socket_path.unlink()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(str(local.socket_path)); replacement.listen(1)
    replacement_stat = local.socket_path.lstat()
    assert (replacement_stat.st_dev, replacement_stat.st_ino) != (original.st_dev, original.st_ino)
    local.cleanup()
    assert local.socket_path.exists()
    replacement.close()
    local.socket_path.unlink()


def test_lifetime_lock_is_released_only_after_owned_socket_cleanup(monkeypatch, tmp_path):
    local = _supervisor(tmp_path)
    local.bind_control_socket()
    released_after_socket_cleanup: list[bool] = []
    original_release = supervisor.SupervisorLock.release

    def observe_release(lock):
        released_after_socket_cleanup.append(not local.socket_path.exists())
        original_release(lock)

    monkeypatch.setattr(supervisor.SupervisorLock, "release", observe_release)
    local.cleanup()
    assert released_after_socket_cleanup == [True]


def test_normal_socket_cleanup_removes_public_and_private_entries_and_restarts(tmp_path):
    local = _supervisor(tmp_path)
    local.bind_control_socket()
    bound_name = local._bound_socket_name
    assert bound_name is not None
    bound_path = local.socket_path.with_name(bound_name)
    assert local.socket_path.exists()
    assert bound_path.exists()

    local.cleanup()

    assert not local.socket_path.exists()
    assert not bound_path.exists()
    restarted = _supervisor(tmp_path)
    restarted.bind_control_socket()
    assert restarted.socket_path.exists()
    restarted.cleanup()


def test_failed_start_private_socket_replacement_survives_while_published_socket_is_removed(tmp_path):
    local = _supervisor(tmp_path)
    private_paths: list[Path] = []

    def replace_private_path() -> None:
        bound_name = local._bound_socket_name
        assert bound_name is not None
        bound_path = local.socket_path.with_name(bound_name)
        bound_path.unlink()
        bound_path.write_bytes(b"private replacement")
        private_paths.append(bound_path)

    local._before_socket_identity_recording = replace_private_path
    with pytest.raises(supervisor.SupervisorError, match="pathname changed during bind"):
        local.bind_control_socket()

    assert not local.socket_path.exists()
    assert private_paths[0].read_bytes() == b"private replacement"


@pytest.mark.parametrize("entry", ("public", "private"))
def test_post_exchange_socket_replacement_survives_at_original_path(tmp_path, entry):
    local = _supervisor(tmp_path)
    local.bind_control_socket()
    bound_name = local._bound_socket_name
    assert bound_name is not None
    target_name = local.socket_path.name if entry == "public" else bound_name
    target_path = local.socket_path.with_name(target_name)
    exchanged = threading.Event()

    def replace_after_exchange(name: str) -> None:
        if name != target_name or exchanged.is_set():
            return
        target_path.rmdir()
        target_path.write_bytes(f"{entry} replacement".encode())
        exchanged.set()

    local._after_socket_withdrawal_exchange = replace_after_exchange
    local.cleanup()

    assert exchanged.is_set()
    assert target_path.read_bytes() == f"{entry} replacement".encode()


def test_process_exit_releases_lifetime_flock_for_stale_recovery(tmp_path):
    lock_path = tmp_path / ".run/local/crateiq-supervisor.lock"
    context = multiprocessing.get_context("fork")
    acquired = context.Event()
    holder = context.Process(target=_acquire_lock_then_exit, args=(str(lock_path), acquired))
    holder.start()
    assert acquired.wait(3)
    holder.join(3)
    assert holder.exitcode == 0
    recovered = supervisor.SupervisorLock(lock_path)
    recovered.acquire()
    recovered.release()


@pytest.mark.parametrize("kind", ("symlink", "regular"))
def test_unsafe_socket_path_is_rejected_without_deletion(tmp_path, kind):
    path = tmp_path / "local.sock"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("keep", encoding="utf-8")
        path.symlink_to(target)
    else:
        path.write_text("keep", encoding="utf-8")
    with pytest.raises(supervisor.SupervisorError):
        supervisor.prepare_socket(path)
    assert path.exists() or path.is_symlink()


def test_operation_admission_gate_drains_only_new_library_mutations_and_is_thread_safe():
    gate = OperationAdmissionGate()
    gate.require_library_mutation()
    gate.begin_draining()
    assert gate.state is AdmissionState.DRAINING_FOR_LIBRARY_SWITCH
    with pytest.raises(LibraryOperationDrainingError):
        gate.require_library_mutation()
    # Read-only callers do not ask the gate for mutation admission.
    assert gate.status()["draining"] is True
    threads = [threading.Thread(target=gate.abort_draining) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert gate.state is AdmissionState.RUNNING
    gate.require_library_mutation()


def test_admission_lease_makes_drain_atomic_with_durable_create():
    gate = OperationAdmissionGate()
    admitted = threading.Event()
    permit_finish = threading.Event()
    durable_created = threading.Event()
    drain_returned = threading.Event()

    def create() -> None:
        with gate.admit():
            admitted.set()
            permit_finish.wait(1)
            durable_created.set()

    worker = threading.Thread(target=create)
    worker.start()
    assert admitted.wait(1)
    drainer = threading.Thread(target=lambda: (gate.begin_draining(), drain_returned.set()))
    drainer.start()
    deadline = time.monotonic() + 1
    while gate.state is not AdmissionState.DRAINING_FOR_LIBRARY_SWITCH and time.monotonic() < deadline:
        time.sleep(0.001)
    with pytest.raises(LibraryOperationDrainingError):
        with gate.admit():
            pass
    assert not drain_returned.is_set()
    permit_finish.set()
    worker.join(1); drainer.join(1)
    assert durable_created.is_set()
    assert drain_returned.is_set()
    assert gate.status()["in_flight_admissions"] == 0


@pytest.mark.parametrize("workflow", ("process_all", "bulk_waveform"))
def test_long_lived_operation_scope_delays_drain_and_allows_only_its_descendants(workflow):
    gate = OperationAdmissionGate()
    with gate.admit():
        scope = gate.open_operation_scope()
    drain_returned = threading.Event()
    drainer = threading.Thread(target=lambda: (gate.begin_draining(), drain_returned.set()))
    drainer.start()
    deadline = time.monotonic() + 1
    while gate.state is not AdmissionState.DRAINING_FOR_LIBRARY_SWITCH and time.monotonic() < deadline:
        time.sleep(0.001)
    assert not drain_returned.is_set()
    with pytest.raises(LibraryOperationDrainingError):
        with gate.admit():
            pass
    descendant_created = threading.Event()
    with gate.admit_descendant(scope):
        descendant_created.set()
    assert descendant_created.is_set()
    assert not drain_returned.is_set()
    scope.release()
    drainer.join(1)
    assert drain_returned.is_set(), workflow
    with pytest.raises(LibraryOperationDrainingError):
        with gate.admit_descendant(scope):
            pass


def test_operation_scope_exceptional_release_prevents_draining_leak():
    gate = OperationAdmissionGate()
    with gate.admit():
        scope = gate.open_operation_scope()
    try:
        raise RuntimeError("simulated operation failure")
    except RuntimeError:
        scope.release()
    gate.begin_draining()
    assert gate.status()["active_operation_scopes"] == 0


def test_activation_state_phase_invariants_reject_incomplete_combinations(tmp_path):
    store = supervisor.ActivationStateStore(tmp_path / "state.json")
    valid_preparing = {
        **store.empty(), "phase": "preparing", "activation_id": "a",
        "requested_root": str(tmp_path.resolve()),
        "requested_library_key": supervisor.library_key_for_root(tmp_path),
    }
    store.write(valid_preparing)
    assert store.read()["phase"] == "preparing"
    invalid_cases = [
        {**store.empty(), "phase": "preparing", "requested_root": str(tmp_path.resolve())},
        {**store.empty(), "phase": "candidate_started", "activation_id": "a", "requested_root": str(tmp_path.resolve())},
        {**store.empty(), "phase": "candidate_verified", "activation_id": "a", "requested_root": str(tmp_path.resolve())},
        {**store.empty(), "phase": "idle", "activation_id": "a"},
    ]
    for invalid in invalid_cases:
        with pytest.raises(supervisor.MalformedActivationState):
            store.write(invalid)


def test_monitor_reaps_normal_and_candidate_crash_and_clears_state(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library")
    local = _supervisor(tmp_path)
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda child: True)
    candidate = local.start_candidate(str(root))
    candidate.process.returncode = 3
    local.monitor_children()
    assert candidate.process.wait_calls >= 1
    assert local.candidate is None
    assert local.state_store.read()["phase"] == "idle"
    active = local.start_active(role="rootless", port=8020, bind_host="127.0.0.1", library_root=None, access_mode="local")
    active.process.returncode = 1
    local.monitor_children()
    assert local.status()["active_child"] is None


def test_terminate_waits_after_sigterm_and_sigkill_fallback(monkeypatch, tmp_path):
    local = _supervisor(tmp_path)
    normal = FakeProcess()
    child = supervisor.ChildProcess(supervisor.ChildSpec("active", 8020, "127.0.0.1", None, "local"), "id", "token", normal)
    monkeypatch.setattr(supervisor.os, "killpg", lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    local._terminate(child)
    assert normal.wait_calls == 1

    class Stubborn(FakeProcess):
        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise TimeoutError()
            self.returncode = -9
            return -9

    stubborn = Stubborn()
    child = supervisor.ChildProcess(supervisor.ChildSpec("active", 8020, "127.0.0.1", None, "local"), "id", "token", stubborn)
    local._terminate(child)
    assert stubborn.wait_calls == 2
    assert stubborn.terminated is True


def test_parent_death_guard_fails_closed(monkeypatch):
    monkeypatch.setattr(supervised_backend.sys, "platform", "linux")
    monkeypatch.setattr(supervised_backend.os, "getppid", lambda: 42)

    class Libc:
        def prctl(self, *_): return 0
    monkeypatch.setattr(supervised_backend.ctypes, "CDLL", lambda *args, **kwargs: Libc())
    supervised_backend._install_parent_death_guard()

    class FailingLibc:
        def prctl(self, *_): return -1
    monkeypatch.setattr(supervised_backend.ctypes, "CDLL", lambda *args, **kwargs: FailingLibc())
    with pytest.raises(supervised_backend.ParentDeathGuardError):
        supervised_backend._install_parent_death_guard()
    monkeypatch.setattr(supervised_backend.sys, "platform", "darwin")
    with pytest.raises(supervised_backend.ParentDeathGuardError):
        supervised_backend._install_parent_death_guard()


def test_parent_death_guard_rejects_parent_change(monkeypatch):
    parents = iter((42, 77))
    monkeypatch.setattr(supervised_backend.sys, "platform", "linux")
    monkeypatch.setattr(supervised_backend.os, "getppid", lambda: next(parents))
    class Libc:
        def prctl(self, *_): return 0
    monkeypatch.setattr(supervised_backend.ctypes, "CDLL", lambda *args, **kwargs: Libc())
    with pytest.raises(supervised_backend.ParentDeathGuardError, match="changed"):
        supervised_backend._install_parent_death_guard()


def test_child_environment_preserves_supported_values_without_arbitrary_parent_env(monkeypatch, tmp_path):
    values = {
        "CRATEIQ_WAVEFORMS_ENABLED": "0", "CRATEIQ_WAVEFORM_CACHE_DIR": "/safe/cache",
        "CRATEIQ_WAVEFORM_MAX_CONCURRENCY": "2", "CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE": "9",
        "CRATEIQ_WAVEFORM_MAX_CACHE_BYTES": "1024", "AUBIO_BIN": "/safe/aubio",
        "KEYFINDER_BIN": "/safe/keyfinder", "FFMPEG_BIN": "/safe/ffmpeg",
        "FFPROBE_BIN": "/safe/ffprobe", "FPCALC_BIN": "/safe/fpcalc", "UNRELATED_SECRET": "nope",
        "CRATEIQ_LIBRARY_ROOT": "/stale", "DJ_MUSIC_ROOT": "/stale",
    }
    for key, value in values.items(): monkeypatch.setenv(key, value)
    local = _supervisor(tmp_path)
    rootless = local.child_environment(supervisor.ChildSpec("rootless", 8020, "127.0.0.1", None, "local"), "child")
    for key in values:
        if key not in {"UNRELATED_SECRET", "CRATEIQ_LIBRARY_ROOT", "DJ_MUSIC_ROOT"}:
            assert rootless[key] == values[key]
    assert "UNRELATED_SECRET" not in rootless
    assert "CRATEIQ_LIBRARY_ROOT" not in rootless and "DJ_MUSIC_ROOT" not in rootless
    root = _managed_root(tmp_path / "library")
    bound = local.child_environment(supervisor.ChildSpec("active", 8020, "127.0.0.1", root, "local"), "child")
    assert bound["CRATEIQ_LIBRARY_ROOT"] == str(root.resolve())
    assert bound["DJ_MUSIC_ROOT"] == str(root.resolve())


def test_candidate_port_retries_forbidden_ports(monkeypatch, tmp_path):
    local = _supervisor(tmp_path)
    ports = iter((8020, 8020, 49001))
    class Listener:
        def __init__(self): self.port = None
        def setsockopt(self, *_): pass
        def bind(self, *_): self.port = next(ports)
        def listen(self, *_): pass
        def getsockname(self): return ("127.0.0.1", self.port)
        def close(self): pass
    monkeypatch.setattr(supervisor.socket, "socket", lambda *_args, **_kwargs: Listener())
    listener = local._reserve_listener(supervisor.ChildSpec("candidate", 0, "127.0.0.1", None, "local"))
    assert listener.getsockname()[1] == 49001


def test_main_binds_control_socket_before_spawning_active_child(monkeypatch, tmp_path):
    order: list[str] = []
    monkeypatch.setattr(supervisor.LocalSupervisor, "bind_control_socket", lambda self: order.append("bind"))
    monkeypatch.setattr(
        supervisor.LocalSupervisor,
        "start_active",
        lambda self, **_kwargs: order.append("spawn"),
    )
    monkeypatch.setattr(supervisor, "serve", lambda _local: order.append("serve"))
    assert supervisor.main([
        "--socket", str(tmp_path / "socket"), "--state", str(tmp_path / "state"),
        "--lock", str(tmp_path / "activation.lock"), "--active-role", "rootless",
        "--port", "8020", "--bind-host", "127.0.0.1", "--access-mode", "local",
    ]) == 0
    assert order[:2] == ["bind", "spawn"]
