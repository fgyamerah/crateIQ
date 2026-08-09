"""
toolkit_runner — safely invoke pipeline.py subcommands as background jobs.

Security model
--------------
  • Commands are validated against ALLOWED_COMMANDS (explicit allowlist).
  • CLI arguments are validated against per-type allowlists:
      - Boolean flags must be in _ALLOWED_BOOL_FLAGS.
      - Value flags must be in _ALLOWED_VALUE_FLAGS; values are validated
        by a per-flag callable.
  • Subprocess commands are always built as argument *lists* — never shell
    strings.  shell=False (the default) is enforced.
  • No user-supplied string is ever interpolated into a shell command.

Lifecycle
---------
  create_and_start_job() is the main entry point called from the route.
  It creates a DB record, launches an asyncio background task, and returns
  the job ID immediately.  The background task calls _run_job(), which:
    1. Opens the log file for writing.
    2. Spawns the subprocess with stdout+stderr → log file.
    3. Waits for completion and records exit_code + final status.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List

from ..core.config import JOBS_LOG_DIR, PIPELINE_PY, PYTHON_BIN, TOOLKIT_ROOT
from ..core.library_root import selected_library_root
from . import process_registry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: FrozenSet[str] = frozenset(
    {
        "playlists",
        "dedupe",
        "audit-quality",
        "analyze-missing",
        "metadata-clean",
        "metadata-sanitize",
        "artist-merge",
        "artist-folder-clean",
        "rekordbox-export",
        "convert-audio",
        "label-intel",
        "set-builder",
        "harmonic-suggest",
        "tag-normalize",
        "db-prune-stale",
        "label-clean",
        "generate-docs",
        "validate-docs",
        "cue-suggest",
    }
)

# Boolean flags — presence only, no value follows
_ALLOWED_BOOL_FLAGS: FrozenSet[str] = frozenset(
    {
        "--dry-run",
        "--verbose",
        "--strict",
        "--force-xml",
        "--force-cue-suggest",
        "--write-tags",
        "--no-progress",
        "--overwrite",
        "--reanalyze",
        "--skip-beets",
        "--apply",
        "--no-strict-harmonic",       # set-builder: disable strict harmonic key validation
        "--no-m3u",                   # rekordbox-export: skip M3U generation
        "--recover-missing-analysis", # rekordbox-export: run aubio/keyfinder inline for missing analysis
    }
)

def _is_safe_input_path(v: str) -> bool:
    """Accept only absolute paths that resolve inside the active library root."""
    try:
        p = Path(v).resolve()
        root = selected_library_root()
        return p == root or root in p.parents
    except Exception:
        return False


# Value flags — each paired with a validator: (str) → bool
_ALLOWED_VALUE_FLAGS: Dict[str, Callable[[str], bool]] = {
    "--input":                 _is_safe_input_path,
    "--report-format":         lambda v: set(v.split(",")) <= {"csv", "json"},
    "--min-lossy-kbps":        lambda v: v.isdigit() and 0 < int(v) < 500,
    "--workers":               lambda v: v.isdigit() and 0 < int(v) <= 16,
    "--format":                lambda v: v in {"csv", "md", "html", "all"},
    # set-builder flags
    "--vibe":                  lambda v: v in {"warm", "peak", "deep", "driving"},
    "--duration":              lambda v: v.isdigit() and 10 <= int(v) <= 360,
    "--genre":                 lambda v: bool(re.match(r"^[\w\s\-/&']{1,64}$", v)),
    "--strategy":              lambda v: v in {
                                   "safest", "energy_lift", "smooth_blend",
                                   "best_warmup", "best_late_set",
                               },
    "--structure":             lambda v: v in {"full", "simple", "peak_only"},
    "--max-bpm-jump":          lambda v: bool(re.match(r"^\d+(\.\d+)?$", v))
                                         and 0.0 <= float(v) <= 20.0,
    "--artist-repeat-window":  lambda v: v.isdigit() and 0 <= int(v) <= 10,
    "--name":                  lambda v: bool(re.match(r"^[\w\-]{1,64}$", v)),
}


# ---------------------------------------------------------------------------
# Command builder
# ---------------------------------------------------------------------------

def build_command(command: str, args: List[str]) -> List[str]:
    """
    Build a subprocess argument list for the given pipeline subcommand.

    Raises ValueError if command or any argument fails validation.
    Never raises for expected input — callers can surface the message
    directly in a 422 response.
    """
    if command not in ALLOWED_COMMANDS:
        raise ValueError(
            f"Command {command!r} is not in the allowed list. "
            f"Allowed: {sorted(ALLOWED_COMMANDS)}"
        )

    cmd: List[str] = [str(PYTHON_BIN), str(PIPELINE_PY), command]

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in _ALLOWED_BOOL_FLAGS:
            cmd.append(arg)

        elif arg in _ALLOWED_VALUE_FLAGS:
            if i + 1 >= len(args):
                raise ValueError(f"Flag {arg!r} requires a value but none was provided.")
            val = args[i + 1]
            if not _ALLOWED_VALUE_FLAGS[arg](val):
                raise ValueError(
                    f"Value {val!r} is not valid for flag {arg!r}."
                )
            cmd.extend([arg, val])
            i += 1  # skip the value token

        else:
            raise ValueError(
                f"Argument {arg!r} is not in the allowed list. "
                f"Allowed flags: {sorted(_ALLOWED_BOOL_FLAGS | set(_ALLOWED_VALUE_FLAGS))}"
            )

        i += 1

    return cmd


# ---------------------------------------------------------------------------
# Background task registry (prevents asyncio garbage-collection)
# ---------------------------------------------------------------------------

_running_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

async def _run_job(job_id: str, cmd: List[str], log_path: Path) -> None:
    """
    Execute *cmd* as a subprocess, stream output to *log_path*, and update
    the job record in the database on start and finish.

    This coroutine is designed to be run as an asyncio background task.
    All exceptions are caught so a crashed runner never takes down the server.
    """
    # Import here to avoid a circular import at module level
    from . import job_service

    log_path.parent.mkdir(parents=True, exist_ok=True)

    job_service.mark_running(job_id)
    log.info("job=%s  starting: %s", job_id, " ".join(cmd))

    exit_code: int = -1
    try:
        # stdout and stderr both go to the log file.
        # Using a binary file and passing it directly to create_subprocess_exec
        # means the OS flushes in real-time — the log endpoint always shows
        # current output even while the process is still running.
        with open(log_path, "wb") as log_fh:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_fh,
                stderr=log_fh,
                cwd=str(TOOLKIT_ROOT),
            )
            # Register process for cancellation support
            process_registry.register(job_id, proc)
            job_service.mark_pid(job_id, proc.pid)

            exit_code = await proc.wait()

        # Determine final status — check cancellation flag first
        if process_registry.is_cancelling(job_id):
            status = "cancelled"
        else:
            status = "succeeded" if exit_code == 0 else "failed"

        log.info("job=%s  finished  exit_code=%d  status=%s", job_id, exit_code, status)

    except Exception as exc:
        status = "failed"
        log.exception("job=%s  runner exception: %s", job_id, exc)
        # Append the exception to the log so the user can see it via the API
        try:
            with open(log_path, "ab") as log_fh:
                log_fh.write(f"\n\n--- RUNNER ERROR ---\n{exc}\n".encode())
        except Exception:
            pass
    finally:
        process_registry.unregister(job_id)
        job_service.clear_pid(job_id)

    job_service.mark_finished(job_id, status=status, exit_code=exit_code)


def create_and_start_job(job_id: str, command: str, args: List[str]) -> None:
    """
    Build the command, resolve the log path, and fire off the background task.

    Called from the POST /api/jobs route handler *after* the job record has
    already been written to the database with status=pending.

    Raises ValueError (propagated from build_command) if validation fails —
    the caller should handle this before the DB record is created.
    """
    cmd      = build_command(command, args)
    log_path = JOBS_LOG_DIR / f"{job_id}.log"

    task = asyncio.create_task(_run_job(job_id, cmd, log_path))

    # Keep a reference so asyncio doesn't garbage-collect the task before it
    # finishes (tasks with no references can be silently dropped).
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)
