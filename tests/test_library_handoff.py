"""Deterministic no-live-service coverage for supervisor library handoff."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import supervisor
from backend.app.core.library_key import library_key_for_root
from backend.app.services import library_registry_service as registry


class FakeProcess:
    next_pid = 61000

    def __init__(self) -> None:
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.returncode: int | None = None
        self.wait_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9


class FakePopen:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, command, **kwargs):
        process = FakeProcess()
        self.calls.append({"command": command, **kwargs})
        self.processes.append(process)
        return process


def _managed_root(path: Path) -> Path:
    path.mkdir()
    for zone in registry.ZONE_NAMES:
        (path / zone).mkdir()
    (path / registry.WORKSPACE_MARKER_NAME).write_text('{"version": 1}', encoding="utf-8")
    return path


def _local(tmp_path: Path, popen: FakePopen) -> supervisor.LocalSupervisor:
    return supervisor.LocalSupervisor(
        repo_root=tmp_path,
        socket_path=tmp_path / ".run/local/crateiq-supervisor.sock",
        state_store=supervisor.ActivationStateStore(tmp_path / ".run/local/library_activation_state.json"),
        lock_path=tmp_path / ".run/local/library_activation.lock",
        supervisor_lock_path=tmp_path / ".run/local/crateiq-supervisor.lock",
        python_executable="/safe/python",
        popen=popen,
        readiness_timeout_seconds=0.01,
    )


def _ready_handoff(monkeypatch, local, saved, blockers=None, gate_calls=None):
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda _child: True)
    monkeypatch.setattr(
        supervisor, "inspect_switch_blockers",
        blockers or (lambda _key: {"can_switch": True, "blockers": [], "ambiguous_legacy_active": []}),
    )
    calls = gate_calls if gate_calls is not None else []
    monkeypatch.setattr(local, "_set_admission_state", lambda child, action: calls.append((child.instance_id, action)) or True)
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", local.repo_root / ".run/local/crateiq.env")
    original_write = registry.write_compatibility_root

    def write_and_record(root):
        original_write(root)
        saved.append(Path(root))

    monkeypatch.setattr(registry, "write_compatibility_root", write_and_record)


def test_compatibility_root_write_is_literal_atomic_data_without_registry_recency(monkeypatch, tmp_path):
    root = _managed_root(tmp_path / "library")
    local_env = tmp_path / ".run/local/crateiq.env"
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", local_env)
    registry.write_compatibility_root(root)
    assert local_env.read_text(encoding="utf-8") == (
        "# Managed by CrateIQ. This file is local-only and contains no secrets.\n"
        f"CRATEIQ_LIBRARY_ROOT={root.resolve()}\n"
    )
    assert local_env.stat().st_mode & 0o777 == 0o600


def test_handoff_a_to_b_drains_blocks_promotes_and_updates_saved_root(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18020, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    gate_calls: list[tuple[str, str]] = []
    inspected: list[str] = []
    _ready_handoff(
        monkeypatch, local, saved, gate_calls=gate_calls,
        blockers=lambda key: inspected.append(key) or {"can_switch": True, "blockers": [], "ambiguous_legacy_active": []},
    )

    result = local.handoff_library(str(root_b))

    assert result["result"] == "activated"
    assert result["library_key"] == library_key_for_root(root_b)
    assert local.active is not None
    assert local.active.spec.library_root == root_b.resolve()
    assert local.active.spec.port == 18020
    assert local.candidate is None
    assert active_a.process.wait_calls >= 1
    assert saved == [root_b.resolve()]
    assert inspected == [library_key_for_root(root_a)]
    assert gate_calls == [(active_a.instance_id, "begin_draining")]
    status = local.status()
    assert status["active_child"]["library_key"] == library_key_for_root(root_b)
    assert status["candidate_child"] is None
    assert status["activation_phase"] == "idle"
    assert local.state_store.read()["failure_reason"] is None
    assert len(popen.processes) == 3
    assert all(process.wait_calls >= 1 for process in popen.processes[:2])
    assert popen.calls[0]["env"]["CRATEIQ_BACKEND_LIBRARY_KEY"] == library_key_for_root(root_a)
    assert popen.calls[1]["env"]["CRATEIQ_BACKEND_LIBRARY_KEY"] == library_key_for_root(root_b)
    assert popen.calls[2]["env"]["CRATEIQ_BACKEND_LIBRARY_KEY"] == library_key_for_root(root_b)


def test_rootless_to_b_promotes_without_a_gate_or_blocker_inspection(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="rootless", port=18021, bind_host="127.0.0.1", library_root=None, access_mode="lan")
    saved: list[Path] = []
    gate_calls: list[tuple[str, str]] = []
    _ready_handoff(monkeypatch, local, saved, gate_calls=gate_calls)

    result = local.handoff_library(str(root_b))

    assert result["result"] == "activated"
    assert local.active is not None and local.active.spec.library_root == root_b.resolve()
    assert local.active.spec.access_mode == "lan"
    assert gate_calls == []
    assert saved == [root_b.resolve()]


def test_same_library_is_a_canonical_noop(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    alias = tmp_path / "alias"
    alias.symlink_to(root_a, target_is_directory=True)
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active = local.start_active(role="active", port=18022, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    result = local.handoff_library(str(alias))
    assert result["result"] == "already_active"
    assert result["active_backend_instance_id"] == active.instance_id
    assert len(popen.processes) == 1


@pytest.mark.parametrize("blocker_kind", ("active", "legacy", "foreign"))
def test_persisted_blockers_abort_drain_and_leave_a_usable(monkeypatch, tmp_path, blocker_kind):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18023, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    gate_calls: list[tuple[str, str]] = []
    if blocker_kind == "legacy":
        payload = {"can_switch": False, "blockers": [], "ambiguous_legacy_active": [{"reason": "ambiguous_legacy_active"}]}
    elif blocker_kind == "foreign":
        payload = {"can_switch": False, "blockers": [{"reason": "foreign_active_installation_inconsistency"}], "ambiguous_legacy_active": []}
    else:
        payload = {"can_switch": False, "blockers": [{"reason": "active_current_library"}], "ambiguous_legacy_active": []}
    _ready_handoff(monkeypatch, local, saved, blockers=lambda _key: payload, gate_calls=gate_calls)

    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is active_a
    assert local.candidate is None
    assert active_a.process.poll() is None
    assert saved == []
    assert [action for _, action in gate_calls] == ["begin_draining", "abort_draining"]
    assert local.state_store.read()["phase"] == "idle"


def test_candidate_identity_failure_reaps_b_and_keeps_a(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18024, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda child: child.spec.role != "candidate")
    monkeypatch.setattr(registry, "write_compatibility_root", lambda root: saved.append(Path(root)))

    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is active_a
    assert local.candidate is None
    assert popen.processes[1].wait_calls >= 1
    assert saved == []


def test_candidate_start_failure_keeps_a_and_never_advances_saved_root(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18028, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    original_popen = local._popen
    calls = 0

    def fail_candidate(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("candidate spawn failed")
        return original_popen(command, **kwargs)

    # The active child was already created; this is the candidate invocation.
    local._popen = fail_candidate
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda _child: True)
    monkeypatch.setattr(registry, "write_compatibility_root", lambda root: saved.append(Path(root)))
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))
    assert local.active is active_a
    assert local.candidate is None
    assert saved == []


def test_candidate_exit_during_verification_is_reaped_and_a_stays_usable(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18029, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []

    def candidate_exits(child):
        if child.spec.role == "candidate":
            child.process.returncode = 3
            return False
        return True

    monkeypatch.setattr(local, "_wait_for_verified_identity", candidate_exits)
    monkeypatch.setattr(registry, "write_compatibility_root", lambda root: saved.append(Path(root)))
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))
    assert local.active is active_a
    assert popen.processes[1].wait_calls >= 1
    assert saved == []


def test_runtime_drain_failure_aborts_a_gate_and_stops_before_old_retirement(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18030, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    gate_actions: list[str] = []
    monkeypatch.setattr(local, "_wait_for_verified_identity", lambda _child: True)
    monkeypatch.setattr(
        local, "_set_admission_state",
        lambda _child, action: gate_actions.append(action) or action == "abort_draining",
    )
    monkeypatch.setattr(registry, "write_compatibility_root", lambda root: saved.append(Path(root)))
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))
    assert local.active is active_a
    assert active_a.process.poll() is None
    assert gate_actions == ["begin_draining", "abort_draining"]
    assert saved == []


def test_promoted_b_verification_failure_restarts_verified_a(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="active", port=18025, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    monkeypatch.setattr(
        local, "_wait_for_verified_identity",
        lambda child: not (child.spec.role == "active" and child.spec.library_root == root_b.resolve()),
    )
    monkeypatch.setattr(supervisor, "inspect_switch_blockers", lambda _key: {"can_switch": True, "blockers": [], "ambiguous_legacy_active": []})
    monkeypatch.setattr(local, "_set_admission_state", lambda *_: True)
    monkeypatch.setattr(registry, "write_compatibility_root", lambda root: saved.append(Path(root)))

    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is not None and local.active.spec.library_root == root_a.resolve()
    assert local.candidate is None
    assert saved == []
    assert len(popen.processes) == 4
    assert popen.processes[2].wait_calls >= 1


def test_failed_promoted_and_failed_rollback_backend_remain_fail_closed(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(
        role="rootless", port=18041, bind_host="127.0.0.1", library_root=None, access_mode="local",
    )
    monkeypatch.setattr(
        local,
        "_wait_for_verified_identity",
        lambda child: child.spec.role == "candidate",
    )
    monkeypatch.setattr(registry, "LOCAL_ENV_PATH", tmp_path / ".run/local/crateiq.env")

    with pytest.raises(supervisor.SupervisorError, match="failed closed"):
        local.handoff_library(str(root_b))

    state = local.state_store.read()
    assert local.active is None
    assert local.candidate is None
    assert all(process.poll() is not None for process in popen.processes)
    assert state["phase"] == "fail_closed"
    assert state["active_instance_id"] is None
    assert state["failure_stage"] == "rollback_backend_start"
    assert local.status()["activation_incomplete"] is True


def test_saved_root_write_failure_restarts_a_without_false_success(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="active", port=18026, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    _ready_handoff(monkeypatch, local, [])
    monkeypatch.setattr(registry, "write_compatibility_root", lambda _root: (_ for _ in ()).throw(OSError("disk failure")))

    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is not None and local.active.spec.library_root == root_a.resolve()
    assert local.candidate is None
    assert len(popen.processes) == 4


def test_old_a_termination_failure_reopens_verified_a_without_promoting_b(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18027, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    gate_calls: list[tuple[str, str]] = []
    _ready_handoff(monkeypatch, local, saved, gate_calls=gate_calls)
    original_terminate = local._terminate

    def fail_only_old(child):
        if child is active_a:
            raise supervisor.SupervisorError("old child did not exit")
        return original_terminate(child)

    monkeypatch.setattr(local, "_terminate", fail_only_old)
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is active_a
    assert active_a.process.poll() is None
    assert len(popen.processes) == 2
    assert saved == []
    assert [action for _, action in gate_calls] == ["begin_draining", "abort_draining"]


def test_candidate_termination_failure_retains_b_and_fails_closed(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18031, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)
    original_terminate = local._terminate

    def fail_candidate(child):
        if child.spec.role == "candidate":
            raise supervisor.SupervisorError("candidate remains alive")
        return original_terminate(child)

    monkeypatch.setattr(local, "_terminate", fail_candidate)
    with pytest.raises(supervisor.SupervisorError, match="failed closed"):
        local.handoff_library(str(root_b))

    assert local.active is active_a and active_a.process.poll() is None
    assert local.candidate is not None and local.candidate.process.poll() is None
    assert local.state_store.read()["phase"] == "fail_closed"
    assert local.status()["activation_incomplete"] is True
    assert saved == []
    assert len(popen.processes) == 2


def test_promoted_b_termination_failure_retains_b_and_never_spawns_replacement(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="active", port=18032, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)
    original_terminate = local._terminate
    monkeypatch.setattr(
        local,
        "_wait_for_verified_identity",
        lambda child: not (child.spec.role == "active" and child.spec.library_root == root_b.resolve()),
    )

    def fail_promoted_b(child):
        if child.spec.role == "active" and child.spec.library_root == root_b.resolve():
            raise supervisor.SupervisorError("promoted B remains alive")
        return original_terminate(child)

    monkeypatch.setattr(local, "_terminate", fail_promoted_b)
    with pytest.raises(supervisor.SupervisorError, match="failed closed"):
        local.handoff_library(str(root_b))

    assert local.active is not None and local.active.spec.library_root == root_b.resolve()
    assert local.active.process.poll() is None
    assert local.candidate is None
    assert local.state_store.read()["phase"] == "fail_closed"
    assert len(popen.processes) == 3


def test_post_replace_saved_root_failure_restores_a_before_clean_rollback(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="active", port=18033, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)
    registry.write_compatibility_root(root_a)
    real_write = registry.write_compatibility_root

    def write_with_late_directory_fsync(root):
        real_fsync = registry.os.fsync
        calls = 0

        def fail_once_after_replace(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("late directory fsync failure")
            return real_fsync(descriptor)

        registry.os.fsync = fail_once_after_replace
        try:
            return real_write(root)
        finally:
            registry.os.fsync = real_fsync

    monkeypatch.setattr(registry, "write_compatibility_root", write_with_late_directory_fsync)
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is not None and local.active.spec.library_root == root_a.resolve()
    assert local.candidate is None
    assert registry.compatibility_root_matches(root_a)
    assert local.state_store.read()["phase"] == "idle"
    assert all(process.poll() is not None for process in popen.processes[1:3])


def test_saved_root_restoration_failure_is_fail_closed(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="active", port=18034, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)
    registry.write_compatibility_root(root_a)
    real_write = registry.write_compatibility_root

    def write_with_late_directory_fsync(root):
        real_fsync = registry.os.fsync
        calls = 0

        def fail_once_after_replace(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("late directory fsync failure")
            return real_fsync(descriptor)

        registry.os.fsync = fail_once_after_replace
        try:
            return real_write(root)
        finally:
            registry.os.fsync = real_fsync

    monkeypatch.setattr(registry, "write_compatibility_root", write_with_late_directory_fsync)
    monkeypatch.setattr(registry, "restore_compatibility_root_state", lambda _previous: (_ for _ in ()).throw(OSError("restore failed")))
    with pytest.raises(supervisor.SupervisorError, match="failed closed"):
        local.handoff_library(str(root_b))

    assert local.active is None and local.candidate is None
    assert local.state_store.read()["phase"] == "fail_closed"
    assert registry.compatibility_root_matches(root_b)
    assert all(process.poll() is not None for process in popen.processes[1:3])


def test_activation_state_write_failure_after_b_config_rolls_back_runtime_and_config(monkeypatch, tmp_path):
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="active", port=18035, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)
    registry.write_compatibility_root(root_a)
    original_write = local.state_store.write

    def fail_activated_write(state):
        if state["phase"] == "activated" and registry.compatibility_root_matches(root_b):
            raise OSError("activation state persistence failed")
        return original_write(state)

    monkeypatch.setattr(local.state_store, "write", fail_activated_write)
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is not None and local.active.spec.library_root == root_a.resolve()
    assert local.candidate is None
    assert registry.compatibility_root_matches(root_a)
    assert local.state_store.read()["phase"] == "idle"
    assert all(process.poll() is not None for process in popen.processes[1:3])


def test_rootless_post_replace_failure_restores_rootless_config_without_b(monkeypatch, tmp_path):
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    local.start_active(role="rootless", port=18036, bind_host="127.0.0.1", library_root=None, access_mode="local")
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)
    assert not registry.LOCAL_ENV_PATH.exists()
    real_write = registry.write_compatibility_root

    def write_with_late_directory_fsync(root):
        real_fsync = registry.os.fsync
        calls = 0

        def fail_once_after_replace(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("late directory fsync failure")
            return real_fsync(descriptor)

        registry.os.fsync = fail_once_after_replace
        try:
            return real_write(root)
        finally:
            registry.os.fsync = real_fsync

    monkeypatch.setattr(registry, "write_compatibility_root", write_with_late_directory_fsync)
    with pytest.raises(supervisor.SupervisorError, match="rolled back"):
        local.handoff_library(str(root_b))

    assert local.active is not None and local.active.spec.role == "rootless"
    assert local.active.spec.library_root is None
    assert local.candidate is None
    assert not registry.LOCAL_ENV_PATH.exists()
    assert local.state_store.read()["phase"] == "idle"
    assert all(process.poll() is not None for process in popen.processes[1:3])


def test_rollback_replacement_a_verification_failure_is_fail_closed_with_correct_identity(monkeypatch, tmp_path):
    """Test the critical rollback-replacement-A fail_closed case.

    Scenario:
    1. Start with A active.
    2. Force handoff sufficiently far that original A is retired (terminated and reaped).
    3. Force B promotion/verification failure so rollback starts replacement A.
    4. Make replacement A verification fail.
    5. Make termination/reap of replacement A ambiguous/fail.
    6. Assert:
       - supervisor enters fail_closed;
       - replacement A remains referenced in memory;
       - durable fail_closed state contains replacement A's instance ID;
       - durable state does NOT claim the terminated original A as current active ownership;
       - explicit failure_stage indicates rollback replacement verification stage;
       - no further replacement child is spawned;
       - state is not idle.
    """
    root_a = _managed_root(tmp_path / "A")
    root_b = _managed_root(tmp_path / "B")
    popen = FakePopen()
    local = _local(tmp_path, popen)
    active_a = local.start_active(role="active", port=18040, bind_host="127.0.0.1", library_root=root_a, access_mode="local")
    original_a_instance_id = active_a.instance_id
    saved: list[Path] = []
    _ready_handoff(monkeypatch, local, saved)

    original_terminate = local._terminate
    replacement_a_instance_id_holder: list[str] = []

    def combined_verification(child):
        # Fail B promotion verification
        if child.spec.role == "active" and child.spec.library_root == root_b.resolve():
            return False
        # Fail replacement A verification (but not original A)
        if child.spec.role == "active" and child.spec.library_root == root_a.resolve() and child.instance_id != original_a_instance_id:
            replacement_a_instance_id_holder.append(child.instance_id)
            return False
        return True

    monkeypatch.setattr(local, "_wait_for_verified_identity", combined_verification)

    # Make termination of replacement A fail (ambiguous)
    def fail_replacement_a_termination(child):
        if replacement_a_instance_id_holder and child.instance_id == replacement_a_instance_id_holder[0]:
            raise supervisor.SupervisorError("replacement A termination ambiguous")
        return original_terminate(child)

    monkeypatch.setattr(local, "_terminate", fail_replacement_a_termination)

    with pytest.raises(supervisor.SupervisorError, match="failed closed"):
        local.handoff_library(str(root_b))

    # Assertions per the defect requirements
    state = local.state_store.read()

    # 1. Supervisor enters fail_closed
    assert state["phase"] == "fail_closed"

    # 2. Replacement A remains referenced in memory
    assert local.active is not None
    assert local.active.spec.library_root == root_a.resolve()
    assert local.active.instance_id == replacement_a_instance_id_holder[0]
    replacement_a_instance_id = local.active.instance_id

    # 3. Durable fail_closed state contains replacement A's instance ID
    assert state["active_instance_id"] == replacement_a_instance_id

    # 4. Durable state does NOT claim the terminated original A as current active ownership
    assert state["old_instance_id"] == original_a_instance_id
    assert state["active_instance_id"] != original_a_instance_id

    # 5. Explicit failure_stage indicates rollback replacement verification stage
    assert state["failure_stage"] == "rollback_backend_verification"

    # 6. No further replacement child is spawned (only original A, candidate B, promoted B, replacement A = 4 processes)
    assert len(popen.processes) == 4

    # 7. State is not idle
    assert state["phase"] != "idle"
    assert local.status()["activation_incomplete"] is True

    # 8. Failure reason contains context
    assert "rollback replacement termination unconfirmed" in state["failure_reason"]
