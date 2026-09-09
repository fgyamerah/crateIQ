"""Fixed supervisor child wrapper; not a user-facing process runner."""
from __future__ import annotations

import ctypes
import os
import signal
import sys


class ParentDeathGuardError(RuntimeError):
    """The child cannot safely run without its supervisor owner."""


def _install_parent_death_guard() -> None:
    """Install fail-closed Linux PDEATHSIG protection before Uvicorn starts."""
    if not sys.platform.startswith("linux"):
        raise ParentDeathGuardError("the local supervisor is supported only on Linux")
    expected_parent = os.getppid()
    if expected_parent <= 1:
        raise ParentDeathGuardError("supervisor parent is already absent")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception as exc:
        raise ParentDeathGuardError("could not configure parent-death protection") from exc
    if result != 0:
        raise ParentDeathGuardError(f"could not configure parent-death protection (errno={ctypes.get_errno()})")
    # A subreaper can receive us during the prctl race, so PID 1 alone is not
    # a sufficient check. Any parent change means do not run unsupervised.
    if os.getppid() != expected_parent:
        raise ParentDeathGuardError("supervisor parent changed during parent-death setup")


def main(argv: list[str] | None = None) -> int:
    try:
        _install_parent_death_guard()
    except ParentDeathGuardError as exc:
        print(f"CrateIQ supervised backend: {exc}", file=sys.stderr)
        return 70
    from uvicorn.main import main as uvicorn_main
    uvicorn_main(args=argv if argv is not None else sys.argv[1:], prog_name="uvicorn")
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked only by the supervisor
    raise SystemExit(main())
