"""Process-local admission control for a future library handoff.

An admission lease covers only the small critical section that records
durable work (operation row, job, or background task). It deliberately does
not cover the lifetime of that work. Draining therefore blocks new durable
work immediately and waits for every already-admitted create section to
finish before it returns.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from enum import Enum
from threading import Condition, RLock, local
from typing import AsyncIterator, Iterator


class AdmissionState(str, Enum):
    RUNNING = "RUNNING"
    DRAINING_FOR_LIBRARY_SWITCH = "DRAINING_FOR_LIBRARY_SWITCH"


class LibraryOperationDrainingError(RuntimeError):
    """Raised before a new library-bound mutation is admitted while draining."""


class OperationScope:
    """Unforgeable, gate-owned authorization for one admitted orchestrator.

    A scope is intentionally not a caller-settable boolean.  Only its owning
    gate can accept it for deferred descendant creation, and it must be
    released by the operation's terminal path.
    """

    def __init__(self, gate: "OperationAdmissionGate") -> None:
        self._gate = gate
        self._released = False

    def release(self) -> None:
        self._gate._release_scope(self)


class OperationAdmissionGate:
    """Thread-safe, re-entrant admission leases for durable work creation.

    The same thread can nest a lease while its outer lease is active. This is
    useful for a single operation's tightly-coupled create helpers and does
    not add a second in-flight admission. Async routes safely use this
    synchronous primitive around their non-awaiting create sections; blocking
    work is dispatched separately and never retains a lease.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._drained = Condition(self._lock)
        self._state = AdmissionState.RUNNING
        self._in_flight = 0
        self._active_scopes: set[OperationScope] = set()
        self._local = local()

    @property
    def state(self) -> AdmissionState:
        with self._lock:
            return self._state

    @contextmanager
    def admit(self) -> Iterator[None]:
        """Grant one bounded durable-work admission or fail while draining."""
        depth = getattr(self._local, "depth", 0)
        with self._lock:
            if depth == 0:
                if self._state is AdmissionState.DRAINING_FOR_LIBRARY_SWITCH:
                    raise LibraryOperationDrainingError(
                        "New library-bound operations are temporarily paused for a library switch."
                    )
                self._in_flight += 1
            self._local.depth = depth + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = getattr(self._local, "depth", 1) - 1
                self._local.depth = remaining
                if remaining == 0:
                    self._in_flight -= 1
                    self._drained.notify_all()

    def begin_draining(self) -> None:
        """Reject new operations and wait for all durable-create authority.

        In addition to short create leases, this waits for admitted
        orchestrators that can create deferred durable descendants.
        """
        with self._lock:
            self._state = AdmissionState.DRAINING_FOR_LIBRARY_SWITCH
            while self._in_flight or self._active_scopes:
                self._drained.wait()

    def open_operation_scope(self) -> OperationScope:
        """Create a long-lived descendant authorization inside an admission.

        The caller must already hold :meth:`admit`; this prevents an
        unrelated request from obtaining a scope after draining has begun.
        """
        if getattr(self._local, "depth", 0) == 0:
            raise RuntimeError("operation scopes require an admitted top-level operation")
        with self._lock:
            if self._state is AdmissionState.DRAINING_FOR_LIBRARY_SWITCH:
                raise LibraryOperationDrainingError(
                    "New descendant-producing operations are temporarily paused for a library switch."
                )
            scope = OperationScope(self)
            self._active_scopes.add(scope)
            return scope

    def reserve_operation_scope(self) -> OperationScope:
        """Atomically reserve descendant authority for a top-level workflow.

        Unlike :meth:`open_operation_scope`, this is the admission itself:
        callers use it *before* creating their durable parent row.  A drain
        can therefore neither strand a newly-created parent without a scope
        nor overtake an already-reserved workflow.
        """
        with self._lock:
            if self._state is AdmissionState.DRAINING_FOR_LIBRARY_SWITCH:
                raise LibraryOperationDrainingError(
                    "New descendant-producing operations are temporarily paused for a library switch."
                )
            scope = OperationScope(self)
            self._active_scopes.add(scope)
            return scope

    def _release_scope(self, scope: OperationScope) -> None:
        with self._lock:
            if scope._gate is not self or scope._released:
                return
            scope._released = True
            self._active_scopes.discard(scope)
            self._drained.notify_all()

    @contextmanager
    def admit_descendant(self, scope: OperationScope) -> Iterator[None]:
        """Allow a durable child only for an already-admitted active scope."""
        with self._lock:
            if scope._gate is not self or scope._released or scope not in self._active_scopes:
                raise LibraryOperationDrainingError(
                    "This operation can no longer create library-bound durable work."
                )
            self._in_flight += 1
        try:
            yield
        finally:
            with self._lock:
                self._in_flight -= 1
                self._drained.notify_all()

    @asynccontextmanager
    async def admit_async(self) -> AsyncIterator[None]:
        """Async-call-site form for a non-awaiting durable-create section."""
        with self.admit():
            yield

    async def begin_draining_async(self) -> None:
        """Do not block an event loop while waiting for threaded admissions."""
        await asyncio.to_thread(self.begin_draining)

    def abort_draining(self) -> None:
        with self._lock:
            self._state = AdmissionState.RUNNING
            self._drained.notify_all()

    def require_library_mutation(self) -> None:
        """Compatibility check for callers that do not create durable work.

        New mutation adapters must use :meth:`admit`; this method intentionally
        remains a state check for any existing read/validation-only callers.
        """
        with self._lock:
            if self._state is AdmissionState.DRAINING_FOR_LIBRARY_SWITCH:
                raise LibraryOperationDrainingError(
                    "New library-bound operations are temporarily paused for a library switch."
                )

    def status(self) -> dict[str, bool | int | str]:
        with self._lock:
            return {
                "state": self._state.value,
                "draining": self._state is AdmissionState.DRAINING_FOR_LIBRARY_SWITCH,
                "in_flight_admissions": self._in_flight,
                "active_operation_scopes": len(self._active_scopes),
            }


operation_admission_gate = OperationAdmissionGate()
