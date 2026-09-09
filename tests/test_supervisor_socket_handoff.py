"""Real loopback-socket regressions for stable-port supervisor handoff."""
from __future__ import annotations

import errno
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.app import supervisor
from backend.app.services import library_registry_service as registry


_TEST_BACKEND = r"""
import ctypes
import json
import os
import signal
import socket
import sys
import urllib.parse


expected_parent = os.getppid()
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGTERM) != 0 or os.getppid() != expected_parent:
    raise SystemExit(70)


def stop(_signum, _frame):
    descriptor = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (os.environ["CRATEIQ_BACKEND_INSTANCE_ID"] + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    raise SystemExit(0)


signal.signal(signal.SIGTERM, stop)
listener = socket.socket(fileno=int(sys.argv[1]))
mode = sys.argv[2]
while True:
    connection, _address = listener.accept()
    try:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = connection.recv(4096)
            if not chunk:
                break
            request += chunk
        request_line = request.split(b"\r\n", 1)[0].decode("ascii")
        path = request_line.split(" ", 2)[1]
        if path.startswith("/api/internal/supervisor-identity"):
            gate_path = mode.removeprefix("gate:") if mode.startswith("gate:") else None
            gated = gate_path is not None and not os.path.exists(gate_path)
            identity = {
                "instance_id": (
                    "wrong-instance"
                    if mode == "wrong-identity" or gated
                    else os.environ["CRATEIQ_BACKEND_INSTANCE_ID"]
                ),
                "supervisor_instance_id": os.environ["CRATEIQ_SUPERVISOR_INSTANCE_ID"],
                "role": os.environ["CRATEIQ_BACKEND_START_ROLE"],
                "port": int(os.environ["CRATEIQ_BACKEND_BOUND_PORT"]),
                "library_root": os.environ.get("CRATEIQ_LIBRARY_ROOT"),
                "library_key": os.environ.get("CRATEIQ_BACKEND_LIBRARY_KEY") or None,
                "verification_token": os.environ["CRATEIQ_BACKEND_VERIFY_TOKEN"],
                "ready": not gated,
            }
            payload = {"identity": identity}
        elif path.startswith("/api/internal/supervisor-admission"):
            action = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get("action", [""])[0]
            payload = {"admission": {"draining": action == "begin_draining"}}
        else:
            payload = {"error": "not_found"}
        body = json.dumps(payload).encode("utf-8")
        connection.sendall(
            b"HTTP/1.1 200 OK\r\n"
            + b"Content-Type: application/json\r\n"
            + b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            + b"Connection: close\r\n\r\n"
            + body
        )
        connection.shutdown(socket.SHUT_WR)
    finally:
        connection.close()
"""


class RecordingProcess:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self.pid = process.pid
        self.wait_calls = 0

    def poll(self):
        return self._process.poll()

    def wait(self, timeout=None):
        self.wait_calls += 1
        return self._process.wait(timeout=timeout)

    def terminate(self):
        return self._process.terminate()

    def kill(self):
        return self._process.kill()


class RecordingPopen:
    """Start real children while recording the supervisor-side socket inode."""

    def __init__(self, sigterm_log: Path) -> None:
        self.sigterm_log = sigterm_log
        self.processes: list[RecordingProcess] = []
        self.listener_targets: list[str] = []
        self.instance_ids: list[str] = []

    def __call__(self, command, **kwargs):
        listener_fd = int(kwargs["pass_fds"][0])
        self.listener_targets.append(os.readlink(f"/proc/self/fd/{listener_fd}"))
        self.instance_ids.append(kwargs["env"]["CRATEIQ_BACKEND_INSTANCE_ID"])
        process = RecordingProcess(subprocess.Popen(command, **kwargs))
        self.processes.append(process)
        return process

    def signaled_instance_ids(self) -> set[str]:
        try:
            return set(self.sigterm_log.read_text(encoding="utf-8").splitlines())
        except FileNotFoundError:
            return set()


def _managed_root(path: Path) -> Path:
    path.mkdir()
    for zone in registry.ZONE_NAMES:
        (path / zone).mkdir()
    (path / registry.WORKSPACE_MARKER_NAME).write_text('{"version": 1}', encoding="utf-8")
    return path


def _open_fd_targets() -> set[str]:
    targets: set[str] = set()
    for entry in Path("/proc/self/fd").iterdir():
        try:
            targets.add(os.readlink(entry))
        except FileNotFoundError:
            pass
    return targets


def _assert_not_retained_by_supervisor(listener_target: str) -> None:
    assert listener_target not in _open_fd_targets()


def _assert_live_owner_is_exclusive(port: int) -> None:
    contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    contender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError) as caught:
            contender.bind(("127.0.0.1", port))
        assert caught.value.errno == errno.EADDRINUSE
    finally:
        contender.close()


def _socket_supervisor(
    tmp_path: Path,
    monkeypatch,
    *,
    fail_promoted: bool = False,
    promoted_gate: Path | None = None,
    active_timeout: float = 0.35,
):
    popen = RecordingPopen(tmp_path / "child-sigterm.log")
    local = supervisor.LocalSupervisor(
        repo_root=tmp_path,
        socket_path=tmp_path / ".run/local/crateiq-supervisor.sock",
        state_store=supervisor.ActivationStateStore(tmp_path / ".run/local/library_activation_state.json"),
        lock_path=tmp_path / ".run/local/library_activation.lock",
        supervisor_lock_path=tmp_path / ".run/local/crateiq-supervisor.lock",
        python_executable=sys.executable,
        popen=popen,
        readiness_timeout_seconds=0.35,
        active_readiness_timeout_seconds=active_timeout,
    )
    launches = 0

    def child_command(_spec, inherited_fd):
        nonlocal launches
        launches += 1
        if promoted_gate is not None and launches == 3:
            mode = f"gate:{promoted_gate}"
        else:
            mode = "wrong-identity" if fail_promoted and launches == 3 else "normal"
        return [
            sys.executable, "-c", _TEST_BACKEND, str(inherited_fd), mode,
            str(popen.sigterm_log),
        ]

    monkeypatch.setattr(local, "child_command", child_command)
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", tmp_path / ".run/local/crateiq.env")
    return local, popen


def test_rootless_to_b_reuses_real_stable_listener_without_stale_owner(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    local, popen = _socket_supervisor(tmp_path, monkeypatch)
    try:
        old_rootless = local.start_active(
            role="rootless", port=0, bind_host="127.0.0.1", library_root=None, access_mode="local",
        )
        stable_port = old_rootless.spec.port
        assert local._wait_for_verified_identity(old_rootless)
        old_listener_target = popen.listener_targets[0]
        _assert_not_retained_by_supervisor(old_listener_target)

        result = local.handoff_library(str(root_b))

        assert result["result"] == "activated"
        assert result["active_port"] == stable_port
        assert old_rootless.process.poll() is not None
        assert local.candidate is None
        assert local.active is not None
        assert local.active.spec.library_root == root_b.resolve()
        assert local.active.spec.port == stable_port
        assert local._wait_for_verified_identity(local.active)
        assert sum(process.poll() is None for process in popen.processes) == 1
        _assert_not_retained_by_supervisor(old_listener_target)
        for listener_target in popen.listener_targets:
            _assert_not_retained_by_supervisor(listener_target)
        _assert_live_owner_is_exclusive(stable_port)
    finally:
        local.cleanup()


def test_rollback_replacement_reuses_real_stable_listener_after_promoted_failure(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    local, popen = _socket_supervisor(tmp_path, monkeypatch, fail_promoted=True)
    try:
        old_rootless = local.start_active(
            role="rootless", port=0, bind_host="127.0.0.1", library_root=None, access_mode="local",
        )
        stable_port = old_rootless.spec.port
        assert local._wait_for_verified_identity(old_rootless)
        old_listener_target = popen.listener_targets[0]

        with pytest.raises(supervisor.SupervisorError, match="handoff rolled back: promoted backend readiness"):
            local.handoff_library(str(root_b))

        assert len(popen.processes) == 4
        assert all(process.poll() is not None for process in popen.processes[:3])
        assert local.candidate is None
        assert local.active is not None
        assert local.active.spec.role == "rootless"
        assert local.active.spec.library_root is None
        assert local.active.spec.port == stable_port
        assert local._wait_for_verified_identity(local.active)
        assert local.state_store.read()["phase"] == "idle"
        assert sum(process.poll() is None for process in popen.processes) == 1
        _assert_not_retained_by_supervisor(old_listener_target)
        for listener_target in popen.listener_targets:
            _assert_not_retained_by_supervisor(listener_target)
        _assert_live_owner_is_exclusive(stable_port)
    finally:
        local.cleanup()


def test_promoted_backend_ready_after_more_than_two_seconds_remains_active(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    readiness_gate = tmp_path / "promoted-ready"
    local, popen = _socket_supervisor(
        tmp_path,
        monkeypatch,
        promoted_gate=readiness_gate,
        active_timeout=5.0,
    )
    timer: threading.Timer | None = None
    try:
        old_rootless = local.start_active(
            role="rootless", port=0, bind_host="127.0.0.1", library_root=None, access_mode="local",
        )
        stable_port = old_rootless.spec.port
        assert local._wait_for_verified_identity(old_rootless)

        # The gate opens relative to the promoted child launch, so the test
        # models delayed application readiness rather than an arbitrary pause
        # before the handoff begins.
        original_child_command = local.child_command
        launch_count = 0

        def delayed_promoted_command(spec, inherited_fd):
            nonlocal launch_count, timer
            launch_count += 1
            command = original_child_command(spec, inherited_fd)
            if launch_count == 2:  # candidate is first; promoted B is second
                timer = threading.Timer(2.5, readiness_gate.touch)
                timer.start()
            return command

        monkeypatch.setattr(local, "child_command", delayed_promoted_command)
        started = time.monotonic()
        result = local.handoff_library(str(root_b))
        elapsed = time.monotonic() - started

        assert elapsed >= 2.0
        assert result["result"] == "activated"
        assert result["active_port"] == stable_port
        assert old_rootless.process.poll() is not None
        assert local.active is not None
        assert local.active.spec.library_root == root_b.resolve()
        assert local.active.process.poll() is None
        assert local._wait_for_verified_identity(local.active)
        assert sum(process.poll() is None for process in popen.processes) == 1
        _assert_live_owner_is_exclusive(stable_port)
    finally:
        if timer is not None:
            timer.cancel()
        local.cleanup()


def _registered_target(root: Path) -> dict[str, str]:
    return {
        "library_id": "library_" + "b" * 64,
        "library_root": str(root.resolve()),
        "library_key": supervisor.library_key_for_root(root) or "",
        "classification": "managed_workspace",
    }


def test_promoted_backend_survives_activation_worker_return_and_monitor_cycle(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    local, popen = _socket_supervisor(tmp_path, monkeypatch)
    target = _registered_target(root_b)
    monkeypatch.setattr(registry, "resolve_registered_library", lambda _library_id: dict(target))
    monkeypatch.setattr(registry, "mark_registered_library_opened", lambda _library_id: None)
    try:
        old_rootless = local.start_active(
            role="rootless", port=0, bind_host="127.0.0.1", library_root=None, access_mode="local",
        )
        stable_port = old_rootless.spec.port
        assert local._wait_for_verified_identity(old_rootless)

        started = local.start_registered_library_activation(target)
        worker = local._activation_thread
        assert started["result"] == "activation_started"
        assert worker is not None
        worker.join(timeout=5)
        assert not worker.is_alive()

        # Worker cleanup and the Linux parent-thread death boundary are now
        # complete. A real post-return identity response plus an explicit
        # monitor cycle proves B remains the owned stable-port child.
        local.monitor_children()
        promoted = local.active
        assert promoted is not None
        assert promoted.process is popen.processes[2]
        assert local._wait_for_verified_identity(promoted)
        local.monitor_children()

        status = local.status()
        assert status["activation"]["status"] == "succeeded"
        assert status["activation"]["result"] == "activated"
        assert local.state_store.read()["phase"] == "idle"
        assert local.state_store.read()["active_instance_id"] is None
        assert local.active is promoted
        assert local.candidate is None
        assert promoted.process.poll() is None
        assert promoted.spec.port == stable_port
        assert promoted.spec.library_root == root_b.resolve()
        assert sum(process.poll() is None for process in popen.processes) == 1
        assert promoted.instance_id not in popen.signaled_instance_ids()
        assert promoted.process.wait_calls == 0
        _assert_live_owner_is_exclusive(stable_port)
    finally:
        local.cleanup()


def test_uncommitted_promoted_failure_is_terminated_and_reaped_after_worker_return(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    local, popen = _socket_supervisor(tmp_path, monkeypatch, fail_promoted=True)
    target = _registered_target(root_b)
    monkeypatch.setattr(registry, "resolve_registered_library", lambda _library_id: dict(target))
    monkeypatch.setattr(registry, "mark_registered_library_opened", lambda _library_id: None)
    try:
        old_rootless = local.start_active(
            role="rootless", port=0, bind_host="127.0.0.1", library_root=None, access_mode="local",
        )
        stable_port = old_rootless.spec.port
        assert local._wait_for_verified_identity(old_rootless)

        local.start_registered_library_activation(target)
        worker = local._activation_thread
        assert worker is not None
        worker.join(timeout=5)
        assert not worker.is_alive()
        local.monitor_children()

        promoted_process = popen.processes[2]
        promoted_instance_id = popen.instance_ids[2]
        status = local.status()
        assert status["activation"]["status"] == "failed"
        assert status["activation"]["error_code"] == "handoff_failed"
        assert local.state_store.read()["phase"] == "idle"
        assert promoted_process.poll() is not None
        assert promoted_process.wait_calls >= 1
        assert promoted_instance_id in popen.signaled_instance_ids()
        assert local.active is not None
        assert local.active.process is popen.processes[3]
        assert local.active.instance_id != promoted_instance_id
        assert local.active.spec.role == "rootless"
        assert local.active.spec.port == stable_port
        assert local.candidate is None
        assert sum(process.poll() is None for process in popen.processes) == 1
        _assert_live_owner_is_exclusive(stable_port)
    finally:
        local.cleanup()
