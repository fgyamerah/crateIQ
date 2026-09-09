"""
Job — internal data model for a pipeline task.

Represents a single row from the jobs table.  All fields use simple Python
types so the model is decoupled from both the DB layer and the API layer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Job:
    id:               str
    library_key:      str
    command:          str
    args:             List[str]   # already deserialized from JSON
    status:           str         # pending | running | succeeded | failed | cancelled
    created_at:       str         # ISO-8601 UTC
    started_at:       Optional[str]
    finished_at:      Optional[str]
    exit_code:        Optional[int]
    log_path:         Optional[str]

    # Process tracking (set once the subprocess starts)
    pid:              Optional[int] = None

    # Progress (populated by rsync jobs; None for pipeline.py jobs)
    progress_current: Optional[int]   = None
    progress_total:   Optional[int]   = None
    progress_percent: Optional[float] = None
    progress_message: Optional[str]   = None

    @classmethod
    def from_row(cls, row) -> "Job":
        return cls(
            id               = row["id"],
            library_key      = row["library_key"],
            command          = row["command"],
            args             = json.loads(row["args_json"] or "[]"),
            status           = row["status"],
            created_at       = row["created_at"],
            started_at       = row["started_at"],
            finished_at      = row["finished_at"],
            exit_code        = row["exit_code"],
            log_path         = row["log_path"],
            pid              = row["pid"],
            progress_current = row["progress_current"],
            progress_total   = row["progress_total"],
            progress_percent = row["progress_percent"],
            progress_message = row["progress_message"],
        )
