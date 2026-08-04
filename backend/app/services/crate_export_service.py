"""Safe portable exports for Manual Crates, isolated from DJ-app writers."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..core.library_root import assert_path_under_root, selected_library_root
from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..schemas.export import CrateExportPreview, CrateExportRequest, CrateExportResult
from . import crate_service


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "crate"


def _track_paths(track_ids: list[int]) -> dict[int, str]:
    if not track_ids or not pipeline_db_exists(): return {}
    with get_pipeline_conn() as conn:
        marks = ",".join("?" for _ in track_ids)
        rows = conn.execute(f"SELECT id, filepath FROM tracks WHERE id IN ({marks})", track_ids).fetchall()
    return {int(row["id"]): str(row["filepath"] or "") for row in rows}


def _display_path(filepath: str, mode: str, root: Path) -> tuple[str, str | None]:
    if not filepath: return "", "Track has no usable library path."
    path = Path(filepath)
    if mode == "absolute": return str(path), None
    if mode == "relative":
        try: return str(path.resolve(strict=False).relative_to(root)), None
        except ValueError: return path.name, "Path is outside the selected root; exported filename instead."
    return path.name, None


def preview(crate_id: int, request: CrateExportRequest) -> CrateExportPreview | None:
    crate = crate_service.get_crate(crate_id)
    if crate is None: return None
    root = selected_library_root()
    paths = _track_paths([track.track_id for track in crate.tracks])
    warnings: list[str] = []
    rows = []
    for track in crate.tracks:
        display, warning = _display_path(paths.get(track.track_id, ""), request.path_mode, root)
        if warning: warnings.append(f"#{track.position} {track.title or track.filename or 'Track'}: {warning}")
        rows.append({"position": track.position, "track_id": track.track_id, "title": track.title, "artist": track.artist, "genre": track.genre, "bpm": track.bpm, "key_camelot": track.key_camelot, "duration_sec": track.duration_sec, "path": display})
    if not rows: warnings.append("This crate is empty; the export contains only its header/metadata.")
    newline = "\r\n" if request.line_endings == "crlf" else "\n"
    content = _render(request, crate, rows, warnings, newline)
    return CrateExportPreview(crate_id=crate.id, crate_name=crate.name, format=request.format, path_mode=request.path_mode, track_count=len(rows), warnings=warnings, content=content)


def _render(request, crate, rows, warnings, newline: str) -> str:
    if request.format == "json":
        tracks = rows if request.include_metadata else [{"position": row["position"], "track_id": row["track_id"], "path": row["path"]} for row in rows]
        return json.dumps({"crate": {"id": crate.id, "name": crate.name, "notes": crate.notes}, "exported_at": datetime.now(timezone.utc).isoformat(), "format": request.format, "path_mode": request.path_mode, "track_count": len(rows), "warnings": warnings, "tracks": tracks}, ensure_ascii=False, indent=2) + newline
    if request.format == "csv":
        out = io.StringIO(newline="")
        fields = ["position", "title", "artist", "album", "genre", "bpm", "key_camelot", "duration_sec", "path"] if request.include_metadata else ["position", "path"]
        writer = csv.DictWriter(out, fieldnames=fields, lineterminator=newline)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: ("" if field == "album" else row.get(field, "")) for field in fields})
        return out.getvalue()
    lines = ["#EXTM3U"]
    for row in rows:
        title = " - ".join(part for part in [row["artist"], row["title"]] if part) if request.include_metadata else ""
        duration = int(row["duration_sec"]) if request.include_metadata and row["duration_sec"] is not None else -1
        lines.extend([f"#EXTINF:{duration},{title}", row["path"]])
    return newline.join(lines) + newline


def write(crate_id: int, request: CrateExportRequest) -> CrateExportResult | None:
    result = preview(crate_id, request)
    if result is None: return None
    root = selected_library_root(); export_dir = assert_path_under_root(root / "exports", root)
    export_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".m3u8" if request.format == "m3u8" else f".{request.format}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidate = export_dir / f"{_safe_name(result.crate_name)}_{stamp}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = export_dir / f"{_safe_name(result.crate_name)}_{stamp}_{counter}{suffix}"; counter += 1
    candidate.write_text(result.content, encoding="utf-8", newline="")
    return CrateExportResult(**result.model_dump(), output_path=str(candidate))
