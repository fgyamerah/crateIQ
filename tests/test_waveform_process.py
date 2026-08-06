"""W2 tests for the safe subprocess supervisor, using only fake process objects.

No real audio tool is ever spawned in this file.
"""
from __future__ import annotations

import asyncio
import signal

import pytest

from backend.app.core import waveform_process
from backend.app.core.waveform_process import ProcessSupervisor
from backend.app.models.waveform_extraction import CancellationToken
from tests.conftest import async_test


class _ChunkStream:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, n: int) -> bytes:  # noqa: ARG002 - fixed-size fake chunks
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _HangingStream:
    async def read(self, n: int) -> bytes:  # noqa: ARG002
        await asyncio.Event().wait()  # never resolves on its own
        return b""  # pragma: no cover - unreachable


class FakeProcess:
    def __init__(
        self,
        *,
        stdout_chunks: list[bytes] | None = None,
        hang_stdout: bool = False,
        stderr_chunks: list[bytes] | None = None,
        exit_code: int = 0,
        respond_to_term: bool = False,
    ) -> None:
        self.pid = 999999
        self.returncode: int | None = None
        self._exit_code = exit_code
        self.stdout = _HangingStream() if hang_stdout else _ChunkStream(stdout_chunks or [])
        self.stderr = _ChunkStream(stderr_chunks or [])
        self.signals: list[int] = []
        self._wait_event = asyncio.Event()
        self._respond_to_term = respond_to_term
        if not hang_stdout:
            self._wait_event.set()

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == signal.SIGKILL:
            self._wait_event.set()
        elif sig == signal.SIGTERM and self._respond_to_term:
            self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        self.returncode = self._exit_code
        return self._exit_code


def _spawn_returning(proc: FakeProcess, calls: list[dict]):
    async def _spawn(*argv, **kwargs):
        calls.append({"argv": list(argv), "kwargs": kwargs})
        return proc

    return _spawn


@pytest.fixture(autouse=True)
def _force_process_group_fallback(monkeypatch):
    """Fake pids are never real process groups; force the send_signal fallback."""

    def _raise(_pid):
        raise ProcessLookupError()

    monkeypatch.setattr(waveform_process.os, "getpgid", _raise)


async def _collect(managed) -> list[bytes]:
    return [chunk async for chunk in managed.stdout]


# ---------------------------------------------------------------------------
# Argument vector / no-shell contract
# ---------------------------------------------------------------------------


@async_test
async def test_run_uses_argument_vector_with_no_shell_and_process_group():
    calls: list[dict] = []
    proc = FakeProcess(stdout_chunks=[b"abc"])
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, calls))
    managed = await supervisor.run(["ffmpeg", "-i", "in.mp3", "-f", "s16le", "pipe:1"], timeout_seconds=5)
    await _collect(managed)
    assert len(calls) == 1
    assert calls[0]["argv"] == ["ffmpeg", "-i", "in.mp3", "-f", "s16le", "pipe:1"]
    assert "shell" not in calls[0]["kwargs"]
    assert calls[0]["kwargs"]["start_new_session"] is True
    assert managed.outcome.exit_code == 0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@async_test
async def test_run_streams_stdout_chunks_in_order():
    proc = FakeProcess(stdout_chunks=[b"one", b"two", b"three"])
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    managed = await supervisor.run(["fake"], timeout_seconds=5)
    chunks = await _collect(managed)
    assert chunks == [b"one", b"two", b"three"]
    assert managed.outcome.exit_code == 0
    assert not managed.outcome.cancelled
    assert not managed.outcome.timed_out


@async_test
async def test_run_reports_nonzero_exit():
    proc = FakeProcess(stdout_chunks=[b"partial"], exit_code=1)
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    managed = await supervisor.run(["fake"], timeout_seconds=5)
    await _collect(managed)
    assert managed.outcome.exit_code == 1


# ---------------------------------------------------------------------------
# Launch failure
# ---------------------------------------------------------------------------


@async_test
async def test_run_reports_process_launch_failure_without_raising():
    async def _spawn(*argv, **kwargs):
        raise FileNotFoundError("no such file: fake-ffmpeg")

    supervisor = ProcessSupervisor(spawn=_spawn)
    managed = await supervisor.run(["fake-ffmpeg"], timeout_seconds=5)
    chunks = await _collect(managed)
    assert chunks == []
    assert managed.outcome.launch_error is not None
    assert managed.outcome.exit_code is None


# ---------------------------------------------------------------------------
# Timeout / termination grace / kill fallback
# ---------------------------------------------------------------------------


@async_test
async def test_run_times_out_and_escalates_term_then_kill():
    proc = FakeProcess(hang_stdout=True, exit_code=-9, respond_to_term=False)
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    managed = await supervisor.run(
        ["fake"], timeout_seconds=0.02, termination_grace_seconds=0.02
    )
    chunks = await _collect(managed)
    assert chunks == []
    assert managed.outcome.timed_out is True
    assert signal.SIGTERM in proc.signals
    assert signal.SIGKILL in proc.signals
    assert managed.outcome.exit_code == -9


@async_test
async def test_run_graceful_termination_succeeds_without_kill():
    proc = FakeProcess(hang_stdout=True, exit_code=-15, respond_to_term=True)
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    managed = await supervisor.run(
        ["fake"], timeout_seconds=0.02, termination_grace_seconds=1.0
    )
    await _collect(managed)
    assert managed.outcome.timed_out is True
    assert signal.SIGTERM in proc.signals
    assert signal.SIGKILL not in proc.signals
    assert managed.outcome.exit_code == -15


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@async_test
async def test_run_cancellation_stops_streaming_and_terminates():
    proc = FakeProcess(hang_stdout=True, exit_code=-9, respond_to_term=False)
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    token = CancellationToken()

    async def _cancel_soon():
        await asyncio.sleep(0.01)
        token.cancel()

    asyncio.ensure_future(_cancel_soon())
    managed = await supervisor.run(
        ["fake"], timeout_seconds=5, cancellation=token, termination_grace_seconds=0.02
    )
    chunks = await _collect(managed)
    assert chunks == []
    assert managed.outcome.cancelled is True
    assert managed.outcome.timed_out is False
    assert signal.SIGTERM in proc.signals
    assert signal.SIGKILL in proc.signals


@async_test
async def test_cancellation_before_run_is_observed_immediately():
    proc = FakeProcess(hang_stdout=True, respond_to_term=True, exit_code=-15)
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    token = CancellationToken()
    token.cancel()
    managed = await supervisor.run(["fake"], timeout_seconds=5, cancellation=token, termination_grace_seconds=0.02)
    chunks = await _collect(managed)
    assert chunks == []
    assert managed.outcome.cancelled is True


# ---------------------------------------------------------------------------
# Bounded stderr
# ---------------------------------------------------------------------------


@async_test
async def test_stderr_tail_is_bounded_to_the_configured_cap():
    stderr_chunks = [b"x" * 100 for _ in range(20)]  # 2000 bytes total
    proc = FakeProcess(stdout_chunks=[b"ok"], stderr_chunks=stderr_chunks)
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    managed = await supervisor.run(["fake"], timeout_seconds=5, stderr_cap_bytes=256)
    await _collect(managed)
    assert len(managed.outcome.stderr_tail) <= 256
    assert managed.outcome.stderr_tail == b"x" * 256  # only the tail is retained


# ---------------------------------------------------------------------------
# run_capped (used by the ffprobe wrapper)
# ---------------------------------------------------------------------------


@async_test
async def test_run_capped_collects_full_output_within_bound():
    proc = FakeProcess(stdout_chunks=[b"{\"a\":", b"1}"])
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    stdout, outcome = await supervisor.run_capped(["fake"], timeout_seconds=5, max_stdout_bytes=1024)
    assert stdout == b'{"a":1}'
    assert outcome.exit_code == 0


@async_test
async def test_run_capped_truncates_oversized_output():
    proc = FakeProcess(stdout_chunks=[b"a" * 600, b"b" * 600])
    supervisor = ProcessSupervisor(spawn=_spawn_returning(proc, []))
    stdout, outcome = await supervisor.run_capped(["fake"], timeout_seconds=5, max_stdout_bytes=1000)
    assert len(stdout) == 1000
    assert b"truncated" in outcome.stderr_tail
