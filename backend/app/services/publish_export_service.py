"""
Guarded export lifecycle: validate -> preview -> confirm -> execute -> verify.

This is a thin orchestrator over the existing, already-safe exporters
(crate_export_service, rekordbox_crate_export_service, serato_export_service).
It never re-implements format rendering or output-path collision avoidance —
it only unifies them behind one preview/confirm/execute/verify envelope and
records confirmed operations in the Cycle 2-style operations history
(publish_operations table).

Preview is not approval. Approval is not execution. Execution is not
verification.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

from ..core.library_root import assert_path_under_root, selected_library_root
from ..schemas.export import (
    CrateExportRequest,
    CrateLineEndings,
    CratePathMode,
    RekordboxExportRequest,
    SeratoExportRequest,
)
from ..schemas.publish import PublishExportPreview, PublishExportResult, PublishExportTarget
from . import (
    crate_export_service,
    crate_service,
    publish_operations_service,
    rekordbox_crate_export_service,
    serato_export_service,
)

_PORTABLE_TARGETS = {"csv", "json", "m3u", "m3u8"}


class PublishExportBlocked(ValueError):
    """Raised when execute() is called but blockers exist or confirm is not true."""


def _empty_crate_blockers(track_count: int) -> List[str]:
    return ["Crate has no tracks — nothing to publish."] if track_count == 0 else []


def _relative_to_root(path: Path, root: Path) -> Optional[str]:
    try:
        return str(path.resolve(strict=False).relative_to(root.resolve(strict=False)))
    except ValueError:
        return None


def preview(
    crate_id: int,
    export_target: PublishExportTarget,
    path_mode: CratePathMode = "filename",
    include_metadata: bool = True,
    line_endings: CrateLineEndings = "lf",
) -> Optional[PublishExportPreview]:
    crate = crate_service.get_crate(crate_id)
    if crate is None:
        return None

    track_count = len(crate.tracks)
    root = selected_library_root()

    if export_target in _PORTABLE_TARGETS:
        portable = crate_export_service.preview(
            crate_id,
            CrateExportRequest(
                format=export_target, path_mode=path_mode,
                include_metadata=include_metadata, line_endings=line_endings,
            ),
        )
        if portable is None:
            return None
        export_dir = assert_path_under_root(root / "exports", root)
        target_path = crate_export_service.next_output_path(
            export_dir, portable.crate_name, crate_export_service.export_suffix(export_target)
        )
        warnings = list(portable.warnings)
    elif export_target == "rekordbox_xml":
        rb = rekordbox_crate_export_service.preview(
            crate_id,
            RekordboxExportRequest(path_mode=path_mode, include_metadata=include_metadata, dry_run=True),
        )
        if rb is None:
            return None
        target_path = Path(rb.output_path)
        warnings = list(rb.warnings)
    elif export_target == "serato":
        se = serato_export_service.preview(crate_id, SeratoExportRequest(path_mode=path_mode, dry_run=True))
        if se is None:
            return None
        target_path = Path(se.staged_directory)
        warnings = list(se.warnings)
    else:  # pragma: no cover - guarded by the Literal type at the API layer
        raise ValueError(f"Unsupported export_target: {export_target!r}")

    return PublishExportPreview(
        crate_id=crate.id,
        crate_name=crate.name,
        export_target=export_target,
        target_path=str(target_path),
        target_exists=target_path.exists(),
        proposed_filename=target_path.name,
        track_count=track_count,
        warnings=warnings,
        blockers=_empty_crate_blockers(track_count),
        no_overwrite=True,
        confirmation_required=True,
    )


def execute(
    crate_id: int,
    export_target: PublishExportTarget,
    path_mode: CratePathMode = "filename",
    include_metadata: bool = True,
    line_endings: CrateLineEndings = "lf",
    confirm: bool = False,
) -> Optional[PublishExportResult]:
    if not confirm:
        raise PublishExportBlocked("Explicit confirm=true is required to execute an export.")

    plan = preview(crate_id, export_target, path_mode, include_metadata, line_endings)
    if plan is None:
        return None
    if plan.blockers:
        raise PublishExportBlocked("; ".join(plan.blockers))

    root = selected_library_root()
    operation = publish_operations_service.start_operation(
        "export",
        export_target=export_target,
        crate_id=plan.crate_id,
        crate_name=plan.crate_name,
        scope=f"crate:{plan.crate_id}",
        track_count=plan.track_count,
    )
    operation_id = operation["id"]

    try:
        output_path, warnings = _write(crate_id, export_target, path_mode, include_metadata, line_endings)
    except Exception as exc:
        publish_operations_service.finish_operation(
            operation_id, status="failed", result="not_written", error_reason=str(exc)[:200],
        )
        raise

    verification_status, verification_details = _verify(export_target, output_path, plan.track_count)
    destination_relative = _relative_to_root(output_path, root)

    publish_operations_service.finish_operation(
        operation_id,
        status="completed",
        destination_relative=destination_relative,
        result="written",
        verification_status=verification_status,
        verification_details=verification_details,
        warnings=warnings,
    )

    return PublishExportResult(
        operation_id=operation_id,
        crate_id=plan.crate_id,
        crate_name=plan.crate_name,
        export_target=export_target,
        written=True,
        output_path=str(output_path),
        track_count=plan.track_count,
        verification_status=verification_status,
        verification_details=verification_details,
        warnings=warnings,
    )


def _write(
    crate_id: int,
    export_target: PublishExportTarget,
    path_mode: CratePathMode,
    include_metadata: bool,
    line_endings: CrateLineEndings,
) -> Tuple[Path, List[str]]:
    if export_target in _PORTABLE_TARGETS:
        result = crate_export_service.write(
            crate_id,
            CrateExportRequest(
                format=export_target, path_mode=path_mode,
                include_metadata=include_metadata, line_endings=line_endings,
            ),
        )
        if result is None:
            raise ValueError("Crate not found")
        return Path(result.output_path), list(result.warnings)
    if export_target == "rekordbox_xml":
        result = rekordbox_crate_export_service.write(
            crate_id,
            RekordboxExportRequest(path_mode=path_mode, include_metadata=include_metadata, dry_run=False),
        )
        if result is None:
            raise ValueError("Crate not found")
        return Path(result.output_path), list(result.warnings)
    if export_target == "serato":
        result = serato_export_service.write(crate_id, SeratoExportRequest(path_mode=path_mode, dry_run=False))
        if result is None:
            raise ValueError("Crate not found")
        return Path(result.staged_directory), list(result.warnings)
    raise ValueError(f"Unsupported export_target: {export_target!r}")  # pragma: no cover


def _verify(
    export_target: PublishExportTarget, output_path: Path, expected_track_count: int
) -> Tuple[str, List[str]]:
    """Verify the artifact actually exists with the expected shape.

    Returns (verification_status, details). Never raises — a verification
    failure is reported, not thrown, since execution already succeeded.
    """
    details: List[str] = []
    try:
        if export_target in _PORTABLE_TARGETS:
            if not output_path.is_file():
                return "failed", [f"Expected file does not exist: {output_path}"]
            text = output_path.read_text(encoding="utf-8")
            if not text:
                return "failed", ["Exported file is empty"]
            if export_target in ("m3u", "m3u8"):
                if not text.startswith("#EXTM3U"):
                    return "failed", ["File does not start with #EXTM3U"]
            elif export_target == "csv":
                if not text.splitlines()[0].startswith("position"):
                    return "failed", ["CSV header does not start with 'position'"]
            elif export_target == "json":
                payload = json.loads(text)
                actual = len(payload.get("tracks", []))
                if actual != expected_track_count:
                    return "failed", [
                        f"JSON track_count mismatch: expected {expected_track_count}, found {actual}"
                    ]
            details.append(f"{output_path.name} exists and matches the expected {export_target} shape")
            return "verified", details

        if export_target == "rekordbox_xml":
            if not output_path.is_file():
                return "failed", [f"Expected XML file does not exist: {output_path}"]
            document = ET.fromstring(output_path.read_text(encoding="utf-8"))
            collection = document.find("COLLECTION")
            if collection is None:
                return "failed", ["XML has no COLLECTION node"]
            actual = int(collection.attrib.get("Entries", "-1"))
            if actual != expected_track_count:
                return "failed", [
                    f"COLLECTION Entries mismatch: expected {expected_track_count}, found {actual}"
                ]
            details.append(f"XML COLLECTION Entries matches track_count ({expected_track_count})")
            return "verified", details

        if export_target == "serato":
            m3u8_path = output_path / "crate.m3u8"
            manifest_path = output_path / "manifest.json"
            if not m3u8_path.is_file():
                return "failed", [f"Expected M3U8 does not exist: {m3u8_path}"]
            if not manifest_path.is_file():
                return "failed", [f"Expected manifest does not exist: {manifest_path}"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actual = manifest.get("track_count")
            if actual != expected_track_count:
                return "failed", [
                    f"Manifest track_count mismatch: expected {expected_track_count}, found {actual}"
                ]
            details.append("Staged M3U8 and manifest both exist and manifest track_count matches")
            return "verified", details

    except Exception as exc:  # verification itself must never crash the request
        return "failed", [f"Verification raised an error: {exc}"]

    return "skipped", ["No verification rule for this export target"]  # pragma: no cover
