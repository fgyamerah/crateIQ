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
import logging
import os
import queue
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
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .services.backend_instance_identity import library_key_for_root
from .services import library_registry_service
from .services.switch_blocker_service import inspect_switch_blockers

IPC_SCHEMA_VERSION = 1
MAX_IPC_MESSAGE_BYTES = 16 * 1024
DEFAULT_IPC_READ_TIMEOUT_SECONDS = 2.0
DEFAULT_CANDIDATE_STARTUP_TIMEOUT_SECONDS = 15.0
DEFAULT_ACTIVE_STARTUP_TIMEOUT_SECONDS = 30.0
IDENTITY_PROBE_TIMEOUT_SECONDS = 1.0
IDENTITY_PROBE_INTERVAL_SECONDS = 0.1
DEFAULT_SOCKET_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "crateiq-supervisor.sock"
DEFAULT_SUPERVISOR_LOCK_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "crateiq-supervisor.lock"
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "library_activation_state.json"
DEFAULT_LOCK_PATH = Path(__file__).resolve().parents[2] / ".run" / "local" / "library_activation.lock"
_ALLOWED_OPERATIONS = frozenset({"ping", "status", "start_candidate", "stop_candidate", "inspect_child", "restore_previous", "handoff_library", "activate_registered_library"})
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
    "preparing": {"candidate_started", "rollback", "fail_closed"},
    "candidate_started": {"candidate_verified", "rollback", "fail_closed"},
    "candidate_verified": {"active_draining", "promoting", "rollback", "fail_closed"},
    "active_draining": {"promoting", "rollback", "fail_closed"},
    "promoting": {"activated", "rollback", "fail_closed"},
    "activated": {"idle", "rollback", "fail_closed"},
    "rollback": {"idle", "fail_closed"},
    "fail_closed": set(),
}
_LEGACY_IDLE_STATE_FIELDS = frozenset({
    "schema_version", "activation_id", "phase", "old_verified_root",
    "requested_root", "old_instance_id", "candidate_instance_id", "updated_at",
})

log = logging.getLogger(__name__)


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


@dataclass
class _ProcessLaunchRequest:
    popen: Callable[..., Any]
    command: list[str]
    kwargs: dict[str, Any]
    completed: threading.Event
    process: Any = None
    error: BaseException | None = None


class _ProcessLaunchBroker:
    """Create worker-requested children from one supervisor-lifetime thread.

    Linux delivers ``PR_SET_PDEATHSIG`` when the specific thread that created
    a child exits, not only when the complete parent process exits. Registered
    activation runs on a short-lived worker, so spawning promoted B directly
    there would make successful worker completion look like supervisor death.
    The broker stays alive until explicit supervisor cleanup, which first
    terminates and reaps every owned child and then closes this thread.
    """

    def __init__(self) -> None:
        self._requests: queue.Queue[_ProcessLaunchRequest | None] = queue.Queue()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False

    def launch(self, popen: Callable[..., Any], command: list[str], **kwargs: Any) -> Any:
        request = _ProcessLaunchRequest(
            popen=popen,
            command=command,
            kwargs=kwargs,
            completed=threading.Event(),
        )
        with self._start_lock:
            if self._closed:
                raise SupervisorError("child launch owner is closed")
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="crateiq-child-launch-owner",
                    daemon=True,
                )
                self._thread.start()
        self._requests.put(request)
        request.completed.wait()
        if request.error is not None:
            raise request.error
        return request.process

    def close(self) -> None:
        with self._start_lock:
            thread = self._thread
            if self._closed:
                return
            self._closed = True
            if thread is None:
                return
            self._requests.put(None)
        thread.join()

    def _run(self) -> None:
        while True:
            request = self._requests.get()
            if request is None:
                return
            try:
                request.process = request.popen(request.command, **request.kwargs)
            except BaseException as exc:
                request.error = exc
            finally:
                request.completed.set()


class ActivationStateStore:
    """Strict, atomic installation-scoped activation state persistence."""

    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = path

    @staticmethod
    def empty() -> dict[str, object]:
        return {
            "schema_version": 2,
            "activation_id": None,
            "phase": "idle",
            "old_verified_root": None,
            "old_library_key": None,
            "requested_root": None,
            "requested_library_key": None,
            "old_instance_id": None,
            "candidate_instance_id": None,
            "candidate_port": None,
            "active_instance_id": None,
            "failure_reason": None,
            "failure_stage": None,
            "updated_at": _utc_now(),
        }

    def read(self) -> dict[str, object]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.empty()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MalformedActivationState("activation state is malformed; it was left unchanged") from exc
        # A completed 1B.2A installation can have only this exact v1 idle
        # shape. It carries no active handoff intent, so it is safe to expand
        # in memory and persist as v2 on the next transition. Any v1 partial
        # handoff remains an explicit fail-closed operator condition.
        if self._is_legacy_idle(raw):
            migrated = self.empty()
            migrated["updated_at"] = raw["updated_at"]
            return migrated
        self._validate(raw, writing=False)
        return raw

    @staticmethod
    def _is_legacy_idle(state: object) -> bool:
        if not isinstance(state, dict) or set(state) != _LEGACY_IDLE_STATE_FIELDS:
            return False
        if state.get("schema_version") != 1 or state.get("phase") != "idle":
            return False
        if not isinstance(state.get("updated_at"), str) or not state["updated_at"].endswith("Z"):
            return False
        return all(state.get(key) is None for key in (
            "activation_id", "old_verified_root", "requested_root", "old_instance_id", "candidate_instance_id",
        ))

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
        if not isinstance(state, dict) or set(state) != set(self.empty()) or state.get("schema_version") != 2:
            raise MalformedActivationState(f"activation {action} has an unsupported schema")
        phase = state.get("phase")
        if phase not in _PHASE_TRANSITIONS:
            raise MalformedActivationState(f"activation {action} has an invalid phase")
        for key in (
            "activation_id", "old_verified_root", "old_library_key", "requested_root",
            "requested_library_key", "old_instance_id", "candidate_instance_id",
            "active_instance_id", "failure_reason", "failure_stage",
        ):
            if state[key] is not None and not isinstance(state[key], str):
                raise MalformedActivationState(f"activation {action} contains an invalid value")
        if state["candidate_port"] is not None and (
            not isinstance(state["candidate_port"], int) or not 1 <= state["candidate_port"] <= 65535
        ):
            raise MalformedActivationState(f"activation {action} contains an invalid candidate port")
        if not isinstance(state["updated_at"], str) or not state["updated_at"].endswith("Z"):
            raise MalformedActivationState("activation state timestamp is invalid")
        for key in ("old_verified_root", "requested_root"):
            value = state.get(key)
            if value is not None and (not isinstance(value, str) or str(Path(value).resolve(strict=False)) != value):
                raise MalformedActivationState(f"activation {action} has a non-canonical path")
        requested = ("activation_id", "requested_root", "requested_library_key")
        required = {
            "idle": (),
            "preparing": requested,
            "candidate_started": requested + ("candidate_instance_id", "candidate_port"),
            "candidate_verified": requested + ("candidate_instance_id", "candidate_port"),
            "active_draining": requested + ("candidate_instance_id", "candidate_port"),
            "promoting": requested + ("candidate_instance_id", "candidate_port"),
            "activated": requested + ("active_instance_id",),
            "rollback": requested,
            "fail_closed": requested + ("failure_reason",),
        }[str(phase)]
        forbidden = {
            "idle": (
                "activation_id", "old_verified_root", "old_library_key", "requested_root",
                "requested_library_key", "old_instance_id", "candidate_instance_id",
                "candidate_port", "active_instance_id",
            ),
            "preparing": ("candidate_instance_id", "candidate_port", "active_instance_id"),
            "candidate_started": ("active_instance_id",),
            "candidate_verified": ("active_instance_id",),
            "active_draining": ("active_instance_id",),
            "promoting": (),
            "activated": ("candidate_instance_id", "candidate_port"),
            "rollback": ("active_instance_id",),
            "fail_closed": (),
        }[str(phase)]
        if any(state[key] is None for key in required) or any(state[key] is not None for key in forbidden):
            raise MalformedActivationState(f"activation {action} is inconsistent for phase {phase}")
        old_root, old_key = state["old_verified_root"], state["old_library_key"]
        if (old_root is None) != (old_key is None):
            raise MalformedActivationState(f"activation {action} has incomplete old library identity")
        if old_root is not None and old_key != library_key_for_root(str(old_root)):
            raise MalformedActivationState(f"activation {action} has invalid old library key")
        requested_root, requested_key = state["requested_root"], state["requested_library_key"]
        if requested_root is not None and requested_key != library_key_for_root(str(requested_root)):
            raise MalformedActivationState(f"activation {action} has invalid requested library key")


class ActivationLock:
    """Advisory OS lock; release on process crash avoids persistent deadlock."""

    def __init__(self, path: Path = DEFAULT_LOCK_PATH) -> None:
        self.path = path
        self._handle: Any | None = None
        self._descriptor: int | None = None
        self._directory_fd: int | None = None
        self.previous_metadata: bytes | None = None

    def acquire(self, *, write_metadata: bool = True) -> None:
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
            self.previous_metadata = os.read(descriptor, 4097)
            if write_metadata:
                os.lseek(descriptor, 0, os.SEEK_SET)
                os.ftruncate(descriptor, 0)
                os.write(
                    descriptor,
                    (json.dumps({"pid": os.getpid(), "acquired_at": _utc_now()}) + "\n").encode("utf-8"),
                )
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

    def clear_metadata(self) -> None:
        """Clear stale advisory metadata while retaining the acquired flock."""
        if self._descriptor is None:
            raise ActivationLockUnavailable("activation lock is not held")
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        os.ftruncate(self._descriptor, 0)
        os.fsync(self._descriptor)

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
        readiness_timeout_seconds: float = DEFAULT_CANDIDATE_STARTUP_TIMEOUT_SECONDS,
        active_readiness_timeout_seconds: float = DEFAULT_ACTIVE_STARTUP_TIMEOUT_SECONDS,
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
        self._process_launch_broker = _ProcessLaunchBroker()
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.active_readiness_timeout_seconds = active_readiness_timeout_seconds
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
        # The supervisor, rather than the outgoing backend request handler,
        # retains activation progress because a successful handoff replaces
        # that handler's process before it could reliably return a final HTTP
        # response. This is one supervisor-owned activation at a time; the
        # durable handoff lock/state remains the authority for the handoff.
        self._activation_thread: threading.Thread | None = None
        self._activation_record_lock = threading.Lock()
        self._activation_record: dict[str, object] = {"status": "idle"}
        self._last_blocker_summary: dict[str, object] | None = None

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
            state = self._begin_activation_state(state, root)
            try:
                # Port zero is bound by _start_child and the resulting socket
                # descriptor is inherited by Uvicorn. There is no bind/close
                # race between choosing a temporary port and starting it.
                candidate = self._start_child(ChildSpec("candidate", 0, "127.0.0.1", root, "local"))
                self.candidate = candidate
                state = self.state_store.transition(
                    state, "candidate_started", candidate_instance_id=candidate.instance_id,
                    candidate_port=candidate.spec.port,
                )
                if not self._wait_for_verified_identity(candidate):
                    raise SupervisorError("candidate readiness or identity verification timed out")
                self.state_store.transition(state, "candidate_verified")
                return candidate
            except Exception:
                if self.candidate is not None:
                    if not self._terminate_owned_child(self.candidate):
                        self._mark_fail_closed(state, "candidate_start_cleanup_unconfirmed", "candidate_termination")
                        raise SupervisorError("candidate cleanup could not be confirmed")
                    self.candidate = None
                # State documents the bounded rollback before returning to a
                # runnable idle state. The old active child is never touched.
                rollback = self.state_store.transition(state, "rollback") if state["phase"] != "rollback" else state
                self.state_store.transition(rollback, "idle", **self._idle_updates("candidate_start_failed"))
                raise

    def stop_candidate(self) -> None:
        with self._state_lock:
            if self.candidate is not None:
                if not self._terminate_owned_child(self.candidate):
                    state = self.state_store.read()
                    self._mark_fail_closed(state, "candidate_stop_cleanup_unconfirmed", "candidate_termination")
                    raise SupervisorError("candidate cleanup could not be confirmed")
                self.candidate = None
            self._return_candidate_state_to_idle()

    @staticmethod
    def _idle_updates(failure_reason: str | None = None) -> dict[str, object]:
        return {
            "activation_id": None,
            "old_verified_root": None,
            "old_library_key": None,
            "requested_root": None,
            "requested_library_key": None,
            "old_instance_id": None,
            "candidate_instance_id": None,
            "candidate_port": None,
            "active_instance_id": None,
            "failure_reason": failure_reason,
            "failure_stage": None,
        }

    def _begin_activation_state(self, state: dict[str, object], root: Path) -> dict[str, object]:
        active = self.active
        old_root = active.spec.library_root if active else None
        return self.state_store.transition(
            state,
            "preparing",
            activation_id=_new_id(),
            requested_root=str(root),
            requested_library_key=library_key_for_root(root),
            old_verified_root=str(old_root) if old_root else None,
            old_library_key=library_key_for_root(old_root),
            old_instance_id=active.instance_id if active else None,
            failure_reason=None,
        )

    @staticmethod
    def _safe_blocker_summary(blockers: dict[str, object]) -> dict[str, object]:
        """Keep only frontend-safe switch-blocker information."""
        active = blockers.get("blockers")
        ambiguous = blockers.get("ambiguous_legacy_active")
        active_items = active if isinstance(active, list) else []
        ambiguous_items = ambiguous if isinstance(ambiguous, list) else []
        reasons = {
            str(item.get("reason"))
            for item in [*active_items, *ambiguous_items]
            if isinstance(item, dict)
        }
        if "active_current_library" in reasons:
            category, message = "active_work", "The active library has work in progress."
        elif "foreign_active_installation_inconsistency" in reasons:
            category, message = "foreign_active_work", "CrateIQ has unresolved active work for another library."
        else:
            category, message = "legacy_ambiguous_work", "CrateIQ found unresolved legacy active work."
        return {"category": category, "count": len(active_items) + len(ambiguous_items), "message": message}

    def start_registered_library_activation(self, payload: dict[str, object]) -> dict[str, object]:
        """Start one registry-bound handoff without trusting a raw path.

        The caller supplies the server-validated canonical tuple, but the
        supervisor resolves the registry ID again before it starts a child.
        This preserves the Unix supervisor as the sole handoff owner and
        makes a direct IPC caller unable to substitute a host path.
        """
        if set(payload) != {"library_id", "library_root", "library_key", "classification"}:
            raise SupervisorError("malformed_request")
        if not all(isinstance(payload[name], str) for name in payload):
            raise SupervisorError("malformed_request")
        try:
            target = library_registry_service.resolve_registered_library(str(payload["library_id"]))
        except library_registry_service.UnknownRegistryLibraryError as exc:
            raise SupervisorError("unknown_registry_library") from exc
        except (library_registry_service.UnsafeRegistryLibraryError, library_registry_service.MalformedRegistryError, ValueError) as exc:
            raise SupervisorError("unsafe_registry_library") from exc
        if any(target[name] != payload[name] for name in ("library_root", "library_key", "classification")):
            raise SupervisorError("invalid_activation_target")

        with self._state_lock:
            self._require_ipc_admission_locked()
            if self._activation_thread is not None and self._activation_thread.is_alive():
                raise SupervisorError("activation_in_progress")
            try:
                phase = str(self.state_store.read()["phase"])
            except MalformedActivationState as exc:
                raise SupervisorError("supervisor_fail_closed") from exc
            if phase == "fail_closed":
                raise SupervisorError("supervisor_fail_closed")
            if phase != "idle":
                raise SupervisorError("activation_in_progress")
            activation_id = _new_id()
            self._last_blocker_summary = None
            self._set_activation_record({
                "status": "activating",
                "activation_id": activation_id,
                "library_id": target["library_id"],
            })
            worker = threading.Thread(
                target=self._run_registered_library_activation,
                args=(activation_id, target),
                name="crateiq-library-activation",
                daemon=True,
            )
            self._activation_thread = worker
            worker.start()
        return {"result": "activation_started", "activation_id": activation_id, "activation_status": "activating"}

    def _run_registered_library_activation(self, activation_id: str, target: dict[str, object]) -> None:
        """Run handoff and recency bookkeeping after verified completion."""
        try:
            # Repeat the registry lookup immediately before the handoff so a
            # stale entry cannot become a process target while queued.
            verified = library_registry_service.resolve_registered_library(str(target["library_id"]))
            if any(verified[name] != target[name] for name in ("library_root", "library_key", "classification")):
                raise SupervisorError("invalid_activation_target")
            result = self.handoff_library(str(verified["library_root"]))
            recency_updated = False
            warning_code: str | None = None
            try:
                library_registry_service.mark_registered_library_opened(str(verified["library_id"]))
                recency_updated = True
            except Exception:
                # Recency is convenience metadata: B is already the verified,
                # saved active backend and must never be rolled back for it.
                warning_code = "registry_recency_update_failed"
                log.exception("library activation completed but registry recency update failed")
            with self._state_lock:
                self._set_activation_record({
                    "status": "succeeded",
                    "activation_id": activation_id,
                    "library_id": verified["library_id"],
                    "result": result["result"],
                    "registry_recency_updated": recency_updated,
                    "warning_code": warning_code,
                })
        except SupervisorError as exc:
            message = str(exc)
            if "persisted operation state blocks" in message:
                code = "switch_blocked"
                record: dict[str, object] = {
                    "status": "blocked", "activation_id": activation_id,
                    "library_id": target["library_id"], "error_code": code,
                    "blocker": self._last_blocker_summary or {
                        "category": "active_work", "count": 0,
                        "message": "The active library has work in progress.",
                    },
                }
            elif "failed closed" in message or message == "supervisor_fail_closed":
                record = {
                    "status": "fail_closed", "activation_id": activation_id,
                    "library_id": target["library_id"], "error_code": "supervisor_fail_closed",
                    "message": "Activation requires supervisor restart or operator intervention.",
                }
            else:
                record = {
                    "status": "failed", "activation_id": activation_id,
                    "library_id": target["library_id"], "error_code": "handoff_failed",
                    "message": "The library switch did not complete.",
                }
            with self._state_lock:
                self._set_activation_record(record)
        except Exception:
            log.exception("library activation worker failed unexpectedly")
            with self._state_lock:
                self._set_activation_record({
                    "status": "failed", "activation_id": activation_id,
                    "library_id": target["library_id"], "error_code": "internal_failure",
                    "message": "The library switch did not complete.",
                })

    def handoff_library(self, requested_root: str) -> dict[str, object]:
        """Perform one local, supervisor-owned root-bound backend handoff.

        This is intentionally not an HTTP-facing operation.  The Unix control
        socket is the local host administration boundary; callers still get
        the same read-only launcher classification before a child is started.
        """
        with self._state_lock, ActivationLock(self.lock_path):
            self._require_ipc_admission_locked()
            self._refresh_children_locked()
            if self.active is None:
                raise SupervisorError("supervisor has no active child to hand off")
            state = self.state_store.read()
            if state["phase"] != "idle":
                raise SupervisorError("incomplete activation state requires explicit recovery before handoff")
            classification = library_registry_service.classify_library_candidate(requested_root)
            if not classification["available"] or classification["classification"] not in _ALLOWED_CANDIDATE_CLASSIFICATIONS:
                raise SupervisorError("requested root is not an allowed, classified CrateIQ library")
            root = Path(str(classification["canonical_path"]))
            requested_key = library_key_for_root(root)
            old_child = self.active
            if old_child.spec.library_root is not None and library_key_for_root(old_child.spec.library_root) == requested_key:
                return {
                    "result": "already_active",
                    "activated": True,
                    "library_key": requested_key,
                    "active_backend_instance_id": old_child.instance_id,
                    "active_port": old_child.spec.port,
                }

            state = self._begin_activation_state(state, root)
            gate_draining = False
            promoted: ChildProcess | None = None
            saved_root_before: bytes | None = None
            saved_root_snapshot_captured = False
            saved_root_write_attempted = False
            try:
                candidate = self._start_child(ChildSpec("candidate", 0, "127.0.0.1", root, "local"))
                self.candidate = candidate
                state = self.state_store.transition(
                    state, "candidate_started", candidate_instance_id=candidate.instance_id,
                    candidate_port=candidate.spec.port,
                )
                if not self._wait_for_verified_identity(candidate):
                    raise SupervisorError("candidate readiness or identity verification timed out")
                state = self.state_store.transition(state, "candidate_verified")

                if old_child.spec.library_root is not None:
                    state = self.state_store.transition(state, "active_draining")
                    # The request can time out after the child has accepted
                    # it, so rollback must always try to reopen A's gate.
                    gate_draining = True
                    if not self._set_admission_state(old_child, "begin_draining"):
                        raise SupervisorError("active backend admission drain did not complete")
                    blockers = inspect_switch_blockers(library_key_for_root(old_child.spec.library_root))
                    if not blockers["can_switch"]:
                        self._last_blocker_summary = self._safe_blocker_summary(blockers)
                        raise SupervisorError("persisted operation state blocks a safe library switch")

                state = self.state_store.transition(state, "promoting")
                if not self._terminate_owned_child(candidate):
                    raise SupervisorError("candidate termination could not be confirmed")
                self.candidate = None
                if not self._terminate_owned_child(old_child):
                    raise SupervisorError("old active termination could not be confirmed")
                self.active = None
                promoted_spec = ChildSpec(
                    "active", old_child.spec.port, old_child.spec.bind_host, root, old_child.spec.access_mode,
                )
                promoted = self._start_child(promoted_spec)
                state = {**state, "active_instance_id": promoted.instance_id, "updated_at": _utc_now()}
                self.state_store.write(state)
                if not self._wait_for_verified_identity(promoted):
                    raise SupervisorError("promoted backend readiness or identity verification timed out")
                # Deliberately after B owns and verifies the stable endpoint.
                saved_root_before = library_registry_service.capture_compatibility_root_state()
                saved_root_snapshot_captured = True
                saved_root_write_attempted = True
                try:
                    library_registry_service.write_compatibility_root(root)
                finally:
                    # Directory fsync can fail after os.replace.  Inspect the
                    # file rather than interpreting an exception as no write.
                    if not library_registry_service.compatibility_root_matches(root):
                        raise SupervisorError("saved compatibility root was not verified after promotion")
                # Compatibility-root persistence can take non-zero time. Do
                # not commit a clean terminal state unless the same promoted
                # child still answers the complete authenticated identity
                # contract on the stable endpoint.
                if not self._wait_for_verified_identity(promoted):
                    raise SupervisorError("promoted backend was not verified at activation commit")
                # Transfer the verified stable-port child from the handoff's
                # failure-cleanup ownership into the supervisor's sole active
                # slot. Keep the local reference until the durable idle write
                # succeeds so any commit failure still rolls B back.
                self.active = promoted
                state = self.state_store.transition(
                    state, "activated", candidate_instance_id=None, candidate_port=None,
                )
                if (
                    self.active is not promoted
                    or promoted.process.poll() is not None
                    or promoted.spec.role != "active"
                    or promoted.spec.port != old_child.spec.port
                    or promoted.spec.library_root != root
                    or library_key_for_root(promoted.spec.library_root) != requested_key
                ):
                    raise SupervisorError("promoted backend exited before idle commit")
                self.state_store.transition(state, "idle", **self._idle_updates())
                committed_active = promoted
                promoted = None
                return {
                    "result": "activated",
                    "activated": True,
                    "library_key": requested_key,
                    "active_backend_instance_id": committed_active.instance_id,
                    "active_port": committed_active.spec.port,
                }
            except Exception as exc:
                reason = str(exc) or exc.__class__.__name__
                if self._rollback_handoff(
                    state,
                    old_child,
                    promoted,
                    gate_draining,
                    reason,
                    saved_root_before=saved_root_before,
                    saved_root_snapshot_captured=saved_root_snapshot_captured,
                    saved_root_write_attempted=saved_root_write_attempted,
                ):
                    raise SupervisorError(f"handoff rolled back: {reason}") from exc
                raise SupervisorError(f"handoff failed closed: {reason}") from exc

    def _rollback_handoff(
        self,
        state: dict[str, object],
        old_child: ChildProcess,
        promoted: ChildProcess | None,
        gate_draining: bool,
        reason: str,
        *,
        saved_root_before: bytes | None,
        saved_root_snapshot_captured: bool,
        saved_root_write_attempted: bool,
    ) -> bool:
        """Restore a verified A/rootless child, or leave durable fail-closed state."""
        if self.candidate is not None:
            if not self._terminate_owned_child(self.candidate):
                self._mark_fail_closed(state, f"{reason}; candidate termination unconfirmed", "candidate_termination")
                return False
            self.candidate = None
        if promoted is not None:
            if self.active is not None and self.active is not promoted:
                self._mark_fail_closed(state, f"{reason}; promoted backend ownership is ambiguous", "promoted_backend_termination")
                return False
            if not self._terminate_owned_child(promoted):
                # Retain the only known handle if termination cannot be
                # confirmed; fail-closed status must expose owned live state.
                self.active = promoted
                self._mark_fail_closed(state, f"{reason}; promoted backend termination unconfirmed", "promoted_backend_termination")
                return False
            if self.active is promoted:
                self.active = None

        if saved_root_write_attempted:
            if not saved_root_snapshot_captured:
                self._mark_fail_closed(state, f"{reason}; prior saved-root state was unavailable", "compatibility_root_restore")
                return False
            if not library_registry_service.compatibility_root_state_matches(saved_root_before):
                try:
                    library_registry_service.restore_compatibility_root_state(saved_root_before)
                except Exception:
                    pass
            if not library_registry_service.compatibility_root_state_matches(saved_root_before):
                self._mark_fail_closed(state, f"{reason}; saved-root rollback was not verified", "compatibility_root_restore")
                return False

        restored: ChildProcess | None = None
        # A termination failure can leave the original process intact.  It is
        # usable only after its private identity and post-abort gate state are
        # both independently confirmed.
        if old_child.process.poll() is None and self._wait_for_verified_identity(old_child):
            if not gate_draining or self._set_admission_state(old_child, "abort_draining"):
                restored = old_child
                self.active = old_child
            else:
                self._mark_fail_closed(state, f"{reason}; original backend admission reopen was not verified", "old_backend_retirement")
                return False
        elif old_child.process.poll() is None:
            # It might still be serving A, but is no longer a verified backend
            # we can safely reuse. Never spawn a replacement beside it.
            self._mark_fail_closed(state, f"{reason}; original backend remains live but unverified", "old_backend_retirement")
            return False
        elif not self._reap_terminated_child(old_child):
            self._mark_fail_closed(state, f"{reason}; original backend termination was not reaped", "old_backend_retirement")
            return False

        if restored is None:
            try:
                replacement = self._start_child(old_child.spec)
                if not self._wait_for_verified_identity(replacement):
                    if not self._terminate_owned_child(replacement):
                        self.active = replacement
                        self._mark_fail_closed(state, f"{reason}; rollback replacement termination unconfirmed", "rollback_backend_verification")
                        return False
                    raise SupervisorError("rollback backend identity verification timed out")
                self.active = replacement
                restored = replacement
            except Exception as exc:
                if self.active is not None and self.active is not old_child:
                    # A failed spawn has no process reference. A started but
                    # failed replacement keeps ownership until it is reaped.
                    if self.active.process.poll() is None:
                        self._mark_fail_closed(state, f"{reason}; rollback replacement status is ambiguous", "rollback_backend_verification")
                        return False
                    if not self._reap_terminated_child(self.active):
                        self._mark_fail_closed(state, f"{reason}; rollback replacement was not reaped", "rollback_backend_termination")
                        return False
                    self.active = None
                self._mark_fail_closed(state, f"{reason}; rollback replacement failed: {exc}", "rollback_backend_start")
                return False

        rollback = state
        try:
            if rollback["phase"] != "rollback":
                rollback = self.state_store.transition(rollback, "rollback", active_instance_id=None)
            if restored is not None:
                if self.active is not restored or restored.process.poll() is not None:
                    if self.active is restored and self._reap_terminated_child(restored):
                        self.active = None
                    self._mark_fail_closed(
                        rollback,
                        f"{reason}; verified rollback backend exited before commit",
                        "rollback_backend_verification",
                    )
                    return False
                self.state_store.transition(rollback, "idle", **self._idle_updates(reason))
                return True
        except (MalformedActivationState, SupervisorError, OSError):
            # The runtime is already unavailable; do not overwrite uncertain
            # durable state just to make it cosmetically idle.
            pass
        return False

    def _mark_fail_closed(self, state: dict[str, object], reason: str, stage: str) -> None:
        """Persist ambiguity without discarding references to owned children."""
        candidate = self.candidate
        active = self.active
        updates: dict[str, object] = {
            "failure_reason": reason,
            "failure_stage": stage,
            "candidate_instance_id": candidate.instance_id if candidate else None,
            "candidate_port": candidate.spec.port if candidate else None,
            # Persist the actual current active child identity if it exists.
            # This covers rollback replacement A which has the original library root,
            # not the requested root. The durable old_instance_id remains the original
            # pre-handoff A identity for diagnostic correlation.
            "active_instance_id": active.instance_id if active is not None else None,
        }
        try:
            if state["phase"] == "fail_closed":
                self.state_store.write({**state, **updates, "updated_at": _utc_now()})
            else:
                self.state_store.transition(state, "fail_closed", **updates)
        except (MalformedActivationState, SupervisorError, OSError):
            # The existing non-idle durable state remains the fail-closed
            # record if storage itself is unavailable.
            pass

    def _set_admission_state(self, child: ChildProcess, action: str) -> bool:
        if action not in {"begin_draining", "abort_draining"}:
            raise SupervisorError("invalid admission action")
        try:
            query = urllib.parse.urlencode({"action": action})
            request = urllib.request.Request(
                f"http://127.0.0.1:{child.spec.port}/api/internal/supervisor-admission?{query}",
                data=b"",
                method="POST",
                headers={"X-CrateIQ-Supervisor-Token": child.verification_token},
            )
            with urllib.request.urlopen(request, timeout=self.readiness_timeout_seconds) as response:
                payload = json.loads(response.read(MAX_IPC_MESSAGE_BYTES + 1))
            admission = payload.get("admission") if isinstance(payload, dict) else None
            if not isinstance(admission, dict):
                return False
            return bool(admission.get("draining")) if action == "begin_draining" else not bool(admission.get("draining"))
        except Exception:
            return False

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
            popen_kwargs = {
                "cwd": self.repo_root,
                "env": environment,
                "shell": False,
                "close_fds": True,
                "pass_fds": (listener.fileno(),),
                "start_new_session": True,
            }
            if threading.current_thread() is threading.main_thread():
                process = self._popen(command, **popen_kwargs)
            else:
                process = self._process_launch_broker.launch(self._popen, command, **popen_kwargs)
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
            # Identity/admission requests can leave accepted connections in
            # TIME_WAIT after a child is definitively terminated and reaped.
            # Every supervisor-reserved listener opts into address reuse so a
            # replacement owned child can bind the same stable endpoint
            # immediately. This does not permit a second bind while the prior
            # listener is still live (SO_REUSEPORT is deliberately not used).
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((spec.bind_host, spec.port))
            listener.listen(128)
            if spec.role != "candidate" or int(listener.getsockname()[1]) not in forbidden:
                return listener
            listener.close()
        raise SupervisorError("could not reserve a safe candidate port")

    def _wait_for_verified_identity(self, child: ChildProcess) -> bool:
        startup_timeout = (
            self.readiness_timeout_seconds
            if child.spec.role == "candidate"
            else self.active_readiness_timeout_seconds
        )
        deadline = time.monotonic() + startup_timeout
        expected_root = str(child.spec.library_root) if child.spec.library_root else None
        expected_library_key = library_key_for_root(child.spec.library_root)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if child.process.poll() is not None:
                return False
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{child.spec.port}/api/internal/supervisor-identity",
                    headers={"X-CrateIQ-Supervisor-Token": child.verification_token},
                )
                with urllib.request.urlopen(
                    request,
                    timeout=min(IDENTITY_PROBE_TIMEOUT_SECONDS, remaining),
                ) as response:
                    raw_payload = response.read(MAX_IPC_MESSAGE_BYTES + 1)
                if len(raw_payload) > MAX_IPC_MESSAGE_BYTES:
                    return False
                payload = json.loads(raw_payload)
                identity = payload.get("identity") if isinstance(payload, dict) else None
                if (
                    isinstance(identity, dict)
                    and identity.get("instance_id") == child.instance_id
                    and identity.get("supervisor_instance_id") == self.instance_id
                    and identity.get("role") == child.spec.role
                    and identity.get("port") == child.spec.port
                    and identity.get("library_root") == expected_root
                    and identity.get("library_key") == expected_library_key
                    and identity.get("verification_token") == child.verification_token
                    and identity.get("ready") is True
                ):
                    return True
            except Exception:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(IDENTITY_PROBE_INTERVAL_SECONDS, remaining))

    def _terminate(self, child: ChildProcess) -> None:
        if child.process.poll() is not None:
            if not self._reap_terminated_child(child):
                raise SupervisorError("terminated child could not be reaped")
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
        if child.process.poll() is None:
            raise SupervisorError("terminated child could not be reaped")

    @staticmethod
    def _reap_terminated_child(child: ChildProcess) -> bool:
        """Confirm an already-exited owned child has been reaped."""
        if child.process.poll() is None:
            return False
        try:
            child.process.wait(timeout=0)
        except Exception:
            return False
        return child.process.poll() is not None

    def _terminate_owned_child(self, child: ChildProcess) -> bool:
        """Terminate and reap an owned child without ever losing ownership.

        Callers must clear ``active``/``candidate`` only after this returns
        ``True``. Any failure leaves the original ``ChildProcess`` reference
        in place for fail-closed status and explicit operator recovery.
        """
        try:
            self._terminate(child)
        except Exception:
            return False
        # _terminate() has waited/reaped before returning. The explicit poll
        # check also protects this invariant when a deterministic test seam
        # replaces _terminate().
        return child.process.poll() is not None

    def _refresh_children_locked(self) -> None:
        if self.candidate is not None and self.candidate.process.poll() is not None:
            if self._reap_terminated_child(self.candidate):
                self.candidate = None
                self._return_candidate_state_to_idle()
        if self.active is not None and self.active.process.poll() is not None:
            if self._reap_terminated_child(self.active):
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
                self.state_store.transition(rollback, "idle", **self._idle_updates("candidate_stopped"))
        except MalformedActivationState:
            # Never overwrite malformed durable state during cleanup.
            pass

    def _set_activation_record(self, record: dict[str, object]) -> None:
        with self._activation_record_lock:
            self._activation_record = record

    def status(self) -> dict[str, object]:
        # A handoff intentionally holds _state_lock across the complete child
        # ownership transition. Status is served by a separate IPC handler and
        # must not queue behind that long operation: doing so exhausts the
        # backend's bounded IPC call and can also starve the promoted backend's
        # event loop while the supervisor is probing its identity endpoint.
        acquired = self._state_lock.acquire(blocking=False)
        try:
            if acquired:
                self._refresh_children_locked()
            active = self.active
            candidate = self.candidate
            try:
                activation_state = self.state_store.read()
                phase = str(activation_state["phase"])
                incomplete = phase != "idle"
            except MalformedActivationState:
                phase = "malformed"
                incomplete = True
        finally:
            if acquired:
                self._state_lock.release()
        with self._activation_record_lock:
            activation = dict(self._activation_record)
        return {
            "supervisor_instance_id": self.instance_id,
            "active_child": active.safe_status() if active else None,
            "candidate_child": candidate.safe_status() if candidate else None,
            "activation_phase": phase,
            "activation_incomplete": incomplete,
            "operation_admission_draining": phase == "active_draining",
            "activation": activation,
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
        if operation == "handoff_library":
            if set(payload) != {"library_root"} or not isinstance(payload["library_root"], str):
                raise SupervisorError("malformed_request")
            return self.handoff_library(payload["library_root"])
        if operation == "activate_registered_library":
            return self.start_registered_library_activation(payload)
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
        self._process_launch_broker.close()

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


def recover_stale_socket(path: Path) -> bool:
    """Atomically remove one proven-stale supervisor socket.

    The caller must hold the installation's supervisor lifetime lock.  This
    explicit recovery primitive never replaces a live, unsafe, or raced path.
    """
    directory_fd = _open_safe_runtime_directory(path.parent)
    withdrawal_name: str | None = None
    exchanged = False
    try:
        details = _entry_stat(directory_fd, path.name)
        if details is None:
            return False
        if stat.S_ISLNK(details.st_mode):
            raise SupervisorError("refusing unsafe supervisor socket symlink")
        if not stat.S_ISSOCK(details.st_mode):
            raise SupervisorError("refusing non-socket supervisor path")
        if _socket_is_live(path, directory_fd=directory_fd):
            raise SupervisorError("a live supervisor still owns the socket")

        identity = (details.st_dev, details.st_ino)
        withdrawal_name = f".{path.name}.recovery.{_new_id()}.withdraw"
        os.mkdir(withdrawal_name, 0o700, dir_fd=directory_fd)
        recovery_directory = _entry_stat(directory_fd, withdrawal_name)
        assert recovery_directory is not None
        recovery_directory_identity = (recovery_directory.st_dev, recovery_directory.st_ino)
        _rename_exchange(directory_fd, path.name, withdrawal_name)
        exchanged = True
        withdrawn = _entry_stat(directory_fd, withdrawal_name)
        replacement = _entry_stat(directory_fd, path.name)
        if not _matches_socket_identity(withdrawn, identity):
            raise SupervisorError("supervisor socket identity changed during recovery")
        if (
            replacement is None
            or not stat.S_ISDIR(replacement.st_mode)
            or (replacement.st_dev, replacement.st_ino) != recovery_directory_identity
        ):
            raise SupervisorError("supervisor socket pathname changed during recovery")
        if not _safe_rmdir(directory_fd, path.name):
            raise SupervisorError("supervisor socket pathname changed during recovery")
        os.unlink(withdrawal_name, dir_fd=directory_fd)
        withdrawal_name = None
        os.fsync(directory_fd)
        return True
    finally:
        if withdrawal_name is not None and not exchanged:
            _safe_rmdir(directory_fd, withdrawal_name)
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
