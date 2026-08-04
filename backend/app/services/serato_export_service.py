"""Staged Serato handoff only; deliberately does not write binary .crate files."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.library_root import assert_path_under_root, selected_library_root
from ..schemas.export import CrateExportRequest, SeratoExportPreview, SeratoExportRequest, SeratoExportResult
from . import crate_export_service


def _staged_folder(base: Path, crate_name: str) -> Path:
    """Return a new safe folder name without replacing a prior staged export."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = f"{crate_export_service._safe_name(crate_name)}_{stamp}"
    candidate = base / stem
    suffix = 2
    while candidate.exists():
        candidate = base / f"{stem}_{suffix}"
        suffix += 1
    return candidate


def _plan(crate_id: int, request: SeratoExportRequest) -> SeratoExportPreview | None:
    if request.destination_mode != "staged" or request.destination_path:
        raise ValueError("Custom and live Serato destinations are not supported; use the staged export directory.")
    portable = crate_export_service.preview(crate_id, CrateExportRequest(format="m3u8", path_mode=request.path_mode, include_metadata=True, line_endings="lf"))
    if portable is None: return None
    root = selected_library_root()
    base = assert_path_under_root(root / "exports" / "serato", root)
    folder = _staged_folder(base, portable.crate_name)
    warnings = [
        "Staged export only: this does not write to your live _Serato_ folder.",
        "Exact Serato .crate binary writing is not implemented; import or review the included M3U8 manually.",
        *portable.warnings,
    ]
    if request.backup_existing:
        warnings.append("No backup was needed because staged exports never replace existing files.")
    return SeratoExportPreview(
        crate_id=portable.crate_id,
        crate_name=portable.crate_name,
        track_count=portable.track_count,
        destination_mode="staged",
        staged_directory=str(folder),
        m3u8_path=str(folder / "crate.m3u8"),
        manifest_path=str(folder / "manifest.json"),
        warnings=warnings,
        m3u8_content=portable.content,
    )


def preview(crate_id: int, request: SeratoExportRequest) -> SeratoExportPreview | None:
    return _plan(crate_id, request)


def write(crate_id: int, request: SeratoExportRequest) -> SeratoExportResult | None:
    plan = _plan(crate_id, request)
    if plan is None: return None
    if request.dry_run:
        return SeratoExportResult(**plan.model_dump(), written=False)
    folder = Path(plan.staged_directory)
    # Timestamped folders and exist_ok=False make replacement impossible.
    folder.mkdir(parents=True, exist_ok=False)
    Path(plan.m3u8_path).write_text(plan.m3u8_content, encoding="utf-8", newline="")
    Path(plan.manifest_path).write_text(json.dumps({"crate_id": plan.crate_id, "crate_name": plan.crate_name, "created_at": datetime.now(timezone.utc).isoformat(), "format": "staged-serato-m3u8", "exact_crate_binary_supported": False, "warnings": plan.warnings, "track_count": plan.track_count, "m3u8_file": Path(plan.m3u8_path).name}, indent=2) + "\n", encoding="utf-8")
    return SeratoExportResult(**plan.model_dump(), written=True)
