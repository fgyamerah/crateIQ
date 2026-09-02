"""Explicit fail-closed recovery for a stopped local CrateIQ launcher.

Recovery is intentionally outside the supervisor IPC and activation APIs.  It
can only turn a valid, persisted ``fail_closed`` record into ``idle`` after
exclusive lifetime/activation locks and process/socket ownership checks prove
that no supervisor-owned backend survived.
"""
from __future__ import annotations

import argparse
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


def inspect_owned_backend_processes(
    repo_root: Path, *, proc_root: Path = Path("/proc"), persisted_ids: Iterable[str | None] = (),
) -> list[OwnedBackendProcess]:
    """Find live children by the fixed wrapper and private ownership metadata.

    Any installation-local wrapper process with missing or contradictory
    metadata is ambiguous and therefore blocks recovery.
    """
    canonical_repo = repo_root.resolve()
    expected_ids = {value for value in persisted_ids if value}
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

        wrapper_command = "backend.app.supervised_backend" in cmdline
        try:
            cwd = (entry / "cwd").resolve(strict=True)
        except FileNotFoundError as exc:
            if wrapper_command and entry.exists():
                raise LauncherRecoveryError(
                    "operator intervention required: backend process ownership is ambiguous"
                ) from exc
            continue
        except OSError as exc:
            if wrapper_command:
                raise LauncherRecoveryError(
                    "operator intervention required: backend process ownership is ambiguous"
                ) from exc
            continue
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
    return owned


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
    """Recover one provably stale fail-closed activation into rootless idle."""
    store = supervisor.ActivationStateStore(state_path)
    state = store.read()
    if state["phase"] == "idle":
        return {"status": "already_idle", "archive": None, "stale_socket_removed": False}
    if state["phase"] != "fail_closed":
        raise LauncherRecoveryError(
            "operator intervention required: only a valid fail_closed activation can be recovered"
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
            activation_lock.acquire()
        except supervisor.ActivationLockUnavailable as exc:
            raise LauncherRecoveryError(
                "operator intervention required: activation lock ownership is ambiguous"
            ) from exc

        # Re-read only after both locks are held so no activation state can
        # change between the proof and the atomic reset.
        state = store.read()
        if state["phase"] == "idle":
            return {"status": "already_idle", "archive": None, "stale_socket_removed": False}
        if state["phase"] != "fail_closed":
            raise LauncherRecoveryError(
                "operator intervention required: activation state changed during recovery"
            )

        persisted_ids = (
            state.get("active_instance_id"),
            state.get("candidate_instance_id"),
            state.get("old_instance_id"),
        )
        owned = inspect_owned_backend_processes(
            repo_root, proc_root=proc_root, persisted_ids=persisted_ids,
        )
        if owned:
            matching = {item.instance_id for item in owned} & {item for item in persisted_ids if item}
            if matching:
                raise LauncherRecoveryError(
                    "operator intervention required: a backend matching persisted activation ownership is alive"
                )
            relevant_ports = {backend_port}
            if state.get("candidate_port") is not None:
                relevant_ports.add(int(state["candidate_port"]))
            if any(item.port in relevant_ports for item in owned):
                raise LauncherRecoveryError(
                    "operator intervention required: a CrateIQ-owned backend still owns an activation port"
                )
            raise LauncherRecoveryError(
                "operator intervention required: a CrateIQ-owned backend process is still alive"
            )

        library_registry_service.validate_registry_state(registry_path)
        _validate_compatibility_state(state, local_env_path)
        try:
            stale_socket_removed = supervisor.recover_stale_socket(socket_path)
        except (supervisor.SupervisorError, OSError) as exc:
            raise LauncherRecoveryError(
                "operator intervention required: supervisor socket ownership is ambiguous"
            ) from exc
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
