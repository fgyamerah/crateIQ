"""Local-only, non-reload backend process supervisor for CrateIQ.

This is intentionally a narrow foundation.  It can start an active child and
verify a temporary candidate, but it cannot hand off port 8020 or activate a
library.  All control messages use a private Unix socket and a fixed allowlist.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
import secrets
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .services.backend_instance_identity import library_key_for_root
from .services import library_registry_service

IPC_SCHEMA_VERSION = 1
MAX_IPC_MESSAGE_BYTES = 16 * 1024
DEFAULT_IPC_READ_TIMEOUT_SECONDS = 2.0
DEFAULT_SOCKET_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "crateiq-supervisor.sock"
DEFAULT_SUPERVISOR_LOCK_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "crateiq-supervisor.lock"
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "library_activation_state.json"
DEFAULT_LOCK_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "library_activation.lock"
_ALLOWED_OPERATIONS = frozenset({"ping", "status", "start_candidate", "stop_candidate", "inspect_child", "restore_previous"})
_ALLOWED_ROLES = frozenset({"rootless", "active", "candidate"})
_ALLOWED_CANDIDATE_CLASSIFICATIONS = frozenset({"managed_workspace", "legacy_direct_library"})
SUPPORTED_CHILD_ENVIRONMENT = frozenset({
    # Waveform runtime contract (backend/app/core/waveform_config.py).
    "CRATEIQ_WAVEFORMS_ENABLED", "CRATEIQ_WAVEFORM_CACHE_DIR",
    "CRATEIQ_WAVEFORM_MAX_CONCURRENCY", "CRATEIQ_WAVEFORM_MAX_QUEUE_SIZE",
    "CRATEIQ_WAVEFORM_MAX_CACHE_BYTES",
    # Tool overrides used by backend preflight/analysis/provider services.
    "AUBIO_BIN", "KEYFINDER_BIN", "FFMPEG_BIN", "FFPROBE_BIN", "FPCALC_BIN",
    "RMLINT_BIN", "RSYNC_BIN", "CRATEIQ_ALLOW_UNSAFE_ROOT",
})
_BASE_CHILD_ENVIRONMENT = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "VIRTUAL_ENV", "PYTHONPATH"})
_ROOT_ENVIRONMENT = frozenset({"CRATEIQ_LIBRARY_ROOT", "CRATEMINDAI_LIBRARY_ROOT", "DJ_MUSIC_ROOT"})
_CANDIDATE_PORT_ATTEMPTS = 8
_PHASE_TRANSITIONS = {
    "idle": {"preparing"},
    "preparing": {"candidate_started", "rollback"},
    "candidate_started": {"candidate_verified", "rollback"},
    "candidate_verified": {"rollback"},
    "handoff": {"rollback"},
    "rollback": {"idle"},
}


class SupervisorError(RuntimeError):
    pass


class MalformedActivationState(SupervisorError):
    pass


class ActivationLockUnavailable(SupervisorError):
    pass


class SupervisorLockUnavailable(SupervisorError):
    pass


def _open_safe_runtime_directory(path: Path) -> int:
    """Open an installation runtime directory without traversing symlinks.

    Each component is resolved from a directory descriptor with ``O_NOFOLLOW``.
    The resulting descriptor, rather than a later pathname lookup, anchors all
    lock and socket operations below it.
    """
    absolute = Path(os.path.abspath(os.fspath(path)))
    parts = absolute.parts
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        for part in parts[1:]:
            try:
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                next_descriptor = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=descriptor,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise SupervisorError("refusing runtime-directory symlink") from exc
                raise SupervisorError("could not safely open runtime directory") from exc
            os.close(descriptor)
            descriptor = next_descriptor
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.getuid():
            raise SupervisorError("runtime directory is not a controlled directory")
        os.fchmod(descriptor, 0o700)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _safe_open_lock(path: Path) -> tuple[int, int]:
    directory_fd = _open_safe_runtime_directory(path.parent)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        os.close(directory_fd)
        if exc.errno == errno.ELOOP:
            raise SupervisorError("refusing unsafe lock symlink") from exc
        raise SupervisorError("could not safely open lock") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise SupervisorError("refusing non-regular lock")
        # O_NOFOLLOW protects the final component from symlinks, but a hard
        # link can still name an unrelated regular file.  Lock acquisition
        # chmods and rewrites its inode, so only a dedicated single-link file
        # is safe to mutate.
        if details.st_nlink != 1:
            raise SupervisorError("refusing multiply-linked lock")
        os.fchmod(descriptor, 0o600)
        return descriptor, directory_fd
    except Exception:
        os.close(descriptor)
        os.close(directory_fd)
        raise


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_id() -> str:
    return secrets.token_urlsafe(24)


@dataclass(frozen=True)
class ChildSpec:
    role: str
    port: int
    bind_host: str
    library_root: Path | None
    access_mode: str


@dataclass
class ChildProcess:
    spec: ChildSpec
    instance_id: str
    verification_token: str
    process: Any

    def safe_status(self) -> dict[str, object]:
        return {
            "pid": int(self.process.pid),
            "instance_id": self.instance_id,
            "role": self.spec.role,
            "port": self.spec.port,
            "library_root": str(self.spec.library_root) if self.spec.library_root else None,
            "library_key": library_key_for_root(self.spec.library_root),
            "running": self.process.poll() is None,
        }


class ActivationStateStore:
    """Strict, atomic installation-scoped activation state persistence."""

    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = path

    @staticmethod
    def empty() -> dict[str, object]:
        return {
            "schema_version": 1,
            "activation_id": None,
            "phase": "idle",
            "old_verified_root": None,
            "requested_root": None,
            "old_instance_id": None,
            "candidate_instance_id": None,
            "updated_at": _utc_now(),
        }

    def read(self) -> dict[str, object]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.empty()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MalformedActivationState("activation state is malformed; it was left unchanged") from exc
        self._validate(raw, writing=False)
        return raw

    def write(self, state: dict[str, object]) -> None:
        # Validate before modifying anything, including a valid but incomplete state.
        self._validate_for_write(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".activation.", suffix=".tmp", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def transition(self, state: dict[str, object], phase: str, **updates: object) -> dict[str, object]:
        self._validate_for_write(state)
        current = str(state["phase"])
        if phase not in _PHASE_TRANSITIONS.get(current, set()):
            raise SupervisorError(f"invalid activation transition {current!r} -> {phase!r}")
        next_state = {**state, **updates, "phase": phase, "updated_at": _utc_now()}
        self.write(next_state)
        return next_state

    def incomplete(self) -> bool:
        return self.read()["phase"] != "idle"

    def _validate_for_write(self, state: dict[str, object]) -> None:
        self._validate(state, writing=True)

    def _validate(self, state: object, *, writing: bool) -> None:
        action = "write" if writing else "state"
        if not isinstance(state, dict) or set(state) != set(self.empty()) or state.get("schema_version") != 1:
            raise MalformedActivationState(f"activation {action} has an unsupported schema")
        phase = state.get("phase")
        if phase not in _PHASE_TRANSITIONS:
            raise MalformedActivationState(f"activation {action} has an invalid phase")
        for key in ("activation_id", "old_verified_root", "requested_root", "old_instance_id", "candidate_instance_id"):
            if state[key] is not None and not isinstance(state[key], str):
                raise MalformedActivationState(f"activation {action} contains an invalid value")
        if not isinstance(state["updated_at"], str) or not state["updated_at"].endswith("Z"):
            raise MalformedActivationState("activation state timestamp is invalid")
        for key in ("old_verified_root", "requested_root"):
            value = state.get(key)
            if value is not None and (not isinstance(value, str) or str(Path(value).resolve(strict=False)) != value):
                raise MalformedActivationState(f"activation {action} has a non-canonical path")
        required = {
            "idle": (),
            "preparing": ("activation_id", "requested_root"),
            "candidate_started": ("activation_id", "requested_root", "candidate_instance_id"),
            "candidate_verified": ("activation_id", "requested_root", "candidate_instance_id"),
            "handoff": ("activation_id", "requested_root", "candidate_instance_id"),
            "rollback": ("activation_id", "requested_root"),
        }[str(phase)]
        forbidden = {
            "idle": ("activation_id", "old_verified_root", "requested_root", "old_instance_id", "candidate_instance_id"),
            "preparing": ("candidate_instance_id",),
            "candidate_started": (), "candidate_verified": (), "handoff": (), "rollback": (),
        }[str(phase)]
        if any(state[key] is None for key in required) or any(state[key] is not None for key in forbidden):
            raise MalformedActivationState(f"activation {action} is inconsistent for phase {phase}")


class ActivationLock:
    """Advisory OS lock; release on process crash avoids persistent deadlock."""

    def __init__(self, path: Path = DEFAULT_LOCK_PATH) -> None:
        self.path = path
        self._handle: Any | None = None
        self._descriptor: int | None = None
        self._directory_fd: int | None = None

    def acquire(self) -> None:
        try:
            descriptor, directory_fd = _safe_open_lock(self.path)
        except SupervisorError as exc:
            raise ActivationLockUnavailable(str(exc)) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            os.close(directory_fd)
            raise ActivationLockUnavailable("another activation preparation is already in progress") from exc
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, (json.dumps({"pid": os.getpid(), "acquired_at": _utc_now()}) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        except Exception:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            os.close(directory_fd)
            raise
        self._descriptor = descriptor
        self._directory_fd = directory_fd

    def release(self) -> None:
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None
        if self._directory_fd is not None:
            os.close(self._directory_fd)
            self._directory_fd = None

    def __enter__(self) -> "ActivationLock":
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


class SupervisorLock:
    """Installation-scoped lifetime lock for the Unix control plane."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None
        self._directory_fd: int | None = None

    def acquire(self) -> None:
        try:
            descriptor, directory_fd = _safe_open_lock(self.path)
        except SupervisorError as exc:
            raise SupervisorError(str(exc)) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            os.close(directory_fd)
            raise SupervisorLockUnavailable("another supervisor already owns the local control socket") from exc
        except Exception:
            os.close(descriptor)
            os.close(directory_fd)
            raise
        self._descriptor = descriptor
        self._directory_fd = directory_fd

    def release(self) -> None:
        if self._descriptor is not None:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
            os.close(self._descriptor)
            self._descriptor = None
        if self._directory_fd is not None:
            os.close(self._directory_fd)
            self._directory_fd = None


class LocalSupervisor:
    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        socket_path: Path = DEFAULT_SOCKET_PATH,
        state_store: ActivationStateStore | None = None,
        lock_path: Path = DEFAULT_LOCK_PATH,
        supervisor_lock_path: Path | None = None,
        python_executable: str | None = None,
        cors_origins: str | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        readiness_timeout_seconds: float = 15.0,
        ipc_read_timeout_seconds: float = DEFAULT_IPC_READ_TIMEOUT_SECONDS,
    ) -> None:
        self.repo_root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.socket_path = socket_path
        self.state_store = state_store or ActivationStateStore()
        self.lock_path = lock_path
        self.supervisor_lock_path = supervisor_lock_path or socket_path.with_name("crateiq-supervisor.lock")
        self.python_executable = python_executable or str(self.repo_root / ".venv" / "bin" / "python")
        self.cors_origins = cors_origins
        self._popen = popen
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.ipc_read_timeout_seconds = ipc_read_timeout_seconds
        self.instance_id = _new_id()
        self.active: ChildProcess | None = None
        self.candidate: ChildProcess | None = None
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._ipc_admission_lock = threading.Lock()
        self._accepting_ipc = True
        self._ipc_handlers_quiesced = threading.Event()
        self._ipc_connections_lock = threading.Lock()
        self._ipc_connections: set[socket.socket] = set()
        self._supervisor_lock: SupervisorLock | None = None
        self._ipc_server: _IPCServer | None = None
        self._socket_identity: tuple[int, int] | None = None
        # Publication is recorded separately from full bind validation.  If a
        # later validation step fails, the public hard link can still be
        # withdrawn safely, while the private path is left fail-closed.
        self._published_socket_identity: tuple[int, int] | None = None
        self._bound_socket_name: str | None = None
        self._runtime_directory_fd: int | None = None
        # Narrow deterministic seams for pathname-race regression coverage.
        self._before_socket_identity_recording: Callable[[], None] | None = None
        self._before_socket_cleanup_unlink: Callable[[], None] | None = None
        self._after_socket_withdrawal_exchange: Callable[[str], None] | None = None
        self._on_ipc_handler_accepted: Callable[[], None] | None = None

    def child_command(self, spec: ChildSpec, inherited_fd: int | None = None) -> list[str]:
        if spec.role not in _ALLOWED_ROLES:
            raise SupervisorError("unsupported child role")
        # The fixed wrapper installs Linux's parent-death guard before it
        # invokes Uvicorn. Using a wrapper avoids unsafe Popen pre-exec hooks
        # from the threaded Unix-socket server.
        command = [self.python_executable, "-m", "backend.app.supervised_backend", "backend.app.main:app", "--app-dir", str(self.repo_root)]
        if inherited_fd is None:
            command.extend(["--host", spec.bind_host, "--port", str(spec.port)])
        else:
            command.extend(["--fd", str(inherited_fd)])
        return command

    def child_environment(self, spec: ChildSpec, instance_id: str) -> dict[str, str]:
        # Deliberately construct an allowlisted process environment. In
        # particular, no inherited selected-library value can survive a
        # rootless launch. Provider configuration stays in Settings/runtime,
        # not browser-controlled IPC input.
        environment = {
            key: value for key, value in os.environ.items()
            if key in _BASE_CHILD_ENVIRONMENT | SUPPORTED_CHILD_ENVIRONMENT
        }
        environment.update({
            "CRATEIQ_SUPERVISOR_INSTANCE_ID": self.instance_id,
            "CRATEIQ_BACKEND_INSTANCE_ID": instance_id,
            "CRATEIQ_BACKEND_START_ROLE": spec.role,
            "CRATEIQ_BACKEND_BOUND_PORT": str(spec.port),
            "CRATEIQ_LAUNCH_ACCESS_MODE": spec.access_mode,
            "CRATEIQ_BACKEND_VERIFY_TOKEN": _new_id(),
        })
        if self.cors_origins:
            environment["CORS_ORIGINS"] = self.cors_origins
        if spec.library_root is None:
            for key in _ROOT_ENVIRONMENT:
                environment.pop(key, None)
        else:
            root = str(spec.library_root)
            environment["CRATEIQ_LIBRARY_ROOT"] = root
            environment["DJ_MUSIC_ROOT"] = root
            environment["CRATEIQ_BACKEND_LIBRARY_KEY"] = library_key_for_root(spec.library_root) or ""
        return environment

    def start_active(self, *, role: str, port: int, bind_host: str, library_root: Path | None, access_mode: str) -> ChildProcess:
        with self._state_lock:
            self._require_ipc_admission_locked()
            if role not in {"rootless", "active"}:
                raise SupervisorError("active child role must be rootless or active")
            self._refresh_children_locked()
            if self.active is not None:
                raise SupervisorError("active child already exists")
            canonical_root = library_root.resolve(strict=False) if library_root is not None else None
            self.active = self._start_child(ChildSpec(role, port, bind_host, canonical_root, access_mode))
            return self.active

    def start_candidate(self, requested_root: str) -> ChildProcess:
        with self._state_lock, ActivationLock(self.lock_path):
            # Re-check after the handler has acquired state serialization. An
            # accepted handler blocked here during shutdown must fail closed.
            self._require_ipc_admission_locked()
            self._refresh_children_locked()
            if self.candidate is not None:
                raise SupervisorError("candidate child already exists")
            state = self.state_store.read()
            if state["phase"] != "idle":
                raise SupervisorError("incomplete activation state requires explicit recovery before another candidate")
            classification = library_registry_service.classify_library_candidate(requested_root)
            if not classification["available"] or classification["classification"] not in _ALLOWED_CANDIDATE_CLASSIFICATIONS:
                raise SupervisorError("candidate root is not an allowed, classified CrateIQ library")
            root = Path(str(classification["canonical_path"]))
            state = self.state_store.transition(
                state, "preparing", activation_id=_new_id(), requested_root=str(root),
                old_verified_root=str(self.active.spec.library_root) if self.active and self.active.spec.library_root else None,
                old_instance_id=self.active.instance_id if self.active else None,
            )
            try:
                # Port zero is bound by _start_child and the resulting socket
                # descriptor is inherited by Uvicorn. There is no bind/close
                # race between choosing a temporary port and starting it.
                candidate = self._start_child(ChildSpec("candidate", 0, "127.0.0.1", root, "local"))
                self.candidate = candidate
                state = self.state_store.transition(state, "candidate_started", candidate_instance_id=candidate.instance_id)
                if not self._wait_for_verified_identity(candidate):
                    raise SupervisorError("candidate readiness or identity verification timed out")
                self.state_store.transition(state, "candidate_verified")
                return candidate
            except Exception:
                if self.candidate is not None:
                    self._terminate(self.candidate)
                    self.candidate = None
                # State documents the bounded rollback before returning to a
                # runnable idle state. The old active child is never touched.
                rollback = self.state_store.transition(state, "rollback") if state["phase"] != "rollback" else state
                self.state_store.transition(
                    rollback, "idle", activation_id=None, requested_root=None,
                    old_verified_root=None, old_instance_id=None, candidate_instance_id=None,
                )
                raise

    def stop_candidate(self) -> None:
        with self._state_lock:
            if self.candidate is not None:
                self._terminate(self.candidate)
                self.candidate = None
            self._return_candidate_state_to_idle()

    def _start_child(self, spec: ChildSpec) -> ChildProcess:
        instance_id = _new_id()
        verification_token = _new_id()
        listener = self._reserve_listener(spec)
        actual_port = int(listener.getsockname()[1])
        spec = ChildSpec(spec.role, actual_port, spec.bind_host, spec.library_root, spec.access_mode)
        command = self.child_command(spec, listener.fileno())
        environment = self.child_environment(spec, instance_id)
        environment["CRATEIQ_BACKEND_VERIFY_TOKEN"] = verification_token
        try:
            process = self._popen(
                command, cwd=self.repo_root, env=environment, shell=False,
                close_fds=True, pass_fds=(listener.fileno(),), start_new_session=True,
            )
        finally:
            listener.close()
        return ChildProcess(spec=spec, instance_id=instance_id, verification_token=verification_token, process=process)

    def _reserve_listener(self, spec: ChildSpec) -> socket.socket:
        """Reserve a candidate port without relying on typical ephemeral ranges."""
        attempts = _CANDIDATE_PORT_ATTEMPTS if spec.role == "candidate" else 1
        forbidden = {8020}
        if self.active is not None:
            forbidden.add(self.active.spec.port)
        for _ in range(attempts):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            listener.bind((spec.bind_host, spec.port))
            listener.listen(128)
            if spec.role != "candidate" or int(listener.getsockname()[1]) not in forbidden:
                return listener
            listener.close()
        raise SupervisorError("could not reserve a safe candidate port")

    def _wait_for_verified_identity(self, child: ChildProcess) -> bool:
        deadline = time.monotonic() + self.readiness_timeout_seconds
        expected_root = str(child.spec.library_root) if child.spec.library_root else None
        while time.monotonic() < deadline:
            if child.process.poll() is not None:
                return False
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{child.spec.port}/api/internal/supervisor-identity",
                    headers={"X-CrateIQ-Supervisor-Token": child.verification_token},
                )
                with urllib.request.urlopen(request, timeout=1.0) as response:
                    payload = json.loads(response.read(MAX_IPC_MESSAGE_BYTES + 1))
                identity = payload.get("identity") if isinstance(payload, dict) else None
                if (
                    isinstance(identity, dict)
                    and identity.get("instance_id") == child.instance_id
                    and identity.get("supervisor_instance_id") == self.instance_id
                    and identity.get("role") == child.spec.role
                    and identity.get("port") == child.spec.port
                    and identity.get("library_root") == expected_root
                    and identity.get("library_key") == library_key_for_root(child.spec.library_root)
                ):
                    return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    def _terminate(self, child: ChildProcess) -> None:
        if child.process.poll() is not None:
            try:
                child.process.wait(timeout=0)
            except Exception:
                pass
            return
        try:
            os.killpg(int(child.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            child.process.terminate()
        try:
            child.process.wait(timeout=5)
        except Exception:
            try:
                os.killpg(int(child.process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                child.process.kill()
            # SIGKILL is never a fire-and-forget fallback: reap it before
            # losing ownership so no zombie child can accumulate.
            try:
                child.process.wait(timeout=5)
            except Exception as exc:
                raise SupervisorError("child did not exit after SIGKILL") from exc

    def _refresh_children_locked(self) -> None:
        if self.candidate is not None and self.candidate.process.poll() is not None:
            try:
                self.candidate.process.wait(timeout=0)
            except Exception:
                pass
            self.candidate = None
            self._return_candidate_state_to_idle()
        if self.active is not None and self.active.process.poll() is not None:
            try:
                self.active.process.wait(timeout=0)
            except Exception:
                pass
            self.active = None

    def monitor_children(self) -> None:
        """Reap exited owned children even when no IPC request is received."""
        with self._state_lock:
            self._refresh_children_locked()

    def _return_candidate_state_to_idle(self) -> None:
        """Record bounded cleanup after a stop/crash without touching active."""
        try:
            state = self.state_store.read()
            if state["phase"] != "idle":
                rollback = self.state_store.transition(state, "rollback") if state["phase"] != "rollback" else state
                self.state_store.transition(
                    rollback, "idle", activation_id=None, requested_root=None,
                    old_verified_root=None, old_instance_id=None, candidate_instance_id=None,
                )
        except MalformedActivationState:
            # Never overwrite malformed durable state during cleanup.
            pass

    def status(self) -> dict[str, object]:
        with self._state_lock:
            self._refresh_children_locked()
            try:
                activation_state = self.state_store.read()
                phase = str(activation_state["phase"])
                incomplete = phase != "idle"
            except MalformedActivationState:
                phase = "malformed"
                incomplete = True
            return {
                "supervisor_instance_id": self.instance_id,
                "active_child": self.active.safe_status() if self.active else None,
                "candidate_child": self.candidate.safe_status() if self.candidate else None,
                "activation_phase": phase,
                "activation_incomplete": incomplete,
                # This supervisor foundation does not yet drive the active
                # backend gate. Its per-process contract is exposed separately.
                "operation_admission_draining": False,
            }

    def handle_message(self, message: object) -> dict[str, object]:
        with self._ipc_admission_lock:
            if not self._accepting_ipc:
                raise SupervisorError("supervisor_shutting_down")
        if not isinstance(message, dict) or set(message) != {"schema_version", "operation", "payload"}:
            raise SupervisorError("malformed_request")
        if message["schema_version"] != IPC_SCHEMA_VERSION or not isinstance(message["operation"], str) or not isinstance(message["payload"], dict):
            raise SupervisorError("malformed_request")
        operation = message["operation"]
        payload = message["payload"]
        if operation not in _ALLOWED_OPERATIONS:
            raise SupervisorError("unknown_operation")
        if operation == "ping":
            if payload:
                raise SupervisorError("malformed_request")
            return {"pong": True, "supervisor_instance_id": self.instance_id}
        if operation == "status":
            if payload:
                raise SupervisorError("malformed_request")
            return self.status()
        if operation == "inspect_child":
            if set(payload) != {"role"} or payload["role"] not in {"active", "candidate"}:
                raise SupervisorError("malformed_request")
            with self._state_lock:
                self._refresh_children_locked()
                child = self.active if payload["role"] == "active" else self.candidate
                return {"child": child.safe_status() if child else None}
        if operation == "start_candidate":
            if set(payload) != {"library_root"} or not isinstance(payload["library_root"], str):
                raise SupervisorError("malformed_request")
            return {"candidate": self.start_candidate(payload["library_root"]).safe_status()}
        if operation == "stop_candidate":
            if payload:
                raise SupervisorError("malformed_request")
            self.stop_candidate()
            return {"stopped": True}
        # Deliberately present only as a future-safe protocol name. It cannot
        # stop, promote, or restart an active child in 1B.2A.
        raise SupervisorError("handoff_not_implemented")

    def _require_ipc_admission_locked(self) -> None:
        with self._ipc_admission_lock:
            if not self._accepting_ipc:
                raise SupervisorError("supervisor_shutting_down")

    def _begin_ipc_shutdown(self) -> None:
        with self._ipc_admission_lock:
            self._accepting_ipc = False
        self._stop_event.set()
        self._interrupt_ipc_connections()

    def _register_ipc_connection(self, connection: socket.socket) -> bool:
        """Track a reader so shutdown can actively unblock it."""
        with self._ipc_admission_lock:
            accepting = self._accepting_ipc
        if not accepting:
            return False
        with self._ipc_connections_lock:
            # Check again while registering: shutdown may have started after
            # the first check and must not leave an untracked reader behind.
            with self._ipc_admission_lock:
                if not self._accepting_ipc:
                    return False
            self._ipc_connections.add(connection)
            return True

    def _unregister_ipc_connection(self, connection: socket.socket) -> None:
        with self._ipc_connections_lock:
            self._ipc_connections.discard(connection)

    def _interrupt_ipc_connections(self) -> None:
        with self._ipc_connections_lock:
            connections = tuple(self._ipc_connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def _quiesce_ipc_handlers(self) -> None:
        if self._ipc_server is not None:
            # daemon_threads=False and block_on_close=True make this wait for
            # every accepted handler. No mutating handler can outlive the
            # lifetime lock or final child cleanup.
            self._ipc_server.server_close()
            self._ipc_server = None
        self._ipc_handlers_quiesced.set()

    def cleanup(self) -> None:
        self._begin_ipc_shutdown()
        self._quiesce_ipc_handlers()
        with self._state_lock:
            if self.candidate is not None:
                self._terminate(self.candidate)
                self.candidate = None
            self._return_candidate_state_to_idle()
            if self.active is not None:
                self._terminate(self.active)
                self.active = None
            self._close_control_socket()

    def bind_control_socket(self) -> None:
        """Acquire lifetime ownership and bind IPC before any child is spawned."""
        if self._ipc_server is not None:
            return
        if self._supervisor_lock is None:
            lock = SupervisorLock(self.supervisor_lock_path)
            lock.acquire()
            self._supervisor_lock = lock
        try:
            self._ipc_handlers_quiesced.clear()
            directory_fd = _open_safe_runtime_directory(self.socket_path.parent)
            self._runtime_directory_fd = directory_fd
            prepare_socket(self.socket_path, directory_fd=directory_fd)
            temporary_name = f".{self.socket_path.name}.{self.instance_id}.bound"
            self._bound_socket_name = temporary_name
            temporary_path = _directory_fd_path(directory_fd, temporary_name)
            self._ipc_server = _IPCServer(temporary_path, _ipc_handler(self))
            temporary_stat = os.stat(temporary_name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISSOCK(temporary_stat.st_mode):
                raise SupervisorError("supervisor control socket was not created safely")
            os.chmod(temporary_name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
            # Hard-link installation is atomic and refuses to replace an
            # object that appeared after stale recovery.
            try:
                os.link(
                    temporary_name, self.socket_path.name,
                    src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise SupervisorError("supervisor socket pathname changed during bind") from exc
            expected = (temporary_stat.st_dev, temporary_stat.st_ino)
            self._published_socket_identity = expected
            if self._before_socket_identity_recording is not None:
                self._before_socket_identity_recording()
            bound_stat = os.stat(temporary_name, dir_fd=directory_fd, follow_symlinks=False)
            socket_stat = os.stat(self.socket_path.name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                not _matches_socket_identity(bound_stat, expected)
                or not _matches_socket_identity(socket_stat, expected)
            ):
                raise SupervisorError("supervisor socket pathname changed during bind")
            self._socket_identity = expected
        except Exception:
            self._close_control_socket()
            raise

    def _close_control_socket(self) -> None:
        if self._ipc_server is not None:
            self._ipc_server.server_close()
            self._ipc_server = None
        try:
            if self._runtime_directory_fd is not None:
                self._withdraw_owned_socket_entries(self._runtime_directory_fd)
        finally:
            self._socket_identity = None
            self._published_socket_identity = None
            self._bound_socket_name = None
            if self._runtime_directory_fd is not None:
                os.close(self._runtime_directory_fd)
                self._runtime_directory_fd = None
            if self._supervisor_lock is not None:
                self._supervisor_lock.release()
                self._supervisor_lock = None

    def _withdraw_owned_socket_entries(self, directory_fd: int) -> None:
        """Remove this instance's socket links without unlinking a replacement.

        Each name is exchanged atomically with an empty, instance-private
        directory. ``rmdir`` can only remove that directory: if another
        process replaces the original pathname, removal fails rather than
        deleting its file or symlink. Once an exchange has occurred cleanup
        is strictly one-way: it never restores an entry through a public or
        private pathname, because that could move a raced-in replacement.
        """
        published = self._published_socket_identity
        expected = self._socket_identity
        bound_name = self._bound_socket_name
        if self._before_socket_cleanup_unlink is not None:
            self._before_socket_cleanup_unlink()

        # A public link was published before the final bind validation, so it
        # may be withdrawn using the identity captured at publication. A
        # failed-start private name is deliberately never unlinked: it was
        # not fully validated and may have been replaced meanwhile.
        if published is not None:
            self._withdraw_owned_socket_entry(directory_fd, self.socket_path.name, published)
        if expected is not None and bound_name is not None:
            self._withdraw_owned_socket_entry(directory_fd, bound_name, expected)

    def _withdraw_owned_socket_entry(
        self, directory_fd: int, name: str, expected: tuple[int, int],
    ) -> None:
        """One-way withdrawal of a fully identified socket directory entry.

        The original pathname is never unlinked after a stat comparison.
        Instead it is atomically exchanged with an empty directory owned by
        this cleanup attempt. A replacement installed after that exchange
        remains at ``name`` because only ``rmdir`` is attempted there.
        """
        # A replacement already visible before withdrawal is left exactly at
        # its original pathname. This is only an admission check: the actual
        # removal path below is still the atomic exchange, never stat/unlink.
        if not _matches_socket_identity(_entry_stat(directory_fd, name), expected):
            return
        withdrawal_name = f".{self.socket_path.name}.{self.instance_id}.{_new_id()}.withdraw"
        try:
            os.mkdir(withdrawal_name, 0o700, dir_fd=directory_fd)
            _rename_exchange(directory_fd, name, withdrawal_name)
        except (FileNotFoundError, SupervisorError, OSError):
            _safe_rmdir(directory_fd, withdrawal_name)
            return
        if self._after_socket_withdrawal_exchange is not None:
            self._after_socket_withdrawal_exchange(name)

        withdrawn = _entry_stat(directory_fd, withdrawal_name)
        # Only remove the empty directory we installed at the original name.
        # If it was replaced, fail closed and leave both entries untouched.
        if not _matches_socket_identity(withdrawn, expected) or not _safe_rmdir(directory_fd, name):
            return
        # This generated withdrawal name was populated by our atomic exchange
        # after the owned inode was recorded. It is not a public/private
        # pathname and no checked shared name is ever unlinked.
        try:
            os.unlink(withdrawal_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


class _IPCServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False


def _ipc_handler(supervisor: LocalSupervisor) -> type[socketserver.StreamRequestHandler]:
    class Handler(socketserver.StreamRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(supervisor.ipc_read_timeout_seconds)
            self._tracked = supervisor._register_ipc_connection(self.connection)

        def handle(self) -> None:
            if not self._tracked:
                return
            if supervisor._on_ipc_handler_accepted is not None:
                supervisor._on_ipc_handler_accepted()
            try:
                data = self.rfile.readline(MAX_IPC_MESSAGE_BYTES + 1)
            except (TimeoutError, socket.timeout, OSError):
                # A timeout or shutdown interruption is intentionally silent:
                # no complete request was received and no mutation happened.
                return
            if len(data) > MAX_IPC_MESSAGE_BYTES or not data.endswith(b"\n"):
                response: dict[str, object] = {"ok": False, "error": "message_too_large_or_unterminated"}
            else:
                try:
                    response = {"ok": True, "result": supervisor.handle_message(json.loads(data))}
                except (UnicodeDecodeError, json.JSONDecodeError, SupervisorError) as exc:
                    response = {"ok": False, "error": str(exc)}
            try:
                self.wfile.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            except OSError:
                pass

        def finish(self) -> None:
            try:
                super().finish()
            except OSError:
                pass
            finally:
                if getattr(self, "_tracked", False):
                    supervisor._unregister_ipc_connection(self.connection)
    return Handler


def _directory_fd_path(directory_fd: int, name: str) -> str:
    """Linux descriptor-anchored pathname for APIs without ``dir_fd``."""
    if sys.platform != "linux":
        raise SupervisorError("descriptor-anchored Unix socket binding requires Linux")
    return f"/proc/self/fd/{directory_fd}/{name}"


def _entry_stat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


_RENAME_EXCHANGE = 0x2


def _rename_exchange(directory_fd: int, first: str, second: str) -> None:
    """Atomically exchange two entries anchored to one runtime directory."""
    if sys.platform != "linux":
        raise SupervisorError("atomic supervisor socket cleanup requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise SupervisorError("atomic supervisor socket cleanup is unavailable")
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd, os.fsencode(first), directory_fd, os.fsencode(second), _RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), first)


def _matches_socket_identity(details: os.stat_result | None, identity: tuple[int, int]) -> bool:
    return bool(
        details is not None
        and stat.S_ISSOCK(details.st_mode)
        and (details.st_dev, details.st_ino) == identity
    )


def _safe_rmdir(directory_fd: int, name: str) -> bool:
    """Remove only an empty withdrawal directory, never an arbitrary file."""
    try:
        os.rmdir(name, dir_fd=directory_fd)
        return True
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False


def _socket_is_live(path: Path, *, directory_fd: int | None = None) -> bool:
    owns_directory_fd = directory_fd is None
    if directory_fd is None:
        directory_fd = _open_safe_runtime_directory(path.parent)
    try:
        details = _entry_stat(directory_fd, path.name)
        if details is None:
            return False
        if stat.S_ISLNK(details.st_mode):
            raise SupervisorError("refusing unsafe supervisor socket symlink")
        if not stat.S_ISSOCK(details.st_mode):
            raise SupervisorError("refusing non-socket supervisor path")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(_directory_fd_path(directory_fd, path.name))
            return True
        except OSError:
            return False
        finally:
            probe.close()
    finally:
        if owns_directory_fd:
            os.close(directory_fd)


def prepare_socket(
    path: Path, *, directory_fd: int | None = None, before_stale_unlink: Callable[[], None] | None = None,
) -> None:
    owns_directory_fd = directory_fd is None
    if directory_fd is None:
        directory_fd = _open_safe_runtime_directory(path.parent)
    try:
        details = _entry_stat(directory_fd, path.name)
        if details is None:
            return
        if stat.S_ISLNK(details.st_mode):
            raise SupervisorError("refusing unsafe supervisor socket symlink")
        if not stat.S_ISSOCK(details.st_mode):
            raise SupervisorError("refusing non-socket supervisor path")
        identity = (details.st_dev, details.st_ino)
        if _socket_is_live(path, directory_fd=directory_fd):
            raise SupervisorError("a live supervisor already owns the socket")
        if before_stale_unlink is not None:
            before_stale_unlink()
        # Do not unlink a stale-looking entry. There is no Linux/Python API
        # that makes the preceding socket probe and destructive unlink one
        # atomic ownership check, so fail closed if a pathname already exists.
        raise SupervisorError("stale supervisor socket requires explicit safe recovery")
    finally:
        if owns_directory_fd:
            os.close(directory_fd)


def serve(supervisor: LocalSupervisor) -> None:
    supervisor.bind_control_socket()
    server = supervisor._ipc_server
    assert server is not None
    def _stop(*_: object) -> None:
        supervisor._stop_event.set()
    # Unit tests run this loop in a thread; process signal ownership belongs
    # only to the main interpreter thread.
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    try:
        while not supervisor._stop_event.is_set():
            server.timeout = 0.2
            server.handle_request()
            supervisor.monitor_children()
    finally:
        supervisor.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CrateIQ local backend supervisor")
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--active-role", choices=("rootless", "active"), required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bind-host", required=True)
    parser.add_argument("--access-mode", choices=("local", "lan"), required=True)
    parser.add_argument("--library-root", type=Path)
    parser.add_argument("--cors-origins")
    args = parser.parse_args(argv)
    if args.active_role == "rootless" and args.library_root is not None:
        parser.error("rootless supervisor child cannot have a library root")
    if args.active_role == "active" and args.library_root is None:
        parser.error("active supervisor child requires a library root")
    supervisor = LocalSupervisor(socket_path=args.socket, state_store=ActivationStateStore(args.state), lock_path=args.lock, cors_origins=args.cors_origins)
    # Fail closed before spawning an active child if a previous incomplete or
    # malformed activation record needs an operator/future recovery protocol.
    if supervisor.state_store.incomplete():
        raise SupervisorError("incomplete activation state detected; refusing new supervisor start")
    try:
        # The flock and bound IPC socket are established before child creation.
        supervisor.bind_control_socket()
        supervisor.start_active(role=args.active_role, port=args.port, bind_host=args.bind_host, library_root=args.library_root, access_mode=args.access_mode)
        serve(supervisor)
    finally:
        supervisor.cleanup()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through script/process tests
    try:
        raise SystemExit(main())
    except SupervisorError as exc:
        print(f"CrateIQ supervisor: {exc}", file=sys.stderr)
        raise SystemExit(2)
