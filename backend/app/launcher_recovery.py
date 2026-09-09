"""Explicit stale-artifact recovery for a stopped local CrateIQ launcher.

Recovery is intentionally outside the supervisor IPC and activation APIs.  It
can turn a valid, persisted ``fail_closed`` record into ``idle`` or withdraw a
stale socket left beside an already-idle record. Both paths require exclusive
lifetime/activation locks and process/socket ownership checks proving that no
supervisor-owned process survived.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import supervisor
from .services import library_registry_service


class LauncherRecoveryError(supervisor.SupervisorError):
    """Recovery cannot prove that clearing fail-closed state is safe."""


_OWNERSHIP_ENV_KEYS = (
    "CRATEIQ_SUPERVISOR_INSTANCE_ID",
    "CRATEIQ_BACKEND_INSTANCE_ID",
    "CRATEIQ_BACKEND_START_ROLE",
    "CRATEIQ_BACKEND_BOUND_PORT",
)


@dataclass(frozen=True)
class OwnedBackendProcess:
    pid: int
    instance_id: str
    supervisor_instance_id: str
    role: str
    port: int


@dataclass(frozen=True)
class OwnedLauncherProcesses:
    supervisor_pids: tuple[int, ...]
    backends: tuple[OwnedBackendProcess, ...]


def _decode_environ(data: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in data.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        try:
            result[key.decode("utf-8")] = value.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return result


def inspect_owned_launcher_processes(
    repo_root: Path, *, proc_root: Path = Path("/proc"), persisted_ids: Iterable[str | None] = (),
) -> OwnedLauncherProcesses:
    """Find live supervisors and children by fixed commands and ownership metadata.

    Any installation-local launcher process with missing or contradictory
    ownership is ambiguous and therefore blocks recovery.
    """
    canonical_repo = repo_root.resolve()
    expected_ids = {value for value in persisted_ids if value}
    supervisor_pids: list[int] = []
    owned: list[OwnedBackendProcess] = []
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as exc:
        raise LauncherRecoveryError("operator intervention required: process ownership cannot be inspected") from exc

    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except FileNotFoundError:
            continue
        except OSError as exc:
            try:
                local_cwd = (entry / "cwd").resolve(strict=True) == canonical_repo
            except OSError:
                local_cwd = False
            if local_cwd:
                raise LauncherRecoveryError(
                    "operator intervention required: local process ownership cannot be inspected"
                ) from exc
            continue

        supervisor_command = "backend.app.supervisor" in cmdline
        wrapper_command = "backend.app.supervised_backend" in cmdline
        try:
            cwd = (entry / "cwd").resolve(strict=True)
        except FileNotFoundError as exc:
            if (supervisor_command or wrapper_command) and entry.exists():
                raise LauncherRecoveryError(
                    "operator intervention required: launcher process ownership is ambiguous"
                ) from exc
            continue
        except OSError as exc:
            if supervisor_command or wrapper_command:
                raise LauncherRecoveryError(
                    "operator intervention required: launcher process ownership is ambiguous"
                ) from exc
            continue
        if supervisor_command and cwd == canonical_repo:
            supervisor_pids.append(int(entry.name))
        wrapper_in_repo = wrapper_command and cwd == canonical_repo
        try:
            environment = _decode_environ((entry / "environ").read_bytes())
        except FileNotFoundError:
            if wrapper_in_repo and entry.exists():
                raise LauncherRecoveryError(
                    "operator intervention required: backend ownership metadata disappeared during inspection"
                )
            continue
        except OSError as exc:
            if wrapper_in_repo:
                raise LauncherRecoveryError(
                    "operator intervention required: backend ownership metadata is unreadable"
                ) from exc
            continue

        present = {key: environment.get(key) for key in _OWNERSHIP_ENV_KEYS if environment.get(key)}
        instance_id = environment.get("CRATEIQ_BACKEND_INSTANCE_ID")
        if instance_id in expected_ids and not wrapper_in_repo:
            raise LauncherRecoveryError(
                "operator intervention required: persisted backend identity has ambiguous process ownership"
            )
        if not wrapper_in_repo:
            continue
        if set(present) != set(_OWNERSHIP_ENV_KEYS):
            raise LauncherRecoveryError(
                "operator intervention required: CrateIQ backend ownership metadata is incomplete"
            )
        role = present["CRATEIQ_BACKEND_START_ROLE"]
        port_text = present["CRATEIQ_BACKEND_BOUND_PORT"]
        if role not in supervisor._ALLOWED_ROLES or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            raise LauncherRecoveryError(
                "operator intervention required: CrateIQ backend ownership metadata is invalid"
            )
        owned.append(OwnedBackendProcess(
            pid=int(entry.name),
            instance_id=present["CRATEIQ_BACKEND_INSTANCE_ID"],
            supervisor_instance_id=present["CRATEIQ_SUPERVISOR_INSTANCE_ID"],
            role=role,
            port=int(port_text),
        ))
    return OwnedLauncherProcesses(tuple(supervisor_pids), tuple(owned))


def inspect_owned_backend_processes(
    repo_root: Path, *, proc_root: Path = Path("/proc"), persisted_ids: Iterable[str | None] = (),
) -> list[OwnedBackendProcess]:
    """Compatibility wrapper for focused ownership callers and tests."""
    return list(inspect_owned_launcher_processes(
        repo_root, proc_root=proc_root, persisted_ids=persisted_ids,
    ).backends)


def _activation_lock_pid(metadata: bytes | None) -> int | None:
    if not metadata or not metadata.strip():
        return None
    if len(metadata) > 4096:
        raise LauncherRecoveryError(
            "operator intervention required: activation lock metadata is oversized"
        )
    try:
        value = json.loads(metadata.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise LauncherRecoveryError(
            "operator intervention required: activation lock metadata is malformed"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"pid", "acquired_at"}
        or not isinstance(value.get("pid"), int)
        or isinstance(value.get("pid"), bool)
        or value["pid"] <= 0
        or not isinstance(value.get("acquired_at"), str)
        or not value["acquired_at"].endswith("Z")
    ):
        raise LauncherRecoveryError(
            "operator intervention required: activation lock metadata is malformed"
        )
    return int(value["pid"])


def _assert_no_live_launcher_processes(
    *,
    repo_root: Path,
    proc_root: Path,
    persisted_ids: tuple[object, object, object],
    backend_port: int,
    candidate_port: object,
    activation_lock_metadata: bytes | None,
) -> None:
    processes = inspect_owned_launcher_processes(
        repo_root, proc_root=proc_root, persisted_ids=persisted_ids,
    )
    if processes.supervisor_pids:
        raise LauncherRecoveryError(
            "operator intervention required: a CrateIQ supervisor process is still alive"
        )
    if processes.backends:
        matching = {item.instance_id for item in processes.backends} & {
            str(item) for item in persisted_ids if item
        }
        if matching:
            raise LauncherRecoveryError(
                "operator intervention required: a backend matching persisted activation ownership is alive"
            )
        relevant_ports = {backend_port}
        if candidate_port is not None:
            relevant_ports.add(int(candidate_port))
        if any(item.port in relevant_ports for item in processes.backends):
            raise LauncherRecoveryError(
                "operator intervention required: a CrateIQ-owned backend still owns an activation port"
            )
        raise LauncherRecoveryError(
            "operator intervention required: a CrateIQ-owned backend process is still alive"
        )

    metadata_pid = _activation_lock_pid(activation_lock_metadata)
    if metadata_pid is not None:
        try:
            metadata_process_exists = (proc_root / str(metadata_pid)).exists()
        except OSError as exc:
            raise LauncherRecoveryError(
                "operator intervention required: activation lock PID ownership cannot be inspected"
            ) from exc
        if metadata_process_exists:
            raise LauncherRecoveryError(
                "operator intervention required: activation lock metadata references a live process"
            )


def _socket_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise LauncherRecoveryError(
            "operator intervention required: supervisor socket pathname cannot be inspected"
        ) from exc


def _saved_compatibility_root(path: Path) -> str | None:
    """Strictly inspect the one literal saved-root setting without sourcing it."""
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LauncherRecoveryError("operator intervention required: saved-root state is unreadable") from exc
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
        raise LauncherRecoveryError("operator intervention required: saved-root state is unsafe")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise LauncherRecoveryError("operator intervention required: saved-root state is unreadable") from exc
    values = [line.partition("=")[2] for line in lines if line.startswith("CRATEIQ_LIBRARY_ROOT=")]
    if len(values) > 1:
        raise LauncherRecoveryError("operator intervention required: saved-root state is ambiguous")
    if not values or not values[0]:
        return None
    value = values[0]
    if str(Path(value).resolve(strict=False)) != value:
        raise LauncherRecoveryError("operator intervention required: saved-root state is not canonical")
    return value


def _validate_compatibility_state(state: dict[str, object], local_env_path: Path) -> None:
    saved_root = _saved_compatibility_root(local_env_path)
    requested_root = state.get("requested_root")
    old_root = state.get("old_verified_root")
    if old_root is None:
        # A rootless activation must not be treated as successful merely
        # because its requested path appears in the compatibility file.
        if saved_root is not None and saved_root == requested_root:
            raise LauncherRecoveryError(
                "operator intervention required: saved root may have advanced to the failed requested library"
            )
        return
    if saved_root is not None and saved_root != old_root:
        raise LauncherRecoveryError(
            "operator intervention required: saved root does not match the previously verified library"
        )


def _archive_activation_state(store: supervisor.ActivationStateStore, state: dict[str, object]) -> Path:
    archive = store.path.with_name(
        f"{store.path.stem}.recovered.{supervisor._new_id()}.json"
    )
    supervisor.ActivationStateStore(archive).write(state)
    return archive


def recover_launcher(
    *,
    repo_root: Path,
    socket_path: Path,
    state_path: Path,
    activation_lock_path: Path,
    supervisor_lock_path: Path,
    registry_path: Path,
    local_env_path: Path,
    backend_port: int,
    proc_root: Path = Path("/proc"),
) -> dict[str, object]:
    """Recover provably stale fail-closed state or an idle supervisor socket."""
    store = supervisor.ActivationStateStore(state_path)
    state = store.read()
    if state["phase"] == "idle" and not _socket_entry_exists(socket_path):
        return {"status": "already_idle", "archive": None, "stale_socket_removed": False}
    if state["phase"] not in {"idle", "fail_closed"}:
        raise LauncherRecoveryError(
            "operator intervention required: an activation is in progress and cannot be recovered"
        )

    lifetime_lock = supervisor.SupervisorLock(supervisor_lock_path)
    try:
        lifetime_lock.acquire()
    except supervisor.SupervisorError as exc:
        raise LauncherRecoveryError(
            "operator intervention required: supervisor lifetime-lock ownership is ambiguous"
        ) from exc

    activation_lock = supervisor.ActivationLock(activation_lock_path)
    try:
        try:
            activation_lock.acquire(write_metadata=False)
        except supervisor.ActivationLockUnavailable as exc:
            raise LauncherRecoveryError(
                "operator intervention required: activation lock ownership is ambiguous"
            ) from exc

        # Re-read only after both locks are held so no activation state can
        # change between the proof and the atomic reset.
        state = store.read()
        if state["phase"] not in {"idle", "fail_closed"}:
            raise LauncherRecoveryError(
                "operator intervention required: activation state changed during recovery"
            )

        persisted_ids = (
            state.get("active_instance_id"),
            state.get("candidate_instance_id"),
            state.get("old_instance_id"),
        )
        _assert_no_live_launcher_processes(
            repo_root=repo_root,
            proc_root=proc_root,
            persisted_ids=persisted_ids,
            backend_port=backend_port,
            candidate_port=state.get("candidate_port"),
            activation_lock_metadata=activation_lock.previous_metadata,
        )
        if state["phase"] == "fail_closed":
            library_registry_service.validate_registry_state(registry_path)
            _validate_compatibility_state(state, local_env_path)
        try:
            stale_socket_removed = supervisor.recover_stale_socket(socket_path)
        except (supervisor.SupervisorError, OSError) as exc:
            raise LauncherRecoveryError(
                "operator intervention required: supervisor socket ownership is ambiguous"
            ) from exc
        if state["phase"] == "idle":
            if not stale_socket_removed:
                return {"status": "already_idle", "archive": None, "stale_socket_removed": False}
            activation_lock.clear_metadata()
            return {
                "status": "recovered_idle_socket",
                "archive": None,
                "stale_socket_removed": True,
            }
        archive = _archive_activation_state(store, state)
        activation_lock.clear_metadata()
        store.write(store.empty())
        return {
            "status": "recovered",
            "archive": str(archive),
            "stale_socket_removed": stale_socket_removed,
        }
    except library_registry_service.MalformedRegistryError as exc:
        raise LauncherRecoveryError(
            "operator intervention required: library registry is malformed and was left unchanged"
        ) from exc
    finally:
        activation_lock.release()
        lifetime_lock.release()


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Safely recover a stale CrateIQ fail-closed launcher")
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--socket", type=Path, default=supervisor.DEFAULT_SOCKET_PATH)
    parser.add_argument("--state", type=Path, default=supervisor.DEFAULT_STATE_PATH)
    parser.add_argument("--activation-lock", type=Path, default=supervisor.DEFAULT_LOCK_PATH)
    parser.add_argument("--supervisor-lock", type=Path, default=supervisor.DEFAULT_SUPERVISOR_LOCK_PATH)
    parser.add_argument("--registry", type=Path, default=library_registry_service.REGISTRY_PATH)
    parser.add_argument("--local-env", type=Path, default=library_registry_service.LOCAL_ENV_PATH)
    parser.add_argument("--backend-port", type=int, default=8020)
    args = parser.parse_args(argv)
    result = recover_launcher(
        repo_root=args.repo_root.resolve(),
        socket_path=args.socket,
        state_path=args.state,
        activation_lock_path=args.activation_lock,
        supervisor_lock_path=args.supervisor_lock,
        registry_path=args.registry,
        local_env_path=args.local_env,
        backend_port=args.backend_port,
    )
    if result["status"] == "already_idle":
        print("CrateIQ launcher recovery: activation state is already idle; no changes made.")
    elif result["status"] == "recovered_idle_socket":
        print("CrateIQ launcher recovery: verified stale supervisor socket removed; activation state remains idle.")
    else:
        print(f"CrateIQ launcher recovery: stale fail-closed state archived at {result['archive']}")
        print("CrateIQ launcher recovery: state reset to rootless idle; no library was activated.")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through service command tests
    try:
        raise SystemExit(main())
    except (supervisor.SupervisorError, OSError) as exc:
        print(f"CrateIQ launcher recovery refused: {exc}", file=sys.stderr)
        raise SystemExit(2)
