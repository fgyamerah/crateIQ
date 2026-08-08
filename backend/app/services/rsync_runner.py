"""
rsync_runner — SSD sync via rsync as background jobs.

Design constraints
------------------
• Source is always derived from the active workspace via
  sync_destination_service.get_sync_source() — never a raw user-supplied
  path, never the managed Inbox or Quarantine.
• Destination is always the explicitly configured, validated destination
  from sync_destination_service — never inferred or defaulted.
• No --delete by default; user must explicitly request it via allow_delete=True.
• Subprocess is built as an argument list; shell=False.
• Both toolkit_runner jobs and rsync jobs share process_registry so the
  POST /api/jobs/{id}/cancel route works for both.

Progress parsing
----------------
rsync is run with --info=progress2 which produces lines like:

    1,048,576  45%    1.23MB/s    0:00:10 (xfr#3, to-chk=97/100)

We parse ``to-chk=remaining/total`` for progress_current = total - remaining,
and file-count-based percent.
Updates are written to the DB at most once per second to avoid write churn.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.config import JOBS_LOG_DIR, RSYNC_BIN
from ..schemas.sync import (
    SyncConfigResponse,
    SyncFileChange,
    SyncPreviewRequest,
    SyncPreviewResponse,
)
from . import job_service, process_registry, sync_destination_service

log = logging.getLogger(__name__)

# Maximum number of file entries returned in a preview response
_MAX_PREVIEW_FILES = 500

# Minimum seconds between DB progress writes (throttle)
_PROGRESS_THROTTLE_S = 1.0

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_RE_CHECK = re.compile(r'to-chk=(\d+)/(\d+)')
_RE_PCT   = re.compile(r'\b(\d+)%')
_RE_SPEED = re.compile(r'([\d.]+\s*[kMGT]B/s)')


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _validate_paths(source_key: str) -> Tuple[Path, Path]:
    """
    Return (src, dst) as resolved absolute paths, or raise ValueError with
    a user-friendly message.

    source_key is retained for API/job-record compatibility (it is stored
    against job/operation history) but is not used to select a path --
    "library" is the only allowed value, and the actual source is always
    resolved fresh from the active workspace.
    """
    src = sync_destination_service.get_sync_source()

    dst = sync_destination_service.get_configured_destination()
    if dst is None:
        raise ValueError(
            "No Publish/SSD Sync destination is configured. "
            "Configure one in Settings before running a sync."
        )
    status, blockers, _warnings = sync_destination_service.describe_destination_status()

    if not src.exists():
        raise ValueError(
            f"Source path does not exist: {src}\n"
            "Check that the path is correct and accessible."
        )
    if not src.is_dir():
        raise ValueError(f"Source path is not a directory: {src}")
    if status == "unsafe":
        raise ValueError("; ".join(blockers))
    if status == "not_mounted":
        raise ValueError(
            f"Destination path does not exist: {dst}\n"
            "The SSD may not be mounted. Mount it first:\n"
            f"  ls {dst.parent}"
        )
    if not os.access(dst, os.W_OK):
        raise ValueError(f"Destination is not writable: {dst}")

    return src, dst


def _rsync_src(src: Path) -> str:
    """
    Return the rsync source string with a trailing slash so rsync syncs
    the *contents* of src into dst (not src as a subdirectory of dst).
    """
    return str(src).rstrip('/') + '/'


# ---------------------------------------------------------------------------
# Dry-run preview
# ---------------------------------------------------------------------------

async def preview_sync(req: SyncPreviewRequest) -> SyncPreviewResponse:
    """
    Run rsync --dry-run and return a structured preview of what would change.

    This is an async function that awaits the subprocess — it runs in the
    FastAPI event loop and does NOT spawn a job or write to the DB.
    Times out after 60 seconds. Read-only: never creates the destination.

    req.source is retained for API compatibility (only "library" is a valid
    value); the source path itself always comes fresh from the active
    workspace, never from the request.
    """
    try:
        src_path = sync_destination_service.get_sync_source()
    except ValueError as exc:
        return SyncPreviewResponse(
            source_path = "",
            dest_path   = "",
            file_count  = 0,
            files       = [],
            warnings    = [str(exc)],
            ssd_mounted = False,
        )

    dest_path = sync_destination_service.get_configured_destination()
    dest_status, dest_blockers, dest_warnings = sync_destination_service.describe_destination_status()
    ssd_mounted = dest_status == "ready"
    src_exists  = src_path.exists() and src_path.is_dir()

    base_warnings: List[str] = list(dest_warnings)
    if dest_status in ("needs_setup", "unsafe"):
        base_warnings.extend(dest_blockers)
    elif dest_status == "not_mounted":
        base_warnings.append(f"SSD not mounted at {dest_path}. Mount the drive before running sync.")
    if not src_exists:
        base_warnings.append(f"Source path not found: {src_path}")

    if not ssd_mounted or not src_exists:
        return SyncPreviewResponse(
            source_path = str(src_path),
            dest_path   = str(dest_path) if dest_path else "",
            file_count  = 0,
            files       = [],
            warnings    = base_warnings,
            ssd_mounted = ssd_mounted,
        )

    cmd = [
        RSYNC_BIN,
        "-av",
        "--no-inc-recursive",
        "--dry-run",
        _rsync_src(src_path),
        str(dest_path),
    ]

    log.info("preview_sync: %s", " ".join(cmd))

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            ),
            timeout=5.0,
        )
        try:
            raw_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SyncPreviewResponse(
                source_path = str(src_path),
                dest_path   = str(dest_path),
                file_count  = 0,
                files       = [],
                warnings    = ["Preview timed out after 60 seconds — library may be very large."],
                ssd_mounted = True,
            )
    except FileNotFoundError:
        return SyncPreviewResponse(
            source_path = str(src_path),
            dest_path   = str(dest_path),
            file_count  = 0,
            files       = [],
            warnings    = [
                f"rsync not found at {RSYNC_BIN!r}. "
                "Install it: sudo apt install rsync"
            ],
            ssd_mounted = True,
        )

    output = raw_bytes.decode("utf-8", errors="replace")
    files, summary, warnings = _parse_dry_run_output(output)
    warnings = base_warnings + warnings

    file_items = [
        SyncFileChange(path=f, is_dir=f.endswith('/'))
        for f in files
    ]
    only_files = [f for f in file_items if not f.is_dir]
    truncated  = len(only_files) > _MAX_PREVIEW_FILES

    return SyncPreviewResponse(
        source_path = str(src_path),
        dest_path   = str(dest_path),
        file_count  = len(only_files),
        files       = only_files[:_MAX_PREVIEW_FILES],
        truncated   = truncated,
        summary     = summary,
        warnings    = warnings,
        ssd_mounted = True,
    )


def _parse_dry_run_output(
    output: str,
) -> Tuple[List[str], Optional[str], List[str]]:
    """
    Parse rsync --dry-run output.

    Returns (file_list, summary_line, warnings).
    file_list contains all paths rsync would transfer (files and dirs).
    summary_line is the "sent X bytes … total size is Y" line.
    """
    lines   = output.splitlines()
    files:    List[str] = []
    summary:  Optional[str] = None
    warnings: List[str] = []
    in_list   = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue
        if stripped.startswith("sending incremental file list") or stripped.startswith("building file list"):
            # "sending incremental file list" is rsync's default header;
            # "building file list ... done" is what it prints instead when
            # --no-inc-recursive is passed (as preview_sync() always does).
            # Both mark the start of the transferred-path listing.
            in_list = True
            continue
        if stripped.startswith("sent ") and "received " in stripped:
            summary = stripped
            in_list = False
            continue
        if stripped.startswith("total size is"):
            if summary:
                summary = f"{summary}  |  {stripped}"
            continue
        if stripped.startswith("created directory"):
            continue
        # rsync warning/error lines
        if stripped.startswith("rsync: ") or stripped.startswith("rsync error"):
            warnings.append(stripped)
            continue

        if in_list:
            files.append(stripped)

    if proc_exit_msg := next(
        (l for l in lines if "error" in l.lower() and "rsync" in l.lower()), None
    ):
        warnings.append(proc_exit_msg.strip())

    return files, summary, warnings


# ---------------------------------------------------------------------------
# Sync config (for the frontend config panel)
# ---------------------------------------------------------------------------

def get_sync_config() -> SyncConfigResponse:
    try:
        sources = {"library": str(sync_destination_service.get_sync_source())}
    except ValueError:
        sources = {"library": ""}
    dest = sync_destination_service.get_configured_destination()
    status, blockers, warnings = sync_destination_service.describe_destination_status()
    return SyncConfigResponse(
        sources              = sources,
        dest                 = str(dest) if dest else None,
        destination_status   = status,
        destination_blockers = blockers,
        destination_warnings = warnings,
        rsync_bin            = RSYNC_BIN,
        ssd_mounted          = status == "ready",
    )


# ---------------------------------------------------------------------------
# Background task registry (prevents asyncio GC of tasks)
# ---------------------------------------------------------------------------

_running_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Live sync job
# ---------------------------------------------------------------------------

def start_sync_job(
    source_key:   str,
    allow_delete: bool = False,
) -> "job_service.Job":  # type: ignore[name-defined]
    """
    Validate paths, create a job record, and fire the background rsync task.
    Returns the created Job so the caller can return its ID.

    Raises ValueError if paths are invalid (SSD not mounted, source missing).
    Raises RuntimeError if the asyncio event loop is not running.
    """
    # Eagerly validate so we return 422 before writing the DB record
    src, dst = _validate_paths(source_key)

    cmd = _build_rsync_cmd(src, dst, allow_delete=allow_delete)
    job = job_service.create_job("ssd-sync", _cmd_args(src, allow_delete))

    log_path = JOBS_LOG_DIR / f"{job.id}.log"
    task = asyncio.create_task(
        _run_rsync_job(job.id, cmd, log_path)
    )
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)

    return job


def _cmd_args(src: Path, allow_delete: bool) -> List[str]:
    """Human-readable args stored in the DB for display in the UI."""
    args = [str(src)]
    if allow_delete:
        args.append("--delete")
    return args


def _build_rsync_cmd(src: Path, dst: Path, allow_delete: bool) -> List[str]:
    cmd = [
        RSYNC_BIN,
        "-avh",
        "--info=progress2",
        "--no-inc-recursive",
    ]
    if allow_delete:
        cmd.append("--delete")
    cmd += [_rsync_src(src), str(dst)]
    return cmd


# ---------------------------------------------------------------------------
# Background runner (parses progress, writes to log file and DB)
# ---------------------------------------------------------------------------

async def _run_rsync_job(job_id: str, cmd: List[str], log_path: Path) -> None:
    """
    Run rsync in the background.
    Reads stdout line by line, writes to the log file, and parses rsync
    --info=progress2 lines to update the job's progress fields in the DB.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    job_service.mark_running(job_id)
    log.info("rsync job=%s  starting: %s", job_id, " ".join(cmd))

    exit_code: int = -1
    last_progress_write = 0.0

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        process_registry.register(job_id, proc)
        job_service.mark_pid(job_id, proc.pid)

        with open(log_path, "wb") as log_fh:
            assert proc.stdout is not None
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break

                log_fh.write(line_bytes)
                log_fh.flush()

                # Parse progress (throttled to once per second)
                line_str = line_bytes.decode("utf-8", errors="replace")
                parsed = _parse_progress_line(line_str)
                if parsed is not None:
                    now = time.monotonic()
                    if now - last_progress_write >= _PROGRESS_THROTTLE_S:
                        current, total, pct, msg = parsed
                        job_service.mark_progress(job_id, current, total, pct, msg)
                        last_progress_write = now

        exit_code = await proc.wait()

        if process_registry.is_cancelling(job_id):
            status = "cancelled"
        else:
            status = "succeeded" if exit_code == 0 else "failed"

        log.info(
            "rsync job=%s  finished  exit_code=%d  status=%s",
            job_id, exit_code, status,
        )

    except Exception as exc:
        status = "failed"
        log.exception("rsync job=%s  runner error: %s", job_id, exc)
        try:
            with open(log_path, "ab") as log_fh:
                log_fh.write(f"\n\n--- RUNNER ERROR ---\n{exc}\n".encode())
        except Exception:
            pass
    finally:
        process_registry.unregister(job_id)
        job_service.clear_pid(job_id)

    job_service.mark_finished(job_id, status=status, exit_code=exit_code)


def _parse_progress_line(
    line: str,
) -> Optional[Tuple[int, int, float, str]]:
    """
    Parse an rsync --info=progress2 progress line.

    Returns (current_files, total_files, percent, message) or None if the
    line does not contain parseable progress data.

    Example line::

        1,048,576  45%    1.23MB/s    0:00:10 (xfr#3, to-chk=97/100)

    -> current=3, total=100, percent=3.0, message="3/100 files . 1.23MB/s"
    """
    check_m = _RE_CHECK.search(line)
    if not check_m:
        return None

    remaining = int(check_m.group(1))
    total     = int(check_m.group(2))
    if total == 0:
        return None

    current = total - remaining

    # Use file-count-based percent (more useful for music libraries
    # than bytes-based, since files vary widely in size)
    pct = round(100.0 * current / total, 1)

    speed_m = _RE_SPEED.search(line)
    speed   = speed_m.group(1) if speed_m else None

    msg = f"{current}/{total} files"
    if speed:
        msg += f" · {speed}"

    return current, total, pct, msg
