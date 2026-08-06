"""Safe subprocess supervision for the W2 waveform extraction engine.

Every launch here is an explicit argument vector with ``shell=False``
(``asyncio.create_subprocess_exec`` never accepts a shell string). Spawn is
injectable so tests exercise real control flow against a fake process instead
of a real audio tool.
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Mapping, Protocol

from .waveform_limits import PCM_READ_CHUNK_BYTES, STDERR_TAIL_BYTES, TERMINATION_GRACE_SECONDS


class SupportsProcessIO(Protocol):
    pid: int
    returncode: int | None

    async def wait(self) -> int: ...
    def send_signal(self, sig: int) -> None: ...


SpawnFn = Callable[..., Awaitable[SupportsProcessIO]]


@dataclass
class ProcessOutcome:
    exit_code: int | None = None
    stderr_tail: bytes = b""
    timed_out: bool = False
    cancelled: bool = False
    launch_error: str | None = None


@dataclass
class ManagedRun:
    stdout: AsyncIterator[bytes]
    outcome: ProcessOutcome = field(default_factory=ProcessOutcome)


def _terminate_process_group(proc: SupportsProcessIO, sig: int) -> None:
    """Best-effort process-group termination; falls back to the single process."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, sig)
        return
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.send_signal(sig)
    except (ProcessLookupError, OSError):
        pass


class ProcessSupervisor:
    """Launches, streams, times out, cancels, and reaps one child process."""

    def __init__(
        self,
        *,
        spawn: SpawnFn | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._monotonic = monotonic

    async def run(
        self,
        argv: list[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float,
        cancellation: "CancellationLike | None" = None,
        stdout_chunk_size: int = PCM_READ_CHUNK_BYTES,
        stderr_cap_bytes: int = STDERR_TAIL_BYTES,
        termination_grace_seconds: float = TERMINATION_GRACE_SECONDS,
    ) -> ManagedRun:
        """Launch ``argv`` with no shell and return a streaming stdout iterator.

        The returned :class:`ManagedRun`'s ``outcome`` is populated in place
        as ``stdout`` is consumed and is only complete once the iterator is
        exhausted.
        """
        outcome = ProcessOutcome()
        try:
            proc = await self._spawn(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=dict(env) if env is not None else None,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            outcome.launch_error = str(exc)

            async def _empty() -> AsyncIterator[bytes]:
                return
                yield b""  # pragma: no cover - makes this an async generator

            return ManagedRun(stdout=_empty(), outcome=outcome)

        stderr_buffer = bytearray()

        async def _drain_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                chunk = await proc.stderr.read(65536)
                if not chunk:
                    return
                stderr_buffer.extend(chunk)
                overflow = len(stderr_buffer) - stderr_cap_bytes
                if overflow > 0:
                    del stderr_buffer[:overflow]

        stderr_task = asyncio.ensure_future(_drain_stderr())

        async def _stdout_chunks() -> AsyncIterator[bytes]:
            deadline = self._monotonic() + timeout_seconds
            assert proc.stdout is not None
            try:
                while True:
                    if cancellation is not None and cancellation.is_cancelled:
                        outcome.cancelled = True
                        break
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        outcome.timed_out = True
                        break
                    read_task = asyncio.ensure_future(proc.stdout.read(stdout_chunk_size))
                    waiters = [read_task]
                    cancel_task = None
                    if cancellation is not None:
                        cancel_task = asyncio.ensure_future(cancellation.wait())
                        waiters.append(cancel_task)
                    done, pending = await asyncio.wait(
                        waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    if not done:
                        outcome.timed_out = True
                        break
                    if cancel_task is not None and cancel_task in done:
                        outcome.cancelled = True
                        break
                    chunk = read_task.result()
                    if not chunk:
                        break
                    yield chunk
            finally:
                if outcome.cancelled or outcome.timed_out:
                    _terminate_process_group(proc, signal.SIGTERM)
                    try:
                        outcome.exit_code = await asyncio.wait_for(proc.wait(), timeout=termination_grace_seconds)
                    except asyncio.TimeoutError:
                        _terminate_process_group(proc, signal.SIGKILL)
                        outcome.exit_code = await proc.wait()
                else:
                    outcome.exit_code = await proc.wait()
                await stderr_task
                outcome.stderr_tail = bytes(stderr_buffer)

        return ManagedRun(stdout=_stdout_chunks(), outcome=outcome)

    async def run_capped(
        self,
        argv: list[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float,
        max_stdout_bytes: int,
        cancellation: "CancellationLike | None" = None,
    ) -> tuple[bytes, ProcessOutcome]:
        """Run a short-lived command and return bounded, fully collected stdout."""
        managed = await self.run(
            argv,
            env=env,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
        )
        collected = bytearray()
        overflowed = False
        async for chunk in managed.stdout:
            remaining = max_stdout_bytes - len(collected)
            if remaining <= 0:
                overflowed = True
                continue
            if len(chunk) > remaining:
                overflowed = True
                collected.extend(chunk[:remaining])
            else:
                collected.extend(chunk)
        if overflowed:
            managed.outcome.stderr_tail += b"\n[stdout truncated: exceeded cap]"
        return bytes(collected), managed.outcome


class CancellationLike(Protocol):
    @property
    def is_cancelled(self) -> bool: ...

    async def wait(self) -> None: ...
