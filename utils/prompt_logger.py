"""
utils/prompt_logger.py — Timestamped prompt/response logger for LLM requests.

Every call to save() writes a new file to ./last-prompts/ at the project root.
Files are never overwritten — each request produces a unique timestamped file.

File format:
    YYYYMMDD_HHMMSS__prompt_log.txt

Used by pipeline.py's LLM-backed commands (e.g. ai-normalize, artist
intelligence, metadata-enrich-online) so relevant calls are logged.
"""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# ./last-prompts/ relative to project root (two levels up from utils/)
_LOG_DIR  = Path(__file__).parent.parent / "last-prompts"
_MAX_LINES = 1000


def _truncate(text: str, label: str = "") -> str:
    """Keep last _MAX_LINES lines; prepend a notice if lines were dropped."""
    lines = text.splitlines()
    if len(lines) <= _MAX_LINES:
        return text
    dropped = len(lines) - _MAX_LINES
    header  = f"[... {dropped} lines truncated — showing last {_MAX_LINES} ...]"
    return header + "\n" + "\n".join(lines[-_MAX_LINES:])


def save(
    prompt:    str,
    response:  Optional[str] = None,
    error:     Optional[str] = None,
    model:     str = "",
    extra:     Optional[dict] = None,
) -> Path:
    """
    Write a prompt/response log file to ./last-prompts/.

    Args:
        prompt:   Full prompt text sent to the LLM.
        response: Response text (None if the call failed or was interrupted).
        error:    Error message or traceback string, if any.
        model:    Model identifier (e.g. "claude-haiku-4-5-20251001").
        extra:    Optional dict of additional key/value metadata lines.

    Returns:
        Path of the written log file.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    ts       = datetime.now()
    filename = ts.strftime("%Y%m%d_%H%M%S") + "__prompt_log.txt"
    log_path = _LOG_DIR / filename

    # If a file with this exact second already exists, append microseconds
    if log_path.exists():
        filename = ts.strftime("%Y%m%d_%H%M%S") + f"_{ts.microsecond:06d}__prompt_log.txt"
        log_path = _LOG_DIR / filename

    sep = "=" * 60

    sections: list[str] = [
        f"timestamp : {ts.isoformat()}",
        f"model     : {model or 'unknown'}",
        f"status    : {'ok' if error is None else 'error/interrupted'}",
    ]
    if extra:
        for k, v in extra.items():
            sections.append(f"{k:<10}: {v}")

    sections += [
        "",
        sep,
        "PROMPT",
        sep,
        _truncate(prompt),
    ]

    if response is not None:
        sections += [
            "",
            sep,
            "RESPONSE",
            sep,
            _truncate(response),
        ]
    else:
        sections += [
            "",
            sep,
            "RESPONSE  (none — call failed or was interrupted)",
            sep,
        ]

    if error:
        sections += [
            "",
            sep,
            "ERROR / INTERRUPTED",
            sep,
            error,
        ]

    log_path.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"[prompt_logger] saved → {log_path}")
    return log_path


# ---------------------------------------------------------------------------
# Pipeline run logging — per-run .log, .jsonl, paged detail files, _summary.json
# ---------------------------------------------------------------------------
import json as _json_module
import logging as _logging
from datetime import datetime as _dt
from typing import Any as _Any

_PAGE_SIZE = 200   # max entries per group page file


class _JsonlHandler(_logging.Handler):
    """Appends structured JSON log records to a .jsonl file."""

    def __init__(self, path: Path, command: str) -> None:
        super().__init__()
        self._path = path
        self._command = command

    def emit(self, record: _logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": _dt.fromtimestamp(record.created).isoformat(),
                "level":     record.levelname,
                "command":   self._command,
                "logger":    record.name,
                "message":   self.format(record),
            }
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(_json_module.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass


class RunLogger:
    """
    Per-run observability: .log file, structured .jsonl, paged detail files,
    and a _summary.json written on finish().

    Runners call inc() / set_counter() / record_outcome() during execution.
    pipeline.py calls finish(exit_code=...) after the runner returns.
    """

    def __init__(self, command: str, log_root: Path) -> None:
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        self._run_dir    = log_root / command
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._prefix     = f"{ts}_{command}"
        self._command    = command
        self._started_at = _dt.now().isoformat()

        self.log_path     = self._run_dir / f"{self._prefix}.log"
        self.jsonl_path   = self._run_dir / f"{self._prefix}.jsonl"
        self.summary_path = self._run_dir / f"{self._prefix}_summary.json"

        # Per-group buffers + page-file name lists
        self._groups:     dict = {"modified": [], "skipped": [], "errors": []}
        self._page_files: dict = {"modified": [], "skipped": [], "errors": []}

        # Named counters and metadata values
        self._counters: dict = {}

        fmt     = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
        datefmt = "%H:%M:%S"

        self._fh = _logging.FileHandler(self.log_path, encoding="utf-8")
        self._fh.setLevel(_logging.DEBUG)
        self._fh.setFormatter(_logging.Formatter(fmt, datefmt=datefmt))

        self._jh = _JsonlHandler(self.jsonl_path, command)
        self._jh.setLevel(_logging.DEBUG)
        self._jh.setFormatter(_logging.Formatter("%(message)s"))

        root = _logging.getLogger()
        root.addHandler(self._fh)
        root.addHandler(self._jh)

    # ------------------------------------------------------------------
    # Counter API
    # ------------------------------------------------------------------

    def inc(self, counter: str, amount: int = 1) -> None:
        """Increment a named integer counter."""
        self._counters[counter] = self._counters.get(counter, 0) + amount

    def set_counter(self, key: str, value: "_Any") -> None:
        """Set an arbitrary metadata value (path, limit, bool flags, etc.)."""
        self._counters[key] = value

    # ------------------------------------------------------------------
    # File outcome tracking → paged detail files
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        group: str,
        path: str,
        reason: str = "",
        details: str = "",
    ) -> None:
        """
        Record a file-level outcome into 'modified', 'skipped', or 'errors'.
        Flushes the current page to disk automatically when PAGE_SIZE is reached.
        """
        if group not in self._groups:
            return
        self._groups[group].append({"path": path, "reason": reason, "details": details})
        if len(self._groups[group]) >= _PAGE_SIZE:
            self._flush_page(group)

    def _flush_page(self, group: str) -> None:
        buf = self._groups[group]
        if not buf:
            return
        page_num = len(self._page_files[group]) + 1
        filename = f"{self._prefix}_{group}_{page_num:03d}.json"
        fpath    = self._run_dir / filename
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                _json_module.dump(buf, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception:
            pass
        self._page_files[group].append(filename)
        self._groups[group] = []

    # ------------------------------------------------------------------
    # Structured JSONL event (optional supplement to root-logger handler)
    # ------------------------------------------------------------------

    def log_file_event(
        self,
        file: str = "",
        stage: str = "",
        action: str = "",
        reason: str = "",
        confidence: "float | None" = None,
        message: str = "",
        level: str = "INFO",
    ) -> None:
        """Write a rich per-file event directly to the JSONL file."""
        entry: dict = {"timestamp": _dt.now().isoformat(), "level": level, "command": self._command}
        if file:                  entry["file"]       = file
        if stage:                 entry["stage"]      = stage
        if action:                entry["action"]     = action
        if reason:                entry["reason"]     = reason
        if confidence is not None: entry["confidence"] = confidence
        if message:               entry["message"]    = message
        try:
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(_json_module.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Finish — flush pages, build summary JSON, detach handlers
    # ------------------------------------------------------------------

    def finish(self, **summary) -> None:
        """
        Called by pipeline.py after the runner exits.
        Flushes pages, writes _summary.json, detaches handlers, prints paths.
        """
        for group in ("modified", "skipped", "errors"):
            self._flush_page(group)

        finished_at = _dt.now().isoformat()
        try:
            duration_sec = (
                _dt.fromisoformat(finished_at) - _dt.fromisoformat(self._started_at)
            ).total_seconds()
        except Exception:
            duration_sec = None

        _pop = self._counters.pop

        results_dict: dict = {
            "changed":   _pop("changed",   0),
            "unchanged": _pop("unchanged", 0),
            "skipped":   _pop("skipped",   0),
            "errors":    _pop("errors",    0),
        }

        ai_dict: dict = {}
        for k in ("valid_responses", "json_failures", "guard_rejections"):
            if k in self._counters:
                ai_dict[k] = _pop(k, 0)

        quality_dict: dict = {}
        for k in ("high_confidence", "medium_confidence", "low_confidence"):
            if k in self._counters:
                quality_dict[k] = _pop(k, 0)

        sanitize_dict: dict = {}
        for k in ("sanitize_changed", "sanitize_clean"):
            if k in self._counters:
                sanitize_dict[k] = _pop(k, 0)

        data: dict = {
            "command":         self._command,
            "input_path":      _pop("input_path",      ""),
            "limit":           _pop("limit",           None),
            "started_at":      self._started_at,
            "finished_at":     finished_at,
            "duration_sec":    duration_sec,
            "files_scanned":   _pop("files_scanned",   0),
            "files_processed": _pop("files_processed", 0),
            "applied":         _pop("applied",         False),
            "results":         results_dict,
        }
        if ai_dict:
            data["ai"] = ai_dict
        if quality_dict:
            data["quality"] = quality_dict
        if sanitize_dict:
            data["sanitize"] = sanitize_dict
        data["review_count"]     = _pop("review_count",     0)
        data["moved_to_ignored"] = _pop("moved_to_ignored", 0)
        data.update(self._counters)   # any remaining custom counters
        data.update(summary)          # kwargs from finish() call (exit_code, etc.)

        detail_files = {g: p for g, p in self._page_files.items() if p}
        if detail_files:
            data["detail_files"] = detail_files

        try:
            with open(self.summary_path, "w", encoding="utf-8") as f:
                _json_module.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception:
            pass

        root = _logging.getLogger()
        for h in (self._fh, self._jh):
            try:
                root.removeHandler(h)
                h.close()
            except Exception:
                pass

        print(f"\n[run log] Summary: {self.summary_path}")
        for group, pages in self._page_files.items():
            if pages:
                print(f"  {group}: {len(pages)} page file(s)")

    def print_paths(self) -> None:
        print(f"Log file    : {self.log_path}")
        print(f"JSONL file  : {self.jsonl_path}")
        print(f"Summary file: {self.summary_path}")


_current_run_logger: "RunLogger | None" = None


def start_run(command: str, log_root: Path) -> RunLogger:
    global _current_run_logger
    _current_run_logger = RunLogger(command, log_root)
    return _current_run_logger


def get_run_logger() -> "RunLogger | None":
    return _current_run_logger
